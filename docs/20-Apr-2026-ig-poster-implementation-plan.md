# `tools/pipeline/ig_poster.py` — V2 implementation plan (20-Apr-2026)

**Author:** Claude Opus 4.7 (Bubba's resident agent on the Mac Mini)
**Status:** Plan — ready for approval, then phased build
**Cross-refs:** `docs/19-Apr-2026-instagram-posting-plan.md` (the V2 architecture doc) · `~/bubba-workspace/skills/farm-instagram-post/SKILL.md` · `~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md`

---

## Scope

**In:**
- `tools/pipeline/ig_poster.py` — Graph-API poster module
- `tools/pipeline/hashtags.yml` — curated tag library (verified tags only per the 2026-04-20 research pass)
- `tools/pipeline/git_helper.py` — small helper for `git add/commit/push` into `farm-2026` (new pattern; no existing repo precedent)
- DB schema additions in `tools/pipeline/store.py`: `ig_permalink`, `ig_posted_at`, `ig_skip_reason`
- `tools/pipeline/config.json` gains an `instagram: {...}` section; `config.example.json` at repo root gets an example
- `scripts/ig-post.py` — stdlib-only CLI wrapper mirroring the `scripts/add-camera.py` shape
- Orchestrator hook in `orchestrator.py:run_cycle()` — behind `instagram.enabled` config flag (default **false**)
- `.env` sourcing: the orchestrator already calls `load_dotenv(".env")`. We extend it to ALSO source `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` if it exists, so Meta tokens surface as `os.environ` values — matches the repo's existing env-var convention without duplicating secrets.
- CHANGELOG entry (v2.29.0 probable)

**Out (this plan — follow-up work):**
- Discord-reaction approval flow (V2.1)
- LaunchAgent `com.farmguardian.ig-poster.plist` for 4x/day cadence (V2.2)
- Reel stitching (V3)
- Auto-embedding @pawel_and_pawleen on farm-2026 homepage

**V2.0 ships as:** a manually-invokable CLI that posts a specified gem with a provided caption, goes through the existing farm-2026-commit-and-push flow to get a GitHub-raw-URL image origin, then Graph API create+publish, then writes `ig_permalink` back to the gem row. No auto-posting until Boss explicitly enables it.

---

## File plan

### New files

| Path | Purpose |
|---|---|
| `tools/pipeline/ig_poster.py` | Graph API client; `post_gem_to_ig()`, `should_post_ig()`, `pick_hashtags()`, helpers |
| `tools/pipeline/hashtags.yml` | The verified-tag library from `docs/19-Apr-2026-instagram-posting-plan.md` §hashtag library |
| `tools/pipeline/git_helper.py` | `commit_image_to_farm_2026(local_path, subdir) -> str (raw_url)` helper |
| `scripts/ig-post.py` | stdlib CLI: `--gem-id N --caption "..." --dry-run` |

### Modified files

| Path | Change |
|---|---|
| `tools/pipeline/store.py` | Add `ig_permalink`, `ig_posted_at`, `ig_skip_reason` to `_SCHEMA_SQL`; idempotent via `CREATE TABLE IF NOT EXISTS` + tolerant `ALTER TABLE` block for pre-existing tables |
| `tools/pipeline/orchestrator.py` | Insert post-Discord hook at ~line 248 behind `cfg["instagram"]["enabled"]`; extend `.env` loading to also source `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` |
| `tools/pipeline/config.json` | Add `instagram: {enabled: false, manual_approval: true, hashtag_library_path: "hashtags.yml", farm_2026_repo_path: "/Users/macmini/Documents/GitHub/farm-2026"}` |
| `config.example.json` (repo root) | Mirror the instagram section for reference |
| `CHANGELOG.md` | v2.29.0 top entry with what/why/how + Round-trip verified |
| `CLAUDE.md` | Update the "Operational skills" bullet to reflect that V2.0 has landed |

### Untouched

- `guardian.py`, `alerts.py`, `detect.py` — no changes; Instagram is pipeline-only, not Guardian-core
- farm-2026 repo — no code changes; just receives photo commits via `git_helper.py`
- The `.env` file itself — NOT committing tokens there; we source the already-existing `bubba-workspace/secrets/farm-guardian-meta.env`

---

## Module structure: `tools/pipeline/ig_poster.py`

```python
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Post curated gems to Instagram @pawel_and_pawleen via Meta Graph API.
#          Parallels gem_poster.py (Discord). Token/ID loading from .env (sourced
#          from bubba-workspace/secrets/farm-guardian-meta.env at orchestrator
#          startup). Image hosting via farm-2026/public/photos/ → GitHub raw URL
#          (IG's media fetcher rejects guardian.markbarney.net paths). Failures
#          here must NEVER break the pipeline cycle — fire-and-log.
# SRP/DRY check: Pass — SRP is building the Graph API container+publish flow.
#                DRY — reuses load_dotenv from gem_poster.py, picks hashtags
#                from hashtags.yml, commits via git_helper.py.

# Public surface
def post_gem_to_ig(
    gem_id: int,
    caption: str,
    db_path: Path,
    farm_2026_repo_path: Path,
    hashtag_library_path: Path,
    dry_run: bool = False,
    override_hashtags: list[str] | None = None,
    skip_hashtags: bool = False,
) -> dict:
    """Full flow: look up gem row → copy image to farm-2026 → git commit+push →
    get raw URL → pick hashtags → build full caption → Graph API create+publish
    → write ig_permalink back to the gem row.

    Returns: {
      "media_id": str | None,     # None if dry_run or failure
      "permalink": str | None,
      "raw_url": str,
      "caption_full": str,         # what was actually posted
      "hashtags": list[str],
      "dry_run": bool,
      "error": str | None,
    }
    """

def should_post_ig(vlm_metadata: dict, gem_row: dict, last_post_ts: str | None) -> tuple[bool, str]:
    """Predicate gate. Returns (should_post, reason).
    Criteria:
    - tier == "strong"
    - image_quality in {"sharp"}  (NOT decent/soft — IG is public-facing; higher bar than Discord)
    - bird_count >= 1
    - no other IG post for this camera_id in the last 12h (scene dedup)
    - no other IG post at all in the last 3h (overall cadence dedup; separate from the 4x/day scheduler)
    """

def pick_hashtags(vlm_metadata: dict, library: dict, last_n_tags_used: list[str]) -> list[str]:
    """Select 8-10 tags from the hashtag library based on gem metadata.
    Weighted toward long-tail (4-5 long-tail + 3-4 mid-tier + 1-2 top-tier).
    Deduplicates against the last N tag sets used to force rotation.
    Never includes creator-branded tags (#markbarney*, #builtwithai, etc.)."""

# Internal helpers
def _load_credentials() -> dict:
    """Returns {"ig_id", "user_token", "app_id", "app_secret"}.
    Reads from os.environ (sourced from bubba-workspace/secrets/farm-guardian-meta.env).
    Raises RuntimeError with a clear message if any are missing."""

def _create_container(ig_id, image_url, caption, token) -> str:
    """POST /v21.0/{ig_id}/media. Returns container id. Raises on failure."""

def _publish(ig_id, container_id, token) -> dict:
    """POST /v21.0/{ig_id}/media_publish. Returns {media_id, permalink, timestamp}."""

def _wait_for_container(container_id, token, timeout_s=30) -> None:
    """Poll /{container_id}?fields=status_code until FINISHED or timeout."""

def _write_permalink(db_path, gem_id, permalink, posted_at, skip_reason=None) -> None:
    """Update image_archive row with IG metadata."""

def _load_hashtag_library(path: Path) -> dict:
    """Load hashtags.yml. Plain-text YAML parser (no pyyaml dependency — the file
    is structured enough that a regex/line-based parser suffices). Returns
    {bucket_name: {top_tier: [...], mid_tier: [...], long_tail: [...]}}."""

def _scene_to_buckets(vlm_metadata: dict) -> list[str]:
    """Map gem metadata to relevant hashtag buckets.

    Current camera roster produces only brooder scenes, so the default
    auto-mapping is:
    - scene=brooder + bird_count>=1 → ["chicks", "chickens", "homestead"]

    OTHER BUCKETS ARE MANUAL-OVERRIDE ONLY in V2.0. The yorkies, coop-build,
    orchard, and yard-diary buckets aren't auto-triggerable from gem metadata
    as it exists today — there's no has_yorkie field, no coop-activity scene,
    etc. Posts with those subjects should use `scripts/ig-post.py --override-tags`
    or the VLM prompt must be extended first (V2.1+).
    """
```

### Key invariants

- Module MUST never raise out of `post_gem_to_ig()` except for auth/credential errors at the entry gate. Network failures, API errors, DB write failures are all caught, logged, and returned in the `error` field.
- `dry_run=True` means: do NOT `git push`, do NOT call Graph API's publish endpoint. Still builds the would-be caption, picks hashtags, copies the image to `farm-2026/public/photos/` as a LOCAL change but does NOT commit.
- The `git_helper` flow is idempotent by file content SHA — if the same gem_id has already been copied to farm-2026 (via existing `ig_permalink`), re-use the URL, don't re-commit.
- Calling this module MUST not require `bubba-workspace/` to exist on the filesystem. The token-env-file is loaded only if present; otherwise credentials come from explicit env vars.

---

## DB schema additions (`tools/pipeline/store.py`)

Add to `_SCHEMA_SQL`:

```sql
    ig_permalink TEXT,
    ig_posted_at TEXT,
    ig_skip_reason TEXT,
```

For existing DBs (the table already exists with these columns missing), add a migration block in `ensure_schema()`:

```python
def _add_column_if_missing(conn, table, col_def):
    col_name = col_def.split()[0]
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

# In ensure_schema(), after executescript(_SCHEMA_SQL):
for col_def in ["ig_permalink TEXT", "ig_posted_at TEXT", "ig_skip_reason TEXT"]:
    _add_column_if_missing(conn, "image_archive", col_def)
```

This matches the idempotent pattern `CREATE TABLE IF NOT EXISTS` already uses. No data migration needed — new columns default to NULL.

`store()` function doesn't need changes for the INSERT — new rows get NULL by default. `ig_poster.py` writes via UPDATE.

---

## `git_helper.py` design

```python
def commit_image_to_farm_2026(
    local_image: Path,
    subdir: str,
    repo_path: Path,
    commit_message: str,
) -> tuple[str, str]:
    """
    Copy local_image to repo_path/public/photos/{subdir}/{basename},
    run `git add`, `git commit`, `git push`.
    Returns (absolute_repo_file_path, github_raw_url).
    Raises subprocess.CalledProcessError on git failure (the caller
    must handle — don't swallow git errors, the IG post can't proceed
    without the URL).
    """
```

- Uses `subprocess.run([..., ...], cwd=repo_path, check=True, capture_output=True, timeout=60)`.
- Computes the raw URL from the current branch (`git rev-parse --abbrev-ref HEAD`) and the configured remote (`git config --get remote.origin.url` → parse owner/repo).
- Does NOT retry on failure. A git push failure is a real issue the user should see.

---

## CLI: `scripts/ig-post.py`

```
usage: ig-post.py [-h] --gem-id GEM_ID --caption CAPTION
                  [--dry-run] [--no-hashtags] [--override-tags TAG,TAG,...]
                  [--hashtag-extra TAG,TAG,...]

Options:
  --gem-id        Integer gem id from image_archive table.
  --caption       The journal-body caption. Hashtags appended automatically.
  --dry-run       Build+print would-be payload; do NOT publish. No git push.
  --no-hashtags   Skip the hashtag block entirely (for very personal posts).
  --override-tags Replace auto-selected tags with this exact list (bypasses
                  auto-selection; still deduped + capped at 10).
  --hashtag-extra Add these tags on top of auto-selected (respects cap).

Exit codes:
  0  success (or dry-run printed a valid payload)
  1  runtime failure (DB error, git failure, Graph API error)
  2  user input error (unknown gem-id, bad caption, malformed tag list)
  3  credential error (missing tokens in env)
```

Pure stdlib (argparse, json, sqlite3, pathlib, subprocess). No venv needed — matches `scripts/add-camera.py`.

---

## Orchestrator hook

Exact insertion point in `tools/pipeline/orchestrator.py:run_cycle()` after the Discord block (around line 248–250):

```python
    # Auto-post gems to Instagram. Gated by config flag. Never break the cycle.
    try:
        if cfg.get("instagram", {}).get("enabled", False):
            from tools.pipeline.ig_poster import post_gem_to_ig, should_post_ig
            should, reason = should_post_ig(
                vlm_result["metadata"],
                gem_row={"id": store_result["gem_id"], "camera_id": camera_name},
                last_post_ts=store_result.get("last_ig_post_ts"),
            )
            if should:
                ig_result = post_gem_to_ig(
                    gem_id=store_result["gem_id"],
                    caption=vlm_result["metadata"].get("caption_draft", "") or "",
                    db_path=DB_PATH,
                    farm_2026_repo_path=Path(cfg["instagram"]["farm_2026_repo_path"]),
                    hashtag_library_path=here / cfg["instagram"]["hashtag_library_path"],
                    dry_run=cfg["instagram"].get("dry_run", False),
                )
                result["posted_to_ig"] = ig_result.get("permalink")
            else:
                result["ig_skip_reason"] = reason
    except Exception as e:
        log.warning("%s: ig post wrapper failed: %s", camera_name, e)
```

Also extend the `.env` loading a few lines earlier:

```python
# At top of orchestrator.py near existing load_dotenv() call:
load_dotenv(repo_root / ".env")
meta_env = Path("/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env")
if meta_env.exists():
    load_dotenv(meta_env)
```

`instagram.enabled` defaults to `false` so this hook is a no-op until Boss explicitly flips it.

---

## `config.json` additions (`tools/pipeline/config.json`)

```json
  "instagram": {
    "enabled": false,
    "auto_dry_run": true,
    "farm_2026_repo_path": "/Users/macmini/Documents/GitHub/farm-2026",
    "hashtag_library_path": "hashtags.yml",
    "min_hours_between_posts": 3,
    "min_hours_per_camera": 12,
    "max_hashtags_per_post": 10
  }
```

`manual_approval` was in an earlier draft but is REMOVED from V2.0 config — the approval flow is V2.1 work. Config keys that have no code behind them are lies; adding them when V2.1 lands is the right time.

---

## Phased build order + verification

**Build priority: the working manual CLI first; predicate + orchestrator-hook later.** V2.0's actual delivered capability is a CLI that replays what I did by hand for posts #1 and #2 — automation-gated pieces (predicate, auto-post hook) only become useful when auto-posting is imminent, which is V2.2+.

Each phase = one commit, ends with a local verification step.

1. **Phase 1 — hashtag library.** Create `tools/pipeline/hashtags.yml`, populate from the verified buckets in `docs/19-Apr-2026-instagram-posting-plan.md`. Commit. Verify: load the file with a short script, print bucket names + tag counts, confirm no creator-branded tags slipped through (grep for `markbarney`, `builtwith`).

2. **Phase 2 — DB migration.** Add the 3 columns + `_add_column_if_missing` helper. Commit. Verify: drop into sqlite3 against the live DB, check `PRAGMA table_info(image_archive)` includes new columns; also test against a fresh empty DB to ensure `ensure_schema()` still works on first-time install.

3. **Phase 3 — `git_helper.py`.** Commit the helper. **Empirically verified 2026-04-20:** `git push --dry-run` via `subprocess.run()` with `GIT_TERMINAL_PROMPT=0` returns `rc=0, "Everything up-to-date"` against both repos with zero prompts — `credential.helper=osxkeychain` handles auth non-interactively. The helper will set `GIT_TERMINAL_PROMPT=0` in the env dict defensively. Verify after commit: manually invoke from REPL with a test file, confirm the push lands.

4. **Phase 4 — `ig_poster.py` core** (minus the hashtag/predicate functions). `post_gem_to_ig()`, `_load_credentials()`, `_create_container()`, `_publish()`, `_wait_for_container()`, `_write_permalink()`. Commit. Verify: invoke from REPL with a known gem id in `dry_run=True` mode — confirms credential load, hashtag library load (bare-bones for now), gem lookup, payload shape. No Graph API publish calls.

5. **Phase 5 — `scripts/ig-post.py` CLI (the V2.0 delivered capability).** Pure stdlib, full flow end-to-end, mirrors the two manual posts already shipped. Commit. Verify in order: (a) `--dry-run` on gem 6595 prints expected payload; (b) without `--dry-run` against a fresh candidate gem, completes the full round-trip and writes `ig_permalink` back. **At this point V2.0's actual capability is live — Boss can post with one command.**

6. **Phase 6 — predicate + hashtag selection.** `should_post_ig()` and `pick_hashtags()`. These are preparation for V2.2 auto-posting but also useful to the CLI (default auto-selection of hashtags; `--no-hashtags` and `--override-tags` still bypass). Commit. Verify: fixture-based — pass in gem rows, check returned bool/reason and tag list; `pick_hashtags()` output respects the rotation rule against `ig_tag_history.json`.

7. **Phase 7 — orchestrator hook + config.** Commit. Verify: with `instagram.enabled: false` (default), the existing Discord-posting flow is unaffected. Restart the pipeline LaunchAgent, confirm no regression. Then flip to `enabled: true` + `auto_dry_run: true` in config, confirm the hook fires and logs "would post" without publishing.

8. **Phase 8 — CHANGELOG + docs sync.** Update CHANGELOG.md with v2.29.0 entry. Update CLAUDE.md's "Operational skills" bullet. Update the memory/skill files in bubba-workspace to reflect that V2 code is live. Commit.

Each phase commits to `main` and pushes. No feature branch — matches the repo's existing workflow. Commits sized so each is revertable without cascading changes.

### Two independent dry-run flags — keep them separate

- **`--dry-run` on `scripts/ig-post.py`** — operator flag. Only honored by the CLI. "I want to see what this WOULD do without publishing." The CLI ignores any config-level dry-run setting; what you type is what you get.
- **`instagram.auto_dry_run` in `tools/pipeline/config.json`** — orchestrator flag. Applies ONLY to the auto-posting branch in `run_cycle()`. Lets Boss enable the hook to exercise the code path without it actually publishing in production.

Naming them differently (`--dry-run` vs `auto_dry_run`) is deliberate — using the same name invites the mistake where someone thinks flipping the config also disarms manual CLI invocation, or vice versa. They cover separate threat models.

---

## Round-trip verification (end-to-end, before calling done)

1. Fresh run of the pipeline with `instagram.enabled: true, dry_run: true`. Confirm a gem triggers `should_post_ig()` = True, log shows "would post" with the exact payload.
2. CLI invocation `scripts/ig-post.py --gem-id <sharp-brooder-gem> --caption "test caption" --dry-run`. Confirm full payload prints, no git push occurred, no Graph API publish occurred.
3. CLI invocation WITHOUT `--dry-run` on a pre-approved gem. Confirm: farm-2026 commit landed, raw URL returns 200, Graph API container created, container status = FINISHED, publish returned media_id, permalink fetched, `image_archive.ig_permalink` populated, post visible on @pawel_and_pawleen.
4. Poll `/me/media` to confirm post count incremented.
5. Delete the test post (via Graph API or IG app) — not sure if Boss wants to keep it, default to ask.

---

## Credential loading — explicit policy

The existing `bubba-workspace/secrets/farm-guardian-meta.env` already has (per the 19-Apr work):
```
LONG_LIVED_USER_TOKEN=...
FB_APP_ID=613565154985119
FB_APP_SECRET=...
FB_PAGE_ID=614607655061302
IG_BUSINESS_ACCOUNT_ID=17841460199126266
# ...etc
```

The orchestrator's `load_dotenv()` call is extended to source that file, so `os.environ["LONG_LIVED_USER_TOKEN"]` etc. surface naturally. `ig_poster._load_credentials()` reads those env vars and returns them as a dict.

**Keychain is source of truth; env file is the mirror; pipeline reads the mirror.** Boss can regenerate the env file from keychain any time with a one-liner if tokens change (already done 2026-04-19; same mechanism applies on refresh). No `subprocess` + `security find-generic-password` from inside the pipeline — that would break the repo's env-var convention.

**Absolute path (`/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env`) is deliberate, not an oversight.** farm-guardian is single-host on this Mac Mini; the file isn't portable because the service isn't portable. If/when the service ever gets ported to another box, this path becomes a config key at that point, not before. Premature portability is a cost.

---

## What I am NOT doing in V2.0

- NOT enabling Instagram auto-posting by default. `instagram.enabled: false`.
- NOT wiring the Discord approval flow. That's V2.1.
- NOT installing a LaunchAgent for scheduled 4x/day posts. That's V2.2.
- NOT implementing reels. V3.
- NOT posting anything to IG during testing beyond what's necessary for round-trip verification (max 1 test post, which I'll propose and wait for approval).
- NOT editing caption on existing posts. (Post #2's stale caption stays as-is; Boss already decided.)

---

## Risks / things I still want to be wary of

- **Git failures in the hot path.** A `git push` can fail for many reasons (network, auth, conflict, pre-commit hook). The current plan is fail-loud and return in the `error` field. For V2.2+ auto-posting, a retry-with-backoff would be needed, but V2.0 (manual CLI) is fine with fail-fast.
- **Caption prompts at scale.** `caption_draft` from VLM can be generic ("A small chick in a brooder."). Manual mode always overrides. Auto-mode (V2.2+) will need a caption refinement step — out of scope here.
- **Hashtag library drift.** The YAML is data; I need to keep `hashtags.yml` in sync with the plan doc. The plan doc is the narrative; the YAML is the source of truth for code. I'll note this in the CHANGELOG.
- **Over-engineering risk.** This doc is long. The actual code will be ~300–400 lines. If any phase bloats past 150 lines, stop and simplify.

---

## Advisor-review gate

Per Boss's explicit 2026-04-20 directive ("call in an advisor because you are a brain-dead fucking fuck-up idiot moron"), I will call advisor after writing this plan and before Phase 1 code. The advisor sees the full conversation including all the mistakes I've made tonight. Apply their feedback before committing any code.
