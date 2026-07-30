"""FastAPI uygulama giriş noktası.

Uvicorn bu modülü `app.main:app` olarak çalıştırır (bkz. systemd/*.service,
Faz 7). Yapılandırma başlangıçta bir kez okunur; geçersizse uygulama hiç
ayağa kalkmaz ve hata journald/console'a açıkça yazılır.

Lifespan sırasıyla: config yükle -> loglama kur -> .env yükle -> Alembic
migration'larını çalıştır -> DB engine kur -> varsayılan admin kullanıcıyı
tohumla -> RecordingManager kur + önceki çalıştırmadan kalan kilit
dosyasını kontrol et -> periyodik bakım görevini (process health-check +
reconciliation + retention) başlat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.camera import router as camera_router
from app.api.health import router as health_router
from app.api.logs import router as logs_router
from app.api.recordings import router as recordings_router
from app.api.service import router as service_router
from app.api.settings import router as settings_router
from app.api.status import router as status_router
from app.api.storage import router as storage_router
from app.camera.continuous_focus import AutofocusSupervisor, LiveSource
from app.camera.focus import FocusController
from app.camera.preview import PreviewManager
from app.core.config import AppConfig, get_config
from app.core.db import create_db_engine
from app.core.env import ensure_env_loaded
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.recording.manager import RecordingManager
from app.recording.state import RecordingState
from app.services.user_seed import seed_default_admin
from app.storage.reconciler import reconcile_recordings
from app.storage.retention import enforce_retention
from app.storage.validator import get_disk_usage

logger = logging.getLogger(__name__)

_HEALTH_CHECK_INTERVAL_SECONDS = 30.0
_RECONCILE_INTERVAL_SECONDS = 300.0
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


def _run_migrations() -> None:
    migrations_dir = _PROJECT_ROOT / "migrations"
    alembic_cfg = AlembicConfig(str(migrations_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    # env.py bu bayrağı görünce fileConfig() çağırıp kök logger'ı ezmiyor
    # (bkz. migrations/env.py) - servisin kendi JSON loglaması korunur.
    alembic_cfg.attributes["skip_logging_config"] = True
    alembic_command.upgrade(alembic_cfg, "head")


async def _periodic_maintenance_loop(app: FastAPI) -> None:
    manager: RecordingManager = app.state.recording_manager
    elapsed_since_reconcile = 0.0

    while True:
        await asyncio.sleep(_HEALTH_CHECK_INTERVAL_SECONDS)

        try:
            await manager.check_and_recover_process_health()
        except Exception:  # noqa: BLE001 - bakım görevi asla servisi düşürmemeli
            logger.exception(
                "health_check_failed", extra={"component": "main", "operation": "maintenance"}
            )

        elapsed_since_reconcile += _HEALTH_CHECK_INTERVAL_SECONDS
        if elapsed_since_reconcile < _RECONCILE_INTERVAL_SECONDS:
            continue
        elapsed_since_reconcile = 0.0

        if manager.session.state != RecordingState.IDLE:
            continue
        try:
            config: AppConfig = app.state.config
            with Session(app.state.db_engine) as session:
                reconcile_recordings(session, Path(config.storage.video_path))
                usage = get_disk_usage(config.storage.mount_path)
                enforce_retention(session, config, usage)
        except Exception:  # noqa: BLE001
            logger.exception(
                "periodic_reconcile_failed", extra={"component": "main", "operation": "maintenance"}
            )


def resolve_live_source(
    preview_manager: PreviewManager, recording_manager: RecordingManager
) -> LiveSource | None:
    """Şu an canlı MJPEG üreten kaynağı döner (kayıt tee dalı > önizleme).

    Argus tek-oturumlu olduğu için aynı anda yalnızca biri aktif olabilir.
    Hem `/api/camera/live/stream` hem sürekli odak denetleyicisi (kare
    kaynağı olarak) AYNI çözümlemeyi kullanır - tek doğru kaynak burasıdır.
    """
    if recording_manager.is_recording:
        return LiveSource(kind="recording", broadcaster=recording_manager.broadcaster)
    if preview_manager.is_active:
        return LiveSource(kind="preview", broadcaster=preview_manager.broadcaster)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # get_config() burada ilk kez çağrılır; ConfigError fırlatırsa uvicorn
    # başlatma sürecini durdurur ve hatayı stderr'e yazar (henüz JSON log
    # kurulmadığı için bu ilk hata düz metin olabilir, bu kasıtlıdır).
    config = get_config()
    configure_logging(config.logging)
    ensure_env_loaded()

    logger.info(
        "service_starting",
        extra={"component": "main", "operation": "startup", "port": config.server.port},
    )

    _run_migrations()
    db_engine = create_db_engine(config)
    with Session(db_engine) as session:
        seed_default_admin(session)

    # Tek FocusController hem kayıt hem önizleme tarafından PAYLAŞILIR -
    # önizlemede bulunan odak değeri (current_value) kayıt başlarken
    # korunup yeniden uygulanabilsin diye (bkz. camera/focus.py::reapply).
    focus_controller = FocusController(config.camera.i2c_bus)
    recording_manager = RecordingManager(config, db_engine, focus_controller)
    recording_manager.recover_from_previous_run()
    preview_manager = PreviewManager(config, recording_manager, focus_controller)

    # Sürekli otomatik odak: canlı akış (önizleme VEYA kayıt) varken keskinliği
    # ölçüp odak kaçtığında kendiliğinden yeniden odaklar - kullanıcının
    # "Otomatik Odakla" butonuna basmasına gerek kalmaz. Aynı zamanda tüm odak
    # yazmalarının tek kapısıdır (manuel/otomatik işlemler bir kilidi paylaşır).
    autofocus_supervisor = AutofocusSupervisor(
        config,
        focus_controller,
        lambda: resolve_live_source(preview_manager, recording_manager),
    )

    app.state.config = config
    app.state.db_engine = db_engine
    app.state.focus_controller = focus_controller
    app.state.recording_manager = recording_manager
    app.state.preview_manager = preview_manager
    app.state.autofocus_supervisor = autofocus_supervisor

    autofocus_supervisor.start()
    maintenance_task = asyncio.create_task(_periodic_maintenance_loop(app))

    logger.info("service_started", extra={"component": "main", "operation": "startup"})
    yield

    maintenance_task.cancel()
    try:
        await maintenance_task
    except asyncio.CancelledError:
        pass
    await autofocus_supervisor.stop()

    if preview_manager.is_active:
        await preview_manager.stop()
    await recording_manager.shutdown()
    logger.info("service_stopping", extra={"component": "main", "operation": "shutdown"})


app = FastAPI(
    title="Jetson Camera Data Collection Service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.error(
        "request_failed",
        extra={
            "component": "main",
            "operation": "exception_handler",
            "error_code": exc.code.value,
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


_HTTP_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    422: ErrorCode.VALIDATION_ERROR,
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # FastAPI/Starlette 404, 401, 405 vb. durumlarda kendi HTTPException'ını
    # fırlatır; bunlar AppError olmadığı için app_error_handler'a düşmez.
    # API sözleşmesi TÜM hataların aynı {"success": false, "error": {...}}
    # formatında dönmesini şart koşuyor, bu yüzden burada da eşleniyor.
    code = _HTTP_STATUS_TO_ERROR_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    message = exc.detail if isinstance(exc.detail, str) else "İstek işlenemedi."
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {"code": code.value, "message": message, "details": {}},
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "İstek gövdesi doğrulanamadı.",
                "details": {"errors": jsonable_encoder(exc.errors())},
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_exception",
        extra={"component": "main", "operation": "exception_handler", "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "Beklenmeyen bir sunucu hatası oluştu.",
                "details": {},
            },
        },
    )


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/login"):
        # Statik dosyalar (build adımı/cache-busting hash'i olmayan sade
        # HTML/CSS/JS, bkz. Faz 6) için Cache-Control YOK varsayılan olarak
        # - bu, tarayıcının Last-Modified/ETag'e rağmen eski bir sürümü
        # sessizce yeniden kullanmasına yol açabiliyor (gerçek kullanıcı
        # testinde gözlemlendi: dashboard.js güncellenmiş ama tarayıcı
        # eski kopyayı kullanmaya devam etmişti). "no-cache" tarayıcıyı her
        # istekte sunucuya SORMAYA zorlar (ETag eşleşirse ucuz bir 304
        # döner) - kör önbellek kullanımını engeller.
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(health_router, prefix="/api")
app.include_router(auth_router)
app.include_router(status_router)
app.include_router(camera_router)
app.include_router(recordings_router)
app.include_router(storage_router)
app.include_router(settings_router)
app.include_router(logs_router)
app.include_router(service_router)


# --- Dashboard (Faz 6): sade HTML/CSS/JS, ayrı bir build adımı yok ---
# index.html/login.html sunucu tarafında auth kontrolü yapmaz (herkese
# açık statik dosyalardır); auth kontrolü tarayıcıda çalışan JS'in
# /api/auth/me çağrısıyla yapılır (bkz. frontend/static/js/app.js).
app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR / "static")), name="static")


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "index.html")


@app.get("/login", include_in_schema=False)
async def serve_login() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "login.html")
