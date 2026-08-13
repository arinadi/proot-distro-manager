"""installer/system.py: stream_cmd and pull_image.

    python tests/test_system.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import system
from installer.system import stream_cmd


def test_stream_cmd_timeout_kills_silent_process() -> None:
    """Regression: the deadline used to be checked only when a line arrived.

    A command producing no output therefore ran to completion no matter the
    timeout, so every long operation in the app had no working limit.
    """
    silent = f'"{sys.executable}" -c "import time; time.sleep(20)"'
    start = time.monotonic()
    rc = stream_cmd(silent, lambda _m: None, timeout=2)
    elapsed = time.monotonic() - start

    check(elapsed < 10, f"timeout did not fire: took {elapsed:.1f}s")
    check(rc == 1, f"expected rc=1 on timeout, got {rc}")


def test_stream_cmd_returns_output_and_code() -> None:
    lines: list[str] = []
    script = '"%s" -c "print(\'hello\'); raise SystemExit(3)"' % sys.executable
    rc = stream_cmd(script, lines.append, timeout=30)

    check(rc == 3, f"expected rc=3, got {rc}")
    check("hello" in lines, f"output not captured: {lines}")


def test_stream_cmd_shows_carriage_return_progress() -> None:
    """Regression: downloaders redraw one line with CR and emit no newline.

    A line-based reader showed nothing for the whole transfer, so pulling the
    image looked frozen.
    """
    # chr(13)/chr(10) rather than escapes: this source is written to a file
    # and read back, and backslashes do not survive that round trip cleanly.
    probe = "\n".join([
        "import sys, time",
        "for i in range(0, 101, 25):",
        "    sys.stdout.write(chr(13) + 'Downloading %d%%' % i)",
        "    sys.stdout.flush()",
        "    time.sleep(0.05)",
        "sys.stdout.write(chr(10) + 'Done' + chr(10))",
    ])
    script = os.path.join(tempfile.gettempdir(), "pdm-progress-probe.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(probe)

    command = '"%s" "%s"' % (sys.executable, script)

    class Logger(list):
        def __call__(self, message=""):
            self.append(("log", message))

        def progress(self, message):
            self.append(("progress", message))

    logger = Logger()
    rc = stream_cmd(command, logger, timeout=30)
    check(rc == 0, f"expected rc=0, got {rc}: {list(logger)}")

    progress = [m for kind, m in logger if kind == "progress"]
    check(len(progress) >= 3, f"progress was not reported live: {list(logger)}")
    check(
        any("Done" in m for kind, m in logger if kind == "log"),
        f"the final line never arrived: {list(logger)}",
    )

    # Without .progress() the redraws are throttled into the log rather than
    # flooding it.
    plain: list[str] = []
    rc = stream_cmd(command, plain.append, timeout=30)
    check(rc == 0, f"expected rc=0, got {rc}: {plain}")
    check(plain, "the throttled path produced nothing at all")
    check(len(plain) < 6, f"the throttled path flooded the log: {plain}")

    os.remove(script)


def test_pull_image_falls_back_to_docker_hub() -> None:
    """GHCR has no pull-rate limit for a public package, but some ISPs route
    its Fastly-backed CDN badly. Docker Hub is faster there but rate-limits
    anonymous pulls per IP — shared with every other subscriber behind the
    same carrier-grade NAT on mobile data. pull_image() must try GHCR
    first and only fall back to Docker Hub if that genuinely failed."""
    original_stream_cmd = system.stream_cmd
    original_run_cmd = system.run_cmd
    original_is_installed = system.is_installed
    calls: list[str] = []

    def fake_run_cmd(cmd: str, timeout: int = 60):
        calls.append(cmd)
        return 0, ""

    try:
        # Primary works: no fallback attempted at all.
        system.stream_cmd = lambda cmd, log, timeout=1800: calls.append(cmd) or 0
        system.run_cmd = fake_run_cmd
        system.is_installed = lambda: True
        calls.clear()
        lines: list[str] = []
        check(system.pull_image(lines.append), "reported failure when the primary pull worked")
        # Exactly one attempt — the fallback ref is a substring of the
        # primary one ("arinadi/pdm" inside "ghcr.io/arinadi/..."),
        # so a call count is the only unambiguous way to prove no retry.
        check(len(calls) == 1, f"fell back when the primary pull already worked: {calls}")

        # Primary fails, fallback works: the partial container must be
        # removed before retrying, and the fallback registry gets a turn.
        attempts = {"n": 0}

        def stream_then_succeed(cmd: str, log, timeout=1800):
            calls.append(cmd)
            attempts["n"] += 1
            return 1 if attempts["n"] == 1 else 0

        system.stream_cmd = stream_then_succeed
        calls.clear()
        lines2: list[str] = []
        check(system.pull_image(lines2.append), "did not recover via the fallback registry")
        check(
            any(c.startswith("proot-distro remove") for c in calls),
            f"did not clean up the partial container before retrying: {calls}",
        )
        installs = [c for c in calls if c.startswith("proot-distro install")]
        check(len(installs) == 2, f"expected a second install attempt, got: {installs}")
        check(
            installs[0] != installs[1],
            f"the second attempt used the same command as the first: {installs}",
        )

        # Both fail: must report failure rather than claim success.
        system.stream_cmd = lambda cmd, log, timeout=1800: calls.append(cmd) or 1
        system.is_installed = lambda: False
        calls.clear()
        lines3: list[str] = []
        check(not system.pull_image(lines3.append), "claimed success when both registries failed")
        check(lines3, "the failure was not explained")
    finally:
        system.stream_cmd = original_stream_cmd
        system.run_cmd = original_run_cmd
        system.is_installed = original_is_installed


TESTS = [
    test_stream_cmd_timeout_kills_silent_process,
    test_stream_cmd_returns_output_and_code,
    test_stream_cmd_shows_carriage_return_progress,
    test_pull_image_falls_back_to_docker_hub,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
