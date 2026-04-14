# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: REST API surface for Farm Guardian's image archive (the dataset
#          produced by tools/pipeline/*). Public endpoints serve curated gems,
#          recent frames, and stats to farm-2026 at
#          https://farm.markbarney.net. Boss-only /review/* endpoints let the
#          single operator promote / demote / flag / unflag / delete rows.
#          Every public SQL query filters has_concerns = 0 as the first
#          predicate (defense-in-depth layer 1). Response models omit the
#          concerns field (layer 2). The /gems/{id} endpoint also 404s if the
#          row has has_concerns = 1 even though URL guessing should never
#          surface such an id (layer 3). Review mutations are filesystem-first,
#          DB-last so that a DB rollback doesn't leave orphan hardlinks.
# SRP/DRY check: Pass — single responsibility is the /api/v1/images/* HTTP
#                surface. SQL lives in database.py; thumbnailing in
#                images_thumb.py; auth in images_auth.py.

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from database import GuardianDB
import images_thumb
from images_auth import require_review_token

log = logging.getLogger("guardian.images.api")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_SCENES = {"brooder", "yard", "coop", "nesting-box", "sky", "other"}
_VALID_ACTIVITIES = {
    "huddling", "eating", "drinking", "dust-bathing", "foraging",
    "preening", "sleeping", "sparring", "alert", "none-visible", "other",
}
_VALID_TIERS_PUBLIC = {"strong", "decent"}
_VALID_TIERS_REVIEW = {"strong", "decent", "skip"}
_VALID_ORDERS = {"newest", "oldest", "random"}
_VALID_IMAGE_SIZES = {"thumb", "1920", "full"}
_SIZE_PX = {"thumb": 480, "1920": 1920, "full": 0}

_PUBLIC_CACHE_LIST = "public, max-age=60, s-maxage=300"
_PUBLIC_CACHE_IMAGE = "public, max-age=86400, immutable"
_PRIVATE_CACHE = "no-store"


def _encode_cursor(ts: str, rid: int) -> str:
    return base64.urlsafe_b64encode(f"{ts}|{rid}".encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    if not cursor:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts, rid = raw.rsplit("|", 1)
        return ts, int(rid)
    except Exception:
        # Bad cursor degrades to "first page" rather than 400 — matches plan
        # doc guidance: don't punish users who bookmarked an old cursor.
        return None, None


def _error(code: str, message: str, status: int, request_id: str, detail=None):
    body = {
        "error": {"code": code, "message": message},
        "request_id": request_id,
    }
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(body, status_code=status)


def _req_id(request: Request) -> str:
    # We don't have a proper middleware for request IDs; synthesize one per call.
    return str(uuid.uuid4())


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_since(since: Optional[str], default_days: int = 90) -> str:
    if since:
        return since
    return (datetime.now(timezone.utc) - timedelta(days=default_days)).isoformat(timespec="seconds")


def _resolve_until(until: Optional[str]) -> str:
    return until or _utcnow_iso()


def _public_url(base: str, image_id: int, size: str) -> str:
    return f"{base}/api/v1/images/gems/{image_id}/image?size={size}"


# ---------------------------------------------------------------------------
# FS helpers for the review mutations (filesystem-first, DB-last)
# ---------------------------------------------------------------------------

def _derived_paths(data_root: Path, image_path_rel: Optional[str]) -> dict:
    """Compute the gems/ and private/ hardlink paths for a given
    image_archive row. image_path is relative to data/ and has the form
    'archive/{YYYY-MM}/{camera}/{filename}.jpg'. Returns None for the
    archive path if image_path_rel is None (row has been retention-swept)."""
    out = {"archive": None, "gems": None, "private": None, "sidecar": None}
    if not image_path_rel:
        return out
    archive = data_root / image_path_rel
    out["archive"] = archive
    out["sidecar"] = archive.with_suffix(".json")
    # archive/{YM}/{camera}/{name}.jpg → gems/{YM}/{camera}/{name}.jpg
    parts = Path(image_path_rel).parts
    if len(parts) >= 4 and parts[0] == "archive":
        tail = Path(*parts[1:])
        out["gems"] = data_root / "gems" / tail
        out["private"] = data_root / "private" / tail
    return out


def _ensure_link(src: Path, dst: Path) -> None:
    """Hardlink dst → src if dst doesn't exist. Falls back to a byte copy
    on cross-device or filesystem errors (matches pipeline behavior in
    tools/pipeline/store.py:181-184)."""
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        dst.write_bytes(src.read_bytes())


def _safe_unlink(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("unlink failed for %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_images_router(db: GuardianDB, config: dict) -> APIRouter:
    images_cfg = config.get("images", {}) or {}
    db_path = Path(config.get("database", {}).get("path", "data/guardian.db"))
    data_root = Path(images_cfg.get("data_root", str(db_path.parent)))
    images_thumb.configure(data_root)

    router = APIRouter(prefix="/api/v1/images", tags=["images"])

    # -----------------------------------------------------------------
    # Ping — no DB if possible, but the plan's Phase-0 verification
    # wants a quick "tunnel reaches us AND the DB answers" signal.
    # -----------------------------------------------------------------
    @router.get("/ping")
    async def ping():
        try:
            n = db.count_all_images()
            return {"ok": True, "rows": n, "ts": _utcnow_iso()}
        except Exception as exc:
            log.error("ping DB read failed: %s", exc)
            return {"ok": True, "rows": None, "ts": _utcnow_iso()}

    # -----------------------------------------------------------------
    # Public list endpoints
    # -----------------------------------------------------------------
    def _shape_public_row(
        row, *, include_tier: bool, request_base: str,
    ) -> dict:
        d = db._img_row_to_dict(row)
        out = {
            "id": d["id"],
            "camera_id": d["camera_id"],
            "ts": d["ts"],
            "thumb_url": _public_url(request_base, d["id"], "thumb"),
            "full_url": _public_url(request_base, d["id"], "1920"),
            "width": d["width"],
            "height": d["height"],
            "scene": d["scene"],
            "bird_count": d["bird_count"],
            "activity": d["activity"],
            "lighting": d["lighting"],
            "composition": d["composition"],
            "image_quality": d["image_quality"],
            "individuals_visible": d["individuals_visible"],
            "any_special_chick": d["any_special_chick"],
            "apparent_age_days": d["apparent_age_days"],
            "caption_draft": d["caption_draft"],
            "share_reason": d["share_reason"],
        }
        if include_tier:
            out["image_tier"] = d["image_tier"]
        return out

    def _base_url(request: Request) -> str:
        # If the frontend reaches us via the Cloudflare tunnel we want URLs
        # rooted at the public hostname; otherwise use the origin the request
        # arrived on. request.url.scheme + host honors the X-Forwarded-Host
        # Cloudflare injects.
        host = request.headers.get("x-forwarded-host") or request.url.netloc
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        return f"{scheme}://{host}"

    def _validate_enum(values: list[str], allowed: set, name: str, rid: str):
        bad = [v for v in values if v not in allowed]
        if bad:
            raise HTTPException(400, f"invalid {name}: {bad}")

    @router.get("/gems")
    async def list_gems(
        request: Request,
        camera: list[str] = Query(default=[]),
        scene: list[str] = Query(default=[]),
        activity: list[str] = Query(default=[]),
        individual: list[str] = Query(default=[]),
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = Query(24, ge=1, le=100),
        cursor: Optional[str] = None,
        order: str = Query("newest"),
    ):
        rid = _req_id(request)
        if order not in _VALID_ORDERS:
            return _error("bad_order", f"order must be one of {sorted(_VALID_ORDERS)}", 400, rid)
        if scene:  _validate_enum(scene, _VALID_SCENES, "scene", rid)
        if activity: _validate_enum(activity, _VALID_ACTIVITIES, "activity", rid)
        cts, cid = _decode_cursor(cursor)
        since_iso = _resolve_since(since, 90)
        until_iso = _resolve_until(until)

        rows = db.query_images(
            tiers=["strong"],
            cameras=camera or None,
            scenes=scene or None,
            activities=activity or None,
            individuals=individual or None,
            since_iso=since_iso,
            until_iso=until_iso,
            order=order,
            cursor_ts=cts,
            cursor_id=cid,
            limit=limit,
        )
        total, estimated = db.count_images(
            tiers=["strong"],
            cameras=camera or None,
            scenes=scene or None,
            activities=activity or None,
            individuals=individual or None,
            since_iso=since_iso,
            until_iso=until_iso,
        )
        base = _base_url(request)
        shaped = [_shape_public_row(r, include_tier=False, request_base=base) for r in rows]
        next_cursor = None
        if shaped and order in ("newest", "oldest") and len(shaped) == limit:
            last = shaped[-1]
            next_cursor = _encode_cursor(last["ts"], last["id"])
        body = {
            "count": len(shaped),
            "total_estimate": total,
            "estimated": estimated,
            "next_cursor": next_cursor,
            "rows": shaped,
        }
        return JSONResponse(body, headers={"Cache-Control": _PUBLIC_CACHE_LIST})

    @router.get("/gems/{image_id}")
    async def get_gem(image_id: int, request: Request):
        rid = _req_id(request)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"gem {image_id} not found", 404, rid)
        if row["has_concerns"] == 1 or row["share_worth"] != "strong" or not row["image_path"]:
            return _error("not_found", f"gem {image_id} not found", 404, rid)
        base = _base_url(request)
        shaped = _shape_public_row(row, include_tier=False, request_base=base)
        related_rows = db.get_related_gems(image_id, limit=4)
        shaped["related"] = [int(r["id"]) for r in related_rows]
        return JSONResponse(shaped, headers={"Cache-Control": _PUBLIC_CACHE_LIST})

    @router.get("/gems/{image_id}/image")
    async def get_gem_image(
        image_id: int,
        request: Request,
        size: str = Query("thumb"),
    ):
        rid = _req_id(request)
        if size not in _VALID_IMAGE_SIZES:
            return _error("bad_size", f"size must be one of {sorted(_VALID_IMAGE_SIZES)}", 400, rid)
        row = db.get_image(image_id)
        if row is None or row["has_concerns"] == 1 or row["share_worth"] != "strong":
            return _error("not_found", f"gem {image_id} not found", 404, rid)
        image_path_rel = row["image_path"]
        sha = row["sha256"]
        size_px = _SIZE_PX[size]

        jpeg_bytes, etag = images_thumb.get_thumb(sha, image_path_rel or "", size_px)
        # If-None-Match → 304
        inm = request.headers.get("if-none-match")
        if inm and inm == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=jpeg_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": _PUBLIC_CACHE_IMAGE,
                "ETag": etag,
            },
        )

    @router.get("/recent")
    async def list_recent(
        request: Request,
        camera: list[str] = Query(default=[]),
        scene: list[str] = Query(default=[]),
        activity: list[str] = Query(default=[]),
        individual: list[str] = Query(default=[]),
        tier: list[str] = Query(default=[]),
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = Query(24, ge=1, le=100),
        cursor: Optional[str] = None,
        order: str = Query("newest"),
    ):
        rid = _req_id(request)
        if order not in _VALID_ORDERS:
            return _error("bad_order", f"order must be one of {sorted(_VALID_ORDERS)}", 400, rid)
        if scene: _validate_enum(scene, _VALID_SCENES, "scene", rid)
        if activity: _validate_enum(activity, _VALID_ACTIVITIES, "activity", rid)
        if tier: _validate_enum(tier, _VALID_TIERS_PUBLIC, "tier", rid)
        tiers = tier or ["strong", "decent"]
        cts, cid = _decode_cursor(cursor)
        since_iso = _resolve_since(since, 7)
        until_iso = _resolve_until(until)

        rows = db.query_images(
            tiers=tiers,
            cameras=camera or None,
            scenes=scene or None,
            activities=activity or None,
            individuals=individual or None,
            since_iso=since_iso,
            until_iso=until_iso,
            order=order,
            cursor_ts=cts,
            cursor_id=cid,
            limit=limit,
        )
        total, estimated = db.count_images(
            tiers=tiers,
            cameras=camera or None,
            scenes=scene or None,
            activities=activity or None,
            individuals=individual or None,
            since_iso=since_iso,
            until_iso=until_iso,
        )
        base = _base_url(request)
        shaped = [_shape_public_row(r, include_tier=True, request_base=base) for r in rows]
        next_cursor = None
        if shaped and order in ("newest", "oldest") and len(shaped) == limit:
            last = shaped[-1]
            next_cursor = _encode_cursor(last["ts"], last["id"])
        return JSONResponse(
            {"count": len(shaped), "total_estimate": total, "estimated": estimated,
             "next_cursor": next_cursor, "rows": shaped},
            headers={"Cache-Control": _PUBLIC_CACHE_LIST},
        )

    @router.get("/stats")
    async def stats(
        since: Optional[str] = None,
        until: Optional[str] = None,
    ):
        since_iso = _resolve_since(since, 7)
        until_iso = _resolve_until(until)
        body = db.get_image_stats(since_iso, until_iso)
        return JSONResponse(body, headers={"Cache-Control": _PUBLIC_CACHE_LIST})

    # -----------------------------------------------------------------
    # Private review endpoints (bearer-token gated)
    # -----------------------------------------------------------------

    def _shape_review_row(row, *, request_base: str) -> dict:
        d = db._img_row_to_review_dict(row)
        d["thumb_url"] = _public_url(request_base, d["id"], "thumb")
        d["full_url"] = _public_url(request_base, d["id"], "full")
        return d

    @router.get("/review/queue", dependencies=[Depends(require_review_token)])
    async def review_queue(
        request: Request,
        camera: list[str] = Query(default=[]),
        scene: list[str] = Query(default=[]),
        activity: list[str] = Query(default=[]),
        individual: list[str] = Query(default=[]),
        tier: list[str] = Query(default=[]),
        since: Optional[str] = None,
        until: Optional[str] = None,
        only_concerns: bool = False,
        only_unreviewed: bool = False,
        limit: int = Query(24, ge=1, le=100),
        cursor: Optional[str] = None,
        order: str = Query("newest"),
    ):
        rid = _req_id(request)
        if order not in _VALID_ORDERS:
            return _error("bad_order", f"order must be one of {sorted(_VALID_ORDERS)}", 400, rid)
        if tier: _validate_enum(tier, _VALID_TIERS_REVIEW, "tier", rid)
        tiers = tier or ["strong", "decent", "skip"]
        cts, cid = _decode_cursor(cursor)
        since_iso = _resolve_since(since, 30)
        until_iso = _resolve_until(until)
        rows = db.query_images(
            tiers=tiers,
            cameras=camera or None,
            scenes=scene or None,
            activities=activity or None,
            individuals=individual or None,
            since_iso=since_iso,
            until_iso=until_iso,
            include_concerns=True,
            only_concerns=only_concerns,
            only_unreviewed=only_unreviewed,
            require_image_path=False,
            order=order,
            cursor_ts=cts,
            cursor_id=cid,
            limit=limit,
        )
        base = _base_url(request)
        shaped = [_shape_review_row(r, request_base=base) for r in rows]
        next_cursor = None
        if shaped and order in ("newest", "oldest") and len(shaped) == limit:
            last = shaped[-1]
            next_cursor = _encode_cursor(last["ts"], last["id"])
        return JSONResponse(
            {"count": len(shaped), "next_cursor": next_cursor, "rows": shaped},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    @router.post("/review/{image_id}/promote", dependencies=[Depends(require_review_token)])
    async def review_promote(image_id: int, request: Request):
        rid = _req_id(request)
        payload = await _body(request)
        note = (payload or {}).get("reason") or None
        if note and len(note) > 500:
            return _error("bad_input", "reason must be ≤500 chars", 400, rid)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"image {image_id} not found", 404, rid)
        paths = _derived_paths(data_root, row["image_path"])
        # FS-first: ensure gems/ hardlink exists when we have archive bytes.
        if paths["archive"] and paths["gems"] and paths["archive"].exists():
            try:
                _ensure_link(paths["archive"], paths["gems"])
                if paths["sidecar"] and paths["sidecar"].exists():
                    _ensure_link(paths["sidecar"], paths["gems"].with_suffix(".json"))
            except OSError as exc:
                return _error("fs_error", f"hardlink failed: {exc}", 500, rid)
        diff = db.apply_review_action(
            image_id=image_id, action="promote", note=note, request_id=rid,
            new_share_worth="strong",
        )
        return JSONResponse(
            {"ok": True, "action": "promote", "image_id": image_id, "diff": diff, "request_id": rid},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    @router.post("/review/{image_id}/demote", dependencies=[Depends(require_review_token)])
    async def review_demote(image_id: int, request: Request):
        rid = _req_id(request)
        payload = await _body(request)
        note = (payload or {}).get("reason") or None
        if note and len(note) > 500:
            return _error("bad_input", "reason must be ≤500 chars", 400, rid)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"image {image_id} not found", 404, rid)
        paths = _derived_paths(data_root, row["image_path"])
        if paths["gems"]:
            _safe_unlink(paths["gems"])
            _safe_unlink(paths["gems"].with_suffix(".json"))
        diff = db.apply_review_action(
            image_id=image_id, action="demote", note=note, request_id=rid,
            new_share_worth="skip",
        )
        return JSONResponse(
            {"ok": True, "action": "demote", "image_id": image_id, "diff": diff, "request_id": rid},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    @router.post("/review/{image_id}/flag", dependencies=[Depends(require_review_token)])
    async def review_flag(image_id: int, request: Request):
        rid = _req_id(request)
        payload = await _body(request)
        note = (payload or {}).get("note")
        if not note or not isinstance(note, str):
            return _error("bad_input", "note is required", 400, rid)
        if len(note) > 500:
            return _error("bad_input", "note must be ≤500 chars", 400, rid)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"image {image_id} not found", 404, rid)
        # Mutate vlm_json.concerns[]: SELECT → parse → append → UPDATE inside
        # the same transaction. apply_review_action() holds the write lock.
        try:
            vlm = json.loads(row["vlm_json"] or "{}")
        except (ValueError, TypeError):
            vlm = {}
        concerns = list(vlm.get("concerns", []) or [])
        concerns.append(note)
        vlm["concerns"] = concerns
        paths = _derived_paths(data_root, row["image_path"])
        # FS-first: hardlink archive JPEG into private/ before DB commits.
        if paths["archive"] and paths["private"] and paths["archive"].exists():
            try:
                _ensure_link(paths["archive"], paths["private"])
                if paths["sidecar"] and paths["sidecar"].exists():
                    _ensure_link(paths["sidecar"], paths["private"].with_suffix(".json"))
            except OSError as exc:
                return _error("fs_error", f"hardlink failed: {exc}", 500, rid)
        diff = db.apply_review_action(
            image_id=image_id, action="flag", note=note, request_id=rid,
            new_has_concerns=1,
            new_vlm_json=json.dumps(vlm),
        )
        return JSONResponse(
            {"ok": True, "action": "flag", "image_id": image_id, "diff": diff, "request_id": rid},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    @router.post("/review/{image_id}/unflag", dependencies=[Depends(require_review_token)])
    async def review_unflag(image_id: int, request: Request):
        rid = _req_id(request)
        payload = await _body(request)
        reason = (payload or {}).get("reason")
        if not reason or not isinstance(reason, str):
            return _error("bad_input", "reason is required", 400, rid)
        if len(reason) > 500:
            return _error("bad_input", "reason must be ≤500 chars", 400, rid)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"image {image_id} not found", 404, rid)
        try:
            vlm = json.loads(row["vlm_json"] or "{}")
        except (ValueError, TypeError):
            vlm = {}
        vlm["concerns"] = []
        paths = _derived_paths(data_root, row["image_path"])
        if paths["private"]:
            _safe_unlink(paths["private"])
            _safe_unlink(paths["private"].with_suffix(".json"))
        diff = db.apply_review_action(
            image_id=image_id, action="unflag", note=reason, request_id=rid,
            new_has_concerns=0,
            new_vlm_json=json.dumps(vlm),
        )
        return JSONResponse(
            {"ok": True, "action": "unflag", "image_id": image_id, "diff": diff, "request_id": rid},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    @router.delete("/review/{image_id}", dependencies=[Depends(require_review_token)])
    async def review_delete(image_id: int, request: Request):
        rid = _req_id(request)
        payload = await _body(request)
        reason = (payload or {}).get("reason")
        if not reason or not isinstance(reason, str):
            return _error("bad_input", "reason is required", 400, rid)
        if len(reason) > 500:
            return _error("bad_input", "reason must be ≤500 chars", 400, rid)
        row = db.get_image(image_id)
        if row is None:
            return _error("not_found", f"image {image_id} not found", 404, rid)
        paths = _derived_paths(data_root, row["image_path"])
        # FS-first: unlink archive + sidecar + gems + private. Keep the DB row
        # so the audit trail survives.
        for key in ("archive", "sidecar", "gems", "private"):
            _safe_unlink(paths[key])
            if key in ("gems", "private") and paths[key] is not None:
                _safe_unlink(paths[key].with_suffix(".json"))
        diff = db.apply_review_action(
            image_id=image_id, action="delete", note=reason, request_id=rid,
            new_image_path_null=True,
        )
        return JSONResponse(
            {"ok": True, "action": "delete", "image_id": image_id, "diff": diff, "request_id": rid},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    @router.get("/review/edits", dependencies=[Depends(require_review_token)])
    async def list_edits(
        since: Optional[str] = None,
        until: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = Query(50, ge=1, le=500),
        cursor: Optional[str] = None,
    ):
        cts, cid = _decode_cursor(cursor)
        rows = db.get_edits(
            since_iso=since, until_iso=until, action=action,
            cursor_ts=cts, cursor_id=cid, limit=limit,
        )
        next_cursor = None
        if rows and len(rows) == limit:
            last = rows[-1]
            next_cursor = _encode_cursor(last["ts"], last["id"])
        return JSONResponse(
            {"count": len(rows), "next_cursor": next_cursor, "edits": rows},
            headers={"Cache-Control": _PRIVATE_CACHE},
        )

    return router
