"""Cookie tabanlı JWT oturumu + CSRF çift-gönderim (double-submit) koruması.

Oturum token'ı httponly cookie'de taşınır (XSS ile çalınamaz). CSRF
koruması için ayrı, httponly OLMAYAN bir cookie'ye rastgele bir token
yazılır; tarayıcı JS bunu okuyup durum değiştiren (POST/PUT/DELETE)
isteklerde bir header olarak geri göndermek zorundadır - yalnızca cookie
otomatik gönderildiği için bunu bir saldırgan sitesi taklit edemez.
"""

from __future__ import annotations

import secrets

from fastapi import Request

from app.core.errors import AppError, ErrorCode

SESSION_COOKIE_NAME = "jetson_camera_session"
CSRF_COOKIE_NAME = "jetson_camera_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class CsrfValidationError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.CSRF_TOKEN_INVALID, "CSRF doğrulaması başarısız.", status_code=403)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(request: Request, csrf_cookie: str | None) -> None:
    if request.method in _SAFE_METHODS:
        return
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not header_value or not secrets.compare_digest(csrf_cookie, header_value):
        raise CsrfValidationError()
