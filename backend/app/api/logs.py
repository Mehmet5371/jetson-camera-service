"""Servis loglarını görüntüleme/indirme uç noktaları.

Loglar `app.core.logging` tarafından JSON satırları olarak
`{logging.directory}/service.log` dosyasına yazılır (bkz. Faz 2). Bu
modül o dosyayı okur - ayrı bir log deposu yoktur.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from app.api.deps import require_password_already_set
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.models.user import User

router = APIRouter(prefix="/api/logs", tags=["logs"])

_MAX_LINES_READ = 20_000


def _log_file_path(config: AppConfig) -> Path:
    return Path(config.logging.directory) / "service.log"


@router.get("")
async def get_logs(
    request: Request,
    _user: User = Depends(require_password_already_set),
    level: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict:
    config: AppConfig = request.app.state.config
    log_path = _log_file_path(config)
    if not log_path.is_file():
        return {"success": True, "data": {"items": []}}

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-_MAX_LINES_READ:]

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if level and entry.get("level") != level.upper():
            continue
        entries.append(entry)

    return {"success": True, "data": {"items": entries[-limit:]}}


@router.get("/download")
async def download_logs(
    request: Request, _user: User = Depends(require_password_already_set)
) -> FileResponse:
    config: AppConfig = request.app.state.config
    log_path = _log_file_path(config)
    if not log_path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "Log dosyası bulunamadı.", status_code=404)

    return FileResponse(
        path=log_path,
        media_type="text/plain",
        filename="jetson-camera-service.log",
    )
