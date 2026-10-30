# 24-Oct-2026 — farm-pi5 SD recovery plan

Author: GPT-6.1

## Scope and authorization

Restore the unbootable Birdcatraz Pi after the user explicitly authorized repairing or rebuilding its SD card. Reuse the existing camera host and systemd unit. No Guardian application, camera identity, router, or publishing changes. Rebuild authorization was given in this chat; no further approval is needed for that recovery.

## Evidence

The Mini detected no Ethernet carrier or USB Pi device. Once the card was inserted into its USB reader, diskutil identified a 62,560,665,600-byte removable USB card at disk16 with no partition table. Six 4 MiB raw samples (start, 16 MiB, 256 MiB, 1 GiB, 8 GiB, end) contained only 0xFF, matching the September incident. This establishes missing boot data, not the cause of data loss or a dead Ethernet port. The first 4 MiB were preserved in /private/tmp/farm-pi5-recovery-24-Oct-2026/card-first-4MiB.bin. If this is the same physical card, replacement is recommended even if a new image verifies.

## Architecture and reuse

Use official Raspberry Pi OS Lite 64-bit (06-Oct-2026, Debian Trixie). Verify both published compressed and extracted SHA256 hashes. Seed the supported cloud-init configuration on bootfs with farm-pi5 hostname, markb account, the Mini SSH public key, and USB gadget networking so recovery does not require HDMI. Keep first boot independent of internet package downloads. Stage the unchanged tools/camera-host-linux/camera_host.py and deploy/farm-pi5/farmcam@.service with documented per-camera by-id environments, then install dependencies after SSH access returns.

## Ordered work and verification

1. Identify the card independently of the 2 TB production SSD and preserve diagnostic evidence.
2. Download, decompress and checksum the official image.
3. Before any device write require exact card size, whole physical device, USB transport, external and removable flags.
4. Write the OS image and verify its full written extent by reading back and comparing SHA256.
5. Configure and reread the boot partition; validate password hash and SSH key.
6. Safely eject; user reinserts card and powers the Pi.
7. Verify USB/Ethernet discovery, SSH, storage/power/kernel logs and a controlled reboot.
8. Restore existing camera services after connectivity is available and check real health endpoints.

## Documentation and changelog

Record image hashes, card verification, first-boot results and any remaining hardware concern in a dated recovery incident note. No application behavior or version change is planned; leave unrelated existing working-tree changes untouched.

## Recovery execution — card prepared; hardware boot pending

- Official image downloaded: `https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz`.
- Compressed SHA256: `483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843`; extracted SHA256: `b8a393dc1c3701450b5fffbfe74368a98fb6eab7415ce456cc0cead886ea8780`. Both verified before writing.
- Wrote 3,078,619,136 bytes to the guarded card. Initial whole-image readback differed because macOS automatically added `.fseventsd` and `.Spotlight-V100` files to FAT. A complete byte comparison found differences only in FAT metadata/free space below 75 MB; the complete Linux root partition matched. Independent FAT parsing verified all 434 original boot files by SHA256 against the image, zero mismatches. This is not a failed write and was not treated as proof of bad flash.
- Preserved stock `meta-data` including `dsmode: local` and stock `network-config`. Configured supported cloud-init: hostname farm-pi5, America/New_York timezone, markb account, existing Mini public key, verified SHA512 password hash for the existing console password, SSH, and USB gadget networking. No network-dependent first-boot installation. The gadget package is preinstalled in this actual image; hardware activation remains to be checked after boot.
- Staged unchanged production source/unit plus documented serial-derived camera environments and an installer under `/boot/firmware/farmcam-rebuild/`. The installer has NOT run; camera restoration is pending network access.
- Unmounted and remounted bootfs read-only; verified all 10 configured/staged file hashes and key provisioning settings. Safely ejected the card.
- Evidence and installers remain in `/private/tmp/farm-pi5-rebuild-24-Oct-2026/`; original erased sectors are in `/private/tmp/farm-pi5-recovery-24-Oct-2026/`.

Next: user returns card to powered-off Pi, reconnects direct USB-C to Mini and Ethernet, then powers on. Allow first-boot resize and an automatic gadget-mode reboot. Verify USB adapter/SSH, actual storage and power state, controlled reboot, and existing camera services. No Pi boot/reboot or working-camera claim has been made yet.
