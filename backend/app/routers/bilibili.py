"""B 站相关路由：扫码登录、账号信息、收藏夹与内容列表。"""
from __future__ import annotations

import base64
import io
import json

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import SessionLocal
from ..deps import require_session
from ..models import BiliAccount
from ..security import decrypt_secret, encrypt_secret
from ..services.bili_client import BiliClient, BiliError

router = APIRouter(prefix="/api/bilibili", tags=["bilibili"])


@router.get("/qr")
async def generate_qr(user: str = Depends(require_session)) -> dict:
    try:
        data = await BiliClient.generate_qr()
    except BiliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    img = qrcode.make(data["url"])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"qrcode_key": data["qrcode_key"], "image": f"data:image/png;base64,{b64}"}


@router.get("/qr/poll")
async def poll_qr(key: str = Query(...), user: str = Depends(require_session)) -> dict:
    try:
        res = await BiliClient.poll_qr(key)
    except BiliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    code = res.get("code")
    if code == 0:
        cookies = res.get("cookies", {})
        client = BiliClient(cookies)
        try:
            info = await client.get_self_info()
        except BiliError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        db = SessionLocal()
        try:
            account = db.query(BiliAccount).first()
            if not account:
                account = BiliAccount(mid=info["mid"], uname=info["uname"], face=info["face"],
                                      enc_cookies=encrypt_secret(json.dumps(cookies)))
                db.add(account)
            else:
                account.mid = info["mid"]
                account.uname = info["uname"]
                account.face = info["face"]
                account.enc_cookies = encrypt_secret(json.dumps(cookies))
            db.commit()
        finally:
            db.close()
        return {"status": "confirmed", "account": info}

    mapping = {86038: "expired", 86090: "scanned", 86101: "pending"}
    return {"status": mapping.get(code, "unknown"), "message": res.get("message")}


@router.get("/account")
def get_account(user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        account = db.query(BiliAccount).first()
        if not account:
            return {"bound": False}
        return {"bound": True, "mid": account.mid, "uname": account.uname, "face": account.face}
    finally:
        db.close()


@router.delete("/account")
def unbind(user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        account = db.query(BiliAccount).first()
        if account:
            db.delete(account)
            db.commit()
        return {"ok": True}
    finally:
        db.close()


def _current_client() -> BiliClient:
    db = SessionLocal()
    try:
        account = db.query(BiliAccount).first()
        if not account:
            raise HTTPException(status_code=400, detail="尚未绑定 B 站账号")
        cookies = json.loads(decrypt_secret(account.enc_cookies))
        return BiliClient(cookies)
    finally:
        db.close()


@router.get("/resolve")
async def resolve_url(url: str = Query(...), user: str = Depends(require_session)) -> dict:
    """解析粘贴的 B 站链接，返回视频卡片信息（供前端加入选片）。"""
    from ..services.url_parser import extract_bvid
    bvid = extract_bvid(url.strip())
    if not bvid:
        raise HTTPException(status_code=400, detail="无法识别的 B 站链接，请粘贴视频页地址或 BV 号")
    client = _current_client()
    try:
        info = await client.get_video_info(bvid)
    except BiliError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "item": {
            "bvid": info["bvid"],
            "title": info["title"],
            "cover": info["cover"],
            "duration": info["duration"],
            "owner": info["owner"],
        }
    }


@router.get("/folders")
async def list_folders(user: str = Depends(require_session)) -> dict:
    db = SessionLocal()
    try:
        account = db.query(BiliAccount).first()
        if not account:
            raise HTTPException(status_code=400, detail="尚未绑定 B 站账号")
        mid = account.mid
        cookies = json.loads(decrypt_secret(account.enc_cookies))
    finally:
        db.close()
    try:
        folders = await BiliClient(cookies).list_folders(mid)
    except BiliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"folders": folders}


@router.get("/folders/{media_id}/resources")
async def list_resources(media_id: int, pn: int = 1, ps: int = 20,
                         user: str = Depends(require_session)) -> dict:
    client = _current_client()
    try:
        return await client.list_resources(media_id, pn, ps)
    except BiliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
