"""installer/provision.py: what XLabs's Dockerfile used to bake in at
build time (admin user, sudo, bash, GL/audio userspace), now done
distro-aware at install time since PDM pulls whatever image Manual
Install points it at.

    python tests/test_provision.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import config, provision


def test_pkg_manager_lookup() -> None:
    for name in ("apt", "apk", "pacman", "dnf"):
        mgr = provision.pkg_manager_by_name(name)
        check(mgr is not None, f"{name} should be a known package manager")
        check(mgr.name == name, f"lookup for {name} returned {mgr}")
    check(provision.pkg_manager_by_name("nonexistent") is None, "an unknown name resolved")


def test_pkg_manager_profiles_are_internally_consistent() -> None:
    """Every profile must actually be usable: install_cmd takes a {pkgs}
    slot, create_user/set_password take a {user} slot, and nothing is
    left blank — a typo here fails silently as a no-op shell command
    otherwise."""
    for mgr in provision.PKG_MANAGERS:
        check("{pkgs}" in mgr.install_cmd, f"{mgr.name}.install_cmd has no {{pkgs}} slot")
        check("{user}" in mgr.create_user_cmd, f"{mgr.name}.create_user_cmd has no {{user}} slot")
        check("{user}" in mgr.set_password_cmd, f"{mgr.name}.set_password_cmd has no {{user}} slot")
        for field in (
            mgr.detect_cmd, mgr.update_cmd, mgr.bash_pkg, mgr.sudo_pkg,
            mgr.mesa_pkgs, mgr.audio_pkgs,
        ):
            check(field.strip(), f"{mgr.name} has a blank field: {mgr}")


def test_detect_package_manager_uses_cache_without_probing() -> None:
    original_get = config.get
    original_is_installed = provision.is_installed
    original_run_cmd = provision.run_cmd
    calls: list[str] = []

    config.get = lambda key, default=None: "apk" if key == provision.PKG_MANAGER_KEY else default
    provision.is_installed = lambda: True
    provision.run_cmd = lambda cmd, timeout=60: (calls.append(cmd), (1, ""))[1]
    try:
        mgr = provision.detect_package_manager(lambda m: None)
        check(mgr is not None and mgr.name == "apk", f"cache was not honoured: {mgr}")
        check(calls == [], "a cache hit must not probe the container")
    finally:
        config.get = original_get
        provision.is_installed = original_is_installed
        provision.run_cmd = original_run_cmd


def test_detect_package_manager_probes_and_caches() -> None:
    """No cache: probes each manager's detect_cmd in order and remembers
    whichever one answers, so the next call doesn't probe again."""
    original_get = config.get
    original_set = config.set_value
    original_unset = config.unset
    original_is_installed = provision.is_installed
    original_run_cmd = provision.run_cmd

    fake_env: dict[str, str] = {}
    config.get = lambda key, default=None: fake_env.get(key, default)
    config.set_value = lambda key, value: (fake_env.__setitem__(key, value), True)[1]
    config.unset = lambda key: (fake_env.pop(key, None), True)[1]
    provision.is_installed = lambda: True

    probed: list[str] = []

    def fake_run_cmd(cmd, timeout=60):
        probed.append(cmd)
        # Only pacman's detect_cmd succeeds.
        return (0, "") if "pacman" in cmd else (1, "")

    provision.run_cmd = fake_run_cmd
    try:
        mgr = provision.detect_package_manager(lambda m: None)
        check(mgr is not None and mgr.name == "pacman", f"expected pacman, got {mgr}")
        check(fake_env.get(provision.PKG_MANAGER_KEY) == "pacman", "detection was not cached")

        probed.clear()
        mgr2 = provision.detect_package_manager(lambda m: None)
        check(mgr2 is not None and mgr2.name == "pacman", "cached value not reused")
        check(probed == [], "a second call re-probed instead of using the cache")
    finally:
        config.get = original_get
        config.set_value = original_set
        config.unset = original_unset
        provision.is_installed = original_is_installed
        provision.run_cmd = original_run_cmd


def test_detect_package_manager_no_container_or_no_match() -> None:
    original_get = config.get
    original_is_installed = provision.is_installed
    original_run_cmd = provision.run_cmd

    config.get = lambda key, default=None: default
    try:
        provision.is_installed = lambda: False
        check(
            provision.detect_package_manager(lambda m: None) is None,
            "no container must read as no package manager, not an error",
        )

        provision.is_installed = lambda: True
        provision.run_cmd = lambda cmd, timeout=60: (1, "")
        lines: list[str] = []
        check(
            provision.detect_package_manager(lines.append) is None,
            "an unrecognised container must read as no package manager",
        )
        check(lines, "the failure to detect a package manager was not explained")
    finally:
        config.get = original_get
        provision.is_installed = original_is_installed
        provision.run_cmd = original_run_cmd


def test_forget_package_manager_clears_the_cache() -> None:
    original_get = config.get
    original_set = config.set_value
    original_unset = config.unset

    fake_env: dict[str, str] = {}
    config.get = lambda key, default=None: fake_env.get(key, default)
    config.set_value = lambda key, value: (fake_env.__setitem__(key, value), True)[1]
    config.unset = lambda key: (fake_env.pop(key, None), True)[1]
    try:
        config.set_value(provision.PKG_MANAGER_KEY, "apt")
        provision.forget_package_manager()
        check(
            config.get(provision.PKG_MANAGER_KEY) is None,
            "forget_package_manager did not clear the cached value",
        )
    finally:
        config.get = original_get
        config.set_value = original_set
        config.unset = original_unset


def test_build_script_is_idempotent_posix_and_substitutes_the_user() -> None:
    """Every profile's script must: guard each step so re-running it does
    nothing extra, use sh rather than bash-only syntax (bash may not
    exist yet — that is one of the things being installed), and actually
    substitute the admin username rather than leaving a template hole."""
    for mgr in provision.PKG_MANAGERS:
        script = provision._build_script(mgr)
        check(script.startswith("#!/bin/sh"), f"{mgr.name} script is not POSIX sh")
        check("set -e" in script, f"{mgr.name} script does not fail fast")
        check(f"id {provision.ADMIN_USER}" in script, f"{mgr.name} script has no idempotency guard for the user")
        check("command -v sudo" in script, f"{mgr.name} script has no idempotency guard for sudo")
        check("command -v bash" in script, f"{mgr.name} script has no idempotency guard for bash")
        check("{user}" not in script, f"{mgr.name} script left a template hole unfilled")
        check(provision.ADMIN_USER in script, f"{mgr.name} script never mentions the admin user")
        for bashism in ("[[", "local ", "function "):
            check(bashism not in script, f"{mgr.name} script contains bash-only syntax: {bashism!r}")


def test_provision_container_uses_sh_not_the_bash_assuming_helper() -> None:
    """Provisioning is the one place that cannot assume container_command
    (which hardcodes bash) — bash's presence is exactly what has not been
    verified yet at this point."""
    original_get = config.get
    original_is_installed = provision.is_installed
    original_run_cmd = provision.run_cmd
    original_stream_cmd = provision.stream_cmd
    original_write = provision.write_container_script

    config.get = lambda key, default=None: "apt" if key == provision.PKG_MANAGER_KEY else default
    provision.is_installed = lambda: True
    provision.run_cmd = lambda cmd, timeout=60: (1, "")
    provision.write_container_script = lambda name, content: True

    commands: list[str] = []
    provision.stream_cmd = lambda cmd, log, timeout=900: (commands.append(cmd), 0)[1]
    try:
        ok = provision.provision_container(lambda m: None)
        check(ok, "provisioning reported failure")
        check(len(commands) == 1, f"expected exactly one command, got {commands}")
        check(" sh /tmp/" in commands[0], f"provisioning did not run via sh: {commands[0]!r}")
        check("bash /tmp/" not in commands[0], f"provisioning assumed bash: {commands[0]!r}")
    finally:
        config.get = original_get
        provision.is_installed = original_is_installed
        provision.run_cmd = original_run_cmd
        provision.stream_cmd = original_stream_cmd
        provision.write_container_script = original_write


def test_gpu_audio_present_checks_the_rootfs_directly() -> None:
    fake_root = tempfile.mkdtemp()
    original_container_path = provision.container_path
    provision.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))
    try:
        check(
            provision.gpu_audio_present() == (False, False),
            "an empty rootfs must read as neither present",
        )

        os.makedirs(os.path.join(fake_root, "usr", "bin"))
        open(os.path.join(fake_root, "usr", "bin", "glxinfo"), "w").close()
        check(
            provision.gpu_audio_present() == (True, False),
            "glxinfo alone must not also report audio as present",
        )

        open(os.path.join(fake_root, "usr", "bin", "pactl"), "w").close()
        check(
            provision.gpu_audio_present() == (True, True),
            "both glxinfo and pactl present must report both ok",
        )
    finally:
        provision.container_path = original_container_path


def test_ensure_gpu_audio_packages_installs_via_the_detected_manager() -> None:
    original_get = config.get
    original_is_installed = provision.is_installed
    original_run_cmd = provision.run_cmd
    original_write = provision.write_container_script

    config.get = lambda key, default=None: "apk" if key == provision.PKG_MANAGER_KEY else default
    provision.is_installed = lambda: True
    provision.run_cmd = lambda cmd, timeout=60: (1, "")

    written: dict[str, str] = {}

    def fake_write(name, content):
        written[name] = content
        return True

    provision.write_container_script = fake_write
    provision.stream_cmd = lambda cmd, log, timeout=900: 0
    try:
        ok = provision.ensure_gpu_audio_packages(lambda m: None)
        check(ok, "reported failure")
        script = next(iter(written.values()))
        apk_profile = provision.pkg_manager_by_name("apk")
        check(apk_profile.mesa_pkgs in script, "the script did not install the distro's mesa packages")
        check(apk_profile.audio_pkgs in script, "the script did not install the distro's audio packages")
    finally:
        config.get = original_get
        provision.is_installed = original_is_installed
        provision.run_cmd = original_run_cmd
        provision.write_container_script = original_write


TESTS = [
    test_pkg_manager_lookup,
    test_pkg_manager_profiles_are_internally_consistent,
    test_detect_package_manager_uses_cache_without_probing,
    test_detect_package_manager_probes_and_caches,
    test_detect_package_manager_no_container_or_no_match,
    test_forget_package_manager_clears_the_cache,
    test_build_script_is_idempotent_posix_and_substitutes_the_user,
    test_provision_container_uses_sh_not_the_bash_assuming_helper,
    test_gpu_audio_present_checks_the_rootfs_directly,
    test_ensure_gpu_audio_packages_installs_via_the_detected_manager,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
