"""FastAPI 依赖：会话鉴权与 CSRF 校验。"""
from __future__ import annotations

import hmac

from fastapi import Cookie, Header, HTTPException, status

from .security import decode_session_token

SESSION_COOKIE = "br_session"
CSRF_COOKIE = "br_csrf"
CSRF_HEADER = "x-csrf-token"


def require_session(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> str:
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_session_token(session)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效")
    return payload.get("sub", "owner")


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias=CSRF_HEADER),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
) -> None:
    """双提交 Cookie 校验：请求头与 Cookie 中的 CSRF 令牌必须一致。"""
    if not csrf_cookie or not x_csrf_token or not hmac.compare_digest(csrf_cookie, x_csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
