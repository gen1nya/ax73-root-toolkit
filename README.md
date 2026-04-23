# ax73-root-toolkit

Persistent root on TP-Link Archer AX73 (v1.0, firmware 1.3.5) via UART → CFE → flash-level squashfs surgery.

End state: password-less root shell on serial console plus `telnet :23`. No software exploits, no secure-boot bypass required — just documented flash modification through the bootloader.

## TL;DR

1. Solder UART, baud 115200.
2. Interrupt CFE autoboot, send `ba r "… init=/bin/sh"` with `ubi.mtd=0` → working `/bin/sh` on main rootfs.
3. Dump `mtd0` (stock rootfs UBI container) to USB.
4. On a PC: `unsquashfs` it, edit `/etc/inittab`, `/etc/shadow`, `/lib/preinit/20_check_jffs2_ready`, drop a pubkey and dropbear host key, `mksquashfs` back with **xz, no BCJ, xattrs on**.
5. **Inject** the new squashfs into the **stock UBI container** byte-by-byte (keep every EC header, every sequence number, every internal volume) — a fresh `ubinize` image is rejected by the 4.1 kernel as "unsupported on-flash UBI format".
6. Reboot, catch CFE again, this time with `ubi.mtd=1` (backup bank) to free `mtd0` for writing.
7. `ubiformat /dev/mtd0 -f new.ubi -s 2048 -y`. Reboot.

You now have a router that boots normal stock userspace but with `/bin/ash --login` on UART and `telnetd :23` respawned by init.

See [`docs/`](docs/) for the complete reasoning and every gotcha we hit along the way.

---

## Why

The AX73 ships locked down:

- `/etc/inittab` runs `/bin/login` on the serial console; `/etc/shadow` has `root:x:…` (no valid hash) so UART login is blocked.
- `/etc/init.d/dropbear` generates its host key into `/tmp/…` and tries to `mv` it to `/etc/dropbear/` — which is read-only squashfs. SSH never comes up on stock.
- `/etc/config/dropbear` configures port 22, but the running daemon is started with `-p 20001` and gated by an iptables whitelist that's only active for the Singapore SKU (`knock_functions.sh`).
- `/lib/preinit/20_check_jffs2_ready` has the OpenWrt overlay-detection logic **commented out** — the script unconditionally drops to a RAM overlay and sets `pi_mount_skip_next=true`, which makes every subsequent `do_mount_ubifs`/`rootfs_pivot_ubifs` hook a no-op. The entire OpenWrt overlayfs chain is present but never triggers.

The net effect is that nothing survives a reboot unless it lives in the squashfs on `mtd0` or in an init hook that reads from a writable partition (none of the stock hooks do).

Therefore: modify the squashfs.

---

## Approach (and why the obvious approach fails)

Naïve plan: extract the squashfs, patch it, rebuild a fresh UBI image with `ubinize`, `ubiformat` it onto `mtd0`. We spent an afternoon failing at exactly this.

Two things broke it:

1. **`ubinize` ≥ 2.3 produces UBI images the kernel 4.1 UBI driver rejects** with `ubi_compare_lebs: unsupported on-flash UBI format`. We never fully rooted the cause (probably a subtle header-field change around sqnum handling), but no combination of `-x`, `-Q`, `-s`, `-O` flags made the resulting image attach.

2. **`mksquashfs -Xbcj arm` produces a squashfs whose files the BCM6750 kernel can read for metadata but not for data**, resulting in `Kernel panic – Requested init /etc/preinit failed (error -5)` after UBI attach succeeds. The stock squashfs uses xz without the BCJ filter; mimicking that is required.

The fix that works:

- Dump the stock UBI container verbatim with `dd if=/dev/mtdblock0`.
- Parse the dump in Python, locate the 259 PEBs belonging to the `rootfs_squashfs` static volume, and **overwrite only their data area + three VID-header fields** (`data_size`, `used_ebs`, `data_crc`) plus `hdr_crc`. Every EC header, every PEB-to-LEB mapping, the internal layout volume (`vol_id = 0x7FFFFE7F`), and the `ubi_rootfs_data` volume we created earlier all stay bit-for-bit identical to stock.
- Write the resulting container back to `mtd0` with `ubiformat -f`.

The kernel now attaches UBI cleanly, mounts squashfs, reads our patched `/etc/inittab`, runs `/bin/ash --login` on UART, starts `telnetd`.

Full walkthrough including the UBI CRC32 quirk (`init=0xFFFFFFFF` with **no** final XOR) is in [`docs/03-flash-surgery.md`](docs/03-flash-surgery.md).

---

## What you need

| | |
|---|---|
| Router | TP-Link Archer AX73 v1.0 (BCM6750, 512 MB RAM, ESMT F50L1G41LB SPI-NAND) |
| UART | 3.3 V USB-TTL adapter OR an ESP32 devboard with `EN` tied to GND (uses its onboard CP2102/CH340) |
| Pinout | See [`docs/01-hardware.md`](docs/01-hardware.md); 115200 8N1 |
| USB stick | Any FAT32, ≥ 64 MB; used to shuttle the ~50 MB firmware image |
| Host | Linux with `mtd-utils` (for `mkfs.ubifs` on overlay, optional), `squashfs-tools`, `dropbear`, `python3`, `pyserial` |

No secure-boot bypass, no specific SoC exploit. The whole chain works because CFE on this SKU doesn't password-lock the serial console and will accept any `bootargs` you hand it.

---

## Quick start

```bash
# 1. Install host prerequisites (Arch/Manjaro)
sudo pacman -S squashfs-tools mtd-utils dropbear python-pyserial

# 2. Clone this repo
git clone <your-fork-url> ax73-root-toolkit && cd ax73-root-toolkit

# 3. Drop your SSH pubkey (optional — used by dropbear if you get it working)
cp ~/.ssh/id_ed25519.pub payload/root/.ssh/authorized_keys

# 4. Generate a dropbear host key (optional, same caveat)
dropbearkey -t rsa -f payload/etc/dropbear/dropbear_rsa_host_key -s 2048

# 5. Grant yourself access to the serial device (one-time).
#    On Arch/Manjaro the group is 'uucp'; Debian/Ubuntu use 'dialout'.
sudo usermod -aG uucp "$USER"   # or dialout
#    Log out and back in so the new group takes effect.

# 6. Solder UART, power on the router, grab a shell:
python3 scripts/ax73-autoroot.py
# power-cycle when prompted; script drops you into /bin/sh on mtd0

# 7. From the shell, dump mtd0 to a USB stick plugged into the router.
#    See docs/02-getting-shell.md for the exact commands.

# 8. On the host, build the patched image:
./scripts/build-and-flash.sh <path-to-mtd0-dump.bin>
# This unsquashfs's, applies payload/, mksquashfs's, injects, produces
#   work/rootfs-injected.ubi.

# 9. Copy work/rootfs-injected.ubi back to the USB, plug into the router,
#    power-cycle, then:
python3 scripts/ax73-autoroot-backupbank.py
# This boots the router from mtd1 (backup bank) leaving mtd0 free.

# 10. In that shell, mount the USB and flash:
#       mount -t vfat /dev/sda1 /tmp/usb
#       cp /tmp/usb/rootfs-injected.ubi /tmp/new.ubi
#       /sbin/ubiformat /dev/mtd0 -f /tmp/new.ubi -s 2048 -y
#       sync && reboot -f

# 11. Router boots normal stock userspace, but:
#       - UART: press Enter → root shell (no password)
#       - `telnet 192.168.129.4 23` → root shell (no password)
```

Sanity-check the image size: a patched 30-32 MB squashfs fits into the stock 31.3 MB static volume. Don't grow it.

---

## Scripts

| Script | What it does |
|---|---|
| [`scripts/ax73-uart.py`](scripts/ax73-uart.py) | Send a single command over UART, read back until the prompt (`/ #` or `CFE>`), return stdout. Used as the building block for everything else. |
| [`scripts/ax73-autoroot.py`](scripts/ax73-autoroot.py) | Waits for power-on, spams Enter through the 1-second CFE autoboot prompt, issues `ba r "… ubi.mtd=0 … init=/bin/sh"` + `r`, leaves you at `/ #` on the main rootfs. |
| [`scripts/ax73-autoroot-backupbank.py`](scripts/ax73-autoroot-backupbank.py) | Same, but with `ubi.mtd=1`. Boots from the backup bank (mtd1) so `mtd0` is free for `ubiformat`. |
| [`scripts/inject-squashfs.py`](scripts/inject-squashfs.py) | Takes a stock `mtd0` dump and a new squashfs, produces a UBI image that preserves every stock header and only rewrites the `rootfs_squashfs` LEBs. The heart of the toolkit. |
| [`scripts/build-and-flash.sh`](scripts/build-and-flash.sh) | End-to-end host-side build: unsquashfs → apply `payload/` → mksquashfs → inject. Doesn't flash — gives you `work/rootfs-injected.ubi` for manual flashing. |

---

## Layout of `payload/`

Files placed in `payload/` are overlaid on top of the unsquashfs'd stock rootfs before repacking. Minimum set:

| File | Purpose |
|---|---|
| `payload/etc/inittab` | UART `ash --login`, `::respawn: telnetd :23` |
| `payload/etc/shadow` | `root::` so UART / telnet accept empty password |
| `payload/lib/preinit/20_check_jffs2_ready` | Restores the commented-out OpenWrt check so `ubi_rootfs_data` volume actually gets mounted |
| `payload/root/.ssh/authorized_keys` | Your SSH pubkey (dropbear can read it, if you can get dropbear working) |
| `payload/etc/dropbear/dropbear_rsa_host_key` | Pre-generated host key so the broken stock init-script doesn't need to generate one |

All payload files carry the permissions you want them to have in squashfs; `build-and-flash.sh` chowns everything to root:root before repacking.

---

## Rollback

Three independent paths, strongest to weakest:

1. **A/B banking** (works if mtd1 is intact — we never touch it):
   `CFE> c` → set `Boot image: 0 → 1` → boots stock backup rootfs.
2. **Re-flash from dump** (works if kernel can still boot):
   `ax73-autoroot-backupbank.py` → `ubiformat /dev/mtd0 -f /tmp/usb/mtd0-dump.bin -s 2048 -y`.
3. **TP-Link TFTP recovery** (works if CFE still alive):
   Hold the Reset button through power-on; router expects `ArcherAX73v1_tp_recovery.bin` at `192.168.0.100`. Needs TP-Link's signed firmware file.

Keep your `mtd0` dump on a USB stick somewhere **before** you start flashing. [Details in `docs/04-rollback.md`](docs/04-rollback.md).

---

## Status

- ✅ UART root — persistent
- ✅ Telnet root on port 23 — persistent
- ✅ **`/etc` and `/root` bind-mounted from a writable UBIFS volume** —
  you can edit files there and changes survive reboot. No full rootfs
  overlay (see below), but enough for 99% of persistent-config needs.
- ❌ **Full rootfs overlay via pivot_root** — not working. Kernel 4.1 on
  BCM6750 lacks overlayfs and mini_fo; the dupe-based fallback panics on
  the second boot. Our `70_pivot_ubifs_root` therefore does targeted
  bind-mounts instead of a full pivot.
- ❌ **Dropbear hangs** after `SSH2_MSG_SERVICE_ACCEPT`, even on
  self-connect through `127.0.0.1`. Something in TP-Link's dropbear 2019.78
  build stalls userauth. Not blocking (telnet works) but unsolved.
  See [`docs/05-dropbear-postmortem.md`](docs/05-dropbear-postmortem.md).

PRs or issues with dropbear debugging or a working full-overlay approach
very welcome.

---

## Hardware variants — read this before you flash

This toolkit was developed and tested against:

- **AX73 v1.0** (sticker on the bottom), **firmware 1.3.5 Build 20230919**.
- SoC Broadcom **BCM6750_A2** (seen in `/proc/cpuinfo`, CFE banner).
- **ESMT F50L1G41LB** 128 MB SPI-**NAND** (NOT the 16 MB NOR that public sources claim for some v1.0 batches — late-production v1.0 shipped with NAND too).

If your hardware differs, especially if you have a real NOR-flash v1.0 or the v2 (different partition layout, sometimes Qualcomm IPQ5018 instead of Broadcom), nothing here will work as-is. Check `/proc/mtd` and `ubinfo /dev/ubi0` before touching flash, and compare against [`docs/01-hardware.md`](docs/01-hardware.md).

---

## Authors

- [@gen1nya](https://github.com/gen1nya) — hands on the soldering iron, router, oscilloscope, patience
- Claude (Anthropic) — scripting, UBI archeology, inject-squashfs.py

Developed over one evening (2026-04-23) through a long live pair-debugging session on UART.

---

## License

MIT. See [`LICENSE`](LICENSE).

Use at your own risk. This voids your warranty. We are not responsible for your router.
