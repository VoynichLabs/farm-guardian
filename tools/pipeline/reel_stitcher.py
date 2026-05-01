# Author: Claude Opus 4.7 (1M context) / updated Claude Sonnet 4.6 01-May-2026
# Date: 20-April-2026
# PURPOSE: Stitch N Guardian gem JPEGs into a 9:16 MP4 suitable for
#          posting to Instagram as a Reel. Per-frame center-crop to
#          9:16 at the source's native height (no upscale), xfade
#          crossfade between frames, H.264 high@4.1 yuv420p, silent
#          AAC track (IG's fetcher occasionally rejects pure-video
#          files, even when the API accepts the container).
#
#          Pure stdlib + cv2 + ffmpeg subprocess. Both deps are already
#          in the pipeline: cv2 via store.py (image decode), ffmpeg as
#          a runtime dep via capture.py. No new Python packages.
#
#          Key design points:
#            - Single ffmpeg subprocess per stitch. Chains xfade filters
#              in one filter_complex expression so encode is one pass.
#            - All frames pre-cropped to the same resolution before
#              ffmpeg sees them (xfade can't handle a mid-reel
#              resolution change). Fleet cams are 1920x1080 or
#              1280x720 landscape; crops are 607x1080 or 405x720.
#            - If the input gem_ids span mixed-resolution cameras,
#              upscale smaller frames to match the largest. This is
#              the ONLY sanctioned upscale — callers are warned. Prefer
#              callers supply same-camera gem sets.
#            - Output MP4 is the cropped-native resolution. No upscale
#              to 1080x1920 — per docs/20-Apr-2026-ig-next-phases-plan.md
#              §3 "do NOT upscale to 1080×1920 — it looks worse than
#              the lower-res native crop."
#            - Audio: anullsrc silent, exact-duration sized. Pure-video
#              MP4s sometimes 400 at the IG container create step.
#            - Failures raise ReelStitcherError with actionable
#              messages. post_reel_to_ig catches; the pipeline never
#              breaks on a stitch failure.
#
# SRP/DRY check: Pass — single responsibility is "N gem_ids -> one
#                MP4 path." No Graph API, no git, no DB mutation. Reuses
#                store.resolve_gem_image_path (Phase 3b) for on-disk
#                lookups so ig_poster and this module share the same
#                path-math.

from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.pipeline.store import resolve_gem_image_path

log = logging.getLogger("pipeline.reel_stitcher")

# _MAX_FRAMES: 90 frames × 1s/frame − 89 × 0.15s xfade ≈ 77s, under
# Instagram's 90s reel limit. All reacted gems come through; no bucketing.
_MIN_FRAMES = 2
_MAX_FRAMES = 90

# Hard cap per frame after 9:16 crop. Any source larger than this
# (e.g. high-res discord-drop images) gets downscaled to fit. Keeps
# ffmpeg from trying to encode a 3000×5000+ frame times 31 inputs.
_MAX_REEL_WIDTH = 1080
_MAX_REEL_HEIGHT = 1920

_FRAME_JPEG_QUALITY = 92
_FFMPEG_TIMEOUT_S = 300


class ReelStitcherError(RuntimeError):
    """Raised when stitching fails (ffmpeg exit, missing source,
    unreadable JPEG, etc).

    Caller (post_reel_to_ig) catches this and bubbles it up in the
    `error` field of its result dict. Never escapes the CLI path.
    """


def _ffmpeg_path() -> str:
    """Resolve the ffmpeg binary path. Prefer PATH (brew-installed on
    this Mac Mini); fail loudly if missing rather than falling through
    to a default that won't exist on this host."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ReelStitcherError(
            "ffmpeg not found on PATH. Install with `brew install ffmpeg`."
        )
    return exe


def _pre_crop_frame(src: Path, dest: Path) -> tuple[int, int]:
    """Center-crop one JPEG to 9:16 at the source's native height.

    Same semantics as ig_poster._prepare_story_image, but writes to a
    caller-controlled destination (the stitcher controls the temp dir
    lifecycle). Returns (width, height) of the crop so the caller can
    detect mixed-resolution input sets.
    """
    # Local cv2 import — matches ig_poster's pattern so a bare CLI
    # invocation doesn't pay the cv2 cold-start on --help.
    import cv2
    img = cv2.imread(str(src))
    if img is None:
        raise ReelStitcherError(f"could not decode source JPEG: {src}")
    h, w = img.shape[:2]
    target_w = int(round(h * 9 / 16))
    if target_w < w:
        x0 = (w - target_w) // 2
        cropped = img[:, x0:x0 + target_w]
    else:
        # Source is already narrower than 9:16 — pad top/bottom with
        # black bars. Unlikely for this fleet (all 16:9), but harmless
        # and avoids surprising callers who feed portrait-mode JPEGs.
        target_h = int(round(w * 16 / 9))
        top = (target_h - h) // 2
        bottom = target_h - h - top
        cropped = cv2.copyMakeBorder(
            img, top, bottom, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0),
        )
    ok = cv2.imwrite(
        str(dest), cropped, [int(cv2.IMWRITE_JPEG_QUALITY), _FRAME_JPEG_QUALITY],
    )
    if not ok:
        raise ReelStitcherError(f"cv2.imwrite failed writing {dest}")
    hh, ww = cropped.shape[:2]
    return ww, hh


def _resize_frame(src: Path, dest: Path, target_w: int, target_h: int) -> None:
    """Resize src to (target_w, target_h) using INTER_LANCZOS4. Used to
    reconcile mixed-resolution input sets; callers guard with an
    equality check so this is skipped when dims already match."""
    import cv2
    img = cv2.imread(str(src))
    if img is None:
        raise ReelStitcherError(f"could not decode {src} for resize")
    h, w = img.shape[:2]
    if (w, h) == (target_w, target_h):
        if src != dest:
            shutil.copy2(src, dest)
        return
    resized = cv2.resize(img, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
    ok = cv2.imwrite(
        str(dest), resized, [int(cv2.IMWRITE_JPEG_QUALITY), _FRAME_JPEG_QUALITY],
    )
    if not ok:
        raise ReelStitcherError(f"cv2.imwrite failed writing resize to {dest}")


def _build_filter_complex(
    n_frames: int, seconds_per_frame: float, crossfade_seconds: float,
) -> str:
    """Build the ffmpeg -filter_complex expression for an N-frame xfade
    chain.

    For n_frames == 2: one xfade.
    For n_frames >= 3: N-1 chained xfades, each re-using the prior
                       chain's output label.

    Offsets are i*spf − i*xfade for i in [1, N−1] — computed so that
    frame i starts fading in at the moment frame i-1 has been visible
    for seconds_per_frame and starts its crossfade.
    """
    if n_frames < 2:
        # Defensive — caller enforces n_frames >= 2 upstream.
        return "[0:v]copy[v]"
    parts = []
    prev_out = "[0:v]"
    for i in range(1, n_frames):
        offset = i * seconds_per_frame - i * crossfade_seconds
        next_label = "[v]" if i == n_frames - 1 else f"[v{i:02d}]"
        parts.append(
            f"{prev_out}[{i}:v]xfade=transition=fade:"
            f"duration={crossfade_seconds}:offset={offset:.4f}{next_label}"
        )
        prev_out = next_label
    return ";".join(parts)


def stitch_gems_to_reel(
    gem_ids: list[int],
    db_path: Path,
    config: dict,
    output_path: Optional[Path] = None,
) -> Path:
    """Stitch N Guardian gem JPEGs into a 9:16 MP4 Reel. Returns the
    written MP4 path.

    Parameters:
      gem_ids
          Ordered list of image_archive ids (2-10). Order drives the
          reel's frame order.
      db_path
          Guardian SQLite DB path, used to resolve gem row -> JPEG path
          via store.resolve_gem_image_path.
      config
          Dict with the pipeline's instagram.reels block:
            output_root         (str/path, default "data/reels")
            seconds_per_frame   (float, default 1.0)
            crossfade_seconds   (float, default 0.15)
          Other keys ignored. If output_path is passed, output_root is
          ignored.
      output_path
          Explicit output MP4 path. If None, a stamped name under
          {output_root}/YYYY-MM/ is generated. Caller is responsible
          for ensuring output_root is absolute (relative paths resolve
          from CWD, which may not be the repo root).

    Raises:
      ReelStitcherError on any step's failure (bad config, missing
      gems, cv2 decode, ffmpeg exit). The caller (post_reel_to_ig or
      the CLI) catches and bubbles up.
    """
    n = len(gem_ids)
    if not (_MIN_FRAMES <= n <= _MAX_FRAMES):
        raise ReelStitcherError(
            f"gem_ids count {n} out of range [{_MIN_FRAMES}, {_MAX_FRAMES}]"
        )

    output_root = Path(config.get("output_root", "data/reels")).expanduser()
    seconds_per_frame = float(config.get("seconds_per_frame", 1.0))
    crossfade_seconds = float(config.get("crossfade_seconds", 0.15))

    if seconds_per_frame <= 0:
        raise ReelStitcherError(
            f"seconds_per_frame must be positive; got {seconds_per_frame}"
        )
    if not (0 <= crossfade_seconds < seconds_per_frame):
        raise ReelStitcherError(
            f"crossfade_seconds must be in [0, seconds_per_frame); "
            f"got {crossfade_seconds} with spf={seconds_per_frame}"
        )

    # Resolve gem ids -> on-disk JPEGs (preserving caller-specified order).
    jpeg_sources: list[Path] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        for gid in gem_ids:
            row = conn.execute(
                "SELECT id, image_path FROM image_archive WHERE id = ?", (gid,),
            ).fetchone()
            if not row:
                raise ReelStitcherError(f"gem_id {gid} not in image_archive")
            jpeg_sources.append(resolve_gem_image_path(dict(row), db_path))

    work_dir = Path(tempfile.mkdtemp(prefix="reel-stitch-"))
    try:
        # Pre-crop each JPEG to 9:16 native-height; collect dimensions.
        cropped_paths: list[Path] = []
        crop_dims: list[tuple[int, int]] = []
        for i, src in enumerate(jpeg_sources):
            dest = work_dir / f"frame-{i:02d}.jpg"
            cropped_paths.append(dest)
            w, h = _pre_crop_frame(src, dest)
            crop_dims.append((w, h))

        # Cap each frame at _MAX_REEL_WIDTH × _MAX_REEL_HEIGHT so a single
        # high-res source (e.g. large discord-drop) doesn't force every
        # other frame to be upscaled to a giant resolution.
        for i, (p, (w, h)) in enumerate(zip(cropped_paths, list(crop_dims))):
            if w > _MAX_REEL_WIDTH or h > _MAX_REEL_HEIGHT:
                scale = min(_MAX_REEL_WIDTH / w, _MAX_REEL_HEIGHT / h)
                new_w = int(w * scale) & ~1   # force even for H.264
                new_h = int(h * scale) & ~1
                log.info(
                    "reel_stitcher: frame %d capped %dx%d → %dx%d",
                    i, w, h, new_w, new_h,
                )
                _resize_frame(p, p, new_w, new_h)
                crop_dims[i] = (new_w, new_h)

        # Reconcile mixed resolutions by resizing smaller frames UP to
        # the largest. ffmpeg's xfade rejects mid-reel res changes, so
        # uniformity is required. Most common case: all s7 (1920x1080 ->
        # 607x1080) or all gwtc (1280x720 -> 405x720) and this is a no-op.
        target_w = max(d[0] for d in crop_dims)
        target_h = max(d[1] for d in crop_dims)
        if any(d != (target_w, target_h) for d in crop_dims):
            log.warning(
                "reel_stitcher: mixed gem resolutions detected; resizing "
                "smaller frames to %dx%d (one sanctioned upscale)",
                target_w, target_h,
            )
            for p, dims in zip(cropped_paths, crop_dims):
                if dims != (target_w, target_h):
                    _resize_frame(p, p, target_w, target_h)

        # Assemble the MP4 with one ffmpeg call.
        total_duration = n * seconds_per_frame - (n - 1) * crossfade_seconds
        filter_complex = _build_filter_complex(
            n, seconds_per_frame, crossfade_seconds,
        )

        if output_path is None:
            ym = datetime.now(timezone.utc).strftime("%Y-%m")
            out_dir = output_root / ym
            out_dir.mkdir(parents=True, exist_ok=True)
            stamped = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            slug = uuid.uuid4().hex[:8]
            output_path = out_dir / f"reel-{stamped}-{slug}.mp4"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [_ffmpeg_path(), "-y"]
        for p in cropped_paths:
            cmd += ["-loop", "1", "-t", f"{seconds_per_frame}", "-i", str(p)]
        audio_stream_idx = n  # audio is the Nth input (0-indexed after N images)
        cmd += [
            "-f", "lavfi",
            "-t", f"{total_duration:.4f}",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", f"{audio_stream_idx}:a",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-b:v", "3M",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(output_path),
        ]
        log.info(
            "reel_stitcher: invoking ffmpeg (n=%d, duration=%.2fs, %dx%d, out=%s)",
            n, total_duration, target_w, target_h, output_path,
        )
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise ReelStitcherError(
                f"ffmpeg exited rc={proc.returncode}\n"
                f"  stderr (tail): {proc.stderr[-500:].strip()}"
            )
    except subprocess.TimeoutExpired as e:
        raise ReelStitcherError(
            f"ffmpeg timed out after {_FFMPEG_TIMEOUT_S}s"
        ) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    log.info(
        "reel_stitcher: wrote %s (%d bytes)",
        output_path, output_path.stat().st_size,
    )
    return output_path
