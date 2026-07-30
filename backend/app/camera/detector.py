"""Kamera backend algılama ve doğrulama.

Faz 1'de bu donanımda (Arducam IMX519) doğrulanan gerçek: sensör yalnızca
ham Bayer formatı (RG10) sunuyor, bu yüzden v4l2src/UVC ISP olmadan
KULLANILAMAZ; yalnızca nvarguscamerasrc (Argus, ISP dahil) çalışır. Bu
modül bunu varsayım olarak kodlamak yerine her başlangıçta gerçek
komutlarla yeniden doğrular - kamera değişirse (örn. ISP'siz düz bir UVC
kameraya geçilirse) v4l2 backend'i otomatik olarak kullanılabilir hale
gelir.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import CameraConfig
from app.core.errors import AppError, ErrorCode
from app.core.shell import run_command

# v4l2-ctl'nin ham Bayer olarak bildirdiği, ISP olmadan doğrudan encode
# edilemeyecek format kodları. Bu liste dışındaki bir format (NV12, I420,
# YUYV, MJPG, UYVY ...) v4l2 backend için "kullanılabilir" sayılır.
_BAYER_FOURCCS = frozenset(
    {
        "RG10", "RG12", "RG16", "BA10", "BA12",
        "RGGB", "BGGR", "GRBG", "GBRG",
        "pRAA", "pBAA", "pGAA", "pgAA",
    }
)

_FORMAT_LINE = re.compile(r"^\s*\[\d+\]:\s*'(?P<fourcc>[A-Za-z0-9]{4})'")
_SIZE_LINE = re.compile(r"^\s*Size:\s*Discrete\s*(?P<width>\d+)x(?P<height>\d+)")
_FPS_VALUE = re.compile(r"\(([\d.]+)\s*fps\)")


@dataclass
class SensorMode:
    fourcc: str
    width: int
    height: int
    fps: float


@dataclass
class CameraBackendInfo:
    backend: str  # "argus" | "v4l2"
    device: str
    source_element: str  # "nvarguscamerasrc" | "v4l2src"
    sensor_modes: list[SensorMode] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CameraNotAvailableError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.CAMERA_NOT_AVAILABLE, message, status_code=503, details=details or {}
        )


class CameraBackendUnsupportedError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.CAMERA_BACKEND_UNSUPPORTED,
            message,
            status_code=503,
            details=details or {},
        )


def _device_exists(device: str) -> bool:
    path = Path(device)
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode)


def _gstreamer_element_available(element_name: str) -> bool:
    return run_command(["gst-inspect-1.0", element_name], timeout=5.0).ok


def _nvargus_daemon_active() -> bool:
    result = run_command(["systemctl", "is-active", "nvargus-daemon"], timeout=5.0)
    return result.stdout.strip() == "active"


def _parse_v4l2_sensor_modes(device: str) -> list[SensorMode]:
    result = run_command(
        ["v4l2-ctl", "--device", device, "--list-formats-ext"], timeout=5.0
    )
    if not result.ok:
        return []

    modes: list[SensorMode] = []
    current_fourcc: str | None = None
    current_size: tuple[int, int] | None = None

    for line in result.stdout.splitlines():
        format_match = _FORMAT_LINE.match(line)
        if format_match:
            current_fourcc = format_match.group("fourcc")
            current_size = None
            continue

        size_match = _SIZE_LINE.match(line)
        if size_match:
            current_size = (int(size_match.group("width")), int(size_match.group("height")))
            continue

        fps_match = _FPS_VALUE.search(line)
        if fps_match and current_fourcc is not None and current_size is not None:
            modes.append(
                SensorMode(
                    fourcc=current_fourcc,
                    width=current_size[0],
                    height=current_size[1],
                    fps=float(fps_match.group(1)),
                )
            )

    return modes


def detect_camera_backend(camera_config: CameraConfig) -> CameraBackendInfo:
    """Yapılandırmaya göre kullanılabilir kamera backend'ini belirler.

    camera_config.backend == "auto" ise önce argus, olmazsa v4l2 denenir.
    "argus" veya "v4l2" açıkça seçildiyse yalnızca o denenir; kullanılamazsa
    net bir CameraNotAvailableError / CameraBackendUnsupportedError fırlatılır
    (servis çökmez, çağıran katman -API/dashboard- bunu anlaşılır bir hataya
    çevirir). "mock" YALNIZCA testler için - gerçek donanıma hiç bakmadan
    videotestsrc kullanan bir CameraBackendInfo döner (bkz. şartname böl. 21).
    """
    if camera_config.backend == "mock":
        return CameraBackendInfo(
            backend="mock",
            device=camera_config.device,
            source_element="videotestsrc",
            sensor_modes=[],
            warnings=["mock backend kullanılıyor - gerçek kamera donanımı YOK, yalnızca test amaçlı."],
        )

    device = camera_config.device
    device_present = _device_exists(device)
    all_modes = _parse_v4l2_sensor_modes(device) if device_present else []

    argus_element_ok = _gstreamer_element_available("nvarguscamerasrc")
    daemon_ok = _nvargus_daemon_active()
    argus_available = device_present and argus_element_ok and daemon_ok

    v4l2_element_ok = _gstreamer_element_available("v4l2src")
    usable_modes = [m for m in all_modes if m.fourcc.upper() not in _BAYER_FOURCCS]
    v4l2_available = device_present and v4l2_element_ok and bool(usable_modes)

    diagnostics = {
        "device": device,
        "device_present": device_present,
        "nvarguscamerasrc_plugin_available": argus_element_ok,
        "nvargus_daemon_active": daemon_ok,
        "v4l2src_plugin_available": v4l2_element_ok,
        "raw_sensor_modes": [m.fourcc for m in all_modes],
    }

    configured = camera_config.backend

    if configured == "argus":
        if not argus_available:
            raise CameraNotAvailableError(
                "Yapılandırmada 'argus' seçili ama Argus kamera altyapısı kullanılamıyor.",
                details=diagnostics,
            )
        return CameraBackendInfo(
            backend="argus", device=device, source_element="nvarguscamerasrc",
            sensor_modes=all_modes,
        )

    if configured == "v4l2":
        if not v4l2_available:
            if all_modes and not usable_modes:
                reason = (
                    "Kamera yalnızca ham Bayer formatı sunuyor "
                    f"({sorted({m.fourcc for m in all_modes})}); v4l2 backend ISP "
                    "içermediği için bu formatı doğrudan encode edemez."
                )
            else:
                reason = "v4l2 kamera erişilebilir değil."
            raise CameraBackendUnsupportedError(reason, details=diagnostics)
        return CameraBackendInfo(
            backend="v4l2", device=device, source_element="v4l2src",
            sensor_modes=usable_modes,
        )

    # configured == "auto"
    if argus_available:
        return CameraBackendInfo(
            backend="argus", device=device, source_element="nvarguscamerasrc",
            sensor_modes=all_modes,
        )
    if v4l2_available:
        return CameraBackendInfo(
            backend="v4l2", device=device, source_element="v4l2src",
            sensor_modes=usable_modes,
            warnings=["Argus kullanılamadı, v4l2 backend'ine düşüldü."],
        )
    raise CameraNotAvailableError(
        "Ne Argus ne de v4l2 üzerinden kullanılabilir bir kamera bulunamadı.",
        details=diagnostics,
    )
