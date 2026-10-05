"""ima 知识库连接：绑定配置、连通测试（官方 OpenAPI，Header 鉴权）。

接口规格来自官方 ima-skills 包（https://app-dl.ima.qq.com/skills/）：
- Base: POST https://ima.qq.com/openapi/<模块>/v1/<action>
  · 知识库模块 wiki/v1（get_addable_knowledge_base_list、add_knowledge …）
  · 笔记模块 note/v1（import_doc 新建笔记、append_doc 追加 …）
- Header: ima-openapi-clientid / ima-openapi-apikey
- 连通测试用 search_knowledge_base（query 传空串返回全部知识库，只读且轻量）

推送笔记的链路（实测 2026-09）：markdown 不能直接塞进 note_info.content_id
（会被 add_knowledge 以 business code 220001 参数错误拒绝），正确做法是
先 note/v1/import_doc 建出 note_id，再把 note_id 作为 content_id 关联进知识库。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import SessionLocal
from ..deps import require_session
from ..models import ImaAccount
from ..security import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/ima", tags=["ima"])

IMA_BASE = "https://ima.qq.com/openapi"
IMA_WIKI = "wiki/v1"
IMA_NOTE = "note/v1"
TIMEOUT = 20


class ImaBindIn(BaseModel):
    client_id: str
    api_key: str


async def _ima_call(client_id: str, api_key: str, path: str, body: dict) -> dict:
    """path 形如 "wiki/v1/search_knowledge_base"、"note/v1/import_doc"。"""
    headers = {
        "ima-openapi-clientid": client_id,
        "ima-openapi-apikey": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{IMA_BASE}/{path}", json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"ima 接口 HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    # 业务码非 0 视为失败（官方约定 code=0 成功）
    if isinstance(data, dict) and data.get("code") not in (0, None):
        raise RuntimeError(f"ima 业务错误 {data.get('code')}: {data.get('msg') or data.get('message')}")
    return data


def _get_config() -> ImaAccount | None:
    db = SessionLocal()
    try:
        return db.query(ImaAccount).first()
    finally:
        db.close()


@router.get("/config")
def get_config(user: str = Depends(require_session)) -> dict:
    cfg = _get_config()
    if not cfg:
        return {"bound": False}
    key = ""
    try:
        key = decrypt_secret(cfg.enc_api_key)
    except ValueError:
        pass
    return {
        "bound": True,
        "client_id": cfg.client_id,
        "api_key_masked": (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("已配置" if key else ""),
    }


@router.post("/bind")
async def bind_ima(payload: ImaBindIn, user: str = Depends(require_session)) -> dict:
    """保存配置并立即做一次连通校验，失败则不落库。"""
    client_id = payload.client_id.strip()
    api_key = payload.api_key.strip()
    if not client_id or not api_key:
        raise HTTPException(status_code=400, detail="Client ID 与 API Key 均不能为空")

    try:
        data = await _ima_call(client_id, api_key, f"{IMA_WIKI}/search_knowledge_base", {"query": "", "limit": 5})
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc

    kb_list = ((data or {}).get("data") or {}).get("knowledge_base_list") or []
    db = SessionLocal()
    try:
        cfg = db.query(ImaAccount).first()
        if cfg:
            cfg.client_id = client_id
            cfg.enc_api_key = encrypt_secret(api_key)
        else:
            cfg = ImaAccount(client_id=client_id, enc_api_key=encrypt_secret(api_key))
            db.add(cfg)
        db.commit()
    finally:
        db.close()
    return {
        "ok": True,
        "message": f"绑定成功，可见 {len(kb_list)} 个知识库",
        "knowledge_bases": [{"id": k.get("id"), "name": k.get("name")} for k in kb_list],
    }


@router.post("/test")
async def test_ima(user: str = Depends(require_session)) -> dict:
    """用已保存的配置测试连通。"""
    cfg = _get_config()
    if not cfg:
        raise HTTPException(status_code=400, detail="尚未绑定 ima")
    try:
        key = decrypt_secret(cfg.enc_api_key)
        data = await _ima_call(cfg.client_id, key, f"{IMA_WIKI}/search_knowledge_base", {"query": "", "limit": 5})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
    kb_list = ((data or {}).get("data") or {}).get("knowledge_base_list") or []
    return {
        "ok": True,
        "message": f"连接正常，可见 {len(kb_list)} 个知识库",
        "knowledge_bases": [{"id": k.get("id"), "name": k.get("name")} for k in kb_list],
    }


@router.delete("/bind")
def unbind_ima(user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        for cfg in db.query(ImaAccount).all():
            db.delete(cfg)
        db.commit()
    finally:
        db.close()
    return {"ok": True}


# ── 笔记推送到 ima 知识库 ──────────────────────────────────
class PushIn(BaseModel):
    title: str
    markdown: str
    knowledge_base_id: str


async def _cred() -> tuple[str, str]:
    cfg = _get_config()
    if not cfg:
        raise HTTPException(status_code=400, detail="尚未绑定 ima，请先在侧栏完成连接")
    return cfg.client_id, decrypt_secret(cfg.enc_api_key)


async def push_markdown_to_kb(client_id: str, api_key: str, knowledge_base_id: str,
                              title: str, markdown: str) -> dict:
    """把 Markdown 正文送进 ima 知识库：先建笔记拿 note_id，再关联进知识库。

    note_info.content_id 只接受 ima 侧真实存在的笔记 ID；塞原始 markdown 会被
    add_knowledge 以 220001 参数错误拒绝。给 add_knowledge 额外传 media_id 同理
    （会回 220001 invalid media_id），笔记类型只认 note_info.content_id。
    """
    if not markdown.strip():
        raise HTTPException(status_code=400, detail="笔记内容为空，无法推送")

    # 1) 在 ima 笔记中创建文档，拿到 note_id
    try:
        note_res = await _ima_call(
            client_id, api_key, f"{IMA_NOTE}/import_doc",
            {"content": markdown, "content_format": 1, "title": title},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"创建 ima 笔记失败：{exc}") from exc

    note_id = str(((note_res or {}).get("data") or {}).get("note_id") or "").strip()
    if not note_id:
        raise HTTPException(status_code=400, detail="创建 ima 笔记失败：接口未返回 note_id")

    # 2) 把这篇笔记关联进目标知识库（media_type=11 笔记，content_id=note_id）
    body = {
        "media_type": 11,
        "title": title,
        "knowledge_base_id": knowledge_base_id,
        "note_info": {"content_id": note_id},
    }
    try:
        data = await _ima_call(client_id, api_key, f"{IMA_WIKI}/add_knowledge", body)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"推送到知识库失败：{exc}") from exc

    media_id = ((data or {}).get("data") or {}).get("media_id") or ""
    return {"note_id": note_id, "media_id": media_id}


@router.get("/kb-list")
async def kb_list(user: str = Depends(require_session)) -> dict:
    """列出当前账号有权限添加内容的知识库（用于推送目标选择）。"""
    client_id, key = await _cred()
    try:
        data = await _ima_call(client_id, key, f"{IMA_WIKI}/get_addable_knowledge_base_list",
                               {"cursor": "", "limit": 50})
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"获取知识库列表失败：{exc}") from exc
    d = (data or {}).get("data") or {}
    kbs = d.get("addable_knowledge_base_list") or d.get("knowledge_base_list") or []
    return {"knowledge_bases": [{"id": k.get("id"), "name": k.get("name")} for k in kbs]}


@router.post("/push")
async def push_note(payload: PushIn, user: str = Depends(require_session)) -> dict:
    """把一篇笔记 Markdown 推送为 ima 知识库中的笔记条目。"""
    client_id, key = await _cred()
    title = payload.title.strip() or "未命名笔记"
    res = await push_markdown_to_kb(client_id, key, payload.knowledge_base_id, title, payload.markdown)
    return {"ok": True, "message": f"已推送到知识库（media_id={res['media_id'] or 'ok'}）", "note_id": res["note_id"]}
