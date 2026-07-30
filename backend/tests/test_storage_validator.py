from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import StorageConfig
from app.storage.validator import (
    StorageNotMountedError,
    StorageWriteTestFailedError,
    check_minimum_free_space,
    get_disk_usage,
    validate_storage,
)


def _storage_config(mount_path: Path, **overrides) -> StorageConfig:
    defaults = dict(
        mount_path=str(mount_path),
        expected_uuid="",
        require_mountpoint=False,
        media_path=str(mount_path / "media"),
        video_path=str(mount_path / "media" / "videos"),
        photo_path=str(mount_path / "media" / "photos"),
        database_path=str(mount_path / "db" / "app.db"),
    )
    defaults.update(overrides)
    return StorageConfig(**defaults)


def test_validate_storage_succeeds_when_writable(tmp_path: Path) -> None:
    config = _storage_config(tmp_path, require_mountpoint=False)
    usage = validate_storage(config)
    assert usage.total_bytes > 0
    assert usage.free_bytes >= 0


def test_validate_storage_fails_when_require_mountpoint_and_not_a_mount(tmp_path: Path) -> None:
    # tmp_path normal bir alt dizindir, gerçek bir mount noktası değildir.
    config = _storage_config(tmp_path, require_mountpoint=True)
    with pytest.raises(StorageNotMountedError):
        validate_storage(config)


def test_validate_storage_fails_when_path_not_writable(tmp_path: Path) -> None:
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o555)
    try:
        config = _storage_config(readonly_dir, require_mountpoint=False)
        with pytest.raises(StorageWriteTestFailedError):
            validate_storage(config)
    finally:
        readonly_dir.chmod(0o755)


def test_get_disk_usage_returns_consistent_values(tmp_path: Path) -> None:
    usage = get_disk_usage(str(tmp_path))
    assert usage.total_bytes >= usage.used_bytes
    assert usage.used_bytes + usage.free_bytes <= usage.total_bytes + 1  # dosya sistemi payı için tolerans
    assert 0 <= usage.percent_used <= 100


def test_check_minimum_free_space_raises_when_insufficient(tmp_path: Path) -> None:
    usage = get_disk_usage(str(tmp_path))
    huge_minimum_gb = (usage.total_bytes / (1024**3)) + 1_000_000
    from app.storage.validator import StorageInsufficientSpaceError

    with pytest.raises(StorageInsufficientSpaceError):
        check_minimum_free_space(usage, huge_minimum_gb)


def test_check_minimum_free_space_passes_when_sufficient(tmp_path: Path) -> None:
    usage = get_disk_usage(str(tmp_path))
    check_minimum_free_space(usage, 0.0)
