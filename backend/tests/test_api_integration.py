"""Faz 5 API entegrasyon testleri.

TEK bir uygulama boot'u üzerinde SIRALI çalışır (modül-seviyesi tekil
durumlar - `get_config` lru_cache, `.env` yükleme bayrağı, login rate
limiter - birden fazla izole boot'u pratik dışı bırakıyor; bu üretimde de
geçerli bir kısıt: servis zaten tek sefer boot olur). Gerçek kamera
donanımı kullanır (kayıt başlat/durdur, kamera testi) - `pytest -m
"not hardware"` ile atlanabilir.
"""

from __future__ import annotations

import os
import secrets
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.hardware

_TEST_ROOT = Path("/mnt/recordings/jetson-camera-service/backend/tests/.hardware_tmp")
# Parola değiştirme testi bu sözlüğü günceller (module-level global
# reassignment yerine mutable container - testler arası sıralı bağımlılığı
# daha açık şekilde ifade eder).
_ADMIN_STATE = {"password": "TestAdminPassw0rd!"}


@pytest.fixture(scope="module")
def api_client():
    base = _TEST_ROOT / f"api_{uuid.uuid4().hex}"
    video_dir = base / "media" / "videos"
    photo_dir = base / "media" / "photos"
    db_dir = base / "db"
    log_dir = base / "logs"
    for d in (video_dir, photo_dir, db_dir, log_dir):
        d.mkdir(parents=True)

    from app.security.passwords import hash_password

    password_hash = hash_password(_ADMIN_STATE["password"])
    secret_key = secrets.token_urlsafe(32)

    config_path = base / "config.yaml"
    config_path.write_text(
        f"""
camera:
  backend: argus
  sensor_id: 0
  device: /dev/video0
  i2c_bus: 10
  width: 1920
  height: 1080
  fps: 30
  autofocus: false
  manual_focus_value: 250
preview:
  width: 960
  height: 540
  fps: 15
  jpeg_quality: 75
recording:
  codec: h264
  encoder: software
  container: mp4
  bitrate_bps: 4000000
  speed_preset: ultrafast
  segment_duration_minutes: 0
  filename_format: "test_%Y%m%d_%H%M%S"
  minimum_free_space_gb: 1
  low_space_action: stop
capture:
  photo_format: jpg
  photo_quality: 90
storage:
  mount_path: /mnt/recordings
  expected_uuid: ""
  require_mountpoint: true
  media_path: {base}/media
  video_path: {video_dir}
  photo_path: {photo_dir}
  database_path: {db_dir}/app.db
retention:
  enabled: false
  maximum_storage_percent: 90
  delete_oldest_files: false
server:
  host: 127.0.0.1
  port: 8080
security:
  authentication_enabled: true
logging:
  level: INFO
  directory: {log_dir}
  max_bytes: 1000000
  backup_count: 1
"""
    )

    env_path = base / ".env"
    env_path.write_text(
        f"JETSON_CAMERA_SECRET_KEY={secret_key}\n"
        "JETSON_CAMERA_ADMIN_USERNAME=admin\n"
        f"JETSON_CAMERA_ADMIN_PASSWORD_HASH={password_hash}\n"
    )

    os.environ["JETSON_CAMERA_CONFIG"] = str(config_path)
    os.environ["JETSON_CAMERA_ENV_FILE"] = str(env_path)

    # Modül seviyesi tekil durumları bu izole test config'i görecek şekilde sıfırla.
    from app.core.config import get_config

    get_config.cache_clear()
    import app.core.env as env_module

    env_module._loaded = False

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client

    shutil.rmtree(base, ignore_errors=True)


def _login(client: TestClient, password: str | None = None) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password or _ADMIN_STATE["password"]},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = client.cookies["jetson_camera_csrf"]


def test_health_is_public(api_client: TestClient) -> None:
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_dashboard_shell_is_served(api_client: TestClient) -> None:
    response = api_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "recording-state-badge" in response.text

    response = api_client.get("/login")
    assert response.status_code == 200
    assert "login-form" in response.text


def test_static_assets_are_served(api_client: TestClient) -> None:
    response = api_client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]

    response = api_client.get("/static/js/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_protected_endpoint_requires_auth(api_client: TestClient) -> None:
    response = api_client.get("/api/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_wrong_password_rejected(api_client: TestClient) -> None:
    response = api_client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_and_forced_password_change_flow(api_client: TestClient) -> None:
    current_password = _ADMIN_STATE["password"]
    response = api_client.post(
        "/api/auth/login", json={"username": "admin", "password": current_password}
    )
    assert response.status_code == 200
    assert response.json()["data"]["must_change_password"] is True

    csrf = api_client.cookies["jetson_camera_csrf"]
    new_password = "NewStrongPassw0rd!"

    # CSRF header olmadan reddedilmeli.
    response = api_client.post(
        "/api/auth/change-password",
        json={"current_password": current_password, "new_password": new_password},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_TOKEN_INVALID"

    # must_change_password=true iken diğer uç noktalar kilitli olmalı.
    api_client.headers["X-CSRF-Token"] = csrf
    response = api_client.get("/api/status")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    response = api_client.post(
        "/api/auth/change-password",
        json={"current_password": current_password, "new_password": new_password},
    )
    assert response.status_code == 200

    _ADMIN_STATE["password"] = new_password
    api_client.headers["X-CSRF-Token"] = api_client.cookies["jetson_camera_csrf"]

    response = api_client.get("/api/status")
    assert response.status_code == 200


def test_system_and_camera_status(api_client: TestClient) -> None:
    _login(api_client)

    response = api_client.get("/api/system")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["l4t_version"] == "R36.4.4"
    assert data["jetpack_version"] == "6.2.1"

    response = api_client.get("/api/camera/status")
    assert response.status_code == 200
    assert response.json()["data"]["backend"] == "argus"


def test_storage_status(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.get("/api/storage")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_mounted"] is True
    assert data["total_bytes"] > 0


def test_recording_not_found_returns_404(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.get("/api/recordings/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_settings_get_and_put(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["data"]["camera"]["fps"] == 30

    response = api_client.put("/api/settings", json={"retention": {"maximum_storage_percent": 80}})
    assert response.status_code == 200
    assert response.json()["data"]["retention"]["maximum_storage_percent"] == 80


def test_full_recording_lifecycle_via_api(api_client: TestClient) -> None:
    _login(api_client)

    response = api_client.post("/api/recordings/start")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "recording"

    # Aktif kayıt sırasında kamera/kayıt ayarları kilitli olmalı.
    response = api_client.put("/api/settings", json={"camera": {"fps": 15}})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETTINGS_LOCKED"

    # İkinci kayıt reddedilmeli.
    response = api_client.post("/api/recordings/start")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_ALREADY_ACTIVE"

    import time

    time.sleep(4)

    response = api_client.post("/api/recordings/stop")
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "idle"

    response = api_client.get("/api/recordings", params={"page": 1, "page_size": 5})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["status"] == "completed"
    assert items[0]["width"] == 1920

    recording_id = items[0]["id"]

    response = api_client.get(f"/api/recordings/{recording_id}/stream")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"

    response = api_client.get(
        f"/api/recordings/{recording_id}/stream", headers={"Range": "bytes=0-999"}
    )
    assert response.status_code == 206
    assert response.headers["content-range"].startswith("bytes 0-999/")

    response = api_client.get(f"/api/recordings/{recording_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" in response.headers["content-disposition"]
    assert len(response.content) == items[0]["file_size_bytes"]

    response = api_client.delete(f"/api/recordings/{recording_id}")
    assert response.status_code == 200

    response = api_client.get(f"/api/recordings/{recording_id}")
    assert response.status_code == 404


def test_camera_test_endpoint(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.post("/api/camera/test")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["test_capture"]["width"] == 1920
    assert data["test_capture"]["codec"] == "h264"


def test_logout_clears_session(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.post("/api/auth/logout")
    assert response.status_code == 200

    response = api_client.get("/api/status")
    assert response.status_code == 401


def test_recordings_invalid_status_filter_rejected(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.get("/api/recordings", params={"status": "not-a-real-status"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_recordings_invalid_id_type_rejected(api_client: TestClient) -> None:
    _login(api_client)
    response = api_client.get("/api/recordings/not-an-integer")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_delete_active_recording_is_forbidden(api_client: TestClient) -> None:
    _login(api_client)

    from datetime import datetime, timezone

    from sqlmodel import Session

    from app.models.recording import Recording, RecordingStatus

    with Session(api_client.app.state.db_engine) as db_session:
        active = Recording(
            filename="currently_recording.mp4",
            file_path="/mnt/recordings/media/videos/currently_recording.mp4",
            started_at=datetime.now(timezone.utc),
            status=RecordingStatus.RECORDING,
            is_valid=True,
        )
        db_session.add(active)
        db_session.commit()
        db_session.refresh(active)
        recording_id = active.id

    try:
        response = api_client.delete(f"/api/recordings/{recording_id}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "RECORDING_DELETE_FORBIDDEN"
    finally:
        with Session(api_client.app.state.db_engine) as db_session:
            leftover = db_session.get(Recording, recording_id)
            if leftover is not None:
                db_session.delete(leftover)
                db_session.commit()
