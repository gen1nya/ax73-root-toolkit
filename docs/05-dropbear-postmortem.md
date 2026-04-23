# Dropbear post-mortem (open question)

Telnet works. Dropbear — the obvious thing to ship as the persistent remote shell — doesn't. We never figured out why.

## Observed behaviour

With a clean `dropbear 2019.78` (the binary shipped by TP-Link, matches md5 against the stock squashfs) bound to `0.0.0.0:22`, a host-side SSH client reliably reaches:

```
debug2: service_accept: ssh-userauth
debug1: SSH2_MSG_SERVICE_ACCEPT received
debug3: send packet: type 50     ← SSH_MSG_USERAUTH_REQUEST (method=none)
Connection closed by 192.168.129.4 port 22
```

Dropbear's own log (`-F -E 2>/tmp/dropbear.log`):

```
[11830] Apr 23 22:58:54 Child connection from 192.168.129.11:42094
[11830] Apr 23 22:59:04 Exit before auth: Exited normally
```

The "10 seconds" between connect and exit is the host-side SSH client timeout — dropbear didn't close the connection, the client did after waiting for a reply to the userauth request that never came.

`/proc/<dropbear-child-pid>/wchan` reads `poll_schedule_timeout`. Child has three fds of interest:

```
fd 5 -> socket:[...]    (the SSH connection)
fd 3 -> pipe:[...]      (read end, authhelper IPC)
fd 4 -> pipe:[...]      (write end, same)
fd 7 -> pipe:[...]      (write end, another pipe)
```

So it's blocked in `poll()` waiting for any of those. Client sent userauth request → socket has data → poll should wake. It doesn't.

## Things we eliminated

| Hypothesis | Test | Result |
|---|---|---|
| KEX/cipher negotiation mismatch vs modern OpenSSH | Forced `aes128-ctr` / `hmac-sha1` | Dropbear logs `No matching algo enc c->s` — fair enough, but the earlier tries with `aes256-ctr + hmac-sha2-256` (which **do** match) still hang |
| Strict home-directory perms rejecting pubkey auth | Bind-mount `/tmp/root-home` (0700) over `/root`, copy `authorized_keys` there with 0600 | Still hangs. And the hang is **before** auth method selection anyway |
| NSS/libc `getpwnam` failing | `/etc/nsswitch.conf` is stock (`hosts: files dns`); `getent passwd root` works from the shell | Not it |
| `/etc/shells` missing `/bin/ash` | Stock has `/bin/ash` listed | Not it |
| Dropbear binary corrupted by our flash | `md5sum /usr/sbin/dropbear` matches stock exactly | Not it |
| Protocol asymmetry with modern OpenSSH | `dbclient → dropbear` on `127.0.0.1` (same binary, no network) | **Hangs identically**. The host-side SSH client is not the problem |
| `/dev/pts` not writable | Remount `devpts` with `mode=620,ptmxmode=666,gid=5` | No change; `mount -t devpts devpts /dev/pts -o mode=620,ptmxmode=666,gid=5` even returns `bogus options` on this kernel |

Last test — self-connect via the router's own `dbclient` — is the important one. Local loopback, no network, same build. It hangs the same way. So whatever is wrong lives inside the dropbear binary (or the environment it's running in), not in the network path or the SSH client.

## Educated guesses we didn't verify

1. **TP-Link's dropbear was patched to depend on an external IPC**. Their `/etc/init.d/dropbear` is otherwise vanilla OpenWrt, but the binary is ~211 KB — larger than a minimal OpenWrt 2019.78 build. It might be waiting on a socket to `ceventd` (`/tmp/ce0.log` in user_nvram mentions it) or to a TP-Link daemon for authorization/session approval.
2. **Compile-time `DROPBEAR_FORK_AUTHHELPER` or similar**. The child has extra pipes `fd 7` that vanilla dropbear wouldn't have. That suggests fork-to-authhelper, and if the authhelper never forks or never replies, the child waits forever.
3. **/dev/urandom blocking**. BCM6750 has an RNG but we didn't verify it's feeding urandom. If dropbear calls `getrandom()` at auth time with `GRND_RANDOM` and the kernel entropy pool is empty, it'd block.

The cleanest next step would be `strace` on a child — but busybox here doesn't ship strace, and we didn't bother cross-compiling one.

## What we shipped instead

`telnetd -F -l /bin/ash -p 23`, spawned by init via `::respawn:` in the patched inittab. Works first try, survives reboot, gives unrestricted root. Adequate for a home AP behind NAT.

If you do need SSH:

- Cross-compile a current dropbear for `armv7l` and drop the binary into `payload/usr/sbin/dropbear` in this repo. The patched squashfs will ship your build. Replace the stock init script too or invoke your binary from inittab.
- Or: reverse-engineer the stock dropbear binary. Ghidra + bdiff against the upstream 2019.78 source should reveal whichever code path is blocking. If you do, open an issue / PR; we'd love to know.

## A cheap workaround

Since telnet works and we're behind NAT anyway, one option is to just tunnel it:

```bash
# on your host, somewhere stable:
ssh -L 2323:192.168.129.4:23 user@your-public-gateway

# then in another terminal
telnet localhost 2323
```

That gives you encrypted transport over an SSH tunnel. Not pretty, but sufficient.
