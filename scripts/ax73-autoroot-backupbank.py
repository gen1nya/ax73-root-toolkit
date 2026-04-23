#!/usr/bin/env python3
"""
Same behaviour as ax73-autoroot.py but boots from the BACKUP bank (mtd1) by
passing ubi.mtd=1 on the kernel command line. This leaves mtd0 detached, so
ubiformat /dev/mtd0 can write without fighting a mounted root.

Use this when you want to flash a new rootfs image to mtd0:

    python3 scripts/ax73-autoroot-backupbank.py
    # power-cycle router
    # shell lands with cat /sys/class/ubi/ubi0/mtd_num == 1
    # then in the shell:
    #   mount USB, copy rootfs-injected.ubi to /tmp/new.ubi
    #   /sbin/ubiformat /dev/mtd0 -f /tmp/new.ubi -s 2048 -y
    #   sync; reboot -f
"""

import re
import serial
import sys
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200

# Note the ubi.mtd=1 below — this is the only difference from ax73-autoroot.py.
CFE_BA_CMD = (
    'ba r "isolcpus=2 root=/dev/ubiblock0_0 ubi.mtd=1 ubi.block=0,0 '
    'rootfstype=squashfs console=ttyAMA0 init=/bin/sh"'
)

POST_ROOT_SETUP = [
    'mount -t proc none /proc',
    'mount -t sysfs none /sys',
    'mount -t tmpfs tmpfs /tmp',
    'echo === BACKUP BANK SETUP COMPLETE ===',
]


def log(msg):
    sys.stderr.write(f'[autoroot-bb] {msg}\n')
    sys.stderr.flush()


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.05)
    log(f'opened {PORT}@{BAUD}')
    log('power-cycle the router now (within 180 s)')

    buf = ''
    state = 'wait_poweron'
    last_spam = 0
    start = time.time()

    while True:
        chunk = ser.read(4096)
        if chunk:
            decoded = chunk.decode(errors='replace')
            sys.stdout.write(decoded)
            sys.stdout.flush()
            buf += decoded
            if len(buf) > 32768:
                buf = buf[-16384:]

        now = time.time()

        if state == 'wait_poweron':
            if 'BTRM' in buf or 'CFE version' in buf or 'Press any key' in buf:
                state = 'wait_autoboot'
                log('boot detected, spamming CFE interrupt')
                buf = ''

        elif state == 'wait_autoboot':
            if now - last_spam > 0.05:
                ser.write(b'\r')
                last_spam = now
            if 'CFE>' in buf:
                state = 'in_cfe'
                log('dropped into CFE; booting BACKUP bank (ubi.mtd=1)')
                time.sleep(0.4)
                ser.write((CFE_BA_CMD + '\r').encode())
                time.sleep(0.3)
                ser.write(b'r\r')
                time.sleep(0.2)
                buf = ''
                state = 'wait_shell'

        elif state == 'wait_shell':
            tail = buf[-200:]
            if re.search(r'/\s*#\s*$', tail) or tail.strip().endswith('/ #'):
                state = 'setup'
                log('root shell on BACKUP bank — mtd0 is FREE for flashing')
                time.sleep(0.3)

        elif state == 'setup':
            for cmd in POST_ROOT_SETUP:
                ser.write((cmd + '\r').encode())
                time.sleep(0.4)
            log('setup commands sent; draining')
            state = 'done'
            done_start = now

        elif state == 'done':
            if now - done_start > 3.0:
                log('=== READY. mtd0 can now be ubiformat-ed safely. ===')
                ser.close()
                return

        if now - start > 180:
            log('TIMEOUT after 180 s — did you power-cycle the router?')
            ser.close()
            sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log('aborted')
        sys.exit(130)
