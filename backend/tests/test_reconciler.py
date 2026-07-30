"""Reconciler testleri - gerçek ffmpeg/ffprobe kullanır (kamera gerekmez).

Sentetik test videoları `ffmpeg -f lavfi -i testsrc` ile üretilir; bu
kamera donanımından tamamen bağımsızdır ve hızlıdır.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.recording import Recording, RecordingStatus
from app.storage.reconciler import reconcile_recordings


def _make_synthetic_mp4(path: Path, duration_seconds: float = 1.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi", "-i", f"testsrc=duration={duration_seconds}:size=320x240:rate=10",
            "-c:v", "libx264", "-y", str(path),
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )


def _make_engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_reconcile_indexes_untracked_file(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    video_path = video_dir / "untracked.mp4"
    _make_synthetic_mp4(video_path)

    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        reconcile_recordings(session, video_dir)

        recordings = session.exec(select(Recording)).all()
        assert len(recordings) == 1
        rec = recordings[0]
        assert rec.file_path == str(video_path)
        assert rec.status == RecordingStatus.COMPLETED
        assert rec.is_valid is True
        assert rec.width == 320
        assert rec.height == 240
        assert rec.codec == "h264"
        assert rec.duration_seconds is not None and rec.duration_seconds > 0
        assert rec.file_size_bytes is not None and rec.file_size_bytes > 0


def test_reconcile_marks_deleted_file_as_missing(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    video_path = video_dir / "will_be_deleted.mp4"
    _make_synthetic_mp4(video_path)

    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        reconcile_recordings(session, video_dir)

    video_path.unlink()

    with Session(engine) as session:
        reconcile_recordings(session, video_dir)
        recordings = session.exec(select(Recording)).all()
        assert len(recordings) == 1
        assert recordings[0].status == RecordingStatus.MISSING
        assert recordings[0].is_valid is False


def test_reconcile_marks_corrupt_file_as_corrupted(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    corrupt_path = video_dir / "corrupt.mp4"
    corrupt_path.write_bytes(b"not a real mp4 file at all")

    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        reconcile_recordings(session, video_dir)
        recordings = session.exec(select(Recording)).all()
        assert len(recordings) == 1
        assert recordings[0].status == RecordingStatus.CORRUPTED
        assert recordings[0].is_valid is False
        assert recordings[0].error_message is not None


def test_reconcile_is_idempotent_for_already_tracked_files(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    video_path = video_dir / "stable.mp4"
    _make_synthetic_mp4(video_path)

    engine = _make_engine(tmp_path)
    with Session(engine) as session:
        reconcile_recordings(session, video_dir)
    with Session(engine) as session:
        reconcile_recordings(session, video_dir)
        recordings = session.exec(select(Recording)).all()
        # Aynı dosya iki kez taranmış olmasına rağmen tek kayıt olmalı.
        assert len(recordings) == 1
