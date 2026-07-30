"""JWT erişim token'ları (kısa ömürlü, httponly cookie içinde taşınır).

Secret key .env'den (`JETSON_CAMERA_SECRET_KEY`) okunur - koda veya
config.yaml'a asla yazılmaz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.core.env import get_required_env
from app.core.errors import AppError, ErrorCode

_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_MINUTES = 12 * 60  # 12 saat - "kısa ömürlü access token" gereksinimi


class InvalidTokenError(AppError):
    def __init__(self, message: str = "Oturum geçersiz veya süresi dolmuş.") -> None:
        super().__init__(ErrorCode.UNAUTHORIZED, message, status_code=401)


def create_access_token(*, user_id: int, username: str) -> str:
    secret_key = get_required_env("JETSON_CAMERA_SECRET_KEY")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    secret_key = get_required_env("JETSON_CAMERA_SECRET_KEY")
    try:
        return jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise InvalidTokenError() from exc
