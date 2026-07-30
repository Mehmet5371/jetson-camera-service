"""Ayarlar okuma/güncelleme uç noktası.

Şartname böl. 7: çözünürlük, FPS, bitrate, segment süresi, minimum boş
disk alanı, otomatik eski kayıt silme, dosya adı formatı dashboard'dan
değiştirilebilmeli. Aktif kayıt sırasında kamera/kayıt ayarları
KİLİTLENİR (retention ayarları her zaman değiştirilebilir).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError

from app.api.deps import (
    get_autofocus_supervisor,
    get_preview_manager,
    get_recording_manager,
    require_password_already_set,
)
from app.camera.continuous_focus import AutofocusSupervisor
from app.camera.preview import PreviewManager
from app.core.config import AppConfig, get_config, save_config
from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.recording.manager import RecordingManager
from app.recording.state import RecordingState

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ContinuousAutofocusSettingsUpdate(BaseModel):
    enabled: bool | None = None
    during_recording: bool | None = None
    sample_interval_seconds: float | None = None
    trigger_ratio: float | None = None
    reference_smoothing: float | None = None
    consecutive_drops: int | None = None
    cooldown_seconds: float | None = None
    fine_step: int | None = None
    fine_range_steps: int | None = None
    verify_settle_seconds: float | None = None
    initial_sweep: bool | None = None
    manual_override_seconds: float | None = None


class CameraSettingsUpdate(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    autofocus: bool | None = None
    manual_focus_value: int | None = None
    continuous_autofocus: ContinuousAutofocusSettingsUpdate | None = None


class RecordingSettingsUpdate(BaseModel):
    bitrate_bps: int | None = None
    speed_preset: str | None = None
    segment_duration_minutes: int | None = None
    filename_format: str | None = None
    minimum_free_space_gb: float | None = None
    low_space_action: str | None = None


class RetentionSettingsUpdate(BaseModel):
    enabled: bool | None = None
    maximum_storage_percent: float | None = None
    delete_oldest_files: bool | None = None


class PreviewSettingsUpdate(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    jpeg_quality: int | None = None


class SettingsUpdateRequest(BaseModel):
    camera: CameraSettingsUpdate | None = None
    preview: PreviewSettingsUpdate | None = None
    recording: RecordingSettingsUpdate | None = None
    retention: RetentionSettingsUpdate | None = None


@router.get("")
async def get_settings(_user: User = Depends(require_password_already_set)) -> dict:
    return {"success": True, "data": get_config().model_dump(mode="json")}


@router.put("")
async def update_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
    preview: PreviewManager = Depends(get_preview_manager),
    autofocus: AutofocusSupervisor = Depends(get_autofocus_supervisor),
) -> dict:
    locked = manager.session.state != RecordingState.IDLE
    if locked and (payload.camera is not None or payload.recording is not None):
        raise AppError(
            ErrorCode.SETTINGS_LOCKED,
            "Aktif bir kayıt sürerken kamera/kayıt ayarları değiştirilemez. Önce kaydı durdurun.",
            status_code=409,
        )

    current_dict = manager.config.model_dump(mode="json")

    if payload.camera is not None:
        updates = {k: v for k, v in payload.camera.model_dump().items() if v is not None}
        # continuous_autofocus İÇ İÇE bir bloktur: sözlüğü olduğu gibi yazmak
        # gönderilmeyen alanları None'a çevirip doğrulamayı bozar - alan alan
        # birleştirilmeli (kısmi güncelleme sözleşmesi iç blokta da geçerli).
        nested = updates.pop("continuous_autofocus", None)
        current_dict["camera"].update(updates)
        if nested is not None:
            current_dict["camera"]["continuous_autofocus"].update(
                {k: v for k, v in nested.items() if v is not None}
            )
    if payload.preview is not None:
        updates = {k: v for k, v in payload.preview.model_dump().items() if v is not None}
        current_dict["preview"].update(updates)
    if payload.recording is not None:
        updates = {k: v for k, v in payload.recording.model_dump().items() if v is not None}
        current_dict["recording"].update(updates)
    if payload.retention is not None:
        updates = {k: v for k, v in payload.retention.model_dump().items() if v is not None}
        current_dict["retention"].update(updates)

    try:
        new_config = AppConfig.model_validate(current_dict)
    except ValidationError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Ayarlar doğrulanamadı.",
            status_code=422,
            details={"errors": exc.errors()},
        ) from exc

    save_config(new_config)
    get_config.cache_clear()
    request.app.state.config = new_config
    manager.update_config(new_config)
    preview.update_config(new_config)
    autofocus.update_config(new_config)

    return {"success": True, "data": new_config.model_dump(mode="json")}
