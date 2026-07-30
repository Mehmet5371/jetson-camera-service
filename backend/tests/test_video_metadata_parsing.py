"""extract_video_metadata() edge-case testleri - donanım/ffprobe gerektirmez,
sentetik (elle yazılmış) ffprobe JSON çıktılarıyla çalışır."""

from __future__ import annotations

from app.storage.reconciler import extract_video_metadata


def test_extract_metadata_from_well_formed_probe_data() -> None:
    probe_data = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"}
        ],
        "format": {"duration": "12.5"},
    }
    metadata = extract_video_metadata(probe_data)
    assert metadata["width"] == 1920
    assert metadata["height"] == 1080
    assert metadata["codec"] == "h264"
    assert metadata["fps"] == 30.0
    assert metadata["duration_seconds"] == 12.5


def test_extract_metadata_missing_video_stream() -> None:
    probe_data = {"streams": [{"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "5.0"}}
    metadata = extract_video_metadata(probe_data)
    assert metadata["width"] is None
    assert metadata["height"] is None
    assert metadata["codec"] is None
    assert metadata["fps"] is None
    assert metadata["duration_seconds"] == 5.0


def test_extract_metadata_no_streams_at_all() -> None:
    metadata = extract_video_metadata({"format": {}})
    assert metadata["width"] is None
    assert metadata["duration_seconds"] is None


def test_extract_metadata_zero_denominator_frame_rate() -> None:
    probe_data = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "0/0"}],
        "format": {"duration": "1.0"},
    }
    metadata = extract_video_metadata(probe_data)
    assert metadata["fps"] is None


def test_extract_metadata_malformed_frame_rate_string() -> None:
    probe_data = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "not-a-fraction"}],
        "format": {"duration": "1.0"},
    }
    metadata = extract_video_metadata(probe_data)
    assert metadata["fps"] is None


def test_extract_metadata_empty_dict() -> None:
    metadata = extract_video_metadata({})
    assert metadata["duration_seconds"] is None
    assert metadata["width"] is None
