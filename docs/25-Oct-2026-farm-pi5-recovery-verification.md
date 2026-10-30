# 25-Oct-2026 — farm-pi5 boot and Ethernet recovery verified

Author: GPT-6.1

## Outcome

The rebuilt Pi boots, accepts the Mini's existing SSH key, retains data across a controlled reboot, and starts both original camera services automatically. Both cameras are physically absent on the indoor bench, so their health endpoints correctly return 503 with device_present=false. Live frames remain unverified until the powered camera hub is reconnected.

The original failure was missing boot data: the card had no partitions and six raw samples across the device contained only erased 0xFF bytes. This matches the September incident pattern but does not establish why the data disappeared. Ethernet hardware was verified working at 1000baseT after the rebuild. The repeat pattern warrants replacement of the card if it is the same physical card.

## Persistent Pi Ethernet configuration

User explicitly asked to pin the Ethernet address. The existing Wired connection 1 profile now keeps normal DHCP and adds a fixed direct-cable address:

- Pi eth0: 10.77.5.2/24, ipv4.method=auto, ipv4.may-fail=yes.
- IPv6 method: link-local; connection.autoconnect-retries=0.
- Mini en0: runtime alias 10.77.5.1/24; its existing DHCP service was preserved.

The initial direct connection had no DHCP server. Automatic configuration could fail and remove its temporary IPv6 reachability. Explicit IPv6 link-local plus the additional static IPv4 address stabilized recovery. The Pi address persisted across reboot. DHCP remains available when the cable is returned to the farm LAN; this work did not create a new router reservation. The Mini's runtime alias is not a persistent change to its network-service configuration and would need to be restored after a Mini reboot if using static IPv4 over this direct cable.

Working SSH during recovery: ssh -4 -B en0 -i /Users/macmini/.ssh/id_ed25519 -o UserKnownHostsFile=/private/tmp/farm-pi5-rebuild-24-Oct-2026/ssh_known_hosts markb@10.77.5.2. IPv6 also worked when explicitly bound with -B en0 at fe80::317:6bd9:cc16:b497%en0.

## Actual verification

- cloud-init completed with no fatal errors; image reports a recoverable missing cc_netplan_nm_patch module warning.
- Root partition expanded to 57.8 GiB and mounts read/write as ext4.
- vcgencmd get_throttled reported 0x0 before and after reboot.
- A 4 MiB random diagnostic file was fsynced, then verified after a changed kernel boot ID. SHA256: ab0f40e6fed85a31e9eb8f41b86e2e95c0daf1ec182f2d3dafefde702c965b7f.
- Both farmcam services are enabled and active after reboot; the installed unchanged production source and systemd template are the repository versions.
- Both real /health endpoints respond with the documented serial-derived paths and report the missing physical cameras. Do not substitute video indices or claim live camera recovery from service status.

## Restoration details

Temporary private-network Wi-Fi supplied internet for the existing staged installer. The Pi clock was corrected, country set to US, and all four documented Python dependencies plus required system libraries installed. The temporary farm-recovery Wi-Fi profile was then deleted and the Wi-Fi radio disabled before reboot, returning to the wired operating model. Full install output and pre/post reboot evidence remain under /private/tmp/farm-pi5-rebuild-24-Oct-2026/.

USB gadget configuration was successfully applied by firstboot: usb0 exists at 10.12.194.1/28, but the UDC reported not attached and the Mac did not enumerate the gadget. The Pi's USB host bus instead enumerated an Apple Mac device. Actual control and verification used Ethernet; no claim of working USB data access was made.

## Samsung SSD notification

The user reported an ejection notification. Read-only checks showed the production Samsung 9100 SSD still mounted read/write at /Volumes/Samsung 9100 SSD, physical disk6, APFS disk7s1, UUID 4D95C3D6-B330-40F8-B9DA-D7FF1ECCA682, with approximately 1.6 TB used. All image/unmount/eject commands during recovery targeted the guarded 62.6 GB SD card disk16; no command targeted disk6 for unmount/eject/write.

A two-hour unified-log audit found Finder successfully querying the Samsung volume around 13:35–13:36 EDT and StorageKit explicitly reporting it mounted at 13:39. No identifiable disk6/disk7 ejection, unmount, disappearance, or APFS fault was found. A 13:32 USB-C port-3 connection cycle mapped to a different controller than the SSD and cannot be attributed to it. The notification's cause/time remain unconfirmed; do not call it harmless or invent a cause.

## Remaining physical work

Return the Pi to its intended supply and Ethernet, reconnect both cameras through the externally powered hub, and verify real advancing frames and actual V4L2 gain=32/auto_exposure=3 on the webcam. Avoid forcing the Pi USB current limit while supplied from a Mini USB port. No application code, router settings, publishing behavior, or unrelated local changes were modified.

## Field verification — 25-Oct-2026, approximately 13:59 EDT

User returned the Pi and connected its cameras. It took its normal farm Ethernet DHCP address 192.168.0.17 and retained the additional recovery address 10.77.5.2. SSH works at the farm address. Both real camera endpoints report device_present=true, camera_open=true, ok=true, zero capture failures, and advancing sequences (8090:594→600; 8091:649→655 over three seconds), with frame ages under 400 ms. The webcam serves 1920×1080, the dashcam1280×720. Actual V4L2 controls were read back: gain32, auto_exposure3. Both services report zero restarts, and power reports throttled=0x0.

Snapshots were inspected visually: the webcam shows the lawn, netting and white truck with a noticeably hazy/soft image; the dashcam shows the enclosure and birds with a clear image. This is a visual limitation of the webcam view, not a failure to stream. No image-processing changes were made.

Guardian archive timestamps at verification:
- usb-webcam-1080p: 2026-10-25T17:57:32+00:00
- jieli-dashcam: 2026-10-25T17:59:11+00:00

Pi recovery and physical camera reconnection are verified complete.
