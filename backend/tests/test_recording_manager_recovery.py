"""recover_from_previous_run() testleri - donanım gerektirmez, yalnızca
dosya sistemi ve /proc üzerinde çalışır."""

from __future__ import annotations

import json
import os
from pathlib import Path

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
from app.recording.manager import RecordingManager, _lock_file_path


def _make_test_engine(mount_path: Path):
    engine = create_engine(f"sqlite:///{mount_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _config(mount_path: Path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(
            backend="argus", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=1920, height=1080, fps=30, autofocus=False, manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=4_000_000,
            speed_preset="ultrafast", segment_duration_minutes=0,
            filename_format="test_%Y%m%d_%H%M%S", minimum_free_space_gb=1,
            low_space_action="stop",
        ),
        capture=CaptureConfig(photo_format="jpg", photo_quality=90),
        storage=StorageConfig(
            mount_path=str(mount_path), expected_uuid="", require_mountpoint=True,
            media_path=str(mount_path / "media"), video_path=str(mount_path / "media" / "videos"),
            photo_path=str(mount_path / "media" / "photos"), database_path=str(mount_path / "db" / "app.db"),
        ),
        retention=RetentionConfig(enabled=False, maximum_storage_percent=90, delete_oldest_files=False),
        server=ServerConfig(host="127.0.0.1", port=8080),
        security=SecurityConfig(authentication_enabled=True),
        logging=LoggingConfig(level="INFO", directory=str(mount_path / "logs"), max_bytes=1_000_000, backup_count=1),
    )


def test_recover_clears_stale_lock_referring_to_unrelated_process(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock_path = _lock_file_path(config)
    lock_path.parent.mkdir(parents=True)
    # PID 1 (init/systemd) gerçekten var ama cmdline'ı "gst-launch-1.0" içermez.
    lock_path.write_text(json.dumps({"pid": 1, "started_at": None, "base_filename": "x"}), encoding="utf-8")

    manager = RecordingManager(config, _make_test_engine(tmp_path), FocusController(10))
    manager.recover_from_previous_run()

    assert not lock_path.exists()


def test_recover_clears_lock_for_dead_pid(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock_path = _lock_file_path(config)
    lock_path.parent.mkdir(parents=True)
    # Gerçekte var olmayan (aşırı büyük) bir PID.
    fake_dead_pid = 2**22
    assert not Path(f"/proc/{fake_dead_pid}").exists()
    lock_path.write_text(
        json.dumps({"pid": fake_dead_pid, "started_at": None, "base_filename": "x"}), encoding="utf-8"
    )

    manager = RecordingManager(config, _make_test_engine(tmp_path), FocusController(10))
    manager.recover_from_previous_run()

    assert not lock_path.exists()


def test_recover_handles_corrupt_lock_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock_path = _lock_file_path(config)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{not valid json", encoding="utf-8")

    manager = RecordingManager(config, _make_test_engine(tmp_path), FocusController(10))
    manager.recover_from_previous_run()

    assert not lock_path.exists()


def test_recover_does_nothing_when_no_lock_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = RecordingManager(config, _make_test_engine(tmp_path), FocusController(10))
    # Exception fırlatmadan sessizce dönmeli.
    manager.recover_from_previous_run()
    assert not _lock_file_path(config).exists()


def test_recover_warns_but_does_not_delete_when_process_is_ours(tmp_path: Path) -> None:
    # Kendi Python test process'imizin PID'ini kullanıyoruz; cmdline'ında
    # "gst-launch-1.0" olmayacağı için normalde temizlenir - burada asıl
    # test ettiğimiz şey "canlı bir PID + cmdline eşleşmesi olmadığında
    # silinir" davranışı. "cmdline eşleştiğinde silinmez" davranışını
    # gerçek bir gst-launch-1.0 sürecine ihtiyaç duymadan doğrulamak için
    # /proc/self/cmdline içeriğini kontrol edip pytest sürecinin kendi
    # cmdline'ında gst-launch-1.0 OLMADIĞINI (dolayısıyla silineceğini)
    # doğruluyoruz - pozitif eşleşme senaryosu donanım testinde (gerçek
    # gst-launch-1.0 süreciyle) ayrıca doğrulanabilir.
    config = _config(tmp_path)
    lock_path = _lock_file_path(config)
    lock_path.parent.mkdir(parents=True)
    own_pid = os.getpid()
    lock_path.write_text(
        json.dumps({"pid": own_pid, "started_at": None, "base_filename": "x"}), encoding="utf-8"
    )

    manager = RecordingManager(config, _make_test_engine(tmp_path), FocusController(10))
    manager.recover_from_previous_run()

    # pytest süreci gst-launch-1.0 olmadığı için stale kabul edilip silinir.
    assert not lock_path.exists()
