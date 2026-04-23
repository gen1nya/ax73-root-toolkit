# Hardware

## Bill of materials

| | |
|---|---|
| Target | TP-Link Archer AX73 v1.0 |
| UART adapter | USB-TTL **at 3.3 V logic level**, or any ESP32 devboard (see "ESP32 trick") |
| USB stick | FAT32, ≥ 64 MB free |
| Jumper wires | Dupont, optionally with hook probes for the UART header |
| Level meter | Multimeter for UART probing; oscilloscope nice to have |
| Host | Linux PC with `python-pyserial`, `mtd-utils`, `squashfs-tools` |

Do not connect a 5 V UART adapter (Arduino-style). The SoC is 3.3 V only. Pick a CP2102 / FT232RL / CH340 explicitly in 3.3 V mode.

## Disassembly

Four Phillips screws under the feet / rubber cover on the bottom. Top shell snaps off. Heatsink is not glued.

## Board inventory

- Broadcom **BCM6750_A2** — ARMv7, 3 cores, 1.5 GHz, under the main heatsink. Integrated 2.4 GHz Wi-Fi.
- Broadcom **BCM43684** — 5 GHz Wi-Fi 6 chip under a separate heatsink.
- **ESMT F50L1G41LB** — 128 MB SPI-NAND, WSON-8 package (looks like a small MOSFET with pads on all four sides — easy to mistake for something else).
- **ESMT M15T4G16256A** — 512 MB DDR3, large BGA.
- Skyworks PAs on Wi-Fi chains.

## UART pinout

Four-pin header next to the SoC, normally unpopulated. Looking at the PCB with the router right-side up, the pins go:

```
[VCC 3.3V]  [GND]  [RX]  [TX]      ← pin 1 (TX) is on the RIGHT (marked)
```

Pin 1 has a square / marked pad on the silkscreen; on this board it's the **rightmost** pin.

**TP-Link is inconsistent across revisions.** Before soldering, verify on your specific board:

1. Router unplugged. Continuity-test each of the 4 pins against any ground (USB shell, metal case, RJ-45 shield). The one that beeps is `GND`.
2. Router plugged in. With multimeter's black on `GND`, red on the remaining pins:
   - Steady **3.3 V** = `VCC`. Do not connect your adapter here.
   - **Idles at 3.3 V but dips during boot** (or shows visible digital activity on a scope) = `TX` (router → host).
   - Idles at 3.3 V, flat during boot = `RX` (host → router).

## Wiring

Minimum (read-only) — two wires:

```
router GND  → adapter GND
router TX   → adapter RX
```

For interactive input (CFE commands, typing in the shell) — three wires:

```
router GND  → adapter GND
router TX   → adapter RX
router RX   → adapter TX
```

Do not connect `VCC`.

## UART parameters

```
115200 baud, 8 data bits, no parity, 1 stop bit, no flow control
```

No tricks, the Broadcom BootROM prints its banner at 115200.

## ESP32 trick (no USB-TTL adapter)

Any ESP32 devboard with an onboard USB-UART bridge (CP2102 / CH340) can stand in for a dedicated adapter without de-soldering anything:

1. Tie the ESP32's `EN` (a.k.a. `CHIP_PU`, `RST`) pin to `GND`. This holds the ESP32 in reset; its `GPIO1` / `GPIO3` become hi-Z. The onboard USB-UART chip is still alive and still wired to those two pins via the PCB.
2. From the host side, `/dev/ttyUSB0` (or `/dev/ttyACM0`) still opens the CP2102/CH340 the way it normally does.
3. Wire the ESP32 pin header:

   ```
   ESP32 GND                  → router GND
   ESP32 GPIO1  (TX0)         → router TX     (read from router)
   ESP32 GPIO3  (RX0)         → router RX     (optional, for input)
   ```

   The reasoning: CP2102's TXD output is internally wired to ESP32 GPIO3 (RX0). CP2102's RXD input is wired to ESP32 GPIO1 (TX0). With the ESP32 held in reset, those pads just pass through to the CP2102 on the board.

4. Don't power the router from the ESP32 — the 3.3 V rails aren't tied, and the router is already powered from its own brick. Ground is the only shared reference.

## Partition layout (`/proc/mtd` on stock)

```
mtd0: 03280000  00020000  "rootfs"          — UBI: active rootfs_squashfs
mtd1: 03280000  00020000  "rootfs_update"   — UBI: backup bank rootfs
mtd2: 00800000  00020000  "data"            — UBI: /data, writable persistent, ~8 MB
mtd3: 00100000  00020000  "nvram"           — CFE nvram (do NOT touch)
mtd4: 03700000  00020000  "image_update"    — backup bank kernel + rootfs combined view
mtd5: 03700000  00020000  "image"           — active bank kernel + rootfs combined view
mtd6: 00460000  00020000  "bootfs"          — JFFS2 with cferam/vmlinux (active)
mtd7: 00460000  00020000  "bootfs_update"   — JFFS2 with backup kernel
mtd8: 00800000  00020000  "misc2"           — UBI: tp_data (user-config, writable), ~8 MB
mtd9: 01f51000  0001f000  "rootfs_squashfs" — squashfs inside mtd0's UBI (read-only view)
```

We only ever write to `mtd0`. The backup bank (`mtd1`, `mtd7`) is our recovery net and must remain untouched.

## Fun details

- The `misc2` / `tp_data` UBIFS mounted at `/tp_data/` contains `user-config`, `router-config`, `ap-config` — each an AES-encrypted XML blob written by `/usr/bin/tddp`. Reversing `tddp` to get the AES key would be a second path to persistence (you could flip a `<RemoteSSH>` flag in the config without touching flash at all) but we haven't gone that route.
- CFE is Broadcom's CFE v1.0.38-163.243 for BCM963178. On this SKU it doesn't prompt for a password at the serial console. A 1-second autoboot window (`*** Press any key to stop auto run (1 seconds) ***`) is your chance to break in.
- There's a **`/sbin/knock_functions.sh`** that manages an iptables allow-list for port **20001** (TP-Link's cloud/debug channel). It's gated by `if [ "$country" != "SG" ]; then return; fi`, so on non-Singapore firmware it's a no-op — but dropbear gets started on port 20001 regardless. That's why `ss :22` is empty on stock.
