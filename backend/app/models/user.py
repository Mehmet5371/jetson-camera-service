"""Dashboard kullanıcı modeli.

Basit tek/az kullanıcılı model - şartname böl. 14 minimum gereksinimleri
karşılar (kullanıcı adı + Argon2 hash, ilk girişte parola değişikliği
zorunluluğu). Çoklu kullanıcı/roller (böl. 26) opsiyonel bir gelecek
modülü olarak bilinçli olarak kapsam dışı bırakıldı.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    must_change_password: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
