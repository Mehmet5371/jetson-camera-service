"""`.env` dosyasından gizli bilgilerin yüklenmesi.

Şartname böl. 14: gizli bilgiler (SECRET_KEY, admin parola hash'i) config.yaml
DEĞİL, `.env` dosyasında tutulur. Bu modül `.env`'i bir kez yükler ve eksik/boş
zorunlu değişkenler için AÇIK hata fırlatır - sessizce varsayılana düşmez
(config.py'deki felsefeyle aynı).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from app.core.errors import AppError, ErrorCode

_ENV_FILE_ENV_VAR = "JETSON_CAMERA_ENV_FILE"
_DEFAULT_RELATIVE_ENV_FILE = Path(".env")
_loaded = False


class EnvConfigError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(ErrorCode.CONFIG_INVALID, message, status_code=500, details=details or {})


def ensure_env_loaded() -> None:
    global _loaded
    if _loaded:
        return
    env_path = Path(os.environ.get(_ENV_FILE_ENV_VAR, _DEFAULT_RELATIVE_ENV_FILE))
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)
    _loaded = True


def get_required_env(name: str) -> str:
    ensure_env_loaded()
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvConfigError(
            f"Zorunlu ortam değişkeni ayarlanmamış: {name}. install.sh'nin .env dosyasını "
            "doğru şekilde oluşturduğundan emin olun.",
            details={"variable": name},
        )
    return value


def get_optional_env(name: str, default: str = "") -> str:
    ensure_env_loaded()
    return os.environ.get(name, default)
