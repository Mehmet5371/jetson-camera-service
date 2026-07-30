"""Veritabanı engine/session yönetimi.

SQLite dosyası config.storage.database_path'te tutulur (SSD'de, bkz. Faz 1
depolama kararı). Şema değişiklikleri Alembic ile yönetilir (bkz.
migrations/); bu modül yalnızca çalışma zamanı engine/session'ını kurar,
CREATE TABLE çağırmaz - şema Alembic migration'larından gelir.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import AppConfig


def create_db_engine(config: AppConfig) -> Engine:
    db_path = Path(config.storage.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


def get_session(engine: Engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
