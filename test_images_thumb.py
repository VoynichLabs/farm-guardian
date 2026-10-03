# Author: Claude Opus 5.5
# Date: 03-October-2026 (bounded-cache tests added same day, v2.77.0)
# PURPOSE: Tests for the gem image variants in images_thumb.py (v2.76.0):
#          the 720px WebP "card" variant used by farm-2026's GemCard, that it
#          caches under a format-specific filename/ETag distinct from the
#          JPEG thumbs, that the historical JPEG filename/ETag is unchanged,
#          that full size is the untouched original, the degraded-ETag rule,
#          and that configure() survives an unmounted SSD (dangling symlink).
#          v2.77.0: proves the cache cannot fill a disk — LRU eviction keeps
#          it under max_bytes, recently used files survive eviction, an
#          unmounted SSD / missing volume / low free space means zero disk
#          writes, and only whitelisted sizes can be cached.
#          Real Pillow encodes against a real portrait JPEG in a temp dir.
# SRP/DRY check: Pass — exercises images_thumb only; no DB, no HTTP.

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

import images_thumb  # noqa: E402

_SHA = "a" * 64


def _setup(tmp: Path, **cache_cfg) -> Path:
    """data root with one 1080x1920 portrait JPEG, like an s7-cam gem, and the
    thumb cache in tmp/cache/thumbs. require_mount defaults to None here
    because a temp dir is not a mount point; the mount tests set it."""
    (tmp / "archive").mkdir()
    (tmp / "cache").mkdir(exist_ok=True)
    src = tmp / "archive" / "gem.jpg"
    Image.new("RGB", (1080, 1920), (90, 120, 60)).save(src, format="JPEG", quality=90)
    cfg = {"dir": str(tmp / "cache" / "thumbs"), "require_mount": None, "min_free_bytes": 0}
    cfg.update(cache_cfg)
    images_thumb.configure(tmp, cfg)
    return src


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.iterdir() if p.is_file())


def _all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


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
        images_thumb.configure(tmp, {"dir": str(tmp / "cache" / "thumbs"),
                                     "require_mount": None})  # must log, not raise
        assert not (tmp / "unmounted-volume").exists()


def test_eviction_keeps_cache_under_cap_and_spares_recent_files():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        thumbs = tmp / "cache" / "thumbs"
        one, _, _ = images_thumb.get_thumb("0" * 64, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
        cap = len(one) * 5 + 10  # room for five card variants
        _setup_cap = {"dir": str(thumbs), "require_mount": None, "min_free_bytes": 0, "max_bytes": cap}
        images_thumb.configure(tmp, _setup_cap)
        keep = "f" * 64
        now = int(time.time()) - 100_000  # older than any real write below
        for i in range(40):
            sha = f"{i:064d}"
            images_thumb.get_thumb(sha, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
            # Deterministic LRU clock: file i was "used" at now+i.
            os.utime(thumbs / f"{sha}-720-webp.webp", (now + i, now + i))
            if i == 0:
                images_thumb.get_thumb(keep, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
            # The keeper is used again on every round, so it stays most recent.
            os.utime(thumbs / f"{keep}-720-webp.webp", (now + i + 0.5, now + i + 0.5))
            assert _dir_bytes(thumbs) <= cap, f"cache over cap after write {i}"
            assert images_thumb.cache_stats()["bytes"] == _dir_bytes(thumbs)
        assert (thumbs / f"{keep}-720-webp.webp").exists(), "recently used file was evicted"
        assert not (thumbs / f"{0:064d}-720-webp.webp").exists(), "oldest file survived"
        assert (thumbs / f"{39:064d}-720-webp.webp").exists(), "newest file was evicted"


def test_configure_trims_an_existing_oversized_cache():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        thumbs = tmp / "cache" / "thumbs"
        thumbs.mkdir(parents=True)
        for i in range(10):
            f = thumbs / f"{i:064d}-480.jpg"
            f.write_bytes(b"x" * 1000)
            os.utime(f, (1_800_000_000 + i, 1_800_000_000 + i))
        (thumbs / "stale.jpg.tmp").write_bytes(b"x" * 5000)
        images_thumb.configure(tmp, {"dir": str(thumbs), "require_mount": None, "max_bytes": 4000})
        assert _dir_bytes(thumbs) <= 4000
        assert not (thumbs / "stale.jpg.tmp").exists()
        assert (thumbs / f"{9:064d}-480.jpg").exists()
        assert not (thumbs / f"{0:064d}-480.jpg").exists()


def test_unmounted_ssd_resizes_in_memory_with_no_disk_writes():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        # A plain folder standing in for /Volumes/<SSD> while it is unmounted.
        fake_volume = tmp / "fake-volume"
        fake_volume.mkdir()
        images_thumb.configure(tmp, {"dir": str(fake_volume / "thumbs"),
                                     "require_mount": str(fake_volume)})
        before = sorted(_all_files(tmp))
        data, etag, media = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 720, images_thumb.FORMAT_WEBP)
        assert media == "image/webp" and etag == f'"{_SHA}-720-webp"'
        with Image.open(io.BytesIO(data)) as im:
            assert im.size == (405, 720)
        assert sorted(_all_files(tmp)) == before, "wrote to disk with the SSD unmounted"
        assert not (fake_volume / "thumbs").exists()
        assert images_thumb.cache_stats()["writable"] is False


def test_missing_volume_is_never_recreated_on_internal_disk():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        gone = tmp / "Volumes" / "Samsung 9100 SSD"
        images_thumb.configure(tmp, {"dir": str(gone / "farm-guardian-data" / "cache" / "thumbs"),
                                     "require_mount": str(gone)})
        images_thumb.get_thumb(_SHA, "archive/gem.jpg", 480)
        assert not (tmp / "Volumes").exists()


def test_cache_dir_outside_required_mount_is_not_written():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        volume = tmp / "volume"
        volume.mkdir()
        other = tmp / "elsewhere"
        other.mkdir()
        # Pretend volume/ is mounted, so only the "cache dir must resolve
        # under the mount" rule can stop the write (e.g. a symlink that
        # points back onto the internal drive).
        real_ismount = images_thumb.os.path.ismount
        images_thumb.os.path.ismount = lambda p: Path(p) == volume
        try:
            os.symlink(other, volume / "thumbs")
            images_thumb.configure(tmp, {"dir": str(volume / "thumbs"), "require_mount": str(volume),
                                         "min_free_bytes": 0})
            images_thumb.get_thumb(_SHA, "archive/gem.jpg", 480)
            assert not any(other.iterdir()), "followed a symlink off the required mount"
            # Control: a real dir under the "mounted" volume is written.
            images_thumb.configure(tmp, {"dir": str(volume / "real"), "require_mount": str(volume),
                                         "min_free_bytes": 0})
            images_thumb.get_thumb(_SHA, "archive/gem.jpg", 480)
            assert (volume / "real" / f"{_SHA}-480.jpg").exists()
        finally:
            images_thumb.os.path.ismount = real_ismount


def test_low_free_space_skips_cache_write():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp, min_free_bytes=1 << 62)
        data, _, _ = images_thumb.get_thumb(_SHA, "archive/gem.jpg", 480)
        assert data
        assert not any((tmp / "cache" / "thumbs").iterdir())


def test_non_whitelisted_size_is_refused():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp)
        try:
            images_thumb.get_thumb(_SHA, "archive/gem.jpg", 333)
        except ValueError:
            pass
        else:
            raise AssertionError("size 333 should be refused")
        assert not any((tmp / "cache" / "thumbs").iterdir())


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
