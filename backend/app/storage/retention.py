"""Disk doluluğuna göre saklama (retention) politikası.

Şartname böl. 8: disk dolmaya yaklaşınca varsayılan GÜVENLİ davranış
kaydı durdurmaktır (bkz. recording.low_space_action, RecordingManager).
Bu modül yalnızca retention.enabled=true VE retention.delete_oldest_files=true
olduğunda devreye girer ve YALNIZCA status=completed kayıtları siler -
aktif olarak kaydedilen bir dosya asla silinmez.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import AppConfig
from app.models.recording import Recording, RecordingStatus
from app.storage.validator import DiskUsage

logger = logging.getLogger(__name__)


def enforce_retention(session: Session, config: AppConfig, usage: DiskUsage) -> list[str]:
    retention = config.retention
    deleted: list[str] = []

    if not retention.enabled or not retention.delete_oldest_files:
        return deleted

    if usage.percent_used < retention.maximum_storage_percent:
        return deleted

    recordings = session.exec(
        select(Recording)
        .where(Recording.status == RecordingStatus.COMPLETED)
        .order_by(Recording.started_at.asc())
    ).all()

    remaining_percent = usage.percent_used
    for recording in recordings:
        if remaining_percent < retention.maximum_storage_percent:
            break

        file_path = Path(recording.file_path)
        file_size = recording.file_size_bytes or 0
        try:
            file_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "retention_delete_failed",
                extra={
                    "component": "retention",
                    "operation": "enforce",
                    "file_path": str(file_path),
                    "error": str(exc),
                },
            )
            continue

        session.delete(recording)
        deleted.append(str(file_path))
        if usage.total_bytes > 0:
            remaining_percent -= (file_size / usage.total_bytes) * 100
        logger.warning(
            "retention_deleted_recording",
            extra={"component": "retention", "operation": "enforce", "file_path": str(file_path)},
        )

    session.commit()
    return deleted
