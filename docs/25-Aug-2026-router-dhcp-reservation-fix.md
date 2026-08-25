# 25-Aug-2026 — Router DHCP reservations fixed; `s7-cam` off its static IP for good

**Done, verified by a full phone reboot.** Router work is Claude's job on this farm — Boss does
not do it and should never be handed a router procedure to run.

---

## The bug

The Archer AX55 reserved `192.168.0.249` for **`8C-F5-A3-B6-5A-E5`** — the S7 handset that was
**retired 10-Aug-2026**. The live phone is `2C-0E-3D-09-77-A4`, so the reservation governed
nothing.

That single mismatch is the root of the whole week: with no working reservation, `.249` could
only be held by a **static IP typed into the phone**, and a static IP is per-saved-network on
Android. So when the phone rebooted onto the guest SSID on 24-Aug it got a guest DHCP address
instead, and `s7-cam` was dark for 16½ hours. See
[`docs/25-Aug-2026-s7-guest-network-incident.md`](docs/25-Aug-2026-s7-guest-network-incident.md).

## What changed

**On the router** (via the new tool below):

| Action | Entry | Why |
|---|---|---|
| **Repointed** | `8C-F5-A3-B6-5A-E5` → **`2C-0E-3D-09-77-A4`**, IP `.249` kept | the reservation now matches the phone that actually exists |
| **Deleted** | `653Pudding` `F0-35-75-81-2C-45` → `.69` | GWTC, retired 10-Aug-2026 |

Used the GUI's **Modify** dialog rather than delete-then-add, so `.249` is never briefly
unclaimed — another device could take it from the pool in that window.

**Left alone deliberately:** `FarmGuardian1 EC-71-DB-4C-AD-53 → .2` and
`Duo2 EC-71-DB-58-70-7E → .14`. Both are Reolink OUIs but neither matches a live camera's known
MAC. **Do not delete what you cannot identify** — a wrong deletion here silently moves a camera.

**On the phone:** switched `653 Pudding Hill 2G Private` from **Static** to **DHCP**. It now
receives `.249` from the reservation instead of asserting it locally.

## Resulting reservation table

```
mini                     D0-11-E5-29-4D-8A    192.168.0.10
Marks-Air                64-76-BA-A2-7E-64    192.168.0.50
farm-pi5                 88-A2-9E-A2-E6-23    192.168.0.17
Mark-MSI-Laptop          9C-B6-D0-06-AF-2F    192.168.0.194
FarmGuardian1            EC-71-DB-4C-AD-53    192.168.0.2     <- unidentified, left alone
FarmGuardian1            BC-09-B9-89-E4-FD    192.168.0.88    <- house-yard
android-32e681a3e96fdb1c 2C-0E-3D-09-77-A4    192.168.0.249   <- s7-cam (FIXED)
Duo2                     78-93-C3-8E-36-0D    192.168.0.155   <- duo2
Duo2                     EC-71-DB-58-70-7E    192.168.0.14    <- unidentified, left alone
```

The router renamed the S7 row to its DHCP hostname `android-32e681a3e96fdb1c`. Cosmetic.

## Verified by rebooting the phone — the exact scenario that broke it

A reboot is what put it on the guest network on 24-Aug, so a reboot is the only honest test:

- Rejoined **`653 Pudding Hill 2G Private`** unattended (Guest is forgotten, so it cannot roam back)
- Took **`192.168.0.249`** by DHCP reservation, ~5s after associating
- IP Webcam **auto-started**, `:8080` open 8s later, `/photo.jpg` → HTTP 200
- `orientation=portrait`, `focusmode=continuous-picture` — settings survived
- Guardian: **6/6 cameras online**

**The static IP is gone from the phone and is no longer load-bearing anywhere.**

## New tool: `tools/router/dhcp_reservations.py`

`list` / `update-mac` / `delete`, driving the AX55 GUI via Playwright (the page does its own
RSA/AES login in JS, so no crypto is reimplemented). Always prints the resulting table — it never
reports success without showing the state it produced.

```bash
/opt/homebrew/bin/python3 tools/router/dhcp_reservations.py list
/opt/homebrew/bin/python3 tools/router/dhcp_reservations.py update-mac --old AA-.. --new BB-..
/opt/homebrew/bin/python3 tools/router/dhcp_reservations.py delete --mac AA-..
```

**⚠️ Traps, all paid for already:**

- **Password is `Bubba123`.** The older scripts in `~/bubba-workspace/tools/router/` hardcode
  `118Oplas`, which is **wrong and rejected**. **Ten failed logins = a 2-hour router lockout**, so
  never "try" a password and do not run those scripts.
- **Playwright is NOT in this repo's `venv`.** Use `/opt/homebrew/bin/python3`.
- **MAC entry is six separate one-octet inputs**, not one field.
- The AX55 renders **hidden duplicates** of most nav labels, so a plain `click()` hits the wrong
  one — `_click_visible()` filters by visibility and x-position.
- Modify icon is index **0**, delete is index **1**, in each row's `span.icon` pair.

## Follow-up

`~/bubba-workspace/tools/router/tplink_*.py` are superseded for reservation work and two of them
carry the wrong password. Worth deleting or re-pointing at this tool so nobody triggers a lockout.
