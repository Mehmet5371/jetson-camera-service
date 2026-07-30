"""Paylaşılan FastAPI dependency'leri.

Engine/config/recording manager, `app.state` üzerinde main.py'nin lifespan'ı
tarafından kurulur; bu modül yalnızca onlara erişim sağlar.
"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Cookie, Depends, Request
from sqlmodel import Session, select

from app.camera.continuous_focus import AutofocusSupervisor
from app.camera.preview import PreviewManager
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.recording.manager import RecordingManager
from app.security.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, verify_csrf
from app.security.tokens import decode_access_token


def get_config_dep(request: Request) -> AppConfig:
    return request.app.state.config


def get_db_session(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.db_engine) as session:
        yield session


def get_recording_manager(request: Request) -> RecordingManager:
    return request.app.state.recording_manager


def get_preview_manager(request: Request) -> PreviewManager:
    return request.app.state.preview_manager


def get_autofocus_supervisor(request: Request) -> AutofocusSupervisor:
    return request.app.state.autofocus_supervisor


def get_current_user(
    request: Request,
    session: Session = Depends(get_db_session),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE_NAME),
) -> User:
    config: AppConfig = request.app.state.config
    if not config.security.authentication_enabled:
        # Yalnızca geliştirme/test amaçlı: auth kapalıysa ilk (tek) admin döner.
        user = session.exec(select(User)).first()
        if user is None:
            raise AppError(ErrorCode.UNAUTHORIZED, "Kullanıcı bulunamadı.", status_code=401)
        return user

    if not session_token:
        raise AppError(ErrorCode.UNAUTHORIZED, "Oturum bulunamadı, giriş yapın.", status_code=401)

    payload = decode_access_token(session_token)
    verify_csrf(request, csrf_cookie)

    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "Kullanıcı bulunamadı.", status_code=401)
    return user


def require_password_already_set(user: User = Depends(get_current_user)) -> User:
    """Varsayılan parola değiştirilmeden iş uç noktalarının kullanılmasını engeller.

    /api/auth/change-password ve /api/auth/logout bu dependency'yi
    KULLANMAZ (get_current_user'ı doğrudan kullanır) - aksi halde kullanıcı
    parolasını değiştiremeden kilitlenir.
    """
    if user.must_change_password:
        raise AppError(
            ErrorCode.PASSWORD_CHANGE_REQUIRED,
            "Devam etmeden önce varsayılan parolayı değiştirmelisiniz.",
            status_code=403,
        )
    return user
