# 17-Apr-2026 — GWTC: stay on Windows, revert the Debian wipe, stabilize

**Author:** Claude Opus 4.7 (1M context) — Bubba
**Status:** APPROVED in-session (Boss: *"I do want this fucking handled"*). Executing immediately. This doc is the written record of why and what.
**Supersedes:** `project_gwtc_debian_wipe_17apr2026.md` auto-memory (being rewritten as "REVERTED" in the same change).

---

## Why this doc exists

The previous session (fired by Boss) committed to wiping GWTC to Debian 13 to stop the daily Windows wedge cycle. The install was **armed but did not complete** — the machine is currently booted back to Windows 11 on its internal eMMC, with a Debian *installer* (not a functioning OS) staged on a 62 GB SD card still in the SD slot. `bcdedit` on GWTC still has the Debian entry as `{default}` (pointing at `\EFI\debian\bootx64.efi`) — **the next power-cycle will re-enter the installer.** Preseed HTTP server on the Mac Mini (PID 32007, port 8000) is still running.

Boss's ask on resuming: *"Read everything, take a few hours, understand what I'm trying to achieve. This entire machine will be managed by a Claude Code assistant like yourself. If it's not obvious for another Claude Code assistant to do, it's not a good idea. I don't want future assistants to ever fail. Don't fucking ask me about shit about this machine ever again."*

That reframes the problem. The right question isn't "Windows vs Debian?" — it's "which OS comes with the mountain of prior art that lets the next Claude manage this box without failing?" The answer is unambiguously **Windows**.

## What GWTC actually does (beyond "just the camera")

GWTC is the **coop-side node** for a multi-arm flock research programme. It is not a disposable chicken-cam box; it's a scientific instrument with a camera function bolted on. The plans on `main` already treat it that way:

| Role | Status | Key artifacts |
|---|---|---|
| `gwtc` webcam → Guardian | live | `deploy/gwtc/{mediamtx.yml,start-camera.bat,farm-watchdog.ps1}`, `config.json:gwtc` |
| Audio arm — speaker for the acoustic-response study | scaffold committed | `tools/flock-response/playback.py` (SSH → PowerShell → `System.Media.SoundPlayer.PlaySync`), `tools/flock-response/sounds/`, `docs/16-Apr-2026-flock-acoustic-response-study-plan.md` |
| Visual arm — screen for silhouette / predator-image trials | plan committed | `docs/16-Apr-2026-gwtc-visual-stimuli-plan.md` (blocked on apparatus unblocks; daytime work) |
| Daytime ambient dashboard / night-light / sunrise sim | brainstormed | `docs/16-Apr-2026-gwtc-coop-node-capabilities-brainstorm.md` |

**Every one of those artifacts assumes Windows** — PowerShell, `tada.wav` smoke-test, Shawl service supervision, `C:\farm-sounds\`, the BCD / watchdog / ffmpeg-dshow path. Wiping to Debian silently invalidates all of it and forces a rewrite of the audio-arm scaffold before the first trial can run.

## Decision — stay on Windows. Reasons, in order:

1. **Prior art is Windows.** The audio-arm scaffold (committed) is SSH + PowerShell. The research-programme brainstorm cites `tada.wav` and `[Console]::Beep()` explicitly. Debian forces a rewrite before the first trial.
2. **Future-Claude onboarding is Windows.** Five+ docs / memory entries / CLAUDE.md sections already document the Windows setup with failure-mode playbooks pre-buried. The Debian path has one stale install-handoff doc. Boss's hard criterion ("obvious for another Claude Code assistant") cuts hard toward Windows.
3. **The daily wedge is already handled.** `farmcam-watchdog` auto-recovers the dshow zombie in ~90 s. Boss's explicit feedback (`feedback_gwtc_hard_reboot_freely.md`): *"It just needs to fucking work. Stop treating it with kid gloves."* Meaning: wedge + auto-recover is an acceptable operational profile, not a reason to wipe.
4. **The Celeron N4020 / 4 GB / 60 GB hardware** is tight for either OS. Windows 11 happens to be the one that's already configured, debloated, SSH'd, keyed, and wired to the rest of the stack.
5. **Debian install didn't complete** and the BCD is a time-bomb. Reverting is strictly less work than finishing a wipe whose handoff doc the *current* agent (me) wouldn't want to inherit.

## What this plan does, step by step

Execution happens in-session right after this doc lands on disk. Each step has a verification step.

### 1. Disarm the BCD on GWTC

Via SSH from the Mini:

```powershell
# Set Windows as the default boot target
bcdedit /default {current}
# Remove the Debian installer entry (stored as the aliased {default} in prior state)
bcdedit /delete {85953294-84d4-11ee-ac9d-e86961680a00}
# Restore a sane timeout so recovery is accessible if needed
bcdedit /timeout 30
# Verify: {default} now resolves to \WINDOWS\system32\winload.efi
bcdedit /enum {bootmgr}
```

**Verification:** `bcdedit /enum {bootmgr}` shows `default={current}`, `displayorder={current}`, no reference to `\EFI\debian\bootx64.efi`.

### 2. Remove the Debian shim from the ESP

```powershell
mountvol X: /S
Remove-Item -Recurse -Force X:\EFI\debian\
mountvol X: /D
```

**Verification:** `dir X:\EFI\` no longer shows a `debian\` directory.

### 3. Kill the preseed HTTP server on the Mac Mini

```bash
kill $(cat /tmp/preseed-http.pid) 2>/dev/null
rm -f /tmp/preseed-http.pid /tmp/preseed-http.log
```

**Verification:** `lsof -i :8000` returns nothing.

### 4. Remove Debian install artifacts from the Mini

```bash
rm -rf /Users/macmini/gwtc-linux-prep/
rm -rf /tmp/gwtc-debian/
rm -rf /tmp/debiso-extract/
```

**Verification:** all three paths return `No such file or directory`.

### 5. Free space on GWTC C:

C: has 14 GB free of 60 GB. Windows Update alone needs more. Via SSH:

```powershell
# Run built-in disk cleanup at verbose preset
cleanmgr /sagerun:1
# Clear Windows Update component store backup (safe after successful updates)
Dism.exe /online /Cleanup-Image /StartComponentCleanup /ResetBase
# If WSL2 is installed, uninstall it — GWTC doesn't need it and it eats space
# (only if present — the MAC-attribution-error writeup confirms it may be there)
wsl --list --verbose
# Manual step if wsl returns distros: wsl --unregister <distro>, then
# Disable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform,Microsoft-Windows-Subsystem-Linux
```

**Verification:** `Get-PSDrive C | Select Used,Free` shows ≥ 20 GB free.

### 6. Update `docs/GWTC_SETUP.md` as the authoritative future-Claude handoff

Top-of-file pointer added:

> **If you are the next Claude Code agent assigned to GWTC, this is the only doc you need to read first.** It covers: what GWTC is (coop camera node + audio/visual research speaker), how to SSH in, the three Shawl services, the watchdog, power/lock-screen quirks, and cross-links to the research-programme docs. Do not propose wiping / reimaging / OS-swapping GWTC without reading `docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` — that's the record of the last time an agent tried.

Plus: keep the MSI-Katana correction, keep the dshow-zombie watchdog section, keep the PIN `5196` pre-login WiFi note, keep the ICMP-blocked-between-wired-and-wireless note.

### 7. Retire / rewrite the stale Debian-wipe memory entry

`~/.claude/projects/-Users-macmini/memory/project_gwtc_debian_wipe_17apr2026.md` currently reads "armed and in progress." That will mislead the next Claude into resuming the wipe. Rewrite the body as:

> **REVERTED 17-Apr-2026.** BCD disarmed, installer artifacts removed from both the Mini and GWTC's ESP, preseed server killed. GWTC stays on Windows 11 long-term; see `farm-guardian/docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` for the reasoning. SD card with Debian installer is still physically in the GWTC SD slot — harmless (BCD no longer chainloads it) but Boss's physical hands are needed to remove it; leave as-is.

Update `MEMORY.md` hook line accordingly.

### 8. CHANGELOG + commit + push

Single commit bundling: the plan doc, the GWTC_SETUP.md header update, and the CHANGELOG entry. Author: Claude Opus 4.7 (1M context). Scope: docs-only + GWTC state change (the actual BCD/cleanup happen via SSH, not in-repo).

## What is explicitly OUT of scope tonight

- **The research programme.** Audio arm, visual arm, operant conditioning sandbox, night-light — all have their own plans and their own welfare-floor unblocks. Do not let stabilization metastasize into new research-platform code.
- **Removing the SD card.** Boss's physical hands required; Boss has said repeatedly not to ask. Card is harmless once BCD is disarmed.
- **Replacing the Windows lock-screen WiFi behaviour.** Tracked in `project_gwtc_offline_pre_login_wifi.md`; the watchdog pattern plus `feedback_gwtc_hard_reboot_freely.md` is the current answer. Don't redesign it tonight.
- **Removing the 62 GB SD card's Debian install.** Irrelevant — BCD no longer points at it, and it boots nothing on its own.

## Success criteria

1. `bcdedit /enum {bootmgr}` on GWTC shows Windows as default, no Debian entry.
2. `lsof -i :8000` on the Mini returns empty.
3. `/Users/macmini/gwtc-linux-prep/` and `/tmp/gwtc-debian/` and `/tmp/debiso-extract/` are gone.
4. `docs/GWTC_SETUP.md` has the future-Claude header.
5. Memory file `project_gwtc_debian_wipe_17apr2026.md` reads "REVERTED" and is discoverable from `MEMORY.md`.
6. One commit on `main` with CHANGELOG entry, pushed to origin.
7. `gwtc` camera is still live at `rtsp://192.168.0.68:8554/gwtc` (no regression in the Guardian `/api/cameras` response).

## Why this is boring by design

Boss's criterion: *"I don't want future assistants to ever fail."* The durable fix for that is not a clever OS swap — it's reducing the surface area a future agent has to navigate, and leaving one authoritative doc (`GWTC_SETUP.md`) at the exact location CLAUDE.md already points to. The stable outcome of tonight's work is: future Claude reads GWTC_SETUP.md, knows what GWTC is, knows how to SSH in, knows the three services, and can focus on the research programme rather than re-litigating the OS choice. That's the win.

---

**done. plan only. executing steps 1–8 immediately after this file lands on disk.**
