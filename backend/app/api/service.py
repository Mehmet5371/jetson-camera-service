"""Kamera servisi (nvargus-daemon) uzaktan yeniden başlatma.

`sudo systemctl restart nvargus-daemon` NOPASSWD sudoers kuralıyla
çalıştırılır (bkz. kurulum dokümantasyonu, Faz 7). `-n` bayrağı şifre
istemesi gerekirse (yanlış yapılandırılmış sudoers) beklemeden anında
başarısız olmasını sağlar.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_recording_manager, require_password_already_set
from app.core.errors import AppError, ErrorCode
from app.core.shell import run_command
from app.models.user import User
from app.recording.manager import RecordingManager
from app.recording.state import RecordingState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/service", tags=["service"])


@router.post("/restart-camera")
async def restart_camera_service(
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
) -> dict:
    if manager.session.state != RecordingState.IDLE:
        raise AppError(
            ErrorCode.CAMERA_SERVICE_BUSY,
            "Aktif bir kayıt sürerken kamera servisi yeniden başlatılamaz. Önce kaydı durdurun.",
            status_code=409,
        )

    result = run_command(["sudo", "-n", "systemctl", "restart", "nvargus-daemon"], timeout=15.0)
    if not result.ok:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "nvargus-daemon yeniden başlatılamadı.",
            status_code=500,
            details={"stderr": result.stderr.strip(), "returncode": result.returncode},
        )

    logger.warning(
        "camera_service_restarted",
        extra={"component": "service_api", "operation": "restart_camera"},
    )
    return {"success": True, "data": {}}
