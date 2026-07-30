"""Yapılandırılmış (JSON) log sistemi.

Tüm loglar journald (systemd altında çalışırken stdout üzerinden) ve ayrıca
döngüsel bir dosyaya JSON satırları olarak yazılır. Parolalar, token'lar veya
diğer gizli bilgiler asla loglanmamalıdır; bu modül bunu garanti edemez,
çağıran kod hassas alanları log çağrılarına eklememelidir.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from app.core.config import LoggingConfig

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)
# Not: stdlib logging, extra={} icinde bu isimlerden biri gecerse (orn. "module",
# "filename", "name") makeRecord() asamasinda KeyError firlatir - JsonFormatter'a
# hic ulasmadan. Bu yuzden cagiran kod extra alanlarinda "component" gibi
# rezerve olmayan isimler kullanmali, "module" KULLANMAMALI.


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.directory)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter()

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "service.log",
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(config.level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # uvicorn kendi logger'larını kurar; bizim handler/format'ımızı kullansınlar
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
