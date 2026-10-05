"""轻量异步任务队列：为每个视频抓取字幕并生成知识笔记。

MVP 采用进程内 asyncio worker 池，任务进度写入数据库，
前端通过轮询 /api/notes/jobs/{id} 获取进度。生产可平滑替换为 Celery / RQ。
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..db import SessionLocal
from ..models import BiliAccount, NoteItem, NoteJob
from ..security import decrypt_secret
from .bili_client import BiliClient
from .content_extract import extract_content
from .note_generator import generate_note

log = logging.getLogger("bilirecall.worker")


class TaskQueue:
    def __init__(self, max_concurrency: int = 2) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self._style: dict[int, str] = {}  # job_id -> style

    def set_style(self, job_id: int, style: str) -> None:
        self._style[job_id] = style

    async def enqueue_job(self, job_id: int) -> None:
        asyncio.create_task(self._run_job(job_id))

    async def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(NoteJob, job_id)
            if not job:
                return
            job.status = "running"
            db.commit()

            account = db.query(BiliAccount).first()
            cookies = json.loads(decrypt_secret(account.enc_cookies)) if account else {}
            client = BiliClient(cookies)
            style = self._style.get(job_id, "knowledge")

            item_ids = [it.id for it in job.items]
        finally:
            db.close()

        await asyncio.gather(*(self._run_item(iid, style, client) for iid in item_ids))
        self.finalize_job(job_id)

    async def _run_item(self, item_id: int, style: str, client: BiliClient) -> None:
        async with self._sem:
            db = SessionLocal()
            try:
                item = db.get(NoteItem, item_id)
                if not item:
                    return
                item.status = "running"
                db.commit()
                bvid = item.bvid
            finally:
                db.close()

            try:
                info = await client.get_video_info(bvid)

                def _progress(stage: str) -> None:
                    db2 = SessionLocal()
                    try:
                        it2 = db2.get(NoteItem, item_id)
                        if it2:
                            it2.error = stage  # 阶段提示复用 error 字段，前端轮询可见
                            db2.commit()
                    finally:
                        db2.close()

                extracted = await extract_content(client, info, progress_cb=_progress)
                note_md = await generate_note(
                    {**info, "owner": item.owner or info["owner"]},
                    extracted["text"], style,
                )
                src = extracted["detail"]

                db = SessionLocal()
                try:
                    item = db.get(NoteItem, item_id)
                    item.title = info["title"]
                    item.owner = info["owner"]
                    item.cover = info["cover"]
                    item.duration = info["duration"]
                    item.note_md = note_md
                    item.status = "done"
                    db.commit()
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001
                log.exception("笔记生成失败 %s", bvid)
                db = SessionLocal()
                try:
                    item = db.get(NoteItem, item_id)
                    item.status = "failed"
                    item.error = str(exc)[:500]
                    db.commit()
                finally:
                    db.close()

    async def retry_item(self, item_id: int) -> None:
        """重试单个条目：读取该条目所属任务的风格与账号凭证，重跑后重新结算任务状态。"""
        db = SessionLocal()
        job_id = None
        style = "knowledge"
        try:
            it = db.get(NoteItem, item_id)
            if not it:
                return
            job_id = it.job_id
            job = db.get(NoteJob, job_id)
            if job and job.scope_json:
                try:
                    scope = json.loads(job.scope_json)
                    style = scope.get("style", "knowledge")
                except Exception:  # noqa: BLE001
                    pass
            account = db.query(BiliAccount).first()
            cookies = json.loads(decrypt_secret(account.enc_cookies)) if account else {}
        finally:
            db.close()

        client = BiliClient(cookies)
        await self._run_item(item_id, style, client)
        if job_id is not None:
            self.finalize_job(job_id)

    def finalize_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(NoteJob, job_id)
            if not job:
                return
            total = len(job.items)
            done = sum(1 for it in job.items if it.status == "done")
            failed = sum(1 for it in job.items if it.status == "failed")
            job.total = total
            job.done = done
            job.failed = failed
            job.status = "done" if failed == 0 else ("failed" if done == 0 else "done")
            job.message = f"完成 {done}/{total}，失败 {failed}"
            db.commit()
        finally:
            db.close()
