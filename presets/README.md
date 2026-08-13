# Presets

Drop a `.tar.gz` from the **Backup** screen here (same format the app
already writes to `~/pdm-backups` — this is just a second, repo-tracked
location for one you want carried across installs) and commit it.

`install.sh` → `install.py` restores it automatically — but **only** the
first time it pulls the default container on this device. If a container
is already there, install.py skips straight past both the pull and the
restore, every time you re-run it. That is not a corner case: install.py
says itself it is "safe to re-run", and re-running it against a container
you have already been using for months is the normal case, not the rare
one. Applying an old preset on top of that would silently overwrite
whatever you've changed since.

That is also why this is install.py-only. Nothing in the TUI — Start,
Doctor's Fix, Reset, **Manual Install** — ever touches `presets/`. All of
those can end up pulling a container too, but "no container right now"
almost always means something else already went wrong (a bad Reset, a
manual `proot-distro remove`), not "this is fresh, seed it." Manual
Install in particular is an explicit choice to install something *other*
than the default — applying a preset built for that default on top of
whatever the user just picked would be actively wrong, not just
unnecessary. Only install.py's own first pull knows it's genuinely fresh,
because it just watched it happen.

If more than one `.tar.gz` ends up here, the newest by modification time
wins; the rest are ignored, not merged.

This is your desktop config (dotfiles, panel layout, window manager theme,
editor settings, the Firefox profile) — not a copy PDM maintains or
updates on its own, and not a substitute for the TUI's own Backup/Restore,
which stays a separate, manual, explicit action on `~/pdm-backups`. See
[`installer/presets.py`](../installer/presets.py) and `restore_preset()` in
[`install.py`](../install.py) for how it's applied.
