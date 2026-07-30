"""_extract_frames() saf unit testleri - donanım/subprocess gerektirmez.

Gerçek `multipartmux` çıktısının byte yapısı Faz "canlı önizleme"
geliştirmesi sırasında gerçek donanımdan alınıp incelendi:
`--frame\\r\\nContent-Type: image/jpeg\\r\\nContent-Length: N\\r\\n\\r\\n<N bytes>\\r\\n`
"""

from __future__ import annotations

from app.camera.mjpeg import extract_frames as _extract_frames


def _make_frame_bytes(payload: bytes) -> bytes:
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
        + payload + b"\r\n"
    )


def test_single_complete_frame_extracted() -> None:
    buffer = bytearray(_make_frame_bytes(b"\xff\xd8\xff\xd9fake-jpeg"))
    frames = _extract_frames(buffer)
    assert frames == [b"\xff\xd8\xff\xd9fake-jpeg"]
    assert len(buffer) == 0  # tüketilen bytes buffer'dan silinmeli


def test_multiple_frames_in_one_chunk() -> None:
    buffer = bytearray(_make_frame_bytes(b"frame1") + _make_frame_bytes(b"frame2-longer"))
    frames = _extract_frames(buffer)
    assert frames == [b"frame1", b"frame2-longer"]
    assert len(buffer) == 0


def test_partial_frame_not_extracted_yet() -> None:
    full = _make_frame_bytes(b"0123456789")
    partial = full[:-3]  # frame verisinin bir kısmı eksik
    buffer = bytearray(partial)
    frames = _extract_frames(buffer)
    assert frames == []
    assert len(buffer) == len(partial)  # buffer olduğu gibi korunmalı


def test_partial_frame_completes_on_next_feed() -> None:
    full = _make_frame_bytes(b"0123456789")
    buffer = bytearray(full[:-3])
    assert _extract_frames(buffer) == []
    buffer.extend(full[-3:])
    assert _extract_frames(buffer) == [b"0123456789"]


def test_frame_with_binary_jpeg_markers_not_confused_with_boundary() -> None:
    # Gerçek JPEG bytes'ları rastgele "--frame" benzeri diziler içerebilir -
    # parser Content-Length'e güvendiği için bu bir sorun OLMAMALI.
    tricky_payload = b"\xff\xd8--framestillinsidethejpeg\xff\xd9"
    buffer = bytearray(_make_frame_bytes(tricky_payload))
    frames = _extract_frames(buffer)
    assert frames == [tricky_payload]


def test_empty_buffer_returns_no_frames() -> None:
    buffer = bytearray()
    assert _extract_frames(buffer) == []


def test_malformed_header_without_content_length_is_skipped() -> None:
    malformed = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    good = _make_frame_bytes(b"realframe")
    buffer = bytearray(malformed + good)
    frames = _extract_frames(buffer)
    assert frames == [b"realframe"]
