"""installer/doctor.py: diagnosis and repair.

    python tests/test_doctor.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import doctor
from installer.system import container_path


def test_doctor_scan_shape() -> None:
    issues = doctor.diagnose()
    names = {i.name for i in issues}

    check(len(issues) >= 8, f"expected at least 8 issues, got {len(issues)}")
    for expected in ("Repository", "Launcher", "Container", "X11 sockets"):
        check(expected in names, f"{expected} missing from diagnosis")

    for issue in issues:
        check(not (issue.ok and issue.fix), f"{issue.name} is ok but offers a fix")
        # An unknown result is not actionable, so it must not advertise a fix.
        check(not (issue.unknown and issue.fix), f"{issue.name} is unknown but offers a fix")

    check(
        all(not i.ok for i in doctor.fixable(issues)),
        "fixable() returned an issue that already passes",
    )


def test_firefox_prefs_are_defaults_not_locks() -> None:
    """The video tuning must set defaults the user can still override."""
    body = doctor.FIREFOX_PREFS
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        check(
            stripped.startswith("pref(") and stripped.endswith(");"),
            f"unexpected line in the prefs file: {line!r}",
        )
        # lockPref would stop about:config from changing it.
        check("lockPref" not in stripped, f"prefs must not lock: {line!r}")

    for expected in ("media.mediasource.vp9.enabled", "media.av1.enabled"):
        check(expected in body, f"{expected} missing from the prefs")

    # Without a container there is nothing to tune, so the check stays out of
    # the way rather than reporting a problem that cannot exist yet.
    if not doctor.is_installed():
        names = {i.name for i in doctor.diagnose()}
        check("Firefox video" not in names, "reported Firefox with no container")

    # The repair refuses rather than raising when the target is absent.
    lines: list[str] = []
    if not os.path.isdir(container_path(doctor.FIREFOX_PREFS_DIR)):
        check(
            not doctor._fix_firefox_prefs(lines.append),
            "the repair claimed success with no container",
        )
        check(lines, "the repair explained nothing")


def test_doctor_reports_security_archive() -> None:
    """The shadowing bug mypy caught: a loop variable named `packages` hid
    the module import for the rest of diagnose(), so this check silently
    referenced a list instead of installer.packages and crashed at runtime
    the moment a container existed."""
    issues = doctor.diagnose()
    names = {i.name for i in issues}
    if doctor.is_installed():
        check("Security archive" in names, "the check did not run with a container present")
    else:
        check(
            "Security archive" not in names,
            "the check ran with no container to check",
        )


def test_electron_sandbox_detection_and_fix() -> None:
    """VS Code (and anything else Electron) opens nothing under proot: the
    SUID sandbox needs unprivileged user namespaces proot only fakes, so
    Chromium's zygote init fails and the app never appears. Doctor finds
    every installed Electron app by the chrome-sandbox helper next to its
    binary — not by name, so something besides VS Code is caught too — and
    patches its .desktop Exec with --no-sandbox."""
    fake_root = tempfile.mkdtemp()
    apps_dir = os.path.join(fake_root, "usr", "share", "applications")
    code_dir = os.path.join(fake_root, "opt", "code")
    bin_dir = os.path.join(fake_root, "usr", "bin")
    os.makedirs(apps_dir)
    os.makedirs(code_dir)
    os.makedirs(bin_dir)

    open(os.path.join(code_dir, "code"), "w").close()
    open(os.path.join(code_dir, "chrome-sandbox"), "w").close()
    code_desktop = os.path.join(apps_dir, "code.desktop")
    with open(code_desktop, "w", newline="\n") as f:
        f.write(
            "[Desktop Entry]\n"
            "Name=Visual Studio Code\n"
            "Exec=/opt/code/code --unity-launch %F\n"
            "Type=Application\n"
        )

    # A non-Electron app in a different directory, with no sandbox helper
    # anywhere near it, must not be touched.
    open(os.path.join(bin_dir, "htop"), "w").close()
    htop_desktop = os.path.join(apps_dir, "htop.desktop")
    with open(htop_desktop, "w", newline="\n") as f:
        f.write("[Desktop Entry]\nName=htop\nExec=/usr/bin/htop\nType=Application\n")

    original_container_path = doctor.container_path
    doctor.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))
    try:
        found, missing = doctor._electron_status()
        check(found == 1, f"expected exactly the Electron app to be found, got {found}")
        check(missing == 1, "a freshly-written .desktop must not already look patched")

        lines: list[str] = []
        check(doctor._fix_electron_sandbox(lines.append), "the fix reported failure")

        patched = open(code_desktop).read()
        check("--no-sandbox" in patched, "Exec was not patched")
        check("--unity-launch" in patched, "the fix dropped an existing flag")
        check("%F" in patched, "the fix dropped the file-open field code")

        untouched = open(htop_desktop).read()
        check("--no-sandbox" not in untouched, "a non-Electron app was patched")

        found, missing = doctor._electron_status()
        check(missing == 0, "the app still reports as unpatched after the fix")

        # Re-running must not add a second --no-sandbox.
        lines2: list[str] = []
        check(doctor._fix_electron_sandbox(lines2.append), "the re-run reported failure")
        check(
            open(code_desktop).read().count("--no-sandbox") == 1,
            "re-running the fix duplicated the flag",
        )
    finally:
        doctor.container_path = original_container_path


def test_resolv_conf_check_and_fix() -> None:
    """DNS failure reads as a dead mirror ("Temporary failure in name
    resolution") when the real cause is an empty or dangling resolv.conf
    inside the container — this is the check and repair for that."""
    fake_root = tempfile.mkdtemp()
    etc_dir = os.path.join(fake_root, "etc")
    os.makedirs(etc_dir)
    target = os.path.join(etc_dir, "resolv.conf")

    original_container_path = doctor.container_path
    doctor.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))
    try:
        check(not doctor._resolv_conf_ok(), "a missing resolv.conf must not read as ok")

        with open(target, "w", newline="\n") as f:
            f.write("# empty, generated at container creation\n")
        check(
            not doctor._resolv_conf_ok(),
            "a resolv.conf with no nameserver line must not read as ok",
        )

        lines: list[str] = []
        check(doctor._fix_resolv_conf(lines.append), "the fix reported failure")
        check(doctor._resolv_conf_ok(), "the fix did not leave a usable resolv.conf")
        check("1.1.1.1" in open(target).read(), "the fix did not write a real nameserver")

        with open(target, "w", newline="\n") as f:
            f.write("nameserver 10.0.0.1\n")
        check(doctor._resolv_conf_ok(), "an existing nameserver was not recognised")
    finally:
        doctor.container_path = original_container_path


def test_timezone_check_and_fix() -> None:
    """The image ships UTC; the repair points /etc/localtime at the
    device's own zone once it can tell what that is, and refuses rather
    than writing a dangling symlink for a zone with no zoneinfo file."""
    fake_root = tempfile.mkdtemp()
    os.makedirs(os.path.join(fake_root, "usr", "share", "zoneinfo", "Asia"))
    open(os.path.join(fake_root, "usr", "share", "zoneinfo", "Asia", "Jakarta"), "w").close()
    os.makedirs(os.path.join(fake_root, "etc"))

    original_container_path = doctor.container_path
    original_run_cmd = doctor.run_cmd
    doctor.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))
    doctor.run_cmd = lambda cmd, timeout=60: (0, "Asia/Jakarta\n")
    try:
        check(
            doctor._android_timezone() == "Asia/Jakarta",
            "did not read the fake getprop output",
        )
        check(
            doctor._container_timezone() is None,
            "a fresh container must not already report a timezone",
        )

        doctor.run_cmd = lambda cmd, timeout=60: (0, "Not/AZone\n")
        lines: list[str] = []
        check(
            not doctor._fix_timezone(lines.append),
            "claimed success for a zone with no zoneinfo file in the container",
        )
        doctor.run_cmd = lambda cmd, timeout=60: (0, "Asia/Jakarta\n")

        if os.name != "nt":
            # Symlink creation needs a privilege this test sandbox may not
            # have on Windows; the logic above it is what matters there.
            check(doctor._fix_timezone(lambda m: None), "the fix reported failure")
            check(
                doctor._container_timezone() == "Asia/Jakarta",
                "/etc/timezone was not written",
            )
            link_target = os.readlink(os.path.join(fake_root, "etc", "localtime"))
            check(
                link_target == "/usr/share/zoneinfo/Asia/Jakarta",
                f"localtime points at {link_target!r}, not the container-relative path",
            )
    finally:
        doctor.container_path = original_container_path
        doctor.run_cmd = original_run_cmd


def test_storage_check_and_cleanup_guard() -> None:
    """The only automatic storage repair is apt cache cleanup, and it must
    refuse rather than pretend when there is no container to clean."""
    if not doctor.is_installed():
        lines: list[str] = []
        check(not doctor._fix_storage(lines.append), "claimed success with no container")
        check(lines, "the refusal was not explained")

    issues = {i.name: i for i in doctor.diagnose()}
    check("Storage" in issues, "Storage must always be reported")
    storage = issues["Storage"]
    if not storage.ok:
        expected_fix = doctor._fix_storage if doctor.is_installed() else None
        check(storage.fix is expected_fix, "Storage repair offered without a container to clean")


def test_doctor_reports_audio() -> None:
    names = {i.name for i in doctor.diagnose()}
    for expected in ("Audio server", "Audio reachable", "Audio output"):
        check(expected in names, f"{expected} missing from the diagnosis")


def test_termux_duplicates_are_safe() -> None:
    """Never offer to remove anything outside the candidate list."""
    dupes = doctor.termux_duplicates()
    check(isinstance(dupes, list), f"expected a list, got {type(dupes)}")
    for dupe in dupes:
        check(
            dupe.package in doctor.TERMUX_DUPLICATES,
            f"{dupe.package} is not a removal candidate",
        )

    # Everything the project itself runs on must be unreachable by this path.
    for essential in (
        "python", "python-pip", "git", "proot-distro", "termux-x11-nightly",
        "pulseaudio", "termux-tools", "bash", "coreutils", "apt", "dpkg",
        "mesa-zink", "virglrenderer-android", "angle-android",
    ):
        check(
            essential not in doctor.TERMUX_DUPLICATES,
            f"{essential} must never be a removal candidate",
        )

    lines: list[str] = []
    check(
        not doctor.remove_termux_packages(["coreutils"], lines.append),
        "removing a non-candidate package was not refused",
    )
    check(
        any("Refusing" in line for line in lines),
        f"refusal was not explained: {lines}",
    )


TESTS = [
    test_doctor_scan_shape,
    test_firefox_prefs_are_defaults_not_locks,
    test_doctor_reports_security_archive,
    test_electron_sandbox_detection_and_fix,
    test_resolv_conf_check_and_fix,
    test_timezone_check_and_fix,
    test_storage_check_and_cleanup_guard,
    test_doctor_reports_audio,
    test_termux_duplicates_are_safe,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
