"""Uygulama genelinde kullanılan standart hata modeli.

API sözleşmesi tüm hataların şu JSON gövdesiyle döndürülmesini şart koşar:
    {"success": false, "error": {"code": "...", "message": "...", "details": {}}}
Bu modül o sözleşmeyi tek bir yerden üretir; her katman (config, kamera,
kayıt, storage, API) aynı hata tipini fırlatır.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ErrorCode(str, Enum):
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"

    # Faz 3: kamera ve kayıt hataları
    CAMERA_NOT_AVAILABLE = "CAMERA_NOT_AVAILABLE"
    CAMERA_BACKEND_UNSUPPORTED = "CAMERA_BACKEND_UNSUPPORTED"
    FOCUS_CONTROL_UNAVAILABLE = "FOCUS_CONTROL_UNAVAILABLE"
    PIPELINE_START_FAILED = "PIPELINE_START_FAILED"
    RECORDING_ALREADY_ACTIVE = "RECORDING_ALREADY_ACTIVE"
    RECORDING_NOT_ACTIVE = "RECORDING_NOT_ACTIVE"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"

    # Faz 4: depolama ve video metadata hataları
    STORAGE_NOT_MOUNTED = "STORAGE_NOT_MOUNTED"
    STORAGE_UUID_MISMATCH = "STORAGE_UUID_MISMATCH"
    STORAGE_WRITE_TEST_FAILED = "STORAGE_WRITE_TEST_FAILED"
    STORAGE_INSUFFICIENT_SPACE = "STORAGE_INSUFFICIENT_SPACE"
    RECORDING_FILE_NOT_FOUND = "RECORDING_FILE_NOT_FOUND"
    RECORDING_DELETE_FORBIDDEN = "RECORDING_DELETE_FORBIDDEN"

    # Faz 5: kimlik doğrulama ve ayarlar hataları
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    RATE_LIMITED = "RATE_LIMITED"
    PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
    CSRF_TOKEN_INVALID = "CSRF_TOKEN_INVALID"
    SETTINGS_LOCKED = "SETTINGS_LOCKED"
    PATH_TRAVERSAL_REJECTED = "PATH_TRAVERSAL_REJECTED"
    CAMERA_SERVICE_BUSY = "CAMERA_SERVICE_BUSY"

    # Canlı önizleme + odak kontrolü hataları
    PREVIEW_ALREADY_ACTIVE = "PREVIEW_ALREADY_ACTIVE"
    PREVIEW_NOT_ACTIVE = "PREVIEW_NOT_ACTIVE"
    PREVIEW_UNAVAILABLE_WHILE_RECORDING = "PREVIEW_UNAVAILABLE_WHILE_RECORDING"
    FOCUS_REQUIRES_ACTIVE_SESSION = "FOCUS_REQUIRES_ACTIVE_SESSION"


class AppError(Exception):
    """API katmanının HTTP hata yanıtına çevirdiği taban istisna sınıfı."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
