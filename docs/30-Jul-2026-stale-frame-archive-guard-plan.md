# 30-Jul-2026 — Stop archiving stale frames from dead cameras (plan)

**Author:** Claude Fable 5
**Status:** ⏸ AWAITING APPROVAL — no code changed yet.
**Background:** [`docs/30-Jul-2026-reolink-s7-offline-incident.md`](30-Jul-2026-reolink-s7-offline-incident.md)

---

## The problem, with live evidence

When a camera dies, the pipeline keeps writing `image_archive` rows containing the same
cached image, forever. It is happening **right now**: `duo2` has been physically off the
network since ~10:20 and un-powered since the 10:50 power cycle, yet it is still accruing
archive rows at 6/minute.

Today's `duo2` frame history — the camera froze at 05:04:26 local:

| Frame | Dimensions | Rows | Span (UTC) |
|---|---|---|---|
| unique every cycle | 4608x1728 | 1 each | up to 09:04:16 — camera live |
| `a3a5dd2081` | 4608x1728 | **1867** | 09:04:26 → 14:18:24 |
| `655f61a9c4` | 2304x864 | **298+** | 14:18:31 → still growing |

Plus 32 duplicate `house-yard` rows (`99d6f60ff1`) during its outage. That is **~2,200 junk
rows and ~7 GB of duplicated JPEG** from a single day, and — worse — it makes an outage look
like a healthy camera. Nobody noticed `s7-cam` had drowned for nearly a day.

### Verified mechanism

- [`tools/pipeline/capture.py:45`](../tools/pipeline/capture.py) — `capture_via_guardian_api()`
  requests `/api/cameras/<name>/frame` with `allow_stale: bool = True` as its default.
- [`dashboard.py:268`](../dashboard.py) — that endpoint calls
  `get_latest_frame(name, allow_stale=allow_stale)` with **no maximum age**, so the last good
  frame is served indefinitely.
- [`capture.py:988`](../capture.py) — `get_latest_frame()` returns a `FrameResult`, which
  carries a `timestamp: float` (line 126). **The age signal we need already exists.**

`allow_stale` was added in v2.37.13 to ride out brief RTSP reconnect windows on GWTC. That
intent is still valid — the defect is only the absence of a ceiling.

### Which guard actually catches this (checked against the data, not assumed)

I verified both proposed mechanisms against the real rows rather than assuming:

- **Age bound — catches it.** During both frozen spans the ring buffer was not updating at
  all, so `FrameResult.timestamp` goes stale immediately.
- **Consecutive-hash equality — also catches it.** 1867 then 298 byte-identical rows.

They fail in different situations, so implement both: the age bound is the primary guard and
fires within one cycle; the hash check is a cheap backstop for any future case where a source
updates its buffer timestamp while delivering unchanged pixels. Neither is redundant.

## Scope

**In scope**

1. A `max_age` bound on the frame endpoint, opt-in so existing consumers are untouched.
2. The pipeline passing that bound and recording a **capture failure** instead of archiving.
3. A consecutive-duplicate-hash backstop at the archive-write boundary.
4. Log/digest visibility so a frozen camera is legible without reading SQL.

**Out of scope**

- Changing `allow_stale`'s default for the dashboard UI or the farm-2026 public site — they
  legitimately want a last-known-good image rather than a hole. Only the pipeline opts in.
- Deleting the ~2,200 existing junk rows. Proposed separately below; **destructive, needs
  explicit approval.**
- The discovery match-by-name / false-`online` bugs — separate task, separate plan.
- Anything about why the cameras died. That is hardware.

## Architecture

Reuse the bound the dashboard already computes rather than inventing one. `dashboard.py`
lines 186–205 (the camera-status endpoint) already derives:

```python
stale_after = max(30.0, 3.0 * interval)   # one missed cycle of slack, 30s floor
```

That is the correct shape and is already tuned to avoid flapping on 3-second cameras. Lift it
into a single shared helper so the status endpoint, the frame endpoint, and the pipeline all
agree — three copies of this constant is exactly the DRY violation to avoid.

**Responsibilities**

| Where | Change |
|---|---|
| `dashboard.py` | extract `stale_after_seconds(interval)` helper; add `max_age: float = 0` query param to `/api/cameras/{name}/frame`; when `max_age > 0` and the frame is older, return **409** (not 404 — "exists but too old" is distinct from "no frame ever") |
| `tools/pipeline/capture.py` | `capture_via_guardian_api()` gains `max_age`; passes it; maps 409 → `CaptureError` |
| pipeline runner | on `CaptureError`, record a capture failure — do not write an archive row |
| archive-write boundary | skip the insert when `sha256` equals the previous row for that camera; count as frozen-frame failure |

Default `max_age=0` means "no bound", so every existing caller behaves exactly as today.

## TODOs (ordered)

1. Extract the `stale_after` helper in `dashboard.py`; repoint the status endpoint at it.
2. Add `max_age` to `/api/cameras/{name}/frame`; return 409 when exceeded. Verify the
   dashboard UI and the farm-2026 live view are unaffected (they pass no `max_age`).
3. Thread `max_age` through `capture_via_guardian_api()` and the burst variant, derived from
   each camera's `cycle_seconds` in `tools/pipeline/config.json`.
4. Map 409 → `CaptureError` and confirm the runner records a failure without archiving.
5. Add the consecutive-duplicate-sha256 guard at the archive-write boundary.
6. Log a single line on entering and leaving the stale state — **not** per cycle; a frozen
   camera must not produce 6 log lines a minute. Surface frozen cameras in
   `scripts/pipeline-digest.py`.
7. **Verification (live, with a real fixture):**
   - `duo2` is *currently frozen and unreachable* — the ideal test case. With the change in,
     it must produce **zero** new archive rows.
   - `house-yard` is live at `.2` — it must be completely unaffected.
   - `mba-cam` (healthy, 90 unique frames/15 min) must be unaffected.
   - GWTC/`usb-cam` reconnect behaviour must still tolerate a brief blip: confirm a gap
     shorter than `3 × cycle` still yields a frame.
   - Confirm `https://guardian.markbarney.net` live view still serves frames.
8. Update `CHANGELOG.md` (minor version — behaviour change) and the incident doc.

## Risks

- **Over-tight bound blanks a working camera.** Mitigated by reusing the already-tuned
  `max(30, 3 × interval)` and by leaving the default unbounded.
- **A publishing lane that assumed a frame always exists** may now see a gap. Gaps are correct;
  worth grepping the reel/story runners for code that assumes continuity.
- **Fewer rows will look like a regression** in frame-count dashboards. It is the fix working.

## Follow-up needing separate approval (destructive)

~2,200 known-junk rows from today (1867 + 298 `duo2`, 32 `house-yard`) and their JPEGs. I would
rather **mark** them — e.g. set `ig_skip_reason`/a frozen flag — than delete, so the reel and
story lanes skip them while the evidence survives. Deleting rows or files needs an explicit
go-ahead; I will not do it as part of this change.

## Docs / changelog touchpoints

- `CHANGELOG.md` — top entry, SemVer minor, what/why/how, author.
- `CLAUDE.md` — one line under the pipeline notes: frame counts are not a liveness signal.
- Python file headers updated on every file touched (`dashboard.py`,
  `tools/pipeline/capture.py`, archive-write module).
