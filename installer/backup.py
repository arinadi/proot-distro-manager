"""Back up and restore the container's admin home directory.

"User data" here means /home/admin: dotfiles, the Firefox profile, editor
settings, the XFCE panel layout — everything a person configured by hand,
as opposed to what apt already tracks and reinstalls on its own.

Both directions go through the container rather than touching the rootfs
directly. proot fakes ownership and, for what the filesystem underneath
cannot represent, backs files with a hardlink-emulation store (rootfs/.l2s)
— a host-side copy of those raw files would not reproduce what the
container itself sees. tar run through a proot login sees the same logical
view any other program inside the container does.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from datetime import datetime, timezone
from typing import Callable, NamedTuple

from .const import ADMIN_USER, BACKUP_DIR, TMPDIR
from .system import container_command, is_installed, stream_cmd, write_container_script

Log = Callable[[str], None]

HOME_IN_CONTAINER = f"/home/{ADMIN_USER}"

BACKUP_SCRIPT = f"""#!/bin/bash
# $1 = destination path, in the shared tmp so the host can see it after.
set -e
tar czf "$1" -C /home {ADMIN_USER}
"""

RESTORE_SCRIPT = f"""#!/bin/bash
# $1 = source archive, staged into the shared tmp beforehand.
set -e
rm -rf /home/{ADMIN_USER}.bak
[ -d /home/{ADMIN_USER} ] && mv /home/{ADMIN_USER} /home/{ADMIN_USER}.bak
tar xzf "$1" -C /home
chown -R {ADMIN_USER}:{ADMIN_USER} /home/{ADMIN_USER}
echo "the previous home was kept at /home/{ADMIN_USER}.bak"
"""


class Backup(NamedTuple):
    name: str
    path: str
    size_bytes: int
    created: datetime


def list_backups() -> list[Backup]:
    try:
        entries = os.listdir(BACKUP_DIR)
    except OSError:
        return []

    backups = []
    for name in entries:
        if not name.endswith(".tar.gz"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        backups.append(
            Backup(
                name, path, stat.st_size,
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )
    return sorted(backups, key=lambda b: b.created, reverse=True)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def create_backup(log: Log) -> bool:
    if not is_installed():
        log("[red]No container to back up.[/red]")
        return False
    if not write_container_script("pdm-backup.sh", BACKUP_SCRIPT):
        log("[red]Could not write the backup script.[/red]")
        return False

    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as e:
        log(f"[red]Could not create {BACKUP_DIR}: {e}[/red]")
        return False

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"home-{stamp}.tar.gz"

    log(f"Archiving {HOME_IN_CONTAINER}...")
    rc = stream_cmd(
        f"{container_command('pdm-backup.sh')} /tmp/{name}", log, timeout=1800
    )
    if rc != 0:
        log("[red]tar failed — see the output above.[/red]")
        return False

    # Written into the shared tmp first, moved into BACKUP_DIR only once
    # complete — an interrupted tar this way never leaves a half-written
    # file where list_backups() would find it.
    host_tmp = os.path.join(TMPDIR, name)
    final_path = os.path.join(BACKUP_DIR, name)
    try:
        shutil.move(host_tmp, final_path)
    except OSError as e:
        log(f"[red]Could not move the archive into {BACKUP_DIR}: {e}[/red]")
        return False

    log(f"Saved {final_path} ({human_size(os.path.getsize(final_path))})")
    return True


def restore_backup(backup: Backup, log: Log) -> bool:
    if not is_installed():
        log("[red]No container to restore into.[/red]")
        return False
    if not write_container_script("pdm-restore.sh", RESTORE_SCRIPT):
        log("[red]Could not write the restore script.[/red]")
        return False

    host_tmp = os.path.join(TMPDIR, backup.name)
    try:
        os.makedirs(TMPDIR, exist_ok=True)
        shutil.copy2(backup.path, host_tmp)
    except OSError as e:
        log(f"[red]Could not stage the archive: {e}[/red]")
        return False

    log(f"Restoring {backup.name} into {HOME_IN_CONTAINER}...")
    rc = stream_cmd(
        f"{container_command('pdm-restore.sh')} /tmp/{backup.name}", log, timeout=1800
    )
    with contextlib.suppress(OSError):
        os.remove(host_tmp)

    if rc != 0:
        log(
            "[red]tar failed — see the output above. If a previous home was "
            f"replaced, it is kept at {HOME_IN_CONTAINER}.bak inside the "
            "container.[/red]"
        )
        return False

    log("Restore complete.")
    return True


def delete_backup(backup: Backup, log: Log) -> bool:
    try:
        os.remove(backup.path)
    except OSError as e:
        log(f"[red]Could not delete {backup.path}: {e}[/red]")
        return False
    log(f"Deleted {backup.path}")
    return True
