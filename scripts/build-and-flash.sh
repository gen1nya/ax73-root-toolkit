#!/bin/bash
# End-to-end host-side build pipeline:
#   1. Unsquashfs the stock squashfs (extracted from mtd0 dump).
#   2. Overlay our payload/ tree on top.
#   3. Fix ownership and perms.
#   4. mksquashfs back with stock-compatible settings.
#   5. Inject into the stock UBI container.
#
# Output: work/rootfs-injected.ubi — copy to USB, flash via
#         /sbin/ubiformat /dev/mtd0 -f /tmp/new.ubi -s 2048 -y
#         from the backup-bank shell (ax73-autoroot-backupbank.py).
#
# Usage:
#     scripts/build-and-flash.sh <path-to-mtd0-rootfs.bin>
#
# Prerequisites (Arch/Manjaro):
#     sudo pacman -S squashfs-tools mtd-utils dropbear python-pyserial

set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <mtd0-dump.bin>" >&2
    echo "" >&2
    echo "  <mtd0-dump.bin>: stock mtd0 dump captured with" >&2
    echo "     dd if=/dev/mtdblock0 of=/tmp/usb/mtd0-rootfs.bin bs=131072" >&2
    echo "  from the router shell (see docs/02-getting-shell.md)" >&2
    exit 1
fi

MTD0_DUMP="$1"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$REPO_ROOT/work"
PAYLOAD="$REPO_ROOT/payload"

if [ ! -f "$MTD0_DUMP" ]; then
    echo "error: $MTD0_DUMP not found" >&2
    exit 1
fi

MTD0_SIZE=$(stat -c%s "$MTD0_DUMP")
if [ "$MTD0_SIZE" != "52953088" ]; then
    echo "warning: mtd0 dump size is $MTD0_SIZE, expected 52953088" >&2
    echo "  Continuing anyway — make sure this is actually a raw mtd0 dump" >&2
fi

mkdir -p "$WORK"
cd "$WORK"

echo "==> extracting squashfs from mtd0 dump"
# UBI volume 0 (rootfs_squashfs) starts at PEB 2 (offset 0x40000) in stock;
# for simplicity pull a copy of the dump here and work off a squashfs extracted
# elsewhere. If you already have ubi0_0-squashfs.bin (from dd if=/dev/ubi0_0),
# pass that instead — skip-if-exists:
if [ ! -f rootfs-extracted/etc/inittab ]; then
    # Derive squashfs payload out of the UBI dump — we read PEBs of vol 0
    # from the mtd0 container and concat their data areas.
    python3 - "$MTD0_DUMP" > stock-squashfs.bin <<'PY'
import struct, sys
PEB, VID_OFF, DATA_OFF = 131072, 2048, 4096
LEB = PEB - DATA_OFF
with open(sys.argv[1], 'rb') as f:
    data = f.read()
n_pebs = len(data) // PEB
pieces = {}   # lnum -> (data_size, bytes)
for p in range(n_pebs):
    vid = data[p*PEB+VID_OFF:p*PEB+VID_OFF+64]
    if vid[:4] != b'UBI!':
        continue
    vid_id = struct.unpack('>I', vid[8:12])[0]
    if vid_id != 0:
        continue
    lnum = struct.unpack('>I', vid[12:16])[0]
    ds = struct.unpack('>I', vid[20:24])[0]
    pieces[lnum] = data[p*PEB+DATA_OFF:p*PEB+DATA_OFF+ds]
for lnum in sorted(pieces):
    sys.stdout.buffer.write(pieces[lnum])
PY

    echo "==> unsquashfs into rootfs-extracted/"
    rm -rf rootfs-extracted
    unsquashfs -d rootfs-extracted stock-squashfs.bin >/dev/null
fi

echo "==> applying payload/ on top"
sudo cp -a "$PAYLOAD"/. rootfs-extracted/
# also pull in .ssh from payload if present
if [ -d "$PAYLOAD/root" ]; then
    sudo cp -a "$PAYLOAD/root/." rootfs-extracted/root/
fi

echo "==> fixing ownership and perms"
sudo chown -R root:root rootfs-extracted
# files the squashfs cares about having tight perms on
[ -f rootfs-extracted/etc/shadow ]                && sudo chmod 600 rootfs-extracted/etc/shadow
[ -d rootfs-extracted/root/.ssh ]                 && sudo chmod 700 rootfs-extracted/root/.ssh
[ -f rootfs-extracted/root/.ssh/authorized_keys ] && sudo chmod 600 rootfs-extracted/root/.ssh/authorized_keys
[ -d rootfs-extracted/etc/dropbear ]              && sudo chmod 700 rootfs-extracted/etc/dropbear
[ -f rootfs-extracted/etc/dropbear/dropbear_rsa_host_key ] \
    && sudo chmod 600 rootfs-extracted/etc/dropbear/dropbear_rsa_host_key

echo "==> mksquashfs with stock-compatible settings"
# CRITICAL: xz without BCJ, with xattrs. See docs/03-flash-surgery.md.
sudo rm -f rootfs-patched.squashfs
sudo mksquashfs rootfs-extracted rootfs-patched.squashfs \
    -comp xz \
    -b 131072 \
    -all-root \
    -no-progress \
    -noappend

SQSZ=$(stat -c%s rootfs-patched.squashfs)
echo "    -> rootfs-patched.squashfs is $SQSZ bytes"
if [ "$SQSZ" -gt 32886784 ]; then
    echo "error: squashfs is too big for the rootfs_squashfs volume (max 32886784)" >&2
    exit 1
fi

echo "==> injecting into stock UBI container"
python3 "$REPO_ROOT/scripts/inject-squashfs.py" \
    --stock "$MTD0_DUMP" \
    --squashfs rootfs-patched.squashfs \
    --out rootfs-injected.ubi

echo ""
echo "=========================================================================="
echo "Built:  $WORK/rootfs-injected.ubi"
echo "MD5:    $(md5sum rootfs-injected.ubi | cut -d' ' -f1)"
echo ""
echo "Next: copy this file to your USB stick, then"
echo "      python3 scripts/ax73-autoroot-backupbank.py"
echo "and in the resulting shell:"
echo ""
echo "      # load USB, mount, flash:"
echo "      mkdir -p /tmp/usb && mount -t vfat /dev/sda1 /tmp/usb"
echo "      cp /tmp/usb/rootfs-injected.ubi /tmp/new.ubi"
echo "      md5sum /tmp/new.ubi   # compare!"
echo "      /sbin/ubiformat /dev/mtd0 -f /tmp/new.ubi -s 2048 -y"
echo "      sync; reboot -f"
echo "=========================================================================="
