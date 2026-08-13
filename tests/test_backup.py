"""installer/backup.py: the Backup screen (Backup/Restore/Delete on
~/pdm-backups). Presets are a separate, install.py-only concept — see
test_install.py — not part of this screen.

    python tests/test_backup.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import backup
from installer.app import BackupScreen, ConfirmScreen, MainScreen, PDMApp


def test_backup_list_and_human_size() -> None:
    """list_backups() must only see its own archives, newest first, and
    survive a BACKUP_DIR that does not exist yet."""
    check(backup.list_backups() == [], "a missing BACKUP_DIR must read as no backups, not an error")

    for byte_count, expected in ((0, "0B"), (999, "999B"), (2048, "2.0KB"), (5 * 1024**3, "5.0GB")):
        check(
            backup.human_size(byte_count) == expected,
            f"human_size({byte_count}) = {backup.human_size(byte_count)!r}, expected {expected!r}",
        )

    fake_dir = tempfile.mkdtemp()
    original_dir = backup.BACKUP_DIR
    backup.BACKUP_DIR = fake_dir
    try:
        for name, age in (("home-20240101-000000.tar.gz", 200), ("home-20240102-000000.tar.gz", 100)):
            path = os.path.join(fake_dir, name)
            with open(path, "wb") as f:
                f.write(b"0" * 1024)
            then = time.time() - age
            os.utime(path, (then, then))
        # Not a backup this tool made — must not show up or be touchable.
        with open(os.path.join(fake_dir, "notes.txt"), "w") as f:
            f.write("hello")

        found = backup.list_backups()
        check(len(found) == 2, f"expected 2 backups, got {len(found)}: {found}")
        check(found[0].name == "home-20240102-000000.tar.gz", "not sorted newest first")
        check(all(b.name.endswith(".tar.gz") for b in found), "a non-archive file was listed")
        check(found[0].size_bytes == 1024, "size was not read from the file")
    finally:
        backup.BACKUP_DIR = original_dir


async def test_backup_screen() -> None:
    """Backup/Restore/Delete all need a highlighted row; without one they
    must warn rather than crash or silently do nothing. Restore is the one
    that can undo real work if it lands on the wrong archive, so — like
    Repos and Mirror — the pick is named on the note line as soon as a row
    is highlighted, not only inside the confirm dialog that follows it."""
    fake_dir = tempfile.mkdtemp()
    original_dir = backup.BACKUP_DIR
    backup.BACKUP_DIR = fake_dir
    try:
        app = PDMApp()
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup")
            await pilot.pause()
            check(isinstance(app.screen, BackupScreen), f"got {app.screen!r}")

            # No backups yet: Restore/Delete must warn, not crash or guess.
            for button in ("#restore", "#delete"):
                await pilot.click(button)
                await pilot.pause()
                check(
                    isinstance(app.screen, BackupScreen),
                    f"{button} with nothing to select left the screen",
                )

            path = os.path.join(fake_dir, "home-20240101-000000.tar.gz")
            with open(path, "wb") as f:
                f.write(b"0" * 2048)
            app.screen._fill()
            await pilot.pause()

            # DataTable highlights row 0 by default the moment rows exist.
            check(
                app.screen.status_text.startswith("Selected:"),
                f"the default row highlight did not update the note: {app.screen.status_text!r}",
            )
            check(
                "home-20240101-000000.tar.gz" in app.screen.status_text,
                f"the note did not name the highlighted archive: {app.screen.status_text!r}",
            )

            await pilot.click("#restore")
            await pilot.pause()
            check(isinstance(app.screen, ConfirmScreen), "Restore with a row selected did not confirm")
            await pilot.click("#cancel")
            await pilot.pause()

            await pilot.click("#back")
            await pilot.pause()
            check(isinstance(app.screen, MainScreen), "back from Backup did not return")
    finally:
        backup.BACKUP_DIR = original_dir


TESTS = [
    test_backup_list_and_human_size,
    test_backup_screen,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
