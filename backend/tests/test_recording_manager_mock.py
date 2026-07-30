"""Mock kamera backend testleri - GERÇEK KAMERA DONANIMI GEREKTİRMEZ.

Şartname böl. 21: "Kamera olmadan test çalıştırılabilmesi için mock camera
backend oluştur." Bu, sahte/stub bir sonuç döndürmez - GStreamer'ın gerçek
`videotestsrc` elementiyle gerçek bir subprocess başlatıp gerçek bir MP4
üretir ve `ffprobe` ile doğrular; yalnızca görüntü KAYNAĞI sentetiktir.
RecordingManager'ın state machine'i, kilit mekanizması, DB reconciliation'ı
dahil TÜM gerçek kod yolu çalışır - donanım testleriyle (test_recording_
manager_hardware.py) birebir aynı iddiaları test eder.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
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

_TEST_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.mock_tmp")


def _build_mock_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="mock",
            sensor_id=0,
            device="/dev/video0",
            i2c_bus=10,
            width=640,
            height=480,
            fps=30,
            autofocus=True,  # I2C'ye hiç dokunmasın diye - mock testte donanım yok
            manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=1_000_000,
            speed_preset="ultrafast", segment_duration_minutes=0,
            filename_format="mocktest_%Y%m%d_%H%M%S", minimum_free_space_gb=1,
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
    base = _TEST_ROOT / uuid.uuid4().hex
    base.mkdir(parents=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _make_engine(output_dir: Path):
    engine = create_engine(f"sqlite:///{output_dir / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.mark.asyncio
async def test_mock_backend_produces_valid_mp4_without_hardware(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    manager = RecordingManager(config, _make_engine(output_dir), FocusController(10))

    session = await manager.start_recording()
    try:
        assert session.state == RecordingState.RECORDING
        assert session.pid is not None
        await asyncio.sleep(3)
    finally:
        if manager.session.state == RecordingState.RECORDING:
            await manager.stop_recording()

    assert manager.session.state == RecordingState.IDLE

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
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "codec_name=h264" in result.stdout
    assert "width=640" in result.stdout
    assert "height=480" in result.stdout


@pytest.mark.asyncio
async def test_mock_backend_double_start_rejected(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    manager = RecordingManager(config, _make_engine(output_dir), FocusController(10))

    await manager.start_recording()
    try:
        with pytest.raises(RecordingAlreadyActiveError):
            await manager.start_recording()
    finally:
        await manager.stop_recording()


@pytest.mark.asyncio
async def test_mock_backend_stop_when_idle_rejected(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    manager = RecordingManager(config, _make_engine(output_dir), FocusController(10))
    with pytest.raises(RecordingNotActiveError):
        await manager.stop_recording()


@pytest.mark.asyncio
async def test_mock_backend_indexed_in_database_after_stop(output_dir: Path) -> None:
    from sqlmodel import Session, select

    from app.models.recording import Recording, RecordingStatus

    config = _build_mock_config(output_dir)
    engine = _make_engine(output_dir)
    manager = RecordingManager(config, engine, FocusController(10))

    await manager.start_recording()
    await asyncio.sleep(2)
    await manager.stop_recording()

    with Session(engine) as session:
        recordings = session.exec(select(Recording)).all()
        assert len(recordings) == 1
        assert recordings[0].status == RecordingStatus.COMPLETED
        assert recordings[0].is_valid is True
        assert recordings[0].width == 640
        assert recordings[0].height == 480


@pytest.mark.asyncio
async def test_mock_backend_live_frames_during_recording(output_dir: Path) -> None:
    """Kayıt SIRASINDA tee dalından canlı MJPEG kareleri (donanımsız)."""
    config = _build_mock_config(output_dir)
    manager = RecordingManager(config, _make_engine(output_dir), FocusController(10))

    await manager.start_recording()
    try:
        frame = None
        for _ in range(50):
            frame = manager.broadcaster.get_latest_frame()
            if frame is not None:
                break
            await asyncio.sleep(0.1)
        assert frame is not None, "kayıt sırasında canlı kare gelmedi"
        assert frame[:2] == b"\xff\xd8"
    finally:
        await manager.stop_recording()

    # Durduktan sonra broadcaster sıfırlanmış olmalı.
    assert manager.broadcaster.get_latest_frame() is None
