"""Depolama doğrulama: mount kontrolü, yazma testi, disk kullanımı.

Şartname böl. 8'in temel amacı: SSD mount değilse asla SD karta (veya
başka bir yanlış konuma) yazma riski alınmaz. Bu kontroller kayıt
başlamadan ÖNCE her seferinde yeniden çalıştırılır - önbelleğe alınmaz,
çünkü SSD çıkarılmış/yeniden takılmış olabilir.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import StorageConfig
from app.core.errors import AppError, ErrorCode
from app.core.shell import run_command


class StorageNotMountedError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(ErrorCode.STORAGE_NOT_MOUNTED, message, status_code=503, details=details or {})


class StorageUuidMismatchError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(ErrorCode.STORAGE_UUID_MISMATCH, message, status_code=503, details=details or {})


class StorageWriteTestFailedError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(ErrorCode.STORAGE_WRITE_TEST_FAILED, message, status_code=503, details=details or {})


class StorageInsufficientSpaceError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(ErrorCode.STORAGE_INSUFFICIENT_SPACE, message, status_code=507, details=details or {})


@dataclass
class DiskUsage:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float


def _get_mount_uuid(mount_path: str) -> str | None:
    result = run_command(["findmnt", "-n", "-o", "UUID", "--target", mount_path], timeout=5.0)
    if not result.ok:
        return None
    value = result.stdout.strip()
    return value or None


def get_disk_usage(mount_path: str) -> DiskUsage:
    usage = shutil.disk_usage(mount_path)
    percent_used = (usage.used / usage.total) * 100 if usage.total > 0 else 0.0
    return DiskUsage(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        percent_used=percent_used,
    )


def validate_storage(storage: StorageConfig) -> DiskUsage:
    """Mount, UUID ve yazılabilirlik kontrollerini yapar; disk kullanımını döner.

    Herhangi bir kontrol başarısız olursa AppError alt sınıfı fırlatır -
    çağıran (RecordingManager/API) bunu net bir dashboard hatasına çevirir,
    servis çökmez.
    """
    mount_path = Path(storage.mount_path)

    if storage.require_mountpoint and not os.path.ismount(mount_path):
        raise StorageNotMountedError(
            f"{mount_path} bir mount noktası değil. SD karta yanlışlıkla "
            "yazmayı önlemek için kayıt başlatılamıyor.",
            details={"mount_path": str(mount_path)},
        )

    if storage.expected_uuid:
        actual_uuid = _get_mount_uuid(str(mount_path))
        if actual_uuid is not None and actual_uuid.lower() != storage.expected_uuid.lower():
            raise StorageUuidMismatchError(
                f"{mount_path} mount edilmiş ama beklenen disk değil "
                f"(beklenen UUID={storage.expected_uuid}, bulunan={actual_uuid}).",
                details={"expected_uuid": storage.expected_uuid, "actual_uuid": actual_uuid},
            )

    probe_path = mount_path / f".write_test_{uuid.uuid4().hex}"
    try:
        probe_path.write_text("write test", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        raise StorageWriteTestFailedError(
            f"{mount_path} üzerine yazma testi başarısız: {exc}",
            details={"mount_path": str(mount_path)},
        ) from exc

    return get_disk_usage(str(mount_path))


def check_minimum_free_space(usage: DiskUsage, minimum_free_gb: float) -> None:
    minimum_free_bytes = minimum_free_gb * (1024**3)
    if usage.free_bytes < minimum_free_bytes:
        raise StorageInsufficientSpaceError(
            f"Yetersiz disk alanı: {usage.free_bytes / (1024**3):.2f} GB boş, "
            f"minimum {minimum_free_gb} GB gerekiyor.",
            details={"free_bytes": usage.free_bytes, "minimum_free_gb": minimum_free_gb},
        )
