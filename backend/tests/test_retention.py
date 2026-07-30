from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

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
from app.models.recording import Recording, RecordingStatus
from app.storage.retention import enforce_retention
from app.storage.validator import DiskUsage


def _config(tmp_path: Path, *, enabled: bool, delete_oldest: bool, max_percent: float) -> AppConfig:
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
            mount_path=str(tmp_path), expected_uuid="", require_mountpoint=False,
            media_path=str(tmp_path / "media"), video_path=str(tmp_path / "media" / "videos"),
            photo_path=str(tmp_path / "media" / "photos"), database_path=str(tmp_path / "db" / "app.db"),
        ),
        retention=RetentionConfig(
            enabled=enabled, maximum_storage_percent=max_percent, delete_oldest_files=delete_oldest
        ),
        server=ServerConfig(host="127.0.0.1", port=8080),
        security=SecurityConfig(authentication_enabled=True),
        logging=LoggingConfig(level="INFO", directory=str(tmp_path / "logs"), max_bytes=1_000_000, backup_count=1),
    )


def _make_recording(video_dir: Path, name: str, age_hours: int, size_bytes: int) -> Recording:
    path = video_dir / name
    path.write_bytes(b"x" * size_bytes)
    return Recording(
        filename=name,
        file_path=str(path),
        started_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        file_size_bytes=size_bytes,
        status=RecordingStatus.COMPLETED,
        is_valid=True,
    )


def test_retention_disabled_does_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False, delete_oldest=True, max_percent=1.0)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    video_dir = tmp_path / "media" / "videos"
    video_dir.mkdir(parents=True)

    with Session(engine) as session:
        rec = _make_recording(video_dir, "old.mp4", age_hours=10, size_bytes=100)
        session.add(rec)
        session.commit()

        usage = DiskUsage(total_bytes=1000, used_bytes=990, free_bytes=10, percent_used=99.0)
        deleted = enforce_retention(session, config, usage)

        assert deleted == []
        assert (video_dir / "old.mp4").exists()


def test_retention_below_threshold_does_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True, delete_oldest=True, max_percent=90.0)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    video_dir = tmp_path / "media" / "videos"
    video_dir.mkdir(parents=True)

    with Session(engine) as session:
        rec = _make_recording(video_dir, "old.mp4", age_hours=10, size_bytes=100)
        session.add(rec)
        session.commit()

        usage = DiskUsage(total_bytes=1000, used_bytes=500, free_bytes=500, percent_used=50.0)
        deleted = enforce_retention(session, config, usage)

        assert deleted == []
        assert (video_dir / "old.mp4").exists()


def test_retention_deletes_oldest_first_until_under_threshold(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True, delete_oldest=True, max_percent=80.0)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    video_dir = tmp_path / "media" / "videos"
    video_dir.mkdir(parents=True)

    with Session(engine) as session:
        oldest = _make_recording(video_dir, "oldest.mp4", age_hours=100, size_bytes=100)
        middle = _make_recording(video_dir, "middle.mp4", age_hours=50, size_bytes=100)
        newest = _make_recording(video_dir, "newest.mp4", age_hours=1, size_bytes=100)
        session.add_all([newest, oldest, middle])  # kasıtlı karışık ekleme sırası
        session.commit()

        # total=1000, used=900 (%90) -> eşik %80 altına düşene kadar sil.
        usage = DiskUsage(total_bytes=1000, used_bytes=900, free_bytes=100, percent_used=90.0)
        deleted = enforce_retention(session, config, usage)

        assert str(video_dir / "oldest.mp4") in deleted
        assert not (video_dir / "oldest.mp4").exists()
        # En yeni dosya asla ilk silinen olmamalı.
        assert deleted[0] == str(video_dir / "oldest.mp4")
        assert (video_dir / "newest.mp4").exists()


def test_retention_never_deletes_active_recording(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=True, delete_oldest=True, max_percent=1.0)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    video_dir = tmp_path / "media" / "videos"
    video_dir.mkdir(parents=True)

    with Session(engine) as session:
        active = _make_recording(video_dir, "active.mp4", age_hours=1000, size_bytes=100)
        active.status = RecordingStatus.RECORDING
        session.add(active)
        session.commit()

        usage = DiskUsage(total_bytes=1000, used_bytes=999, free_bytes=1, percent_used=99.9)
        deleted = enforce_retention(session, config, usage)

        assert deleted == []
        assert (video_dir / "active.mp4").exists()
