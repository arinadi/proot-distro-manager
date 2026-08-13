#!/usr/bin/env python3
"""Run every PDM test module together.

Tests are split by feature/screen (test_doctor.py, test_backup.py, ...) so
each file stays small enough to read on its own — see tests/support.py for
the shared check()/run() helpers every module uses. This file just collects
each module's TESTS list and runs them as one suite, the same set CI runs.

    python tests/run_tests.py           # everything
    python tests/test_doctor.py         # just one module, while working on it

Each test that guards a previously shipped bug says which one, so a future
change that reintroduces it fails with an explanation rather than a number.

Nothing here presses Doctor's Fix button: those repairs run real pip and pkg
installs against the machine running the tests.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import support
from test_action_screen import TESTS as ACTION_SCREEN_TESTS
from test_audio import TESTS as AUDIO_TESTS
from test_backup import TESTS as BACKUP_TESTS
from test_bench import TESTS as BENCH_TESTS
from test_config import TESTS as CONFIG_TESTS
from test_doctor import TESTS as DOCTOR_TESTS
from test_install import TESTS as INSTALL_TESTS
from test_main_screen import TESTS as MAIN_SCREEN_TESTS
from test_manual_install import TESTS as MANUAL_INSTALL_TESTS
from test_packages import TESTS as PACKAGES_TESTS
from test_preflight import TESTS as PREFLIGHT_TESTS
from test_provision import TESTS as PROVISION_TESTS
from test_settings_screen import TESTS as SETTINGS_SCREEN_TESTS
from test_store_screen import TESTS as STORE_SCREEN_TESTS
from test_system import TESTS as SYSTEM_TESTS

# Rough dependency order: low-level helpers first, screens last — not load-
# bearing (every test cleans up after itself), just easier to read a failed
# run top to bottom.
ALL_TESTS = [
    *SYSTEM_TESTS,
    *PREFLIGHT_TESTS,
    *DOCTOR_TESTS,
    *INSTALL_TESTS,
    *CONFIG_TESTS,
    *PACKAGES_TESTS,
    *PROVISION_TESTS,
    *BACKUP_TESTS,
    *MANUAL_INSTALL_TESTS,
    *BENCH_TESTS,
    *AUDIO_TESTS,
    *SETTINGS_SCREEN_TESTS,
    *STORE_SCREEN_TESTS,
    *MAIN_SCREEN_TESTS,
    *ACTION_SCREEN_TESTS,
]


def main() -> int:
    return support.run(ALL_TESTS)


if __name__ == "__main__":
    sys.exit(main())
