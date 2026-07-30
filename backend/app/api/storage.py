"""Depolama durumu uç noktası."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_password_already_set
from app.core.config import AppConfig
from app.models.user import User
from app.storage.validator import get_disk_usage

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("")
async def get_storage(
    request: Request, _user: User = Depends(require_password_already_set)
) -> dict:
    config: AppConfig = request.app.state.config
    usage = get_disk_usage(config.storage.mount_path)
    is_mounted = (
        os.path.ismount(config.storage.mount_path) if config.storage.require_mountpoint else True
    )
    return {
        "success": True,
        "data": {
            "mount_path": config.storage.mount_path,
            "is_mounted": is_mounted,
            "total_bytes": usage.total_bytes,
            "used_bytes": usage.used_bytes,
            "free_bytes": usage.free_bytes,
            "percent_used": round(usage.percent_used, 1),
        },
    }
