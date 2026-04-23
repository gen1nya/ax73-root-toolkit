# Persistent SSH via Alpine dropbear

TP-Link's stock dropbear (2019.78) hangs after `SSH2_MSG_SERVICE_ACCEPT` —
see [05-dropbear-postmortem.md](05-dropbear-postmortem.md). A fresh Alpine
Linux build of the same dropbear family works on the first try, pubkey auth
and all. This doc documents the setup once you have a persistent shell
(UART or telnet:23) and persistent `/etc`+`/root` via the bind-mount hook.

## Bundle

Three files, ~1 MB total, sourced from Alpine 3.20 armhf:

| file | apk package | what it does |
|---|---|---|
| `dropbear` | `dropbear-2024.85-r0.apk` | the server binary, PIE, dynamically linked to musl |
| `dropbearkey` | same apk | host-key generator |
| `ld-musl-armhf.so.1` | `musl-1.2.5-r3.apk` | musl libc + dynamic linker |

The binaries are linked against musl (`interp /lib/ld-musl-armhf.so.1`). The
router runs glibc. We don't touch `/lib/` — instead we invoke the musl
loader **by explicit path**, which makes it load itself (it's both linker
and libc) and run the given binary without needing anything in `/lib`.

Download and extract:

```bash
mkdir -p /tmp/bundle && cd /tmp/bundle
curl -sL -o dropbear.apk https://dl-cdn.alpinelinux.org/alpine/v3.20/main/armhf/dropbear-2024.85-r0.apk
curl -sL -o musl.apk     https://dl-cdn.alpinelinux.org/alpine/v3.20/main/armhf/musl-1.2.5-r3.apk
mkdir db musl
tar xzf dropbear.apk -C db/ 2>/dev/null
tar xzf musl.apk     -C musl/ 2>/dev/null
cp db/usr/sbin/dropbear db/usr/bin/dropbearkey musl/lib/ld-musl-armhf.so.1 .
chmod +x *
```

## Install on router

Put the three files in `/root/bin/` (persistent thanks to the UBIFS
bind-mount), generate host keys in `/root/dropbear-keys/`, and add a line
to `/etc/inittab`.

```sh
# from the router shell (UART or telnet):

mkdir -p /root/bin /root/dropbear-keys
# copy the three files here — via USB, or over UART-base64, or netcat from
# the laptop. Then chmod +x them.

/root/bin/ld-musl-armhf.so.1 /root/bin/dropbearkey -t ed25519 -f /root/dropbear-keys/ed25519
/root/bin/ld-musl-armhf.so.1 /root/bin/dropbearkey -t rsa -s 2048 -f /root/dropbear-keys/rsa

# your SSH pubkey — payload/root/.ssh/authorized_keys already seeded this
# during the squashfs build; verify:
cat /root/.ssh/authorized_keys
chmod 700 /root /root/.ssh
chmod 600 /root/.ssh/authorized_keys

# respawn via init
cat >> /etc/inittab <<'EOF'
::respawn:/root/bin/ld-musl-armhf.so.1 /root/bin/dropbear -F -E -p 2222 -r /root/dropbear-keys/ed25519 -r /root/dropbear-keys/rsa
EOF

# reload init
kill -HUP 1

# check
ss -tln | grep 2222    # or: netstat -tln | grep 2222
ps | grep 'root/bin/dropbear' | grep -v grep
```

That's it. SSH stays up across reboots because init re-reads inittab, sees
the `::respawn:` line, starts our daemon.

## Why port 2222 and not 22

Port 22 is occupied — `/etc/init.d/dropbear` launches the stock TP-Link
dropbear there on a non-Singapore firmware. Our replacement on 2222
coexists peacefully. If you want 22 instead, either:

- Disable the stock init script: `rm /etc/rc.d/S50dropbear` (ok because
  `/etc` is persistent now)
- Or change our `-p 2222` to `-p 22` and kill the stock one: `killall
  /usr/sbin/dropbear` (the stock, not ours — match by full binary path)

## Connect

```bash
ssh -i ~/.ssh/id_ed25519 -p 2222 root@<router-lan-ip>
```

Host-key algorithm negotiation works out of the box with modern OpenSSH;
no `-oHostKeyAlgorithms=+ssh-rsa` needed because our build has ed25519
host keys which OpenSSH clients prefer.

## Rotate host keys

```sh
/root/bin/ld-musl-armhf.so.1 /root/bin/dropbearkey -t ed25519 \
    -f /root/dropbear-keys/ed25519-new
# swap atomically:
mv /root/dropbear-keys/ed25519-new /root/dropbear-keys/ed25519
# respawned dropbear on next connection uses the new key
kill $(pidof dropbear)     # init respawns with new key
```

## Uninstall

Remove the `::respawn:` line from `/etc/inittab`, `kill -HUP 1`, then
`rm -rf /root/bin /root/dropbear-keys`.

## Space

Alpine musl + dropbear + dropbearkey ≈ 1 MB. Plus ≈ 1 KB of host keys. Out
of the 5 MB free in our `ubi_rootfs_data` UBIFS volume, this is nothing.
`strace` (another 350 KB static armv7 binary that's very handy for debugging
and lives happily in `/root/bin/`) is bundled alongside in the working setup.
