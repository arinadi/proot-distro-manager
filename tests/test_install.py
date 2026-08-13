"""install.py: the bootstrap script install.sh hands off to, and
installer/presets.py: the repo-tracked config it can restore.

Nothing here calls main() — install_libs/install_termux_packages/
install_container all touch the real machine (pip, pkg, proot-distro),
same reason run_tests.py never presses Doctor's Fix button. This covers
the preset-restore wiring and gating, since a wrong default there means
silently overwriting a container the user has been living in for months.

Presets are deliberately install.py-only: no TUI screen offers to apply
one, and Reset/Doctor's Fix never touch presets/ either. install.py is the
one place a container is ever truly new — everywhere else "no container"
usually just means something else already went wrong, not "seed it".

    python tests/test_install.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

import install
from installer import presets


def test_find_preset_picks_newest() -> None:
    """The newest .tar.gz in presets/ wins; a missing or empty dir reads as
    no preset, not an error, since most installs never add one."""
    original_dir = presets.PRESETS_DIR
    try:
        presets.PRESETS_DIR = os.path.join(tempfile.mkdtemp(), "does-not-exist")
        check(presets.find_preset() is None, "a missing presets/ dir must read as no preset")

        fake_dir = tempfile.mkdtemp()
        presets.PRESETS_DIR = fake_dir
        check(presets.find_preset() is None, "an empty presets/ dir must read as no preset")

        for name, age in (("old.tar.gz", 200), ("new.tar.gz", 50)):
            path = os.path.join(fake_dir, name)
            with open(path, "wb") as f:
                f.write(b"0" * 512)
            then = time.time() - age
            os.utime(path, (then, then))
        with open(os.path.join(fake_dir, "notes.txt"), "w") as f:
            f.write("not a preset")

        found = presets.find_preset()
        check(found is not None, "a populated presets/ dir returned None")
        check(found.name == "new.tar.gz", f"expected the newest archive, got {found.name}")
    finally:
        presets.PRESETS_DIR = original_dir


def test_restore_preset_only_applies_to_a_fresh_pull() -> None:
    """Regression: an earlier version gated this on container_exists()
    alone, so re-running install.py — which is meant to be safe, and
    normally finds the container already there — would restore the preset
    onto it every time, silently overwriting whatever the user had done
    with it since. fresh=False (the container already existed) must be a
    hard no, even with a preset sitting right there."""
    calls: list[str] = []
    original_find_preset = install.find_preset
    original_apply_preset = install.apply_preset
    install.apply_preset = lambda log: (calls.append("applied"), True)[1]

    class FakePreset:
        name = "home-preset.tar.gz"

    try:
        install.find_preset = lambda: FakePreset()

        install.restore_preset(fresh=False)
        check(calls == [], "must not restore onto a container that already existed")

        install.restore_preset(fresh=True)
        check(calls == ["applied"], "must restore onto a container this run just pulled")

        calls.clear()
        install.find_preset = lambda: None
        install.restore_preset(fresh=True)
        check(calls == [], "must not restore when there is no preset, fresh or not")
    finally:
        install.find_preset = original_find_preset
        install.apply_preset = original_apply_preset


def test_install_container_reports_whether_it_was_fresh() -> None:
    """restore_preset's fresh/not-fresh gate is only meaningful if
    install_container() actually reports which one happened, rather than
    the caller having to guess from container_exists() before and after."""
    import inspect

    check(
        install.install_container.__annotations__.get("return") is bool,
        "install_container must declare a bool return so callers can't ignore it",
    )
    source = inspect.getsource(install.install_container)
    check(
        "return False" in source and "return True" in source,
        "install_container must report both the already-present and freshly-pulled cases",
    )


def test_bootstrap_order_is_container_then_user_then_preset() -> None:
    """restore_backup's RESTORE_SCRIPT chowns to the admin user by name —
    that only works once the user actually exists in the container, so the
    bootstrap order matters, not just that all three steps eventually run."""
    import inspect

    source = inspect.getsource(install.main)
    container_at = source.index("install_container()")
    admin_at = source.index("setup_admin_user()")
    preset_at = source.index("restore_preset(")
    check(
        container_at < admin_at < preset_at,
        "install.py must set up the admin user before restoring a preset into its home",
    )


TESTS = [
    test_find_preset_picks_newest,
    test_restore_preset_only_applies_to_a_fresh_pull,
    test_install_container_reports_whether_it_was_fresh,
    test_bootstrap_order_is_container_then_user_then_preset,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
