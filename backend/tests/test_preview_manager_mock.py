"""PreviewManager yaşam döngüsü + RecordingManager ile karşılıklı dışlama
testleri - mock backend (`videotestsrc`) kullanır, GERÇEK KAMERA GEREKMEZ.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

from app.camera.focus import FocusController
from app.camera.preview import (
    PreviewAlreadyActiveError,
    PreviewManager,
    PreviewNotActiveError,
    PreviewUnavailableWhileRecordingError,
)
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
from app.recording.state import RecordingState

_TEST_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.mock_tmp")


def _build_mock_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="mock", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=640, height=480, fps=30, autofocus=True, manual_focus_value=250,
        ),
        preview=PreviewConfig(width=320, height=240, fps=10, jpeg_quality=70),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=1_000_000,
            speed_preset="ultrafast", segment_duration_minutes=0,
            filename_format="previewtest_%Y%m%d_%H%M%S", minimum_free_space_gb=1,
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
async def test_preview_start_stop_produces_real_frames(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    assert not preview.is_active
    await preview.start()
    assert preview.is_active

    # videotestsrc'ten gerçek JPEG kareleri gelmeli (sahte/boş değil).
    for _ in range(50):
        if preview.get_latest_frame() is not None:
            break
        await asyncio.sleep(0.1)
    frame = preview.get_latest_frame()
    assert frame is not None
    assert frame[:2] == b"\xff\xd8"  # JPEG magic bytes

    await preview.stop()
    assert not preview.is_active
    assert preview.get_latest_frame() is None


@pytest.mark.asyncio
async def test_preview_double_start_rejected(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    await preview.start()
    try:
        with pytest.raises(PreviewAlreadyActiveError):
            await preview.start()
    finally:
        await preview.stop()


@pytest.mark.asyncio
async def test_preview_stop_when_inactive_rejected(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    with pytest.raises(PreviewNotActiveError):
        await preview.stop()


@pytest.mark.asyncio
async def test_preview_rejected_while_recording_active(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    await recording_manager.start_recording()
    try:
        assert recording_manager.session.state == RecordingState.RECORDING
        with pytest.raises(PreviewUnavailableWhileRecordingError):
            await preview.start()
    finally:
        await recording_manager.stop_recording()


@pytest.mark.asyncio
async def test_multiple_subscribers_both_receive_frames(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, _make_engine(output_dir), focus_controller)
    preview = PreviewManager(config, recording_manager, focus_controller)

    await preview.start()
    try:
        stream_a = preview.stream()
        stream_b = preview.stream()

        chunk_a = await asyncio.wait_for(stream_a.__anext__(), timeout=5)
        chunk_b = await asyncio.wait_for(stream_b.__anext__(), timeout=5)

        assert chunk_a.startswith(b"--frame\r\n")
        assert chunk_b.startswith(b"--frame\r\n")

        await stream_a.aclose()
        await stream_b.aclose()
    finally:
        await preview.stop()
