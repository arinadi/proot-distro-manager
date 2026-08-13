#!/data/data/com.termux/files/usr/bin/bash
# ═══════════════════════════════════════════════════════════════
# PDM Dev — Docker development wrapper
#  Usage: dev.sh [setup|start|shell|stop|status]
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

PROOT_DISTRO="debian"
ADMIN="admin"
PROJECT_DIR="/data/data/com.termux/files/home/pdm"

# ── Helpers ──────────────────────────────────────────────────
proot_exec() {
    proot-distro login "$PROOT_DISTRO" -- su - "$ADMIN" -c "$*"
}

proot_exec_root() {
    proot-distro login "$PROOT_DISTRO" -- bash -c "$*"
}

# ── Commands ─────────────────────────────────────────────────
cmd_setup() {
    echo ">>> [1/4] Copying project into proot..."
    proot_exec_root "mkdir -p /home/$ADMIN/pdm"
    proot_exec_root "rsync -a --exclude='.git' --exclude='node_modules' $PROJECT_DIR/ /home/$ADMIN/pdm/"

    echo ">>> [2/4] Setting permissions..."
    proot_exec_root "chown -R $ADMIN:$ADMIN /home/$ADMIN/pdm"

    echo ">>> [3/4] Installing locale..."
    proot_exec_root "sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen" 2>/dev/null || true

    echo ">>> [4/4] Testing MATE..."
    proot_exec "echo 'MATE packages:' && dpkg -l | grep mate-desktop | head -3"

    echo ""
    echo "╔═══════════════════════════════════════╗"
    echo "║  ✅ Setup complete!                   ║"
    echo "║  Run: dev.sh start                    ║"
    echo "╚═══════════════════════════════════════╝"
}

cmd_start() {
    echo ">>> Starting MATE desktop..."
    echo "  Display: $DISPLAY"

    # Start PulseAudio in proot
    proot_exec "pulseaudio --start --exit-idle-time=-1 2>/dev/null" || true

    # Launch MATE session
    proot_exec "
        export DISPLAY=$DISPLAY
        export PULSE_SERVER=tcp:127.0.0.1:4713
        export NO_AT_BRIDGE=1
        export LIBGL_ALWAYS_SOFTWARE=1
        rm -f /tmp/dbus-* 2>/dev/null
        dbus-launch --exit-with-session mate-session
    "
}

cmd_shell() {
    echo ">>> Entering proot shell (user: $ADMIN)..."
    proot_exec "bash"
}

cmd_stop() {
    echo ">>> Stopping MATE session..."
    proot_exec_root "pkill -f mate-session 2>/dev/null || true"
    proot_exec_root "pkill -f dbus-launch 2>/dev/null || true"
    echo "  ✓ Stopped"
}

cmd_status() {
    echo ">>> arinanoX Dev Status"
    echo ""

    # Check proot container
    if proot-distro list 2>/dev/null | grep -q "$PROOT_DISTRO"; then
        echo "  Proot:  ✓ $PROOT_DISTRO installed"
    else
        echo "  Proot:  ✗ $PROOT_DISTRO not found"
    fi

    # Check MATE
    if proot_exec "dpkg -l mate-desktop 2>/dev/null | grep -q ^ii"; then
        echo "  MATE:   ✓ installed"
    else
        echo "  MATE:   ✗ not installed"
    fi

    # Check admin user
    if proot_exec_root "id $ADMIN 2>/dev/null" &>/dev/null; then
        echo "  User:   ✓ $ADMIN"
    else
        echo "  User:   ✗ $ADMIN not found"
    fi

    # Check processes
    MATE_PROCS=$(proot_exec_root "pgrep -c mate-session 2>/dev/null" || echo "0")
    if [ "$MATE_PROCS" -gt 0 ]; then
        echo "  MATE:   ✓ running ($MATE_PROCS processes)"
    else
        echo "  MATE:   • not running"
    fi

    echo ""
}

cmd_help() {
    echo "arinanoX Dev — Docker/Podman development environment"
    echo ""
    echo "Usage: dev.sh <command>"
    echo ""
    echo "Commands:"
    echo "  setup    Copy project + setup admin user in proot"
    echo "  start    Launch MATE desktop session"
    echo "  shell    Enter interactive proot shell"
    echo "  stop     Stop MATE session"
    echo "  status   Show environment status"
    echo "  help     Show this help"
    echo ""
    echo "Host requirements (Windows + WSLg):"
    echo "  - Podman Desktop or Docker Desktop installed"
    echo "  - WSLg enabled (Windows 11) — X11 auto-forwarded"
    echo ""
    echo "Host requirements (Linux/Mac):"
    echo "  - VcXsrv or X410 running (Display :0)"
    echo "  - export DISPLAY=:0"
}

# ── Main ─────────────────────────────────────────────────────
case "${1:-help}" in
    setup)  cmd_setup ;;
    start)  cmd_start ;;
    shell)  cmd_shell ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    help|*) cmd_help ;;
esac
