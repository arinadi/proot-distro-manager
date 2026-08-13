#!/usr/bin/env python3
"""PDM installer.

install.sh gets git, Python, and this repo onto the machine; everything else
happens here — Python libraries, Termux packages, the Debian container, and
the pdm launcher.

Runs unattended: it reports problems and keeps going rather than prompting,
because the usual entry point is `curl ... | bash`, where stdin is not a
terminal. Safe to re-run — every step skips work already done.
"""

import os
import shutil
import subprocess
import sys

try:
    from installer.const import (
        ADMIN_USER,
        CONTAINER_NAME,
        HOME_BIN,
        IMAGE_REF,
        IMAGE_REF_FALLBACK,
        LAUNCHER_SRC,
        PROOT_DIR,
        REPO_DIR,
    )
    from installer.preflight import (
        X11_APK_URL,
        blocking_failure,
        run_all_checks,
    )
    from installer.preflight import (
        check_x11_app as preflight_check_x11_app,
    )
    from installer.presets import find_preset
    from installer.presets import restore_preset as apply_preset
    from installer.system import ensure_home_bin_on_path, link_launcher
except ImportError:
    sys.exit(
        "install.py must be run from inside the repository.\n"
        "Use the bootstrapper instead:\n"
        "  curl -sL https://raw.githubusercontent.com/arinadi/proot-distro-manager/main/install.sh | bash"
    )

CYAN, GREEN, YELLOW, RED, DIM, NC = (
    "\033[0;36m", "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[2m", "\033[0m",
)

_failures: list[str] = []
_manual: list[str] = []


def say(msg: str) -> None:
    print(f"{CYAN}>>>{NC} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}  ok{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}  warning{NC} {msg}")


def fail(step: str, msg: str) -> None:
    print(f"{RED}  failed{NC} {msg}")
    _failures.append(step)


def run(cmd: str, timeout: int | None = None) -> int:
    """Run a shell command with its output going straight to the terminal."""
    try:
        return subprocess.run(cmd, shell=True, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"{RED}  timed out after {timeout}s{NC}")
        return 1


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# ── Steps ──────────────────────────────────────────────────


def preflight() -> bool:
    """Report environment state. Returns False only on a fatal problem."""
    say("Checking environment")
    checks = run_all_checks()
    width = max(len(c.name) for c in checks)
    for check in checks:
        mark = f"{GREEN}ok{NC}" if check.ok else f"{YELLOW}--{NC}"
        print(f"  {mark} {check.name.ljust(width)}  {DIM}{check.message}{NC}")

    blocker = blocking_failure(checks)
    if blocker:
        print(f"\n{RED}{blocker.name}: {blocker.message}{NC}")
        print("An internet connection is required to install.")
        return False
    return True


def install_libs() -> None:
    say("Installing Python libraries")
    req = os.path.join(REPO_DIR, "requirements.txt")
    target = f"-r {req}" if os.path.exists(req) else "textual"

    # Termux's Python is externally managed on newer releases, so the plain
    # install is tried first and the override only as a fallback.
    attempts = [
        f"{sys.executable} -m pip install {target} --quiet",
        f"{sys.executable} -m pip install {target} --quiet --break-system-packages",
        f"{sys.executable} -m pip install {target} --quiet --user",
    ]
    for cmd in attempts:
        if run(cmd) == 0:
            ok("textual installed")
            return
    fail("libs", "could not install textual")


def install_termux_packages() -> None:
    """Install every Termux-side dependency, in dependency order.

    x11-repo and tur-repo are not enabled by default, and the X11 and
    graphics packages live in them — so the repos must be installed and the
    package lists refreshed before anything from them can resolve.
    """
    if not have("pkg"):
        warn("not a Termux environment, skipping Termux packages")
        return

    say("Refreshing package lists")
    if run("pkg update -y") != 0:
        warn("pkg update reported an error, continuing")

    # 1. Repositories first — these come from the default repo.
    say("Enabling x11-repo and tur-repo")
    if run("pkg install -y x11-repo tur-repo") == 0:
        ok("repositories enabled")
    else:
        fail("repos", "could not enable x11-repo/tur-repo")

    # 2. Re-index so the newly enabled repos are usable.
    say("Re-indexing after enabling repositories")
    if run("pkg update -y") != 0:
        warn("pkg update reported an error, continuing")

    # 3. Everything else, grouped so a failure names the culprit.
    #    termux-wake-lock is part of core Termux, so nothing to install for it.
    groups = {
        "proot-distro": ["proot-distro"],
        "audio": ["pulseaudio"],
        "networking": ["netcat-openbsd"],
        "Termux:X11": ["termux-x11-nightly", "xorg-xrandr"],
        # virglrenderer-android is the one that matters: it works on most
        # devices. The zink pair is the faster fallback where it works,
        # reported to be Qualcomm only.
        "graphics": [
            "virglrenderer-android",
            "mesa-zink", "virglrenderer-mesa-zink", "vulkan-loader-android",
            "angle-android",
        ],
    }
    for label, packages in groups.items():
        say(f"Installing {label}")
        if run(f"pkg install -y {' '.join(packages)}") == 0:
            ok(label)
            continue

        # One unavailable name fails the whole line and takes every other
        # package in it down too — which is how a device ended up without
        # virglrenderer-android despite it being available. Retry one at a
        # time to salvage the rest.
        warn(f"{label} failed as a group, retrying one at a time")
        missing = [p for p in packages if run(f"pkg install -y {p}") != 0]
        if missing:
            fail(label, f"could not install: {', '.join(missing)}")
        else:
            ok(f"{label} (installed individually)")

    check_x11_app()


def check_x11_app() -> None:
    """The termux-x11-nightly package is only the Termux half.

    The desktop renders inside the com.termux.x11 Android app, which cannot
    be installed with pkg — it has to be sideloaded.
    """
    say("Checking Termux:X11 app")
    result = preflight_check_x11_app()
    if result.ok:
        ok("Termux:X11 app installed")
        return

    if result.unknown:
        warn(f"could not determine whether the app is installed ({result.message})")
        print(f"    If the desktop does not appear, install it from {X11_APK_URL}")
        return

    warn("Termux:X11 app is NOT installed — the desktop has nowhere to display")
    print(f"    Install the APK from {X11_APK_URL}")
    _manual.append(f"Install the Termux:X11 app: {X11_APK_URL}")


def container_exists() -> bool:
    return os.path.isdir(os.path.join(PROOT_DIR, "rootfs"))


def install_container() -> bool:
    """Returns True only when this call actually pulled the image — not
    when a container was already there. restore_preset() needs that
    distinction: applying a preset onto a container that already existed
    could silently overwrite months of the user's own changes with an old
    snapshot, rather than seeding a container that never had any."""
    say("Installing Debian container")
    if container_exists():
        ok("container already present, skipping")
        return False

    if not have("proot-distro"):
        fail("container", "proot-distro is not available")
        return False

    # GHCR first (no pull-rate limit for a public package), Docker Hub as
    # the fallback for ISPs where ghcr.io's CDN routes badly — see
    # installer/const.py for why it stays a fallback rather than the default.
    for attempt, ref in enumerate((IMAGE_REF, IMAGE_REF_FALLBACK)):
        if attempt > 0:
            print(f"{DIM}  {IMAGE_REF} did not work — trying {ref} instead.{NC}")
            run(f"proot-distro remove {CONTAINER_NAME}", timeout=60)
        print(f"{DIM}  Pulling {ref} — this takes a few minutes.{NC}")
        if run(f"proot-distro install {ref} --name {CONTAINER_NAME}", timeout=1800) == 0:
            break
    else:
        fail("container", "image pull failed from both registries")
        return False

    if not container_exists():
        fail("container", "image pulled but no rootfs was created")
        return False
    ok("container installed")
    return True


def setup_admin_user() -> None:
    """The image ships an admin user; this repairs a container that was built
    or restored without one."""
    if not container_exists():
        return

    say("Verifying admin user")
    setup = (
        f"id {ADMIN_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash {ADMIN_USER}; "
        f'echo "{ADMIN_USER}:{ADMIN_USER}" | chpasswd; '
        f'echo "{ADMIN_USER} ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/{ADMIN_USER}; '
        f"chmod 0440 /etc/sudoers.d/{ADMIN_USER}"
    )
    if run(f"proot-distro login {CONTAINER_NAME} -- bash -c '{setup}'", timeout=120) == 0:
        ok("admin user ready")
    else:
        fail("user", "could not configure the admin user")


def restore_preset(fresh: bool) -> None:
    """Applies presets/*.tar.gz (see presets/README.md) — but only onto a
    container this same run just pulled. `fresh=False` means the container
    was already there (install.py is safe to re-run, and usually is one),
    which could just as easily be a container the user has been living in
    for months; restoring a preset onto that would silently clobber it.

    Preset-only, on purpose: this is not where a user's own Backup/Restore
    belongs. That stays a manual, explicit action from the TUI's Backup
    screen — it is never something the bootstrap decides on its own."""
    if not fresh:
        return
    preset = find_preset()
    if preset is None:
        return

    say(f"Restoring preset ({preset.name})")
    if apply_preset(lambda msg: print(f"    {msg}")):
        ok("preset restored")
    else:
        warn("preset restore failed — see the output above")


def install_launcher() -> None:
    say("Installing launcher")
    linked, where = link_launcher()
    if not linked:
        fail("launcher", where)
        return

    ok(f"{where} -> {LAUNCHER_SRC}")

    # $PREFIX/bin is already on PATH; only the ~/bin fallback needs a shell
    # startup entry, and then pdm is available in the next session.
    if where.startswith(HOME_BIN):
        touched = ensure_home_bin_on_path()
        if touched:
            ok(f"added ~/bin to PATH in {', '.join(os.path.basename(t) for t in touched)}")
        _manual.append("Open a new terminal session so ~/bin is on PATH")


# ── Main ───────────────────────────────────────────────────


def main() -> int:
    print()
    print(f"{CYAN}===========================================")
    print("  PDM Installer")
    print(f"==========================================={NC}")
    print()

    if not preflight():
        return 1

    install_libs()
    install_termux_packages()
    fresh_container = install_container()
    setup_admin_user()
    restore_preset(fresh_container)
    install_launcher()

    print()
    if _failures:
        print(f"{YELLOW}Finished with problems in: {', '.join(sorted(set(_failures)))}{NC}")
        print("Fix those, then re-run the installer. It is safe to run again.")
        _print_manual()
        return 1

    print(f"{GREEN}Installation complete.{NC}")
    _print_manual()
    print()
    print("  Open a new terminal session, then run:  pdm")
    print()
    return 0


def _print_manual() -> None:
    """Steps the installer cannot perform — re-running will not clear these."""
    if not _manual:
        return
    print()
    print(f"{YELLOW}Still needs you:{NC}")
    for item in _manual:
        print(f"  - {item}")


if __name__ == "__main__":
    sys.exit(main())
