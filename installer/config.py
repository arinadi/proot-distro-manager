"""User configuration, kept in a .env file next to the repo.

Everything the app decides about a particular device belongs here rather than
scattered across purpose-built files: the GPU configuration a benchmark chose,
and whatever settings follow. Plain KEY=value so it can be read and edited
without the app.

Untracked. It describes one device and would be wrong on any other.
"""

from __future__ import annotations

import os

from .const import REPO_DIR

CONFIG_PATH = os.path.join(REPO_DIR, ".env")

HEADER = """# PDM — per-device settings.
#
# Written by the app and safe to edit by hand. Not tracked by git: these
# values describe this device and would be wrong on another.
"""


def load() -> dict[str, str]:
    """Read the file. A missing or unreadable file is an empty config."""
    values: dict[str, str] = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return values

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def get(key: str, default: str | None = None) -> str | None:
    return load().get(key, default)


def set_value(key: str, value: str) -> bool:
    """Write one key, leaving the others alone."""
    values = load()
    values[key] = value
    return _write(values)


def unset(key: str) -> bool:
    values = load()
    values.pop(key, None)
    return _write(values)


def _write(values: dict[str, str]) -> bool:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADER)
            for key in sorted(values):
                f.write(f"{key}={values[key]}\n")
        return True
    except OSError:
        return False
