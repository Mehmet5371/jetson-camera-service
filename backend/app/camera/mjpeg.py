"""Paylaşılan MJPEG (multipart/x-mixed-replace) yayın altyapısı.

Hem canlı önizleme (PreviewManager, kayıt YOKKEN) hem kayıt sırasındaki
canlı görüntü (RecordingManager'ın tee dalı) aynı frame okuma/ayrıştırma/
yayınlama mantığını kullanır - bu modül o ortak parçadır (DRY).

Neden ayrı bir pipe fd (stdout DEĞİL): gerçek donanımda doğrulandı ki
`gst-launch-1.0`'ın kendi durum mesajları ("Setting pipeline to PLAYING",
"Redistribute latency" vb.) STDOUT'a yazılıyor - MJPEG'i de stdout'a
koyarsak akış bozulur. Bu yüzden pipeline MJPEG'i `fdsink fd=<N>` ile
ayrılmış bir OS pipe'ına yazar, gst-launch'ın stdout/stderr'i ayrıca
loglama/çökme tespiti için kullanılır.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_CONTENT_LENGTH_RE = re.compile(rb"Content-Length:\s*(\d+)")
_HEADER_SEPARATOR = b"\r\n\r\n"
_FRAME_TRAILER_LEN = 2  # her frame'den sonraki "\r\n"
_SUBSCRIBER_QUEUE_MAXSIZE = 2  # "leaky": yavaş tüketici eski kare biriktirmez
_READ_CHUNK_SIZE = 65536


def extract_frames(buffer: bytearray) -> list[bytes]:
    """Bir bytearray tampondan tam MJPEG karelerini çıkarır, tüketilenleri siler.

    multipartmux çıktı formatı (gerçek donanımdan doğrulandı):
    `--frame\\r\\nContent-Type: image/jpeg\\r\\nContent-Length: N\\r\\n\\r\\n<N bytes>\\r\\n`
    """
    frames: list[bytes] = []
    while True:
        header_end = buffer.find(_HEADER_SEPARATOR)
        if header_end == -1:
            break
        header = bytes(buffer[:header_end])
        match = _CONTENT_LENGTH_RE.search(header)
        if match is None:
            del buffer[: header_end + len(_HEADER_SEPARATOR)]
            continue
        length = int(match.group(1))
        frame_start = header_end + len(_HEADER_SEPARATOR)
        frame_end = frame_start + length
        if len(buffer) < frame_end + _FRAME_TRAILER_LEN:
            break  # bu frame henüz tam gelmedi
        frames.append(bytes(buffer[frame_start:frame_end]))
        del buffer[: frame_end + _FRAME_TRAILER_LEN]
    return frames


class MjpegBroadcaster:
    """Tek bir MJPEG kaynağını (bir pipe fd) birden çok istemciye yayınlar.

    `feed()` ile ham bytes beslenir; tam kareler çıkarılıp en son kare
    saklanır (get_latest_frame - autofocus için) ve tüm aboneler'e (stream)
    "leaky" bir kuyrukla dağıtılır (yavaş istemci gecikme biriktirmez, kare
    kaybeder).
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[bytes | None]] = set()
        self._latest_frame: bytes | None = None
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        for frame in extract_frames(self._buffer):
            self._latest_frame = frame
            self._broadcast(frame)

    def _broadcast(self, frame: bytes) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(frame)

    def get_latest_frame(self) -> bytes | None:
        return self._latest_frame

    def has_subscribers(self) -> bool:
        return len(self._subscribers) > 0

    async def stream(self) -> AsyncIterator[bytes]:
        """Bir istemciye multipart/x-mixed-replace parçaları üretir."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n"
                    + frame + b"\r\n"
                )
        finally:
            self._subscribers.discard(queue)

    def reset(self) -> None:
        self._latest_frame = None
        self._buffer.clear()

    def close(self) -> None:
        """Tüm aboneleri sonlandırır (stream generator'ları None alıp biter)."""
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)


class MjpegPipeReader:
    """Bir alt sürecin ayrılmış MJPEG pipe fd'sini okuyup broadcaster'a besler.

    Kullanım:
        reader = MjpegPipeReader(broadcaster)
        write_fd = reader.create_pipe()   # subprocess'e pass_fds ile geçilir, fdsink fd=<write_fd>
        # subprocess başlatıldıktan SONRA:
        reader.close_write_end()          # yazma ucunu ebeveynde kapat
        await reader.start_reading()      # okuma döngüsünü başlat
        ...
        await reader.stop()
    """

    def __init__(self, broadcaster: MjpegBroadcaster) -> None:
        self._broadcaster = broadcaster
        self._read_fd: int | None = None
        self._write_fd: int | None = None
        self._transport: asyncio.ReadTransport | None = None
        self._reader: asyncio.StreamReader | None = None
        self._read_task: asyncio.Task[None] | None = None

    def create_pipe(self) -> int:
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        self._read_fd = read_fd
        self._write_fd = write_fd
        return write_fd

    def close_write_end(self) -> None:
        # Alt süreç yazma ucunu miras aldı; ebeveyn kopyasını kapatmalı ki
        # süreç öldüğünde okuma ucu EOF görsün.
        if self._write_fd is not None:
            os.close(self._write_fd)
            self._write_fd = None

    async def start_reading(self) -> None:
        assert self._read_fd is not None
        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(self._reader)
        self._transport, _ = await loop.connect_read_pipe(
            lambda: protocol, os.fdopen(self._read_fd, "rb")
        )
        self._read_fd = None  # fdopen sahipliği devraldı
        self._read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                chunk = await self._reader.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                self._broadcaster.feed(chunk)
        except Exception:  # noqa: BLE001 - okuma döngüsü çökerse yalnızca loglanır
            logger.exception(
                "mjpeg_read_loop_failed", extra={"component": "mjpeg_pipe_reader"}
            )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._read_task is not None:
            with contextlib.suppress(Exception):
                await self._read_task
            self._read_task = None
        # Yarım kalmış write_fd (start_reading çağrılmadan stop edilirse)
        if self._write_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._write_fd)
            self._write_fd = None
        if self._read_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._read_fd)
            self._read_fd = None
        self._broadcaster.reset()
