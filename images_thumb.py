# Author: Claude Opus 4.6 (1M context); updated Claude Opus 5.5 03-Oct-2026 (WebP card variant, v2.76.0)
# Date: 14-April-2026 (updated 03-Oct-2026)
# PURPOSE: Lazy thumbnail + image bytes server for /api/v1/images/*/image.
#          Generates resized copies on first request using Pillow, caches them
#          under data/cache/thumbs/{sha256}-{size}.{jpg|webp}, serves cached
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
# SRP/DRY check: Pass — single responsibility is image-bytes delivery.

from __future__ import annotations

import base64
import io
import logging
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
_THUMB_CACHE: Path = _DATA_ROOT / "cache" / "thumbs"


# Output formats: name -> (Pillow format, file extension, media type).
FORMAT_JPEG = "jpeg"
FORMAT_WEBP = "webp"
_FORMATS = {
    FORMAT_JPEG: ("JPEG", "jpg", "image/jpeg"),
    FORMAT_WEBP: ("WEBP", "webp", "image/webp"),
}


def configure(data_root: Path) -> None:
    """Called by register_api() so we resolve image paths relative to the
    right filesystem root (config-driven).

    data/cache/thumbs is normally a symlink onto the Samsung SSD. If the SSD
    is unmounted the symlink dangles and mkdir raises; log it and carry on
    rather than taking Guardian (cameras, detection) down with the image API.
    Requests then degrade via get_thumb's own error handling."""
    global _DATA_ROOT, _THUMB_CACHE
    _DATA_ROOT = Path(data_root)
    _THUMB_CACHE = _DATA_ROOT / "cache" / "thumbs"
    try:
        _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("thumb cache %s unavailable (SSD unmounted?): %s", _THUMB_CACHE, exc)


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
    """Returns (image_bytes, etag, media_type). size in {480, 720, 1920, 0};
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

    _pil_format, ext, media_type = _FORMATS[fmt]
    cache_key = sha256 or source.stem
    # JPEG keeps its historical filename and ETag so every cached thumb and
    # every browser-held ETag from before the WebP variant stays valid.
    suffix = f"{size}" if fmt == FORMAT_JPEG else f"{size}-{fmt}"
    cache_path = _THUMB_CACHE / f"{cache_key}-{suffix}.{ext}"
    etag = f'"{cache_key}-{suffix}"'
    if cache_path.exists():
        return cache_path.read_bytes(), etag, media_type
    try:
        _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        data = _generate_thumb(source, size, fmt)
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(cache_path)
        return data, etag, media_type
    except Exception as exc:
        log.warning("thumb generation failed for %s @ %d %s: %s", image_path_rel, size, fmt, exc)
        # Degrade gracefully: return the source JPEG bytes uncached. The
        # ETag gets a -raw suffix so a browser never caches the original
        # under the resized variant's validator.
        return source.read_bytes(), f'"{cache_key}-{suffix}-raw"', "image/jpeg"
