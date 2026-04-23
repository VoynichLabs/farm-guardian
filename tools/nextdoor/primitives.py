# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Low-level Nextdoor UI primitives. Selectors captured live
#          2026-04-23 via chrome-devtools MCP against Boss's logged-in
#          Nextdoor session on this Mac Mini (news_feed + opened
#          composer). Every interactive element worth automating is
#          keyed off a `data-testid` that Nextdoor's React app emits,
#          so these are stable against class-hash rotation.
#
#          Every primitive is wrapped so a missing / stale selector
#          logs and returns False rather than raising — sessions stay
#          alive, hard errors bubble up only from challenge.inspect_page.
#
#          Hard safety (see skill doc):
#            - no neighbor-request / friend primitive here, ever
#            - no DM primitive here, ever
#            - audience floor is `visibility-menu-option-2` = "Your
#              neighborhood · Hampton only"; never widen to nearby /
#              anyone.
#
# SRP/DRY check: Pass — pure UI primitives, no orchestration logic.

from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import ElementHandle, Page

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import challenge  # sibling module

log = logging.getLogger("nextdoor.primitives")


# ---------------------------------------------------------------------------
# NEXTDOOR_SELECTORS — captured live 2026-04-23 via chrome-devtools MCP.
# Refresh by re-running the claude-for-chrome-brief.md inspection or by
# using tools/chrome_session/codegen.py --profile nextdoor.
# ---------------------------------------------------------------------------
NEXTDOOR_SELECTORS: dict[str, str] = {
    # --- feed ---
    "FEED_POST_CARD": '[data-testid="feed-item-card"]',
    # React/Like button. aria-label on the button is "React" (long-press
    # opens a reaction picker); the default icon is the heart labelled
    # "Like". A plain click = Like.
    "POST_LIKE_BUTTON": '[data-testid="reaction-button"]',
    # aria-pressed flips to "true" once the current user has reacted.
    "POST_LIKED_INDICATOR": '[data-testid="reaction-button"][aria-pressed="true"]',
    # Click-to-open comment drawer on the card. Div with role=button.
    "POST_REPLY_BUTTON": '[data-testid="post-reply-button"]',
    # Comment textarea — revealed after clicking POST_REPLY_BUTTON.
    "POST_COMMENT_INPUT": 'textarea[data-testid="comment-add-reply-input"]',
    # Submit button that posts the comment once there's text.
    "POST_COMMENT_SUBMIT": '[data-testid="inline-composer-reply-button"]',
    # The first photo on a post. URL lives on `src`. Smartlink previews
    # use `[data-testid="smartlink-image"]` instead — we only want the
    # first-party post photo.
    "POST_IMAGE": '[data-testid="resized-image"]',
    # Body copy container. Nested `[data-testid="styled-text"]` holds
    # the actual text node; either works for innerText.
    "POST_CAPTION_TEXT": '[data-testid="post-body"]',

    # --- create-post composer (cross-post lane) ---
    # The "What's happening, neighbor?" prompt strip at the top of the
    # feed. role=button. Clicking opens the composer dialog.
    "CREATE_POST_ENTRYPOINT": '[data-testid="prompt-container"]',
    # The composer dialog itself (aria-label="create post composer").
    "COMPOSER_DIALOG": '[data-testid="content-composer-dialog"]',
    # Textarea for body copy.
    "COMPOSER_BODY_INPUT": 'textarea[data-testid="composer-text-field"]',
    # Hidden file input. accept="image/*, video/*", multiple.
    "COMPOSER_PHOTO_INPUT": 'input[data-testid="uploader-fileinput"]',
    # The audience picker trigger. Shows the current audience label
    # ("Anyone" by default).
    "COMPOSER_AUDIENCE_PICKER": '[data-testid="neighbor-audience-visibility-button"]',
    # Narrowest audience option in the dropdown menu.
    # Option 0 = "Anyone" (widest — off-Nextdoor public).
    # Option 1 = "Nearby neighborhoods" (your neighborhood + 21 others).
    # Option 2 = "Your neighborhood · Hampton only" — the floor we want.
    "COMPOSER_AUDIENCE_NEIGHBORHOOD_OPTION": '[data-testid="visibility-menu-option-2"]',
    "COMPOSER_SUBMIT": '[data-testid="composer-submit-button"]',
    "COMPOSER_CLOSE": '[data-testid="composer-close-button"]',
}


# -------- timing --------

def human_sleep(lo: float = 5.0, hi: float = 14.0, long_pause_chance: float = 0.10) -> None:
    if random.random() < long_pause_chance:
        secs = random.uniform(25.0, 75.0)
    else:
        secs = random.triangular(lo, hi, lo + (hi - lo) * 0.35)
    time.sleep(secs)


# -------- feed navigation --------

def goto_feed(page: Page) -> None:
    page.goto("https://nextdoor.com/news_feed/", wait_until="domcontentloaded")
    # Let the React app hydrate and spawn feed-item-card nodes.
    try:
        page.wait_for_selector(NEXTDOOR_SELECTORS["FEED_POST_CARD"], timeout=10000)
    except Exception:
        log.warning("goto_feed: no feed-item-card after 10s")
    page.wait_for_timeout(1500)
    challenge.inspect_page(page)


def scroll_feed(page: Page, distance_px: int = 800) -> None:
    page.mouse.wheel(0, distance_px + random.randint(-120, 180))
    page.wait_for_timeout(random.randint(500, 1300))


def find_feed_posts(page: Page, max_posts: int = 6) -> list[ElementHandle]:
    """Return visible post article handles from the news feed."""
    try:
        handles = page.locator(NEXTDOOR_SELECTORS["FEED_POST_CARD"]).element_handles()
    except Exception as e:
        log.warning("find_feed_posts locator failed: %s", e)
        return []
    return handles[:max_posts]


# -------- like --------

def like_post(page: Page, article: ElementHandle) -> bool:
    """Click this article's reaction button. Skips if already reacted."""
    try:
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        already = article.query_selector(NEXTDOOR_SELECTORS["POST_LIKED_INDICATOR"])
    except Exception:
        already = None
    if already:
        log.info("like_post: post already reacted to; skipping")
        return False
    try:
        btn = article.query_selector(NEXTDOOR_SELECTORS["POST_LIKE_BUTTON"])
    except Exception:
        btn = None
    if not btn:
        log.info("like_post: no reaction-button found in card")
        return False
    try:
        btn.click(timeout=2500)
        page.wait_for_timeout(random.randint(500, 1100))
        return True
    except Exception as e:
        log.warning("like_post click failed: %s", e)
        return False


# -------- comment --------

def comment_on_post(page: Page, article: ElementHandle, text: str) -> bool:
    try:
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    # Reveal the comment input by clicking the reply button.
    try:
        reply_btn = article.query_selector(NEXTDOOR_SELECTORS["POST_REPLY_BUTTON"])
        if reply_btn:
            reply_btn.click(timeout=2500)
            page.wait_for_timeout(random.randint(600, 1200))
    except Exception as e:
        log.warning("comment_on_post: reply toggle failed: %s", e)
    try:
        target = article.query_selector(NEXTDOOR_SELECTORS["POST_COMMENT_INPUT"])
    except Exception:
        target = None
    if not target:
        log.info("comment_on_post: no comment textarea exposed")
        return False
    try:
        target.click()
        for ch in text:
            target.type(ch, delay=random.randint(50, 140))
        page.wait_for_timeout(random.randint(700, 1500))
        submit = article.query_selector(NEXTDOOR_SELECTORS["POST_COMMENT_SUBMIT"])
        if submit:
            submit.click(timeout=2500)
        else:
            # Fallback: Enter can submit short single-line comments.
            target.press("Enter")
        page.wait_for_timeout(random.randint(1500, 2500))
        return True
    except Exception as e:
        log.warning("comment_on_post type/submit failed: %s", e)
        return False


# -------- create post (cross-post lane) --------

def open_create_post_dialog(page: Page) -> bool:
    """Click the "What's happening, neighbor?" prompt strip to open the
    composer dialog."""
    try:
        entry = page.locator(NEXTDOOR_SELECTORS["CREATE_POST_ENTRYPOINT"]).first
        if entry.count() == 0:
            log.warning("open_create_post_dialog: prompt-container not found")
            return False
        entry.click(timeout=3000)
        page.wait_for_selector(NEXTDOOR_SELECTORS["COMPOSER_DIALOG"], timeout=5000)
        page.wait_for_timeout(random.randint(500, 1000))
        return True
    except Exception as e:
        log.warning("open_create_post_dialog failed: %s", e)
        return False


def attach_photo(page: Page, image_path: Path) -> bool:
    try:
        file_input = page.locator(NEXTDOOR_SELECTORS["COMPOSER_PHOTO_INPUT"]).first
        file_input.set_input_files(str(image_path))
        page.wait_for_timeout(random.randint(1500, 2800))
        return True
    except Exception as e:
        log.warning("attach_photo failed: %s", e)
        return False


def type_post_body(page: Page, body_text: str) -> bool:
    try:
        target = page.locator(NEXTDOOR_SELECTORS["COMPOSER_BODY_INPUT"]).first
        if target.count() == 0:
            return False
        target.click()
        for ch in body_text:
            target.type(ch, delay=random.randint(30, 90))
        page.wait_for_timeout(random.randint(400, 900))
        return True
    except Exception as e:
        log.warning("type_post_body failed: %s", e)
        return False


def set_audience_neighborhood(page: Page) -> bool:
    """Set the composer's audience to "Your neighborhood · Hampton only"
    (visibility-menu-option-2). **Hard safety requirement** — never
    widen; if the narrowest option is missing, refuse."""
    try:
        picker = page.locator(NEXTDOOR_SELECTORS["COMPOSER_AUDIENCE_PICKER"]).first
        if picker.count() == 0:
            log.warning("audience picker not found — refusing to submit")
            return False
        picker.click(timeout=3000)
        page.wait_for_timeout(random.randint(500, 1100))
        option = page.locator(NEXTDOOR_SELECTORS["COMPOSER_AUDIENCE_NEIGHBORHOOD_OPTION"]).first
        if option.count() == 0:
            log.warning("'Your neighborhood' option not found — refusing")
            return False
        option.click(timeout=3000)
        page.wait_for_timeout(random.randint(500, 1100))
        return True
    except Exception as e:
        log.warning("set_audience_neighborhood failed: %s", e)
        return False


def submit_post(page: Page) -> bool:
    try:
        btn = page.locator(NEXTDOOR_SELECTORS["COMPOSER_SUBMIT"]).first
        if btn.count() == 0:
            return False
        btn.click(timeout=3000)
        page.wait_for_timeout(random.randint(2500, 4000))
        return True
    except Exception as e:
        log.warning("submit_post failed: %s", e)
        return False


def close_composer(page: Page) -> bool:
    try:
        btn = page.locator(NEXTDOOR_SELECTORS["COMPOSER_CLOSE"]).first
        if btn.count() == 0:
            return False
        btn.click(timeout=2000)
        page.wait_for_timeout(400)
        confirm = page.get_by_role("button", name="Discard")
        if confirm.count() > 0:
            confirm.first.click()
        return True
    except Exception as e:
        log.warning("close_composer failed: %s", e)
        return False
