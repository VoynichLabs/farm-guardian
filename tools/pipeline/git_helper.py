# Author: Claude Opus 4.7 (1M context); updated Claude Sonnet 4.6 02-May-2026
# Date: 20-April-2026 (initial 20-Apr-2026; extension whitelist added 20-Apr-2026 for Phase 3 reels)
# PURPOSE: Commit a local image OR short video into the farm-2026 repo's
#          public/photos/ directory and push to origin, so the media
#          surfaces as a GitHub raw URL that Instagram's media fetcher
#          will accept. This solves the IG fetcher's requirement that
#          image_url / video_url end in a recognized extension
#          (.jpg/.jpeg/.png for feed + stories, .mp4 for reels) — the
#          guardian.markbarney.net tunnel's /api/v1/images/gems/{id}/image
#          path is rejected by IG even though the response is correct
#          image/jpeg. See docs/19-Apr-2026-instagram-posting-plan.md
#          for the full diagnosis.
#
#          Runs `git add / git commit / git push` via subprocess with
#          GIT_TERMINAL_PROMPT=0 so a credential prompt can never hang
#          the pipeline. The osxkeychain credential.helper on this Mac
#          Mini handles auth non-interactively — verified 2026-04-20
#          against both farm-guardian and farm-2026 with
#          `git push --dry-run` returning rc=0 in a clean Python
#          subprocess environment.
#
#          Extension whitelist (Phase 3, 20-Apr-2026): the public commit
#          function rejects anything that isn't in a small allow-list so
#          we can't accidentally commit a stray .DS_Store, .txt, or
#          another binary. Self-documenting and catches mistakes early.
#
# SRP/DRY check: Pass — single responsibility is "move a local media
#                file into farm-2026's public tree and make it a public
#                HTTPS URL." No IG API calls (ig_poster.py does that),
#                no media selection (ig_poster.py's should_post path
#                does that), no DB writes (store.py and the ig_poster
#                UPDATE do that).

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("pipeline.git_helper")

# Media types farm-2026 is willing to host publicly. Anything else gets
# rejected at the top of commit_image_to_farm_2026 so we can't
# accidentally commit a stray .DS_Store, .txt sidecar, or arbitrary
# binary. Stored lowercase; comparison is case-insensitive.
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4"}


class GitHelperError(RuntimeError):
    """Raised when a subprocess git operation fails.

    Caller (ig_poster.py) should catch this, log, and bubble it up in
    the `error` field of post_gem_to_ig()'s return dict. Never swallow —
    a push failure means the IG post can't proceed.
    """


def _git(
    repo_path: Path,
    *args: str,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run `git <args>` in repo_path with non-interactive env.

    Raises GitHelperError with a captured-stderr message on non-zero exit
    or timeout. Callers get actionable output.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Defense in depth: even if something downstream tries to prompt, fail
    # fast instead of hanging. ASKPASS=/bin/echo makes the prompt return
    # an empty string rather than blocking.
    env.setdefault("GIT_ASKPASS", "/bin/echo")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise GitHelperError(
            f"git {' '.join(args)} timed out after {timeout}s in {repo_path}"
        ) from e
    if result.returncode != 0:
        raise GitHelperError(
            f"git {' '.join(args)} failed (rc={result.returncode}) in {repo_path}:\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result


def _parse_owner_repo(origin_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub remote URL.

    Handles the two common forms:
      - https://github.com/Owner/Repo(.git)?
      - git@github.com:Owner/Repo(.git)?

    Raises GitHelperError on anything else — we don't currently support
    self-hosted git forges (if the farm-2026 remote ever moves off
    GitHub, this helper needs to learn the new host's raw-URL shape).
    """
    patterns = [
        r"^https?://(?:www\.)?github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/.]+?)(?:\.git)?/?$",
    ]
    for p in patterns:
        m = re.match(p, origin_url)
        if m:
            return m.group(1), m.group(2)
    raise GitHelperError(
        f"Cannot derive owner/repo from origin URL: {origin_url!r}"
    )


def _current_branch(repo_path: Path) -> str:
    """Return the currently checked-out branch name.

    Raises if HEAD is detached — ig_poster's auto-commit flow doesn't
    make sense on a detached HEAD; the operator should fix their repo
    first.
    """
    result = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    if branch == "HEAD":
        raise GitHelperError(
            f"{repo_path} is in detached-HEAD state; refusing to commit"
        )
    return branch


def _github_raw_url(owner: str, repo: str, branch: str, path_in_repo: str) -> str:
    """Build the raw.githubusercontent.com URL for a file at main HEAD.

    Using the branch name (rather than a commit SHA) means the URL
    tracks whatever HEAD is — fine for this use case because IG
    fetches the image once at container-create and then caches it on
    Meta's CDN; we don't need the URL to remain stable long-term.
    """
    # Leading slashes would double up. Strip defensively.
    path_clean = path_in_repo.lstrip("/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path_clean}"


def _push_with_rebase_retry(repo_path: Path, branch: str, max_retries: int = 2) -> None:
    """Push to origin, retrying with pull --rebase on ref-lock failures.

    social-publisher, ig-daily-reel, and archive-throwback all push to
    farm-2026 concurrently. When two pushes race, one gets a stale ref error
    ("cannot lock ref ... but expected ..."). Fix: pull --rebase to fast-forward
    past the other script's commit, then retry the push.
    """
    for attempt in range(max_retries + 1):
        try:
            _git(repo_path, "push", "origin", branch, timeout=300)
            return
        except GitHelperError as e:
            if attempt < max_retries and (
                "cannot lock ref" in str(e)
                or "stale" in str(e)
                or "non-fast-forward" in str(e)
                or "fetch first" in str(e)
            ):
                log.warning("git_helper: push conflict (attempt %d/%d), rebasing: %s", attempt + 1, max_retries, str(e)[:120])
                _git(repo_path, "pull", "--rebase", "origin", branch, timeout=300)
            else:
                raise


def commit_image_to_farm_2026(
    local_image: Path,
    subdir: str,
    repo_path: Path,
    commit_message: str,
) -> tuple[Path, str]:
    """Copy local_image into repo_path/public/photos/<subdir>/<basename>,
    then git add / commit / push. Returns (absolute_committed_path,
    github_raw_url).

    The raw URL is live the instant the push completes — IG's fetcher
    accepts GitHub raw URLs immediately (verified empirically on posts
    #1 through #3). No need to wait for Railway to redeploy.

    Idempotence: if the destination file already exists with matching
    SHA256, skip the copy+commit (no-op, still returns the raw URL).
    Callers who want to force a fresh commit should move/rename the
    source file first.

    Raises:
      GitHelperError - any git step fails (timeout, auth, conflict,
                       pre-commit hook, etc.). Caller should surface
                       this; don't swallow.
      FileNotFoundError - local_image doesn't exist.
      ValueError - subdir tries to escape public/photos/ (any `..` or
                   absolute path).
    """
    local_image = Path(local_image).resolve()
    if not local_image.exists():
        raise FileNotFoundError(f"local_image does not exist: {local_image}")

    # Extension whitelist — reject anything that isn't a supported public
    # media type (photos for feed + stories, mp4 for reels). Catches
    # accidents like staging a .DS_Store or a .txt sidecar. Case-insensitive.
    ext = local_image.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(
            f"local_image extension {ext!r} not in allowed set "
            f"{sorted(_ALLOWED_EXTENSIONS)} (got {local_image.name})"
        )

    # Guard against path traversal. subdir must be a plain relative
    # segment like "brooder" or "iphone-drops/2026-04-20".
    if ".." in Path(subdir).parts or Path(subdir).is_absolute():
        raise ValueError(
            f"subdir {subdir!r} is not a plain relative path under public/photos"
        )

    repo_path = Path(repo_path).resolve()
    photos_dir = repo_path / "public" / "photos" / subdir
    photos_dir.mkdir(parents=True, exist_ok=True)
    dest = photos_dir / local_image.name

    # Idempotence: if the destination exists and is identical, skip the
    # copy and the commit. Still return the raw URL — the caller can
    # proceed to the IG publish step.
    import hashlib

    def _sha(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    need_commit = True
    if dest.exists() and _sha(dest) == _sha(local_image):
        log.info("git_helper: dest %s already matches source (sha match), skipping copy+commit", dest)
        need_commit = False
    else:
        shutil.copy2(local_image, dest)
        log.info("git_helper: copied %s -> %s", local_image, dest)

    # Compute the raw URL before doing any git work — if the URL
    # computation fails (unparseable origin, detached HEAD), bail
    # before touching the working tree's index.
    origin = _git(repo_path, "remote", "get-url", "origin").stdout.strip()
    owner, repo = _parse_owner_repo(origin)
    branch = _current_branch(repo_path)
    rel_path = dest.relative_to(repo_path).as_posix()
    raw_url = _github_raw_url(owner, repo, branch, rel_path)

    if need_commit:
        _git(repo_path, "add", rel_path)
        # If there's nothing staged (race condition: someone committed
        # the same file in between, or the copy was a no-op on content
        # identical to HEAD), skip commit+push.
        status = _git(repo_path, "status", "--porcelain", "--", rel_path)
        if status.stdout.strip():
            _git(repo_path, "commit", "-m", commit_message)
            # Push with pull-rebase retry: multiple scripts (social-publisher,
            # ig-daily-reel, archive-throwback) push to farm-2026 concurrently.
            # If refs diverged since our commit, pull --rebase and retry once.
            _push_with_rebase_retry(repo_path, branch)
            log.info("git_helper: committed+pushed %s", rel_path)
        else:
            log.info("git_helper: %s is clean after add (already committed), skipping commit", rel_path)

    return dest, raw_url
