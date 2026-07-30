"""Sağlık kontrolü endpoint'i.

/api/health kimlik doğrulama gerektirmez (yük dengeleyici / systemd health
check / dashboard bağlantı testi için). Yalnızca servisin ayakta olduğunu ve
yapılandırmanın başarıyla yüklendiğini bildirir; kamera veya kayıt durumu
için /api/status kullanılacaktır (Faz 5).
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health() -> dict:
    return {
        "success": True,
        "data": {
            "status": "ok",
            "service_version": __version__,
        },
    }
