"""笔记任务路由：创建、查询进度、查看/导出笔记。"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from ..db import SessionLocal
from ..deps import require_csrf, require_session
from ..models import NoteItem, NoteJob
from ..schemas import JobCreateRequest, JobDetailOut, JobOut
from ..services.task_queue import TaskQueue

router = APIRouter(prefix="/api/notes", tags=["notes"])

# 单进程共享任务队列（在 main 中注入 max_concurrency）
task_queue: TaskQueue | None = None


def set_queue(q: TaskQueue) -> None:
    global task_queue
    task_queue = q


def _job_out(job: NoteJob) -> dict:
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "failed": job.failed,
        "message": job.message,
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M") if isinstance(job.created_at, datetime) else str(job.created_at),
    }


@router.post("/jobs", dependencies=[Depends(require_csrf)])
async def create_job(body: JobCreateRequest, user: str = Depends(require_session)) -> dict:
    videos = list(body.videos)
    # ── video-to-summary 模式：支持直接粘贴链接 ──
    from ..services.url_parser import extract_bvid
    from ..services.bili_client import BiliClient
    existing_bvids = {v.bvid for v in videos}
    url_errors: list[str] = []
    if body.urls:
        db0 = SessionLocal()
        try:
            account = db0.query(BiliAccount).first()
            cookies = json.loads(decrypt_secret(account.enc_cookies)) if account else {}
        finally:
            db0.close()
        client = BiliClient(cookies)
        for u in body.urls:
            bvid = extract_bvid(u.url.strip())
            if not bvid:
                url_errors.append(f"无法解析链接：{u.url[:80]}")
                continue
            if bvid in existing_bvids:
                continue
            try:
                info = await client.get_video_info(bvid)
            except Exception as exc:  # noqa: BLE001
                url_errors.append(f"获取视频信息失败 {bvid}: {str(exc)[:80]}")
                continue
            videos.append(VideoRef(
                bvid=bvid,
                title=info.get("title", ""),
                owner=info.get("owner", ""),
                cover=info.get("cover", ""),
                duration=info.get("duration", 0),
            ))
            existing_bvids.add(bvid)

    # 校验至少选了一样
    if not videos and not body.collections:
        detail = "请至少选择一个视频或合集片段"
        if url_errors:
            detail += "；" + "；".join(url_errors)
        raise HTTPException(status_code=400, detail=detail)

    db = SessionLocal()
    try:
        title = body.title or f"知识笔记 · {datetime.now().strftime('%m-%d %H:%M')}"
        job = NoteJob(
            title=title,
            scope_json=json.dumps(body.model_dump(), ensure_ascii=False),
        )
        db.add(job)
        db.flush()

        # ── 缓存复用：同 bvid 已有成功笔记则直接复用，不再重复调用 LLM ──
        cached = {}
        if videos:
            bvids = [v.bvid for v in videos]
            rows = (
                db.query(NoteItem)
                .filter(NoteItem.bvid.in_(bvids), NoteItem.status == "done", NoteItem.note_md != "")
                .order_by(NoteItem.id.desc())
                .all()
            )
            seen: set[str] = set()
            for r in rows:
                if r.bvid not in seen:
                    seen.add(r.bvid)
                    cached[r.bvid] = r.note_md

        reused = 0
        for v in videos:
            if v.bvid in cached:
                db.add(NoteItem(
                    job_id=job.id, bvid=v.bvid, title=v.title,
                    owner=v.owner, cover=v.cover, duration=v.duration,
                    note_md=cached[v.bvid], status="done",
                    error="（复用缓存笔记，未重新生成）",
                ))
                reused += 1
            else:
                db.add(NoteItem(
                    job_id=job.id, bvid=v.bvid, title=v.title,
                    owner=v.owner, cover=v.cover, duration=v.duration,
                ))
        job.total = len(videos)
        job.message = f"缓存复用 {reused} 条" if reused else ""
        db.commit()
        job_id = job.id
    finally:
        db.close()

    # 全部命中缓存则无需入队
    if task_queue and job.total > reused:
        task_queue.set_style(job_id, body.style)
        await task_queue.enqueue_job(job_id)
    elif task_queue:
        # 全部命中缓存（或没有待生成条目）：任务不进队列，必须在这里直接结算，
        # 否则 job 会永远停在 pending，前端一直显示「排队中」。
        task_queue.finalize_job(job_id)
    result = {"ok": True, "job_id": job_id, "reused": reused}
    if url_errors:
        result["warnings"] = url_errors
    return result


@router.get("/jobs")
def list_jobs(user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        jobs = db.query(NoteJob).order_by(NoteJob.id.desc()).limit(50).all()
        return {"jobs": [_job_out(j) for j in jobs]}
    finally:
        db.close()


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        job = db.get(NoteJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        out = _job_out(job)
        out["items"] = [
            {
                "id": it.id, "bvid": it.bvid, "title": it.title, "owner": it.owner,
                "cover": it.cover, "duration": it.duration, "status": it.status, "error": it.error,
            }
            for it in job.items
        ]
        return out
    finally:
        db.close()


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_csrf)])
def delete_job(job_id: int, user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        job = db.get(NoteJob, job_id)
        if job:
            db.delete(job)
            db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/items/{item_id}/retry", dependencies=[Depends(require_csrf)])
async def retry_item(item_id: int, user: str = Depends(require_session)) -> dict:
    """重试单个失败的笔记条目。"""
    if not task_queue:
        raise HTTPException(status_code=500, detail="任务队列未就绪")
    db = SessionLocal()
    try:
        it = db.get(NoteItem, item_id)
        if not it:
            raise HTTPException(status_code=404, detail="条目不存在")
        if it.status == "running":
            raise HTTPException(status_code=400, detail="该条目正在生成中")
        it.status = "pending"
        it.error = None
        job_id = it.job_id
        job = db.get(NoteJob, job_id)
        if job:
            job.status = "running"
            job.message = "重试中"
        db.commit()
    finally:
        db.close()
    await task_queue.retry_item(item_id)
    return {"ok": True}


@router.get("/export")
def export_all(user: str = Depends(require_session)) -> PlainTextResponse:
    """把全部已完成笔记合并导出为一个 Markdown 文件。"""
    db = SessionLocal()
    try:
        items = (
            db.query(NoteItem)
            .filter(NoteItem.status == "done")
            .order_by(NoteItem.id.desc())
            .all()
        )
        parts = ["# 哔记 · 全部笔记导出\n"]
        for it in items:
            parts.append(f"\n---\n\n## {it.title or it.bvid}\n")
            meta = " · ".join(x for x in [it.owner, f"{it.duration}s" if it.duration else ""] if x)
            if meta:
                parts.append(f"> {meta}\n")
            parts.append((it.note_md or "（暂无内容）") + "\n")
        body = "\n".join(parts)
        filename = f"bilirecall-notes-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
        return PlainTextResponse(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()


@router.get("/items/{item_id}")
def item_detail(item_id: int, user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        it = db.get(NoteItem, item_id)
        if not it:
            raise HTTPException(status_code=404, detail="笔记不存在")
        return {
            "id": it.id, "bvid": it.bvid, "title": it.title, "owner": it.owner,
            "cover": it.cover, "duration": it.duration, "status": it.status,
            "error": it.error, "note_md": it.note_md,
        }
    finally:
        db.close()


@router.get("/items/{item_id}/markdown")
def item_markdown(item_id: int, user: str = Depends(require_session)) -> PlainTextResponse:
    db = SessionLocal()
    try:
        it = db.get(NoteItem, item_id)
        if not it:
            raise HTTPException(status_code=404, detail="笔记不存在")
        filename = f"{it.title or it.bvid}.md".replace("/", "_")
        return PlainTextResponse(
            content=it.note_md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        db.close()
