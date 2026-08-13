<div align="center">
  <h1>📱 PDM — Proot-Distro Manager</h1>
  <p><strong>PD made Easy.</strong></p>
  <p>Install, break, reinstall — any Linux distro <a href="https://github.com/termux/proot-distro">proot-distro</a>
  can pull, on your phone, no root. Built for Linux enthusiasts who like
  distro-hopping and experimenting, not developers who just want one desktop
  that works and stays out of the way — that's
  <a href="https://github.com/arinadi/XLabs">XLabs</a>, PDM's sibling project.</p>
  <p>
    <a href="https://github.com/arinadi/proot-distro-manager/actions"><img src="https://img.shields.io/github/actions/workflow/status/arinadi/proot-distro-manager/checks.yml?label=checks"></a>
    <a href="https://github.com/arinadi/proot-distro-manager/blob/main/LICENSE"><img src="https://img.shields.io/github/license/arinadi/proot-distro-manager"></a>
    <a href="https://github.com/arinadi/proot-distro-manager/commits/main"><img src="https://img.shields.io/github/last-commit/arinadi/proot-distro-manager"></a>
    <a href="https://github.com/arinadi/proot-distro-manager/stargazers"><img src="https://img.shields.io/github/stars/arinadi/proot-distro-manager"></a>
  </p>

  ```bash
  curl -sL https://raw.githubusercontent.com/arinadi/proot-distro-manager/main/install.sh | bash
  ```

  <img src="docs/arinanox-screenshot.jpg" alt="PDM desktop" width="360" style="border-radius:12px;">
  <p>
    A community project — try a distro, hit something broken, <a href="https://github.com/arinadi/proot-distro-manager/issues">open an issue</a>.<br>
    <small>Termux&nbsp;&nbsp;·&nbsp;&nbsp;proot-distro&nbsp;&nbsp;·&nbsp;&nbsp;XFCE&nbsp;&nbsp;·&nbsp;&nbsp;X11&nbsp;&nbsp;·&nbsp;&nbsp;Textual</small>
  </p>
</div>

---

## ⚡ Why

Your phone is a pocket PC with 8GB+ RAM and an ARM64 CPU — it deserves a real desktop. PDM sets one up declaratively, in one step:

| Problem | PDM Solution |
|---------|----------------------|
| Chrome sleeps tabs | Firefox ESR desktop browser — stays alive |
| No glibc apps | Debian 13 proot — standard glibc |
| 30 min of apt + config | Pre-built OCI image, pulled in one step |
| Fiddly X11 + audio + dbus startup | One menu entry, with cleanup on stop |
| Teardown that leaves stale locks | One teardown path, verified before it reports success |
| Locked into one distro | **Manual Install** — any image proot-distro can pull |

**Can't do:** Docker, systemd services, native x86, real root (proot fakes it). See [Limitations](#️-limitations).

**Who this is for:** you like trying distros, you don't mind rough edges
on the ones PDM has verified less thoroughly (Arch, Fedora — see
[Manual Install](#manual-install)), and you'll actually tell us when
something breaks. If you just want a dev desktop that works and don't
care which distro it runs, [XLabs](https://github.com/arinadi/XLabs) is
built for exactly that instead — one recipe, made reliable, nothing to
configure.

---

## 🌱 Design

A from-scratch Python [Textual](https://github.com/Textualize/textual) TUI,
not shell scripts — idempotent, resumable,
[tested](tests/run_tests.py) on every push. **Doctor** diagnoses and fixes
named failure modes — DNS, timezone, the Electron sandbox, per-device
GPU/audio — instead of "reinstall and hope."

PDM builds and publishes no image of its own — it's a manager, not a
distributor. The default pull is [XLabs](https://github.com/arinadi/XLabs)'s
own maintained Debian 13 + XFCE4 build, the same as picking it explicitly
from **Manual Install**; anything else proot-distro can pull is one tap
away in that same screen. XLabs puts all its effort into that one fixed
recipe working reliably — PDM starts from that codebase and philosophy,
then opens the front door instead of building a second image to maintain
alongside it. Both GPLv3.

---

## 🚀 Quick Start

### Install (one-time)

```bash
curl -sL https://raw.githubusercontent.com/arinadi/proot-distro-manager/main/install.sh | bash
```

`install.sh` bootstraps git, Python and this repo, then `install.py` does the
rest — Termux packages, the default Debian container, the `pdm` launcher.
Safe to re-run; each step skips what's already done, so an interrupted
install just resumes.

**One manual step:** sideload the
[Termux:X11 app](https://github.com/termux/termux-x11/releases/tag/nightly) —
`pkg` can only install Termux packages, not Android apps. The installer and
Doctor both flag it if missing.

Then open a new terminal session:

### Daily Use

```bash
pdm                     # Launch the TUI
```

The menu is sized for a thumb:

| | | |
|---|---|---|
| **Start Desktop** | **Stop Desktop** | |
| **Manual Install** | | |
| Update | Store | Settings |
| Doctor | Backup | |
| Reset | Cache | |

Everything is tappable — Termux delivers touches as mouse events. Full
reference below in [The TUI, screen by screen](#-the-tui-screen-by-screen).

---

## 🏗️ How It Works

Two layers: the **core** (an image someone else built and publishes —
XLabs's by default, `ghcr.io/arinadi/xlabs:latest`) and your **user layer**
(whatever you install inside the container — survives restarts, wiped by
Reset). PDM doesn't build or host the core itself; XLabs's default is a
starting point, not the only option — see
[Manual Install](#manual-install) below for any other image.

Image pulls try GHCR first, falling back to Docker Hub — GHCR has no rate
limit, which matters on mobile data behind carrier NAT where Docker Hub's
anonymous-pull limit is shared with everyone else on the same IP.

Starting the desktop is a chain, torn down in reverse on stop:

```mermaid
flowchart LR
    A[Audio] --> B[GPU renderer] --> C[X11] --> D[Container session]
```

### Python TUI

Built with [Textual](https://github.com/Textualize/textual). Every action
runs in a background thread, so a slow image pull streams live without
freezing the interface. Every screen returns to the menu, and anything
destructive is gated behind a confirm step first:

```mermaid
stateDiagram-v2
    [*] --> MainScreen
    MainScreen --> ActionScreen: Start · Stop · Update · Doctor · Store...
    MainScreen --> ConfirmScreen: destructive actions
    ConfirmScreen --> ActionScreen: Confirm
    ConfirmScreen --> MainScreen: Cancel
    ActionScreen --> MainScreen: Back, once idle
```

Back is disabled while a job is running, so leaving mid-install can't strand
the container half-set-up. A watchdog kills the whole process tree if it hangs:

```mermaid
sequenceDiagram
    User->>Screen: choose an action
    Screen->>Worker: run in a thread (Back disabled)
    Worker->>Subprocess: stream output live
    Worker-->>Screen: done (Back enabled)
```

---

## 🖥️ The TUI, screen by screen

<!-- screenshot: main menu -->

Built for a thumb, not a mouse: full-width buttons, generous spacing, Back
always at the bottom. Button size follows Termux's terminal font size — if
buttons feel too small to tap reliably, raise the font size (pinch-to-zoom or
Termux's Style menu), not something PDM controls from the Python side.

### Start Desktop

1. **Wake lock** — keeps Android from freezing the session
2. **Audio** — PulseAudio, plus the socket the container will use
3. **GPU renderer** — first virgl/ANGLE backend found, else software
4. **X11** — `termux-x11 :0`, waits for the socket to actually accept
5. **Session** — `dbus-launch startxfce4` as `admin`

Always runs a full stop first — stale sockets and orphaned proot processes
are exactly what break the next start. If the desktop doesn't come up, a
full diagnostic report is collected automatically. A container installed
via **Manual Install** without a desktop environment has nothing for this
to start — that's expected, not a failure; use it as a shell instead.

### Stop Desktop

<!-- screenshot: stop -->

Innermost first: the container's proot tree, then X11, then audio, then a
sweep for anything left over. No polite XFCE logout — a fresh proot login
has no D-Bus session to ask for one, so it's `TERM` then `KILL` throughout,
and it **verifies with `pgrep`** rather than just claiming success.

### Manual Install

<!-- screenshot: manual install -->

Replaces whatever is currently installed with **any image proot-distro can
pull** — `ubuntu:24.04`, `alpine:latest`, `archlinux:latest`,
`ghcr.io/org/image:tag`, anything. The field itself takes whatever you
type; two rows of quick-picks fill it in for you:

- **★ Flagship — XLabs** (`ghcr.io/arinadi/xlabs:latest`): Debian Trixie,
  XFCE, by Arinano. The one maintained, ready-to-use build here — not a
  vanilla rootfs, a full desktop that boots straight to **Start Desktop**
  working, the same as PDM's default pull — it is the same image, not a copy.
- **Debian / Ubuntu / Alpine / Arch Linux / Fedora**: the vanilla upstream
  rootfs for each, with no desktop environment baked in — install one
  afterward from Store, or use the container as a plain shell.

This is the same container slot **Reset** uses, not a second, independent
one — Start, Doctor, Backup and everything else still only know about the
one container named in `installer/const.py`. Picking a different image
here doesn't add a container alongside the default; it replaces it, exactly
like Reset does, just pointed somewhere else. True multi-container support
is on the [roadmap](#-roadmap), not built yet.

XLabs's image ships an admin user, sudo and bash baked in at build time —
a vanilla pull has none of that. PDM provisions it instead, right after
any fresh pull: detects the container's package manager (apt / apk /
pacman / dnf), creates the admin user the distro-appropriate way
(`useradd`+`chpasswd` on the first three, BusyBox's `adduser`+`passwd` on
Alpine), and installs sudo and bash if either is missing. Idempotent — a
fast no-op on XLabs's own image, where all of it is already true. See
`installer/provision.py`.

Vanilla picks work with **Store** only once the image's package manager is
apt (Debian, Ubuntu) — Alpine's `apk`, Arch's `pacman` and Fedora's `dnf`
aren't wired up there yet; also on the roadmap. GPU (Mesa) and audio
(`pactl`) userspace, needed for Start Desktop to actually render and play
anything, install the same distro-aware way — Doctor reports and fixes it
under **GPU/Audio packages**.

### Update

`git pull --ff-only`, hard-resetting to `origin/main` if that's refused. A
**Restart** button relaunches `pdm` on the new code — pulling alone doesn't
update a process that's already running.

### Store

<!-- screenshot: store search -->

A package browser for the container. Search, see what's already installed
(**I**), tap **Install** — apt runs inside the container with output
streamed live, Termux untouched. First search is slower: the image ships
without package lists to stay small, so it fetches them once.

**Mirror** switches which Debian mirror apt uses — **Measure** times a real
download from each candidate and picks the fastest, **Refresh** pulls the
current list from Debian itself. Security updates are always kept pointed at
`security.debian.org` regardless of mirror, so switching never breaks
`apt update`.

**Repos** adds signed third-party sources:

| Repo | What it gives you |
|------|-------------------|
| `backports` | Newer packages from Debian itself |
| `mozilla` | Firefox tracking release rather than ESR |
| `vscode` | Visual Studio Code |

**Add** takes any repository — name, URI, key URL. A signing key is
required, and every field is validated before it's written.

Store is apt-specific today — a container installed via Manual Install onto
a non-Debian image (Alpine's `apk`, Arch's `pacman`, Fedora's `dnf`) won't
have a working Store yet. See the [roadmap](#-roadmap).

### Doctor

<!-- screenshot: doctor -->

One screen, full checkup: internet, Python, the container, storage, DNS,
timezone, GPU, audio, and more — each shown as ● present, ○ missing, or
**?** unknown.

- **Fix (N)** repairs everything repairable in one press
- **Package manager** — apt / apk / pacman / dnf, detected once and
  cached; everything distro-aware (provisioning, GPU/Audio packages)
  reads this instead of re-probing the container
- **GPU/Audio packages** — Mesa (`glxinfo`) and `pactl`, the userspace
  Start Desktop needs to actually render and play anything; present on
  XLabs's image already, installed distro-aware here otherwise
- **DNS** — repoints `resolv.conf` at public DNS when name resolution breaks
- **Timezone** — matches Android's zone (the image ships as UTC)
- **Electron apps** (VS Code, etc.) — patches launchers with `--no-sandbox`;
  their sandbox can't initialize under proot, so without this they don't
  open at all
- **Video** — turns off VP9/AV1 in Firefox so YouTube doesn't stutter
  (no hardware video decode under proot — everything falls back to CPU)
- **Audio** — benchmarks unix/tcp, with and without shared memory, keeps
  whichever actually plays
- **Bench** — runs glmark2 across GPU presets (virgl, ANGLE, zink,
  software) and keeps the fastest
- **Dupes** — tools installed in both Termux and the container; can remove
  the Termux copies, container treated as primary

Recording audio isn't possible — Termux doesn't hold the microphone
permission, so there's no source to capture from.

Most checks here are host-level (Termux/Android facts) and apply to any
container. A few — the Debian security-archive fix, the Electron sandbox
patch — assume Debian/apt and a desktop environment. On a container from
Manual Install, those specific checks quietly find nothing to check rather
than misreporting; see the [roadmap](#-roadmap) for making that pluggable
per distro instead of Debian-shaped by default.

### Settings

<!-- screenshot: settings -->

Per-device overrides, saved to `.env` — mostly for when auto-detection (GPU
Bench, Audio method) picks wrong, plus `termux-x11` rendering flags for
devices with a black screen or swapped colors. All take effect on next
restart. **Uninstall PDM** lives here too, behind the same confirm as
Reset.

### Backup

<!-- screenshot: backup -->

Archives `/home/admin` — dotfiles, browser profile, editor config, panel
layout — not apt packages, which reinstall themselves. Runs `tar` **inside**
the container rather than copying files from the host, since proot's
ownership and hardlink emulation don't survive a raw copy. Stored outside
the container under `~/pdm-backups`, so a **Reset** can't take a backup
down with it.

A backup dropped into [`presets/`](presets/) in this repo (instead of
`~/pdm-backups`) is your own — PDM doesn't ship one. Commit it, and
`install.py` restores it the first time it ever pulls the container on a
device — not on every run, and not from the TUI: see
[`presets/README.md`](presets/README.md) for why it's scoped that
narrowly.

### Reset and Cache

**Reset** deletes the container and pulls a fresh copy of PDM's default (XLabs's)
image. **Cache** drops downloaded image layers only. Both confirm first. To
reinstall with something other than the default, use **Manual Install**
instead of Reset.

### Copying anything

Every output screen has a **C** button / `c` key — tries the Android
clipboard, then the terminal's OSC 52, and always mirrors to a file too,
since neither clipboard path is guaranteed to work.

### Keys

| Key | Where |
|-----|-------|
| `q` | Quit, from the menu |
| `Escape` | Back — refused while an action is running |
| `c` | Copy the screen's output |
| `Enter` | Run the search, in Store |

Layout holds down to a 36-column terminal.

---

## 📦 What's Included

Whatever the installed image ships, plus anything added afterward from
[Store](#store) — PDM doesn't curate or trim package lists inside any
image, default or otherwise. XLabs's default build is a **vanilla
baseline**, documented in [its own README](https://github.com/arinadi/XLabs#-whats-included):
XFCE, Firefox ESR, Mesa userspace, and little else. That documentation
lives with the image that owns it, not duplicated here where it would
drift out of sync.

---

## 🎮 Graphics

No GPU vendor detection — the start sequence just tries renderers in order
and takes the first that exists: virgl, then ANGLE (Vulkan), then software.
This runs regardless of which image is installed; whether OpenGL actually
works depends on that image shipping Mesa userspace — XLabs's default
does, a vanilla distro pull from Manual Install may not until it's
installed via Store.

---

## 📂 Structure

```
proot-distro-manager/
├── install.sh          ← Bootstrap: git, Python, repo checkout
├── install.py          ← Full installer
├── pdm                 ← TUI launcher
├── installer/          ← TUI package
│   ├── app.py          ←   Textual app: screens, runners
│   ├── app.tcss        ←   Styling
│   ├── start.py        ←   Desktop lifecycle
│   ├── preflight.py    ←   Environment checks (pure stdlib)
│   ├── system.py       ←   Subprocess helpers, image pulls
│   └── const.py        ←   Paths and names
│   └── doctor.py       ←   Diagnosis and repair
│   ├── bench.py        ←   GPU benchmark and profile
│   ├── config.py       ←   .env, per-device settings
│   ├── backup.py       ←   Home directory backup/restore
│   ├── presets.py      ←   Restore a repo-tracked preset (see backup.py)
│   └── provision.py    ←   Distro-aware user/sudo/bash/GPU/audio setup
├── tests/              ← Headless TUI tests, run by CI
├── docker/dev/         ← Local TUI test harness
├── presets/            ← Your own backup, restored on fresh installs
└── docs/               ← Debugging notes and references
```

---

## 🔁 Termux and the container overlap

By design: proot-distro binds Termux's `$PREFIX` into the container and
appends it to the container's `PATH`, so Termux's binaries are reachable
inside as a fallback (the container's own copy always wins when both
exist). `--shared-tmp` does the same for `/tmp` — it's literally the same
directory on both sides.

`Doctor → Dupes` finds tools installed on both and can remove the Termux
copies, on the assumption the container is where you work. It never
touches anything PDM itself needs.

---

## ⚠️ Limitations

| Limitation | Workaround |
|-----------|------------|
| No root | proot provides root-like environment |
| No systemd | Start services manually |
| No GPU passthrough | virgl renderer, software fallback |
| ARM64 only | QEMU for cross-arch (slow) |
| No native X11 | Termux:X11 app required |
| No Docker or Podman | See [Containers](#-containers-docker-podman) |
| Store/Doctor assume Debian | See [Roadmap](#-roadmap) |
| One container at a time | See [Roadmap](#-roadmap) |

---

## 🐳 Containers: Docker, Podman

Neither runs — it's an Android kernel limitation (no user namespaces or
cgroups for regular apps), not something proot or PDM can patch around.
Plain rootless Podman hits the same wall even without proot involved at
all, so this isn't a PDM gap specifically.

Rooting the phone and flashing a custom kernel removes the wall. So does a
different approach entirely, like [Podroid](https://github.com/aanundgit/podroid),
which runs a full VM instead of proot — heavier, and out of scope here.

**For a normal dev toolchain** — a language runtime, a database, a build
tool — [Store](#store) installs it straight into the container (on
Debian-based images today). No container-inside-container needed for that.

---

## 🗺️ Roadmap

PDM starts from XLabs's single-container, single-distro codebase and opens
one door — Manual Install. The rest of what "flexible" could mean here is
still ahead, roughly in the order it'd actually get built:

- **Multi-container management.** Today, "the container" is one name
  (`installer/const.py`) that Start, Stop, Doctor, Backup, Store and Reset
  all assume. A container **list** instead — name, distro, desktop,
  created date, size — with Start/Stop/Reset/Cache becoming per-container
  actions from a picker, plus an overview screen and the ability to clone
  a container before doing something risky to it.
- **Pluggable Store.** `installer/provision.py` already has a
  package-manager abstraction (apt / apk / pacman / dnf) — it just only
  does provisioning (admin user, sudo, bash) and GPU/Audio packages so
  far. Extending the same table to Store's search/install/mirror logic
  is the rest of this item, not a second abstraction to design.
- **Pluggable Doctor.** Host-level checks (DNS, timezone, stale sockets,
  storage, Termux:X11) stay shared across every container. GPU/Audio
  packages is the first distro-aware fix; the rest — Debian's
  security-archive bug, Alpine's musl-vs-glibc proot quirks, Arch's
  pacman keyring init, Fedora's dnf slowness on mobile data — become
  named fixes the same way, matching the existing philosophy of
  diagnosing specific failure modes instead of "just reinstall."
- **Desktop environment choice**, not just distro choice: XFCE (today's
  recipe), LXQt/MATE as lighter alternatives, or headless/CLI-only for a
  pure dev shell with no X11 at all.
- **Curated recipes** — one-tap combos ("Debian + XFCE dev", "Alpine + CLI
  minimal") as a *vanilla image + a scripted setup step* (pull, then apt/apk
  install a package set through Store), not a new prebuilt image PDM
  builds and hosts itself — same reasoning as [Design](#-design): compose
  what already exists rather than maintain a second copy of it.
- **Per-container Backup/Restore/Presets** — the username and home path
  currently assume `admin`/Debian; this travels with each container's own
  metadata once multi-container support exists, and presets become a
  small manifest (distro + DE + packages + dotfiles) rather than just a
  raw tar.

None of this is committed to a timeline — it's the shape the project is
aimed at, kept here so a contribution knows where it fits rather than
guessing.

---

## 🗣️ Feedback Wanted

PDM only gets more distros right with more people actually trying them.
Two things are specifically worth an issue, not just a shrug:

- **A distro that doesn't provision right.** `installer/provision.py`'s
  apt and apk profiles are verified against real containers; pacman and
  dnf are best-effort from each distro's documented conventions, not
  tested here — if one fails, Doctor's **C** (copy) button on the
  failed step is the single most useful thing to attach.
- **A distro you want as a [Manual Install](#manual-install) quick-pick**
  that isn't there yet. The list is five deep on purpose, not a ceiling.

[Open an issue](https://github.com/arinadi/proot-distro-manager/issues) —
that's the whole process. No template, no ceremony.

---

## 🛑 Android 12+ Phantom Process Killer

Background processes can get killed by Android. Disable it:

- **Android 14+:** Developer Options → Disable child process restrictions
- **Android 12–13:** `adb shell settings put global settings_enable_monitor_phantom_procs false`

---

## 📜 License

GPLv3 — see [LICENSE](LICENSE). Forked from
[XLabs](https://github.com/arinadi/XLabs), which itself credits DroidDesk
for the original idea and starting point.
