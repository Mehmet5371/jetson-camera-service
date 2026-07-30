"""Canlı önizleme (MJPEG) yöneticisi - kayıt YOKKEN.

Kayıt SIRASINDA canlı görüntü RecordingManager'ın tee dalından gelir (bkz.
recording/manager.py); bu modül yalnızca idle durumdaki (kayıt yok)
önizlemeyi yönetir. İkisi de aynı MjpegBroadcaster altyapısını (camera/
mjpeg.py) ve AYNI FocusController'ı (main.py'de tek instance) paylaşır -
böylece önizlemede bulunan odak değeri kayıt başlarken korunur.

Argus tek-oturum kısıtlaması (Faz 1): aynı anda tek proses kameraya
erişebilir, bu yüzden önizleme yalnızca idle iken açılabilir.

Pipeline dosyaya YAZMAZ (yalnızca ayrılmış bir fd'ye MJPEG) - durdurma
SIGKILL ile güvenli (EOS gerekmez).
"""

from __future__ import annotations

import asyncio
import logging

from app.camera.detector import detect_camera_backend
from app.camera.focus import FocusController
from app.camera.mjpeg import MjpegBroadcaster, MjpegPipeReader
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.recording.manager import RecordingManager
from app.recording.pipeline import build_preview_pipeline_args
from app.recording.state import RecordingState

logger = logging.getLogger(__name__)

_FIRST_FRAME_TIMEOUT_SECONDS = 8.0
_FIRST_FRAME_POLL_INTERVAL_SECONDS = 0.05


class PreviewAlreadyActiveError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.PREVIEW_ALREADY_ACTIVE, "Önizleme zaten aktif.", status_code=409)


class PreviewNotActiveError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.PREVIEW_NOT_ACTIVE, "Aktif bir önizleme yok.", status_code=409)


class PreviewUnavailableWhileRecordingError(AppError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.PREVIEW_UNAVAILABLE_WHILE_RECORDING,
            "Kayıt sürerken ayrı önizleme başlatılamaz (kayıt zaten canlı görüntü sağlıyor).",
            status_code=409,
        )


class PreviewStartFailedError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.PIPELINE_START_FAILED, message, status_code=503, details=details or {}
        )


class PreviewManager:
    def __init__(
        self,
        config: AppConfig,
        recording_manager: RecordingManager,
        focus_controller: FocusController,
    ) -> None:
        self._config = config
        self._recording_manager = recording_manager
        self._focus_controller = focus_controller  # RecordingManager ile PAYLAŞILAN
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._broadcaster = MjpegBroadcaster()
        self._mjpeg_reader: MjpegPipeReader | None = None

    def update_config(self, new_config: AppConfig) -> None:
        self._config = new_config

    @property
    def is_active(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def broadcaster(self) -> MjpegBroadcaster:
        return self._broadcaster

    @property
    def focus_controller(self) -> FocusController:
        return self._focus_controller

    def get_latest_frame(self) -> bytes | None:
        return self._broadcaster.get_latest_frame()

    def stream(self):
        return self._broadcaster.stream()

    async def start(self) -> None:
        async with self._lock:
            if self.is_active:
                raise PreviewAlreadyActiveError()
            if self._recording_manager.session.state != RecordingState.IDLE:
                raise PreviewUnavailableWhileRecordingError()

            backend_info = detect_camera_backend(self._config.camera)
            self._broadcaster.reset()
            mjpeg_reader = MjpegPipeReader(self._broadcaster)
            write_fd = mjpeg_reader.create_pipe()
            args = build_preview_pipeline_args(self._config, backend_info, write_fd)

            logger.info(
                "preview_starting",
                extra={"component": "preview_manager", "operation": "start", "pipeline_args": args},
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    pass_fds=(write_fd,),
                )
            except OSError as exc:
                await mjpeg_reader.stop()
                raise PreviewStartFailedError(f"Önizleme pipeline'ı başlatılamadı: {exc}") from exc

            mjpeg_reader.close_write_end()
            await mjpeg_reader.start_reading()
            self._mjpeg_reader = mjpeg_reader
            self._process = process
            self._focus_controller.mark_stream_restarted()

            # İlk kare gelene (Argus akışı GERÇEKTEN başlayana) kadar bekle -
            # bu olmadan hemen ardından gelen odak yazma çağrısı I2C henüz
            # hazır değilken çarpabilir (gerçek donanımda gözlemlenen yarış).
            elapsed = 0.0
            while (
                self._broadcaster.get_latest_frame() is None
                and elapsed < _FIRST_FRAME_TIMEOUT_SECONDS
            ):
                if process.returncode is not None:
                    await self._force_stop_locked()
                    raise PreviewStartFailedError(
                        "Önizleme pipeline'ı ilk kareyi üretmeden sonlandı.",
                        details={"returncode": process.returncode},
                    )
                await asyncio.sleep(_FIRST_FRAME_POLL_INTERVAL_SECONDS)
                elapsed += _FIRST_FRAME_POLL_INTERVAL_SECONDS

            if self._broadcaster.get_latest_frame() is None:
                await self._force_stop_locked()
                raise PreviewStartFailedError(
                    f"Önizleme {_FIRST_FRAME_TIMEOUT_SECONDS}s içinde hiç kare üretmedi."
                )

            # Önizleme açılır açılmaz bilinen son odak değerini uygula (akış
            # yeniden başladığı için lens dinlenme konumuna dönmüş olabilir).
            self._apply_focus_best_effort()

            logger.info(
                "preview_started",
                extra={"component": "preview_manager", "operation": "start", "pid": process.pid},
            )

    def _apply_focus_best_effort(self) -> None:
        try:
            self._focus_controller.reapply(self._config.camera.manual_focus_value)
        except AppError as exc:
            logger.warning(
                "preview_focus_apply_failed",
                extra={
                    "component": "preview_manager",
                    "operation": "start",
                    "error_code": exc.code.value,
                },
            )

    async def stop(self) -> None:
        async with self._lock:
            if not self.is_active:
                raise PreviewNotActiveError()
            await self._force_stop_locked()
            logger.info("preview_stopped", extra={"component": "preview_manager", "operation": "stop"})

    async def _force_stop_locked(self) -> None:
        """`self._lock` TUTULUYORKEN çağrılmalı (start hatası + stop paylaşımı)."""
        process = self._process
        if process is not None:
            process.kill()
            await process.wait()
        self._broadcaster.close()
        if self._mjpeg_reader is not None:
            await self._mjpeg_reader.stop()
            self._mjpeg_reader = None
        self._process = None

    def set_manual_focus(self, value: int) -> None:
        if not self.is_active and not self._recording_manager.is_recording:
            raise AppError(
                ErrorCode.FOCUS_REQUIRES_ACTIVE_SESSION,
                "Odak ayarlamak için önce önizlemeyi başlatın (veya bir kayıt aktif olmalı).",
                status_code=409,
            )
        self._focus_controller.set_focus(value)
