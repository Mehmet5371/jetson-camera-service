from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import load_config
from app.core.errors import AppError

VALID_CONFIG = """
camera:
  backend: auto
  sensor_id: 0
  device: /dev/video0
  i2c_bus: 10
  width: 1920
  height: 1080
  fps: 30
  autofocus: true
  manual_focus_value: 250

preview:
  width: 960
  height: 540
  fps: 15
  jpeg_quality: 75

recording:
  codec: h264
  encoder: software
  container: mp4
  bitrate_bps: 8000000
  speed_preset: ultrafast
  segment_duration_minutes: 30
  filename_format: "%Y-%m-%d_%H-%M-%S"
  minimum_free_space_gb: 5
  low_space_action: stop

capture:
  photo_format: jpg
  photo_quality: 95

storage:
  mount_path: /mnt/recordings
  expected_uuid: ""
  require_mountpoint: true
  media_path: /mnt/recordings/media
  video_path: /mnt/recordings/media/videos
  photo_path: /mnt/recordings/media/photos
  database_path: /mnt/recordings/db/app.db

retention:
  enabled: false
  maximum_storage_percent: 90
  delete_oldest_files: false

server:
  host: 0.0.0.0
  port: 8080

security:
  authentication_enabled: true

logging:
  level: INFO
  directory: /mnt/recordings/jetson-camera-service/logs
  max_bytes: 10485760
  backup_count: 5
"""


def test_load_valid_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG, encoding="utf-8")

    config = load_config(config_file)

    assert config.camera.backend == "auto"
    assert config.camera.device == "/dev/video0"
    assert config.recording.encoder == "software"
    assert config.storage.database_path == "/mnt/recordings/db/app.db"


def test_missing_file_raises_app_error(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.yaml"

    with pytest.raises(AppError):
        load_config(missing_path)


def test_malformed_yaml_raises_app_error(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("camera: [unclosed", encoding="utf-8")

    with pytest.raises(AppError):
        load_config(config_file)


def test_invalid_camera_backend_raises_app_error(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        VALID_CONFIG.replace("backend: auto", "backend: not-a-real-backend"),
        encoding="utf-8",
    )

    with pytest.raises(AppError):
        load_config(config_file)


def test_missing_required_field_raises_app_error(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        VALID_CONFIG.replace("device: /dev/video0\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(AppError):
        load_config(config_file)


def test_non_mapping_root_raises_app_error(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(AppError):
        load_config(config_file)
