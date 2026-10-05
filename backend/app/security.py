"""安全模块：
1) Web 会话使用 JWT（HttpOnly Cookie 承载），并配 CSRF 双提交校验。
2) B 站登录凭证（SESSDATA / bili_jct 等）落库前用 Fernet 对称加密，
   密钥由 SECRET_KEY 经 PBKDF2 派生，避免明文凭证泄露。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ALGO = "HS256"


def _fernet() -> Fernet:
    s = get_settings()
    raw = hashlib.pbkdf2_hmac("sha256", s.secret_key.encode(), b"bilirecall-cred-v1", 200_000)
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover
        raise ValueError("凭证解密失败：SECRET_KEY 可能已变更") from exc


# ── Web 会话 JWT ────────────────────────────────────────────
def create_session_token(subject: str = "owner") -> str:
    s = get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + s.session_ttl_hours * 3600,
    }
    return jwt.encode(payload, s.secret_key, algorithm=_ALGO)


def decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None


# ── 口令校验 & CSRF ─────────────────────────────────────────
def verify_app_password(candidate: str) -> bool:
    return hmac.compare_digest(candidate or "", get_settings().app_password)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
