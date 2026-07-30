"""Kamera algılama testleri - gerçek v4l2-ctl/gst-inspect-1.0 komutlarını
çalıştırır (mock değildir), ancak kamerayı AÇMAZ (yalnızca sorgular), bu
yüzden aktif bir kayıtla çakışmaz.
"""

from __future__ import annotations

import pytest

from app.camera.detector import (
    CameraBackendUnsupportedError,
    CameraNotAvailableError,
    detect_camera_backend,
)
from app.core.config import CameraConfig

pytestmark = pytest.mark.hardware


def _camera_config(backend: str) -> CameraConfig:
    return CameraConfig(
        backend=backend,
        sensor_id=0,
        device="/dev/video0",
        i2c_bus=10,
        width=1920,
        height=1080,
        fps=30,
        autofocus=False,
        manual_focus_value=250,
    )


def test_argus_detected_on_this_hardware() -> None:
    info = detect_camera_backend(_camera_config("argus"))
    assert info.backend == "argus"
    assert info.source_element == "nvarguscamerasrc"
    assert len(info.sensor_modes) == 4


def test_auto_prefers_argus_on_this_hardware() -> None:
    info = detect_camera_backend(_camera_config("auto"))
    assert info.backend == "argus"


def test_v4l2_rejected_for_raw_bayer_sensor() -> None:
    # Faz 1'de doğrulandı: bu sensör yalnızca RG10 (ham Bayer) sunuyor, ISP
    # olmadan v4l2 backend ile encode edilemez.
    with pytest.raises(CameraBackendUnsupportedError):
        detect_camera_backend(_camera_config("v4l2"))


def test_nonexistent_device_raises_camera_not_available() -> None:
    config = _camera_config("argus")
    config = config.model_copy(update={"device": "/dev/video99"})
    with pytest.raises(CameraNotAvailableError):
        detect_camera_backend(config)
