"""Login deneme hız sınırlama - süreç içi bellekte (in-memory) kayan pencere.

Tek worker/tek process varsayımı (bkz. RecordingManager docstring'i) burada
da geçerlidir; bu sınırlayıcı process'ler arası paylaşılmaz. Bu servis
tanımı gereği tek instance/tek worker çalıştığı için bu yeterlidir.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock

from app.core.errors import AppError, ErrorCode

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300  # 5 dakikada en fazla 5 başarısız deneme


class RateLimitedError(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            f"Çok fazla başarısız giriş denemesi. {retry_after_seconds} saniye sonra tekrar deneyin.",
            status_code=429,
            details={"retry_after_seconds": retry_after_seconds},
        )


@dataclass
class _AttemptLog:
    timestamps: list[float] = field(default_factory=list)


class LoginRateLimiter:
    def __init__(self, max_attempts: int = _MAX_ATTEMPTS, window_seconds: int = _WINDOW_SECONDS) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, _AttemptLog] = defaultdict(_AttemptLog)
        self._lock = Lock()

    def check(self, key: str) -> None:
        """Yeni bir deneme yapılabilir mi kontrol eder; limit aşıldıysa RateLimitedError fırlatır."""
        now = time.monotonic()
        with self._lock:
            log = self._attempts[key]
            log.timestamps = [t for t in log.timestamps if now - t < self._window_seconds]
            if len(log.timestamps) >= self._max_attempts:
                oldest = min(log.timestamps)
                retry_after = int(self._window_seconds - (now - oldest)) + 1
                raise RateLimitedError(retry_after)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._attempts[key].timestamps.append(now)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
