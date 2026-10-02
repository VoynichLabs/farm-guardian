# Author: Claude Opus 5.5
# Date: 02-October-2026
# PURPOSE: Decide from PIXELS whether a frame has a large out-of-focus patch,
#          so a bird jammed against the lens (inside the phone's minimum focus
#          distance) or smeared by motion stops being scored and posted as a
#          sharp gem. Local, deterministic, ~55 ms a frame, no VLM.
#
#          WHY THIS EXISTS (02-Oct-2026, Boss: "spamming me with photos that
#          are often blurry and out of focus ... That's not 92. That's like
#          50."). The frame he sent (17:16:42Z) is a rooster's head blurred to
#          a red smear across the top-left, and the VLM filed it as
#          image_quality=sharp, share_reason "Close rooster, sharp eye, facing
#          camera", score 92. That share_reason is a stock phrase: the 4b model
#          wrote it byte-for-byte on 522 of 1,958 s7 frames that day. Every
#          focus signal on the posting path (image_quality, bird_face_visible,
#          detail_score) is read out of that same rote answer, so no amount of
#          score re-weighting or floor-raising can fix it. The verdict has to
#          come from the pixels.
#
#          WHY NOT LAPLACIAN VARIANCE. It was tried twice (whole-frame, then
#          inside the YOLO box — see frame_selector.subject_laplacian) and
#          re-measured for this fix: inside-box Laplacian on the rejected
#          frames ran 134-3131, on crisp frames 156-1789. No separation.
#          Laplacian variance measures CONTRAST x sharpness, so wood chips
#          and wire mesh showing through a box swamp it, and a crisp
#          smooth-feathered bird scores lower than a blurred barred one.
#
#          THE MEASURE. Split the frame into a grid of square tiles (18 across
#          the short side). In each tile, edge sharpness is
#              p99(|gradient|) / (p95 - p5 of luminance)
#          i.e. how abruptly the tile's strongest edge changes, divided by how
#          much contrast the tile has. A crisp edge crosses its contrast in a
#          pixel or two (ratio ~0.3-0.5); a defocused one takes many (<0.2).
#          Dividing by contrast is what Laplacian variance lacks. A tile is
#            sharp  ratio >= SOFT_RATIO
#            soft   ratio <  SOFT_RATIO
#            flat   contrast too low to judge (sky, a white hen's breast,
#                   the inside of a defocused comb)
#          Flat is NOT evidence of blur by itself (an in-focus white hen is
#          mostly flat tiles ringed by sharp ones), but a defocused bird is
#          soft edges around a flat interior. So: take connected regions of
#          (soft OR flat) tiles and count only the SOFT tiles in the biggest
#          one. A white hen's region contains no soft tiles and scores 0.
#
#          THE BOWL. This camera has a permanently out-of-focus feed bowl
#          across the bottom of the frame. Without handling it, every blurry
#          region touching the bowl merges with it and the count is noise
#          (measured: no separation at all). Rather than hardcode where the
#          bowl is — Boss re-aims cameras — the judge keeps a slow running
#          average of each tile's "not sharp" state and ignores tiles that
#          are not sharp most of the time. It follows a re-aim by itself in
#          roughly an hour of frames, and a bird would have to hold still at
#          the lens for ~25 minutes of hot-cadence frames to be learned as
#          furniture.
#
#          MEASURED (02-Oct-2026, s7-cam, 266 post-eligible frames that day):
#          the count is bimodal, 153 frames at <12 tiles and 98 at >=20 (of a
#          31x17 = 527 tile grid), with
#          a thin valley between. Eight frames Boss would reject (his pasted
#          one included) scored 20-127; ten crisp ones scored 0-12 with one
#          exception at 56, which at full size is a hen visibly soft from
#          being too close. Checked at full resolution, not thumbnails: frames
#          above the line are soft, frames below are crisp. Stable across the
#          day (hourly median 1-5 tiles from dawn to dusk on 01-Oct), so it is
#          not a lighting artefact. Thresholds are a share of frame area so
#          they survive a resolution change: 3.0% is 16 of 527 tiles, in the
#          valley; 8.0% is 43. Replayed through this class in order, the two
#          days' post-eligible frames split 264 sharp / 61 soft / 82 blurred.
#
#          FAILS OPEN. Any exception, a cold static map, or a frame-shape
#          change returns verdict None and the caller leaves the VLM's answer
#          alone.
# SRP/DRY check: Pass — single responsibility is judging focus of one already
#                decoded frame. quality_gate.laplacian_variance and
#                frame_selector.subject_laplacian were checked first and
#                measure a different (and here non-discriminating) quantity;
#                neither is duplicated. No capture, no VLM, no DB.

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("pipeline.focus_check")

# Tiles across the frame's SHORT side. 18 gives 60 px tiles on a 1080-wide
# s7 frame: big enough to contain an edge, small enough to localise a comb.
GRID_SHORT_SIDE = 18
# p95-p5 luminance below this: tile too flat to have an edge worth measuring.
MIN_TILE_CONTRAST = 20.0
# Edge sharpness ratio below this is "soft". Crisp tiles sit at 0.3-0.5.
SOFT_RATIO = 0.2

# Share of the frame (percent of tiles) covered by soft tiles in the largest
# out-of-focus region. Derived from the 02-Oct-2026 distribution; see header.
DEFAULT_SOFT_AREA_PCT = 3.0      # 16 tiles of 527: demote to "soft"
DEFAULT_BLURRED_AREA_PCT = 8.0   # 43 tiles: demote to "blurred"

# Static (bowl) learning. A tile not-sharp in more than STATIC_LEVEL of
# recent frames is furniture and is ignored.
STATIC_ALPHA = 0.01
STATIC_LEVEL = 0.6
# No verdicts until the static map has seen this many frames; before that the
# bowl would be counted as a blurry bird on every frame.
WARMUP_FRAMES = 30
_PERSIST_EVERY = 20


@dataclass
class FocusVerdict:
    # "sharp" | "soft" | "blurred", or None when the judge abstains.
    verdict: str | None
    soft_area_pct: float = 0.0
    reason: str = ""


def tile_states(image_bgr) -> tuple[np.ndarray, np.ndarray]:
    """Return (soft, flat) boolean tile grids for one frame.

    Vectorised: the frame is cropped to a whole number of tiles and reshaped
    to (rows, tile, cols, tile) so the percentiles run across all tiles at
    once.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # A light blur first: sensor noise and JPEG ringing otherwise read as
    # single-pixel "edges" and make defocused tiles look crisp.
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    height, width = gray.shape
    tile = max(16, min(height, width) // GRID_SHORT_SIDE)
    rows, cols = (height - 1) // tile, (width - 1) // tile
    if rows < 2 or cols < 2:
        raise ValueError(f"frame too small for focus grid: {width}x{height}")

    grad_x = np.abs(np.diff(gray, axis=1))[:-1, :]
    grad_y = np.abs(np.diff(gray, axis=0))[:, :-1]
    gradient = np.maximum(grad_x, grad_y)

    def _tiles(plane: np.ndarray) -> np.ndarray:
        cropped = plane[: rows * tile, : cols * tile]
        return (cropped.reshape(rows, tile, cols, tile)
                .transpose(0, 2, 1, 3).reshape(rows, cols, tile * tile))

    luminance = _tiles(gray)
    low, high = np.percentile(luminance, (5, 95), axis=2)
    contrast = high - low
    strongest_edge = np.percentile(_tiles(gradient), 99, axis=2)

    flat = contrast < MIN_TILE_CONTRAST
    ratio = strongest_edge / np.maximum(contrast, 1e-6)
    soft = (~flat) & (ratio < SOFT_RATIO)
    return soft, flat


def largest_soft_region(soft: np.ndarray, flat: np.ndarray,
                        static: np.ndarray | None = None) -> int:
    """Soft-tile count of the biggest connected (soft OR flat) region, with
    static tiles removed. See the header for why flat tiles join regions but
    are not counted."""
    candidate = soft | flat
    if static is not None:
        candidate = candidate & ~static
    count, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=4)
    if count <= 1:
        return 0
    counted = soft if static is None else (soft & ~static)
    per_region = np.bincount(labels[counted], minlength=count)
    per_region[0] = 0  # label 0 is "not a candidate"
    return int(per_region.max())


class FocusJudge:
    """Per-camera focus judge. Holds the slow static (bowl) map and persists
    it so a pipeline restart does not cost a fresh warm-up."""

    def __init__(self, state_path: Path | None = None,
                 soft_area_pct: float = DEFAULT_SOFT_AREA_PCT,
                 blurred_area_pct: float = DEFAULT_BLURRED_AREA_PCT) -> None:
        self._state_path = state_path
        self._soft_area_pct = float(soft_area_pct)
        self._blurred_area_pct = float(blurred_area_pct)
        self._static_level: np.ndarray | None = None
        self._frames_seen = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            with np.load(self._state_path) as state:
                self._static_level = state["static_level"].astype(np.float32)
                self._frames_seen = int(state["frames_seen"])
        except Exception as exc:
            log.warning("focus: could not read %s (%s) — warming up from scratch",
                        self._state_path, exc)
            self._static_level, self._frames_seen = None, 0

    def _persist(self) -> None:
        if self._state_path is None or self._static_level is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            # np.savez appends .npz unless the name already ends with it, so
            # the temp name must end in .npz for the rename to find it.
            temp_path = self._state_path.with_name(self._state_path.stem + ".tmp.npz")
            np.savez(temp_path, static_level=self._static_level,
                     frames_seen=self._frames_seen)
            temp_path.replace(self._state_path)
        except Exception as exc:
            log.warning("focus: could not persist static map (%s)", exc)

    def judge(self, image_bgr) -> FocusVerdict:
        """Judge one frame and fold it into the static map. Never raises."""
        try:
            soft, flat = tile_states(image_bgr)
        except Exception as exc:
            log.warning("focus: measurement failed (%s) — abstaining", exc)
            return FocusVerdict(None, reason=f"measure_error: {exc}")

        not_sharp = (soft | flat).astype(np.float32)
        with self._lock:
            if self._static_level is None or self._static_level.shape != not_sharp.shape:
                # First frame ever, or the frame geometry changed (re-aim to
                # landscape, resolution change): the old map is meaningless.
                self._static_level, self._frames_seen = not_sharp.copy(), 0
            warmed = self._frames_seen >= WARMUP_FRAMES
            static = self._static_level > STATIC_LEVEL
            self._static_level = ((1.0 - STATIC_ALPHA) * self._static_level
                                  + STATIC_ALPHA * not_sharp)
            self._frames_seen += 1
            if self._frames_seen % _PERSIST_EVERY == 0:
                self._persist()

        if not warmed:
            return FocusVerdict(None, reason="warming_up")

        tiles = largest_soft_region(soft, flat, static)
        area_pct = round(100.0 * tiles / soft.size, 1)
        if area_pct >= self._blurred_area_pct:
            verdict = "blurred"
        elif area_pct >= self._soft_area_pct:
            verdict = "soft"
        else:
            verdict = "sharp"
        return FocusVerdict(verdict, soft_area_pct=area_pct)


_JUDGES: dict[str, FocusJudge] = {}
_JUDGES_LOCK = threading.Lock()


def judge_for(camera_name: str, focus_cfg: dict, state_dir: Path) -> FocusJudge:
    """Process-wide judge per camera, built from config on first use."""
    with _JUDGES_LOCK:
        judge = _JUDGES.get(camera_name)
        if judge is None:
            judge = FocusJudge(
                state_path=Path(state_dir) / f"focus-static-{camera_name}.npz",
                soft_area_pct=float(focus_cfg.get("soft_area_pct", DEFAULT_SOFT_AREA_PCT)),
                blurred_area_pct=float(focus_cfg.get("blurred_area_pct", DEFAULT_BLURRED_AREA_PCT)),
            )
            _JUDGES[camera_name] = judge
        return judge
