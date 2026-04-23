# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Low-level Nextdoor UI primitives (SKELETON — selectors are
#          PLACEHOLDERS and must be filled in during the first attended
#          session with Boss at the Mac Mini; see
#          ~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md §
#          "First attended session (selectors capture)").
#
#          Every primitive is wrapped so a missing / stale selector logs
#          and returns False rather than raising — sessions stay alive,
#          hard errors bubble up only from challenge.inspect_page.
#
#          Primitives to fill in:
#            - goto_feed(page)
#            - scroll_feed(page, distance_px)
#            - find_feed_posts(page, max_posts)
#            - like_post(page, article)
#            - comment_on_post(page, article, text)
#            - open_create_post_dialog(page)
#            - attach_photo(page, image_path)
#            - set_audience_neighborhood(page)
#            - submit_post(page)
#
# SRP/DRY check: Pass once selectors are filled. Today's job is the
#                interface shape — selectors come from observation.

from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import ElementHandle, Page

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import challenge  # sibling module

log = logging.getLogger("nextdoor.primitives")


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
    page.wait_for_timeout(3000)
    challenge.inspect_page(page)


def scroll_feed(page: Page, distance_px: int = 800) -> None:
    page.mouse.wheel(0, distance_px + random.randint(-120, 180))
    page.wait_for_timeout(random.randint(500, 1300))


def find_feed_posts(page: Page, max_posts: int = 6) -> list[ElementHandle]:
    """Return visible post article handles. TODO: Nextdoor's post
    container selector needs capture — the placeholders below are
    informed guesses and will likely need replacing."""
    for selector in (
        "article[data-testid*='post']",
        "div[data-testid='feed-card']",
        "main article",
        "[role='article']",
    ):
        try:
            handles = page.locator(selector).element_handles()
        except Exception:
            handles = []
        if handles:
            return handles[:max_posts]
    return []


# -------- like --------

def like_post(page: Page, article: ElementHandle) -> bool:
    """Click this article's Like / Thank / React button. TODO: confirm
    Nextdoor's aria-label strings; 'Like' and 'Thank' both seen in
    third-party reverse engineering."""
    try:
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    for sel in (
        "button[aria-label='Like']",
        "button[aria-label='Thank']",
        "button:has-text('Like')",
    ):
        try:
            btn = article.query_selector(sel)
        except Exception:
            btn = None
        if not btn:
            continue
        try:
            btn.click(timeout=2500)
            page.wait_for_timeout(random.randint(500, 1100))
            return True
        except Exception as e:
            log.warning("like_post click failed on %s: %s", sel, e)
    log.info("like_post: no recognized like button found")
    return False


# -------- comment --------

def comment_on_post(page: Page, article: ElementHandle, text: str) -> bool:
    try:
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    for sel in (
        "textarea[placeholder*='comment' i]",
        "div[contenteditable='true'][aria-label*='comment' i]",
        "textarea",
    ):
        try:
            target = article.query_selector(sel)
        except Exception:
            target = None
        if not target:
            continue
        try:
            target.click()
            for ch in text:
                target.type(ch, delay=random.randint(50, 140))
            page.wait_for_timeout(random.randint(700, 1500))
            # Try clicking a Post / Submit button; fall back to Enter.
            submit = article.query_selector("button:has-text('Post')") or article.query_selector("button[type='submit']")
            if submit:
                submit.click(timeout=2500)
            else:
                target.press("Enter")
            page.wait_for_timeout(random.randint(1500, 2500))
            return True
        except Exception as e:
            log.warning("comment_on_post via %s failed: %s", sel, e)
    log.info("comment_on_post: no recognized comment target found")
    return False


# -------- create post (cross-post lane) --------

def open_create_post_dialog(page: Page) -> bool:
    """Click the primary "Post" / "Start a post" entrypoint. TODO: confirm
    selector. Nextdoor has a large 'What's happening in your neighborhood'
    text field at the top of the feed that acts as the entrypoint."""
    for sel in (
        "button:has-text('Start a post')",
        "button:has-text('Post')",
        "div:has-text('What''s happening')",
        "[aria-label*='Create post' i]",
    ):
        try:
            el = page.locator(sel).first
            if el.count() == 0:
                continue
            el.click(timeout=3000)
            page.wait_for_timeout(random.randint(800, 1500))
            return True
        except Exception as e:
            log.warning("open_create_post_dialog %s failed: %s", sel, e)
    return False


def attach_photo(page: Page, image_path: Path) -> bool:
    """Attach a photo via the dialog's hidden file input. TODO: confirm
    selector; `input[type='file']` inside the post dialog is the typical
    pattern but the exact scoping may differ."""
    try:
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(str(image_path))
        page.wait_for_timeout(random.randint(1200, 2500))
        return True
    except Exception as e:
        log.warning("attach_photo failed: %s", e)
        return False


def type_post_body(page: Page, body_text: str) -> bool:
    """Type the post body into the open dialog's textarea."""
    for sel in (
        "div[contenteditable='true']",
        "textarea",
    ):
        try:
            target = page.locator(sel).first
            if target.count() == 0:
                continue
            target.click()
            for ch in body_text:
                target.type(ch, delay=random.randint(30, 90))
            page.wait_for_timeout(random.randint(400, 900))
            return True
        except Exception as e:
            log.warning("type_post_body via %s failed: %s", sel, e)
    return False


def set_audience_neighborhood(page: Page) -> bool:
    """Ensure the post's audience picker is set to 'Just my neighborhood'
    — the narrowest option. **This is a hard safety requirement** per
    the Nextdoor skill doc; never widen the audience automatically.

    TODO: confirm the picker's aria-label / option labels during the
    first attended session. The strings below are guesses."""
    try:
        # Open the audience picker.
        picker = page.locator("button:has-text('audience'), [aria-label*='audience' i]").first
        if picker.count() == 0:
            log.warning("audience picker not found — refusing to submit")
            return False
        picker.click(timeout=3000)
        page.wait_for_timeout(random.randint(600, 1200))
        # Click the narrowest option.
        option = page.locator(
            "[role='menuitem']:has-text('Just my neighborhood'), "
            "li:has-text('Just my neighborhood')"
        ).first
        if option.count() == 0:
            log.warning("'Just my neighborhood' option not found — refusing")
            return False
        option.click(timeout=3000)
        page.wait_for_timeout(random.randint(500, 1100))
        return True
    except Exception as e:
        log.warning("set_audience_neighborhood failed: %s", e)
        return False


def submit_post(page: Page) -> bool:
    for sel in (
        "button:has-text('Post'):not([aria-label*='Like' i])",
        "button[type='submit']",
    ):
        try:
            el = page.locator(sel).first
            if el.count() == 0:
                continue
            el.click(timeout=3000)
            page.wait_for_timeout(random.randint(2000, 3500))
            return True
        except Exception as e:
            log.warning("submit_post %s failed: %s", sel, e)
    return False
