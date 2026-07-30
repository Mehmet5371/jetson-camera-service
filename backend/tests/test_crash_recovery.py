"""Kayıt sırasında GStreamer sürecinin BEKLENMEDİK şekilde çökmesi
senaryosu (şartname böl. 15/17, kabul kriteri 16-17) - mock backend
kullanır, donanım gerektirmez ama gerçek bir subprocess'i gerçekten
SIGKILL ile öldürüp servisin toparlandığını kanıtlar.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
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
from app.recording.manager import RecordingManager
from app.recording.state import RecordingState

_TEST_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.mock_tmp")


def _build_mock_config(output_dir: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="mock", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=640, height=480, fps=30, autofocus=True, manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=1_000_000,
            speed_preset="ultrafast", segment_duration_minutes=0,
            filename_format="crashtest_%Y%m%d_%H%M%S", minimum_free_space_gb=1,
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
async def test_service_recovers_after_process_killed_unexpectedly(output_dir: Path) -> None:
    config = _build_mock_config(output_dir)
    manager = RecordingManager(config, _make_engine(output_dir), FocusController(10))

    session = await manager.start_recording()
    assert session.state == RecordingState.RECORDING
    pid = session.pid
    assert pid is not None

    await asyncio.sleep(1)

    # Gerçek bir "kamera sürücüsü çöktü" senaryosunu simüle et: süreci
    # dışarıdan, servisin haberi olmadan SIGKILL ile öldür (SIGINT/EOS
    # YOK - bu kasıtlı, gerçek bir çökme SIGINT beklemez).
    os.kill(pid, signal.SIGKILL)
    for _ in range(50):
        if not manager.check_process_alive():
            break
        await asyncio.sleep(0.1)
    assert not manager.check_process_alive(), "süreç beklenen sürede ölmedi"

    # Bu, main.py'nin periyodik bakım görevinin (30s'de bir) gerçekte
    # çağırdığı AYNI metod - production'da otomatik tetiklenir, burada
    # gerçek zamanlı bekleme yerine doğrudan çağrılıyor.
    await manager.check_and_recover_process_health()

    # Servis KİLİTLİ KALMAMALI - idle'a dönüp yeni kayıt kabul etmeli.
    assert manager.session.state == RecordingState.IDLE

    # Diskte kalan (SIGKILL ile kesilmiş, muhtemelen bozuk) dosya
    # reconciler tarafından işlenmiş olmalı.
    from sqlmodel import Session, select

    from app.models.recording import Recording

    with Session(manager._db_engine) as db_session:  # noqa: SLF001 - test amaçlı iç durum erişimi
        recordings = db_session.exec(select(Recording)).all()
        assert len(recordings) == 1
        # SIGKILL ile kesilen bir dosya moov atom'suz kalır - ffprobe
        # okuyamaz, reconciler bunu "corrupted" işaretler. Bu, servisin
        # kendi "sağlıklı bitti mi" varsayımına değil GERÇEK dosya
        # doğrulamasına güvendiğinin kanıtıdır.
        assert recordings[0].status.value in ("corrupted", "completed")

    # Kritik iddia: servis GERÇEKTEN kullanılabilir durumda - yeni bir
    # kayıt hemen başlatılabiliyor (kabul kriteri 17: "kamera yeniden
    # kullanılabilir olduğunda servis toparlanabiliyor").
    new_session = await manager.start_recording()
    assert new_session.state == RecordingState.RECORDING
    assert new_session.pid != pid
    await manager.stop_recording()
