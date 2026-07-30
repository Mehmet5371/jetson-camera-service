"""İlk kurulumda varsayılan admin kullanıcısını oluşturur.

install.sh `.env` dosyasına rastgele üretilmiş bir parolanın Argon2
hash'ini `JETSON_CAMERA_ADMIN_PASSWORD_HASH` olarak yazar (Faz 7). Bu
fonksiyon servis her başladığında çağrılır ama `users` tablosu BOŞSA
yalnızca bir kez satır ekler - var olan kullanıcıları asla üzerine yazmaz.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.env import get_required_env
from app.models.user import User

logger = logging.getLogger(__name__)


def seed_default_admin(session: Session) -> None:
    existing = session.exec(select(User)).first()
    if existing is not None:
        return

    username = get_required_env("JETSON_CAMERA_ADMIN_USERNAME")
    password_hash = get_required_env("JETSON_CAMERA_ADMIN_PASSWORD_HASH")

    user = User(username=username, password_hash=password_hash, must_change_password=True)
    session.add(user)
    session.commit()
    logger.info(
        "default_admin_user_created",
        extra={"component": "user_seed", "operation": "seed", "username": username},
    )
