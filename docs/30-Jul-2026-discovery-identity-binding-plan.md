# 30-Jul-2026 — Discovery: bind cameras by hardware identity, stop faking `online` (plan)

**Author:** Claude Fable 5
**Status:** ⏸ AWAITING APPROVAL — no code changed yet.
**Background:** [`docs/30-Jul-2026-reolink-s7-offline-incident.md`](30-Jul-2026-reolink-s7-offline-incident.md)

---

## ⚠️ First: the premise of defect #1 was wrong, and the obvious fix would cause an outage

The task description (written from my own earlier, mistaken conclusion) says a **different**
Reolink at `192.168.0.2` was impersonating `house-yard`, and proposes matching on **MAC**.

That is not what happened, and **matching on MAC would have taken house-yard offline at the
exact moment it recovered.**

`192.168.0.2` *is* house-yard. Evidence gathered after that conclusion was written:

- Its burned-in OSD reads `FarmGuardian1`, bottom-right, on a live 4K frame.
- The frame shows the actual house yard — coop, sunflowers, pink canopy — timestamped
  `30/07/2026 11:05:47`, i.e. current.
- Guardian authenticates against it and has been capturing from it since 10:56:34.

The MAC differs because **Reolink cameras carry a separate MAC per interface** (Ethernet and
Wi-Fi). house-yard came back on its *other* interface, which has no DHCP reservation, so it
landed on `.2` instead of its pinned `.88`. The router's **two `Duo2` reservations**
(`78-93-C3-8E-36-0D`→`.155` and `EC-71-DB-58-70-7E`→`.14`) are the same phenomenon recorded for
the other camera, and the CHANGELOG's "drifted from .14/.15 after the 4-Jul ethernet→WiFi flip"
is that event.

**So discovery's rebind to `.2` was correct.** A MAC-equality guard would have rejected it.

The real weakness is narrower but genuine: discovery binds on a **human-editable name**, so two
cameras sharing a name are indistinguishable. The right identifier is the one that is stable
*per device* rather than per interface.

### The identifier to use: `serial`

Confirmed in the vendored library, `venv/lib/python3.13/site-packages/reolink_aio/api.py`:

| Property | Line | Source | Stable across interfaces? |
|---|---|---|---|
| `mac_address` | 382 | per-NIC | ❌ **no — this is the trap above** |
| `serial` | 387 | `dev_info["serial"]` via `GetDevInfo` (line 3793) | ✅ yes |
| `uid` | 391 | `P2p.uid`, extra command (line 3854) | ✅ yes, but costs a second call |

`GetDevInfo` is *already* the call discovery makes, and it already returns `serial` in the same
response — so this costs **zero additional requests**.

Also worth separating: the `mba-cam`/`usb-cam` mislabeling in `HARDWARE_INVENTORY.md` was
caused by `USB_CAM_PREFER_EXTERNAL` on the camera host, a completely different mechanism. It is
not evidence for this change and shouldn't be cited as such.

## Scope

**In scope**

1. Bind Reolink cameras on `serial`, with name as a fallback only until a serial is learned.
2. Refuse — loudly — to rebind when a known serial does not match.
3. Make `rtsp_url_override` stop asserting `online`; probe the port instead.

**Out of scope**

- MAC-based matching (see above).
- Reserving both MACs per camera on the router — an operational fix, worth doing, but manual
  and needs Boss at the router UI.
- The stale-frame archive bug — separate plan, already written.

## Affected cameras (small blast radius)

| Camera | `device_name` | `rtsp_url_override` | Touched by |
|---|---|---|---|
| `house-yard` | `FarmGuardian1` | no | defect 1 |
| `duo2` | `Duo2` | **yes** | defects 1 and 2 |
| `gwtc` | — | yes (**disabled**) | defect 2 |
| everything else | — | no | neither |

## Architecture

### Defect 1 — identity binding

`_reolink_device_name()` (discovery.py:365) currently returns `DevInfo["name"]`. Widen it to
`_reolink_device_identity() -> Optional[tuple[str, str]]` returning `(name, serial)` from the
same `GetDevInfo` response. `resolve_reolink_ip()` (line 269) and `_find_reolink_by_name()`
(line 316) then prefer serial.

**Trust-on-first-use**, because no serial is recorded anywhere today:

- No serial known → match on name as now, **learn and store** the serial, log that the bind was
  name-only and therefore unverified.
- Serial known → require it to match. A name match with a different serial is **refused** and
  logged at ERROR with both serials.

**Store the learned serial in a sidecar, not `config.json`.** `config.json` is tracked in git
and contains the camera password; having a long-running daemon rewrite it invites churn and
accidental credential commits. `resolve_reolink_ip` already mutates `cam_cfg` **in memory only**,
so a sidecar (`data/camera-identity.json`) matches existing behaviour and keeps git clean.

### Defect 2 — `rtsp_url_override` asserting `online`

discovery.py:189–209 builds `CameraInfo(..., online=True)` unconditionally. The helper needed
already exists — `_rtsp_port_open()` at line 342. Fix:

- `urlparse` the override for host and port (do **not** assume 554).
- `online = self._rtsp_port_open(host, port)`.
- Still register the camera either way, so the dashboard keeps listing it.

Also fix the logging: it currently emits `Camera 'duo2' online (manual RTSP)` **every 5 minutes**
regardless of state, four seconds after logging that the camera is probably powered off. Log on
state *transition* only.

## TODOs (ordered)

1. Widen `_reolink_device_name` → `_reolink_device_identity`, returning `(name, serial)`.
2. Add the sidecar identity store (read/write `data/camera-identity.json`).
3. Rework `resolve_reolink_ip` / `_find_reolink_by_name` for serial-preferred matching with
   trust-on-first-use and a loud refusal path.
4. Probe the port in the `rtsp_override` branch; set `online` from the result.
5. Convert both discovery log sites to transition-only logging.
6. **Verification — two live fixtures available right now:**
   - `duo2` is genuinely off the network and un-powered → must report `online=False`. Today it
     falsely reports online every 5 minutes, so this is directly observable.
   - `house-yard` is live at `.2` with **no recorded serial** → must stay online, must bind via
     the name fallback, and must learn its serial. It must **not** be unbound; that is the
     regression that matters most.
   - Confirm `gwtc` (disabled) and the six non-Reolink cameras are untouched.
   - Confirm the dashboard and `guardian.markbarney.net` still list every camera.
7. `CHANGELOG.md` top entry (SemVer minor — behaviour change), file headers on `discovery.py`.

## Risks

- **⚠️ The LAN sweep is a credential-lockout hazard.** `_find_reolink_by_name` attempts a Login
  against *every* host on the /24 with port 554 open, using this camera's credentials. Against
  any other Reolink on the network that is a failed login, and Reolink locks out after repeated
  failures. I tripped exactly this lockout by hand during the investigation — `.2` refused
  `Login` with `rspCode -7` for 30+ minutes while Guardian's already-authenticated session kept
  working fine. **This change must not increase login volume**, and ideally the sweep should
  stop early once matched. Worth a follow-up to rate-limit or cache negative results.
- **Trust-on-first-use learns a wrong serial if run while mis-bound.** house-yard is correctly
  bound right now, so learning today is safe — but it means the first run should be done
  deliberately, not during an outage.
- **A camera that is offline when this ships never gets its serial learned** (duo2). It will
  fall back to name matching whenever it returns. Acceptable, and it should log that clearly.
- Anything consuming `online` will start seeing `False` for duo2. That is the point, but the
  reel/alert lanes should be checked for code that assumes a camera is always online.

## Docs / changelog touchpoints

- `CHANGELOG.md` — top entry, what/why/how, author.
- `CLAUDE.md` — short note that Reolinks have per-interface MACs and that `device_name` is not
  an identity; pin **both** MACs when adding a DHCP reservation.
- `HARDWARE_INVENTORY.md` — record house-yard's dual MAC (`BC-09-B9-89-E4-FD` reserved →`.88`,
  `EC-71-DB-4C-AD-53` currently →`.2`) and note the distinct cause of the mba-cam/usb-cam case.
- File headers on every Python file touched.
