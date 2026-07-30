"""Servis başlangıcında kayıt klasörü <-> veritabanı senkronizasyonu.

Şartname böl. 9: veritabanında olmayan dosyaları indeksle, dosyası
silinmiş kayıtları `missing` işaretle, `ffprobe` ile geçerliliği doğrula.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.shell import run_command
from app.models.recording import Recording, RecordingStatus

logger = logging.getLogger(__name__)


def probe_video_file(path: Path) -> dict | None:
    result = run_command(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=15.0,
    )
    if not result.ok:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def extract_video_metadata(probe_data: dict) -> dict:
    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    fmt = probe_data.get("format", {})
    metadata: dict = {"width": None, "height": None, "codec": None, "fps": None}

    if video_stream:
        metadata["width"] = video_stream.get("width")
        metadata["height"] = video_stream.get("height")
        metadata["codec"] = video_stream.get("codec_name")
        rate = video_stream.get("avg_frame_rate", "0/0")
        try:
            num_str, den_str = rate.split("/")
            num, den = float(num_str), float(den_str)
            metadata["fps"] = (num / den) if den != 0 else None
        except (ValueError, ZeroDivisionError):
            metadata["fps"] = None

    duration = fmt.get("duration")
    metadata["duration_seconds"] = float(duration) if duration is not None else None
    return metadata


def _index_new_file(file_path: str) -> Recording:
    path = Path(file_path)
    stat = path.stat()
    probe_data = probe_video_file(path)
    started_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    if probe_data is None:
        return Recording(
            filename=path.name,
            file_path=file_path,
            started_at=started_at,
            file_size_bytes=stat.st_size,
            status=RecordingStatus.CORRUPTED,
            is_valid=False,
            error_message="ffprobe dosyayı okuyamadı veya dosya bozuk.",
        )

    metadata = extract_video_metadata(probe_data)
    return Recording(
        filename=path.name,
        file_path=file_path,
        started_at=started_at,
        duration_seconds=metadata["duration_seconds"],
        file_size_bytes=stat.st_size,
        width=metadata["width"],
        height=metadata["height"],
        fps=metadata["fps"],
        codec=metadata["codec"],
        status=RecordingStatus.COMPLETED,
        is_valid=True,
    )


def reconcile_recordings(session: Session, video_dir: Path) -> None:
    video_dir.mkdir(parents=True, exist_ok=True)

    existing = session.exec(select(Recording)).all()
    by_path = {rec.file_path: rec for rec in existing}
    disk_files = {str(p) for p in video_dir.glob("*.mp4")}

    for file_path, rec in by_path.items():
        if file_path not in disk_files and rec.status != RecordingStatus.MISSING:
            rec.status = RecordingStatus.MISSING
            rec.is_valid = False
            session.add(rec)
            logger.warning(
                "recording_marked_missing",
                extra={"component": "reconciler", "operation": "reconcile", "file_path": file_path},
            )

    for file_path in disk_files:
        if file_path in by_path:
            continue
        recording = _index_new_file(file_path)
        session.add(recording)
        logger.info(
            "recording_indexed_from_disk",
            extra={
                "component": "reconciler",
                "operation": "reconcile",
                "file_path": file_path,
                "status": recording.status.value,
            },
        )

    session.commit()
