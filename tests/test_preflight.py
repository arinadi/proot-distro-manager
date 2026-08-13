"""installer/preflight.py: environment checks (pure stdlib).

    python tests/test_preflight.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer.preflight import run_all_checks


def test_preflight_shape() -> None:
    checks = run_all_checks()
    names = {c.name for c in checks}

    check(len(checks) >= 6, f"expected at least 6 checks, got {len(checks)}")
    check("X11 app" in names, "X11 app check missing")
    for c in checks:
        check(isinstance(c.unknown, bool), f"{c.name} has no unknown flag")
        # A check may not claim both that it passed and that it could not run.
        check(not (c.ok and c.unknown), f"{c.name} is both ok and unknown")


TESTS = [
    test_preflight_shape,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
