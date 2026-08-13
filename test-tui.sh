#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  PDM TUI Test Script (Podman)
#
#  Purpose: Test TUI locally on PC using Podman
#  Usage: bash test-tui.sh
#
#  NOTE: This is for LOCAL TESTING ONLY.
#        Actual install happens on Android (Termux).
#
#  What this tests:
#    - TUI welcome screen
#    - Pre-flight checks
#    - Menu navigation
#    - GPU detection
#
#  What this does NOT test:
#    - Actual desktop launch (no X11 in container)
#    - proot-distro (not available in container)
#    - Termux packages (pkg not available)
# ═══════════════════════════════════════════════════════════════
set -e

IMAGE="pdm-dev"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# On Windows (Git Bash / MSYS), convert to Windows path for Podman
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    PROJECT_DIR="$(cygpath -w "$PROJECT_DIR")"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  PDM TUI Test (Podman)"
echo "═══════════════════════════════════════════"
echo ""

# Check Podman
if ! command -v podman &>/dev/null; then
    echo "✗ Podman not found. Install: https://podman-desktop.io/"
    exit 1
fi

# Check Podman machine
if ! podman machine info &>/dev/null; then
    echo ">>> Starting Podman machine..."
    podman machine init 2>/dev/null || true
    podman machine start
fi

echo ">>> Building dev container..."
podman build -t "$IMAGE" -f docker/dev/Dockerfile docker/dev

echo ""
echo ">>> Running TUI test..."
echo "    (Project mounted at /data/data/com.termux/files/home/pdm)"
echo "    Press Ctrl+C to exit"
echo ""

podman run -it --rm \
    -v "${PROJECT_DIR}:/data/data/com.termux/files/home/pdm" \
    "$IMAGE" bash -c "
        cd /data/data/com.termux/files/home/pdm
        pip install rich requests --quiet --break-system-packages 2>/dev/null || \
        pip install rich requests --quiet --user 2>/dev/null || true
        python install.py
    "
