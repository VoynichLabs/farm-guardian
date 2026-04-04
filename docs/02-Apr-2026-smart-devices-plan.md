# Smart Device Deterrent Integration — Farm Guardian

**Author:** Bubba (Claude Opus 4.6)
**Date:** 02-April-2026
**Status:** Plan — Ready for implementation
**Context:** Hawk attack confirmed today (02-Apr, ~3:15 PM EDT) — predator carried off a hen from beside the coop in broad daylight. The passive inflatable tube man was not running at the time and even when active has proven ineffective as a static/predictable deterrent.

---

## The Idea

We already have a wacky waving inflatable tube man near the coop, plugged into an outdoor outlet. It doesn't work as a passive deterrent because hawks habituate to predictable stimuli. But if it's **off 99% of the time** and **erupts to life the instant a predator is detected**, it becomes an unpredictable, multi-sensory ambush that the hawk cannot learn to ignore.

The integration is simple: WiFi smart plugs on the same local network as the Mac Mini, controlled via `python-kasa` (local API, no cloud). Guardian detects a predator → fires the smart plugs → physical devices activate.

This is not limited to the tube man. Any device that plugs into an outlet becomes a programmable deterrent: sprinklers (solenoid valve on a hose), speakers, lights, whatever. Each plug is just another toggle in the deterrent config.

---

## Hardware to Buy

### Primary: Kasa EP40 — Outdoor Smart Plug (Dual Outlet)

**Amazon:** https://www.amazon.com/dp/B091FXH2FR

| Spec | Detail |
|------|--------|
| **Model** | TP-Link Kasa EP40 |
| **ASIN** | B091FXH2FR |
| **Price** | $19.98 (sale, normally $24.99) |
| **Delivery** | Prime — Tomorrow (Apr 3) |
| **Rating** | 4.6/5 — 18,491 ratings |
| **Outlets** | 2 (independently controllable) |
| **Weather** | IP64 — rain, dust, outdoor rated |
| **Max Load** | 15A / 1875W per outlet |
| **WiFi** | 2.4GHz (standard for smart home devices) |
| **Hub** | No hub required |
| **Local Control** | ✅ Confirmed supported by `python-kasa` library |
| **Cloud Required** | No — `python-kasa` communicates directly on LAN |

**Why this one:**
- Two outlets = tube man on one, sprinkler solenoid valve on the other
- IP64 weatherproof — it's going outside near the coop
- `python-kasa` has explicit support for Kasa EP-series outdoor plugs
- 18K+ reviews, battle-tested hardware
- $20 — trivial cost

**Buy link:** https://www.amazon.com/dp/B091FXH2FR

### Optional Second Plug: Kasa KP401 — Outdoor Smart Plug (Single Outlet)

**Amazon:** https://www.amazon.com/dp/B099KLNM24

- $15.99, single outlet, same IP64 rating, 12K+ reviews
- Good for a dedicated sprinkler line or a speaker
- Same `python-kasa` support

### Sprinkler Integration

The boss already has sprinklers. To make them Guardian-controlled:

**Orbit 58874N Hose Faucet Timer / Solenoid Valve** (~$35 on Amazon) — or any 24V solenoid valve on the hose bib. Plug it into the smart plug outlet. When Guardian fires the plug, water flows. When it cuts power, water stops. No plumbing, no wiring — just screw it onto the hose.

Alternatively, any electric sprinkler timer with a "manual override" button can be hot-wired to stay on when powered, so the smart plug acts as the on/off switch.

---

## Software Architecture

### New Module: `smart_devices.py`

Single responsibility: discover and control smart plugs on the local network.

```python
# smart_devices.py — WiFi Smart Plug Control for Farm Guardian
#
# Uses python-kasa to control TP-Link Kasa smart plugs on the local network.
# Each plug is registered in config.json with a name, IP, and which outlet(s)
# it controls. The deterrent engine calls activate/deactivate by device name.
#
# All communication is local (LAN only, no cloud). python-kasa sends encrypted
# commands directly to the plug's IP address.

from kasa import SmartPlug, Discover

class SmartDeviceManager:
    """Manages WiFi smart plugs as physical deterrent actuators."""

    async def discover_devices(self) -> list
    async def activate(self, device_name: str, duration_seconds: int = 120) -> bool
    async def deactivate(self, device_name: str) -> bool
    async def activate_outlet(self, device_name: str, outlet: int, duration: int) -> bool
    async def status(self, device_name: str) -> dict
    async def all_off(self) -> None  # emergency kill switch
```

### Config Addition

Add to `config.json`:

```json
{
  "smart_devices": {
    "enabled": true,
    "devices": [
      {
        "name": "tube-man",
        "ip": "192.168.0.XXX",
        "type": "kasa",
        "model": "EP40",
        "outlet": 0,
        "description": "Wacky waving inflatable tube man near coop"
      },
      {
        "name": "sprinkler",
        "ip": "192.168.0.XXX",
        "type": "kasa",
        "model": "EP40",
        "outlet": 1,
        "description": "Solenoid valve on garden hose — ground predator deterrent"
      }
    ]
  }
}
```

### Integration with `deterrent.py`

The existing deterrent response engine (from PLAN_V2.md) gets expanded to include smart device actions alongside camera features:

```json
{
  "deterrent": {
    "response_rules": {
      "hawk": {
        "level": 2,
        "actions": ["spotlight", "audio_alarm", "tube-man"]
      },
      "fox": {
        "level": 3,
        "actions": ["spotlight", "siren", "audio_alarm", "tube-man", "sprinkler"]
      },
      "coyote": {
        "level": 3,
        "actions": ["spotlight", "siren", "audio_alarm", "tube-man", "sprinkler"]
      },
      "raccoon": {
        "level": 2,
        "actions": ["spotlight", "audio_alarm", "sprinkler"]
      },
      "cat": {
        "level": 1,
        "actions": ["sprinkler"]
      },
      "bear": {
        "level": 3,
        "actions": ["spotlight", "siren", "audio_alarm", "tube-man"]
      }
    },
    "device_durations": {
      "tube-man": 120,
      "sprinkler": 30
    }
  }
}
```

### Detection → Response Flow

```
YOLO detects hawk (confidence > threshold, dwell > 3 frames)
    │
    ├── Camera: spotlight ON, audio alarm
    ├── Smart plug "tube-man": outlet 0 ON (120s auto-off)
    ├── Smart plug "sprinkler": outlet 1 ON (30s auto-off) [ground predators only]
    ├── Discord: alert with snapshot
    └── Log: deterrent_actions table
    
    ... 120 seconds later (or when animal leaves) ...
    
    ├── Camera: spotlight OFF
    ├── Smart plug "tube-man": outlet 0 OFF
    └── Log: outcome = "deterred" or "no_effect"
```

### Why This Works (Predator Psychology)

1. **Unpredictability** — The tube man is dormant 99% of the time. The hawk has no pattern to learn. When it fires, it's a genuine surprise.

2. **Multi-sensory** — Light (camera spotlight) + sound (siren/audio alarm) + chaotic physical movement (tube man) + water (sprinkler for ground predators). Multiple stimuli simultaneously overwhelm the animal's threat assessment.

3. **Reactive, not passive** — The deterrent fires in direct response to the predator's presence. The animal associates the location with danger, not with predictable background noise.

4. **Escalating** — Level 1 (spotlight) for low threats, Level 3 (everything at once) for high threats. Cats get a sprinkler spray. Bears get the full arsenal.

5. **Measurable** — Every activation is logged. We track whether the animal left within 60 seconds. Over time, we learn which deterrents work best for which species.

---

## Dependencies

Add to `requirements.txt`:

```
python-kasa>=0.7.0
```

`python-kasa` is the community-maintained Python library for TP-Link smart home devices. It communicates directly with devices on the local network — no cloud, no TP-Link account, no app required. Supports Kasa EP40, KP401, and all other Kasa/Tapo devices.

**Library docs:** https://python-kasa.readthedocs.io/en/stable/
**GitHub:** https://github.com/python-kasa/python-kasa

---

## Implementation Notes for the Developer

1. **python-kasa is async** — use `asyncio`. The existing Guardian codebase is threaded (not async), so you'll need to bridge with `asyncio.run()` or `loop.run_in_executor()` from the deterrent callback. Keep the async boundary clean — `smart_devices.py` is async internally, but exposes sync wrappers for the threaded caller.

2. **EP40 has two outlets** — addressed as child devices in python-kasa. Use `SmartPlug` with `child_id` to control each outlet independently. Test with `kasa discover` on the command line first to confirm the device shows up and responds.

3. **Auto-off timer** — implement in `smart_devices.py`, not in the plug itself. Use `asyncio.sleep(duration)` then `turn_off()`. This keeps the logic in our code where we can log it, not hidden in plug firmware.

4. **Emergency kill switch** — `all_off()` method that turns off every registered device immediately. Wire this to Guardian's shutdown handler so devices don't stay on if the service crashes.

5. **Device discovery** — on startup, attempt to connect to each configured device IP. Log which ones are reachable. Don't crash if a device is offline — just log a warning and skip it. Re-check on each deterrent activation.

6. **Initial setup of the Kasa plug** — the EP40 needs to be provisioned once via the Kasa app on a phone (to connect it to the home WiFi network). After that, `python-kasa` communicates with it directly by IP. The Kasa app is only needed for initial WiFi setup — never again after that.

---

## Incident Log

**02-April-2026, ~3:15 PM EDT** — Massive squawk heard from yard. Pawleen (Yorkshire terrier) alerted aggressively. Found large pile of feathers near the coop. No carcass, no injured bird found. Inflatable tube man was not running at the time. Assessment: hawk strike, likely red-tailed hawk based on time of day (mid-afternoon) and method (strike-and-pluck pattern). Bird appears to have been carried off. Feather pattern consistent with predator plucking, not chicken-on-chicken aggression.

Camera (Reolink E1 Outdoor Pro) arriving ~03-April-2026. Smart plug can arrive same day if ordered today.

---

*This plan is ready for implementation. The developer should start with `smart_devices.py` as a standalone module, test it against the EP40 once it arrives, then wire it into `deterrent.py`.*
