FROM termux/termux-docker

# ═══════════════════════════════════════════════
#  arinanoX Dev — Termux packages only (TUI test)
# ═══════════════════════════════════════════════

# ── Prepare home dir (root) ─────────────────────
# /data/data/ is 771 root:root — UID 1000 can't traverse it.
RUN mkdir -p /data/data/com.termux/files/home \
    && chmod 755 /data/data /data/data/com.termux \
      /data/data/com.termux/files /data/data/com.termux/files/home

# ── Termux packages (as UID 1000) ───────────────
USER 1000:1000
RUN pkg update -y && pkg install -y \
        proot-distro \
        x11-repo \
        pulseaudio \
        rsync \
        python3 \
        curl \
        git \
        openssh \
    && pkg clean

# ── Back to root for runtime ────────────────────
USER root:root

WORKDIR /data/data/com.termux/files/home

CMD ["/bin/bash"]
