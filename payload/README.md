# payload/

Files in this tree are overlaid on top of the unsquashfs'd stock rootfs by
`scripts/build-and-flash.sh` before the squashfs is rebuilt.

## What ships

- [`etc/inittab`](etc/inittab) — UART `ash --login` + persistent `telnetd :23`.
- [`etc/shadow`](etc/shadow) — `root::` (empty password), so UART login and
  telnet accept an immediate return.
- [`lib/preinit/20_check_jffs2_ready`](lib/preinit/20_check_jffs2_ready) —
  restores the commented-out OpenWrt overlay check. Lets you create a UBI
  volume `ubi_rootfs_data` later for a proper writable rootfs overlay without
  having to re-flash.
- [`root/.ssh/authorized_keys`](root/.ssh/authorized_keys) — placeholder.
  **Replace with your own pubkey before building.**

## What you need to add yourself

The two files below are gitignored (per-device secrets). Create them before
running `scripts/build-and-flash.sh`:

| File | How to generate |
|---|---|
| `root/.ssh/authorized_keys` | `cp ~/.ssh/id_ed25519.pub payload/root/.ssh/authorized_keys`<br>(see `authorized_keys.example` as a template) |
| `etc/dropbear/dropbear_rsa_host_key` *(optional)* | `dropbearkey -t rsa -f payload/etc/dropbear/dropbear_rsa_host_key -s 2048` — pre-generates the host key that the stock init script fails to create at runtime. Only matters if you eventually get dropbear working (see `docs/05-dropbear-postmortem.md`). |

The `authorized_keys` and `dropbear_rsa_host_key` files are in `.gitignore` —
they're per-device secrets, not something to commit.

## Adding more patches

Drop any file under `payload/` and it will land at the same path inside the
rootfs. Examples of useful additions:

- `payload/etc/rc.local` — commands to run after full boot (TP-Link's stock
  `/etc/rc.local` mostly starts `tddp`; you can replace it or append).
- `payload/etc/profile.d/custom.sh` — add aliases / env vars for your shell.
- `payload/usr/sbin/dropbear` — your cross-compiled modern build that replaces
  the TP-Link one that hangs.
- `payload/root/bin/…` — your own scripts; `/root/bin` isn't in `$PATH` by
  default, so either add it via `etc/profile.d` or invoke by full path.

## Perms

`scripts/build-and-flash.sh` chowns the whole payload tree to root:root before
repacking and applies tight perms to the sensitive files:

- `/etc/shadow` → `600`
- `/root/.ssh/` → `700`, `authorized_keys` → `600`
- `/etc/dropbear/` → `700`, `dropbear_rsa_host_key` → `600`

If you add files that need specific perms, either set them on your working
copy before running the build, or edit the build script.
