# Step-and-Dwell Patrol Plan — 08-Apr-2026

## Goal

Replace the continuous sweep patrol with step-and-dwell. The continuous sweep moves at ~70°/second, producing motion-blurred frames that are useless for detection. Step-and-dwell stops at each position for clean frame capture.

## Scope

**In scope:**
- Generate evenly-spaced patrol positions across the useful pan range
- Skip dead zone positions (mounting post)
- Move to each position, stop, settle, autofocus, dwell for configurable duration
- Reverse direction at boundaries (ping-pong pattern)
- Deterrent pause/resume integration

**Out of scope:**
- Tilt management (deferred — needs calibration)
- Sky-watch mode for hawks (future)
- Detection-weighted dwell times (future — bias toward hot zones)

## Design

### Patrol positions

With `step_degrees: 30` and `dead_zone_pan: [6800, 440]`, the positions are:
- 600 (30°), 1200 (60°), 1800 (90°), 2400 (120°), 3000 (150°), 3600 (180°), 4200 (210°), 4800 (240°), 5400 (270°), 6000 (300°), 6600 (330°)
- 11 positions, each dwelling 8 seconds = ~2 minute full cycle

### Sequence at each position

1. Move to pan target at speed 8 (slow, controlled)
2. Stop, wait 3 seconds for camera to physically settle
3. Trigger autofocus, wait for lens to lock
4. Dwell 8 seconds — camera is stationary, capturing clean frames
5. Advance to next position

### Config (`ptz.sweep`)

| Key | Default | Purpose |
|-----|---------|---------|
| `step_degrees` | 30 | Degrees between patrol positions |
| `dwell_seconds` | 8 | How long to hold at each position |
| `move_speed` | 8 | PTZ speed for repositioning (1-64) |
| `settle_seconds` | 3 | Wait after stop for camera to physically stabilize |
| `dead_zone_pan` | null | [start, end] pan range to skip |
| `positioning_tolerance` | 300 | Pan units considered "close enough" (~15°) |
| `position_poll_interval` | 0.3 | How often to check position during moves |

## Verification

1. Start guardian with --debug
2. Confirm patrol positions are logged on startup
3. Watch camera — should move to position, stop, hold steady for 8 seconds, move to next
4. Dashboard feed should be stable and in-focus during dwells
5. Verify dead zone positions are skipped
6. Full cycle should take ~2 minutes
