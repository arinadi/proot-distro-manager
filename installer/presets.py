"""Restore a repo-tracked config preset after a fresh Debian install.

A preset is just a Backup archive (see backup.py) committed into presets/
instead of living in ~/pdm-backups. This finds the file and hands it to
the existing restore path — no separate restore logic to keep in sync with
what Backup actually does.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable

from .backup import Backup, restore_backup
from .const import REPO_DIR

Log = Callable[[str], None]

PRESETS_DIR = os.path.join(REPO_DIR, "presets")


def find_preset() -> Backup | None:
    """The newest .tar.gz in presets/, or None if there isn't one."""
    try:
        names = [n for n in os.listdir(PRESETS_DIR) if n.endswith(".tar.gz")]
    except OSError:
        return None
    if not names:
        return None

    stats = []
    for name in names:
        try:
            stats.append((name, os.stat(os.path.join(PRESETS_DIR, name))))
        except OSError:
            continue
    if not stats:
        return None

    name, stat = max(stats, key=lambda pair: pair[1].st_mtime)
    return Backup(
        name,
        os.path.join(PRESETS_DIR, name),
        stat.st_size,
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def restore_preset(log: Log) -> bool:
    preset = find_preset()
    if preset is None:
        log("[yellow]No preset in presets/ — skipping.[/yellow]")
        return False
    log(f"Restoring preset {preset.name}...")
    return restore_backup(preset, log)
