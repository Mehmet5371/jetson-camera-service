"""Kamera durumu, test, canlı görüntü (önizleme + kayıt) ve odak uç noktaları.

Canlı görüntü iki kaynaktan gelebilir:
- Kayıt YOKKEN: PreviewManager'ın ayrı önizleme pipeline'ı.
- Kayıt SIRASINDA: RecordingManager'ın tee dalı (kayıtla aynı Argus akışı).
`/api/camera/live/stream` hangisi aktifse ondan yayınlar - dashboard tek bir
<img> ile hem önizlemeyi hem kayıt sırasındaki canlı görüntüyü gösterir.

Odak (manuel + otomatik) PAYLAŞILAN FocusController üzerinden çalışır ve
her iki durumda da (önizleme veya kayıt aktifken) kullanılabilir - VCM
motoru yalnızca Argus akışı açıkken I2C'ye yanıt verdiği için akış YOKKEN
odak ayarlanamaz.

Tüm odak işlemleri `AutofocusSupervisor` üzerinden gider (camera/
continuous_focus.py): sürekli odak döngüsü, manuel slider ve "Otomatik
Odakla" butonu aynı kilidi paylaşır, böylece iki odaklama işlemi asla I2C
üzerinde birbirine girmez.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.api.deps import (
    get_autofocus_supervisor,
    get_preview_manager,
    get_recording_manager,
    require_password_already_set,
)
from app.camera.autofocus import AutofocusFailedError
from app.camera.continuous_focus import AutofocusSupervisor
from app.camera.detector import detect_camera_backend
from app.camera.preview import PreviewManager
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.recording.manager import RecordingManager

router = APIRouter(prefix="/api/camera", tags=["camera"])


@router.get("/status")
async def get_camera_status(
    request: Request, _user: User = Depends(require_password_already_set)
) -> dict:
    config: AppConfig = request.app.state.config
    info = detect_camera_backend(config.camera)
    return {
        "success": True,
        "data": {
            "backend": info.backend,
            "source_element": info.source_element,
            "device": info.device,
            "sensor_modes": [
                {"fourcc": m.fourcc, "width": m.width, "height": m.height, "fps": m.fps}
                for m in info.sensor_modes
            ],
            "warnings": info.warnings,
        },
    }


@router.post("/test")
async def test_camera(
    _user: User = Depends(require_password_already_set),
    manager: RecordingManager = Depends(get_recording_manager),
) -> dict:
    result = await manager.test_camera()
    return {"success": True, "data": result}


@router.post("/preview/start")
async def start_preview(
    _user: User = Depends(require_password_already_set),
    preview: PreviewManager = Depends(get_preview_manager),
) -> dict:
    await preview.start()
    return {"success": True, "data": {"active": True}}


@router.post("/preview/stop")
async def stop_preview(
    _user: User = Depends(require_password_already_set),
    preview: PreviewManager = Depends(get_preview_manager),
) -> dict:
    await preview.stop()
    return {"success": True, "data": {"active": False}}


@router.get("/live/stream")
async def stream_live(
    _user: User = Depends(require_password_already_set),
    autofocus: AutofocusSupervisor = Depends(get_autofocus_supervisor),
) -> StreamingResponse:
    # KRİTİK: aktiflik kontrolü StreamingResponse OLUŞTURULMADAN ÖNCE
    # yapılmalı - aksi halde HTTP 200 başlığı generator'ın ilk yield'inden
    # önce gider ve hata artık status'u değiştiremez (gerçek testte bulunan
    # tuzak).
    source = autofocus.live_source()
    if source is None:
        raise AppError(
            ErrorCode.PREVIEW_NOT_ACTIVE,
            "Canlı görüntü yok - önizlemeyi başlatın veya kayıt alın.",
            status_code=409,
        )
    return StreamingResponse(
        source.broadcaster.stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


class FocusRequest(BaseModel):
    mode: Literal["manual", "auto"]
    value: int | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _validate_manual_has_value(self) -> "FocusRequest":
        if self.mode == "manual" and self.value is None:
            raise ValueError("mode='manual' için 'value' zorunludur (0-1000).")
        return self


@router.get("/focus")
async def get_focus(
    _user: User = Depends(require_password_already_set),
    autofocus: AutofocusSupervisor = Depends(get_autofocus_supervisor),
) -> dict:
    return {
        "success": True,
        "data": {
            "current_value": autofocus.focus_controller.current_value,
            "session_active": autofocus.live_source() is not None,
            "continuous": autofocus.status(),
        },
    }


@router.post("/focus")
async def set_focus(
    payload: FocusRequest,
    _user: User = Depends(require_password_already_set),
    autofocus: AutofocusSupervisor = Depends(get_autofocus_supervisor),
) -> dict:
    # Odak yazmaları (manuel, buton, sürekli odak döngüsü) tek bir kilidi
    # paylaşır - bu yüzden hepsi supervisor üzerinden gider.
    source = autofocus.live_source()
    if source is None:
        raise AppError(
            ErrorCode.FOCUS_REQUIRES_ACTIVE_SESSION,
            "Odak için önce önizlemeyi başlatın veya kayıt alın.",
            status_code=409,
        )

    if payload.mode == "manual":
        assert payload.value is not None
        await autofocus.apply_manual_focus(payload.value)
        return {
            "success": True,
            "data": {
                "mode": "manual",
                "value": payload.value,
                "continuous": autofocus.status(),
            },
        }

    # auto: aktif kaynağın (kayıt veya önizleme) en son karesini kullan.
    try:
        result = await autofocus.run_manual_sweep(source.broadcaster.get_latest_frame)
    except AutofocusFailedError as exc:
        raise AppError(ErrorCode.FOCUS_CONTROL_UNAVAILABLE, str(exc), status_code=503) from exc

    return {
        "success": True,
        "data": {
            "mode": "auto",
            "value": result.best_position,
            "sharpness": result.best_sharpness,
            "steps_taken": result.steps_taken,
            "continuous": autofocus.status(),
        },
    }


class ContinuousFocusRequest(BaseModel):
    enabled: bool


@router.post("/focus/continuous")
async def set_continuous_focus(
    payload: ContinuousFocusRequest,
    _user: User = Depends(require_password_already_set),
    autofocus: AutofocusSupervisor = Depends(get_autofocus_supervisor),
) -> dict:
    """Sürekli otomatik odağı çalışma zamanında aç/kapat.

    Bu yalnızca ÇALIŞAN sürece etki eder (config'e yazılmaz) - servis yeniden
    başladığında `camera.continuous_autofocus.enabled` ayarı geçerli olur.
    Kalıcı değişiklik için Ayarlar sekmesini (PUT /api/settings) kullanın.
    Canlı akış gerekmez: kapalı akışta da önceden açılıp/kapatılabilir.
    """
    autofocus.set_continuous_enabled(payload.enabled)
    return {"success": True, "data": autofocus.status()}
