"""Kayıt (video) metadata modeli.

Şartname böl. 9'daki alan listesini birebir karşılar. SQLModel hem
Pydantic doğrulama hem de SQLAlchemy ORM tablo tanımını tek sınıfta
birleştirir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


class RecordingStatus(str, Enum):
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CORRUPTED = "corrupted"
    MISSING = "missing"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Recording(SQLModel, table=True):
    __tablename__ = "recordings"

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(index=True)
    file_path: str = Field(unique=True, index=True)
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    status: RecordingStatus = Field(default=RecordingStatus.RECORDING, index=True)
    is_valid: bool = Field(default=True)
    thumbnail_path: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    error_message: str | None = None
