"""鉴权路由：口令登录、登出、会话状态。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from ..config import get_settings
from ..deps import CSRF_COOKIE, SESSION_COOKIE, require_session
from ..schemas import LoginRequest
from ..security import create_session_token, new_csrf_token, verify_app_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, response: Response) -> dict:
    if not verify_app_password(body.password):
        return {"ok": False, "message": "口令错误"}
    s = get_settings()
    token = create_session_token()
    csrf = new_csrf_token()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max_age, httponly=True, samesite="lax", secure=s.cookie_secure, path="/"
    )
    # CSRF Cookie 需可被前端 JS 读取，故 httponly=False
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=max_age, httponly=False, samesite="lax", secure=s.cookie_secure, path="/"
    )
    return {"ok": True, "csrf": csrf}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
def session_state(user: str = Depends(require_session)) -> dict:
    return {"authenticated": True, "user": user}
