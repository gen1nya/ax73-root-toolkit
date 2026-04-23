#!/usr/bin/env python3
"""
Inject a patched squashfs into a stock UBI container.

Read the stock mtd0 dump as a sequence of 128 KiB PEBs. For each PEB that
belongs to the target static volume (rootfs_squashfs, vol_id=0 by default):

  - If its LEB falls within the new squashfs: overwrite the data area with
    the new content, recompute data_size, used_ebs, data_crc; re-seal the
    VID header with a fresh hdr_crc.

  - Otherwise (unused tail): wipe the VID header and data area to 0xFF,
    turning the PEB back into a free PEB. EC header is preserved (keeps
    erase counter + image_seq, which is what the kernel checks).

Every other PEB (layout volume vol_id=0x7FFFFE7F, any other user volumes,
unallocated PEBs) is copied through untouched. This way the kernel's UBI
driver sees a container bit-for-bit identical to stock in every respect
except the content of the target volume.

The CRC32 variant UBI uses is Ethernet CRC32 with init=0xFFFFFFFF and NO
final XOR. Python's zlib.crc32 applies the final XOR; we cancel it with ~.

Usage:
    inject-squashfs.py --stock mtd0-rootfs.bin \\
                        --squashfs rootfs-patched.squashfs \\
                        --out rootfs-injected.ubi
"""

import argparse
import struct
import sys
import zlib

# NAND geometry for ESMT F50L1G41LB on the AX73.
# (Hardcoded because the whole point is to match mtd0 exactly.)
PEB_SIZE = 131072
VID_OFFSET = 2048
DATA_OFFSET = 4096
LEB_SIZE = PEB_SIZE - DATA_OFFSET  # 126976

TARGET_VOL_ID = 0  # rootfs_squashfs


def ubi_crc32(data: bytes) -> int:
    """UBI's CRC32: init=0xFFFFFFFF, NO final XOR."""
    return (~zlib.crc32(data)) & 0xFFFFFFFF


def inject(stock_path, squashfs_path, out_path, vol_id=TARGET_VOL_ID,
           verbose=True):
    stock = bytearray(open(stock_path, 'rb').read())
    new_squashfs = open(squashfs_path, 'rb').read()

    n_pebs = len(stock) // PEB_SIZE
    if verbose:
        print(f'stock container:   {n_pebs} PEBs, {len(stock):,} bytes',
              file=sys.stderr)
        print(f'new squashfs:      {len(new_squashfs):,} bytes', file=sys.stderr)

    # Build LEB -> PEB map for the target volume
    leb_to_peb = {}
    for p in range(n_pebs):
        vid_off = p * PEB_SIZE + VID_OFFSET
        vid = stock[vid_off:vid_off + 64]
        if vid[:4] != b'UBI!':
            continue
        this_vol = struct.unpack('>I', vid[8:12])[0]
        this_lnum = struct.unpack('>I', vid[12:16])[0]
        if this_vol == vol_id:
            leb_to_peb[this_lnum] = p

    total_lebs = len(leb_to_peb)
    if total_lebs == 0:
        sys.exit(f'error: no PEBs found with vol_id={vol_id}')
    if verbose:
        print(f'target vol {vol_id}:   {total_lebs} LEBs mapped',
              file=sys.stderr)

    new_used_ebs = (len(new_squashfs) + LEB_SIZE - 1) // LEB_SIZE
    if new_used_ebs > total_lebs:
        sys.exit(
            f'error: new squashfs needs {new_used_ebs} LEBs but only '
            f'{total_lebs} are available in the target volume'
        )
    if verbose:
        print(f'new squashfs fits in {new_used_ebs} LEBs (of {total_lebs} '
              'available)', file=sys.stderr)

    for lnum in range(total_lebs):
        peb = leb_to_peb[lnum]
        peb_off = peb * PEB_SIZE
        vid_off = peb_off + VID_OFFSET

        if lnum < new_used_ebs:
            # Rewrite this LEB with the corresponding chunk of new squashfs.
            chunk = new_squashfs[lnum * LEB_SIZE:(lnum + 1) * LEB_SIZE]
            # Last LEB may be short — pad to LEB_SIZE with 0xFF for storage,
            # but data_crc covers only the valid data bytes.
            if len(chunk) < LEB_SIZE:
                chunk = chunk + b'\xff' * (LEB_SIZE - len(chunk))

            data_size = len(new_squashfs) - lnum * LEB_SIZE
            if data_size > LEB_SIZE:
                data_size = LEB_SIZE

            # Data area
            stock[peb_off + DATA_OFFSET:peb_off + DATA_OFFSET + LEB_SIZE] = chunk

            # Patch VID header
            vid = bytearray(stock[vid_off:vid_off + 64])
            dcrc = ubi_crc32(chunk[:data_size])
            struct.pack_into('>I', vid, 20, data_size)
            struct.pack_into('>I', vid, 24, new_used_ebs)
            struct.pack_into('>I', vid, 32, dcrc)
            hdr_crc = ubi_crc32(bytes(vid[:60]))
            struct.pack_into('>I', vid, 60, hdr_crc)
            stock[vid_off:vid_off + 64] = vid

            if verbose and (lnum < 2 or lnum >= new_used_ebs - 2):
                print(f'  LEB {lnum:3d} (PEB {peb}): data_size={data_size} '
                      f'data_crc=0x{dcrc:08x}', file=sys.stderr)
        else:
            # Unused tail LEB — turn PEB into an empty PEB (EC header stays,
            # VID header + data wiped).
            for i in range(vid_off, peb_off + PEB_SIZE):
                stock[i] = 0xFF

    with open(out_path, 'wb') as f:
        f.write(stock)
    if verbose:
        print(f'wrote {out_path} ({len(stock):,} bytes)', file=sys.stderr)

    # Post-hoc verification — make sure every stored data_crc matches the
    # content we just wrote.
    data = bytes(stock)
    for lnum in range(new_used_ebs):
        peb = leb_to_peb[lnum]
        vid = data[peb * PEB_SIZE + VID_OFFSET:peb * PEB_SIZE + VID_OFFSET + 64]
        ds = struct.unpack('>I', vid[20:24])[0]
        stored = struct.unpack('>I', vid[32:36])[0]
        actual = data[peb * PEB_SIZE + DATA_OFFSET:peb * PEB_SIZE + DATA_OFFSET + ds]
        computed = ubi_crc32(actual)
        if stored != computed:
            sys.exit(f'error: LEB {lnum} (PEB {peb}): stored CRC 0x{stored:08x} '
                     f'vs computed 0x{computed:08x}')
    if verbose:
        print('verification: all data_crc fields match', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stock', required=True,
                    help='stock mtd0 dump (50 MB UBI container from '
                         'dd if=/dev/mtdblock0)')
    ap.add_argument('--squashfs', required=True,
                    help='patched squashfs image (from mksquashfs)')
    ap.add_argument('--out', required=True,
                    help='output UBI image, ready for ubiformat -f')
    ap.add_argument('--vol-id', type=int, default=TARGET_VOL_ID,
                    help=f'target volume id (default {TARGET_VOL_ID} = rootfs_squashfs)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    inject(args.stock, args.squashfs, args.out,
           vol_id=args.vol_id, verbose=not args.quiet)


if __name__ == '__main__':
    main()
