#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  PDM — bootstrap entry point
#  Usage: curl -sL https://raw.githubusercontent.com/arinadi/proot-distro-manager/main/install.sh | bash
#
#  Gets git and Python onto the machine and the repo onto disk, then
#  hands over to install.py for the actual install. Keep this file
#  boring: it is the one thing that cannot assume anything.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_URL="https://github.com/arinadi/proot-distro-manager.git"
REPO_DIR="$HOME/pdm"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${CYAN}>>>${NC} $1"; }
ok()   { echo -e "${GREEN}  ok${NC} $1"; }
die()  { echo -e "${RED}  failed${NC} $1"; exit 1; }

pkg_install() {
    if command -v pkg &>/dev/null; then
        pkg install -y "$@"
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y "$@"
    elif command -v brew &>/dev/null; then
        brew install "$@"
    else
        die "no supported package manager; install manually: $*"
    fi
}

ensure_git() {
    command -v git &>/dev/null || { info "Installing git"; pkg_install git; }
    command -v git &>/dev/null || die "git is still missing"
    ok "git $(git --version | awk '{print $3}')"
}

ensure_python() {
    if command -v python3 &>/dev/null; then
        PYTHON="python3"
    elif command -v python &>/dev/null; then
        PYTHON="python"
    else
        info "Installing Python"
        pkg_install python
        PYTHON="python3"
    fi
    command -v "$PYTHON" &>/dev/null || die "Python is still missing"

    if ! $PYTHON -m pip --version &>/dev/null; then
        info "Installing pip"
        $PYTHON -m ensurepip --upgrade &>/dev/null \
            || curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON \
            || die "could not install pip"
    fi
    ok "Python $($PYTHON --version 2>&1 | awk '{print $2}') with pip"
}

sync_repo() {
    if [ -d "$REPO_DIR/.git" ]; then
        info "Updating repository"
        git -C "$REPO_DIR" pull --ff-only || {
            info "Fast-forward failed, resetting to origin/main"
            git -C "$REPO_DIR" fetch origin main
            git -C "$REPO_DIR" reset --hard origin/main
        } || die "could not update $REPO_DIR"
    else
        info "Cloning repository"
        git clone --depth 1 "$REPO_URL" "$REPO_DIR" || die "clone failed"
    fi
    [ -f "$REPO_DIR/install.py" ] || die "$REPO_DIR/install.py is missing"
    ok "$REPO_DIR"
}

main() {
    echo
    echo -e "${CYAN}===========================================${NC}"
    echo -e "${CYAN}  PDM Bootstrap${NC}"
    echo -e "${CYAN}===========================================${NC}"
    echo
    ensure_git
    ensure_python
    sync_repo
    exec "$PYTHON" "$REPO_DIR/install.py"
}

main "$@"
