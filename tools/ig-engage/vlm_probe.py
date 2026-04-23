# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: One-shot probe to test whether the locally-loaded Qwen3.6-VL
#          on LM Studio will write a short, warm, context-aware Instagram
#          comment when given an image + simulated caption. Some instruction-
#          tuned models refuse "help me comment on Instagram" as a bot-like
#          task; if Qwen3.6 refuses, we fall back to a curated template
#          library. This script tells us which path we're on.
#
# SRP/DRY check: Pass — one-shot smoke test. Not imported by the engager.

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import requests

LM_STUDIO = "http://localhost:1234"
MODEL = "qwen/qwen3.6-35b-a3b"
SAMPLE_IMAGE = Path(
    "/Users/macmini/Documents/GitHub/farm-2026/public/photos/brooder/"
    "2026-04-19-portrait.jpg"
)
SAMPLE_CAPTION = (
    "Day 14 in the brooder. One of our silver-laced Wyandottes turned around "
    "for a portrait this morning. #backyardchickens #babychicks"
)

SYSTEM = (
    "You are a social-media intern helping a hobby-farm owner engage with "
    "other small bird-and-dog accounts on Instagram. You are looking at "
    "someone else's post from their account. Your job is to write a single "
    "short, warm, human-sounding comment to leave on the post.\n\n"
    "Voice rules (these are absolute):\n"
    "- 1 to 8 words.\n"
    "- Lowercase-friendly, casual, kind.\n"
    "- Never markety, never salesy, never \"great content!\".\n"
    "- No emoji spam; 0 or 1 emoji max.\n"
    "- A curiosity question is great (e.g., \"what breed is the striped one?\"). "
    "Builds reciprocity.\n"
    "- Specific beats generic: name what you actually see.\n"
    "- Never mention you are an AI; never reference Instagram itself.\n"
    "- Output ONLY the comment text — no quotes, no label, no explanation."
)


def main() -> int:
    if not SAMPLE_IMAGE.exists():
        print(f"sample image missing: {SAMPLE_IMAGE}", file=sys.stderr)
        return 1
    b64 = base64.b64encode(SAMPLE_IMAGE.read_bytes()).decode("ascii")
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Here's the post. Caption:\n"
                            f"> {SAMPLE_CAPTION}\n\n"
                            "Write the comment."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            },
        ],
        "temperature": 0.8,
        "max_tokens": 60,
        "reasoning": "off",
    }

    print(f"Probing {MODEL} against {SAMPLE_IMAGE.name}…")
    r = requests.post(
        f"{LM_STUDIO}/v1/chat/completions", json=body, timeout=120
    )
    if r.status_code != 200:
        print(f"LM Studio HTTP {r.status_code}: {r.text[:300]}")
        return 2
    data = r.json()
    comment = data["choices"][0]["message"]["content"].strip()
    print("\n--- comment ---")
    print(repr(comment))
    print("---")
    print(f"len chars: {len(comment)}   words: {len(comment.split())}")

    # Refusal detection. Qwen variants sometimes prepend a "I can't help with that"
    # framing if they interpret the task as botting.
    lowered = comment.lower()
    refusal_markers = (
        "can't help",
        "cannot help",
        "i'm not able",
        "not appropriate",
        "as an ai",
        "i cannot",
        "i won't",
        "policy",
    )
    refused = any(m in lowered for m in refusal_markers)
    print(f"refusal detected: {refused}")

    # Run three samples with different captions to check variety.
    print("\n--- variety check (3 extra samples) ---")
    captions = [
        "Mixed flock pile-up under the heat lamp. #turkeypoults #backyardchickens",
        "Pawel and Pawleen posing on the porch. #yorkielovers #smalldogs",
        "Two-week update: the crested chicks are getting puffy. #easteregger",
    ]
    seen = set()
    for cap in captions:
        body["messages"][1]["content"][0]["text"] = (
            f"Here's the post. Caption:\n> {cap}\n\nWrite the comment."
        )
        try:
            r2 = requests.post(
                f"{LM_STUDIO}/v1/chat/completions", json=body, timeout=120
            )
            c = r2.json()["choices"][0]["message"]["content"].strip()
            print(f"  caption: {cap[:45]}…")
            print(f"  comment: {c!r}")
            seen.add(c.lower())
        except Exception as e:
            print(f"  caption: {cap[:45]}…  error: {e}")
    print(f"\nunique comments: {len(seen)}/{len(captions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
