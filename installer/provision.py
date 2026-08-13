"""What XLabs's Dockerfile used to bake in at build time — an admin user,
sudo, bash, the GL/audio userspace the desktop needs — done at install
time by PDM instead, because PDM pulls whatever image Manual Install
points it at, not only one it controls the build of.

A vanilla distro pull has none of that. This is the one place the gap
gets closed, distro-aware, right after any fresh pull — the same step
whether the pull was XLabs's own image (where everything below is
already true, and every step here is a fast no-op) or something Manual
Install chose instead.

Verified against Debian/Ubuntu (apt) and Alpine (apk) base images, which
this project can actually run. Arch (pacman) and Fedora (dnf) profiles
follow each distro's own documented conventions but have not been run
against a live container here — if one of those turns out wrong, Doctor
reports the failure with output attached rather than silently miscounting
it as done.
"""

from __future__ import annotations

import os
from typing import Callable, NamedTuple

from . import config
from .const import ADMIN_USER, CONTAINER_NAME
from .system import container_path, is_installed, run_cmd, stream_cmd, write_container_script

Log = Callable[[str], None]

PKG_MANAGER_KEY = "PDM_PKG_MANAGER"
PROVISION_SCRIPT_NAME = "pdm-provision.sh"


class PkgManager(NamedTuple):
    name: str
    detect_cmd: str
    update_cmd: str
    install_cmd: str  # "{pkgs}" placeholder
    bash_pkg: str
    sudo_pkg: str
    mesa_pkgs: str
    audio_pkgs: str
    # "{user}" placeholder. Creates the user, its home, and a login shell —
    # not the password, which set_password_cmd handles separately, since
    # useradd/adduser set it differently enough that folding it in here
    # would need the same branching this table exists to avoid.
    create_user_cmd: str
    set_password_cmd: str


# useradd+chpasswd (shadow-utils) is the same across apt/pacman/dnf — all
# three ship it as part of their base system, not something apt/pacman/dnf
# itself needs to install. Alpine's BusyBox provides adduser/passwd
# instead, with different flags and no chpasswd equivalent by default.
_SHADOW_UTILS_USER = "useradd -m -s /bin/bash {user}"
_SHADOW_UTILS_PASSWORD = "echo '{user}:{user}' | chpasswd"

PKG_MANAGERS: tuple[PkgManager, ...] = (
    PkgManager(
        name="apt",
        detect_cmd="command -v apt-get",
        update_cmd="apt-get update -qq",
        install_cmd="DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}",
        bash_pkg="bash",
        sudo_pkg="sudo",
        mesa_pkgs="libgl1-mesa-dri libegl1 libgles2 libglx-mesa0 mesa-utils",
        audio_pkgs="pulseaudio-utils",
        create_user_cmd=_SHADOW_UTILS_USER,
        set_password_cmd=_SHADOW_UTILS_PASSWORD,
    ),
    PkgManager(
        name="apk",
        detect_cmd="command -v apk",
        update_cmd="apk update -q",
        install_cmd="apk add --no-cache {pkgs}",
        bash_pkg="bash",
        sudo_pkg="sudo",
        mesa_pkgs="mesa-gl mesa-dri-gallium mesa-utils",
        audio_pkgs="pulseaudio-utils",
        # -D: no password prompt, set with `passwd` right after instead.
        create_user_cmd="adduser -D -s /bin/bash {user}",
        set_password_cmd="printf '{user}\\n{user}\\n' | passwd {user}",
    ),
    PkgManager(
        name="pacman",
        detect_cmd="command -v pacman",
        update_cmd="pacman -Sy --noconfirm",
        install_cmd="pacman -S --noconfirm --needed {pkgs}",
        bash_pkg="bash",
        sudo_pkg="sudo",
        mesa_pkgs="mesa mesa-utils",
        audio_pkgs="libpulse",
        create_user_cmd=_SHADOW_UTILS_USER,
        set_password_cmd=_SHADOW_UTILS_PASSWORD,
    ),
    PkgManager(
        name="dnf",
        detect_cmd="command -v dnf",
        update_cmd="dnf makecache -q",
        install_cmd="dnf install -y {pkgs}",
        bash_pkg="bash",
        sudo_pkg="sudo",
        mesa_pkgs="mesa-dri-drivers mesa-libGL mesa-libEGL glx-utils",
        audio_pkgs="pulseaudio-utils",
        create_user_cmd=_SHADOW_UTILS_USER,
        set_password_cmd=_SHADOW_UTILS_PASSWORD,
    ),
)


def pkg_manager_by_name(name: str) -> PkgManager | None:
    for mgr in PKG_MANAGERS:
        if mgr.name == name:
            return mgr
    return None


def forget_package_manager() -> None:
    """A fresh pull invalidates whatever was detected for the container
    that just got removed — call before Reset/Manual Install's own pull,
    since a Manual Install can change the distro entirely and even a
    same-image Reset is worth re-checking rather than trusting a value
    written for a container that no longer exists."""
    config.unset(PKG_MANAGER_KEY)


def detect_package_manager(log: Log) -> PkgManager | None:
    """Cached in .env after the first successful detection — Doctor
    re-scanning, or a second provision, reads that instead of probing the
    container over again."""
    cached = config.get(PKG_MANAGER_KEY)
    if cached:
        mgr = pkg_manager_by_name(cached)
        if mgr is not None:
            return mgr

    if not is_installed():
        return None

    for mgr in PKG_MANAGERS:
        cmd = f'proot-distro login {CONTAINER_NAME} --shared-tmp -- sh -c "{mgr.detect_cmd}"'
        rc, _out = run_cmd(cmd, timeout=30)
        if rc == 0:
            config.set_value(PKG_MANAGER_KEY, mgr.name)
            return mgr

    log("[yellow]No known package manager (apt/apk/pacman/dnf) found in the container.[/yellow]")
    return None


def _build_script(mgr: PkgManager) -> str:
    """POSIX sh, not bash — bash itself may not exist yet, which is one of
    the things this script is here to fix. Every step checks before
    acting, so re-running this against an already-provisioned container
    (XLabs's own image, or a second Fix) does nothing on top of what's
    already true."""
    create_user = mgr.create_user_cmd.format(user=ADMIN_USER)
    set_password = mgr.set_password_cmd.format(user=ADMIN_USER)
    install_sudo = mgr.install_cmd.format(pkgs=mgr.sudo_pkg)
    install_bash = mgr.install_cmd.format(pkgs=mgr.bash_pkg)

    return f"""#!/bin/sh
set -e

if ! id {ADMIN_USER} >/dev/null 2>&1; then
    echo "Creating {ADMIN_USER}..."
    {create_user}
    {set_password}
fi

if ! command -v sudo >/dev/null 2>&1; then
    echo "Installing sudo..."
    {mgr.update_cmd}
    {install_sudo}
fi
echo "{ADMIN_USER} ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/{ADMIN_USER}
chmod 0440 /etc/sudoers.d/{ADMIN_USER}

if ! command -v bash >/dev/null 2>&1; then
    echo "Installing bash..."
    {mgr.update_cmd}
    {install_bash}
fi
"""


def provision_container(log: Log) -> bool:
    """Admin user, sudo, bash — the minimum every other screen in this
    app assumes exists. Call after any fresh pull, default or Manual
    Install; safe (and fast) to call again later too."""
    mgr = detect_package_manager(log)
    if mgr is None:
        log("[red]Could not provision: no supported package manager found.[/red]")
        return False

    log(f"  package manager: {mgr.name}")

    if not write_container_script(PROVISION_SCRIPT_NAME, _build_script(mgr)):
        log("[red]Could not write the provisioning script.[/red]")
        return False

    # Root, not --user admin: the user may not exist yet, which is exactly
    # what this script is here to fix. sh, not the usual container_command
    # helper — that assumes bash, the one thing not guaranteed yet either.
    cmd = f"proot-distro login {CONTAINER_NAME} --shared-tmp -- sh /tmp/{PROVISION_SCRIPT_NAME}"
    rc = stream_cmd(cmd, log, timeout=600)
    return rc == 0


# glxinfo (mesa-utils) and pactl (pulseaudio-utils/libpulse) stand in for
# their whole package group — every profile's mesa_pkgs/audio_pkgs
# installs one of these, so their presence on disk is a cheap, host-side
# proxy for "did this run already" without a container login round trip.
_GLXINFO_PATHS = ("/usr/bin/glxinfo", "/usr/local/bin/glxinfo")
_PACTL_PATHS = ("/usr/bin/pactl", "/usr/local/bin/pactl")


def gpu_audio_present() -> tuple[bool, bool]:
    """(gpu_ok, audio_ok) — whether the GL and audio-client userspace
    Start Desktop needs are already in the container, checked from the
    host via the rootfs directly rather than a container login."""
    gpu_ok = any(os.path.exists(container_path(p)) for p in _GLXINFO_PATHS)
    audio_ok = any(os.path.exists(container_path(p)) for p in _PACTL_PATHS)
    return gpu_ok, audio_ok


def _install_cmd(mgr: PkgManager, pkgs: str) -> str:
    return mgr.install_cmd.format(pkgs=pkgs)


def ensure_gpu_audio_packages(log: Log) -> bool:
    """Mesa (GPU) and pulseaudio-utils-equivalent (audio) userspace —
    present on XLabs's own image already, absent on a vanilla Manual
    Install pull until this runs. Doctor's Fix calls this; so does
    anything that wants Start Desktop to actually render something."""
    mgr = detect_package_manager(log)
    if mgr is None:
        log("[red]Could not install GPU/audio packages: no supported package manager found.[/red]")
        return False

    script = f"""#!/bin/sh
set -e
{mgr.update_cmd}
{_install_cmd(mgr, f"{mgr.mesa_pkgs} {mgr.audio_pkgs}")}
"""
    if not write_container_script("pdm-gpu-audio.sh", script):
        log("[red]Could not write the package script.[/red]")
        return False

    cmd = f"proot-distro login {CONTAINER_NAME} --shared-tmp -- sh /tmp/pdm-gpu-audio.sh"
    rc = stream_cmd(cmd, log, timeout=600)
    return rc == 0
