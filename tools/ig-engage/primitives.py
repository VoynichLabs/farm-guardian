# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Low-level IG engagement primitives the main engager calls into.
#          Each function is wrapped so that a missing selector / transient
#          failure logs and returns False rather than raising — sessions
#          stay alive, hard errors bubble up only from challenge.inspect_page.
#
#          Primitives:
#            - human_sleep(lo, hi): non-uniform delay with occasional long pause
#            - scroll_feed(page, n): scroll N posts on the home feed
#            - find_home_posts(page): yield visible feed post articles
#            - like_current_post(page, article): click the like button
#            - fetch_image_bytes(page, article): grab the JPEG for VLM
#            - extract_caption(article): string of post caption + hashtags
#            - comment_on_post(page, article, text): type + submit comment
#            - visit_hashtag(page, tag): navigate to /explore/tags/<tag>/
#            - react_to_story(page): tap an emoji on the currently-open story
#
# SRP/DRY check: Pass — primitives are thin, stateless wrappers around
#                Playwright actions. Budget / challenge / VLM logic lives
#                elsewhere; this file is all DOM.
#
# NOTE: Instagram rewrites its class hashes constantly, so every selector
# here prefers aria-label / role / semantic HTML over class names. If a
# selector goes stale, update the aria-label string — don't reach for a
# class hash.

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests
from playwright.sync_api import ElementHandle, Page

import challenge  # sibling module

log = logging.getLogger("ig-engage.primitives")


# -------- timing --------

def human_sleep(lo: float = 6.0, hi: float = 18.0, long_pause_chance: float = 0.12) -> None:
    """Wait a non-uniform amount of time to mimic a distracted human. 12% of
    the time we stretch to 30–90s (user got a text, put the phone down, etc.)."""
    if random.random() < long_pause_chance:
        secs = random.uniform(30.0, 90.0)
    else:
        # Triangular skews toward the lower end — most actions are fast,
        # but some are slower. Feels more human than flat uniform.
        secs = random.triangular(lo, hi, lo + (hi - lo) * 0.35)
    time.sleep(secs)


# -------- home feed --------

def goto_home(page: Page) -> None:
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    challenge.inspect_page(page)


def scroll_feed(page: Page, distance_px: int = 800) -> None:
    page.mouse.wheel(0, distance_px + random.randint(-150, 150))
    page.wait_for_timeout(random.randint(400, 1200))


def find_home_posts(page: Page, max_posts: int = 8) -> list[ElementHandle]:
    """Return the first N post article elements currently in the DOM. IG
    recycles articles as you scroll; callers should re-query rather than
    hold onto handles across scrolls."""
    try:
        articles = page.locator("article[role='presentation']").element_handles()
    except Exception:
        articles = []
    if not articles:
        try:
            articles = page.locator("main article").element_handles()
        except Exception:
            articles = []
    return articles[:max_posts]


# -------- like --------

def like_current_post(page: Page, article: ElementHandle) -> bool:
    """Find the Like button inside this article and click it. Returns True
    on apparent success, False on any failure. Skips if already liked
    (aria-label='Unlike')."""
    try:
        # The button carries aria-label 'Like' or 'Unlike'. We scroll it into
        # view to ensure Instagram fires its intersection-observer that marks
        # the post as "seen" before we like it.
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        already = article.query_selector("svg[aria-label='Unlike']")
        if already:
            log.info("skip: post already liked")
            return False
        btn = article.query_selector("svg[aria-label='Like']")
        if not btn:
            log.info("skip: Like button not found in article")
            return False
        btn.click(timeout=3000)
        page.wait_for_timeout(random.randint(500, 1200))
        return True
    except Exception as e:
        log.warning("like_current_post failed: %s", e)
        return False


# -------- image + caption extraction --------

def fetch_image_bytes(page: Page, article: ElementHandle) -> Optional[bytes]:
    """Pull the first displayed image from the article, via its src or
    srcset URL. Returns raw JPEG/PNG bytes, or None. Uses requests (not
    Playwright's response API) because IG CDN URLs are publicly fetchable
    and we want to keep the browser's network tab clean."""
    try:
        img = article.query_selector("img[src*='scontent'], img[srcset*='scontent']")
        if not img:
            img = article.query_selector("img")
        if not img:
            return None
        src = img.get_attribute("src") or ""
        if not src and (srcset := img.get_attribute("srcset")):
            # Pick the biggest candidate from srcset.
            candidates = [c.strip().split(" ")[0] for c in srcset.split(",") if c.strip()]
            src = candidates[-1] if candidates else ""
        if not src:
            return None
        r = requests.get(src, timeout=15, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        })
        if r.status_code != 200:
            log.info("image fetch HTTP %d for %s", r.status_code, src[:120])
            return None
        return r.content
    except Exception as e:
        log.warning("fetch_image_bytes failed: %s", e)
        return None


def extract_caption(article: ElementHandle) -> str:
    """Best-effort caption text. IG puts captions in a few places; we
    concatenate what we can find, then truncate to 500 chars (more than
    enough for the VLM)."""
    try:
        # The caption block usually lives in an h1 or a div[dir='auto'] inside
        # the article, paired with the username.
        h = article.query_selector("h1")
        if h:
            t = (h.inner_text() or "").strip()
            if t and len(t) > 5:
                return t[:500]
        spans = article.query_selector_all("span[dir='auto']")
        parts: list[str] = []
        for s in spans[:6]:
            try:
                t = (s.inner_text() or "").strip()
            except Exception:
                continue
            if t and t not in parts:
                parts.append(t)
            if sum(len(p) for p in parts) > 400:
                break
        return " ".join(parts)[:500]
    except Exception as e:
        log.warning("extract_caption failed: %s", e)
        return ""


# -------- hashtag visits --------

def visit_hashtag(page: Page, tag: str) -> None:
    clean = tag.lstrip("#").strip().lower()
    page.goto(
        f"https://www.instagram.com/explore/tags/{clean}/",
        wait_until="domcontentloaded",
    )
    page.wait_for_timeout(random.randint(1800, 3200))
    challenge.inspect_page(page)


# -------- comments --------

def comment_on_post(page: Page, article: ElementHandle, text: str) -> bool:
    """Type `text` into this article's comment box and submit it. Returns
    True on apparent success."""
    try:
        article.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        # First click the Comment speech-bubble to focus the box (some IG
        # variants require this on the feed before the textarea appears).
        comment_icon = article.query_selector("svg[aria-label='Comment']")
        if comment_icon:
            try:
                comment_icon.click(timeout=2000)
                page.wait_for_timeout(random.randint(500, 1200))
            except Exception:
                pass
        textarea = article.query_selector("textarea[aria-label*='comment' i]")
        if not textarea:
            textarea = article.query_selector("textarea")
        if not textarea:
            log.info("skip: comment textarea not found")
            return False
        textarea.click()
        # Type slowly to look human.
        for ch in text:
            textarea.type(ch, delay=random.randint(40, 140))
        page.wait_for_timeout(random.randint(600, 1400))
        # Submit via Enter (IG accepts Return to post).
        textarea.press("Enter")
        page.wait_for_timeout(random.randint(1500, 2500))
        return True
    except Exception as e:
        log.warning("comment_on_post failed: %s", e)
        return False


# -------- story reactions --------

def open_first_story_and_react(page: Page, emojis: list[str] | None = None) -> bool:
    """Open the first story in the tray, let it play for a moment, tap the
    quick-emoji reaction bar at the bottom with a weighted random pick.
    Returns True if we reacted."""
    emojis = emojis or ["❤️", "🥰", "😍", "😂", "🙌", "🔥"]
    try:
        # Click the first story tile in the tray (top of the home page).
        goto_home(page)
        tray = page.locator(
            "ul li button, li[role='button']"
        ).element_handles()
        # The very first ring is your own "Your story"; skip it.
        candidates = [t for t in tray[1:20] if t.is_visible()]
        if not candidates:
            log.info("no visible story tray entries; skipping story reaction")
            return False
        random.choice(candidates[:6]).click(timeout=3000)
        page.wait_for_timeout(random.randint(2500, 4500))
        # The quick-emoji bar appears on story viewer. Each emoji is a button
        # with a role. We click one at random.
        emoji = random.choice(emojis)
        button = page.locator(f"button:has-text('{emoji}')").first
        if button.count() == 0:
            # Fallback: click any visible reaction-bar button.
            bar_buttons = page.locator(
                "section button, div[role='button']:has(img[alt*='emoji' i])"
            ).element_handles()
            if not bar_buttons:
                log.info("story reaction bar not found; closing story")
                page.keyboard.press("Escape")
                return False
            random.choice(bar_buttons[:8]).click(timeout=2000)
        else:
            button.click(timeout=2500)
        page.wait_for_timeout(random.randint(800, 1600))
        page.keyboard.press("Escape")
        return True
    except Exception as e:
        log.warning("open_first_story_and_react failed: %s", e)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
