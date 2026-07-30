"""Kimlik doğrulama uç noktaları: giriş, çıkış, parola değiştirme, whoami."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db_session
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.security.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, generate_csrf_token
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import LoginRateLimiter
from app.security.tokens import ACCESS_TOKEN_TTL_MINUTES, create_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_rate_limiter = LoginRateLimiter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


def _set_session_cookies(response: Response, request: Request, *, token: str, csrf_token: str) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_TTL_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_TTL_MINUTES * 60,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/login")
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> dict:
    config: AppConfig = request.app.state.config
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_key = f"{client_ip}:{payload.username}"

    _login_rate_limiter.check(rate_limit_key)

    user = session.exec(select(User).where(User.username == payload.username)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        _login_rate_limiter.record_failure(rate_limit_key)
        logger.warning(
            "login_failed",
            extra={"component": "auth", "operation": "login", "username": payload.username, "client_ip": client_ip},
        )
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Kullanıcı adı veya parola hatalı.", status_code=401)

    _login_rate_limiter.record_success(rate_limit_key)

    token = create_access_token(user_id=user.id, username=user.username)
    csrf_token = generate_csrf_token()
    _set_session_cookies(response, request, token=token, csrf_token=csrf_token)

    logger.info(
        "login_succeeded",
        extra={"component": "auth", "operation": "login", "username": user.username, "client_ip": client_ip},
    )

    return {
        "success": True,
        "data": {
            "username": user.username,
            "must_change_password": user.must_change_password,
        },
    }


@router.post("/logout")
async def logout(response: Response, user: User = Depends(get_current_user)) -> dict:
    _clear_session_cookies(response)
    logger.info("logout", extra={"component": "auth", "operation": "logout", "username": user.username})
    return {"success": True, "data": {}}


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "success": True,
        "data": {
            "username": user.username,
            "must_change_password": user.must_change_password,
        },
    }


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    if not verify_password(payload.current_password, user.password_hash):
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Mevcut parola hatalı.", status_code=401)

    db_user = session.get(User, user.id)
    assert db_user is not None
    db_user.password_hash = hash_password(payload.new_password)
    db_user.must_change_password = False
    session.add(db_user)
    session.commit()

    # Parola değiştikten sonra tüm eski oturumların geçersiz kılınması için
    # yeni bir token üret ve cookie'leri yenile (basit ama etkili bir
    # "diğer oturumları kapat" yaklaşımı - token'da parola sürümü yok,
    # bu yüzden eski token'lar süresi dolana kadar teknik olarak geçerli
    # kalır; çoklu-oturum iptali gelecekte bir 'token_version' alanıyla
    # güçlendirilebilir).
    token = create_access_token(user_id=db_user.id, username=db_user.username)
    csrf_token = generate_csrf_token()
    _set_session_cookies(response, request, token=token, csrf_token=csrf_token)

    logger.info(
        "password_changed",
        extra={"component": "auth", "operation": "change_password", "username": db_user.username},
    )
    return {"success": True, "data": {}}
