# Author: Claude Opus 4.8 (1M context) — Bubba coding sub-agent
# Date: 12-June-2026 (v2.41.0 — test for send_motion_alert cooldown gate)
# PURPOSE: Unit test for AlertManager.send_motion_alert (alerts.py). Verifies the per-camera
#          motion-alert cooldown gate WITHOUT any real Discord post or live camera: the
#          network-facing _post_webhook is replaced with a MagicMock returning True, and the
#          AlertManager is constructed with camera_controller=None and called with frame=None,
#          so no HTTP snapshot is attempted. Asserts: (1) the first call for a camera sends,
#          (2) an immediate second call for the SAME camera is suppressed by cooldown,
#          (3) a different camera still sends (proving the cooldown is keyed per-camera).
#          Run from the worktree dir so it imports the worktree's alerts.py (the live repo has
#          its own copy on sys.path otherwise). Standalone — no pytest required.
# SRP/DRY check: Pass — single responsibility is exercising the motion-alert cooldown gate;
#          reuses the real AlertManager rather than re-implementing its logic.

import sys
from unittest.mock import MagicMock

from alerts import AlertManager


def _make_manager(cooldown_seconds: int) -> AlertManager:
    """Build an AlertManager with no camera controller and a known cooldown, then
    stub the network call so nothing is posted to Discord."""
    config = {
        "alerts": {
            # A real-looking URL on purpose: we do NOT want the "webhook not
            # configured -> True" shortcut in _post_webhook to mask the gate. We
            # replace _post_webhook entirely below.
            "discord_webhook_url": "https://discord.com/api/webhooks/123/abc",
            "include_snapshot": False,  # no snapshot path; nothing to fetch/encode
            "mention_on_alert": False,
        },
        "detection": {"alert_cooldown_seconds": cooldown_seconds},
    }
    manager = AlertManager(config, camera_controller=None)
    # Replace the only network-facing method so no real POST happens.
    manager._post_webhook = MagicMock(return_value=True)
    return manager


def test_cooldown_gate() -> None:
    """First call sends; immediate second call for the same camera is suppressed."""
    manager = _make_manager(cooldown_seconds=300)

    first = manager.send_motion_alert("house-yard")
    assert first is True, "first motion alert for a camera should send"
    assert manager._post_webhook.call_count == 1, "first call should hit the webhook once"

    second = manager.send_motion_alert("house-yard")
    assert second is False, "immediate second alert for same camera must be on cooldown"
    assert manager._post_webhook.call_count == 1, (
        "suppressed call must NOT hit the webhook again"
    )

    print("PASS: cooldown gate — first sent, immediate second suppressed (same camera)")


def test_cooldown_is_per_camera() -> None:
    """The cooldown is keyed per-camera, so a different camera still sends."""
    manager = _make_manager(cooldown_seconds=300)

    assert manager.send_motion_alert("house-yard") is True
    # Different camera, still within the first camera's cooldown window.
    assert manager.send_motion_alert("coop-cam") is True, (
        "a different camera must not be blocked by another camera's cooldown"
    )
    assert manager._post_webhook.call_count == 2, "two distinct cameras should post twice"

    print("PASS: cooldown is per-camera — distinct cameras each send")


def test_failed_post_starts_no_cooldown() -> None:
    """A failed post must NOT start a cooldown (timestamp stamped only on success)."""
    manager = _make_manager(cooldown_seconds=300)
    manager._post_webhook = MagicMock(return_value=False)  # simulate a failed POST

    first = manager.send_motion_alert("house-yard")
    assert first is False, "a failed post returns False"

    # Because the failed post started no cooldown, a retry is allowed immediately
    # (and will be attempted again rather than being silently swallowed).
    manager._post_webhook = MagicMock(return_value=True)
    retry = manager.send_motion_alert("house-yard")
    assert retry is True, "after a failed post (no cooldown set), a retry should send"

    print("PASS: failed post starts no cooldown — immediate retry sends")


def main() -> int:
    tests = [
        test_cooldown_gate,
        test_cooldown_is_per_camera,
        test_failed_post_starts_no_cooldown,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL: {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
