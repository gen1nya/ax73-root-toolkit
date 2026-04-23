#!/usr/bin/env python3
"""
One-shot UART helper for the AX73 root shell.

Sends a single command line, reads back everything until the prompt
(/ # on the router shell, CFE> in CFE), prints to stdout.

Usage:
    ax73-uart.py "<command>"
    echo "<command>" | ax73-uart.py
    ax73-uart.py --timeout 60 "<command>"

Defaults:
    port   /dev/ttyUSB0
    baud   115200
    prompt regex matches '/ #' or 'CFE>' at end of line
    timeout 20 s (fine for most commands; raise to ~240 s for ubiformat)
"""

import argparse
import re
import serial
import sys
import time


def talk(port, baud, cmd, timeout_sec, prompt_re):
    ser = serial.Serial(port, baud, timeout=0.3)
    # drain any pending output first, prime with a newline
    ser.reset_input_buffer()
    ser.write(b'\n')
    time.sleep(0.2)
    ser.read(10000)

    # send the command
    ser.write(cmd.encode() + b'\n')

    buf = b''
    last_rx = time.time()
    start = time.time()
    while time.time() - start < timeout_sec:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            last_rx = time.time()
            if prompt_re and prompt_re.search(buf.decode(errors='replace')):
                time.sleep(0.15)             # let trailing bytes arrive
                buf += ser.read(10000)
                break
        else:
            # commands that stream slowly (grep -r, dd, ubiformat progress)
            # need a long idle tolerance.
            if buf and time.time() - last_rx > 15.0:
                break
    ser.close()
    return buf.decode(errors='replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='/dev/ttyUSB0')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--timeout', type=float, default=20.0)
    ap.add_argument('--prompt', default=r'(/\s*#|CFE>)\s*$')
    ap.add_argument('cmd', nargs='*')
    args = ap.parse_args()

    if args.cmd:
        cmd = ' '.join(args.cmd)
    else:
        cmd = sys.stdin.read().strip()
    if not cmd:
        print("no command given", file=sys.stderr)
        sys.exit(1)

    prompt_re = re.compile(args.prompt, re.MULTILINE)
    sys.stdout.write(talk(args.port, args.baud, cmd, args.timeout, prompt_re))


if __name__ == '__main__':
    main()
