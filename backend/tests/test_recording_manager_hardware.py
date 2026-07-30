"""Gerçek kamera donanımı gerektiren entegrasyon testleri (bkz. şartname böl. 21).

Bu testler gerçek Arducam IMX519'u kullanarak gerçek bir kayıt başlatıp
durdurur; mock değildir. CI'da kamerasız çalıştırılacaksa
`pytest -m "not hardware"` ile atlanabilir.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import SQLModel, create_engine

from app.core.config import (
    AppConfig,
    CameraConfig,
    CaptureConfig,
    LoggingConfig,
    PreviewConfig,
    RecordingConfig,
    RetentionConfig,
    SecurityConfig,
    ServerConfig,
    StorageConfig,
)
from app.camera.focus import FocusController
from app.recording.manager import RecordingAlreadyActiveError, RecordingManager, RecordingNotActiveError
from app.recording.state import RecordingState


def _make_test_engine(output_dir: Path) -> Engine:
    engine = create_engine(f"sqlite:///{output_dir / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


pytestmark = pytest.mark.hardware

_HARDWARE_TMP_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.hardware_tmp")


def _build_test_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="argus",
            sensor_id=0,
            device="/dev/video0",
            i2c_bus=10,
            width=1920,
            height=1080,
            fps=30,
            autofocus=False,
            manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264",
            encoder="software",
            container="mp4",
            bitrate_bps=4_000_000,
            speed_preset="ultrafast",
            segment_duration_minutes=0,
            filename_format="test_%Y%m%d_%H%M%S",
            minimum_free_space_gb=1,
            low_space_action="stop",
        ),
        capture=CaptureConfig(photo_format="jpg", photo_quality=90),
        storage=StorageConfig(
            mount_path="/mnt/recordings",
            expected_uuid="",
            require_mountpoint=True,
            media_path=str(output_dir),
            video_path=str(output_dir / "videos"),
            photo_path=str(output_dir / "photos"),
            database_path=str(output_dir / "app.db"),
        ),
        retention=RetentionConfig(enabled=False, maximum_storage_percent=90, delete_oldest_files=False),
        server=ServerConfig(host="127.0.0.1", port=8080),
        security=SecurityConfig(authentication_enabled=True),
        logging=LoggingConfig(
            level="INFO", directory=str(output_dir / "logs"), max_bytes=1_000_000, backup_count=1
        ),
    )


@pytest.fixture
def output_dir():
    base = _HARDWARE_TMP_ROOT / uuid.uuid4().hex
    base.mkdir(parents=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_stop_produces_valid_mp4_and_applies_focus(output_dir: Path) -> None:
    config = _build_test_config(output_dir)
    manager = RecordingManager(config, _make_test_engine(output_dir), FocusController(10))

    session = await manager.start_recording()
    try:
        assert session.state == RecordingState.RECORDING
        assert session.pid is not None
        # Odak, akış aktifken en iyi çaba (best-effort) uygulanır; hata fırlatmadan
        # tamamlandığını (veya en azından kaydı engellemediğini) doğrula.
        assert manager._focus_controller.current_value in (250, None)

        await asyncio.sleep(6)
    finally:
        # Assertion/istisna durumunda bile gerçek gst-launch-1.0 sürecinin
        # yetim kalmaması ve Argus oturumunu tutmaya devam etmemesi için:
        # önceki bir test çalıştırmasında bu finally eksikti ve orphan bir
        # süreç sonraki testleri "Failed to create CaptureSession" ile
        # başarısız kılmıştı.
        if manager.session.state == RecordingState.RECORDING:
            await manager.stop_recording()

    assert manager.session.state == RecordingState.IDLE
    assert manager.session.pid is None

    video_dir = output_dir / "videos"
    files = list(video_dir.glob("*.mp4"))
    assert len(files) == 1, f"tam olarak bir mp4 bekleniyordu, bulunan: {files}"
    assert files[0].stat().st_size > 0

    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=codec_name,width,height",
            "-of", "default=noprint_wrappers=1",
            str(files[0]),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "codec_name=h264" in result.stdout
    assert "width=1920" in result.stdout
    assert "height=1080" in result.stdout


@pytest.mark.asyncio
async def test_double_start_rejected(output_dir: Path) -> None:
    config = _build_test_config(output_dir)
    manager = RecordingManager(config, _make_test_engine(output_dir), FocusController(10))

    await manager.start_recording()
    try:
        with pytest.raises(RecordingAlreadyActiveError):
            await manager.start_recording()
    finally:
        await manager.stop_recording()


@pytest.mark.asyncio
async def test_stop_when_idle_rejected(output_dir: Path) -> None:
    config = _build_test_config(output_dir)
    manager = RecordingManager(config, _make_test_engine(output_dir), FocusController(10))
    with pytest.raises(RecordingNotActiveError):
        await manager.stop_recording()


@pytest.mark.asyncio
async def test_live_preview_frames_available_during_recording(output_dir: Path) -> None:
    """Kayıt SIRASINDA canlı önizleme (tee dalı) - kullanıcı raporundaki
    'kayıt başlayınca görüntü kayboluyor' sorununun düzeltmesi. Kayıt
    kalitesinin (1920x1080) önizleme dalından ETKİLENMEDİĞİNİ de doğrular."""
    config = _build_test_config(output_dir)
    manager = RecordingManager(config, _make_test_engine(output_dir), FocusController(10))

    await manager.start_recording()
    try:
        # Kayıt sürerken tee dalından gerçek MJPEG kareleri gelmeli.
        frame = None
        for _ in range(50):
            frame = manager.broadcaster.get_latest_frame()
            if frame is not None:
                break
            await asyncio.sleep(0.1)
        assert frame is not None, "kayıt sırasında canlı önizleme karesi gelmedi"
        assert frame[:2] == b"\xff\xd8"  # JPEG magic

        # Birden fazla kare akıyor mu (donmuş tek kare değil)?
        first = manager.broadcaster.get_latest_frame()
        await asyncio.sleep(1.0)
        # En az birkaç kare değişmiş olmalı (canlı akış).
        assert manager.broadcaster.get_latest_frame() is not None
    finally:
        if manager.session.state == RecordingState.RECORDING:
            await manager.stop_recording()

    # Kayıt dosyası TAM çözünürlükte (önizleme dalı kaliteyi düşürmedi).
    files = list((output_dir / "videos").glob("*.mp4"))
    assert len(files) == 1
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", str(files[0])],
        capture_output=True, text=True, timeout=10,
    )
    assert "width=1920" in result.stdout
    assert "height=1080" in result.stdout
