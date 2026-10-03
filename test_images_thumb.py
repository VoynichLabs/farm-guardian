# Author: Claude Opus 5.5
# Date: 03-October-2026
# PURPOSE: Tests for the gem image variants in images_thumb.py (v2.76.0):
#          the 720px WebP "card" variant used by farm-2026's GemCard, that it
#          caches under a format-specific filename/ETag distinct from the
#          JPEG thumbs, that the historical JPEG filename/ETag is unchanged,
#          that full size is the untouched original, the degraded-ETag rule,
#          and that configure() survives an unmounted SSD (dangling symlink).
#          Real Pillow encodes against a real portrait JPEG in a temp dir.
# SRP/DRY check: Pass — exercises images_thumb only; no DB, no HTTP.

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

import images_thumb  # noqa: E402

_SHA = "a" * 64


def _setup(tmp: Path) -> Path:
    """data root with one 1080x1920 portrait JPEG, like an s7-cam gem."""
    (tmp / "archive").mkdir()
    src = tmp / "archive" / "gem.jpg"
    Image.new("RGB", (1080, 1920), (90, 120, 60)).save(src, format="JPEG", quality=90)
    images_thumb.configure(tmp)
    return src


def test_card_variant_is_720_webp_and_cached():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        data, etag, media = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
        assert media == "image/webp"
        assert etag == f'"{_SHA}-720-webp"'
        with Image.open(io.BytesIO(data)) as im:
            assert im.format == "WEBP"
            assert im.size == (405, 720)
        cached = tmp / "cache" / "thumbs" / f"{_SHA}-720-webp.webp"
        assert cached.read_bytes() == data
        again, _, _ = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
        assert again == data


def test_jpeg_thumb_keeps_historical_name_and_etag():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        data, etag, media = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 480)
        assert media == "image/jpeg"
        assert etag == f'"{_SHA}-480"'
        assert (tmp / "cache" / "thumbs" / f"{_SHA}-480.jpg").exists()
        with Image.open(io.BytesIO(data)) as im:
            assert im.format == "JPEG" and im.size == (270, 480)


def test_full_size_is_original_bytes():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        src = _setup(tmp)
        data, etag, media = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 0, images_thumb.FORMAT_WEBP)
        assert data == src.read_bytes()
        assert media == "image/jpeg" and etag == f'"{_SHA}-full"'


def test_missing_source_is_degraded_placeholder():
    with tempfile.TemporaryDirectory() as d:
        _setup(Path(d))
        _, etag, media = images_thumb.get_thumb(_SHA, "archive/nope.jpg", 720, images_thumb.FORMAT_WEBP)
        assert media == "image/jpeg"
        assert images_thumb.is_degraded_etag(etag)
        assert not images_thumb.is_degraded_etag(f'"{_SHA}-720-webp"')
        assert images_thumb.is_degraded_etag(f'"{_SHA}-720-webp-raw"')


def test_configure_survives_dangling_cache_symlink():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "cache").mkdir()
        os.symlink(tmp / "unmounted-volume" / "thumbs", tmp / "cache" / "thumbs")
        images_thumb.configure(tmp)  # must log, not raise
        assert not (tmp / "unmounted-volume").exists()


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
