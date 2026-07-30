"""Genel durum ve sistem bilgisi uç noktaları + canlı durum WebSocket'i."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.api.deps import get_recording_manager, require_password_already_set
from app.core.config import AppConfig
from app.core.system_info import get_resource_usage, get_system_info
from app.models.user import User
from app.recording.manager import RecordingManager
from app.security.tokens import decode_access_token
from app.security.auth import SESSION_COOKIE_NAME
from app.storage.validator import get_disk_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["status"])

_SERVICE_START_MONOTONIC = time.monotonic()
_WS_PUSH_INTERVAL_SECONDS = 2.0


def _build_status_payload(config: AppConfig, manager: RecordingManager) -> dict:
    session = manager.session
    disk_usage = get_disk_usage(config.storage.mount_path)
    resources = get_resource_usage()

    active_file = None
    active_file_size_bytes = None
    if session.output_dir is not None:
        candidates = sorted(session.output_dir.glob(f"{session.base_filename}*.mp4"))
        if candidates:
            active_file = candidates[-1].name
            try:
                active_file_size_bytes = candidates[-1].stat().st_size
            except OSError:
                active_file_size_bytes = None

    return {
        "recording": {
            "state": session.state.value,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "active_filename": active_file,
            "active_file_size_bytes": active_file_size_bytes,
            "process_alive": manager.check_process_alive(),
        },
        "storage": {
            "mount_path": config.storage.mount_path,
            "total_bytes": disk_usage.total_bytes,
            "used_bytes": disk_usage.used_bytes,
            "free_bytes": disk_usage.free_bytes,
            "percent_used": round(disk_usage.percent_used, 1),
        },
        "resources": {
            "cpu_percent": resources.cpu_percent,
            "ram_used_mb": round(resources.ram_used_mb, 1),
            "ram_total_mb": round(resources.ram_total_mb, 1),
            "ram_percent": resources.ram_percent,
            "disk_percent_root": resources.disk_percent_root,
            "cpu_temp_celsius": resources.cpu_temp_celsius,
            "gpu_temp_celsius": resources.gpu_temp_celsius,
        },
        "service_uptime_seconds": round(time.monotonic() - _SERVICE_START_MONOTONIC, 1),
    }


@router.get("/status")
async def get_status(
    request: Request,
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
) -> dict:
    config: AppConfig = request.app.state.config
    return {"success": True, "data": _build_status_payload(config, manager)}


@router.get("/system")
async def get_system(_user: User = Depends(require_password_already_set)) -> dict:
    info = get_system_info()
    return {
        "success": True,
        "data": {
            "hostname": info.hostname,
            "jetson_model": info.jetson_model,
            "jetpack_version": info.jetpack_version,
            "l4t_version": info.l4t_version,
            "ubuntu_version": info.ubuntu_version,
            "ip_addresses": info.ip_addresses,
            "system_time": info.system_time,
            "timezone": info.timezone,
            "uptime_seconds": round(info.uptime_seconds, 1),
        },
    }


async def _authenticate_websocket(websocket: WebSocket, session: Session) -> User | None:
    config: AppConfig = websocket.app.state.config
    if not config.security.authentication_enabled:
        from sqlmodel import select

        return session.exec(select(User)).first()

    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001 - herhangi bir token hatası bağlantıyı reddeder
        return None
    return session.get(User, int(payload["sub"]))


@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket) -> None:
    # get_db_session() Depends() ile kullanılamaz: o dependency HTTP
    # `Request` nesnesi bekliyor, WebSocket route'ları FastAPI'de ayrı bir
    # scope kullanır ve Request tipini otomatik çözemez (gerçek testte
    # "missing 1 required positional argument: 'request'" hatasıyla
    # bulundu). Bu yüzden session burada doğrudan app.state.db_engine'den
    # kuruluyor.
    with Session(websocket.app.state.db_engine) as session:
        user = await _authenticate_websocket(websocket, session)
    if user is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    config: AppConfig = websocket.app.state.config
    manager: RecordingManager = websocket.app.state.recording_manager

    try:
        while True:
            payload = _build_status_payload(config, manager)
            await websocket.send_json({"success": True, "data": payload})
            await asyncio.sleep(_WS_PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        logger.info("ws_status_disconnected", extra={"component": "status_ws", "operation": "disconnect"})
