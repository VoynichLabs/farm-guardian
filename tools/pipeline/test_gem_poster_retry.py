# Author: Claude Opus 5
# Date: 07-August-2026
# PURPOSE: Regression cover for gem_poster.post_gem's retry behaviour (v2.67.1).
#          On 07-Aug-2026 four consecutive s7-cam gems that had passed every
#          quality gate were permanently destroyed by a transient Discord
#          http=503, because post_gem made exactly one attempt and the caller
#          ignored its return value. These tests pin the fix so neither
#          regresses.
#
#          Runs against a throwaway local HTTP server on 127.0.0.1 — it never
#          touches Discord and never posts to #farm-2026. Follows this
#          package's existing convention of plain runnable scripts (no pytest
#          in the venv):  venv/bin/python tools/pipeline/test_gem_poster_retry.py
#
# SRP/DRY check: Pass — tests one function's retry contract. Reuses post_gem's
#                own webhook_url/timeout/backoff parameters rather than
#                monkeypatching requests, so it exercises the real code path.

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.gem_poster import post_gem

_STATUS = {"code": 503}
_HITS: list[float] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib naming
        _HITS.append(time.time())
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        code = _STATUS["code"]
        self.send_response(code)
        if code == 429:
            self.send_header("Retry-After", "1")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


JPEG = b"\xff\xd8" + b"x" * 2048
results: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    results.append(condition)
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def main() -> int:
    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/hook"

    print("post_gem retry contract:")

    def attempt(code: int):
        _STATUS["code"] = code
        _HITS.clear()
        ok = post_gem(JPEG, "caption", "s7-cam", url, timeout=8, backoff_seconds=0.2)
        return ok, len(_HITS)

    ok, n = attempt(503)
    check("5xx retries to the attempt limit then gives up",
          n == 3 and ok is False, f"attempts={n} ok={ok}")

    ok, n = attempt(200)
    check("2xx succeeds on the first attempt",
          n == 1 and ok is True, f"attempts={n} ok={ok}")

    # A dead webhook or bad payload must NOT be retried — retrying a 404
    # posts nothing and just burns tick-loop time.
    ok, n = attempt(404)
    check("4xx (non-429) does not retry",
          n == 1 and ok is False, f"attempts={n} ok={ok}")

    ok, n = attempt(429)
    check("429 is treated as retryable",
          n == 3 and ok is False, f"attempts={n} ok={ok}")

    # The whole point of v2.67.1: a gem lost under the old one-shot code now
    # survives a transient upstream failure.
    _STATUS["code"] = 503
    _HITS.clear()

    def recover():
        time.sleep(0.5)
        _STATUS["code"] = 200

    threading.Thread(target=recover, daemon=True).start()
    ok = post_gem(JPEG, "caption", "s7-cam", url, timeout=8, backoff_seconds=0.2)
    check("recovers a gem after a transient 503",
          ok is True and len(_HITS) > 1, f"attempts={len(_HITS)} ok={ok}")

    # Connection-level failure path, and it must stay bounded — post_gem runs
    # inline in the daemon tick loop.
    _HITS.clear()
    started = time.time()
    ok = post_gem(JPEG, "caption", "s7-cam", "http://192.0.2.1:9/hook",
                  timeout=2, backoff_seconds=0.2)
    elapsed = time.time() - started
    check("unroutable host returns False within a bounded time",
          ok is False and elapsed < 20, f"ok={ok} elapsed={elapsed:.1f}s")

    check("missing webhook url is a no-op",
          post_gem(JPEG, "caption", "s7-cam", "") is False)

    server.shutdown()
    failures = len(results) - sum(results)
    print(f"\ngem_poster retry: {sum(results)} passed, {failures} failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
