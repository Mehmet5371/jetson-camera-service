"""Sürekli otomatik odak denetleyicisi (kullanıcı butonuna basmadan çalışır).

NEDEN "sürekli süpürme" DEĞİL: lens'i durmadan taramak (Arducam örneğindeki
tek seferlik süpürmeyi bir döngüye almak) yanlış olurdu - her süpürme
sırasında görüntü fiziksel olarak bulanıklaşıp netleşir (bu, kayda da aynen
geçer), VCM'e ve I2C'ye sürekli yazılır, CPU boşa harcanır. Bunun yerine bu
modül "sürekli ÖLÇER, gerektiğinde ODAKLAR":

1. Canlı MJPEG akışının (önizleme VEYA kayıt tee dalı - hangisi aktifse) en
   son karesinden `sample_interval_seconds`'de bir keskinlik ölçülür
   (`measure_sharpness`: merkez ROI'de Laplacian varyansı - süpürmenin
   kullandığı ÖLÇÜTÜN AYNISI, bu yüzden değerler karşılaştırılabilir).
2. Ölçüm, referans keskinliğin `trigger_ratio` katının altına `consecutive_drops`
   kez ÜST ÜSTE düşerse odak kaçmış sayılır. Tek bir düşük ölçüm tetiklemez:
   keskinlik yalnızca odağa değil SAHNE İÇERİĞİNE de bağlıdır (kontrast
   tabanlı AF'nin evrensel sınırlaması), tek kare üzerinden karar vermek
   sahne değişimini odak kayması sanmaya yol açar. Referans, ölçümlerin
   EMA'sıdır (en yüksek değer DEĞİL): gerçek donanımda aynı pozisyonda
   keskinliğin mutlak değerinin zamanla kaydığı ölçüldü, "gördüğüm en
   yüksek değer" referansı bir gürültü tepesine kilitlenip sonsuz yanlış
   tetiklemeye yol açıyor.
3. Tetiklenince ÖNCE mevcut pozisyonun çevresinde yerel arama yapılır (hızlı,
   lens az hareket eder). Tam süpürmeye yalnızca en iyi pozisyon yerel
   PENCERENİN KENARINDA çıkarsa yükseltilir - yani gerçek tepe pencerenin
   dışındadır. (Mutlak keskinlik eşiğiyle karar vermek yanlıştı: değerler
   zamanla kaydığı için gereksiz tam süpürmeler tetikliyordu.)
4. Sonuç KABUL edilmeden önce ISP'nin oturması beklenip yeniden ölçülür;
   odaklama görüntüyü belirgin şekilde KÖTÜLEŞTİRDİYSE lens eski pozisyonuna
   geri alınır. `cooldown_seconds` iki odaklama arasında en kısa süreyi
   zorlar (lens'in sürekli gidip gelmesini - "hunting" - önler).

Akış her yeniden başladığında (Faz 3 bulgusu: VCM lens'i akış restart'ında
fiziksel olarak dinlenme konumuna döner) durum sıfırlanır; odak daha önce hiç
bulunmamışsa bir kez TAM süpürme yapılır (`initial_sweep`) - kullanıcının
hiçbir butona basmasına gerek kalmadan sistem net görüntüyle başlar.

Bu sınıf AYNI ZAMANDA tüm odak yazmalarının tek kapısıdır: manuel slider,
"Otomatik Odakla" butonu ve bu döngü aynı `asyncio.Lock`'u paylaşır, böylece
iki odaklama işlemi asla I2C üzerinde birbirine girmez.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.camera.autofocus import (
    AutofocusFailedError,
    AutofocusResult,
    local_search_positions,
    measure_sharpness,
    run_autofocus_sweep,
    run_local_search,
)
from app.camera.focus import FocusController
from app.camera.mjpeg import MjpegBroadcaster
from app.core.config import AppConfig
from app.core.errors import AppError

logger = logging.getLogger(__name__)

# Akış yokken boşta bekleme aralığı (ölçülecek bir şey yok, sık uyanmanın
# anlamı da yok).
_IDLE_POLL_INTERVAL_SECONDS = 1.0
# Üst üste bu kadar I2C hatasından sonra sürekli odak KENDİNİ kapatır -
# donanım yoksa/odak motoru yanıt vermiyorsa saniyede bir hata loglayıp
# journal'ı doldurmak yerine bir kez uyarır (kullanıcı dashboard'dan veya
# servisi yeniden başlatarak tekrar açabilir).
_MAX_CONSECUTIVE_I2C_FAILURES = 5
# Doğrulama ölçümü, odaklama öncesinden bu katın ALTINA düşerse odak geri
# alınır. Bilerek düşük (=belirgin kötüleşme) bir eşik: mutlak keskinlik
# değerleri zamanla kaydığı için küçük farklar "kötüleşme" sayılamaz.
_REVERT_MARGIN = 0.6
# Tek karelik gürültü tetiklemesin: son bu kadar ölçümün MEDYANI kullanılır.
_SAMPLE_WINDOW = 3


@dataclass(frozen=True)
class LiveSource:
    """Şu an canlı görüntü üreten kaynak (önizleme veya kayıt tee dalı)."""

    kind: Literal["preview", "recording"]
    broadcaster: MjpegBroadcaster


class AutofocusSupervisor:
    def __init__(
        self,
        config: AppConfig,
        focus_controller: FocusController,
        get_live_source: Callable[[], LiveSource | None],
    ) -> None:
        self._config = config
        self._focus = focus_controller
        self._get_live_source = get_live_source
        # Tüm odak yazmaları (döngü + manuel + buton) bu kilidi paylaşır.
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

        self._runtime_enabled = config.camera.continuous_autofocus.enabled
        self._disabled_reason: str | None = None
        self._suspended_until: float | None = None

        # Akış (oturum) başına sıfırlanan durum
        self._session_token: tuple[str, int] | None = None
        self._initial_done = False
        # Referans keskinlik: ölçümlerin EMA'sı (en yüksek değer DEĞİL - bkz.
        # modül başlığı, gerçek donanımda ölçülen kayma).
        self._reference: float | None = None
        self._recent_samples: list[float] = []
        self._drop_streak = 0
        self._consecutive_i2c_failures = 0
        self._was_suspended = False

        # Akışlar arasında KORUNAN durum: en son otomatik odağın bulduğu
        # pozisyon ve o pozisyondaki keskinlik. Kayıt başlarken (yeni Argus
        # akışı) tam süpürmeyi tekrarlamamak için kullanılır.
        self._known_focus_value: int | None = None
        self._known_sharpness: float | None = None

        self._last_sharpness: float | None = None
        self._last_refocus_at: float | None = None
        self._refocus_count = 0
        self._last_action: dict | None = None

    # --- yaşam döngüsü -----------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def update_config(self, new_config: AppConfig) -> None:
        """Ayarlar dashboard'dan değiştirildiğinde çağrılır (api/settings.py).

        Ayar kaydetmek AÇIK bir kullanıcı eylemi olduğu için çalışma zamanı
        şalteri ve otomatik kapanma sebebi de yeni config'e göre sıfırlanır.
        """
        self._config = new_config
        self._runtime_enabled = new_config.camera.continuous_autofocus.enabled
        self._disabled_reason = None
        self._suspended_until = None

    # --- durum / kontrol ---------------------------------------------------

    @property
    def focus_controller(self) -> FocusController:
        return self._focus

    def live_source(self) -> LiveSource | None:
        return self._get_live_source()

    def set_continuous_enabled(self, enabled: bool) -> None:
        """Çalışma zamanı şalteri (config'e YAZILMAZ - kalıcı varsayılan
        `camera.continuous_autofocus.enabled` ayarındadır)."""
        self._runtime_enabled = enabled
        self._suspended_until = None
        self._disabled_reason = None
        if enabled:
            # Yeniden açılınca durumu "yeni akış" gibi ele al: referansı
            # sıfırdan öğrenmek YETMEZ - o an görüntü bulanıksa bulanık
            # keskinlik referans olur ve sistem sonsuza kadar bulanık kalır
            # (tek karelik ölçümden "bu bulanık" sonucu ÇIKARILAMAZ, kontrast
            # tabanlı AF'nin doğası). Bu yüzden gerekiyorsa yeniden odaklanır.
            self._initial_done = False
            self._reference = None
            self._recent_samples.clear()
            self._drop_streak = 0
            self._consecutive_i2c_failures = 0
        logger.info(
            "continuous_autofocus_toggled",
            extra={
                "component": "continuous_autofocus",
                "operation": "toggle",
                "af_enabled": enabled,
            },
        )

    def _suspend_after_manual(self) -> None:
        seconds = self._config.camera.continuous_autofocus.manual_override_seconds
        self._suspended_until = math.inf if seconds <= 0 else time.monotonic() + seconds

    def _suspend_seconds_left(self) -> float | None:
        if self._suspended_until is None:
            return None
        if self._suspended_until == math.inf:
            return math.inf
        remaining = self._suspended_until - time.monotonic()
        if remaining <= 0:
            self._suspended_until = None
            return None
        return remaining

    @property
    def effective_enabled(self) -> bool:
        """Config + çalışma zamanı şalteri birlikte. `camera.autofocus: false`
        otomatik odağın ANA şalteridir, sürekli odağı da kapatır."""
        camera = self._config.camera
        return camera.autofocus and camera.continuous_autofocus.enabled and self._runtime_enabled

    def status(self) -> dict:
        remaining = self._suspend_seconds_left()
        source = self._get_live_source()
        settings = self._config.camera.continuous_autofocus
        return {
            "enabled": self.effective_enabled,
            "configured_enabled": settings.enabled and self._config.camera.autofocus,
            "runtime_enabled": self._runtime_enabled,
            "during_recording": settings.during_recording,
            "active": self.effective_enabled and remaining is None and self._is_monitored(source),
            "suspended": remaining is not None,
            "suspended_seconds_left": None if remaining is None or remaining == math.inf else round(remaining, 1),
            "disabled_reason": self._disabled_reason,
            "source": None if source is None else source.kind,
            "last_sharpness": None if self._last_sharpness is None else round(self._last_sharpness, 1),
            "baseline_sharpness": None if self._reference is None else round(self._reference, 1),
            "refocus_count": self._refocus_count,
            "last_action": self._last_action,
        }

    def _is_monitored(self, source: LiveSource | None) -> bool:
        if source is None:
            return False
        if source.kind == "recording":
            return self._config.camera.continuous_autofocus.during_recording
        return True

    # --- manuel işlemler (API bunları çağırır) -----------------------------

    async def apply_manual_focus(self, value: int) -> None:
        """Slider'dan gelen manuel odak. Sürekli odağı geçici olarak duraklatır
        (kullanıcının ayarını hemen ezmemek için)."""
        async with self._lock:
            self._focus.set_focus(value)
            self._known_focus_value = value
            self._known_sharpness = None  # referans artık bilinmiyor
            self._reference = None
            self._recent_samples.clear()
            self._drop_streak = 0
            self._consecutive_i2c_failures = 0
            self._suspend_after_manual()

    async def run_manual_sweep(
        self, get_latest_frame: Callable[[], bytes | None]
    ) -> AutofocusResult:
        """"Otomatik Odakla" butonu: hemen TAM süpürme. Kullanıcı açıkça
        otomatik odak istediği için varsa duraklatma da kaldırılır."""
        async with self._lock:
            result = await run_autofocus_sweep(self._focus, get_latest_frame)
        self._suspended_until = None
        self._initial_done = True
        self._remember_result(result.best_position, result.best_sharpness)
        return result

    def _remember_result(self, position: int, sharpness: float) -> None:
        self._known_focus_value = position
        self._known_sharpness = sharpness
        self._reference = sharpness
        self._recent_samples.clear()
        self._drop_streak = 0
        self._last_refocus_at = time.monotonic()
        self._refocus_count += 1

    # --- ölçüm/karar döngüsü ----------------------------------------------

    async def _run_loop(self) -> None:
        while True:
            source = self._get_live_source()
            interval = (
                self._config.camera.continuous_autofocus.sample_interval_seconds
                if source is not None
                else _IDLE_POLL_INTERVAL_SECONDS
            )
            await asyncio.sleep(interval)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - sürekli odak asla servisi düşürmemeli
                logger.exception(
                    "continuous_autofocus_tick_failed",
                    extra={"component": "continuous_autofocus", "operation": "tick"},
                )

    async def _tick(self) -> None:
        source = self._get_live_source()
        if source is None:
            self._reset_session_state(None)
            return

        # Kaynak değiştiyse (önizleme -> kayıt, veya akış yeniden başladı) yeni
        # bir Argus akışı var: lens dinlenme konumuna dönmüş olabilir, ölçüm
        # geçmişi de artık geçersiz.
        token = (source.kind, id(source.broadcaster))
        if token != self._session_token:
            self._reset_session_state(token)

        if not self.effective_enabled or not self._is_monitored(source):
            return

        if self._suspend_seconds_left() is not None:
            self._was_suspended = True
            return
        if self._was_suspended:
            # Duraklatma bitti: kullanıcının bıraktığı pozisyon bulanık olabilir
            # ve tek ölçümden bunu ANLAYAMAYIZ - "yeni akış" gibi yeniden
            # değerlendir (gerekiyorsa odaklan).
            self._was_suspended = False
            self._initial_done = False
            self._reference = None
            self._recent_samples.clear()

        frame = source.broadcaster.get_latest_frame()
        if frame is None:
            return
        sharpness = measure_sharpness(frame)
        if sharpness is None:
            return
        self._last_sharpness = sharpness
        smoothed = self._smoothed_sample(sharpness)

        if not self._initial_done:
            self._initial_done = True
            await self._handle_new_stream(source, smoothed)
            return

        settings = self._config.camera.continuous_autofocus
        if self._reference is None:
            self._reference = smoothed
            return

        if smoothed < self._reference * settings.trigger_ratio:
            self._drop_streak += 1
        else:
            # Yalnızca "normal" seviyedeki ölçümler referansı günceller -
            # böylece referans ışık/ISP kaymasını izler ama gerçek bir odak
            # kaçmasıyla AŞAĞI çekilmez (aksi halde bulanıklığa alışırdı).
            self._drop_streak = 0
            self._reference += settings.reference_smoothing * (smoothed - self._reference)
            return

        if self._drop_streak < settings.consecutive_drops:
            return
        if not self._cooldown_elapsed():
            return

        await self._refocus(source, reason="sharpness_drop", sharpness_before=smoothed)

    def _smoothed_sample(self, sharpness: float) -> float:
        """Son ölçümlerin medyanı - tek karelik gürültü tetiklemesin."""
        self._recent_samples.append(sharpness)
        if len(self._recent_samples) > _SAMPLE_WINDOW:
            self._recent_samples.pop(0)
        ordered = sorted(self._recent_samples)
        return ordered[len(ordered) // 2]

    def _reset_session_state(self, token: tuple[str, int] | None) -> None:
        self._session_token = token
        self._initial_done = False
        self._reference = None
        self._recent_samples.clear()
        self._drop_streak = 0
        self._consecutive_i2c_failures = 0
        self._last_sharpness = None
        self._was_suspended = False

    def _cooldown_elapsed(self) -> bool:
        if self._last_refocus_at is None:
            return True
        cooldown = self._config.camera.continuous_autofocus.cooldown_seconds
        return (time.monotonic() - self._last_refocus_at) >= cooldown

    async def _handle_new_stream(self, source: LiveSource, sharpness: float) -> None:
        """Yeni bir akışın ilk ölçümü: gerekiyorsa bir kez odaklan.

        - Bu süreçte daha önce hiç otomatik odak yapılmamışsa (odak nerede
          bilinmiyor): `initial_sweep` ise TAM süpürme.
        - Yapılmışsa: RecordingManager/PreviewManager o değeri zaten yeniden
          uyguladı; yalnızca gerçekten netse kabul et, keskinlik bilinen
          referansın belirgin altındaysa yeniden odakla.
        """
        settings = self._config.camera.continuous_autofocus
        self._reference = sharpness

        if self._known_sharpness is None:
            if settings.initial_sweep:
                await self._refocus(source, reason="initial", sharpness_before=sharpness)
            return

        if sharpness < self._known_sharpness * settings.trigger_ratio:
            await self._refocus(source, reason="stream_restart", sharpness_before=sharpness)
        else:
            # Yeniden uygulanan odak iyi durumda - referansı bu akışın gerçek
            # ölçümüyle güncelle (aydınlatma/sahne değişmiş olabilir).
            self._known_sharpness = max(self._known_sharpness, sharpness)

    async def _refocus(
        self, source: LiveSource, *, reason: str, sharpness_before: float
    ) -> None:
        """Yerel arama; yetersizse tam süpürmeye yükseltir. I2C hatalarını
        yutar (kayıt/önizleme devam etmeli), üst üste çok hata olursa kendini
        kapatır."""
        settings = self._config.camera.continuous_autofocus
        get_frame = source.broadcaster.get_latest_frame
        center = self._focus.current_value
        started_at = time.monotonic()

        logger.info(
            "continuous_autofocus_refocus_started",
            extra={
                "component": "continuous_autofocus",
                "operation": "refocus",
                "reason": reason,
                "source_kind": source.kind,
                "sharpness": round(sharpness_before, 1),
                "baseline": None if self._reference is None else round(self._reference, 1),
                "focus_value": center,
            },
        )

        escalated = False
        reverted = False
        async with self._lock:
            # Kilidi beklerken akış kapanmış veya kaynak değişmiş olabilir.
            current = self._get_live_source()
            if current is None or id(current.broadcaster) != id(source.broadcaster):
                return
            try:
                if center is not None and reason != "initial":
                    result = await run_local_search(
                        self._focus,
                        get_frame,
                        center=center,
                        step=settings.fine_step,
                        span_steps=settings.fine_range_steps,
                    )
                    # Tam süpürmeye yalnızca tepe pencerenin KENARINDA
                    # çıkarsa yükselt: gerçek tepe pencerenin dışındadır.
                    # (Mutlak keskinlik eşiğiyle karar vermek yanlıştı -
                    # değerler zamanla kayıyor, gereksiz tam süpürme oluyordu.)
                    window = local_search_positions(
                        center, settings.fine_step, settings.fine_range_steps
                    )
                    if result.best_position in (window[0], window[-1]):
                        escalated = True
                        result = await run_autofocus_sweep(self._focus, get_frame)
                else:
                    result = await run_autofocus_sweep(self._focus, get_frame)

                # Sonucu KABUL etmeden önce ISP oturduktan sonra yeniden ölç.
                verified = await self._verify_result(get_frame, settings.verify_settle_seconds)
                if (
                    verified is not None
                    and center is not None
                    and verified < sharpness_before * _REVERT_MARGIN
                ):
                    # Odaklama görüntüyü belirgin şekilde KÖTÜLEŞTİRDİ (gerçek
                    # donanımda 57 -> 6 gözlendi): eski pozisyona geri dön.
                    self._focus.set_focus(center)
                    reverted = True
            except AutofocusFailedError as exc:
                # Kare gelmiyor (akış kapandı/donuk) - bir sonraki tur tekrar
                # denenir, cooldown gereksiz tekrarı zaten sınırlar.
                self._last_refocus_at = time.monotonic()
                self._drop_streak = 0
                logger.warning(
                    "continuous_autofocus_no_frames",
                    extra={
                        "component": "continuous_autofocus",
                        "operation": "refocus",
                        "reason": reason,
                        "error_message": str(exc),
                    },
                )
                return
            except AppError as exc:
                self._handle_i2c_failure(exc)
                return

        self._consecutive_i2c_failures = 0
        final_position = center if reverted and center is not None else result.best_position
        final_sharpness = sharpness_before if reverted else result.best_sharpness
        self._remember_result(final_position, final_sharpness)
        self._last_action = {
            "reason": reason,
            "value": final_position,
            "sharpness": round(final_sharpness, 1),
            "sharpness_before": round(sharpness_before, 1),
            "steps_taken": result.steps_taken,
            "escalated": escalated,
            "reverted": reverted,
            "duration_seconds": round(time.monotonic() - started_at, 2),
        }
        logger.info(
            "continuous_autofocus_reverted" if reverted else "continuous_autofocus_refocused",
            extra={
                "component": "continuous_autofocus",
                "operation": "refocus",
                "reason": reason,
                "focus_value": final_position,
                "sharpness": round(final_sharpness, 1),
                "steps_taken": result.steps_taken,
                "escalated": escalated,
            },
        )

    async def _verify_result(
        self, get_frame: Callable[[], bytes | None], settle_seconds: float
    ) -> float | None:
        """Odaklama sonrası, ISP oturduktan SONRA yeniden ölç.

        Süpürme sırasındaki ölçümler kısa beklemeyle (0.25s) alınır; gerçek
        donanımda büyük bir odak hareketinden sonra keskinliğin ~2 saniye
        boyunca yükselmeye devam ettiği ÖLÇÜLDÜ (Argus ISP adaptasyonu), yani
        süpürme sırasındaki mutlak değerler olduğundan düşük olabilir.
        """
        if settle_seconds > 0:
            await asyncio.sleep(settle_seconds)
        frame = get_frame()
        if frame is None:
            return None
        return measure_sharpness(frame)

    def _handle_i2c_failure(self, exc: AppError) -> None:
        # Akış tam da süpürme sırasında kapandıysa (kullanıcı kaydı/önizlemeyi
        # durdurdu) VCM I2C'si zaten yanıt vermez - bu bir arıza DEĞİL.
        if self._get_live_source() is None:
            self._reset_session_state(None)
            return

        self._consecutive_i2c_failures += 1
        self._last_refocus_at = time.monotonic()
        self._drop_streak = 0
        if self._consecutive_i2c_failures < _MAX_CONSECUTIVE_I2C_FAILURES:
            logger.warning(
                "continuous_autofocus_focus_write_failed",
                extra={
                    "component": "continuous_autofocus",
                    "operation": "refocus",
                    "error_code": exc.code.value,
                    "error_message": exc.message,
                    "failure_count": self._consecutive_i2c_failures,
                },
            )
            return

        self._runtime_enabled = False
        self._disabled_reason = (
            f"Odak motoruna üst üste {self._consecutive_i2c_failures} kez yazılamadı "
            "(I2C yanıt vermiyor). Sürekli odak kendini kapattı."
        )
        logger.error(
            "continuous_autofocus_self_disabled",
            extra={
                "component": "continuous_autofocus",
                "operation": "refocus",
                "error_code": exc.code.value,
                "error_message": exc.message,
                "failure_count": self._consecutive_i2c_failures,
            },
        )
