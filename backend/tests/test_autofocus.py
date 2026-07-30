"""Otomatik odak testleri - donanım/I2C gerektirmez.

`measure_sharpness` gerçek cv2 ile sentetik (üretilmiş) görüntüler
üzerinde test edilir. `run_autofocus_sweep` sahte bir FocusController ve
sahte bir kare kaynağıyla süpürme/erken-durdurma mantığını doğrular -
gerçek I2C yazma olmadan.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.camera.autofocus import (
    AutofocusFailedError,
    measure_sharpness,
    run_autofocus_sweep,
)


def _encode_jpeg(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def _sharp_image() -> np.ndarray:
    # Yüksek frekanslı bir dama tahtası deseni - net/keskin bir görüntüyü temsil eder.
    image = np.zeros((200, 200), dtype=np.uint8)
    image[::2, ::2] = 255
    image[1::2, 1::2] = 255
    return image


def _blurry_image() -> np.ndarray:
    # Aynı dama tahtasının bulanıklaştırılmış hali - düşük keskinlik.
    sharp = _sharp_image()
    return cv2.GaussianBlur(sharp, (25, 25), 10)


def _flat_image() -> np.ndarray:
    # Düz gri bir yüzey - kenar/kontrast yok (gerçek testte kameranın boş
    # bir duvara baktığı senaryoyla aynı).
    return np.full((200, 200), 128, dtype=np.uint8)


def test_sharp_image_has_higher_sharpness_than_blurry() -> None:
    sharp_score = measure_sharpness(_encode_jpeg(_sharp_image()))
    blurry_score = measure_sharpness(_encode_jpeg(_blurry_image()))
    assert sharp_score is not None
    assert blurry_score is not None
    assert sharp_score > blurry_score


def test_flat_image_has_near_zero_sharpness() -> None:
    score = measure_sharpness(_encode_jpeg(_flat_image()))
    assert score is not None
    assert score < 1.0


def test_invalid_jpeg_bytes_returns_none() -> None:
    assert measure_sharpness(b"not a real jpeg") is None


class _FakeFocusController:
    """Gerçek I2C yazmadan set_focus çağrılarını kaydeder."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.current_value: int | None = None

    def set_focus(self, value: int) -> None:
        self.calls.append(value)
        self.current_value = value


@pytest.mark.asyncio
async def test_autofocus_sweep_finds_peak_position(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bu test SÜPÜRME/erken-durdurma KARAR mantığını izole test eder - gerçek
    # görüntü/blur simülasyonundan bilerek bağımsızdır (JPEG keskinlik ölçümü
    # zaten test_sharp_image_has_higher_sharpness_than_blurry ile ayrıca
    # doğrulanıyor). measure_sharpness, odak pozisyonunun 300'e uzaklığına
    # göre düzgün azalan bir değer döner - gerçek bir tepe noktası simüle eder.
    monkeypatch.setattr("app.camera.autofocus._SETTLE_SECONDS", 0.0)

    def fake_measure_sharpness(jpeg_bytes: bytes) -> float:
        position = int(jpeg_bytes.decode())
        distance = abs(position - 300)
        return 1000.0 / (1 + distance)

    monkeypatch.setattr("app.camera.autofocus.measure_sharpness", fake_measure_sharpness)

    focus_controller = _FakeFocusController()

    def _get_latest_frame() -> bytes | None:
        if focus_controller.current_value is None:
            return None
        # Gerçek bir JPEG değil - yalnızca fake_measure_sharpness'ın
        # pozisyonu geri okuyabilmesi için taşıyıcı bir bayt dizisi.
        return str(focus_controller.current_value).encode()

    result = await run_autofocus_sweep(focus_controller, _get_latest_frame)

    # Tepe nokta 300'dü; süpürme adımı 50 olduğu için en yakın adaylardan
    # (250, 300 veya 350) birine yakınsaması bekleniyor.
    assert abs(result.best_position - 300) <= 50
    assert focus_controller.current_value == result.best_position


@pytest.mark.asyncio
async def test_autofocus_raises_when_no_frames_ever_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.camera.autofocus._SETTLE_SECONDS", 0.0)
    focus_controller = _FakeFocusController()

    with pytest.raises(AutofocusFailedError):
        await run_autofocus_sweep(focus_controller, lambda: None)
