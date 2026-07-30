"""Sürekli otomatik odak testleri - donanım/I2C gerektirmez.

Burada SAHTE bir "lens" simüle edilir: keskinlik, odak pozisyonunun gerçek
(bilinen) tepe noktasına uzaklığıyla azalır ve kareler GERÇEK JPEG'lerdir -
yani `measure_sharpness` (cv2 + Laplacian) gerçekten çalışır. Böylece test
edilen şey yalnızca karar mantığı değil, ölçüm→karar→odaklama zincirinin
tamamı olur.

Testin doğruladığı asıl davranış: kullanıcı HİÇBİR butona basmadan
(a) akış başlayınca odak bulunur, (b) sahne değişip odak kaçarsa
kendiliğinden düzeltilir, (c) her şey yolundayken lens rahat bırakılır
(gereksiz süpürme YOK - kayda bulanıklık basmamak için kritik).
"""

from __future__ import annotations

import asyncio

import cv2
import numpy as np
import pytest

from app.camera.autofocus import AutofocusResult, local_search_positions, measure_sharpness
from app.camera.continuous_focus import AutofocusSupervisor, LiveSource
from app.camera.focus import FocuserError
from app.camera.mjpeg import MjpegBroadcaster
from app.core.config import (
    AppConfig,
    CameraConfig,
    CaptureConfig,
    ContinuousAutofocusConfig,
    LoggingConfig,
    PreviewConfig,
    RecordingConfig,
    RetentionConfig,
    SecurityConfig,
    ServerConfig,
    StorageConfig,
)

_POLL_SECONDS = 0.02


def _config(**continuous_overrides) -> AppConfig:
    continuous = dict(
        enabled=True,
        during_recording=True,
        sample_interval_seconds=0.06,
        trigger_ratio=0.7,
        reference_smoothing=0.1,
        consecutive_drops=3,
        cooldown_seconds=0.0,
        fine_step=25,
        fine_range_steps=4,
        # Testlerde doğrulama beklemesi 0: sahte lens anında "oturur"
        # (gerçek donanımda ISP adaptasyonu için 1.0s kullanılıyor).
        verify_settle_seconds=0.0,
        initial_sweep=True,
        manual_override_seconds=30.0,
    )
    continuous.update(continuous_overrides)
    return AppConfig(
        camera=CameraConfig(
            backend="mock", sensor_id=0, device="/dev/video0", i2c_bus=10,
            width=1920, height=1080, fps=30, autofocus=True, manual_focus_value=250,
            continuous_autofocus=ContinuousAutofocusConfig(**continuous),
        ),
        preview=PreviewConfig(width=960, height=540, fps=15, jpeg_quality=75),
        recording=RecordingConfig(
            codec="h264", encoder="software", container="mp4", bitrate_bps=8_000_000,
            speed_preset="ultrafast", segment_duration_minutes=30,
            filename_format="%Y-%m-%d_%H-%M-%S", minimum_free_space_gb=5,
            low_space_action="stop",
        ),
        capture=CaptureConfig(photo_format="jpg", photo_quality=90),
        storage=StorageConfig(
            mount_path="/mnt/recordings", expected_uuid="", require_mountpoint=True,
            media_path="/mnt/recordings/media", video_path="/mnt/recordings/media/videos",
            photo_path="/mnt/recordings/media/photos", database_path="/mnt/recordings/db/app.db",
        ),
        retention=RetentionConfig(enabled=False, maximum_storage_percent=90, delete_oldest_files=False),
        server=ServerConfig(host="0.0.0.0", port=8080),
        security=SecurityConfig(authentication_enabled=True),
        logging=LoggingConfig(level="INFO", directory="/tmp/logs", max_bytes=1_000_000, backup_count=1),
    )


class _FakeFocusController:
    """Gerçek I2C yazmadan odak pozisyonunu tutar (FocusController arayüzü)."""

    def __init__(self, start_value: int = 250, fail: bool = False) -> None:
        self.current_value: int | None = start_value
        self.calls: list[int] = []
        self.fail = fail

    def set_focus(self, value: int) -> None:
        if self.fail:
            raise FocuserError("I2C yazma başarısız (test).")
        self.calls.append(value)
        self.current_value = value

    def initialize(self) -> None:  # pragma: no cover - arayüz tamlığı için
        pass

    def mark_stream_restarted(self) -> None:  # pragma: no cover
        pass

    def reapply(self, default_value: int) -> None:  # pragma: no cover
        self.set_focus(self.current_value if self.current_value is not None else default_value)


def _checkerboard() -> np.ndarray:
    image = np.zeros((200, 200), dtype=np.uint8)
    image[::2, ::2] = 255
    image[1::2, 1::2] = 255
    return image


class _SimulatedLensBroadcaster(MjpegBroadcaster):
    """Odak pozisyonuna göre bulanıklığı değişen GERÇEK JPEG kareleri üretir.

    `true_focus`, lensin gerçekten net olduğu pozisyon; ondan uzaklaştıkça
    kare Gauss bulanıklığıyla bozulur - gerçek bir kameranın odak eğrisinin
    (tek tepe noktalı) sadeleştirilmiş hali.
    """

    def __init__(self, controller: _FakeFocusController, true_focus: int) -> None:
        super().__init__()
        self._controller = controller
        self.true_focus = true_focus
        self._base = _checkerboard()
        self._cache: dict[int, bytes] = {}

    def get_latest_frame(self) -> bytes | None:
        position = self._controller.current_value
        if position is None:
            return None
        key = (position, self.true_focus)
        cached = self._cache.get(hash(key))
        if cached is not None:
            return cached
        distance = abs(position - self.true_focus)
        if distance <= 25:
            image = self._base
        else:
            sigma = distance / 60.0
            kernel = int(sigma * 4) | 1  # tek sayı olmalı
            image = cv2.GaussianBlur(self._base, (kernel, kernel), sigma)
        ok, buffer = cv2.imencode(".jpg", image)
        assert ok
        frame = buffer.tobytes()
        self._cache[hash(key)] = frame
        return frame


def _supervisor(
    config: AppConfig,
    controller: _FakeFocusController,
    broadcaster: MjpegBroadcaster | None,
    kind: str = "preview",
) -> AutofocusSupervisor:
    def get_live_source() -> LiveSource | None:
        if broadcaster is None:
            return None
        return LiveSource(kind=kind, broadcaster=broadcaster)  # type: ignore[arg-type]

    return AutofocusSupervisor(config, controller, get_live_source)  # type: ignore[arg-type]


async def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """Koşul sağlanana kadar bekler (sabit sleep yerine gerçek sinyal bekleme)."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(_POLL_SECONDS)
        elapsed += _POLL_SECONDS
    return predicate()


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gerçek VCM'in hareket etmesini beklemek gerekmiyor (sahte lens anında
    # "hareket ediyor") - süpürmeleri test hızında çalıştır.
    monkeypatch.setattr("app.camera.autofocus._SETTLE_SECONDS", 0.0)


@pytest.mark.asyncio
async def test_focuses_at_stream_start_without_any_user_action() -> None:
    """Asıl gereksinim: "odakla" butonuna basmadan sistem kendisi odaklanmalı."""
    controller = _FakeFocusController(start_value=250)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(), controller, broadcaster)

    supervisor.start()
    try:
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 1)
    finally:
        await supervisor.stop()

    assert controller.current_value is not None
    assert abs(controller.current_value - 700) <= 50
    status = supervisor.status()
    assert status["enabled"] is True
    assert status["last_action"]["reason"] == "initial"


@pytest.mark.asyncio
async def test_refocuses_by_itself_when_scene_distance_changes() -> None:
    controller = _FakeFocusController(start_value=250)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(), controller, broadcaster)

    supervisor.start()
    try:
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 1)
        # Sahne/mesafe değişti: net odak artık çok farklı bir pozisyonda.
        broadcaster.true_focus = 200
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 2)
    finally:
        await supervisor.stop()

    assert controller.current_value is not None
    assert abs(controller.current_value - 200) <= 50
    assert supervisor.status()["last_action"]["reason"] == "sharpness_drop"


@pytest.mark.asyncio
async def test_lens_is_left_alone_while_image_stays_sharp() -> None:
    """Odak yerindeyken süpürme YAPILMAMALI - aksi halde kayda periyodik
    bulanıklık basar (bu, "sürekli süpürme" tasarımının reddedilme sebebi)."""
    controller = _FakeFocusController(start_value=700)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(initial_sweep=False), controller, broadcaster)

    supervisor.start()
    try:
        # Birkaç örnekleme turu boyunca hiç odaklama tetiklenmemeli.
        await asyncio.sleep(0.06 * 8)
        assert supervisor.status()["refocus_count"] == 0
        assert controller.calls == []
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_manual_focus_suspends_continuous_autofocus() -> None:
    controller = _FakeFocusController(start_value=700)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(initial_sweep=False), controller, broadcaster)

    # Kullanıcı bilerek bulanık bir pozisyona çekiyor (örn. kendisi
    # çerçeveliyor) - sürekli odak bunu HEMEN geri almamalı.
    await supervisor.apply_manual_focus(100)
    supervisor.start()
    try:
        await asyncio.sleep(0.06 * 8)
        assert supervisor.status()["suspended"] is True
        assert supervisor.status()["refocus_count"] == 0
        assert controller.current_value == 100
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_runtime_toggle_off_disables_monitoring() -> None:
    controller = _FakeFocusController(start_value=250)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(), controller, broadcaster)
    supervisor.set_continuous_enabled(False)

    supervisor.start()
    try:
        await asyncio.sleep(0.06 * 8)
        assert supervisor.status()["enabled"] is False
        assert supervisor.status()["refocus_count"] == 0
    finally:
        await supervisor.stop()

    # Tekrar açıldığında çalışmaya devam etmeli.
    supervisor.set_continuous_enabled(True)
    supervisor.start()
    try:
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 1)
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_camera_autofocus_false_is_master_switch() -> None:
    config = _config()
    config = config.model_copy(
        update={"camera": config.camera.model_copy(update={"autofocus": False})}
    )
    controller = _FakeFocusController(start_value=250)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(config, controller, broadcaster)

    supervisor.start()
    try:
        await asyncio.sleep(0.06 * 8)
    finally:
        await supervisor.stop()
    assert supervisor.status()["enabled"] is False
    assert controller.calls == []


@pytest.mark.asyncio
async def test_disabled_during_recording_when_configured() -> None:
    controller = _FakeFocusController(start_value=250)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(
        _config(during_recording=False), controller, broadcaster, kind="recording"
    )

    supervisor.start()
    try:
        await asyncio.sleep(0.06 * 8)
    finally:
        await supervisor.stop()

    assert supervisor.status()["refocus_count"] == 0
    assert controller.calls == []


@pytest.mark.asyncio
async def test_does_nothing_without_live_source() -> None:
    controller = _FakeFocusController(start_value=250)
    supervisor = _supervisor(_config(), controller, None)

    supervisor.start()
    try:
        await asyncio.sleep(0.2)
    finally:
        await supervisor.stop()

    assert supervisor.status()["active"] is False
    assert controller.calls == []


class _DecayingBroadcaster(MjpegBroadcaster):
    """İlk karelerde net, sonra kalıcı olarak bulanık - odağın kaçıp bir daha
    düzelmediği (çünkü lens'e yazılamıyor) durumu simüle eder."""

    def __init__(self, sharp_frames: int) -> None:
        super().__init__()
        self._remaining_sharp = sharp_frames
        base = _checkerboard()
        ok, sharp = cv2.imencode(".jpg", base)
        assert ok
        ok, blurry = cv2.imencode(".jpg", cv2.GaussianBlur(base, (25, 25), 10))
        assert ok
        self._sharp = sharp.tobytes()
        self._blurry = blurry.tobytes()

    def get_latest_frame(self) -> bytes | None:
        if self._remaining_sharp > 0:
            self._remaining_sharp -= 1
            return self._sharp
        return self._blurry


@pytest.mark.asyncio
async def test_self_disables_after_repeated_i2c_failures() -> None:
    """Odak motoru yanıt vermiyorsa (donanım yok/kablo koptu) döngü saniyede
    bir hata loglayarak journal'ı doldurmamalı - kendini kapatmalı."""
    controller = _FakeFocusController(start_value=250, fail=True)
    broadcaster = _DecayingBroadcaster(sharp_frames=1)
    supervisor = _supervisor(_config(initial_sweep=False), controller, broadcaster)

    supervisor.start()
    try:
        assert await _wait_until(lambda: supervisor.status()["disabled_reason"] is not None)
    finally:
        await supervisor.stop()

    status = supervisor.status()
    assert status["enabled"] is False
    assert "I2C" in status["disabled_reason"]


@pytest.mark.asyncio
async def test_refocus_reverts_when_result_is_clearly_worse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Odaklama görüntüyü kötüleştirirse geri alınmalı.

    GERÇEK DONANIMDA gözlendi: bir süpürme, keskinliği 57 olan pozisyondan
    keskinliği 6 olan bir pozisyona geçip orada bıraktı (süpürme sırasındaki
    ölçümler ISP oturmadığı için yanıltıcıydı). Doğrulama ölçümü + geri alma
    tam olarak bunu engeller.
    """
    controller = _FakeFocusController(start_value=700)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=700)
    supervisor = _supervisor(_config(), controller, broadcaster)

    async def _misleading_search(focus_controller, get_frame, **kwargs) -> AutofocusResult:
        # Süpürme bulanık bir pozisyona gidip "harika buldum" diyor.
        focus_controller.set_focus(100)
        return AutofocusResult(best_position=100, best_sharpness=999.0, steps_taken=1)

    monkeypatch.setattr("app.camera.continuous_focus.run_local_search", _misleading_search)

    frame = broadcaster.get_latest_frame()
    assert frame is not None
    sharpness_before = measure_sharpness(frame)
    assert sharpness_before is not None

    await supervisor._refocus(
        LiveSource(kind="preview", broadcaster=broadcaster),
        reason="sharpness_drop",
        sharpness_before=sharpness_before,
    )

    assert controller.current_value == 700  # eski (net) pozisyona geri alındı
    assert supervisor.status()["last_action"]["reverted"] is True


@pytest.mark.asyncio
async def test_full_sweep_only_when_local_peak_is_at_window_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yerel arama pencerenin ORTASINDA tepe bulduysa tam süpürme YAPILMAMALI.

    Bu, gerçek donanımda gereksiz (ve zararlı) tam süpürmelerin sebebiydi:
    eskiden karar mutlak keskinlik eşiğiyle veriliyordu, ama mutlak değerler
    zamanla kayıyor.
    """
    sweeps: list[str] = []

    async def _full_sweep(focus_controller, get_frame) -> AutofocusResult:
        sweeps.append("full")
        return AutofocusResult(best_position=300, best_sharpness=10.0, steps_taken=1)

    monkeypatch.setattr("app.camera.continuous_focus.run_autofocus_sweep", _full_sweep)

    for best_position, expect_full_sweep in ((500, False), (400, True)):
        # center=500, step=25, span=4 -> pencere [400..600]; 400 kenardır.
        async def _local(focus_controller, get_frame, **kwargs) -> AutofocusResult:
            focus_controller.set_focus(best_position)
            return AutofocusResult(best_position=best_position, best_sharpness=50.0, steps_taken=1)

        monkeypatch.setattr("app.camera.continuous_focus.run_local_search", _local)
        sweeps.clear()

        controller = _FakeFocusController(start_value=500)
        broadcaster = _SimulatedLensBroadcaster(controller, true_focus=500)
        supervisor = _supervisor(_config(), controller, broadcaster)
        await supervisor._refocus(
            LiveSource(kind="preview", broadcaster=broadcaster),
            reason="sharpness_drop",
            sharpness_before=1.0,  # düşük: geri alma tetiklenmesin
        )
        assert (sweeps == ["full"]) is expect_full_sweep


@pytest.mark.asyncio
async def test_reenabling_while_image_is_blurry_triggers_refocus() -> None:
    """Sürekli odak yeniden açıldığında bulanık görüntüyü referans SANMAMALI.

    Tek karelik ölçümden "bu bulanık" sonucu çıkarılamaz (kontrast tabanlı
    AF'nin doğası); bu yüzden yeniden açılış "yeni akış" gibi ele alınır ve
    gerekiyorsa odaklanır. Bu hata gerçek donanımda gözlendi: lens manuel
    olarak 900'e (bulanık) çekildikten sonra şalter kapatılıp açılınca sistem
    bulanık görüntüyü referans alıp sonsuza kadar bulanık kalıyordu.
    """
    controller = _FakeFocusController(start_value=200)
    broadcaster = _SimulatedLensBroadcaster(controller, true_focus=200)
    supervisor = _supervisor(_config(), controller, broadcaster)

    supervisor.start()
    try:
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 1)
        # Kullanıcı elle bulanık bir pozisyona çekiyor, sonra şalteri
        # kapatıp tekrar açıyor.
        await supervisor.apply_manual_focus(900)
        supervisor.set_continuous_enabled(False)
        await asyncio.sleep(0.06 * 3)
        supervisor.set_continuous_enabled(True)
        assert await _wait_until(lambda: supervisor.status()["refocus_count"] >= 2)
    finally:
        await supervisor.stop()

    assert controller.current_value is not None
    assert abs(controller.current_value - 200) <= 50


def test_local_search_positions_are_clipped_and_sorted() -> None:
    assert local_search_positions(500, 25, 2) == [450, 475, 500, 525, 550]
    # Aralık kenarında kırpılır ve yinelenen değer üretmez.
    assert local_search_positions(10, 25, 2) == [0, 10, 35, 60]
    assert local_search_positions(1000, 50, 2) == [900, 950, 1000]
