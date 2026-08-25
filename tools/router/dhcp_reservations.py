# Author: Claude Opus 5
# Date: 25-August-2026
# PURPOSE: Manage TP-Link Archer AX55 DHCP Address Reservations from the Mac Mini —
#          list / update-mac / delete. Router work is Claude's job on this farm, not Boss's
#          (he is non-technical and has said explicitly he depends on us for it), so this
#          exists to make reservation changes a normal, repeatable operation instead of a
#          hand-driven one-shot.
#
#          WHY IT EXISTS (25-Aug-2026): the Galaxy-S7 reservation still pointed at the
#          RETIRED handset's MAC (8C-F5-A3-B6-5A-E5) while the live phone is
#          2C-0E-3D-09-77-A4. That mismatch is what forced s7-cam onto a fragile
#          static-IP-on-the-phone setup, which in turn is why a reboot onto the guest SSID
#          took the camera down for 16.5 hours. See
#          docs/25-Aug-2026-s7-guest-network-incident.md.
#
#          The AX55 GUI performs its own RSA/AES login encryption in page JS, so this drives
#          the real browser via Playwright rather than reimplementing any crypto.
#
#          ⚠️ PASSWORD: `Bubba123`. The OLDER scripts in ~/bubba-workspace/tools/router/ have
#          `118Oplas` hardcoded, which is WRONG and REJECTED. Ten failed logins = a 2-hour
#          router lockout, so do not run those, and do not "try" a password.
#
#          ⚠️ MAC entry is SIX separate one-octet inputs in the Modify dialog, not one field.
#
# SRP/DRY check: Pass — the bubba-workspace scripts are single-purpose one-shots (one adds a
#          hardcoded reservation, one dumps page text) with a stale password; none offers
#          list/update/delete or verifies its own result. This supersedes them for
#          reservation work.
import argparse
import re
import sys
import time

from playwright.sync_api import sync_playwright

ROUTER = "http://192.168.0.1"
ROUTER_PASSWORD = "Bubba123"
RESERVATION_TABLE = "table.su-table__body-table"

# Column order in the reservation table, and which Modify icon is which. Established by DOM
# probe 25-Aug-2026: icon 0 opens "Modify a Reservation Entry", icon 1 deletes.
ICON_MODIFY = 0
ICON_DELETE = 1


def _norm_mac(mac: str) -> str:
    """Normalise any MAC spelling to the router's AA-BB-CC-DD-EE-FF form."""
    hexes = re.findall(r"[0-9A-Fa-f]{2}", mac.replace(":", "-"))
    if len(hexes) != 6:
        raise ValueError(f"not a MAC address: {mac!r}")
    return "-".join(h.upper() for h in hexes)


def _click_visible(page, text: str, x_max: int = 99999) -> bool:
    """Click the first VISIBLE element with exactly this text, left of x_max.

    The AX55 menu renders hidden duplicates of most labels, so a plain click() hits the
    wrong one; x_max keeps us in the left-hand nav.
    """
    loc = page.get_by_text(text, exact=True)
    for i in range(loc.count()):
        el = loc.nth(i)
        try:
            if el.is_visible():
                box = el.bounding_box()
                if box and box["x"] < x_max:
                    el.click()
                    return True
        except Exception:
            continue
    return False


def _open_dhcp_page(page) -> None:
    page.goto(ROUTER, wait_until="domcontentloaded")
    time.sleep(3)
    field = page.locator("input[type=password]").first
    field.click()
    field.fill(ROUTER_PASSWORD)
    field.press("Enter")
    time.sleep(7)
    if not _click_visible(page, "Advanced"):
        raise RuntimeError("could not open Advanced — login probably failed")
    time.sleep(3)
    if not _click_visible(page, "DHCP Server", 400):
        raise RuntimeError("could not open DHCP Server page")
    time.sleep(9)


def read_reservations(page) -> list[dict]:
    rows = page.evaluate(
        """() => {
        const t = document.querySelector('table.su-table__body-table');
        if (!t) return [];
        return [...t.querySelectorAll('tr')].map(r => {
            const c = [...r.querySelectorAll('td')].map(x => x.innerText.trim());
            return c.length >= 3 ? {name: c[0], mac: c[1], ip: c[2]} : null;
        }).filter(Boolean);
    }"""
    )
    return rows


def _row_for_mac(page, mac: str):
    return page.locator(f"{RESERVATION_TABLE} tr", has_text=_norm_mac(mac)).first


def update_mac(page, old_mac: str, new_mac: str) -> None:
    """Repoint an existing reservation at a different device, keeping its IP.

    Uses the Modify dialog rather than delete+add so the reserved IP is never briefly
    unclaimed (another device could grab it from the pool in that window).
    """
    old_norm, new_norm = _norm_mac(old_mac), _norm_mac(new_mac)
    row = _row_for_mac(page, old_norm)
    if row.count() == 0:
        raise RuntimeError(f"no reservation with MAC {old_norm}")
    row.locator("span.icon").nth(ICON_MODIFY).click()
    time.sleep(3)

    # MAC is six single-octet inputs. Identify them as the six 2-char-maxlength boxes
    # that precede the IP field in the dialog.
    octets = new_norm.split("-")
    boxes = page.locator("input:visible")
    mac_inputs = []
    for i in range(boxes.count()):
        el = boxes.nth(i)
        try:
            if (el.get_attribute("maxlength") or "") == "2":
                mac_inputs.append(el)
        except Exception:
            continue
    if len(mac_inputs) < 6:
        raise RuntimeError(f"expected 6 MAC octet inputs, found {len(mac_inputs)}")
    for el, octet in zip(mac_inputs[:6], octets):
        el.click()
        el.fill("")
        el.type(octet, delay=40)
    time.sleep(0.5)
    if not _click_visible(page, "SAVE"):
        raise RuntimeError("could not click SAVE")
    time.sleep(6)


def delete_reservation(page, mac: str) -> None:
    row = _row_for_mac(page, mac)
    if row.count() == 0:
        raise RuntimeError(f"no reservation with MAC {_norm_mac(mac)}")
    row.locator("span.icon").nth(ICON_DELETE).click()
    time.sleep(3)
    # Some firmwares raise a confirm step; click it only if one appeared.
    for label in ("YES", "OK", "CONFIRM", "DELETE"):
        if _click_visible(page, label):
            break
    time.sleep(5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Manage Archer AX55 DHCP reservations.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="print the current reservation table")
    up = sub.add_parser("update-mac", help="repoint a reservation at a new device, keeping its IP")
    up.add_argument("--old", required=True)
    up.add_argument("--new", required=True)
    de = sub.add_parser("delete", help="remove a reservation")
    de.add_argument("--mac", required=True)
    args = ap.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1800})
        page = ctx.new_page()
        page.set_default_timeout(20000)
        try:
            _open_dhcp_page(page)
            if args.cmd == "list":
                pass
            elif args.cmd == "update-mac":
                update_mac(page, args.old, args.new)
                print(f"updated {_norm_mac(args.old)} -> {_norm_mac(args.new)}")
            elif args.cmd == "delete":
                delete_reservation(page, args.mac)
                print(f"deleted {_norm_mac(args.mac)}")
            # Always read back and print the resulting table — never report success
            # without showing the state it produced.
            print(f"\n{'DEVICE':22} {'MAC':20} IP")
            for r in read_reservations(page):
                print(f"  {r['name']:20} {r['mac']:20} {r['ip']}")
        finally:
            ctx.close()
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
