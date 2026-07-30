"""build_pipeline_args() saf unit testleri - donanım/subprocess gerektirmez,
yalnızca üretilen argüman listesinin doğruluğunu kontrol eder."""

from __future__ import annotations

from pathlib import Path

from app.camera.detector import CameraBackendInfo
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
from app.recording.pipeline import build_pipeline_args


def _config(**recording_overrides) -> AppConfig:
    defaults = dict(
        codec="h264", encoder="software", container="mp4", bitrate_bps=8_000_000,
        speed_preset="ultrafast", segment_duration_minutes=30,
        filename_format="%Y-%m-%d_%H-%M-%S", minimum_free_space_gb=5,
        low_space_action="stop",
    )
    defaults.update(recording_overrides)
    return AppConfig(
        camera=CameraConfig(
            backend="auto", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=1920, height=1080, fps=30, autofocus=False, manual_focus_value=250,
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(**defaults),
        capture=CaptureConfig(photo_format="jpg", photo_quality=90),
        storage=StorageConfig(
            mount_path="/mnt/recordings", expected_uuid="", require_mountpoint=True,
            media_path="/mnt/recordings/media", video_path="/mnt/recordings/media/videos",
            photo_path="/mnt/recordings/media/photos", database_path="/mnt/recordings/db/app.db",
        ),
        retention=RetentionConfig(enabled=False, maximum_storage_percent=90, delete_oldest_files=False),
        server=ServerConfig(host="0.0.0.0", port=8080),
        security=SecurityConfig(authentication_enabled=True),
        logging=LoggingConfig(level="INFO", directory="/tmp/logs", max_bytes=1_000_000, backup_count=1),
    )


def test_argus_backend_uses_nvarguscamerasrc_and_nvvidconv() -> None:
    config = _config()
    backend_info = CameraBackendInfo(backend="argus", device="/dev/video0", source_element="nvarguscamerasrc")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "test")

    assert args[0] == "gst-launch-1.0"
    assert "-e" in args
    assert "nvarguscamerasrc" in args
    assert "sensor-id=0" in args
    assert "nvvidconv" in args
    assert any("memory:NVMM" in a for a in args)
    assert "x264enc" in args


def test_v4l2_backend_uses_v4l2src_and_videoconvert() -> None:
    config = _config()
    backend_info = CameraBackendInfo(backend="v4l2", device="/dev/video0", source_element="v4l2src")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "test")

    assert "v4l2src" in args
    assert "device=/dev/video0" in args
    assert "videoconvert" in args
    assert not any("memory:NVMM" in a for a in args)


def test_mock_backend_uses_videotestsrc() -> None:
    config = _config()
    backend_info = CameraBackendInfo(backend="mock", device="/dev/video0", source_element="videotestsrc")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "test")

    assert "videotestsrc" in args
    assert "is-live=true" in args


def test_segmented_recording_uses_indexed_filename_pattern() -> None:
    config = _config(segment_duration_minutes=30)
    backend_info = CameraBackendInfo(backend="argus", device="/dev/video0", source_element="nvarguscamerasrc")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "myrecording")

    location_arg = next(a for a in args if a.startswith("location="))
    assert location_arg == "location=/tmp/videos/myrecording_%03d.mp4"
    max_size_arg = next(a for a in args if a.startswith("max-size-time="))
    assert max_size_arg == f"max-size-time={30 * 60_000_000_000}"


def test_single_file_recording_has_no_index_suffix() -> None:
    config = _config(segment_duration_minutes=0)
    backend_info = CameraBackendInfo(backend="argus", device="/dev/video0", source_element="nvarguscamerasrc")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "myrecording")

    location_arg = next(a for a in args if a.startswith("location="))
    assert location_arg == "location=/tmp/videos/myrecording.mp4"
    assert "max-size-time=0" in args


def test_bitrate_is_converted_from_bps_to_kbps_for_x264enc() -> None:
    # x264enc "bitrate" özelliği kbit/s bekler, config bps tutar - Faz 3'te
    # doğrulanan kritik dönüşüm.
    config = _config(bitrate_bps=8_000_000)
    backend_info = CameraBackendInfo(backend="argus", device="/dev/video0", source_element="nvarguscamerasrc")
    args = build_pipeline_args(config, backend_info, Path("/tmp/videos"), "test")

    assert "bitrate=8000" in args
    assert "bitrate=8000000" not in args
