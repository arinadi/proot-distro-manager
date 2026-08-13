"""installer/config.py: .env, per-device settings.

    python tests/test_config.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run


def test_config_roundtrip(tmp_key: str = "PDM_TEST_KEY") -> None:
    """The .env holds per-device settings, so writing one key must not lose
    the others."""
    from installer import config

    original = config.load()
    try:
        check(config.set_value(tmp_key, "one"), "could not write the config")
        check(config.get(tmp_key) == "one", "value did not round-trip")

        check(config.set_value("PDM_TEST_OTHER", "two"), "second write failed")
        check(config.get(tmp_key) == "one", "writing one key dropped another")

        # Comments and blank lines must not become keys.
        check("#" not in "".join(config.load()), "a comment was parsed as a key")
    finally:
        config.unset(tmp_key)
        config.unset("PDM_TEST_OTHER")

    check(config.get(tmp_key) is None, "unset left the key behind")
    for key, value in original.items():
        check(config.get(key) == value, f"the test disturbed {key}")


def test_draw_path_roundtrip() -> None:
    """termux-x11's rendering flags (Settings) are picked from a fixed
    set; an unknown or missing .env value must fall back to normal rather
    than reach start_x11() and break the launch command."""
    from installer import config, start

    original = config.get(start.DRAW_PATH_KEY)
    try:
        check(start.load_draw_path() in start.DRAW_PATHS, "default draw path is not a known one")

        check(start.save_draw_path("force-bgra"), "could not save a known draw path")
        check(start.load_draw_path() == "force-bgra", "did not round-trip")

        check(not start.save_draw_path("not-a-real-path"), "accepted an unknown draw path")
        check(start.load_draw_path() == "force-bgra", "an invalid save changed the saved value")

        config.set_value(start.DRAW_PATH_KEY, "garbage")
        check(
            start.load_draw_path() == start.DEFAULT_DRAW_PATH,
            "a corrupted .env value was not caught by load_draw_path",
        )
    finally:
        if original is None:
            config.unset(start.DRAW_PATH_KEY)
        else:
            config.set_value(start.DRAW_PATH_KEY, original)


TESTS = [
    test_config_roundtrip,
    test_draw_path_roundtrip,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
