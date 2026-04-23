# Getting a shell (no flashing yet)

Goal of this chapter: drop into `/bin/sh` running as PID 1 on the real `mtd0` rootfs, via CFE, without touching flash. From there you can dump partitions, probe the running system, and generally poke around before you commit to anything.

## 1. Open a serial console

```bash
sudo chmod a+rw /dev/ttyUSB0                 # once per boot
picocom -b 115200 --logfile boot.txt /dev/ttyUSB0
```

Exit with `Ctrl-A Ctrl-X`.

## 2. Interrupt CFE autoboot

Power-cycle the router. Within one second you'll see:

```
*** Press any key to stop auto run (1 seconds) ***
```

Hit Enter repeatedly (or spam any key) until you get:

```
CFE>
```

If you miss the window and the kernel boots, power-cycle and try again.

## 3. The `ba r` + `init=/bin/sh` trick

Once at `CFE>`:

```
ba r "isolcpus=2 root=/dev/ubiblock0_0 ubi.mtd=0 ubi.block=0,0 rootfstype=squashfs console=ttyAMA0 init=/bin/sh"
r
```

`ba r` replaces the kernel command line; `r` runs the already-selected kernel image with it. Kernel boots, mounts the squashfs, and `exec`s `/bin/sh` instead of the stock `/etc/preinit`. You land at `/ #`.

**Do not add `rw`.** Squashfs is inherently read-only; `rw` makes the mount fail with `EROFS (-30)` and the kernel panics on mount.

## 4. Minimum liveable environment

`init=/bin/sh` skips the usual preinit hooks, so nothing is mounted beyond what CFE and the kernel set up. Add what you need:

```sh
mount -t proc  none /proc
mount -t sysfs none /sys
mount -t tmpfs tmpfs /tmp
```

## 5. Get the persistent UBIFS volumes online

The `data` (mtd2) and `misc2` (mtd8) partitions are UBI containers with a single UBIFS volume each. Attach them:

```sh
mkdir -p /tmp/data /tmp/misc2
ubiattach /dev/ubi_ctrl -m 2
mount -t ubifs /dev/ubi1_0 /tmp/data
ubiattach /dev/ubi_ctrl -m 8
mount -t ubifs /dev/ubi2_0 /tmp/misc2
```

Both are writable. `/tmp/data` holds `.kernel_nvram.setting` + `.user_nvram.setting` (Broadcom WL config); `/tmp/misc2` holds the encrypted TP-Link XML configs and your WPS PIN.

## 6. Bring in USB storage

If you want to shuttle files without going through UART:

```sh
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/ehci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-plat-hcd.ko
insmod /lib/modules/4.1.52/extra/bcm_usb.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/storage/usb-storage.ko
sleep 3
ls /dev/sd*               # sda1 should appear
mkdir -p /tmp/usb
mount -t vfat /dev/sda1 /tmp/usb
```

Do **not** `insmod` `bcm_enet.ko` here if you don't need the network — on several of our tries the PHY probe hung the kernel and forced a power-cycle. The USB modules are safe.

## 7. Dump mtd0 for a backup

Before any flash experiments:

```sh
# mtd0 = rootfs UBI container, 50 MB
dd if=/dev/mtdblock0 of=/tmp/usb/mtd0-rootfs.bin bs=131072 status=none

# optional companions — all useful for full recovery
dd if=/dev/ubi0_0     of=/tmp/usb/ubi0_0-squashfs.bin bs=131072 status=none  # 32 MB
dd if=/dev/mtdblock6  of=/tmp/usb/mtd6-bootfs.bin     bs=131072 status=none  # 4.4 MB
dd if=/dev/mtdblock3  of=/tmp/usb/mtd3-nvram.bin      bs=131072 status=none  # 1 MB
sync
```

Pull the USB, move the dump to your host, archive it — this is your recovery kit.

## 8. Automating steps 2-4

[`scripts/ax73-autoroot.py`](../scripts/ax73-autoroot.py) watches the serial port, catches the autoboot prompt, injects the `ba r`/`r` sequence, then mounts proc/sys/tmpfs and the two UBIFS volumes. Usage:

```bash
python3 scripts/ax73-autoroot.py
# then power-cycle the router within ~180 s
```

It leaves the serial line at `/ #` with everything set up. After that, `scripts/ax73-uart.py "<command>"` gives you a one-shot `run-on-router` helper for the rest of the session.

## Notes on `ba` and `kernp`

The CFE on this SKU silently merges any `ba r` / `ba a` input with its built-in defaults, keeping the rightmost occurrence of each `key=value` pair. The stock baseline includes `init=/etc/preinit` at the tail, so a naïve `ba a "init=/bin/sh"` loses the race (stock `init=/etc/preinit` wins as the last one).

`ba r "… init=/bin/sh"` works because the replacement moves our `init=` to the tail.

`kernp` (documented as "extra bootloader parameter for kernel") didn't reliably stick in our testing — the kernel command line after `r` was unchanged. We didn't dig into why; `ba r` is enough.
