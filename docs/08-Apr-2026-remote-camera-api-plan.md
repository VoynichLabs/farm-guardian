# Remote Camera Control API Endpoints — 08-Apr-2026

## Goal

Add the missing API endpoints that allow a remote Claude session (or any HTTP client) to fully control the Reolink camera over the internet. Currently the API has PTZ move/stop and spotlight/siren, but is missing snapshot, autofocus, zoom, position readback, and guard control — the exact operations needed for remote camera setup and monitoring.

## Scope

**In scope:**
- `GET /api/v1/cameras/{camera_id}/snapshot` — take a JPEG snapshot and return it
- `GET /api/v1/cameras/{camera_id}/position` — read current pan/tilt/zoom
- `POST /api/v1/cameras/{camera_id}/zoom` — set zoom level
- `POST /api/v1/cameras/{camera_id}/autofocus` — trigger autofocus
- `POST /api/v1/cameras/{camera_id}/guard` — enable/disable PTZ guard
- Fix dead `ptz_save_preset` reference in existing PTZ endpoint

**Out of scope:**
- Authentication/API keys (future — hosted mode)
- Rate limiting
- Streaming video endpoint

## Why

Mark wants to control the camera from his phone via the Claude app. If the dashboard API is exposed through Railway/Cloudflare, a remote Claude session can hit these endpoints to move the camera, take snapshots, and adjust settings — without needing to be on the local network running Python scripts.

## Verification

1. Start guardian
2. `curl http://localhost:6530/api/v1/cameras/house-yard/position` → returns pan/tilt/zoom JSON
3. `curl http://localhost:6530/api/v1/cameras/house-yard/snapshot --output test.jpg` → returns JPEG
4. `curl -X POST http://localhost:6530/api/v1/cameras/house-yard/autofocus` → triggers autofocus
5. `curl -X POST http://localhost:6530/api/v1/cameras/house-yard/zoom -d '{"level": 0}'` → sets zoom
