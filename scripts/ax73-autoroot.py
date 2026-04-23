#!/usr/bin/env python3
"""
Power-cycle the router while this script is running; it will:

1. Detect the CFE banner on serial.
2. Spam Enter during the 1-second 'Press any key to stop auto run' window.
3. At the CFE> prompt, send:
       ba r "isolcpus=2 root=/dev/ubiblock0_0 ubi.mtd=0 ubi.block=0,0 \\
             rootfstype=squashfs console=ttyAMA0 init=/bin/sh"
       r
4. Wait for '/ #' shell prompt.
5. Mount /proc, /sys, /tmp (tmpfs), and attach+mount the two user-data UBIFS
   volumes (ubi1 = /data, ubi2 = /tp_data).
6. Print '=== READY ===' and exit.

After it exits, use scripts/ax73-uart.py to run any further commands on the
router.

Targets mtd0 — the main rootfs. Use ax73-autoroot-backupbank.py if you need to
flash mtd0 (it boots the backup bank instead, leaving mtd0 idle).
"""

import re
import serial
import sys
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200

CFE_BA_CMD = (
    'ba r "isolcpus=2 root=/dev/ubiblock0_0 ubi.mtd=0 ubi.block=0,0 '
    'rootfstype=squashfs console=ttyAMA0 init=/bin/sh"'
)

POST_ROOT_SETUP = [
    'mount -t proc none /proc',
    'mount -t sysfs none /sys',
    'mount -t tmpfs tmpfs /tmp',
    'mkdir -p /tmp/data /tmp/misc2',
    'ubiattach /dev/ubi_ctrl -m 2 >/dev/null 2>&1',
    'mount -t ubifs /dev/ubi1_0 /tmp/data',
    'ubiattach /dev/ubi_ctrl -m 8 >/dev/null 2>&1',
    'mount -t ubifs /dev/ubi2_0 /tmp/misc2',
    'echo === SETUP COMPLETE ===',
]


def log(msg):
    sys.stderr.write(f'[autoroot] {msg}\n')
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
                log('dropped into CFE; injecting ba r + r')
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
                log('root shell on mtd0')
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
                log('=== READY. Use scripts/ax73-uart.py from here. ===')
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
