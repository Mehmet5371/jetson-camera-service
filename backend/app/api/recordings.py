"""Kayıt başlatma/durdurma, listeleme, izleme, indirme, silme uç noktaları."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, func, select

from app.api.deps import (
    get_db_session,
    get_preview_manager,
    get_recording_manager,
    require_password_already_set,
)
from app.camera.preview import PreviewManager
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.models.recording import Recording, RecordingStatus
from app.models.user import User
from app.recording.manager import RecordingManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_RANGE_HEADER_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")

_SORT_FIELDS = {
    "started_at": Recording.started_at,
    "file_size_bytes": Recording.file_size_bytes,
    "duration_seconds": Recording.duration_seconds,
    "status": Recording.status,
    "filename": Recording.filename,
}


def _resolve_safe_path(recording: Recording, allowed_dir: Path) -> Path:
    """Yalnızca DB'deki file_path'i kullanır (kullanıcıdan gelen ham yol asla
    kullanılmaz) ama yine de savunma amaçlı olarak dosyanın gerçekten izin
    verilen kayıt klasörünün İÇİNDE olduğunu doğrular (böl. 10 gereği)."""
    resolved = Path(recording.file_path).resolve()
    allowed_resolved = allowed_dir.resolve()
    if allowed_resolved not in resolved.parents and resolved != allowed_resolved:
        raise AppError(
            ErrorCode.PATH_TRAVERSAL_REJECTED,
            "Dosya izin verilen kayıt klasörünün dışında.",
            status_code=403,
        )
    return resolved


@router.post("/start")
async def start_recording(
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
    preview: PreviewManager = Depends(get_preview_manager),
) -> dict:
    # Kamera Argus'ta tek proses kısıtlaması nedeniyle önizleme ve kayıt
    # aynı anda çalışamaz - kayıt başlatılırken önizleme varsa otomatik
    # durdurulur (kullanıcının önce elle durdurmasını istemek yerine).
    if preview.is_active:
        await preview.stop()

    session = await manager.start_recording()
    return {
        "success": True,
        "data": {
            "state": session.state.value,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "base_filename": session.base_filename,
        },
    }


@router.post("/stop")
async def stop_recording(
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
) -> dict:
    session = await manager.stop_recording()
    return {"success": True, "data": {"state": session.state.value}}


@router.get("")
async def list_recordings(
    _user: User = Depends(require_password_already_set),
    session: Session = Depends(get_db_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    sort_by: Literal["started_at", "file_size_bytes", "duration_seconds", "status", "filename"] = "started_at",
    sort_order: Literal["asc", "desc"] = "desc",
    status_filter: RecordingStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=256),
) -> dict:
    query = select(Recording)
    count_query = select(func.count()).select_from(Recording)

    if status_filter is not None:
        query = query.where(Recording.status == status_filter)
        count_query = count_query.where(Recording.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.where(Recording.filename.like(pattern))
        count_query = count_query.where(Recording.filename.like(pattern))

    sort_column = _SORT_FIELDS[sort_by]
    query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    total = session.exec(count_query).one()
    recordings = session.exec(query).all()

    return {
        "success": True,
        "data": {
            "items": [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "duration_seconds": r.duration_seconds,
                    "file_size_bytes": r.file_size_bytes,
                    "width": r.width,
                    "height": r.height,
                    "codec": r.codec,
                    "status": r.status.value,
                    "is_valid": r.is_valid,
                }
                for r in recordings
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    }


@router.get("/{recording_id}")
async def get_recording(
    recording_id: int,
    _user: User = Depends(require_password_already_set),
    session: Session = Depends(get_db_session),
) -> dict:
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise AppError(ErrorCode.NOT_FOUND, "Kayıt bulunamadı.", status_code=404)

    return {
        "success": True,
        "data": {
            "id": recording.id,
            "filename": recording.filename,
            "started_at": recording.started_at.isoformat() if recording.started_at else None,
            "ended_at": recording.ended_at.isoformat() if recording.ended_at else None,
            "duration_seconds": recording.duration_seconds,
            "file_size_bytes": recording.file_size_bytes,
            "width": recording.width,
            "height": recording.height,
            "fps": recording.fps,
            "codec": recording.codec,
            "status": recording.status.value,
            "is_valid": recording.is_valid,
            "thumbnail_path": recording.thumbnail_path,
            "error_message": recording.error_message,
            "created_at": recording.created_at.isoformat(),
        },
    }


async def _iter_file_range(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _stream_video_response(request: Request, path: Path, *, download: bool) -> StreamingResponse:
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    headers = {"Accept-Ranges": "bytes"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'

    if range_header:
        match = _RANGE_HEADER_PATTERN.match(range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Geçersiz Range header'ı.")
        start_str, end_str = match.groups()
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)

        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range karşılanamıyor.")

        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(
            _iter_file_range(path, start, end),
            status_code=206,
            media_type="video/mp4",
            headers=headers,
        )

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        _iter_file_range(path, 0, file_size - 1),
        status_code=200,
        media_type="video/mp4",
        headers=headers,
    )


@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: int,
    request: Request,
    _user: User = Depends(require_password_already_set),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    config: AppConfig = request.app.state.config
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise AppError(ErrorCode.NOT_FOUND, "Kayıt bulunamadı.", status_code=404)

    path = _resolve_safe_path(recording, Path(config.storage.video_path))
    if not path.is_file():
        raise AppError(ErrorCode.RECORDING_FILE_NOT_FOUND, "Kayıt dosyası diskte bulunamadı.", status_code=404)

    return _stream_video_response(request, path, download=False)


@router.get("/{recording_id}/download")
async def download_recording(
    recording_id: int,
    request: Request,
    _user: User = Depends(require_password_already_set),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    config: AppConfig = request.app.state.config
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise AppError(ErrorCode.NOT_FOUND, "Kayıt bulunamadı.", status_code=404)

    path = _resolve_safe_path(recording, Path(config.storage.video_path))
    if not path.is_file():
        raise AppError(ErrorCode.RECORDING_FILE_NOT_FOUND, "Kayıt dosyası diskte bulunamadı.", status_code=404)

    return _stream_video_response(request, path, download=True)


@router.delete("/{recording_id}")
async def delete_recording(
    recording_id: int,
    request: Request,
    _user: User = Depends(require_password_already_set),
    session: Session = Depends(get_db_session),
) -> dict:
    config: AppConfig = request.app.state.config
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise AppError(ErrorCode.NOT_FOUND, "Kayıt bulunamadı.", status_code=404)

    if recording.status in (RecordingStatus.RECORDING, RecordingStatus.FINALIZING):
        # Pratikte bu duruma düşülmez: aktif kayıt henüz reconciler tarafından
        # indekslenmemiş olur (dosya kayıt bitene kadar DB'ye girmez). Yine de
        # şartname böl. 10'un açık gereksinimi olduğu için savunma amaçlı
        # kontrol ediliyor.
        raise AppError(
            ErrorCode.RECORDING_DELETE_FORBIDDEN,
            "Aktif olarak kaydedilen bir dosya silinemez.",
            status_code=409,
        )

    path = _resolve_safe_path(recording, Path(config.storage.video_path))
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AppError(
            ErrorCode.INTERNAL_ERROR, f"Dosya silinemedi: {exc}", status_code=500
        ) from exc

    session.delete(recording)
    session.commit()

    logger.info(
        "recording_deleted",
        extra={"component": "recordings_api", "operation": "delete", "recording_id": recording_id},
    )
    return {"success": True, "data": {}}
