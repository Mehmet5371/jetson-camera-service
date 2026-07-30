"""Otomatik odak: kontrast (Laplacian varyansı) tabanlı süpürme algoritması.

Arducam'ın resmi örneğindeki (`~/Jetson_IMX519_Focus_Example/Autofocus.py`)
YÖNTEMLE aynı mantık: odak değerini adım adım artırıp her adımda merkez
bölgenin (ROI, görüntünün orta %20x%20'lik kısmı) keskinliğini Laplacian
varyansıyla ölçer, keskinlik 3 adım üst üste düşünce süpürmeyi erken
durdurur, en keskin pozisyona geri döner. Fark: bu proje threading yerine
asyncio kullanır ve kareleri ayrı bir kamera sınıfı yerine
`PreviewManager`'ın MJPEG akışından (zaten JPEG-encode edilmiş) alır -
`opencv-python-headless` bu yüzden yalnızca JPEG ÇÖZMEK için gerekir,
GStreamer entegrasyonu gerekmez.

İki süpürme biçimi var, ikisi de aynı çekirdeği (`_sweep_positions`) kullanır:
- `run_autofocus_sweep`: TAM aralık (0-1000, 50'lik adım, erken durdurmalı) -
  odağın nerede olduğu hiç bilinmediğinde.
- `run_local_search`: mevcut pozisyonun ÇEVRESİ (küçük adım, erken durdurma
  YOK) - sürekli odak (continuous_focus.py) küçük kaymaları hızlıca ve lens'i
  az hareket ettirerek düzeltmek için bunu kullanır; yetersiz kalırsa tam
  süpürmeye yükseltir.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from app.camera.focus import MAX_FOCUS_VALUE, MIN_FOCUS_VALUE, FocusController

logger = logging.getLogger(__name__)

_FOCUS_STEP = 50
# VCM'in fiziksel olarak hareket edip önizleme pipeline'ının yeni bir kare
# üretmesi için bekleme süresi (Arducam'ın MOVE_TIME'ından daha muhafazakar,
# çünkü bizim kare kaynağımız ayrı bir subprocess üzerinden async geliyor).
_SETTLE_SECONDS = 0.25
_DECLINE_LIMIT = 3
# Erken durdurma yalnızca GERÇEK bir tepe noktası geçildiğinde anlamlıdır:
# o ana kadarki en iyi keskinliğin bu katının altına düşmek "düşüş" sayılır.
# Arducam örneği bunun yerine "önceki ölçümden küçük veya EŞİT" kuralını
# kullanıyor; o kural ağır bulanık (keskinliğin ~0'a doyduğu) bir aralıkta
# üst üste eşit değerler görüp süpürmeyi HEMEN kesiyor ve odağı 0'da bırakıyor
# (sürekli odak testlerinde simüle edilmiş lensle tam olarak bu gözlendi).
_DECLINE_RATIO = 0.8
_MAX_MISSING_FRAMES = 5
# Arducam'ın kendi ROI'siyle aynı: merkez %20x%20'lik bölge (x, y, w, h).
_ROI = (0.4, 0.4, 0.2, 0.2)


class AutofocusFailedError(Exception):
    pass


@dataclass
class AutofocusResult:
    best_position: int
    best_sharpness: float
    steps_taken: int


def measure_sharpness(jpeg_bytes: bytes) -> float | None:
    array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape[:2]
    roi_x, roi_y, roi_w, roi_h = _ROI
    x_start, y_start = int(width * roi_x), int(height * roi_y)
    x_end, y_end = x_start + int(width * roi_w), y_start + int(height * roi_h)
    roi = image[y_start:y_end, x_start:x_end]
    if roi.size == 0:
        return None
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


async def _sweep_positions(
    focus_controller: FocusController,
    get_latest_frame: Callable[[], bytes | None],
    positions: Sequence[int],
    *,
    decline_limit: int | None,
    operation: str,
) -> AutofocusResult:
    """Verilen odak pozisyonlarını sırayla dener, en keskin olanı uygular.

    `decline_limit` verilirse (tam süpürme) keskinlik o kadar adım üst üste
    düşünce süpürme erken kesilir - tepe noktası geçilmiştir, kalan aralığı
    taramak boşuna lens hareketidir. Yerel aramada None verilir (aralık
    zaten küçük, erken kesmenin kazancı yok).
    """
    history: list[tuple[int, float]] = []
    decline_streak = 0
    best_so_far = 0.0
    consecutive_missing_frames = 0

    for position in positions:
        focus_controller.set_focus(position)
        await asyncio.sleep(_SETTLE_SECONDS)

        frame = get_latest_frame()
        if frame is None:
            consecutive_missing_frames += 1
            if consecutive_missing_frames > _MAX_MISSING_FRAMES:
                raise AutofocusFailedError(
                    "Canlı görüntü akışından kare alınamadı - otomatik odak için "
                    "önizlemenin veya bir kaydın aktif olması gerekir."
                )
            continue
        consecutive_missing_frames = 0

        sharpness = measure_sharpness(frame)
        if sharpness is None:
            continue

        history.append((position, sharpness))

        if decline_limit is not None:
            if best_so_far > 0.0 and sharpness < best_so_far * _DECLINE_RATIO:
                decline_streak += 1
            else:
                decline_streak = 0
            best_so_far = max(best_so_far, sharpness)
            if decline_streak >= decline_limit:
                break
        else:
            best_so_far = max(best_so_far, sharpness)

    if not history:
        raise AutofocusFailedError("Otomatik odak sırasında hiç geçerli kare analiz edilemedi.")

    best_position, best_sharpness = max(history, key=lambda item: item[1])
    focus_controller.set_focus(best_position)

    logger.info(
        "autofocus_completed",
        extra={
            "component": "autofocus",
            "operation": operation,
            "best_position": best_position,
            "best_sharpness": best_sharpness,
            "steps_taken": len(history),
        },
    )
    return AutofocusResult(
        best_position=best_position, best_sharpness=best_sharpness, steps_taken=len(history)
    )


async def run_autofocus_sweep(
    focus_controller: FocusController,
    get_latest_frame: Callable[[], bytes | None],
) -> AutofocusResult:
    """TAM aralık süpürmesi: en keskin pozisyonu bulur, uygular ve döner.

    `get_latest_frame` her çağrıda canlı görüntü akışının EN SON karesini
    (JPEG bytes) döner - `MjpegBroadcaster.get_latest_frame` ile uyumlu.
    Akış aktif değilse (frame hiç gelmiyorsa) `AutofocusFailedError`
    fırlatılır.
    """
    positions = list(range(MIN_FOCUS_VALUE, MAX_FOCUS_VALUE + 1, _FOCUS_STEP))
    return await _sweep_positions(
        focus_controller,
        get_latest_frame,
        positions,
        decline_limit=_DECLINE_LIMIT,
        operation="sweep",
    )


def local_search_positions(center: int, step: int, span_steps: int) -> list[int]:
    """`center` çevresinde ±(step*span_steps) aralığında artan pozisyon listesi.

    Aralık dışına taşan değerler kırpılır, yinelenenler teklenir; sıralı
    (artan) döner - lens tek yönde düzgün ilerler, ileri-geri zıplamaz.
    """
    candidates = {
        min(MAX_FOCUS_VALUE, max(MIN_FOCUS_VALUE, center + offset * step))
        for offset in range(-span_steps, span_steps + 1)
    }
    return sorted(candidates)


async def run_local_search(
    focus_controller: FocusController,
    get_latest_frame: Callable[[], bytes | None],
    *,
    center: int,
    step: int,
    span_steps: int,
) -> AutofocusResult:
    """Mevcut pozisyonun ÇEVRESİNDE hızlı yerel arama (küçük odak kaymaları için)."""
    return await _sweep_positions(
        focus_controller,
        get_latest_frame,
        local_search_positions(center, step, span_steps),
        decline_limit=None,
        operation="local_search",
    )
