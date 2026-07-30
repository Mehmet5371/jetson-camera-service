"""Kayıt yaşam döngüsü yöneticisi.

Sorumluluklar: pipeline başlatma/durdurma, state machine, tek eşzamanlı
kayıt kilidi, PID takibi, güvenli (EOS tabanlı) sonlandırma.

Güvenli durdurma neden SIGINT: Faz 3'te gerçek donanımda doğrulandı -
`gst-launch-1.0 -e` SIGINT aldığında pipeline'a EOS event'i enjekte edip
splitmuxsink/mp4mux'un dosyayı düzgün finalize etmesini bekliyor, sonra
temiz çıkıyor. SIGKILL/SIGTERM ile öldürmek MP4'ü bozar (moov atom
yazılmadan kesilir) - bu yüzden yalnızca zaman aşımında son çare olarak
kullanılır.

Tek worker varsayımı: Bu yönetici sınıfı process içi asyncio.Lock ile
serialize eder. Bu YALNIZCA uvicorn tek worker ile çalıştığı sürece
yeterlidir (bkz. systemd birimi, Faz 7) - birden fazla worker asla
kullanılmamalıdır, çünkü GStreamer subprocess'i tek bir Python
process'ine bağlıdır.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from app.camera.detector import CameraBackendInfo, detect_camera_backend
from app.camera.focus import FocusController
from app.camera.mjpeg import MjpegBroadcaster, MjpegPipeReader
from app.core.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.recording.pipeline import build_pipeline_args
from app.recording.state import RecordingState, validate_transition
from app.storage.reconciler import extract_video_metadata, probe_video_file, reconcile_recordings
from app.storage.validator import check_minimum_free_space, validate_storage

logger = logging.getLogger(__name__)

_GRACEFUL_STOP_TIMEOUT_SECONDS = 20.0
_TERMINATE_TIMEOUT_SECONDS = 5.0
_ARGUS_HANDSHAKE_DELAY_SECONDS = 2.0
# Kayıt başlarken canlı önizleme dalından ilk MJPEG karesinin gelmesini
# (Argus akışı gerçekten başladı = odak I2C'si artık yanıt verir) bekleme
# üst sınırı. Bu gelmeden odak yazma çağrısı I2C henüz hazır değilken
# başarısız olabilir (Faz 3'ün "VCM yalnızca akış aktifken yanıt verir"
# bulgusu - önizleme geliştirmesinde de aynı yarış gözlemlendi).
_FIRST_FRAME_TIMEOUT_SECONDS = 8.0
_FIRST_FRAME_POLL_INTERVAL_SECONDS = 0.05


@dataclass
class RecordingSession:
    state: RecordingState
    base_filename: str
    started_at: datetime | None = None
    pid: int | None = None
    output_dir: Path | None = None


class RecordingAlreadyActiveError(AppError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.RECORDING_ALREADY_ACTIVE,
            "Zaten aktif bir kayıt var; yeni kayıt başlatılamaz.",
            status_code=409,
        )


class RecordingNotActiveError(AppError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.RECORDING_NOT_ACTIVE,
            "Aktif bir kayıt yok; durdurulacak bir şey bulunamadı.",
            status_code=409,
        )


class PipelineStartFailedError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.PIPELINE_START_FAILED, message, status_code=503, details=details or {}
        )


def _lock_file_path(config: AppConfig) -> Path:
    return Path(config.storage.mount_path) / "run" / "recording.lock"


class RecordingManager:
    def __init__(
        self, config: AppConfig, db_engine: Engine, focus_controller: FocusController
    ) -> None:
        self._config = config
        self._db_engine = db_engine
        self._lock = asyncio.Lock()
        self._session = RecordingSession(state=RecordingState.IDLE, base_filename="")
        self._process: asyncio.subprocess.Process | None = None
        # PreviewManager ile PAYLAŞILAN tek FocusController - önizlemede
        # bulunan odak değeri (current_value) kayıt başlarken korunup
        # yeniden uygulanabilsin diye (bkz. main.py, _apply_focus).
        self._focus_controller = focus_controller
        # Kayıt sırasında canlı önizleme (tee dalı MJPEG).
        self._broadcaster = MjpegBroadcaster()
        self._mjpeg_reader: MjpegPipeReader | None = None

    @property
    def session(self) -> RecordingSession:
        return self._session

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def broadcaster(self) -> MjpegBroadcaster:
        return self._broadcaster

    @property
    def focus_controller(self) -> FocusController:
        return self._focus_controller

    @property
    def is_recording(self) -> bool:
        return self._session.state == RecordingState.RECORDING

    def update_config(self, new_config: AppConfig) -> None:
        """Ayarlar dashboard'dan değiştirildiğinde (bkz. api/settings.py)
        çağrılır. Aktif kayıt sırasında kamera/kayıt alanlarının
        değişmediği zaten API katmanında garanti edilir (SETTINGS_LOCKED);
        burada yalnızca yeni config nesnesi takılır. FocusController
        paylaşıldığı için burada yeniden yaratılmaz (i2c_bus değişirse
        main.py yeniden kurar).
        """
        self._config = new_config

    def check_process_alive(self) -> bool:
        """Kayıt sırasında GStreamer subprocess'inin hâlâ ayakta olup olmadığını
        kontrol eder (ilkel/anlık kontrol - state'i değiştirmez)."""
        if self._session.state != RecordingState.RECORDING or self._process is None:
            return True
        return self._process.returncode is None

    async def check_and_recover_process_health(self) -> None:
        """Periyodik bakım görevi (bkz. main.py) tarafından çağrılır.

        Kayıt sırasında GStreamer subprocess'i beklenmedik şekilde öldüyse
        (örn. kamera kablosu koptu, sürücü çöktü): durumu error'a çekip
        diskte kalan (muhtemelen yarım/bozuk) dosyayı reconciler ile
        işler, sonra otomatik olarak idle'a döner - servis kilitli kalmaz,
        kamera tekrar kullanılabilir olduğunda yeni kayıt başlatılabilir
        (bkz. şartname böl. 15/17).
        """
        async with self._lock:
            if self._session.state != RecordingState.RECORDING or self._process is None:
                return
            if self._process.returncode is None:
                return  # hâlâ canlı, yapılacak bir şey yok

            pid = self._process.pid
            output_dir = self._session.output_dir
            logger.error(
                "recording_process_died_unexpectedly",
                extra={
                    "component": "recording_manager",
                    "operation": "health_check",
                    "pid": pid,
                    "returncode": self._process.returncode,
                },
            )

            await self._stop_mjpeg_reader()
            self._process = None
            self._set_state(RecordingState.ERROR)
            self._remove_lock_file()

            if output_dir is not None:
                self._reconcile_after_stop(output_dir)

            # Otomatik kurtarma: error durumunda takılı kalmak yerine idle'a
            # dön ki yeni bir kayıt denemesi hemen yapılabilsin.
            self._set_state(RecordingState.IDLE)
            self._session = RecordingSession(state=RecordingState.IDLE, base_filename="")

    async def start_recording(self) -> RecordingSession:
        async with self._lock:
            if self._session.state != RecordingState.IDLE:
                raise RecordingAlreadyActiveError()

            self._set_state(RecordingState.STARTING)

            try:
                # SSD mount değilse veya doluysa pipeline'ı HİÇ başlatma - bu
                # sırayla yapılıyor ki kamera kilitlenmeden önce ucuz/hızlı
                # kontroller tükensin (bkz. şartname böl. 8).
                usage = validate_storage(self._config.storage)
                check_minimum_free_space(usage, self._config.recording.minimum_free_space_gb)
                backend_info = detect_camera_backend(self._config.camera)
                session = await self._launch_pipeline(backend_info)
            except AppError:
                self._set_state(RecordingState.IDLE)
                raise
            except Exception as exc:  # noqa: BLE001 - beklenmeyen hata da güvenli şekilde IDLE'a döner
                self._set_state(RecordingState.IDLE)
                raise PipelineStartFailedError(
                    f"Kayıt başlatılırken beklenmeyen hata: {exc}"
                ) from exc

            # session zaten state=RECORDING olarak oluşturuldu (_launch_pipeline);
            # burada yalnızca STARTING->RECORDING geçişinin geçerliliğini
            # self._session (hâlâ STARTING) üzerinden doğrulayıp sonra yeni
            # session nesnesini takıyoruz - _set_state(RECORDING) çağırıp
            # ardından objeyi değiştirmek recording->recording'e (kendine
            # geçiş) çarpar, bu yüzden sıralama önemli.
            validate_transition(self._session.state, RecordingState.RECORDING)
            self._session = session
            self._write_lock_file()
            self._apply_focus_best_effort()

            logger.info(
                "recording_started",
                extra={
                    "component": "recording_manager",
                    "operation": "start",
                    "pid": session.pid,
                    "output_dir": str(session.output_dir),
                },
            )
            return self._session

    async def _launch_pipeline(self, backend_info: CameraBackendInfo) -> RecordingSession:
        timestamp = datetime.now().strftime(self._config.recording.filename_format)
        output_dir = Path(self._config.storage.video_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Canlı önizleme (tee dalı) için ayrılmış bir MJPEG pipe'ı kur.
        # videotestsrc (mock) fdsink'i destekler ama mock testte de aynı
        # kod yolu çalışır - MJPEG dalı her backend için eklenir.
        self._broadcaster.reset()
        mjpeg_reader = MjpegPipeReader(self._broadcaster)
        write_fd = mjpeg_reader.create_pipe()

        args = build_pipeline_args(
            self._config, backend_info, output_dir, timestamp, mjpeg_fd=write_fd
        )
        logger.info(
            "recording_pipeline_launch",
            extra={"component": "recording_manager", "operation": "start", "pipeline_args": args},
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                pass_fds=(write_fd,),
            )
        except OSError as exc:
            await mjpeg_reader.stop()  # pipe fd'lerini temizle
            raise PipelineStartFailedError(
                f"GStreamer pipeline başlatılamadı: {exc}", details={"args": args}
            ) from exc

        mjpeg_reader.close_write_end()
        await mjpeg_reader.start_reading()
        self._mjpeg_reader = mjpeg_reader
        self._process = process
        self._focus_controller.mark_stream_restarted()

        # İlk MJPEG karesini bekle = Argus akışı GERÇEKTEN başladı. Bu
        # gelmeden odak yazma çağrılırsa I2C hazır olmayabilir. Sabit
        # bekleme yerine gerçek sinyal beklemek daha sağlam.
        elapsed = 0.0
        while (
            self._broadcaster.get_latest_frame() is None
            and elapsed < _FIRST_FRAME_TIMEOUT_SECONDS
        ):
            if process.returncode is not None:
                await mjpeg_reader.stop()
                self._mjpeg_reader = None
                self._process = None
                raise PipelineStartFailedError(
                    "GStreamer pipeline ilk kareyi üretmeden sonlandı.",
                    details={"returncode": process.returncode},
                )
            await asyncio.sleep(_FIRST_FRAME_POLL_INTERVAL_SECONDS)
            elapsed += _FIRST_FRAME_POLL_INTERVAL_SECONDS

        return RecordingSession(
            state=RecordingState.RECORDING,
            base_filename=timestamp,
            started_at=datetime.now(timezone.utc),
            pid=process.pid,
            output_dir=output_dir,
        )

    def _apply_focus_best_effort(self) -> None:
        # VCM odak motorunun I2C'si yalnızca Argus akışı aktifken yanıt
        # veriyor (Faz 3). Akış yeniden başladığında lens fiziksel olarak
        # dinlenme konumuna (bulanık) döndüğü için, önizlemede bulunan (veya
        # config'deki varsayılan) odak değerini burada YENİDEN uyguluyoruz -
        # aksi halde kayıt bulanık başlıyordu (kullanıcı raporu).
        # mock backend'de i2cset başarısız olur, bu beklenen - kayıt iptal
        # edilmez, yalnızca loglanır.
        try:
            self._focus_controller.reapply(self._config.camera.manual_focus_value)
            logger.info(
                "recording_focus_applied",
                extra={
                    "component": "recording_manager",
                    "operation": "start",
                    "focus_value": self._focus_controller.current_value,
                },
            )
        except AppError as exc:
            logger.warning(
                "focus_control_failed",
                extra={
                    "component": "recording_manager",
                    "operation": "start",
                    "error_code": exc.code.value,
                    "error_message": exc.message,
                },
            )

    async def stop_recording(self) -> RecordingSession:
        async with self._lock:
            if self._session.state != RecordingState.RECORDING:
                raise RecordingNotActiveError()

            process = self._process
            assert process is not None
            pid = process.pid

            self._set_state(RecordingState.STOPPING)
            logger.info(
                "recording_stopping",
                extra={"component": "recording_manager", "operation": "stop", "pid": pid},
            )
            process.send_signal(signal.SIGINT)
            self._set_state(RecordingState.FINALIZING)

            corrupted = False
            try:
                await asyncio.wait_for(process.wait(), timeout=_GRACEFUL_STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                corrupted = True
                logger.error(
                    "recording_force_kill",
                    extra={
                        "component": "recording_manager",
                        "operation": "stop",
                        "pid": pid,
                        "reason": (
                            f"SIGINT sonrası {_GRACEFUL_STOP_TIMEOUT_SECONDS}s içinde "
                            "kapanmadı, SIGTERM/SIGKILL'e escalate ediliyor"
                        ),
                    },
                )
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=_TERMINATE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            await self._stop_mjpeg_reader()
            finished_session = replace(self._session, state=RecordingState.IDLE)
            self._process = None
            self._session = RecordingSession(state=RecordingState.IDLE, base_filename="")
            self._remove_lock_file()

            # Gerçek dosyayı ffprobe ile doğrulayıp veritabanına işle. Bilerek
            # kendi "corrupted" bayrağımıza güvenmiyoruz - dosyayı gerçekten
            # okuyup COMPLETED/CORRUPTED kararını buradan alıyoruz (bkz.
            # reconciler modülü, Faz 4).
            if finished_session.output_dir is not None:
                self._reconcile_after_stop(finished_session.output_dir)

            logger.info(
                "recording_stopped",
                extra={
                    "component": "recording_manager",
                    "operation": "stop",
                    "pid": pid,
                    "corrupted": corrupted,
                },
            )
            return finished_session

    async def _stop_mjpeg_reader(self) -> None:
        if self._mjpeg_reader is not None:
            self._broadcaster.close()
            await self._mjpeg_reader.stop()
            self._mjpeg_reader = None

    def _reconcile_after_stop(self, video_dir: Path) -> None:
        try:
            with Session(self._db_engine) as db_session:
                reconcile_recordings(db_session, video_dir)
        except Exception:  # noqa: BLE001 - reconciliation hatası kaydı durdurmayı engellemez
            logger.exception(
                "post_stop_reconcile_failed",
                extra={"component": "recording_manager", "operation": "stop"},
            )

    async def test_camera(self) -> dict:
        """Kısa (2 saniyelik) gerçek bir test kaydı alıp ffprobe ile doğrular.

        Aktif bir kayıt varsa reddedilir (kamera Argus'ta tek proses
        kısıtlaması nedeniyle paylaşılamaz - bkz. Faz 1). Test dosyası
        doğrulandıktan sonra silinir, veritabanına işlenmez.
        """
        async with self._lock:
            if self._session.state != RecordingState.IDLE:
                raise RecordingAlreadyActiveError()

            backend_info = detect_camera_backend(self._config.camera)

            test_dir = Path(self._config.storage.mount_path) / "run" / "camera_test"
            test_dir.mkdir(parents=True, exist_ok=True)
            test_filename = f"test_{int(datetime.now(timezone.utc).timestamp())}"
            # Test kaydı kullanıcının segment_duration_minutes ayarından
            # BAĞIMSIZ olmalı - her zaman tek dosya (segment eki YOK).
            # Aksi halde varsayılan config'de (segment_duration_minutes=30)
            # build_pipeline_args dosyayı "{test_filename}_000.mp4" olarak
            # üretir ama burada eksiz "{test_filename}.mp4" aranırsa dosya
            # hiç bulunamaz (gerçek testte tam olarak bu şekilde bulunan bug).
            test_recording_config = self._config.recording.model_copy(
                update={"segment_duration_minutes": 0}
            )
            test_config = self._config.model_copy(update={"recording": test_recording_config})
            args = build_pipeline_args(test_config, backend_info, test_dir, test_filename)

            try:
                process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )
            except OSError as exc:
                raise PipelineStartFailedError(f"Test pipeline başlatılamadı: {exc}") from exc

            await asyncio.sleep(_ARGUS_HANDSHAKE_DELAY_SECONDS)
            if process.returncode is not None:
                output = await process.stdout.read() if process.stdout else b""
                raise PipelineStartFailedError(
                    "Kamera testi başlamadan önce sonlandı.",
                    details={
                        "returncode": process.returncode,
                        "output": output.decode(errors="replace")[-2000:],
                    },
                )

            await asyncio.sleep(2.0)
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=_GRACEFUL_STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

            test_file = test_dir / f"{test_filename}.{self._config.recording.container}"
            probe_data = probe_video_file(test_file) if test_file.exists() else None
            test_file.unlink(missing_ok=True)

            if probe_data is None:
                raise PipelineStartFailedError(
                    "Kamera testi bir video üretti ama dosya doğrulanamadı (ffprobe başarısız)."
                )

            metadata = extract_video_metadata(probe_data)
            return {
                "backend": backend_info.backend,
                "source_element": backend_info.source_element,
                "sensor_modes": [
                    {"fourcc": m.fourcc, "width": m.width, "height": m.height, "fps": m.fps}
                    for m in backend_info.sensor_modes
                ],
                "test_capture": metadata,
            }

    async def shutdown(self) -> None:
        """Servis kapanırken (SIGTERM/lifespan) aktif kaydı güvenle sonlandırır."""
        if self._session.state == RecordingState.RECORDING:
            logger.info(
                "shutdown_finalizing_active_recording",
                extra={"component": "recording_manager", "operation": "shutdown"},
            )
            await self.stop_recording()

    def recover_from_previous_run(self) -> None:
        """Servis başlarken önceki bir çalıştırmadan kalan kilit dosyasını
        kontrol eder. Şartname böl. 15: eski/geçersiz PID dosyaları YANLIŞ
        bir prosesi sonlandırmamalı - bu yüzden burada asla kill YAPILMAZ;
        yalnızca (a) PID hâlâ gerçekten bizim gst-launch-1.0 sürecimizse
        uyarı loglanır (operatör kararına bırakılır), (b) değilse kilit
        dosyası temizlenir.
        """
        lock_path = _lock_file_path(self._config)
        if not lock_path.is_file():
            return

        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            logger.warning(
                "stale_lock_file_unreadable",
                extra={"component": "recording_manager", "operation": "recover", "path": str(lock_path)},
            )
            lock_path.unlink(missing_ok=True)
            return

        cmdline_path = Path(f"/proc/{pid}/cmdline")
        is_our_process = False
        if cmdline_path.is_file():
            try:
                cmdline = cmdline_path.read_bytes().decode(errors="replace")
            except OSError:
                cmdline = ""
            is_our_process = "gst-launch-1.0" in cmdline

        if is_our_process:
            logger.warning(
                "orphaned_recording_process_detected",
                extra={
                    "component": "recording_manager",
                    "operation": "recover",
                    "pid": pid,
                    "note": (
                        "Önceki servis çalıştırmasından kalan bir gst-launch-1.0 "
                        "süreci hâlâ çalışıyor. Otomatik olarak SONLANDIRILMADI "
                        "(aktif bir kaydı bozma riski). Manuel müdahale gerekebilir."
                    ),
                },
            )
        else:
            logger.info(
                "stale_lock_file_cleared",
                extra={"component": "recording_manager", "operation": "recover", "pid": pid},
            )
            lock_path.unlink(missing_ok=True)

    def _write_lock_file(self) -> None:
        lock_path = _lock_file_path(self._config)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": self._session.pid,
            "started_at": self._session.started_at.isoformat() if self._session.started_at else None,
            "base_filename": self._session.base_filename,
        }
        lock_path.write_text(json.dumps(payload), encoding="utf-8")

    def _remove_lock_file(self) -> None:
        _lock_file_path(self._config).unlink(missing_ok=True)

    def _set_state(self, target: RecordingState) -> None:
        validate_transition(self._session.state, target)
        self._session.state = target
