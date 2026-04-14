# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: Lazy thumbnail + JPEG bytes server for /api/v1/images/*/image.
#          Generates thumbnails on first request using Pillow, caches them
#          under data/cache/thumbs/{sha256}-{size}.jpg, serves cached copies
#          on subsequent hits. ETag is sha256-size so the client can
#          If-None-Match its way to 304s. If a row's image_path is NULL
#          (post-retention or skip tier that somehow got requested), serves
#          a 1-pixel gray placeholder JPEG with a short max-age so the UI
#          can render something instead of a broken <img>.
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


def configure(data_root: Path) -> None:
    """Called by register_api() so we resolve image paths relative to the
    right filesystem root (config-driven)."""
    global _DATA_ROOT, _THUMB_CACHE
    _DATA_ROOT = Path(data_root)
    _THUMB_CACHE = _DATA_ROOT / "cache" / "thumbs"
    _THUMB_CACHE.mkdir(parents=True, exist_ok=True)


def placeholder() -> tuple[bytes, dict]:
    """Bytes + headers for the 'metadata-only, image retained only as
    metadata' case. Short cache so a future retention-aware re-ingest
    can produce a real thumbnail."""
    return _PLACEHOLDER_BYTES, {
        "Cache-Control": "public, max-age=60",
        "ETag": '"placeholder-1"',
    }


def _resolve(image_path_rel: str) -> Path:
    """image_path in the DB is relative to data/; absolute paths also
    supported for historical rows."""
    p = Path(image_path_rel)
    return p if p.is_absolute() else _DATA_ROOT / p


def _generate_thumb(source: Path, size: int) -> bytes:
    """size=0 means 'serve the original bytes as-is' (full-resolution)."""
    if size <= 0:
        return source.read_bytes()
    with Image.open(source) as im:
        # Preserve orientation. LANCZOS is slow-but-good; we cache once and
        # serve forever so it only runs on first hit.
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        # RGB convert in case the source was ever saved in a different mode.
        im.convert("RGB").save(buf, format="JPEG", quality=80 if size <= 480 else 85)
        return buf.getvalue()


def get_thumb(sha256: Optional[str], image_path_rel: str, size: int) -> tuple[bytes, str]:
    """Returns (jpeg_bytes, etag). size in {480, 1920, 0}.
    sha256 may be None for very old rows; we fall back to the path as the
    cache key but still serve correctly."""
    if not image_path_rel:
        b, _ = placeholder()
        return b, '"placeholder-1"'
    source = _resolve(image_path_rel)
    if not source.exists():
        b, _ = placeholder()
        return b, '"placeholder-1"'
    if size <= 0:
        # Full-resolution: skip the cache entirely — one-to-one with the
        # archive JPEG, re-encoding would lose quality.
        etag = f'"{sha256 or source.name}-full"'
        return source.read_bytes(), etag

    cache_key = sha256 or source.stem
    cache_path = _THUMB_CACHE / f"{cache_key}-{size}.jpg"
    etag = f'"{cache_key}-{size}"'
    if cache_path.exists():
        return cache_path.read_bytes(), etag
    try:
        _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
        data = _generate_thumb(source, size)
        tmp = cache_path.with_suffix(".jpg.tmp")
        tmp.write_bytes(data)
        tmp.replace(cache_path)
        return data, etag
    except Exception as exc:
        log.warning("thumb generation failed for %s @ %d: %s", image_path_rel, size, exc)
        # Degrade gracefully: return the source bytes uncached.
        return source.read_bytes(), etag
