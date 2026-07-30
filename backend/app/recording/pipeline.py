"""GStreamer pipeline'ları için argüman listesi kurucuları (kayıt + önizleme).

ÖNEMLİ: Burada üretilen değerler yalnızca Pydantic ile doğrulanmış config
alanlarından (sayılar, kapalı enum'lar) gelir; hiçbir ham kullanıcı stringi
doğrudan bir argümana eklenmez. Çıktı, subprocess'e TEK BİR shell stringi
olarak değil, bir argüman LİSTESİ olarak verilecek şekilde tasarlanmıştır
(shell=True yok, shell metakarakter/injection riski yok).

Bu donanımda (Faz 1) doğrulanan gerçek: donanımsal H.264 encoder yok, bu
yüzden x264enc (yazılım) kullanılır; nvvidconv NVMM bellekten sistem
belleğine (I420) dönüşüm yapar.

Canlı önizleme (MJPEG) çıktısı STDOUT'a DEĞİL, ayrılmış bir OS pipe fd'sine
yazılır (`fdsink fd=<N>`) - çünkü gst-launch'ın kendi durum mesajları
stdout'a gidiyor ve MJPEG'i bozardı (gerçek donanımda doğrulandı, bkz.
camera/mjpeg.py).
"""

from __future__ import annotations

from pathlib import Path

from app.camera.detector import CameraBackendInfo
from app.core.config import AppConfig

_NANOSECONDS_PER_MINUTE = 60_000_000_000


def _build_source_args(
    backend_info: CameraBackendInfo,
    *,
    sensor_id: int,
    device: str,
    width: int,
    height: int,
    fps: int,
) -> tuple[list[str], str, list[str]]:
    """Kamera backend'ine göre kaynak/caps/dönüştürücü argümanlarını kurar.

    Kayıt ve önizleme pipeline'ları AYNI backend seçim mantığını paylaşır -
    yalnızca çözünürlük/fps ve pipeline'ın geri kalanı (encoder/sink) farklıdır.
    """
    if backend_info.backend == "argus":
        source_args = ["nvarguscamerasrc", f"sensor-id={sensor_id}"]
        caps = f"video/x-raw(memory:NVMM),width={width},height={height},framerate={fps}/1"
        convert_args = ["nvvidconv"]
    elif backend_info.backend == "v4l2":
        source_args = ["v4l2src", f"device={device}"]
        caps = f"video/x-raw,width={width},height={height},framerate={fps}/1"
        convert_args = ["videoconvert"]
    elif backend_info.backend == "mock":
        # Yalnızca test amaçlı: gerçek donanıma hiç dokunmadan sentetik
        # görüntü üretir (bkz. detector.py, şartname böl. 21).
        source_args = ["videotestsrc", "is-live=true"]
        caps = f"video/x-raw,width={width},height={height},framerate={fps}/1"
        convert_args = ["videoconvert"]
    else:
        raise ValueError(f"desteklenmeyen kamera backend'i: {backend_info.backend}")
    return source_args, caps, convert_args


def _mjpeg_branch_args(config: AppConfig, mjpeg_fd: int) -> list[str]:
    """tee'nin canlı önizleme dalı: ölçekle -> JPEG -> multipart -> ayrılmış fd.

    Kayıt çözünürlüğünden bağımsız düşük önizleme çözünürlüğüne ölçeklenir
    (CPU/bant genişliği tasarrufu). `leaky=downstream` queue: önizleme dalı
    yavaşlarsa kayıt dalını GECİKTİRMEZ (kare düşürür) - kayıt kalitesi
    önizleme için asla feda edilmez.
    """
    preview = config.preview
    return [
        "t.", "!",
        "queue", "max-size-buffers=2", "leaky=downstream", "!",
        "videoscale", "!",
        f"video/x-raw,width={preview.width},height={preview.height}", "!",
        "jpegenc", f"quality={preview.jpeg_quality}", "!",
        "multipartmux", "boundary=frame", "!",
        "fdsink", f"fd={mjpeg_fd}",
    ]


def build_pipeline_args(
    config: AppConfig,
    backend_info: CameraBackendInfo,
    output_dir: Path,
    base_filename: str,
    mjpeg_fd: int | None = None,
) -> list[str]:
    """Kayıt pipeline'ı. mjpeg_fd verilirse tee ile aynı anda canlı önizleme
    (MJPEG) de o fd'ye yazılır - kayıt sırasında canlı görüntü + odak için."""
    camera = config.camera
    recording = config.recording

    source_args, caps, convert_args = _build_source_args(
        backend_info,
        sensor_id=camera.sensor_id,
        device=camera.device,
        width=camera.width,
        height=camera.height,
        fps=camera.fps,
    )

    # x264enc "bitrate" özelliği kbit/s bekler, config bps tutar.
    bitrate_kbps = max(1, recording.bitrate_bps // 1000)
    key_int_max = camera.fps * 2

    encoder_args = [
        "x264enc",
        f"speed-preset={recording.speed_preset}",
        f"bitrate={bitrate_kbps}",
        f"key-int-max={key_int_max}",
        "tune=zerolatency",
    ]

    max_size_time_ns = (
        recording.segment_duration_minutes * _NANOSECONDS_PER_MINUTE
        if recording.segment_duration_minutes > 0
        else 0
    )
    if recording.segment_duration_minutes > 0:
        location = str(output_dir / f"{base_filename}_%03d.{recording.container}")
    else:
        location = str(output_dir / f"{base_filename}.{recording.container}")

    sink_args = [
        "splitmuxsink",
        f"location={location}",
        f"max-size-time={max_size_time_ns}",
        "muxer=mp4mux",
    ]

    args: list[str] = ["gst-launch-1.0", "-e"]
    args += source_args
    args += ["!", caps, "!"]
    args += convert_args
    args += ["!", "video/x-raw,format=I420", "!"]

    if mjpeg_fd is None:
        # Önizleme dalı yok (örn. kamera testi) - düz kayıt.
        args += encoder_args
        args += ["!", "h264parse", "!"]
        args += sink_args
        return args

    # tee: bir dal MP4 kaydı, bir dal canlı önizleme (MJPEG).
    args += ["tee", "name=t"]
    # Kayıt dalı - explicit caps ŞART: aksi halde tee dalları birbirinin
    # çözünürlüğünü sızdırabiliyor (gerçek donanımda gözlemlenen tuzak,
    # kayıt 640x360'a düşüyordu).
    args += [
        "t.", "!",
        "queue", "max-size-buffers=4", "leaky=downstream", "!",
        f"video/x-raw,width={camera.width},height={camera.height}", "!",
    ]
    args += encoder_args
    args += ["!", "h264parse", "!"]
    args += sink_args
    # Önizleme dalı.
    args += _mjpeg_branch_args(config, mjpeg_fd)
    return args


def build_preview_pipeline_args(
    config: AppConfig, backend_info: CameraBackendInfo, mjpeg_fd: int
) -> list[str]:
    """Kayıt YOKKEN canlı önizleme (odak/çerçeveleme) için MJPEG pipeline'ı.

    Çıktı ayrılmış bir OS pipe fd'sine yazılır (stdout DEĞİL - gst-launch
    mesajlarıyla karışmaması için). `-e` bayrağı YOK: dosya finalize
    edilmiyor, durdurma SIGKILL ile yapılır (bkz. camera/preview.py).
    """
    camera = config.camera
    preview = config.preview

    source_args, caps, convert_args = _build_source_args(
        backend_info,
        sensor_id=camera.sensor_id,
        device=camera.device,
        width=preview.width,
        height=preview.height,
        fps=preview.fps,
    )

    args: list[str] = ["gst-launch-1.0"]
    args += source_args
    args += ["!", caps, "!"]
    args += convert_args
    args += ["!", "video/x-raw,format=I420", "!"]
    args += ["jpegenc", f"quality={preview.jpeg_quality}", "!"]
    args += ["multipartmux", "boundary=frame", "!"]
    args += ["fdsink", f"fd={mjpeg_fd}"]
    return args
