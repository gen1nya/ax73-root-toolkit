# Rollback / recovery

Three paths, cheapest first.

## 1. CFE A/B banking (no UART dance needed)

The AX73 has two firmware banks. We only ever write to bank A (`mtd0` rootfs + `mtd6` bootfs). Bank B (`mtd1` + `mtd7`) is the stock backup and remains untouched.

If your patched rootfs boots but misbehaves — or if you brick it in a way that the kernel still boots but userspace is broken:

1. Catch CFE at power-on (spam Enter during the `Press any key to stop auto run (1 seconds)` window).
2. `c` — interactive boot-params editor.
3. Enter through fields until `Boot image (0=latest, 1=previous)`, change to `1`, Enter through the rest.
4. `reset` → reboots, loads stock backup userspace.

Your patched bank A sits there, unloaded. You can re-flash it any time, or use `c` again to switch back.

## 2. Re-flash from dump

If the kernel still boots (CFE works, but bank A's rootfs is totally broken), use the backup-bank autoroot:

```bash
python3 scripts/ax73-autoroot-backupbank.py
# power-cycle when prompted
```

You land at `/ #` on mtd1's stock rootfs. mtd0 is free:

```sh
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/ehci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-plat-hcd.ko
insmod /lib/modules/4.1.52/extra/bcm_usb.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/storage/usb-storage.ko
sleep 3

mkdir -p /tmp/usb
mount -t vfat /dev/sda1 /tmp/usb
# Flash back the original stock dump you made before touching anything
/sbin/ubiformat /dev/mtd0 -f /tmp/usb/mtd0-rootfs.bin -s 2048 -y
sync
reboot -f
```

You may see `ubiformat: error!: no eraseblocks for volume table` — expected, same reason as during the patched flash.

## 3. TP-Link TFTP recovery

Last resort — only useful if CFE is broken (which shouldn't happen since we never write to `mtd3` or the CFE-ROM region).

1. Connect your host to a LAN port of the router.
2. Set host IP to `192.168.0.100/24`.
3. Run a TFTP server on the host, sharing TP-Link's signed recovery firmware as `ArcherAX73v1_tp_recovery.bin`:
   ```bash
   # on the host
   sudo dnf install tftp-server    # or your distro's equivalent
   # drop ArcherAX73v1_tp_recovery.bin into /srv/tftp/
   sudo systemctl start tftp.socket
   ```
4. Press and hold the router's Reset button while powering it on, release after ~10 seconds.
5. CFE enters recovery mode: sets itself to `192.168.0.1`, TFTPs the file, writes it to flash.

You need the actual signed recovery `.bin` from TP-Link (not our patched image). Grab it from TP-Link's download page for your region / hardware revision, or from a TFTP-capture of the stock recovery process. We do not distribute it.

## What we will **never** recommend

- Writing anything to `mtd3` (`nvram`) — that's CFE's NVRAM. Corrupt it and the only fallback is a physical NAND programmer.
- Writing to `mtd6`/`mtd7` (`bootfs`/`bootfs_update`) unless you're rebuilding the kernel image. We keep the stock kernel unmodified.
- Blindly `ubiformat`ting without `-y` from a non-interactive shell — it prompts for confirmation, and the UART-over-ssh-over-whatever chain sometimes swallows the "y".

## Insurance checklist

Before **any** flash:

- [ ] Full `mtd0` dump on at least two devices (host + USB stick stays with the router).
- [ ] `mtd6` (bootfs) and `mtd3` (nvram) dumps too — small, always take them.
- [ ] You can get a shell via `ax73-autoroot.py` (tests that UART, CFE prompt, `ba r` path are alive).
- [ ] You know which LAN port is which if you plan to use TFTP recovery.

If any of those is missing, don't flash.
