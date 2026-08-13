"""ActionScreen: the thread-worker runner shared by every long-running menu
action (Start, Stop, Update, Reset, Cache, ...).

    python tests/test_action_screen.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run, wait_for_rows
from textual.widgets import Button, RichLog

from installer import app as app_module
from installer.app import ActionScreen, MainScreen, PDMApp


async def test_escape_cannot_leave_running_action() -> None:
    """Regression: escape used to walk out of a screen whose Back was disabled.

    Leaving mid image pull abandoned a half-installed container with nothing
    on screen to say so.
    """
    release = threading.Event()

    def blocking(log) -> None:
        log("working")
        release.wait(timeout=15)
        log("done")

    # Drive the real UI path. Pushing a screen from outside the app's own
    # handlers never mounts it, so the runner is swapped instead — that also
    # keeps the test from touching the machine's image cache.
    original = app_module.run_clean_cache
    app_module.run_clean_cache = blocking

    app = PDMApp()
    try:
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#cache")
            await pilot.pause()
            await pilot.click("#confirm")

            # pilot.pause() waits on pending messages and times out while a
            # thread worker is blocked, so use plain sleeps until released.
            await asyncio.sleep(0.5)

            screen = app.screen
            check(isinstance(screen, ActionScreen), f"got {screen!r}")
            check(
                screen.query_one("#back", Button).disabled,
                "Back should be disabled while working",
            )

            await pilot.press("escape")
            await asyncio.sleep(0.3)
            check(
                isinstance(app.screen, ActionScreen),
                "escape left a running action screen",
            )

            release.set()
            for _ in range(60):
                await asyncio.sleep(0.1)
                if not app.screen.query_one("#back", Button).disabled:
                    break
            await pilot.pause()

            check(
                not app.screen.query_one("#back", Button).disabled,
                "Back never re-enabled after the worker finished",
            )
            check(app.screen.query_one("#log", RichLog).lines, "no output was written")

            await pilot.press("escape")
            await pilot.pause()
            check(isinstance(app.screen, MainScreen), "escape did not leave a finished screen")
    finally:
        app_module.run_clean_cache = original
        release.set()


def _expected_export_path() -> str:
    directory = (
        app_module.REPO_DIR
        if os.path.isdir(app_module.REPO_DIR)
        else tempfile.gettempdir()
    )
    return os.path.join(directory, app_module.EXPORT_NAME)


async def test_copy_buttons_export_output() -> None:
    """The diagnostic screen must be able to hand its text back out."""
    app = PDMApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()

        await pilot.click("#doctor")
        await pilot.pause()
        await wait_for_rows(pilot, app, "#doctor-table")

        screen = app.screen
        screen.query_one("#copy", Button)
        payload = screen.copy_payload()
        check(payload.strip(), "Doctor produced an empty copy payload")
        check("pdm" in payload, f"Doctor payload has no header: {payload[:40]!r}")

        await pilot.click("#copy")
        await pilot.pause()
        check(
            os.path.exists(_expected_export_path()),
            "copy did not mirror the output to a file",
        )

        await pilot.click("#back")
        await pilot.pause()
        check(isinstance(app.screen, MainScreen), "Doctor did not return to the menu")


async def test_update_offers_restart() -> None:
    """Update should be able to relaunch onto the code it just pulled.

    The runner is swapped for a controllable one: the real update finishes
    instantly when there is no checkout, which would make the "disabled while
    working" assertion a race.
    """
    release = threading.Event()

    def blocking(log) -> None:
        log("pulling")
        release.wait(timeout=15)

    original = app_module.run_update
    app_module.run_update = blocking

    app = PDMApp()
    try:
        async with app.run_test(size=(60, 30)) as pilot:
            await pilot.pause()
            check(app.restart_requested is False, "restart wanted before it was asked for")

            await pilot.click("#update")
            await asyncio.sleep(0.5)

            screen = app.screen
            check(isinstance(screen, ActionScreen), f"got {screen!r}")
            check(
                screen.query_one("#restart", Button).disabled,
                "restart was offered while the update was still running",
            )

            release.set()
            for _ in range(80):
                await asyncio.sleep(0.1)
                if not app.screen.query_one("#back", Button).disabled:
                    break
            await pilot.pause()

            check(
                not app.screen.query_one("#restart", Button).disabled,
                "restart stayed disabled after the update finished",
            )

            await pilot.click("#restart")
            await pilot.pause()
            check(app.restart_requested, "pressing Restart did not request one")
    finally:
        app_module.run_update = original
        release.set()


def test_other_actions_do_not_offer_restart() -> None:
    """Only Update relaunches. Everything else just returns to the menu."""
    plain = ActionScreen("Plain", lambda log: None)
    check(plain._offer_restart is False, "restart is opt-in and was not requested")


def test_export_never_creates_a_repo_directory() -> None:
    """Copying must not conjure ~/pdm on a machine without a checkout."""
    if os.path.isdir(app_module.REPO_DIR):
        return  # nothing to prove on a real checkout
    app_module._write_export("probe")
    check(
        not os.path.isdir(app_module.REPO_DIR),
        f"copying created {app_module.REPO_DIR}",
    )


TESTS = [
    test_escape_cannot_leave_running_action,
    test_copy_buttons_export_output,
    test_update_offers_restart,
    test_other_actions_do_not_offer_restart,
    test_export_never_creates_a_repo_directory,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
