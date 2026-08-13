# Image Optimization — proot Dead Weight in the xfce4 Recommends Tree

`image/Dockerfile` is currently a deliberate **vanilla baseline**: recommends
enabled everywhere, nothing trimmed except what apt itself reports as
uninstallable here (`xorg-`, `initramfs-tools-`, `desktop-base-`,
`thunar-volman-`). The Dockerfile's own comment marks this as phase 1 —
"Size is a phase 2 concern." This doc is that phase 2 groundwork: what the
`xfce4` recommends tree actually pulls in, verified against real Debian 13
package metadata rather than guessed.

## Why this matters beyond size

proot has no cgroups, no namespaces, and no access to real hardware — no
USB, no removable storage, no NIC beyond whatever Termux's network
namespace already exposes. A large slice of XFCE's Recommends tree exists
specifically to manage things in that category (session daemons, disk
mounting, power, color-managed displays). None of it can do anything useful
here; most of it never even gets a working system D-Bus to register on.

One finding is more than dead weight: `xfce4-pulseaudio-plugin` recommends
`pulseaudio | pipewire-pulse`, which pulls a **second audio server into the
container**. The project's own design (`installer/const.py`,
[README#settings](../README.md#settings)) runs the PulseAudio *server* in
Termux and has the container talk to it as a client over one shared socket
(`$PREFIX/tmp/pulse-socket`). A stray in-container server is a real
correctness risk, not just bytes — the panel plugin or `pavucontrol` could
end up pointed at the wrong daemon entirely.

## Method

Reproduced with a plain Debian 13 container (no proot involved — this is
pure apt dependency resolution, identical whether it runs under proot or
not):

```sh
podman run --rm debian:13 bash -c '
  apt-get update -qq
  apt-get install -y --dry-run \
    xfwm4 xfce4-session xfce4-settings xfconf \
    xfce4 xfce4-terminal dbus-x11 \
    x11-xserver-utils libgl1-mesa-dri libegl1 libgles2 libglx-mesa0 mesa-utils \
    pulseaudio-utils firefox-esr \
    xorg- xserver-xorg- xserver-xorg-core- xserver-xorg-video-all- \
    xserver-xorg-input-all- xserver-xorg-legacy- initramfs-tools- \
    desktop-base- thunar-volman-
'
```

This is the exact package list `image/Dockerfile` installs today (three
`RUN apt-get install` layers combined). `--dry-run` resolves the full
dependency and recommends tree without installing anything.

## What gets pulled in that proot can't use

| Package(s) | Pulled by | Why it's dead weight under proot |
|---|---|---|
| `systemd`, `systemd-sysv`, `systemd-cryptsetup`, `systemd-timesyncd` | `xfce4-session` Recommends `systemd-sysv`; also transitively via `accountsservice` | proot can't run systemd as PID 1 (needs cgroups/namespaces proot doesn't provide) — this never becomes an init system, ever |
| `udisks2` | `thunar` Recommends | Manages real block devices via system D-Bus; no USB/SD card reaches the proot container — Termux/Android owns that layer |
| `upower` | `xfce4-session` Recommends | Battery daemon; Termux already exposes battery through its own APIs, there's no real ACPI/sysfs power supply inside the container |
| `colord`, `colord-data`, `xiccd` | `xfce4-settings` Recommends | Display color-calibration daemon and its ICC device connector; no color sensor exists to calibrate |
| `avahi-daemon` | `thunar` → `gvfs` chain | mDNS/zeroconf service discovery; an always-on network listener with no use case on a single-user phone desktop |
| `gvfs`, `gvfs-daemons`, `gvfs-common`, `gvfs-libs` | `thunar` Recommends | Virtual filesystem backends for MTP/USB/network shares; MTP and USB backends are meaningless with no USB access, and nothing in this project uses the network-share backends |
| `accountsservice` | `mate-polkit` Depends | User-account/session metadata daemon, normally paired with `logind` — nothing here calls it |

## The audio conflict specifically

| Package(s) | Pulled by | Risk |
|---|---|---|
| `pulseaudio` (full server) **or** `pipewire`, `pipewire-bin`, `pipewire-pulse`, `wireplumber`, `libwireplumber-*` | `xfce4-pulseaudio-plugin` Recommends `pulseaudio \| pipewire-pulse` | A second, unmanaged audio server inside the container, competing with the Termux-side server this project is built around |

Excluding `pulseaudio-` alone does **not** fix this — apt just satisfies the
same Recommends via the other half of the alternative (`pipewire-pulse`),
pulling in the entire PipeWire stack instead. Both halves of the
alternative have to be excluded together:

```
pulseaudio- pipewire-pulse-
```

## Verified-safe exclusion list

Appending these to the existing `pkgname-` exclusions (same syntax already
used in `image/Dockerfile` for `xorg-`, `initramfs-tools-`, etc.) removes
every package above without touching the core session:

```
systemd-sysv- systemd-timesyncd- udisks2- upower- colord- xiccd- \
gvfs- gvfs-daemons- avahi-daemon- pulseaudio- pipewire-pulse- accountsservice-
```

Result, same dry-run method: **617 → 510 packages** (~17% fewer). Sanity
check confirms the core session is untouched — `xfwm4`, `xfdesktop4`,
`xfce4-session`, and `xfce4-panel` all still resolve to Install. This is a
scalpel exclusion of specific transitive packages, not a broad
`--no-install-recommends` — the change that previously broke `xfwm4`/
`xfdesktop4` launch (see the Dockerfile's own "vanilla baseline" comment)
was disabling recommends wholesale, not excluding named packages like this.

## Deliberately not excluded

- **`polkitd`** — a hard `Depends` of `mate-polkit`, not a Recommends, so it
  can't be excluded without also dropping `mate-polkit` itself (kept on
  purpose for the privilege-elevation dialog, per the Dockerfile's own
  reasoning). It's lightweight and doesn't drag in the systemd/udisks2/
  avahi tail the packages above do.
- **`pavucontrol`** — a real, usable GUI mixer once `pulseaudio-utils`
  (the client) is talking to the shared Termux socket. Not dead weight,
  just optional; left as a size/scope call rather than a proot-fit one.
- **`tumbler`, `xdg-user-dirs`** — thumbnailer and `~/Desktop` /
  `~/Documents` creation. Both work fine with no real hardware and are
  visibly useful in Thunar.

## Re-running this audit

Debian package metadata changes across releases. Re-run the dry-run command
above (with or without the exclusion list) whenever `image/Dockerfile`'s
package set changes, to confirm the findings still hold before trusting
this table.
