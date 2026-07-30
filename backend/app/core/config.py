"""Merkezi yapılandırma sistemi.

Yapılandırma tek kaynaktan (YAML dosyası) okunur ve Pydantic modelleriyle
doğrulanır. Dosya bulunamazsa veya şema dışı bir değer içeriyorsa servis
sessizce varsayılana düşmez; açık ve anlaşılır bir ConfigError fırlatır ve
uygulama başlamaz. Bu bilinçli bir tercihtir: SD karta yanlışlıkla kayıt
yapmak veya yanlış kamera backend'iyle çalışmak, servisin hiç başlamamasından
daha kötü bir sonuçtur.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.errors import AppError, ErrorCode

# Servis systemd altında çalışırken JETSON_CAMERA_CONFIG environment
# değişkeniyle mutlak yol verilir (bkz. systemd/*.service, Faz 7).
# Verilmezse proje kökündeki config/config.yaml aranır (geliştirme ortamı).
_ENV_VAR = "JETSON_CAMERA_CONFIG"
_DEFAULT_RELATIVE_PATH = Path("config/config.yaml")


class ConfigError(AppError):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.CONFIG_INVALID,
            message,
            status_code=500,
            details=details or {},
        )


class ContinuousAutofocusConfig(BaseModel):
    """Sürekli (olay tetiklemeli) otomatik odak ayarları.

    Lens'i durmadan süpürmek YANLIŞ olurdu: her süpürme sırasında görüntü
    fiziksel olarak bulanıklaşıp netleşir (kayda da öyle geçer) ve I2C'ye
    gereksiz yazma yapılır. Bu yüzden mekanizma "sürekli ÖLÇEN, gerektiğinde
    ODAKLAYAN" bir denetleyicidir: keskinlik her `sample_interval_seconds`'de
    ölçülür, referansın `trigger_ratio` katının altına `consecutive_drops`
    kez ÜST ÜSTE düşerse yeniden odaklama tetiklenir (bkz. camera/
    continuous_focus.py).
    """

    enabled: bool = True
    # Kayıt sırasında da çalışsın mı? true: sahne mesafesi değişirse kayıt
    # net kalır (odaklama hareketi kayda da geçer). false: kayıt, önizlemede
    # bulunan odak değeriyle sabit devam eder.
    during_recording: bool = True
    # Keskinlik ölçüm sıklığı. Ölçüm ucuz (tek JPEG çözme + merkez ROI'de
    # Laplacian varyansı) ama bedava değil - 0.5s makul bir dengedir.
    sample_interval_seconds: float = Field(default=0.5, gt=0.05, le=30.0)
    # Referans keskinliğin bu katının ALTI "odak kaçtı" sayılır.
    trigger_ratio: float = Field(default=0.7, gt=0.05, lt=1.0)
    # Referans, ölçümlerin üstel hareketli ortalamasıdır (EMA); bu katsayı
    # ne kadar hızlı uyum sağladığını belirler. GERÇEK DONANIMDA ÖLÇÜLDÜ:
    # aynı pozisyonda keskinliğin MUTLAK değeri zamanla kayıyor (Argus
    # ISP'nin pozlama/gürültü azaltma adaptasyonu + ışık değişimi; aynı
    # sahnede dakikalar arayla 21 ile 68 arası okundu). Bu yüzden referans
    # olarak "görülen en yüksek değer" TUTULAMAZ - tek bir gürültü tepesi
    # referansı kalıcı olarak şişirip sonsuz yanlış tetiklemeye yol açar.
    reference_smoothing: float = Field(default=0.1, gt=0.0, le=1.0)
    # Tek bir düşük ölçüm tetiklemez (sahne içeriği de keskinliği değiştirir);
    # üst üste bu kadar düşük ölçüm gerekir.
    consecutive_drops: int = Field(default=3, ge=1, le=50)
    # İki yeniden odaklama arasındaki en kısa süre (lens'in sürekli
    # gidip gelmesini/"hunting" önler).
    cooldown_seconds: float = Field(default=6.0, ge=0.0, le=3600.0)
    # Yeniden odaklama ÖNCE mevcut pozisyonun çevresinde yerel arama yapar
    # (hızlı, lens az hareket eder): ±(fine_step * fine_range_steps).
    fine_step: int = Field(default=25, ge=1, le=500)
    fine_range_steps: int = Field(default=4, ge=1, le=40)
    # Odaklama sonrası doğrulama ölçümü için bekleme. Süpürme sırasında her
    # pozisyon kısa bekleme (0.25s) ile ölçülür; sonuç KABUL edilmeden önce
    # ISP'nin oturması beklenip yeniden ölçülür - ölçüm gerçekten kötüyse
    # odak eski pozisyonuna geri alınır (gerçek donanımda bir süpürmenin
    # sahneyi 57'den 6'ya düşüren bir pozisyonda bıraktığı gözlendi).
    verify_settle_seconds: float = Field(default=1.0, ge=0.0, le=10.0)
    # Akış (Argus) ilk kez başladığında bir kez tam süpürme yap - kullanıcının
    # hiçbir butona basmasına gerek kalmadan sistem net görüntüyle başlar.
    initial_sweep: bool = True
    # Manuel odak (slider) sonrası sürekli odağı bu kadar saniye duraklat -
    # kullanıcının ayarını hemen ezmemek için. 0 = kullanıcı tekrar açana
    # kadar duraklat (süresiz).
    manual_override_seconds: float = Field(default=30.0, ge=0.0, le=86400.0)


class CameraConfig(BaseModel):
    # "mock": gerçek donanım YOK - GStreamer'ın videotestsrc'i ile sentetik
    # görüntü üretir. YALNIZCA testler için (bkz. şartname böl. 21: "kamera
    # olmadan test çalıştırılabilmesi için mock kamera backend"). Üretim
    # config.example.yaml'da ASLA varsayılan değer değildir ve "auto" bunu
    # otomatik seçmez - yalnızca açıkça "mock" yazılırsa devreye girer.
    backend: Literal["auto", "argus", "v4l2", "mock"] = "auto"
    sensor_id: int = Field(ge=0)
    device: str
    # Arducam VCM odak motoru bu I2C bus'ta yaşıyor (adres 0x0c, sensörün
    # kendisinden -0x1a- ayrı bir cihaz). Bu Jetson'da CAM0 için doğrulanan
    # değer 10'dur (bkz. Arducam Jetson_IMX519_Focus_Example/README.md).
    i2c_bus: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    # Otomatik odağın ana şalteri: false ise ne akış başında otomatik
    # odaklama ne de sürekli odak çalışır, yalnızca manual_focus_value
    # (ve kullanıcının slider'ı) geçerlidir.
    autofocus: bool = True
    # Arducam Focuser.py'deki OPT_FOCUS aralığıyla birebir aynı: 0-1000.
    # 1023 veya 4095 DEĞİL - bu üst katmandaki mantıksal değer, gerçek
    # 12-bit DAC dönüşümü focus.py içinde yapılır.
    manual_focus_value: int = Field(ge=0, le=1000)
    continuous_autofocus: ContinuousAutofocusConfig = ContinuousAutofocusConfig()


class PreviewConfig(BaseModel):
    # Kayıt çözünürlüğünden BİLEREK düşük - CPU/bant genişliği tasarrufu
    # (önizleme yalnızca odak/çerçeveleme için, kalite kritik değil).
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    jpeg_quality: int = Field(ge=1, le=100)


class RecordingConfig(BaseModel):
    codec: Literal["h264", "h265"] = "h264"
    # Bu donanımda hw encoder yok; "software" dışındaki değerler şu an
    # desteklenmiyor. Alan yine de config'de tutulur ki gelecekte bir
    # Orin NX/AGX Orin'e taşınırsa "hardware" seçeneği eklenebilsin.
    encoder: Literal["software"] = "software"
    container: Literal["mp4"] = "mp4"
    bitrate_bps: int = Field(gt=0)
    speed_preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium"
    ] = "ultrafast"
    segment_duration_minutes: int = Field(ge=0)
    filename_format: str
    minimum_free_space_gb: float = Field(gt=0)
    low_space_action: Literal["stop", "delete_oldest", "warn_only"] = "stop"


class CaptureConfig(BaseModel):
    photo_format: Literal["jpg", "png"] = "jpg"
    photo_quality: int = Field(ge=1, le=100)


class StorageConfig(BaseModel):
    mount_path: str
    expected_uuid: str = ""
    require_mountpoint: bool = True
    media_path: str
    video_path: str
    photo_path: str
    database_path: str

    @field_validator(
        "mount_path", "media_path", "video_path", "photo_path", "database_path"
    )
    @classmethod
    def _must_be_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"path must be absolute, got: {value}")
        return value


class RetentionConfig(BaseModel):
    enabled: bool = False
    maximum_storage_percent: float = Field(gt=0, le=100)
    delete_oldest_files: bool = False


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(gt=0, le=65535)


class SecurityConfig(BaseModel):
    authentication_enabled: bool = True


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    directory: str
    max_bytes: int = Field(gt=0)
    backup_count: int = Field(ge=0)


class AppConfig(BaseModel):
    camera: CameraConfig
    preview: PreviewConfig
    recording: RecordingConfig
    capture: CaptureConfig
    storage: StorageConfig
    retention: RetentionConfig
    server: ServerConfig
    security: SecurityConfig
    logging: LoggingConfig


def _resolve_config_path(explicit_path: str | Path | None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)
    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        return Path(env_value)
    return _DEFAULT_RELATIVE_PATH


def load_config(path: str | Path | None = None) -> AppConfig:
    """YAML dosyasından yapılandırmayı okur ve doğrular.

    Hata durumunda ConfigError fırlatır (AppError alt sınıfı); bu istisna
    silinip varsayılana düşülmez, çağıran (main.py) bunu üst katmana
    yansıtıp uygulamanın başlamasını engellemelidir.
    """
    config_path = _resolve_config_path(path)

    if not config_path.is_file():
        raise ConfigError(
            f"Yapılandırma dosyası bulunamadı: {config_path}. "
            f"'{_ENV_VAR}' ortam değişkenini veya config/config.yaml "
            "dosyasının varlığını kontrol edin.",
            details={"path": str(config_path)},
        )

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Yapılandırma dosyası okunamadı: {config_path} ({exc})",
            details={"path": str(config_path)},
        ) from exc

    try:
        raw_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Yapılandırma dosyası geçerli YAML değil: {config_path} ({exc})",
            details={"path": str(config_path)},
        ) from exc

    if not isinstance(raw_data, dict):
        raise ConfigError(
            f"Yapılandırma dosyasının kökü bir eşleme (mapping) olmalı: {config_path}",
            details={"path": str(config_path)},
        )

    try:
        return AppConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigError(
            f"Yapılandırma doğrulanamadı: {config_path}",
            details={"path": str(config_path), "errors": exc.errors()},
        ) from exc


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """FastAPI dependency injection için önbelleğe alınmış yapılandırma.

    Süreç ömrü boyunca tek sefer okunur. Ayarlar dashboard'dan değiştirildiğinde
    (bkz. api/settings.py) bu önbellek `get_config.cache_clear()` ile açıkça
    temizlenip yeniden yüklenmelidir.
    """
    return load_config()


def save_config(config: AppConfig, path: str | Path | None = None) -> None:
    """Doğrulanmış bir AppConfig'i YAML olarak diske yazar.

    Çağıran, kaydetmeden ÖNCE AppConfig.model_validate ile doğrulamış
    olmalıdır (bu fonksiyon zaten doğrulanmış bir nesne alır, ham dict
    almaz - şema dışı bir değerin diske yazılması mümkün değildir).
    """
    config_path = _resolve_config_path(path)
    data = config.model_dump(mode="json")
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
