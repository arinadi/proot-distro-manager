"""MainScreen: navigation, the confirm gate on destructive actions, and the
narrow-terminal layout regression.

    python tests/test_main_screen.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run, wait_for_rows
from textual.widgets import Button

from installer.app import (
    ConfirmScreen,
    DoctorScreen,
    DupesScreen,
    MainScreen,
    PDMApp,
    SettingsScreen,
    StoreScreen,
)


async def test_tui_navigation() -> None:
    app = PDMApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        check(isinstance(app.screen, MainScreen), f"got {app.screen!r}")

        await pilot.click("#store")
        await pilot.pause()
        check(isinstance(app.screen, StoreScreen), f"got {app.screen!r}")
        await pilot.click("#back")
        await pilot.pause()

        await pilot.click("#doctor")
        await pilot.pause()
        check(isinstance(app.screen, DoctorScreen), f"got {app.screen!r}")
        rows = await wait_for_rows(pilot, app, "#doctor-table")
        # >=12 rather than the old >=8: Internet and Python are folded in
        # from the removed Status screen as real Issues now.
        check(rows >= 12, f"expected >=12 doctor rows, got {rows}")
        check(
            {"Internet", "Python"} <= {i.name for i in app.screen._issues},
            "Internet/Python were not folded in from the old Status screen",
        )
        check(
            "Desktop:" in app.screen._info and "Cache:" in app.screen._info,
            f"Status's running/cache/version facts were not folded in: {app.screen._info!r}",
        )

        fixable = [i for i in app.screen._issues if not i.ok and i.fix is not None]
        disabled = app.screen.query_one("#fix", Button).disabled
        check(
            disabled == (not fixable),
            f"Fix button disabled={disabled} but {len(fixable)} issues are fixable",
        )
        await pilot.click("#back")
        await pilot.pause()
        check(isinstance(app.screen, MainScreen), f"got {app.screen!r}")


async def test_destructive_actions_are_gated() -> None:
    app = PDMApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()

        for button in ("#reset", "#cache"):
            await pilot.click(button)
            await pilot.pause()
            check(isinstance(app.screen, ConfirmScreen), f"{button} skipped confirmation")
            await pilot.click("#cancel")
            await pilot.pause()
            check(isinstance(app.screen, MainScreen), f"cancel on {button} did not return")


async def test_uninstall_is_gated() -> None:
    app = PDMApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#settings")
        await pilot.pause()

        await pilot.click("#uninstall")
        await pilot.pause()
        check(isinstance(app.screen, ConfirmScreen), "#uninstall skipped confirmation")
        await pilot.click("#cancel")
        await pilot.pause()
        check(isinstance(app.screen, SettingsScreen), "cancel on #uninstall did not return")


async def test_narrow_terminal_layout() -> None:
    """Every control must stay on screen at phone widths.

    Regression: a fifth button on Doctor pushed Back off screen, and it only
    surfaced as an OutOfBounds click at 80 columns — a real phone is narrower.
    """
    for width in (40, 45, 60):
        app = PDMApp()
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()

            screen = app.screen
            for button in screen.query(Button):
                check(
                    screen.region.contains_region(button.region),
                    f"{button.id} is off screen at {width} columns: "
                    f"{button.region} outside {screen.region}",
                )
                check(
                    button.region.width >= len(str(button.label)),
                    f"{button.id} label '{button.label}' is wider than its "
                    f"{button.region.width}-column button at {width} columns",
                )

            await pilot.click("#doctor")
            await pilot.pause()
            await wait_for_rows(pilot, app, "#doctor-table")
            # Raises OutOfBounds if the control is not on screen.
            await pilot.click("#copy")
            await pilot.pause()

            await pilot.click("#dupes")
            await pilot.pause()
            check(isinstance(app.screen, DupesScreen), f"got {app.screen!r}")
            await pilot.click("#copy")
            await pilot.pause()
            await pilot.click("#back")
            await pilot.pause()
            check(isinstance(app.screen, DoctorScreen), "dupes did not return")

            await pilot.click("#back")
            await pilot.pause()
            check(
                isinstance(app.screen, MainScreen),
                f"Doctor did not return at {width} columns",
            )

            # Raises OutOfBounds if Settings or its Back button is not
            # reachable — three Select widgets stacked in a VerticalScroll,
            # the same shape that pushed Back off screen on Repos once.
            await pilot.click("#settings")
            await pilot.pause()
            check(isinstance(app.screen, SettingsScreen), f"got {app.screen!r}")
            await pilot.click("#back")
            await pilot.pause()
            check(
                isinstance(app.screen, MainScreen),
                f"Settings did not return at {width} columns",
            )


TESTS = [
    test_tui_navigation,
    test_destructive_actions_are_gated,
    test_uninstall_is_gated,
    test_narrow_terminal_layout,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
