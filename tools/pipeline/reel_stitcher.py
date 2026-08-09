# Author: Claude Opus 4.7 (1M context) / updated Claude Sonnet 4.6 01-May-2026; Claude Sonnet 4.6 09-May-2026 — landscape mode for time-lapse camera lanes; Claude Opus 5 28-Jul-2026 — optional per-frame durations + duration guard; Claude Opus 5 09-Aug-2026 — stitch_frames_to_timelapse (dense fixed-fps path), v2.69.0
# Date: 20-April-2026; 28-Jul-2026 — per_frame_seconds (variable hold per frame); 09-Aug-2026 — dense time-lapse path
# PURPOSE: Stitch N Guardian gem JPEGs into an MP4 suitable for
#          posting to Instagram as a Reel.
#
#          TWO STITCH PATHS, picked by frame count:
#
#          stitch_gems_to_reel  — the xfade path. Tens of frames, each
#            held ~1s, crossfaded. One ffmpeg input + one filter per
#            frame. Used by every reaction-gated and per-camera lane.
#
#          stitch_frames_to_timelapse — the DENSE path (09-Aug-2026).
#            Hundreds of frames at a fixed 18fps, no transitions, via
#            ffmpeg's image2 demuxer over a numbered sequence. Used by
#            the house-yard/duo2 weekly + monthly lanes, which need
#            continuous motion rather than a slideshow. See v2.69.0.
#
#          Both share the same framing helpers and output contract, so
#          a lane switches between them without any other change.
#
#          Two output modes, common to both paths:
#
#          Portrait (default, landscape=False): center-crop each frame
#          to 9:16 at the source's native height. Used by all reaction-
#          gated and s7-cam lanes whose content is portrait-native or
#          cropped from portrait sources.
#
#          Landscape (landscape=True): scale each frame to fit 1920×1080
#          with black bars for any AR deviation. Used by time-lapse lanes
#          for vlm_bypass cameras (mba-cam, gwtc, usb-cam, dominator-cam)
#          which capture 16:9 frames that must not be center-cropped to a
#          405×720 strip.
#
#          Common to both modes: xfade crossfade between frames, H.264
#          high@4.1 yuv420p, silent AAC track (IG's fetcher occasionally
#          rejects pure-video files, even when the API accepts the
#          container). Pure stdlib + cv2 + ffmpeg subprocess.
#
#          Key design points:
#            - Single ffmpeg subprocess per stitch. Chains xfade filters
#              in one filter_complex expression so encode is one pass.
#            - All frames pre-processed to the same resolution before
#              ffmpeg sees them (xfade can't handle a mid-reel
#              resolution change). Fleet cams are 1920x1080 or
#              1280x720 landscape; portrait crops are 607x1080 or
#              405x720; landscape output is always 1920x1080.
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
#            - per_frame_seconds (28-Jul-2026, optional): give individual
#              frames a longer hold. When None every existing caller gets
#              byte-identical behaviour. When supplied it MUST drive all
#              three of (a) the cumulative xfade offsets, (b) each image
#              input's own -t, and (c) the silent audio track length.
#              Changing only (a) makes the crossfade run off the end of a
#              1.0s input that was meant to hold 1.8s -> black frames.
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

# _MAX_TIMELAPSE_FRAMES: the cap for the DENSE path (stitch_frames_to_timelapse)
# only. Deliberately a separate constant from _MAX_FRAMES — ig_selection imports
# _MAX_FRAMES and the S7 daily lane budgets its per-frame gem holds against it,
# so raising that one would silently restretch reels this change has no business
# touching. 900 frames at 18 fps is 50s, comfortably inside _MAX_REEL_SECONDS.
_MAX_TIMELAPSE_FRAMES = 900

# _TIMELAPSE_FPS: playback rate for the dense path. At 18 fps each frame is on
# screen for 0.056s, so a week of 5-minute daylight captures reads as continuous
# motion rather than a slideshow that lingers and cuts. This is the fix for the
# 09-Aug-2026 complaint that the weekly Reolink reels were "choppy" — 17 frames
# held 1.8s each with five real hours between consecutive shots.
_TIMELAPSE_FPS = 18.0

# _MAX_REEL_SECONDS: the frame cap above is really a DURATION proxy, and
# that equivalence only holds while every frame is the same length. Once
# per_frame_seconds is in play, 90 frames no longer implies ~77s, so the
# real limit has to be checked directly. This is a hard backstop that
# raises — callers that can trim intelligently (the S7 daily lane knows
# which frames are reacted gems and which are droppable filler) must fit
# the budget themselves before calling.
_MAX_REEL_SECONDS = 77.0

# Hard cap per frame after 9:16 crop (portrait mode). Any source larger
# than this gets downscaled to fit. Keeps ffmpeg from encoding huge frames.
_MAX_REEL_WIDTH = 1080
_MAX_REEL_HEIGHT = 1920

# Hard cap for landscape mode (time-lapse vlm_bypass cameras).
# _pre_fit_landscape_frame always outputs exactly this resolution, so the
# cap is effectively a no-op, but it's here for consistency.
_MAX_LANDSCAPE_WIDTH = 1920
_MAX_LANDSCAPE_HEIGHT = 1080

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


def _pre_fit_landscape_frame(src: Path, dest: Path) -> tuple[int, int]:
    """Scale one JPEG to fit within 1920×1080 preserving aspect ratio.

    Landscape (16:9) sources fill the frame exactly; portrait or unusual
    AR sources receive black bars. Used by time-lapse lanes for cameras
    that capture 16:9 frames (mba-cam 1280×720, usb-cam/dominator-cam
    1920×1080). Returns (width, height) of the output, which is always
    (_MAX_LANDSCAPE_WIDTH, _MAX_LANDSCAPE_HEIGHT) = (1920, 1080).
    """
    import cv2
    img = cv2.imread(str(src))
    if img is None:
        raise ReelStitcherError(f"could not decode source JPEG: {src}")
    h, w = img.shape[:2]
    scale = min(_MAX_LANDSCAPE_WIDTH / w, _MAX_LANDSCAPE_HEIGHT / h)
    new_w = int(w * scale) & ~1   # force even for H.264
    new_h = int(h * scale) & ~1
    if (new_w, new_h) != (w, h):
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        h, w = img.shape[:2]
    # Pad to exact 1920×1080 with black bars if needed.
    if w < _MAX_LANDSCAPE_WIDTH or h < _MAX_LANDSCAPE_HEIGHT:
        top = (_MAX_LANDSCAPE_HEIGHT - h) // 2
        bottom = _MAX_LANDSCAPE_HEIGHT - h - top
        left = (_MAX_LANDSCAPE_WIDTH - w) // 2
        right = _MAX_LANDSCAPE_WIDTH - w - left
        img = cv2.copyMakeBorder(
            img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0),
        )
    ok = cv2.imwrite(
        str(dest), img, [int(cv2.IMWRITE_JPEG_QUALITY), _FRAME_JPEG_QUALITY],
    )
    if not ok:
        raise ReelStitcherError(f"cv2.imwrite failed writing {dest}")
    hh, ww = img.shape[:2]
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


def compute_reel_duration(
    per_frame_seconds: list[float], crossfade_seconds: float,
) -> float:
    """Total playing time of an xfade chain with the given frame holds.

    Each of the N-1 crossfades overlaps two frames, so the overlap is
    reclaimed once per transition. Lives here (rather than in the caller)
    so the runner's trim-to-fit loop and the stitcher's own guard cannot
    drift apart on the formula.
    """
    n = len(per_frame_seconds)
    if n == 0:
        return 0.0
    return sum(per_frame_seconds) - (n - 1) * crossfade_seconds


def _build_filter_complex(
    n_frames: int,
    seconds_per_frame: float,
    crossfade_seconds: float,
    per_frame_seconds: Optional[list[float]] = None,
) -> str:
    """Build the ffmpeg -filter_complex expression for an N-frame xfade
    chain.

    For n_frames == 2: one xfade.
    For n_frames >= 3: N-1 chained xfades, each re-using the prior
                       chain's output label.

    Uniform case (per_frame_seconds None): offsets are i*spf − i*xfade for
    i in [1, N−1] — frame i starts fading in at the moment frame i-1 has
    been visible for seconds_per_frame and starts its crossfade.

    Variable case: the same rule, but "how long the earlier frames have
    been visible" is the running SUM of their individual holds rather than
    i*spf. Offsets are therefore cumulative; they cannot be derived from
    the index alone.
    """
    if n_frames < 2:
        # Defensive — caller enforces n_frames >= 2 upstream.
        return "[0:v]copy[v]"
    holds = per_frame_seconds or [seconds_per_frame] * n_frames
    parts = []
    prev_out = "[0:v]"
    elapsed = 0.0
    for i in range(1, n_frames):
        elapsed += holds[i - 1]
        offset = elapsed - i * crossfade_seconds
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
    landscape: bool = False,
    per_frame_seconds: Optional[list[float]] = None,
) -> Path:
    """Stitch N Guardian gem JPEGs into an MP4 Reel. Returns the written
    MP4 path.

    Parameters:
      gem_ids
          Ordered list of image_archive ids (2-90). Order drives the
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
      landscape
          When True, output a 16:9 (1920×1080) MP4 by scaling frames to
          fit within 1920×1080 with black bars, rather than center-cropping
          to 9:16. Use for time-lapse lanes on mba-cam, gwtc, usb-cam,
          and dominator-cam which capture 16:9 frames.
      per_frame_seconds
          Optional per-frame hold times, one per gem_id, in the same
          order. None (the default, and what every pre-28-Jul-2026 caller
          passes) keeps the flat seconds_per_frame behaviour exactly.
          Supplied, it lets a lane hold chosen frames longer — the S7
          daily Reel gives Discord-reacted gems a longer beat than the
          un-reacted filler around them.

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

    # Resolve the per-frame hold list once; everything downstream (input
    # -t, xfade offsets, audio length, duration guard) reads from this.
    if per_frame_seconds is None:
        holds = [seconds_per_frame] * n
    else:
        holds = [float(s) for s in per_frame_seconds]
        if len(holds) != n:
            raise ReelStitcherError(
                f"per_frame_seconds has {len(holds)} entries but there are "
                f"{n} gem_ids; they must correspond one-to-one"
            )
        # Every hold must exceed the crossfade, or that frame is fully
        # consumed by its own transition and never actually shows.
        if min(holds) <= crossfade_seconds:
            raise ReelStitcherError(
                f"every per_frame_seconds value must exceed crossfade_seconds="
                f"{crossfade_seconds}; got minimum {min(holds)}"
            )

    total_duration = compute_reel_duration(holds, crossfade_seconds)
    if total_duration > _MAX_REEL_SECONDS:
        if per_frame_seconds is None:
            # Uniform callers predate this guard and are governed by
            # _MAX_FRAMES. growth_timelapse runs 1.2s/frame and can legally
            # reach 90 frames (~90.2s), so hard-failing here would break a
            # working lane rather than protect it. Warn and proceed —
            # existing behaviour is unchanged by construction.
            log.warning(
                "reel_stitcher: %d frames at %.2fs run %.1fs, over the "
                "%.0fs budget (IG's Reel limit is 90s). Proceeding — uniform "
                "callers are capped by _MAX_FRAMES, not by duration.",
                n, seconds_per_frame, total_duration, _MAX_REEL_SECONDS,
            )
        else:
            raise ReelStitcherError(
                f"reel would run {total_duration:.1f}s, over the "
                f"{_MAX_REEL_SECONDS}s budget (Instagram's Reel limit is 90s). "
                f"Trim frames before calling — the stitcher will not silently "
                f"drop them, because only the caller knows which frames matter."
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
        # Pre-process each JPEG; collect dimensions.
        # Portrait mode: center-crop to 9:16 at native height.
        # Landscape mode: scale to fit 1920×1080 with black bars.
        cropped_paths: list[Path] = []
        crop_dims: list[tuple[int, int]] = []
        for i, src in enumerate(jpeg_sources):
            dest = work_dir / f"frame-{i:02d}.jpg"
            cropped_paths.append(dest)
            if landscape:
                w, h = _pre_fit_landscape_frame(src, dest)
            else:
                w, h = _pre_crop_frame(src, dest)
            crop_dims.append((w, h))

        # Cap per frame. In landscape mode, _pre_fit_landscape_frame already
        # outputs 1920×1080, so this is a no-op but kept for consistency.
        max_cap_w = _MAX_LANDSCAPE_WIDTH if landscape else _MAX_REEL_WIDTH
        max_cap_h = _MAX_LANDSCAPE_HEIGHT if landscape else _MAX_REEL_HEIGHT
        for i, (p, (w, h)) in enumerate(zip(cropped_paths, list(crop_dims))):
            if w > max_cap_w or h > max_cap_h:
                scale = min(max_cap_w / w, max_cap_h / h)
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
        filter_complex = _build_filter_complex(
            n, seconds_per_frame, crossfade_seconds, holds,
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
        # Each input's own -t must match its hold. If the offsets stretch
        # but the inputs stay 1.0s, the xfade reads past the end of a clip
        # that was supposed to hold longer and the render goes black there.
        for p, hold in zip(cropped_paths, holds):
            cmd += ["-loop", "1", "-t", f"{hold}", "-i", str(p)]
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


def _resolve_gem_jpegs(gem_ids: list[int], db_path: Path) -> list[Path]:
    """gem_ids -> on-disk JPEG paths, preserving caller order.

    Extracted 09-Aug-2026 so stitch_gems_to_reel (xfade path) and
    stitch_frames_to_timelapse (dense path) share one lookup instead of
    duplicating the SELECT + resolve_gem_image_path pairing.
    """
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
    return jpeg_sources


def stitch_frames_to_timelapse(
    gem_ids: list[int],
    db_path: Path,
    output_path: Path,
    landscape: bool = True,
    fps: float = _TIMELAPSE_FPS,
) -> Path:
    """Stitch MANY frames into a continuous-motion time-lapse MP4.

    The dense sibling of stitch_gems_to_reel, added 09-Aug-2026 for the
    house-yard/duo2 weekly + monthly lanes. Boss's complaint was that those
    reels were "choppy" — they held each shot ~1.8s and cut between frames
    captured hours apart. The cure is many frames played fast, which the
    xfade path structurally cannot do:

      - stitch_gems_to_reel spends one ffmpeg `-i` and one chained xfade
        filter PER FRAME. That is fine at 17 frames and untenable at 900
        (nine hundred decoders in one filter graph).
      - A crossfade is meaningless at 0.056s/frame anyway — the fade would
        occupy most of every frame's screen time and read as mush.

    So this path uses ffmpeg's image2 demuxer over a numbered sequence: one
    input, one decode, fixed frame rate, no transitions. Frames are still
    pre-processed through the same _pre_fit_landscape_frame / _pre_crop_frame
    helpers, so framing matches the xfade path exactly.

    Parameters:
      gem_ids     Ordered image_archive ids (oldest-first for a time-lapse).
                  Capped at _MAX_TIMELAPSE_FRAMES; callers subsample first.
      db_path     Guardian SQLite DB, for id -> JPEG path resolution.
      output_path Destination MP4. Parent dirs are created.
      landscape   True -> 1920x1080 fit; False -> 9:16 center-crop.
      fps         Playback rate. Duration is len(gem_ids)/fps, checked
                  against _MAX_REEL_SECONDS.

    Raises ReelStitcherError on any failure — same contract as the xfade
    path, so post_reel_to_ig's existing handler covers both.
    """
    n = len(gem_ids)
    if n < _MIN_FRAMES:
        raise ReelStitcherError(
            f"need at least {_MIN_FRAMES} frames for a time-lapse; got {n}"
        )
    if n > _MAX_TIMELAPSE_FRAMES:
        raise ReelStitcherError(
            f"{n} frames exceeds the {_MAX_TIMELAPSE_FRAMES}-frame time-lapse "
            f"cap. Subsample in the selector — only it knows how to keep the "
            f"span even, and silently dropping the tail would turn a month "
            f"into a week."
        )
    if fps <= 0:
        raise ReelStitcherError(f"fps must be positive; got {fps}")

    duration = n / fps
    if duration > _MAX_REEL_SECONDS:
        raise ReelStitcherError(
            f"{n} frames at {fps:g}fps runs {duration:.1f}s, over the "
            f"{_MAX_REEL_SECONDS}s budget (Instagram's Reel limit is 90s)."
        )

    jpeg_sources = _resolve_gem_jpegs(gem_ids, db_path)

    work_dir = Path(tempfile.mkdtemp(prefix="reel-timelapse-"))
    try:
        # Pre-process every frame to identical dimensions. image2 is stricter
        # than xfade here: a mid-sequence resolution change makes the demuxer
        # fail outright rather than warn.
        dims: list[tuple[int, int]] = []
        for i, src in enumerate(jpeg_sources):
            dest = work_dir / f"f{i:05d}.jpg"
            if landscape:
                dims.append(_pre_fit_landscape_frame(src, dest))
            else:
                dims.append(_pre_crop_frame(src, dest))

        target_w = max(d[0] for d in dims)
        target_h = max(d[1] for d in dims)
        for i, d in enumerate(dims):
            if d != (target_w, target_h):
                frame = work_dir / f"f{i:05d}.jpg"
                _resize_frame(frame, frame, target_w, target_h)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Silent AAC track for the same reason the xfade path carries one:
        # IG's fetcher intermittently rejects pure-video MP4s.
        cmd = [
            _ffmpeg_path(), "-y",
            "-framerate", f"{fps:g}",
            "-i", str(work_dir / "f%05d.jpg"),
            "-f", "lavfi",
            "-t", f"{duration:.4f}",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            # Encode at 30fps regardless of capture rate: IG re-encodes
            # anything non-standard, and 18fps source -> 30fps output is a
            # clean frame duplication rather than a resample.
            "-r", "30",
            "-b:v", "6M",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ]
        log.info(
            "reel_stitcher: time-lapse ffmpeg (n=%d, %.1fs at %gfps, %dx%d, "
            "out=%s)", n, duration, fps, target_w, target_h, output_path,
        )
        # Dense encodes are far heavier than a 17-frame xfade; give ffmpeg
        # proportionally more room than _FFMPEG_TIMEOUT_S allows.
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S * 4,
        )
        if proc.returncode != 0:
            raise ReelStitcherError(
                f"ffmpeg exited rc={proc.returncode}\n"
                f"  stderr (tail): {proc.stderr[-500:].strip()}"
            )
    except subprocess.TimeoutExpired as e:
        raise ReelStitcherError(
            f"time-lapse ffmpeg timed out after {_FFMPEG_TIMEOUT_S * 4}s"
        ) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    log.info(
        "reel_stitcher: wrote time-lapse %s (%d bytes)",
        output_path, output_path.stat().st_size,
    )
    return output_path
