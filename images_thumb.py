# Author: Claude Opus 4.6 (1M context); updated Claude Opus 5.5 03-Oct-2026 (WebP card variant v2.76.0; bounded cache v2.77.0)
# Date: 14-April-2026 (updated 03-Oct-2026)
# PURPOSE: Lazy thumbnail + image bytes server for /api/v1/images/*/image.
#          Generates resized copies on first request using Pillow, caches them
#          as {sha256}-{size}.{jpg|webp} in the configured cache dir, serves cached
#          copies on subsequent hits. ETag is sha256-size(-format) so the
#          client can If-None-Match its way to 304s. If a row's image_path is
#          NULL (post-retention or skip tier that somehow got requested),
#          serves a 1-pixel gray placeholder JPEG with a short max-age so the
#          UI can render something instead of a broken <img>.
#
#          03-Oct-2026: added a WebP output format (used by the 720px "card"
#          variant that farm-2026's GemCard shows). Format is folded into both
#          the cache filename and the ETag so a WebP is never served — or
#          304'd — where a JPEG was asked for. get_thumb now also returns the
#          media type. data/cache/thumbs is a symlink to the Samsung SSD
#          (same pattern as data/archive); configure() tolerates the SSD being
#          unmounted instead of crashing Guardian startup.
#
#          03-Oct-2026 (v2.77.0): the cache can no longer fill a disk. Path,
#          required mount and a hard byte cap come from images.thumb_cache in
#          config (safe defaults: Samsung SSD, 2 GiB). Writes happen only when
#          the SSD is really mounted and has headroom; otherwise the variant is
#          encoded in memory and nothing touches disk. Over the cap, least-
#          recently-used files (mtime, refreshed on hit) are evicted. Only the
#          whitelisted sizes in ALLOWED_SIZES can be cached.
# SRP/DRY check: Pass — single responsibility is image-bytes delivery.

from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger("guardian.images.thumb")

# 1x1 gray JPEG, base64. Embedded so we never ship a file dependency.
_PLACEHOLDER_B64 = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oADAMB"
    "AAIRAxEAPwD3+iiigD//2Q=="
)
_PLACEHOLDER_BYTES = base64.b64decode(_PLACEHOLDER_B64)


# data_root is the parent of the archive dir — image_path rows are stored
# relative to data/ (see tools/pipeline/store.py:164 relative_to logic).
_DATA_ROOT: Path = Path("data")

# Resized-image cache settings (config: images.thumb_cache). Defaults are the
# safe production values: cache on the Samsung SSD, never written unless that
# volume is really mounted, hard-capped at 2 GiB, and never written when the
# SSD is down to its last 20 GiB.
DEFAULT_CACHE_DIR = "/Volumes/Samsung 9100 SSD/farm-guardian-data/cache/thumbs"
DEFAULT_REQUIRE_MOUNT = "/Volumes/Samsung 9100 SSD"
DEFAULT_MAX_BYTES = 2 * 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 20 * 1024 ** 3
# Eviction runs the cache down to this fraction of the cap so it does not
# rescan the directory on every single write once it is full.
_EVICT_TO_FRACTION = 0.9
# A cache hit refreshes the file's mtime (our LRU clock — macOS does not keep
# atime reliably) at most this often, so hot files are not touched per request.
_TOUCH_INTERVAL_S = 3600

# Only these pixel sizes may ever be cached (API: thumb, card, 1920). Anything
# else is refused so a caller cannot mint unbounded variants. 0 = original.
ALLOWED_SIZES = frozenset({480, 720, 1920})

_THUMB_CACHE: Path = Path(DEFAULT_CACHE_DIR)
_REQUIRE_MOUNT: Optional[Path] = Path(DEFAULT_REQUIRE_MOUNT)
_MAX_BYTES: int = DEFAULT_MAX_BYTES
_MIN_FREE_BYTES: int = DEFAULT_MIN_FREE_BYTES
_cache_lock = threading.Lock()
_cache_total: int = 0  # running byte total of files in _THUMB_CACHE


# Output formats: name -> (Pillow format, file extension, media type).
FORMAT_JPEG = "jpeg"
FORMAT_WEBP = "webp"
_FORMATS = {
    FORMAT_JPEG: ("JPEG", "jpg", "image/jpeg"),
    FORMAT_WEBP: ("WEBP", "webp", "image/webp"),
}


def configure(data_root: Path, cache_cfg: Optional[dict] = None) -> None:
    """Called by register_api() with the config-driven data root (where the
    archive JPEGs live) and the images.thumb_cache block.

    cache_cfg keys (all optional; missing keys take the safe defaults):
      dir            absolute cache directory
      require_mount  mount point that must really be mounted before any write;
                     null disables the check (tests only)
      max_bytes      hard cap on the cache's total size
      min_free_bytes skip writes when the cache volume has less free space

    Never raises: an unmounted SSD just means every request is resized in
    memory with no disk writes, instead of taking Guardian down."""
    global _DATA_ROOT, _THUMB_CACHE, _REQUIRE_MOUNT, _MAX_BYTES, _MIN_FREE_BYTES, _cache_total
    cfg = cache_cfg or {}
    _DATA_ROOT = Path(data_root)
    _THUMB_CACHE = Path(cfg.get("dir") or DEFAULT_CACHE_DIR)
    mount = cfg.get("require_mount", DEFAULT_REQUIRE_MOUNT)
    _REQUIRE_MOUNT = Path(mount) if mount else None
    _MAX_BYTES = int(cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    _MIN_FREE_BYTES = int(cfg.get("min_free_bytes", DEFAULT_MIN_FREE_BYTES))
    with _cache_lock:
        _cache_total = 0
        if not _cache_writable():
            log.warning("thumb cache %s unavailable (SSD %s not mounted?) — resizing "
                        "in memory, no disk writes", _THUMB_CACHE, _REQUIRE_MOUNT)
            return
        try:
            _THUMB_CACHE.mkdir(exist_ok=True)
        except OSError as exc:
            log.warning("thumb cache %s unavailable: %s", _THUMB_CACHE, exc)
            return
        # Seed the running total and enforce the cap on whatever is there now.
        _evict_locked(force_scan=True)
    log.info("thumb cache %s: %.1f MB of %.1f MB cap", _THUMB_CACHE,
             _cache_total / 1e6, _MAX_BYTES / 1e6)


def _cache_writable() -> bool:
    """True only if writing into _THUMB_CACHE cannot land on the wrong disk.

    The required mount point must be an actual mount (an unmounted
    /Volumes/X is a plain folder on the internal drive, or absent), and the
    cache dir's real path — following the data/cache/thumbs symlink — must
    sit under it. The cache dir's parent must already exist: we never mkdir
    parents, so a missing volume can't be recreated as internal folders."""
    try:
        if _REQUIRE_MOUNT is not None:
            if not os.path.ismount(_REQUIRE_MOUNT):
                return False
            real_cache = Path(os.path.realpath(_THUMB_CACHE))
            real_mount = Path(os.path.realpath(_REQUIRE_MOUNT))
            if real_cache != real_mount and real_mount not in real_cache.parents:
                return False
        return Path(os.path.realpath(_THUMB_CACHE)).parent.is_dir()
    except OSError:
        return False


def _enough_free_space() -> bool:
    try:
        return shutil.disk_usage(_THUMB_CACHE).free >= _MIN_FREE_BYTES
    except OSError:
        return False


def _evict_locked(force_scan: bool = False) -> None:
    """With _cache_lock held: if the cache is over its cap, delete the least
    recently used files (oldest mtime) until it is at or below
    _EVICT_TO_FRACTION of the cap. Re-syncs _cache_total from disk whenever
    it scans, so out-of-band deletes or crashes cannot make it drift."""
    global _cache_total
    if not force_scan and _cache_total <= _MAX_BYTES:
        return
    try:
        entries = []
        for e in os.scandir(_THUMB_CACHE):
            if e.name.endswith(".tmp"):
                # Leftover from a crash mid-write (writes hold the lock, so
                # none can be in progress now). Sweep it.
                try:
                    os.unlink(e.path)
                except OSError:
                    pass
            elif e.is_file(follow_symlinks=False):
                st = e.stat(follow_symlinks=False)
                entries.append((st.st_mtime, st.st_size, e.path))
    except OSError as exc:
        log.warning("thumb cache scan failed: %s", exc)
        return
    total = sum(size for _, size, _ in entries)
    if total > _MAX_BYTES:
        target = int(_MAX_BYTES * _EVICT_TO_FRACTION)
        entries.sort()  # oldest mtime first = least recently used
        removed = 0
        for _mtime, size, path in entries:
            if total <= target:
                break
            try:
                os.unlink(path)
                total -= size
                removed += 1
            except FileNotFoundError:
                total -= size
            except OSError as exc:
                log.warning("thumb cache evict %s failed: %s", path, exc)
        log.info("thumb cache evicted %d files, now %.1f MB (cap %.1f MB)",
                 removed, total / 1e6, _MAX_BYTES / 1e6)
    _cache_total = total


def _touch(path: Path) -> None:
    """Refresh a cache hit's mtime so LRU eviction keeps it (throttled)."""
    try:
        if time.time() - path.stat().st_mtime > _TOUCH_INTERVAL_S:
            os.utime(path)
    except OSError:
        pass


def _store(cache_path: Path, data: bytes) -> None:
    """Write one variant into the cache if — and only if — it is safe to:
    SSD mounted, enough free space, then enforce the cap. Any failure leaves
    the request served from memory with nothing written."""
    global _cache_total
    with _cache_lock:
        if not _cache_writable() or not _enough_free_space():
            return
        try:
            _THUMB_CACHE.mkdir(exist_ok=True)
            tmp = cache_path.with_name(cache_path.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(cache_path)
        except OSError as exc:
            log.warning("thumb cache write %s failed: %s", cache_path.name, exc)
            return
        _cache_total += len(data)
        _evict_locked()


def cache_stats() -> dict:
    """Current cache location, size and cap (for checks and logging)."""
    with _cache_lock:
        return {"dir": str(_THUMB_CACHE), "bytes": _cache_total, "max_bytes": _MAX_BYTES,
                "writable": _cache_writable()}


def placeholder() -> tuple[bytes, dict]:
    """Bytes + headers for the 'metadata-only, image retained only as
    metadata' case. Short cache so a future retention-aware re-ingest
    can produce a real thumbnail."""
    return _PLACEHOLDER_BYTES, {
        "Cache-Control": "public, max-age=60",
        "ETag": '"placeholder-1"',
    }


def is_degraded_etag(etag: str) -> bool:
    """True for the placeholder and the failed-resize fallback — responses
    that should be cached briefly, not for the long immutable lifetime."""
    return etag.startswith('"placeholder') or etag.endswith('-raw"')


def _resolve(image_path_rel: str) -> Path:
    """image_path in the DB is relative to data/; absolute paths also
    supported for historical rows."""
    p = Path(image_path_rel)
    return p if p.is_absolute() else _DATA_ROOT / p


def _generate_thumb(source: Path, size: int, fmt: str = FORMAT_JPEG) -> bytes:
    """size=0 means 'serve the original bytes as-is' (full-resolution)."""
    if size <= 0:
        return source.read_bytes()
    with Image.open(source) as im:
        # Preserve orientation. LANCZOS is slow-but-good; we cache once and
        # serve forever so it only runs on first hit.
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        # RGB convert in case the source was ever saved in a different mode.
        rgb = im.convert("RGB")
        if fmt == FORMAT_WEBP:
            # method=6 is the slowest/smallest encoder setting; fine because
            # each variant is encoded once and then served from disk.
            rgb.save(buf, format="WEBP", quality=78, method=6)
        else:
            rgb.save(buf, format="JPEG", quality=80 if size <= 480 else 85)
        return buf.getvalue()


def get_thumb(
    sha256: Optional[str], image_path_rel: str, size: int, fmt: str = FORMAT_JPEG,
) -> tuple[bytes, str, str]:
    """Returns (image_bytes, etag, media_type). size in ALLOWED_SIZES or 0;
    fmt is FORMAT_JPEG or FORMAT_WEBP (ignored for size 0, which is always
    the archive original).
    sha256 may be None for very old rows; we fall back to the path as the
    cache key but still serve correctly."""
    if fmt not in _FORMATS:
        raise ValueError(f"unknown image format {fmt!r}")
    if not image_path_rel:
        b, _ = placeholder()
        return b, '"placeholder-1"', "image/jpeg"
    source = _resolve(image_path_rel)
    if not source.exists():
        b, _ = placeholder()
        return b, '"placeholder-1"', "image/jpeg"
    if size <= 0:
        # Full-resolution: skip the cache entirely — one-to-one with the
        # archive JPEG, re-encoding would lose quality.
        etag = f'"{sha256 or source.name}-full"'
        return source.read_bytes(), etag, "image/jpeg"

    if size not in ALLOWED_SIZES:
        raise ValueError(f"image size {size} is not one of {sorted(ALLOWED_SIZES)}")

    _pil_format, ext, media_type = _FORMATS[fmt]
    cache_key = sha256 or source.stem
    # JPEG keeps its historical filename and ETag so every cached thumb and
    # every browser-held ETag from before the WebP variant stays valid.
    suffix = f"{size}" if fmt == FORMAT_JPEG else f"{size}-{fmt}"
    cache_path = _THUMB_CACHE / f"{cache_key}-{suffix}.{ext}"
    etag = f'"{cache_key}-{suffix}"'
    try:
        data = cache_path.read_bytes()
        _touch(cache_path)
        return data, etag, media_type
    except OSError:
        pass  # not cached yet, evicted, or the SSD is unmounted
    try:
        data = _generate_thumb(source, size, fmt)
    except Exception as exc:
        log.warning("thumb generation failed for %s @ %d %s: %s", image_path_rel, size, fmt, exc)
        # Degrade gracefully: return the source JPEG bytes uncached. The
        # ETag gets a -raw suffix so a browser never caches the original
        # under the resized variant's validator.
        return source.read_bytes(), f'"{cache_key}-{suffix}-raw"', "image/jpeg"
    # Same bytes whether or not the write happens, so the ETag is the same.
    _store(cache_path, data)
    return data, etag, media_type
