#!/usr/bin/env python3
# Author: Claude Haiku 5.5 Medium
# Date: 05-Nov-2026
# PURPOSE: Build a labelled table of Samsung S7 gem posts from #farm-2026, with the
#          Boss's reactions as the label (1 = he reacted, 0 = passed over) and the
#          archive frame behind each post joined on, so the posted frames can be
#          studied as a dataset and the score can be checked against what he likes.
#
#          Steps:
#            1. One paged listing of the channel back to the 2026 season, cached as JSON
#               on the SSD so later runs never re-list. Only Guardian webhook posts with
#               a "⭐ N/100" score line from the S7 are kept. No per-person reactor
#               lookups are made. Rate limits (429) wait and retry the same page; any
#               other failure raises, so the listing never stops silently.
#            2. Load the S7 archive rows since the scale change (read-only), with their
#               scoring details.
#            3. Link each post to its frame:
#                 exact_stamp        the sync stamped this post's message ID on the frame
#                                    (only posts that drew reactions carry a stamp)
#                 time_score_unique  exactly one frame was captured in the minute before
#                                    the post with the same score. Measured gap between a
#                                    frame and its post: one to eleven seconds.
#                 caption_tiebreak   several frames matched on time and score; exactly
#                                    one has a caption that starts with the posted caption
#                 ambiguous          several frames matched and no caption tiebreak; the
#                                    row is kept but flagged and should be excluded
#                 no_frame_found     no frame in the minute before the post with that score
#            4. Validate the link method on the reacted posts: ignore their stamp, re-link
#               by time, score and caption, and check the stamped frame is recovered.
#            5. Write the CSV and a readme to the SSD kaggle folder, and print summaries.
#
#          Posted text: Discord posts begin with a mention line (the Boss's ID and a
#          heading), then the caption, then the score line. The mention line is removed
#          before comparing captions. A trailing ellipsis from the Discord trim is removed.
#
#          Each row also carries scoring_era, because the score's formula changed on
#          12-Aug (floor), 19-Sep (frame-fill term) and 02-Oct (measured fill). Analyses
#          must stay within one era when asking what the score rewards.
#          Integration: tools/discord_harvester.py (bot token, API base, channel, headers);
#          data/guardian.db opened read-only. Output lives under
#          /Volumes/Team 4TB/kaggle/s7-reaction-20261105/ (big data goes on the SSD).
#          Run with the project venv: venv/bin/python scripts/s7-reaction-dataset.py
#          Add --refresh to re-list the channel instead of using the cached listing.
# SRP/DRY check: Pass — Discord plumbing is imported from discord_harvester; the archive
#                read is the only DB access; no pipeline or posting code is touched.

import argparse
import bisect
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# data/ is gitignored, so the live database lives only in the main checkout, not in this worktree.
DB_PATH = "/Users/macmini/GitHub/farm-guardian/data/guardian.db"
sys.path.insert(0, os.path.join(REPO, "tools"))
import discord_harvester as dh  # noqa: E402  (path inserted above)

BASE_DIR = "/Volumes/Team 4TB/kaggle/s7-reaction-20261105"
CACHE_PATH = os.path.join(BASE_DIR, "cache", "s7_posts.json")
CSV_PATH = os.path.join(BASE_DIR, "s7_posts_labelled.csv")
README_PATH = os.path.join(BASE_DIR, "README.md")

SCORE_RE = re.compile(r"⭐\s*(\d+)\s*/\s*100")
S7_ATTACHMENT = "s7-cam-gem.jpg"
S7_DISPLAY_NAME = "S7 Birdcatraz"
SEASON_START = datetime(2026, 4, 1, tzinfo=timezone.utc)
# The posted score moved to the 0-100 scale in July; earlier posts used a different scale.
SCALE_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
# The posting floor moved from 65 to 80 on 12 August; the same-era check uses this date.
FLOOR_80_START = datetime(2026, 8, 12, tzinfo=timezone.utc)
# A frame is linked to a post only if it was captured within this many seconds before it.
LINK_WINDOW = timedelta(seconds=60)


def parse_discord_time(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def parse_archive_time(text):
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def list_channel_back_to(token, cutoff):
    """Page backwards through the channel until messages are older than cutoff.
    429 waits and retries the same page; any other error raises, so nothing is dropped."""
    headers = dh.discord_headers(token)
    collected = []
    before = None
    pages = 0
    while True:
        url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages?limit=100"
        if before:
            url += f"&before={before}"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 429:
            wait = float(resp.json().get("retry_after", 1.0))
            time.sleep(wait + 0.25)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"listing stopped: http {resp.status_code}: {resp.text[:160]}")
        page = resp.json()
        if not page:
            break
        collected.extend(page)
        pages += 1
        before = page[-1]["id"]
        if parse_discord_time(page[-1]["timestamp"]) < cutoff:
            break
        time.sleep(0.5)
    print(f"channel pages read: {pages}, messages listed: {len(collected)}", flush=True)
    return collected


def is_s7_post(msg):
    names = [a.get("filename", "") for a in (msg.get("attachments") or [])]
    if S7_ATTACHMENT in names:
        return True
    return (msg.get("author") or {}).get("username", "") == S7_DISPLAY_NAME


def extract_s7_posts(messages):
    """Keep S7 webhook posts that carry a score line, with the text needed for linking."""
    posts = []
    for msg in messages:
        if not msg.get("webhook_id"):
            continue
        content = msg.get("content", "") or ""
        match = SCORE_RE.search(content)
        if not match or not is_s7_post(msg):
            continue
        posts.append({
            "msg_id": msg["id"],
            "timestamp": msg["timestamp"],
            "score": int(match.group(1)),
            "content": content,
            "total_reactions_on_list": sum(int(r.get("count", 0)) for r in (msg.get("reactions") or [])),
        })
    return posts


def load_or_list_posts(refresh):
    """Use the cached listing unless asked to refresh, so the channel is listed once."""
    if not refresh and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as fh:
            posts = json.load(fh)
        print(f"using cached listing: {len(posts)} S7 posts", flush=True)
        return posts
    token = dh.load_bot_token()
    messages = list_channel_back_to(token, SEASON_START)
    posts = extract_s7_posts(messages)
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(posts, fh, ensure_ascii=False)
    print(f"listed and cached: {len(posts)} S7 posts", flush=True)
    return posts


def load_archive_rows():
    """S7 archive rows since the scale change, read-only, with the scoring details."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, ts, image_path, discord_message_id, share_worth, vlm_json "
            "FROM image_archive WHERE camera_id = 's7-cam' AND ts >= ? ORDER BY ts",
            (SCALE_START.isoformat(),),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row_id, ts, image_path, msg_id, share_worth, vlm_text in rows:
        try:
            vlm = json.loads(vlm_text or "{}")
        except json.JSONDecodeError:
            continue
        score = vlm.get("overall_score")
        if not isinstance(score, int) or isinstance(score, bool):
            continue
        out.append({
            "row_id": row_id,
            "ts": parse_archive_time(ts),
            "image_path": image_path,
            "stamped_msg_id": msg_id,
            "share_worth": share_worth,
            "score": score,
            "caption": (vlm.get("caption_draft") or "").strip(),
            "image_quality": vlm.get("image_quality"),
            "activity": vlm.get("activity"),
            "bird_count": vlm.get("bird_count"),
            "scene": vlm.get("scene"),
            "lighting": vlm.get("lighting"),
            "composition": vlm.get("composition"),
            "bird_face_visible": vlm.get("bird_face_visible"),
            "standout_bird": vlm.get("standout_bird"),
            "expression_score": vlm.get("expression_score"),
            "detail_score": vlm.get("detail_score"),
            "subject_coverage_pct": vlm.get("subject_coverage_pct"),
            "largest_subject_pct": vlm.get("largest_subject_pct"),
        })
    return out


def post_caption_text(content):
    """The caption as posted. Drops the leading mention-and-heading line, stops at the
    score line, and removes the ellipsis the Discord trim may add."""
    lines = content.split("\n")
    if lines and lines[0].startswith("<@"):
        lines = lines[1:]
    text = "\n".join(lines).split("⭐", 1)[0].rstrip()
    return text[:-1].rstrip() if text.endswith("…") else text


class FrameIndex:
    """Archive rows sorted by capture time, so each post's window is a slice."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda row: row["ts"])
        self.times = [row["ts"] for row in self.rows]

    def frames_before(self, post_time):
        lo = bisect.bisect_left(self.times, post_time - LINK_WINDOW)
        hi = bisect.bisect_right(self.times, post_time)
        return self.rows[lo:hi]


def candidate_frames(index, post):
    """Frames captured in the minute before the post with the same score."""
    post_time = parse_discord_time(post["timestamp"])
    return [row for row in index.frames_before(post_time) if row["score"] == post["score"]]


def link_post(index, post):
    """Return (frame_or_None, method) for a post that carries no stamp."""
    candidates = candidate_frames(index, post)
    if not candidates:
        return None, "no_frame_found"
    if len(candidates) == 1:
        return candidates[0], "time_score_unique"
    caption = post_caption_text(post["content"])
    matched = [row for row in candidates if caption and row["caption"].startswith(caption)]
    if len(matched) == 1:
        return matched[0], "caption_tiebreak"
    # Several frames fit and the caption does not decide it: keep the latest, but flag it.
    return max(candidates, key=lambda row: row["ts"]), "ambiguous"


def link_posts(posts, rows, index):
    stamped = {row["stamped_msg_id"]: row for row in rows if row["stamped_msg_id"]}
    linked = []
    for post in posts:
        if parse_discord_time(post["timestamp"]) < SCALE_START:
            continue
        if post["msg_id"] in stamped:
            frame, method, reacted = stamped[post["msg_id"]], "exact_stamp", 1
        else:
            frame, method = link_post(index, post)
            reacted = 0
        linked.append({"post": post, "frame": frame, "method": method, "reacted": reacted})
    return linked


def validate_linker(linked, index):
    """Re-link reacted posts ignoring their stamp. Reports whether the stamped frame is among
    the candidates, and how often the unforced link (unique or caption tiebreak) is right."""
    total = in_candidates = forced_right = forced_total = 0
    for item in linked:
        if item["method"] != "exact_stamp":
            continue
        total += 1
        truth = item["frame"]["row_id"]
        candidates = candidate_frames(index, item["post"])
        if any(row["row_id"] == truth for row in candidates):
            in_candidates += 1
        frame, method = link_post(index, item["post"])
        if method in ("time_score_unique", "caption_tiebreak"):
            forced_total += 1
            if frame is not None and frame["row_id"] == truth:
                forced_right += 1
    return {
        "reacted_posts": total,
        "stamped_frame_among_candidates": in_candidates,
        "confident_links": forced_total,
        "confident_links_correct": forced_right,
    }


# The score's formula changed twice. Posts from different eras must not be pooled when the
# question is what the score rewards: see CHANGELOG v2.68.0 (08-Aug: no frame-fill term),
# the fill axis added 19-Sep, and the measured-fill reading from 02-Oct (v2.75.1).
SCORING_ERAS = [
    (datetime(2026, 8, 12, tzinfo=timezone.utc), datetime(2026, 9, 19, tzinfo=timezone.utc), "A_no_fill"),
    (datetime(2026, 9, 19, tzinfo=timezone.utc), datetime(2026, 10, 2, tzinfo=timezone.utc), "B1_vlm_fill"),
    (datetime(2026, 10, 2, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc), "B2_measured_fill"),
]


def scoring_era(posted_at_text):
    """Name of the scoring era a post falls in. Before 12 August the floor was 65 and the
    score was on the older scale, so those posts are labelled pre_floor_80."""
    when = parse_discord_time(posted_at_text)
    for start, end, name in SCORING_ERAS:
        if start <= when < end:
            return name
    return "pre_floor_80"


CSV_COLUMNS = [
    "msg_id", "posted_at", "scoring_era", "score", "reacted", "link_method",
    "frame_row_id", "frame_captured_at", "image_path", "share_worth", "image_quality",
    "activity", "bird_count", "scene", "lighting", "composition", "bird_face_visible",
    "standout_bird", "expression_score", "detail_score", "subject_coverage_pct",
    "largest_subject_pct", "posted_caption",
]


def write_csv(linked):
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in linked:
            frame = item["frame"] or {}
            writer.writerow({
                "msg_id": item["post"]["msg_id"],
                "posted_at": item["post"]["timestamp"],
                "scoring_era": scoring_era(item["post"]["timestamp"]),
                "score": item["post"]["score"],
                "reacted": item["reacted"],
                "link_method": item["method"],
                "frame_row_id": frame.get("row_id", ""),
                "frame_captured_at": frame["ts"].isoformat() if frame else "",
                "image_path": frame.get("image_path", ""),
                "share_worth": frame.get("share_worth", ""),
                "image_quality": frame.get("image_quality", ""),
                "activity": frame.get("activity", ""),
                "bird_count": frame.get("bird_count", ""),
                "scene": frame.get("scene", ""),
                "lighting": frame.get("lighting", ""),
                "composition": frame.get("composition", ""),
                "bird_face_visible": frame.get("bird_face_visible", ""),
                "standout_bird": frame.get("standout_bird", ""),
                "expression_score": frame.get("expression_score", ""),
                "detail_score": frame.get("detail_score", ""),
                "subject_coverage_pct": frame.get("subject_coverage_pct", ""),
                "largest_subject_pct": frame.get("largest_subject_pct", ""),
                "posted_caption": post_caption_text(item["post"]["content"]),
            })


def write_readme(validation, method_counts):
    lines = [
        "# S7 posted frames labelled by the Boss's reactions",
        "",
        "One row per Samsung S7 gem post in #farm-2026 since July 2026.",
        "",
        "- reacted = 1 when the Boss reacted to the post, 0 when the post drew no reaction.",
        "  A 0 means no reaction at all, not a verdict; the Boss may not have looked.",
        "- link_method says how the post was tied to its archive frame. exact_stamp and",
        "  time_score_unique and caption_tiebreak are confident. ambiguous means several frames",
        "  fit and the caption did not decide it; exclude those from analysis. no_frame_found",
        "  means no frame with that score was captured in the minute before the post.",
        "- The scoring details (score, sub-scores, activity, bird count, scene, image quality)",
        "  are outputs of a small vision-language model, not human labels. Treat them as",
        "  features to test, not as truth.",
        "- frame_captured_at is the frame's capture time, which is under one to eleven seconds",
        "  before the post for stamped posts.",
        "- scoring_era says which scoring rules applied when the post was made. The score's",
        "  formula changed on 12 August (floor), 19 September (frame-fill term) and 2 October",
        "  (measured fill). Never pool eras when asking what the score rewards.",
        "- Frames pruned from disk keep their row, but their image file may be missing.",
        "",
        f"Linker check on reacted posts: {json.dumps(validation)}",
        f"Link method counts: {json.dumps(method_counts)}",
    ]
    with open(README_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def summarise(linked):
    """Reaction rate by score band: same-era (floor 80) and week-old posts, excluding
    ambiguous and unlinked posts."""
    now = datetime.now(timezone.utc)
    era = [
        item for item in linked
        if parse_discord_time(item["post"]["timestamp"]) >= FLOOR_80_START
        and item["method"] not in ("ambiguous", "no_frame_found")
        and (now - parse_discord_time(item["post"]["timestamp"])).days >= 7
    ]
    print(f"same-era, week-old, confidently linked posts: {len(era)}", flush=True)
    for low, high in ((80, 84), (85, 89), (90, 94), (95, 100)):
        band = [item for item in era if low <= item["post"]["score"] <= high]
        if band:
            rate = sum(item["reacted"] for item in band) / len(band)
            print(f"  score {low}-{high}: posts={len(band)} reacted_share={rate:.3f}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-list the channel")
    args = parser.parse_args()

    posts = load_or_list_posts(args.refresh)
    rows = load_archive_rows()
    index = FrameIndex(rows)
    linked = link_posts(posts, rows, index)
    validation = validate_linker(linked, index)

    method_counts = {}
    for item in linked:
        method_counts[item["method"]] = method_counts.get(item["method"], 0) + 1
    write_csv(linked)
    write_readme(validation, method_counts)

    print(f"linked posts since the scale change: {len(linked)}", flush=True)
    for method, count in sorted(method_counts.items()):
        print(f"  {method}: {count}", flush=True)
    print(f"validation: {json.dumps(validation)}", flush=True)
    summarise(linked)
    print(f"out={CSV_PATH}", flush=True)


if __name__ == "__main__":
    main()
