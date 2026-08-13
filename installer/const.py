"""Shared constants — no imports beyond stdlib, safe for any module."""

import os

TERMUX_PREFIX = "/data/data/com.termux/files/usr"

CONTAINER_NAME = "pdm"
ADMIN_USER = "admin"

# GHCR first: no pull-rate limit for a public package, which matters more
# than raw speed here — most installs happen over mobile data behind
# carrier-grade NAT, where a Docker Hub anonymous-pull limit (10/hour per
# IP as of 2026) is shared with every other subscriber on the same IP, not
# just this tool's own pulls. Docker Hub is faster for some ISPs (ghcr.io
# routes through Fastly's AnyCast CDN, which some ISPs peer with poorly),
# so it stays as an automatic fallback rather than being dropped.
IMAGE_REF = "ghcr.io/arinadi/proot-distro-manager:latest"
IMAGE_REF_FALLBACK = "arinadi/proot-distro-manager:latest"

PROOT_ROOT = f"{TERMUX_PREFIX}/var/lib/proot-distro"
PROOT_DIR = f"{PROOT_ROOT}/containers/{CONTAINER_NAME}"
CACHE_DIR = f"{PROOT_ROOT}/cache"

REPO_URL = "https://github.com/arinadi/proot-distro-manager.git"
REPO_DIR = os.path.expanduser("~/pdm")

# $PREFIX/bin is the whole of Termux's default PATH, so a launcher linked
# there needs no shell startup file. ~/bin is the fallback off Termux.
PREFIX_BIN = f"{TERMUX_PREFIX}/bin"
HOME_BIN = os.path.expanduser("~/bin")
LAUNCHER_SRC = os.path.join(REPO_DIR, "pdm")

# Termux writes here; TMPDIR is set in a normal Termux session but not always
# in the environment a launcher inherits, so fall back explicitly.
TMPDIR = os.environ.get("TMPDIR", f"{TERMUX_PREFIX}/tmp")

# Home-directory backups, next to REPO_DIR on Termux's own storage — outside
# the container, so a Reset (which deletes the whole rootfs) cannot take a
# backup down with it.
BACKUP_DIR = os.path.expanduser("~/pdm-backups")
