"""Sistem bilgisi toplama: psutil + /proc + /sys/class/thermal.

`nvidia-jetpack` apt paketi bu cihazda KURULU DEĞİL (Faz 5'te doğrulandı -
`dpkg-query` boş dönüyor), bu yüzden JetPack sürümü doğrudan sorgulanamıyor.
Bunun yerine `/etc/nv_tegra_release`'den L4T sürümü okunup bilinen
L4T->JetPack eşlemesinden (NVIDIA'nın yayınladığı resmi tabloya göre)
türetiliyor. Eşleşme yoksa None döner - asla tahmini bir sürüm uydurulmaz.

Sıcaklık için tegrastats'ın metin çıktısını parse etmek yerine (format
değişikliklerine karşı kırılgan) doğrudan `/sys/class/thermal/thermal_zone*`
okunuyor - bu cihazda gerçek zone isimleri doğrulandı: cpu-thermal,
gpu-thermal, cv0/1/2-thermal, soc0/1/2-thermal, tj-thermal.
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.core.shell import run_command

_BOOT_TIME = psutil.boot_time()

# NVIDIA'nın resmi L4T <-> JetPack eşleme tablosundan (yalnızca bu projede
# karşılaşılmış/doğrulanmış sürümler eklendi; kapsam dışı bir L4T sürümü
# için tahmin yapılmaz).
_L4T_TO_JETPACK: dict[str, str] = {
    "R36.4.4": "6.2.1",
}


@dataclass
class SystemInfo:
    hostname: str
    jetson_model: str | None
    jetpack_version: str | None
    l4t_version: str | None
    ubuntu_version: str | None
    ip_addresses: list[str]
    system_time: str
    timezone: str
    uptime_seconds: float


@dataclass
class ResourceUsage:
    cpu_percent: float
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float
    disk_percent_root: float
    cpu_temp_celsius: float | None
    gpu_temp_celsius: float | None


def get_hostname() -> str:
    return socket.gethostname()


def get_ip_addresses() -> list[str]:
    addresses: list[str] = []
    for interface, addrs in psutil.net_if_addrs().items():
        if interface == "lo":
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET:
                addresses.append(addr.address)
    return addresses


def get_jetson_model() -> str | None:
    path = Path("/proc/device-tree/model")
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    text = raw.split(b"\x00")[0].decode(errors="replace").strip()
    return text or None


def _read_l4t_version() -> str | None:
    path = Path("/etc/nv_tegra_release")
    if not path.is_file():
        return None
    content = path.read_text(errors="replace")
    match = re.search(r"# R(\d+) \(release\), REVISION: ([\d.]+)", content)
    if not match:
        return None
    return f"R{match.group(1)}.{match.group(2)}"


def get_l4t_and_jetpack_version() -> tuple[str | None, str | None]:
    l4t_version = _read_l4t_version()
    jetpack_version = _L4T_TO_JETPACK.get(l4t_version) if l4t_version else None
    return l4t_version, jetpack_version


def get_ubuntu_version() -> str | None:
    path = Path("/etc/os-release")
    if not path.is_file():
        return None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("VERSION_ID="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def get_timezone() -> str:
    result = run_command(["timedatectl", "show", "--property=Timezone", "--value"], timeout=5.0)
    if result.ok and result.stdout.strip():
        return result.stdout.strip()
    return "unknown"


def get_system_info() -> SystemInfo:
    l4t_version, jetpack_version = get_l4t_and_jetpack_version()
    return SystemInfo(
        hostname=get_hostname(),
        jetson_model=get_jetson_model(),
        jetpack_version=jetpack_version,
        l4t_version=l4t_version,
        ubuntu_version=get_ubuntu_version(),
        ip_addresses=get_ip_addresses(),
        system_time=time.strftime("%Y-%m-%d %H:%M:%S"),
        timezone=get_timezone(),
        uptime_seconds=time.time() - _BOOT_TIME,
    )


def _read_thermal_zone_temps() -> dict[str, float]:
    temps: dict[str, float] = {}
    base = Path("/sys/class/thermal")
    if not base.is_dir():
        return temps
    for zone_dir in sorted(base.glob("thermal_zone*")):
        type_path = zone_dir / "type"
        temp_path = zone_dir / "temp"
        if not (type_path.is_file() and temp_path.is_file()):
            continue
        try:
            zone_type = type_path.read_text().strip()
            raw_temp = int(temp_path.read_text().strip())
        except (OSError, ValueError, TypeError):
            # Bazı thermal zone'lar (örn. bu cihazda cv0/cv1/cv2-thermal,
            # kullanılmayan compute-vision çekirdekleri için) boş/okunamaz
            # içerik döndürüyor ve pathlib.read_text() garip bir TypeError
            # fırlatabiliyor (gerçek donanımda gözlemlendi). Bu zone'u atla.
            continue
        temps[zone_type] = raw_temp / 1000.0
    return temps


def get_resource_usage(root_path: str = "/") -> ResourceUsage:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(root_path)
    temps = _read_thermal_zone_temps()

    return ResourceUsage(
        cpu_percent=psutil.cpu_percent(interval=0.2),
        ram_used_mb=memory.used / (1024 * 1024),
        ram_total_mb=memory.total / (1024 * 1024),
        ram_percent=memory.percent,
        disk_percent_root=disk.percent,
        cpu_temp_celsius=temps.get("cpu-thermal"),
        gpu_temp_celsius=temps.get("gpu-thermal"),
    )
