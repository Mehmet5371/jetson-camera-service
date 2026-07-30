"""Path traversal koruması testleri (şartname böl. 10/21) - donanım gerektirmez.

Kullanıcı /api/recordings/{id}/stream veya /download'a asla ham bir dosya
yolu göndermez (yalnızca tamsayı id) - path traversal riski yapısal olarak
zaten engellidir. Bu testler, DB'deki file_path alanı bir şekilde
(bozulma, manuel müdahale) izin verilen klasörün dışına işaret ederse
savunma amaçlı ikinci katmanın gerçekten çalıştığını doğrular.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.api.recordings import _resolve_safe_path
from app.core.errors import AppError, ErrorCode
from app.models.recording import Recording, RecordingStatus


def _recording(file_path: str) -> Recording:
    return Recording(
        filename=Path(file_path).name,
        file_path=file_path,
        started_at=datetime.now(timezone.utc),
        status=RecordingStatus.COMPLETED,
        is_valid=True,
    )


def test_file_inside_allowed_dir_is_accepted(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "videos"
    allowed_dir.mkdir()
    recording = _recording(str(allowed_dir / "test.mp4"))

    resolved = _resolve_safe_path(recording, allowed_dir)
    assert resolved == (allowed_dir / "test.mp4").resolve()


def test_absolute_path_outside_allowed_dir_is_rejected(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "videos"
    allowed_dir.mkdir()
    recording = _recording("/etc/passwd")

    with pytest.raises(AppError) as exc_info:
        _resolve_safe_path(recording, allowed_dir)
    assert exc_info.value.code == ErrorCode.PATH_TRAVERSAL_REJECTED


def test_dot_dot_traversal_is_rejected(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "videos"
    allowed_dir.mkdir()
    sibling_secret = tmp_path / "secret.txt"
    sibling_secret.write_text("gizli")
    recording = _recording(str(allowed_dir / ".." / "secret.txt"))

    with pytest.raises(AppError) as exc_info:
        _resolve_safe_path(recording, allowed_dir)
    assert exc_info.value.code == ErrorCode.PATH_TRAVERSAL_REJECTED


def test_sibling_directory_is_rejected(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "videos"
    allowed_dir.mkdir()
    other_dir = tmp_path / "photos"
    other_dir.mkdir()
    recording = _recording(str(other_dir / "test.mp4"))

    with pytest.raises(AppError) as exc_info:
        _resolve_safe_path(recording, allowed_dir)
    assert exc_info.value.code == ErrorCode.PATH_TRAVERSAL_REJECTED
