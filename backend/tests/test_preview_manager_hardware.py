"""PreviewManager + gerçek odak kontrolü testleri - GERÇEK KAMERA GEREKİR.

Faz "canlı önizleme + odak" geliştirmesinde gerçek donanımda manuel olarak
doğrulanan davranışın (bkz. PROJECT_STATE.md) otomatik test karşılığı:
önizleme aktifken I2C üzerinden manuel odak yazma başarılı oluyor (akış
kapalıyken başarısız olduğu Faz 3'te zaten ayrıca doğrulanmıştı).
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

from app.camera.focus import FocusController
from app.camera.preview import PreviewManager
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
from app.recording.manager import RecordingManager

pytestmark = pytest.mark.hardware

_TEST_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.hardware_tmp")


def _build_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="argus", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=1920, height=1080, fps=30, autofocus=False, manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=4_000_000,
            speed_preset="ultrafast", segment_duration_minutes=0,
            filename_format="previewhw_%Y%m%d_%H%M%S", minimum_free_space_gb=1,
            low_space_action="stop",
        ),
        capture=CaptureConfig(photo_format="jpg", photo_quality=90),
        storage=StorageConfig(
            mount_path="/mnt/recordings", expected_uuid="", require_mountpoint=True,
            media_path=str(output_dir), video_path=str(output_dir / "videos"),
            photo_path=str(output_dir / "photos"), database_path=str(output_dir / "app.db"),
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
    base = _TEST_ROOT / uuid.uuid4().hex
    base.mkdir(parents=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _make_engine(output_dir: Path):
    engine = create_engine(f"sqlite:///{output_dir / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.mark.asyncio
async def test_preview_produces_real_camera_frames(output_dir: Path) -> None:
    config = _build_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    await preview.start()
    try:
        for _ in range(50):
            if preview.get_latest_frame() is not None:
                break
            await asyncio.sleep(0.1)
        frame = preview.get_latest_frame()
        assert frame is not None
        assert frame[:2] == b"\xff\xd8"
        assert len(frame) > 1000  # gerçek bir kamera karesi, boş/siyah değil
    finally:
        await preview.stop()


@pytest.mark.asyncio
async def test_manual_focus_succeeds_while_preview_active(output_dir: Path) -> None:
    # Faz 3'te doğrulanan bulgu: VCM motoru yalnızca Argus akışı aktifken
    # I2C'ye yanıt veriyor. Bu test önizlemenin gerçekten o akışı sağladığını
    # ve odak yazmanın BAŞARILI olduğunu (exception fırlatmadığını) kanıtlar.
    config = _build_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    await preview.start()
    try:
        preview.set_manual_focus(400)  # exception fırlatmamalı
        assert preview.focus_controller.current_value == 400
    finally:
        await preview.stop()
