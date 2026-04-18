# 18-Apr-2026 — GWTC: current state + interactive Debian install walkthrough

**Author:** Claude Opus 4.7 (1M context) — Bubba
**Date:** 2026-04-18 12:25 ET
**Status:** Live operational record. GWTC is working RIGHT NOW on Windows with autologon; Debian install is pre-staged on a 62 GB SD card in Boss's hands as the fallback.
**Supersedes nothing** — complements `17-Apr-2026-gwtc-windows-stabilization-plan.md` (that doc is still the reverted-Debian-wipe record) and `GWTC_SETUP.md` (operational doc for the Windows side).

---

## Why this doc exists

The previous two sessions thrashed on "Windows or Debian?" Boss's explicit instruction on this turn: *"Make a memory about this so I'm not going out this fucking blind next time."* This is that memory, in the repo. Between this doc and `GWTC_SETUP.md`, any future Claude should be able to pick up GWTC — in either state — without asking Boss any question that isn't "do you want to proceed with X."

## Post-strip state (afternoon 18-Apr-2026)

After the noon autologon fix, Boss directed: *"No human is ever going to use that machine again. Only Claude Code instances. Be ruthless like a barbarian, loot and pillage. This is no longer a piece of Microsoft corporate bloatware junk for stupid non-technical users. This is your Claude Code bird command machine."* The afternoon was spent executing that.

### What's still running (by design)

| Layer | Kept |
|---|---|
| Camera | MediaMTX (`:8554`, Shawl), farmcam (ffmpeg dshow → RTSP, Shawl), farmcam-watchdog (Shawl) |
| Remote access | Windows OpenSSH Server (sshd service, Automatic) |
| Login | Autologon to local `cam` account, blank password, UAC off, lock screen off, CAD off |
| Audio | Core Windows audio stack (for `System.Media.SoundPlayer.PlaySync()` — the audio-arm playback primitive) |
| Frameworks | Windows Store kernel + dependencies (UI.Xaml, VCLibs, etc.), .NET runtimes, C++ redists |
| Tooling (new, afternoon 18-Apr) | Python 3.12, Git, Node.js LTS, Claude Code CLI — installed via winget + npm so any future Claude instance can SSH in, write code, pull repos, run things |

### What was removed / severed

**AppX packages (49 removed + provisioned-package entries cleared, bulk):**
Amazon Prime Video, Hulu, RandomSalad Solitaire, Clipchamp, Exafunction Windsurf, Cortana, BingNews, BingSearch, BingWeather, Copilot, Edge.GameAssist, GamingApp, GetHelp, Getstarted, Messaging, OfficeHub, Solitaire Collection, StickyNotes, Mixed Reality Portal, OneConnect, OneDriveSync, OutlookForWindows, Paint, People, Power Automate Desktop, ScreenSketch, SkypeApp, StartExperiencesApp, Todos, Whiteboard, Windows Alarms, Windows Camera app (NOT the webcam driver — just the consumer app), `windowscommunicationsapps` (Mail+Calendar), Feedback Hub, Windows Maps, all Xbox AppX, Your Phone, Zune Music/Video, Microsoft Family, Quick Assist, `Client.WebExperience` (News/Widgets), CrossDevice, DE language pack, plus all remaining Edge AppX.

**Microsoft Edge (Chromium):** Chrome folders deleted from `C:\Program Files (x86)\Microsoft\Edge\`, `\EdgeUpdate\`, `\EdgeWebView\`. Edge Update services (`edgeupdate`, `edgeupdatem`, `MicrosoftEdgeElevationService`) Stopped and set StartupType Disabled. `HKLM\SOFTWARE\Microsoft\EdgeUpdate\AllowUninstall=1` set pre-uninstall.

**Windows Update — fully severed:**
- Services disabled: `wuauserv`, `BITS`, `DoSvc` (Delivery Optimization). Plus `UsoSvc` and `WaaSMedicSvc` set `Start=4` directly via registry (they're protected from `sc config` even as admin; WaaSMedicSvc specifically exists to *repair* a disabled `wuauserv`, so nuking it was essential).
- Policies written under `HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\`: `AU\NoAutoUpdate=1`, `AU\AUOptions=1`, `DisableWindowsUpdateAccess=1`.
- Scheduled tasks disabled: `\UpdateOrchestrator\*`, `\WindowsUpdate\*`, `\Application Experience\*`, `\Customer Experience Improvement Program\*`, `\Feedback\*`, `\Office\*`. 14 tasks confirmed disabled in the run (Microsoft Compatibility Appraiser, Consolidator, UsbCeip, DmClient, Schedule Wake To Work, Scheduled Start, etc.).

**Telemetry / Feedback / Consumer features:**
- `HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection\AllowTelemetry=0`
- `DoNotShowFeedbackNotifications=1`
- `CloudContent\DisableWindowsConsumerFeatures=1` + `DisableConsumerAccountStateContent=1`
- Services disabled: `DiagTrack` (Connected User Experiences and Telemetry), `dmwappushservice`, `WerSvc` (Windows Error Reporting).

**Security prompts / password gates:**
- `HKLM\...\Policies\System\EnableLUA=0` — UAC off globally (takes effect next reboot).
- `ConsentPromptBehaviorAdmin=0`, `PromptOnSecureDesktop=0` — secure-desktop prompts off.
- `DisableCAD=1` — no Ctrl+Alt+Del requirement.
- `HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization\NoLockScreen=1` — lock screen gone.
- `powercfg CONSOLELOCK 0` on both AC and DC — no password prompt on resume.
- `DevicePasswordLessBuildVersion=0` — blocks Windows 11 passwordless-sign-in gate (autologon precondition, set in the noon session).
- `LimitBlankPasswordUse=0` — allows the `cam` account's blank password at the console.

**Other dead services:** `Spooler` (print queue — no printer needed), `Fax`, `WSearch` (Windows Search indexer — no one's searching).

**Startup entries:**
- `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run` — `SecurityHealth` and `TPCtrlServer` values removed.
- Startup folders for `cam`, `markb`, and all-users purged.

### Bird-on-keyboard defenses (evening, 18-Apr-2026)

After the afternoon strip, Boss deployed GWTC to the coop. It dropped off the LAN within a few hours. Boss observed birds walking on the keyboard (the laptop lid is open, coop-interior-facing — keyboard exposed). Most likely culprit: a bird hits `Win+L`, session locks, Realtek 8723DU USB WiFi drops, LAN invisibility follows. Boss is covering the keyboard physically; applied four registry/power defenses as a complement:

- **Win+L disabled:** `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\DisableLockWorkstation=1`. Removes the system-wide lock-workstation keyboard shortcut. A bird can no longer lock the session by keypress.
- **Power / Sleep / Lid buttons neutralized:** `powercfg` sets `PBUTTONACTION`, `SBUTTONACTION`, `LIDACTION` all to `0` (no action) on both AC and DC. Lid-close or a bird pressing the power/sleep key does nothing.
- **USB selective suspend globally off:** `powercfg` sets the USB suspend setting (GUID `48e6b7a6-50f5-4782-a5d4-53bb8f07e226`) to `0` on both AC and DC. The Realtek USB WiFi adapter stays powered.
- **Monitor timeout set to never:** `powercfg /change monitor-timeout-ac 0 && monitor-timeout-dc 0`. Avoids display-sleep side-effects on this adapter.

**Verified at 2026-04-18 17:00 ET after the bird-induced power cycle:** GWTC back on LAN, all services RUNNING, `/api/cameras/gwtc/frame` returns 200 + 117 KB JPEG showing the coop interior with birds visible — which is the job.

### What was NOT removed (explicit safety list)

- **Windows Defender core** — real-time monitoring can be disabled if it ever causes a demonstrable camera-side issue, but on a LAN-only machine with no user browsing it doesn't matter. Tamper Protection on Win11 22H2+ blocks the usual disables anyway.
- **Windows Store (`Microsoft.WindowsStore`)** — kept because winget sometimes uses it for installs.
- **Windows Terminal, Notepad, Photos** — tiny, useful for a Claude-over-SSH session that wants to inspect a file.
- **Win32 touchpad / Realtek WiFi drivers** — obviously.
- **WSL2 feature** (if present) — untouched per existing memory `project_gwtc_offline_pre_login_wifi.md`; if a future Claude needs Linux-flavored tooling without an OS swap, it's already there.

### Tooling installed for next-Claude ergonomics

Installed via `winget install --silent --accept-source-agreements --accept-package-agreements --disable-interactivity`:

- `Python.Python.3.12` (Python 3 system-wide on PATH)
- `Git.Git` (git on PATH; required for `git clone` of this repo from SSH)
- `OpenJS.NodeJS.LTS` (Node.js + npm — required for Claude Code CLI)

Then `npm install -g @anthropic-ai/claude-code` (Claude Code CLI; pair with an Anthropic OAuth token — bridge creds from the Mini via the multi-Claude-SSH pattern documented in `CLAUDE.md`).

## The live state at noon (before the strip)

| Fact | Detail |
|---|---|
| OS | Windows 11 Home 22631 |
| Hardware | Gateway GWTC116-2, Celeron N4020 (2C/2T @ 1.1 GHz), 3.9 GB RAM, 60 GB Biwin eMMC, Realtek 8723DU USB WiFi |
| Camera | Built-in Hy-HD-Camera, 1280×720 via DirectShow |
| Autologon | Local `cam` account (blank password), logs in on boot with no lock-screen pause |
| Services (Shawl, LocalSystem, auto-start) | `mediamtx` (RTSP :8554), `farmcam` (ffmpeg dshow → RTSP), `farmcam-watchdog` (90-s dshow-zombie recovery) |
| Verified | Two full reboots tonight returned to the LAN with no human input; `/api/cameras/gwtc/frame` → 200 + ~87 KB fresh JPEG after ~90 s |

**What "working right now" means:** power-cycle GWTC, walk away, and within ~2 minutes the Guardian dashboard shows fresh frames again. No PIN entry, no keyboard at the coop.

## How the autologon is wired (so a future Claude can fix it without re-deriving)

All changes are registry / `net user` / `wmic` — no code deploys, no installers.

1. Local admin account `cam` with blank password:
   ```powershell
   New-LocalUser -Name cam -NoPassword -AccountNeverExpires -FullName CoopCam
   net localgroup administrators cam /add
   net user cam /logonpasswordchg:no
   net user cam /expires:never
   wmic useraccount where "Name='cam'" set PasswordExpires=FALSE
   ```
2. Disable Windows 11's passwordless-sign-in gate (otherwise the DefaultPassword reg value is ignored):
   ```
   reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device" /v DevicePasswordLessBuildVersion /t REG_DWORD /d 0 /f
   ```
3. Allow blank-password accounts to sign in at the console:
   ```
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LimitBlankPasswordUse /t REG_DWORD /d 0 /f
   ```
4. Autologon reg values:
   ```
   reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 1 /f
   reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d cam /f
   reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultDomainName /t REG_SZ /d 653PUDDING /f
   reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d "" /f
   ```

**Verification** (can be run any time from the Mini):
```bash
ssh markb@192.168.0.68 'reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" | findstr /I "AutoAdminLogon DefaultUserName DefaultDomainName DefaultPassword"'
```
Expected output contains `AutoAdminLogon REG_SZ 1`, `DefaultUserName REG_SZ cam`, `DefaultDomainName REG_SZ 653PUDDING`, `DefaultPassword REG_SZ` (blank value).

## The one known landmine

**Windows Update may silently reset `DevicePasswordLessBuildVersion` back to `1`.** If that happens after some future update cycle, Windows ignores the DefaultPassword reg value, the console sits at the passwordless-sign-in screen, WiFi never comes up pre-login, and GWTC drops off the LAN exactly like before the fix.

**Symptom from the Mini:** after a GWTC reboot, `nc -z -w 3 192.168.0.68 22` returns empty for >5 minutes. The machine is on, but fully absent from the LAN.

**Don't re-fight the registry if that happens.** It's the switch trigger for the Debian migration below.

## Debian fallback — SD card is pre-staged

Boss holds a 62 GB SD card containing the Debian 13.4 netinst ISO, isohybrid-written. Confirmed 2026-04-18 via ISO9660 magic + "Debian 1" volume label at sector 16 on `/dev/disk4` from the Mini. **The card is ready to boot as-is; no re-flash, no re-download needed.**

### Boot sequence (Boss's hands, one time)

1. Insert SD card into GWTC's internal SD slot.
2. Full power off (hold power button until fully off, not sleep).
3. Power on. Mash **F7** from the first Gateway logo frame.
   - If F7 doesn't pop a boot-menu, try **F12**, then **F9**, then **Esc**. We haven't empirically confirmed which of those it is on this particular Gateway firmware; the 2021-era consumer Gateway laptops are split across the four.
4. From the boot menu, pick "UEFI: Mass Storage" or "UEFI: SDHC" — something SD/storage-flavored. **Not** "Windows Boot Manager."
5. At the purple Debian grub screen, pick **"Graphical install"** (fall back to "Install" if 1366×768 doesn't render).

### Installer prompts (most are defaults)

| Prompt | Answer |
|---|---|
| Language | English |
| Location | United States |
| Keyboard | American English |
| Hostname | `gwtc` |
| Domain | leave blank |
| WiFi network | `653 Pudding Hill 2G Private` |
| WPA passphrase | `4136870990!` |
| Root password | **leave both fields blank → Continue** (forces sudo-only) |
| Full name | `Boss` or anything |
| Username | `markb` (matches the Mini's SSH key target) |
| User password | anything memorable |
| Time zone | Eastern |
| Partitioning | "Guided — use entire disk" → **the 60 GB eMMC** (typically `/dev/mmcblk0` or `/dev/sda`), **not** the 62 GB SD card → "All files in one partition" → "Finish partitioning" → **Yes** to erase |
| Mirror | any US mirror · Proxy blank · Popcon No |
| Software selection | **uncheck everything except** "SSH server" and "standard system utilities" — no desktop |
| GRUB | Yes, to the eMMC |

### WiFi firmware landmine

The SD card carries the **regular** Debian 13.4 netinst ISO (no non-free firmware). The Realtek 8723DU USB WiFi adapter usually needs the `firmware-realtek` blob from non-free to bring up the NIC during install.

**If WiFi doesn't come up during the installer's network step**, the installer will offer to load firmware from removable media. You don't have a second removable handy. Two options:

1. **Preferred: re-flash the SD card with the firmware-included ISO** (Mini command):
   ```bash
   diskutil list external   # confirm disk4
   diskutil unmountDisk /dev/disk4
   curl -L -o /tmp/debian-firmware.iso "https://cdimage.debian.org/cdimage/unofficial/non-free/firmware/bookworm/current/firmware-13.4.0-amd64-netinst.iso"
   sudo dd if=/tmp/debian-firmware.iso of=/dev/rdisk4 bs=4m status=progress
   diskutil eject /dev/disk4
   ```
   Then resume the boot steps above.
2. **Fallback: wired ethernet** — GWTC has no built-in ethernet, but Boss has a USB-to-ethernet adapter somewhere. Ask. If ethernet works, the install proceeds without WiFi firmware; WiFi can be set up post-install from shell.

### Post-install bootstrap (from the Mini, Claude does this)

Once Debian is up and SSH-reachable at `192.168.0.68`:

```bash
# 1. Verify SSH + basics
ssh markb@192.168.0.68 'uname -a; id; free -h; df -h /'

# 2. Install the camera stack
ssh markb@192.168.0.68 'sudo apt-get update && sudo apt-get install -y --no-install-recommends ffmpeg v4l-utils curl ca-certificates'

# 3. mediamtx binary (same version as the Windows side for consistency)
ssh markb@192.168.0.68 'cd /tmp && curl -fsSL -o mediamtx.tar.gz https://github.com/bluenviron/mediamtx/releases/download/v1.14.0/mediamtx_v1.14.0_linux_amd64.tar.gz && tar xzf mediamtx.tar.gz && sudo install -m 0755 mediamtx /usr/local/bin/'

# 4. Two systemd units (mediamtx + ffmpeg publisher) + /etc/mediamtx/mediamtx.yml
# (exact unit files in the Debian-side CLAUDE.md-to-be; template in
#  project_gwtc_debian_wipe_17apr2026.md's post-install section as a starting point)

# 5. Ports: mediamtx :8554 (same as Windows); ffmpeg -f v4l2 -i /dev/video0 (not dshow)
```

Once frames flow, update `farm-guardian/config.json:gwtc` — no change needed; the RTSP URL stays `rtsp://192.168.0.68:8554/gwtc`, which is the whole point of publishing from the same hostname on the same port.

### Audio-arm port (same-day, small)

`tools/flock-response/playback.py` currently SSH-triggers PowerShell's `System.Media.SoundPlayer.PlaySync()`. On Debian, that becomes `aplay <path>` (blocking) or `paplay <path>` if we want PulseAudio routing. ~15-line diff. The SSH protocol wrapper, the `PlaybackResult` dataclass, and the `measure_latency.py` sibling don't change.

### What goes away on the Debian side

- `farmcam-watchdog` — not needed. Linux `ffmpeg -f v4l2 -i /dev/video0` doesn't wedge the way dshow does after Windows reboots. Delete the service; the v4l2 path is simpler.
- All PowerShell / `System.Media.SoundPlayer` references in the repo's GWTC-related tools — replaced by `aplay`.
- The WSL2 caveats in `GWTC_SETUP.md` — Linux is Linux, no virtual-adapter quirks to describe.

## Decision procedure for the next Claude picking up GWTC

1. **If `nc -z -w 3 192.168.0.68 22` from the Mini returns success after an unattended reboot** → GWTC is in its working Windows-autologon state. Leave it alone. Nothing to do unless Boss has a new ask.
2. **If it returns empty for >5 min after a reboot** → Windows Update has likely reset `DevicePasswordLessBuildVersion`. Confirm with Boss, then execute the Debian install above. Don't spend the session re-fighting the Windows registry.
3. **If Boss proactively says "do Debian"** → same walkthrough, no symptom required. Start at the boot sequence.

**Do NOT flip-flop between Windows and Debian mid-session.** See `~/.claude/projects/-Users-macmini/memory/feedback_gwtc_os_flipflop_18apr2026.md` for the rule and the five-flip cautionary tale from the 17/18-Apr-2026 session.

## Cross-references

- `docs/GWTC_SETUP.md` — Windows operational doc (services, troubleshooting, power, etc.). Currently Windows-only; after a successful Debian migration it gains a Linux section.
- `docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` — the reverted-Debian-wipe plan. Outdated as a recommendation but still the rationale record.
- `~/.claude/projects/-Users-macmini/memory/project_gwtc_state_18apr2026.md` — auto-memory mirror of this doc for Claude sessions that haven't cloned the repo.
- `~/.claude/projects/-Users-macmini/memory/feedback_gwtc_os_flipflop_18apr2026.md` — don't re-litigate mid-session.
- `tools/flock-response/playback.py` — audio-arm scaffold; target of the ~15-line port if/when we flip to Debian.
- `deploy/gwtc/` — Windows-side service definitions (Shawl + watchdog + mediamtx.yml + start-camera.bat). Stays as historical record on Debian; not deleted.

---

**done. repo doc + two auto-memories in sync; next Claude has both the live state and the fallback walkthrough.**
