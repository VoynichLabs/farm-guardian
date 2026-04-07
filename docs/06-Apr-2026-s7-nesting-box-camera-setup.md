# Samsung Galaxy S7 — Nesting Box Camera Setup

## What This Is

Use the old Samsung Galaxy S7 phone as a fixed security camera inside the chicken nesting box. The phone connects to the same WiFi network as the Mac Mini and streams video via RTSP. Farm Guardian treats it like any other camera — captures frames, runs detection, logs events.

## What You Need

- Samsung Galaxy S7 (no wipe needed — just install one app)
- USB charger + cable (phone stays plugged in permanently — old battery won't last)
- WiFi access (same network as the Mac Mini and Reolink camera)
- A way to mount/prop the phone inside the nesting box

## Step 1: Install IP Webcam on the S7

1. On the S7, open the **Google Play Store**
2. Search for **"IP Webcam"** by Pavel Khlebovich
3. Install the **free version** (the Pro version exists but isn't needed)
4. Open the app

## Step 2: Configure IP Webcam

In the app settings (before starting the server):

- **Video preferences > Video resolution**: 1280x720 is fine for a nesting box (saves bandwidth vs 4K)
- **Video preferences > Quality**: 50-70% (good enough for detection, lower = less WiFi load)
- **Connection settings > Login/password**: Set a username and password (e.g., `admin` / your usual camera password). This secures the stream on your local network.
- **Power management > Prevent sleep**: Enable this so the phone doesn't go to sleep
- **Audio mode**: Can leave on or turn off — Farm Guardian doesn't use audio

Then tap **"Start server"** at the bottom of the app.

The app will show the phone's IP address and port, something like:

```
http://192.168.0.XX:8080
```

## Step 3: Find the RTSP Stream URL

IP Webcam provides several stream URLs. The one Farm Guardian uses:

```
rtsp://admin:YOUR_PASSWORD@192.168.0.XX:8080/h264_ulaw.sdp
```

Replace:
- `admin:YOUR_PASSWORD` with whatever login you set in Step 2
- `192.168.0.XX` with the IP address the app shows
- Port is usually `8080` (the app will tell you)

To test the stream works, you can open it in VLC on any computer:
1. Open VLC > Media > Open Network Stream
2. Paste the RTSP URL
3. You should see the phone's camera feed

## Step 4: Add to Farm Guardian Config

Edit `config.json` and add a second camera entry:

```json
{
  "cameras": [
    {
      "name": "house-yard",
      "ip": "192.168.0.88",
      "port": 80,
      "username": "admin",
      "password": "YOUR_CAMERA_PASSWORD",
      "onvif_port": 8000,
      "type": "ptz"
    },
    {
      "name": "nesting-box",
      "ip": "192.168.0.XX",
      "port": 8080,
      "username": "admin",
      "password": "YOUR_S7_PASSWORD",
      "type": "fixed",
      "rtsp_url_override": "rtsp://admin:YOUR_S7_PASSWORD@192.168.0.XX:8080/h264_ulaw.sdp"
    }
  ]
}
```

**Key differences from the Reolink:**
- `"type": "fixed"` — no PTZ controls, no patrol, no spotlight/siren
- `"rtsp_url_override"` — bypasses ONVIF discovery since the S7 doesn't support ONVIF. Farm Guardian will use this URL directly.

**Note:** The `rtsp_url_override` field doesn't exist in the codebase yet. It will need a small change to `discovery.py` to check for this field and use it instead of trying ONVIF discovery. That's a separate task.

## Step 5: Mount the Phone

- Place the S7 inside or at the entrance of the nesting box, camera lens facing the area you want to watch
- Keep it plugged into USB power at all times
- Angle it so the camera can see hens entering/exiting and anything that might bother them
- Keep the phone's screen facing away or dimmed (IP Webcam has a "dim screen" option) so it doesn't disturb the chickens

## Step 6: Set a Static IP (Recommended)

So the S7 always gets the same IP address on your WiFi:

1. On the S7: Settings > WiFi > long-press your network > Modify > Advanced
2. Change IP settings from DHCP to Static
3. Set the IP to something outside your router's DHCP range (e.g., `192.168.0.50`)
4. Gateway: your router IP (usually `192.168.0.1`)
5. DNS: `8.8.8.8`

Or do it on your router's DHCP reservation page if you prefer.

## What Farm Guardian Will Do With It

Once configured, the nesting box camera gets the same treatment as the Reolink:
- Frame capture at ~1 fps
- YOLO detection (will see chickens, and any predator that enters)
- Vision model refinement (chicken vs hawk, house cat vs bobcat)
- Alerts to Discord if a predator is detected inside the nesting box
- Event logging to the database

It will NOT get:
- PTZ patrol (it's a fixed camera)
- Spotlight/siren deterrents (the S7 has no hardware for that)
- The Kasa smart outlet could potentially fill that gap — plug a light or alarm near the nesting box and trigger it via Farm Guardian

## Code Support

The `rtsp_url_override` field is fully supported in `discovery.py`. When Farm Guardian sees this field on a camera config, it skips ONVIF discovery and uses the URL directly. No further code changes needed — just add the config entry and restart Guardian.
