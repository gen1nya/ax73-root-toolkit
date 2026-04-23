# Flash surgery — squashfs injection into a stock UBI container

This is the substantive chapter. If you read only one thing in this repo, make it this one.

## What we're patching and why

| Path in squashfs | Change | Why |
|---|---|---|
| `/etc/inittab` | Replace `ttyAMA0::askfirst:/bin/login` with `ttyAMA0::askfirst:/bin/ash --login`; add `::respawn:/usr/sbin/telnetd -F -l /bin/ash -p 23` | UART root without `/etc/shadow` needing a valid hash; persistent telnet on 23 |
| `/etc/shadow` | `root::…` (empty hash, `!` → empty) | So `/bin/login` (or anything that calls into shadow) accepts empty password |
| `/lib/preinit/20_check_jffs2_ready` | Un-comment the original OpenWrt check | Allows `ubi_rootfs_data` overlay to take effect if you create that volume |
| `/etc/dropbear/dropbear_rsa_host_key` | Replace the zero-byte stock file with a real pre-generated 2048-bit RSA key | The stock init script's keygen fails silently because `/etc/dropbear/` is RO |
| `/root/.ssh/authorized_keys` | Your SSH pubkey | In case you get dropbear working |

Everything else is untouched. We want the router to look as close to stock as possible to the rest of the userspace — same hostname, same services, same configs.

## Step 1: unpack

```bash
mkdir work && cd work

# stock rootfs UBI container (the dump you made earlier)
cp /path/to/mtd0-rootfs.bin .

# the squashfs inside ubi0:rootfs_squashfs is also worth having as a sanity-check
cp /path/to/ubi0_0-squashfs.bin .

unsquashfs -d rootfs ubi0_0-squashfs.bin
```

You get `work/rootfs/` with 3164 files / 434 dirs / 424 symlinks. Poke around.

## Step 2: apply the payload

Copy the files from this repo's `payload/` tree into the extracted rootfs, making sure to fix ownership (squashfs-tools respect it) and perms:

```bash
sudo cp -a ../payload/. rootfs/
sudo chown -R root:root rootfs/
sudo chmod 600 rootfs/etc/shadow
sudo chmod 700 rootfs/root/.ssh
sudo chmod 600 rootfs/root/.ssh/authorized_keys
sudo chmod 700 rootfs/etc/dropbear
sudo chmod 600 rootfs/etc/dropbear/dropbear_rsa_host_key
```

## Step 3: rebuild the squashfs (DO read this)

The stock squashfs was built with:

```
Compression: xz
Block size:  131072
Filters:     none          ← critical: NO -Xbcj
Xattrs:      compressed    ← critical: DO NOT pass -no-xattrs
```

Mimic exactly:

```bash
sudo mksquashfs rootfs rootfs-patched.squashfs \
    -comp xz \
    -b 131072 \
    -all-root \
    -no-progress \
    -noappend
```

If you add `-Xbcj arm` — thinking it'll improve compression for ARM binaries — the resulting squashfs will mount (the superblock and inode table are readable), but reading individual files will fail with `EIO`. BCM6750's kernel (`squashfs: version 4.0 (2009/01/31)`) doesn't have the BCJ ARM filter compiled in. You'll get:

```
Kernel panic - not syncing: Requested init /etc/preinit failed (error -5).
```

after a successful UBI attach, which is a nasty error to debug — the squashfs looks valid, the mount succeeds, only reads fail.

Similarly, `-no-xattrs` produces a squashfs the kernel mounts cleanly but with slightly different layout semantics, and the patched `/etc/` has wrong xattrs at runtime. Just match stock.

Verify:

```bash
unsquashfs -s rootfs-patched.squashfs
# Compression xz
# Block size 131072
# Xattrs are compressed           ← yes
# Filesystem size ~31 MB          ← under stock's 32.8 MB
```

The patched squashfs should be smaller than or equal to the stock one (ours was 32.8 MB stock → 31.8 MB patched; no problem, UBI static volume can hold any size up to its LEB count × LEB size).

## Step 4: why `ubinize` fails

The naïve next step is:

```bash
# DON'T DO THIS
cat > ubinize.cfg <<EOF
[rootfs_squashfs]
mode=ubi
image=rootfs-patched.squashfs
vol_id=0
vol_size=32886784
vol_type=static
vol_name=rootfs_squashfs
vol_alignment=1
EOF
ubinize -o rootfs-new.ubi -m 2048 -p 131072 -s 2048 ubinize.cfg
```

You'll get a perfectly valid-looking UBI image. `ubiformat` it to `mtd0`. Reboot. Kernel greets you with:

```
ubi0: attaching mtd0
ubi0 error: ubi_compare_lebs: unsupported on-flash UBI format
ubi0 error: ubi_attach_mtd_dev: failed to attach mtd0, error -22
```

What's happening: `ubinize` ≥ 2.3 from `mtd-utils` 2.3 emits UBI images whose sqnum handling, or whatever subtle header field, triggers the ancient (Linux 4.1, 2015-vintage) UBI driver's "this is a format we don't know" branch in `ubi_compare_lebs`. Trying `-x 0`, `-x 1`, `-Q <specific-number>`, varying `-s` — none of it made the image attach.

Stock boots fine. That means the stock format **is** acceptable to this kernel. So: do not create a fresh UBI image; inject your payload into the stock one.

## Step 5: the injection approach

Read the stock `mtd0` dump as a sequence of 128 KB PEBs. Each PEB has:

```
offset 0      EC header    (64 B, UBI# magic)
offset 2048   VID header   (64 B, UBI! magic — if PEB is allocated to a volume)
offset 4096   data area    (126976 B — a.k.a. one LEB)
```

For every PEB whose VID header says `vol_id = 0` (the `rootfs_squashfs` volume), two things are true:
- `lnum` is this PEB's logical position within the volume (0, 1, 2, …, 258)
- the data area contains that LEB's bytes of squashfs content

The `data_crc` field of the VID header is the UBI-CRC32 of the valid data bytes (not the padding). `data_size` is how many bytes of the LEB are valid; `used_ebs` is the total LEB count used by this static volume (same value in every PEB of the volume).

Our patched squashfs is smaller than stock. It needs fewer LEBs (251 vs 259). So:

1. Enumerate PEBs of `vol_id = 0`.
2. For LEB 0..250: overwrite the PEB's data area with the corresponding chunk of the new squashfs, update `data_size`, `used_ebs = 251`, `data_crc`, and `hdr_crc`.
3. For LEB 251..258 (the unused tail): wipe the PEB's VID header + data area to `0xFF`, which makes it a free PEB from UBI's perspective (the EC header is still valid, the erase counter is preserved).

Every other PEB — belonging to `vol_id = 2147479551` (UBI internal layout volume), to `vol_id = 1` (`ubi_rootfs_data`, if you've created it), or unallocated — stays identical. Sequence numbers, image_seq, erase counters — none of it changes.

The resulting file is 50 MB, ready to be `ubiformat`ted straight to `mtd0`.

### UBI CRC32 — do not get this wrong

UBI's CRC32 is the standard Ethernet CRC32 polynomial with:

- Initial state: `0xFFFFFFFF`
- **No** final XOR

Python's `zlib.crc32(data)` gives you the Ethernet variant with `init=0xFFFFFFFF` **and** a final XOR by `0xFFFFFFFF`. To cancel the final XOR and match UBI:

```python
def ubi_crc32(data):
    return (~zlib.crc32(data)) & 0xFFFFFFFF
```

(Calling `zlib.crc32(data, 0xFFFFFFFF)` does **not** do this — the `init` parameter there gets XORed with `0xFFFFFFFF` internally; the final XOR is still applied.)

Quick sanity check against a stock PEB:

```python
# Verify: compute hdr_crc of stock EC header, compare with stored value
with open('mtd0-rootfs.bin', 'rb') as f:
    peb0 = f.read(64)
stored = int.from_bytes(peb0[60:64], 'big')
computed = (~zlib.crc32(peb0[:60])) & 0xFFFFFFFF
assert stored == computed, "CRC formula is wrong"
```

If this assertion doesn't pass, your injection will produce a UBI image the kernel rejects with `ubi_io_read_vid_hdr: bad CRC`.

## Step 6: build the injected image

[`scripts/inject-squashfs.py`](../scripts/inject-squashfs.py) does all of the above:

```bash
python3 scripts/inject-squashfs.py \
    --stock work/mtd0-rootfs.bin \
    --squashfs work/rootfs-patched.squashfs \
    --out work/rootfs-injected.ubi
```

Output is a 50 MB file with every stock header preserved and the `rootfs_squashfs` LEBs rewritten. Verify it's valid:

```bash
# EC magic at offset 0
head -c 4 work/rootfs-injected.ubi | xxd
# 00000000: 5542 4923                                UBI#
# Size should match mtd0:
ls -la work/rootfs-injected.ubi   # 52953088 bytes == 0x03280000
```

## Step 7: flash

You can't `ubiformat` a mounted UBI, and `mtd0` is mounted as root whenever you boot from the primary bank. The trick is to boot from the **backup** bank (`mtd1`, `rootfs_update`) first, which makes `mtd0` idle.

Power-cycle the router, run:

```bash
python3 scripts/ax73-autoroot-backupbank.py
```

This is identical to `ax73-autoroot.py` but with `ubi.mtd=1` in the cmdline it hands to CFE. When you get `/ #`, mtd0 is free:

```sh
cat /sys/class/ubi/ubi0/mtd_num    # prints: 1
cat /proc/mtd | head -1            # mtd0: 03280000 00020000 "rootfs"
# mtd0 is listed but not attached to any UBI.
```

Pull your USB stick out of the host, plug it into the router, then in the router shell:

```sh
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/ehci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-hcd.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/host/xhci-plat-hcd.ko
insmod /lib/modules/4.1.52/extra/bcm_usb.ko
insmod /lib/modules/4.1.52/kernel/drivers/usb/storage/usb-storage.ko
sleep 3

mkdir -p /tmp/usb
mount -t vfat /dev/sda1 /tmp/usb
cp /tmp/usb/rootfs-injected.ubi /tmp/new.ubi
md5sum /tmp/new.ubi                # compare with the one you built on the host

/sbin/ubiformat /dev/mtd0 -f /tmp/new.ubi -s 2048 -y
sync
reboot -f
```

`ubiformat` will print `formatting eraseblock N — X% complete` for all 404 blocks, then `flashing eraseblock N` for the ones that carry data, then exit. You may see:

```
ubiformat: error!: no eraseblocks for volume table
```

That's non-fatal in our case. The error means ubiformat wanted to write its own vol_tbl into a spare PEB, but every PEB in the image is allocated or explicitly empty. The vol_tbl is already embedded in the stock container we flashed (as `vol_id = 0x7FFFFE7F`, 2 LEBs), so the image is complete without ubiformat's help.

## Step 8: first boot

On the next power-on CFE picks the primary bank by default (unchanged setting `Boot image: 0`). The kernel:

1. Attaches UBI on mtd0 (no `unsupported on-flash UBI format` because we kept stock headers).
2. Mounts `rootfs_squashfs` → squashfs (no `EIO` because we didn't use BCJ).
3. Runs `/etc/preinit`, which runs `/etc/init.d/rcS`, which brings up telnetd on 23 because we put it in inittab.

First verification:

```bash
# From another terminal on your host:
telnet 192.168.129.4 23
# After connection hit Enter:
# BusyBox v1.19.4  built-in shell (ash)
# / # id
# uid=0(root) gid=0(root)
```

IP may differ; in standalone AP-mode without an upstream DHCP server the router sets itself to `192.168.129.4`. Once plugged into a LAN with DHCP it gets whatever the upstream hands out.

UART gives the same root shell by just pressing Enter.

## What survives a factory reset (🚨)

A factory reset from the stock web UI / reset button clears `/tp_data` and `/data` (UBIFS user configs) but **does not** touch `mtd0`. Your patches are in the squashfs, which is part of mtd0, which is part of the firmware image bank. So:

- Web-UI factory reset: your backdoor survives.
- TP-Link firmware upgrade via web UI: TP-Link's .bin gets written to the **inactive** bank, then boot image is switched. Your patched rootfs would become the new backup; the next TP-Link image boots, you lose UART/telnet until you flip back.
- TFTP recovery: writes a full signed firmware to both banks. Your patches go away.

If you want sustained persistence across TP-Link firmware updates you need additional machinery (a flash-monitor that re-patches after every update, or write-protection of the relevant NAND blocks). Out of scope here.
