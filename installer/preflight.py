"""Environment checks.

Pure stdlib on purpose: the installer runs these before anything has been
pip-installed, so this module must not import textual, rich, or system.py.
"""

import os
import shutil
import socket
import subprocess
import sys
from typing import NamedTuple

from .const import PROOT_DIR

X11_PACKAGE = "com.termux.x11"
X11_APK_URL = "https://github.com/termux/termux-x11/releases/tag/nightly"


class CheckResult(NamedTuple):
    name: str
    ok: bool
    message: str
    # True when the check could not be performed at all. Distinct from ok=False,
    # which asserts the thing is genuinely absent.
    unknown: bool = False


def check_internet() -> CheckResult:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=5).close()
        return CheckResult("Internet", True, "Connected")
    except OSError:
        return CheckResult("Internet", False, "No connection")


def check_storage(min_gb: float = 4.0) -> CheckResult:
    """The image unpacks to well over 2 GB, so warn below 4 GB free."""
    for path in ("/data", os.path.expanduser("~"), "/"):
        try:
            free_gb = shutil.disk_usage(path).free / (1024**3)
        except OSError:
            continue
        ok = free_gb >= min_gb
        detail = f"{free_gb:.1f} GB free"
        return CheckResult("Storage", ok, detail if ok else f"{detail} (need {min_gb:g} GB)")
    return CheckResult("Storage", False, "Cannot determine free space")


def check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 9)
    version = f"v{major}.{minor}"
    return CheckResult("Python", ok, version if ok else f"{version} (need 3.9+)")


def check_proot() -> CheckResult:
    found = shutil.which("proot-distro") is not None
    return CheckResult("proot-distro", found, "Installed" if found else "Missing")


def check_x11() -> CheckResult:
    found = shutil.which("termux-x11") is not None
    return CheckResult("Termux:X11", found, "Installed" if found else "Missing")


def check_x11_app() -> CheckResult:
    """The Android app, which is separate from the termux-x11 package.

    Without it the desktop starts but has nowhere to render, so this is worth
    reporting on its own rather than folding into the package check.

    Querying it from Termux is fiddly. `--user 0` is required on devices with
    a work profile or Samsung Secure Folder, where a bare query fails with
    "Shell does not have permission to access user <n>". pm also needs stdin
    off the terminal and stderr folded in, or it trips over the character
    device. When the query itself fails the result is `unknown`, not
    "missing" — those are different answers and only one of them is
    actionable.
    """
    pm = shutil.which("pm")
    if pm is None and os.path.exists("/system/bin/pm"):
        pm = "/system/bin/pm"
    if pm is None:
        return CheckResult("X11 app", False, "Cannot query installed apps", unknown=True)

    attempts = (
        [pm, "list", "packages", "--user", "0", X11_PACKAGE],
        [pm, "list", "packages", X11_PACKAGE],
    )
    for argv in attempts:
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        out = result.stdout or ""
        if f"package:{X11_PACKAGE}" in out:
            return CheckResult("X11 app", True, "Installed")

        # An empty, successful query is a real answer: the app is not there.
        lowered = out.lower()
        refused = (
            result.returncode != 0
            or "exception" in lowered
            or "denial" in lowered
            or "does not have permission" in lowered
        )
        if not refused:
            return CheckResult("X11 app", False, f"Missing — sideload from {X11_APK_URL}")

    return CheckResult(
        "X11 app", False, "pm refused the query — check manually", unknown=True
    )


def check_container() -> CheckResult:
    found = os.path.isdir(os.path.join(PROOT_DIR, "rootfs"))
    return CheckResult("Container", found, "Installed" if found else "Not installed")


def run_all_checks() -> list[CheckResult]:
    return [
        check_internet(),
        check_storage(),
        check_python(),
        check_proot(),
        check_x11(),
        check_x11_app(),
        check_container(),
    ]


def blocking_failure(checks: list[CheckResult]) -> CheckResult | None:
    """Return the first check that must pass before installing, if it failed.

    Only Internet is fatal — the rest describe work the installer is about
    to do anyway.
    """
    for check in checks:
        if check.name == "Internet" and not check.ok:
            return check
    return None
