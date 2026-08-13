"""Desktop lifecycle: PulseAudio → virgl → X11 → Xfce4, and the reverse on stop.

Every function here blocks and takes a `log` callback so the caller decides
where output goes — a Textual RichLog, or plain print from the installer.
"""

import os
import socket as sock
import subprocess
import time

from . import audio, bench, config
from .const import ADMIN_USER, CONTAINER_NAME, PROOT_DIR, REPO_DIR, TMPDIR
from .system import container_command, run_cmd, write_container_script

ANGLE_DIR = "/data/data/com.termux/files/usr/opt/angle-android"
XFCE_LOG = os.path.join(REPO_DIR, "xfce4.log")
VIRGL_LOG = os.path.join(REPO_DIR, "virgl.log")

# termux-x11's own draw path. Some devices show a black screen (fixed by
# -legacy-drawing, which skips the modern Android hardware-buffer path) or
# swapped color channels (fixed by -force-bgra, when the device's native
# buffer format differs from what the X server assumes) — both reported
# upstream, both device-specific, and neither detectable from here: nothing
# short of a person looking at the screen can tell which one a device needs.
DRAW_PATH_KEY = "TERMUX_X11_DRAW_PATH"
DRAW_PATHS = {
    "normal": "",
    "legacy-drawing": "-legacy-drawing",
    "force-bgra": "-force-bgra",
    "legacy-drawing+force-bgra": "-legacy-drawing -force-bgra",
}
DEFAULT_DRAW_PATH = "normal"


def load_draw_path() -> str:
    name = config.get(DRAW_PATH_KEY)
    return name if name in DRAW_PATHS else DEFAULT_DRAW_PATH


def save_draw_path(name: str) -> bool:
    if name not in DRAW_PATHS:
        return False
    return config.set_value(DRAW_PATH_KEY, name)

# Set inside the container when a virgl server is actually up. Accelerating
# GL needs both halves: the server in Termux and these on the client side.
# Without them the session silently uses llvmpipe no matter what is running
# outside. Values from the virglrenderer tutorial on ivonblog.com.
GPU_EXPORTS = """export GALLIUM_DRIVER=virpipe
export MESA_GL_VERSION_OVERRIDE=4.0"""

# Leftovers worth sweeping only after the proot tree is gone. Deliberately
# narrow: an earlier version matched "dbus-" and would kill any process on the
# device with that in its command line, including another proot-distro's.
SURVIVOR_PATTERN = (
    f"proot.*{CONTAINER_NAME}|xfce4-session|startxfce4|xfwm4|xfdesktop|"
    "termux-x11|virgl_test_server"
)

def _noop(_message: str) -> None:
    pass


def is_running() -> bool:
    """True when an Xfce4 session is alive."""
    rc, _ = run_cmd("pgrep -f 'xfce4-session|startxfce4'")
    return rc == 0


# ── Stop ───────────────────────────────────────────────────


def _wait_gone(pattern: str, seconds: float) -> bool:
    """Poll until nothing matches `pattern`, or the time runs out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rc, _ = run_cmd(f"pgrep -f '{pattern}'")
        if rc != 0:
            return True
        time.sleep(0.3)
    rc, _ = run_cmd(f"pgrep -f '{pattern}'")
    return rc != 0


def stop_desktop(log=_noop) -> bool:
    """Stop the desktop, innermost first, and report whether it worked.

    Order matters and used to be inverted: the Android X server was killed
    first, which pulls the display out from under the session. The container
    goes first now and the display last.
    """
    # 1. Kill the container. proot runs with --kill-on-exit, so taking the
    #    proot process takes every process inside with it — which is why the
    #    long list of xfce4/thunar/dbus patterns this used to carry is neither
    #    needed nor safe.
    #
    #    There is no polite logout step. xfce4-session-logout talks to the
    #    session manager over its D-Bus and ICE sockets, and a fresh
    #    proot-distro login has neither — no DBUS_SESSION_BUS_ADDRESS pointing
    #    at the running session — so the request never arrived and every stop
    #    simply waited eight seconds before killing anyway. TERM first still
    #    gives the tree a chance to exit on its own.
    log("Stopping the container...")
    run_cmd(f"pkill -TERM -f 'proot.*{CONTAINER_NAME}' 2>/dev/null")
    if not _wait_gone(f"proot.*{CONTAINER_NAME}", 5):
        run_cmd(f"pkill -9 -f 'proot.*{CONTAINER_NAME}' 2>/dev/null")
        _wait_gone(f"proot.*{CONTAINER_NAME}", 3)

    # 2. Now the display can go.
    log("Stopping X11...")
    run_cmd("pkill -TERM -f termux-x11 2>/dev/null")
    _wait_gone("termux-x11", 3)
    run_cmd("pkill -9 -f termux-x11 2>/dev/null")
    run_cmd("am force-stop com.termux.x11 2>/dev/null")

    # 3. Audio and the renderer. `pulseaudio --kill` is the supported way and
    #    unloads its own modules; the old code unloaded module-null-sink,
    #    which was never loaded, and left the aaudio/sles sinks behind.
    log("Stopping audio and renderer...")
    run_cmd("pulseaudio --kill 2>/dev/null")
    run_cmd("pkill -TERM -f virgl_test_server 2>/dev/null")

    # 4. Anything that outlived its parent.
    rc, _ = run_cmd(f"pgrep -f '{SURVIVOR_PATTERN}'")
    if rc == 0:
        log("Sweeping survivors...")
        run_cmd(f"pkill -9 -f '{SURVIVOR_PATTERN}' 2>/dev/null")
        time.sleep(0.5)

    # 5. Sockets and runtime dirs. With --shared-tmp the container's /tmp is
    #    this directory, so this is where the session's residue actually is;
    #    the rootfs paths below only matter for sessions started before that
    #    change.
    log("Cleaning sockets...")
    run_cmd(f"rm -rf {TMPDIR}/.X11-unix 2>/dev/null")
    run_cmd(f"rm -f {TMPDIR}/.X*-lock 2>/dev/null")
    run_cmd(f"rm -rf {TMPDIR}/dbus-* {TMPDIR}/.xfsm-ICE-* {TMPDIR}/.ICE-unix 2>/dev/null")
    run_cmd(f"rm -rf {TMPDIR}/runtime-* {TMPDIR}/pulse* 2>/dev/null")

    proot_tmp = os.path.join(PROOT_DIR, "rootfs/tmp")
    run_cmd(f"rm -rf {proot_tmp}/.X11-unix {proot_tmp}/.X*-lock 2>/dev/null")
    run_cmd(f"rm -rf {proot_tmp}/dbus-* {proot_tmp}/runtime-* {proot_tmp}/.xfsm-ICE-* 2>/dev/null")
    proot_home = os.path.join(PROOT_DIR, "rootfs/home/admin")
    run_cmd(f"rm -f {proot_home}/.ICEauthority {proot_home}/.Xauthority 2>/dev/null")
    run_cmd(f"rm -rf {proot_home}/.cache/sessions 2>/dev/null")

    run_cmd("termux-wake-unlock 2>/dev/null")

    # 6. Say what actually happened rather than always claiming success.
    rc, out = run_cmd(f"pgrep -af '{SURVIVOR_PATTERN}'")
    if rc == 0:
        log("[yellow]Stopped, but these survived:[/yellow]")
        for line in out.strip().splitlines()[:8]:
            log(f"  {line}")
        return False

    log("Stopped.")
    return True


# ── Start steps ────────────────────────────────────────────


def acquire_wake_lock(log=_noop) -> bool:
    """Hold a Termux wake lock so Android does not freeze the session."""
    rc, _ = run_cmd("termux-wake-lock 2>/dev/null")
    if rc != 0:
        log("  wake lock unavailable (install Termux:API to keep sessions alive)")
        return False
    return True


def virgl_running() -> bool:
    rc, _ = run_cmd("pgrep -f virgl_test_server")
    return rc == 0


def _launch_virgl(command: str, log) -> bool:
    """Start a virgl server and confirm it is still there a moment later.

    Output goes to a log rather than /dev/null. The old version discarded it
    and returned True unconditionally, so a server that died on startup was
    reported as success and the desktop quietly fell back to software
    rendering with nothing to say why.
    """
    os.makedirs(os.path.dirname(VIRGL_LOG), exist_ok=True)
    with open(VIRGL_LOG, "w") as f:
        subprocess.Popen(command, shell=True, stdout=f, stderr=f)

    time.sleep(1.5)
    if virgl_running():
        return True

    try:
        with open(VIRGL_LOG) as f:
            for line in f.read().strip().splitlines()[:3]:
                log(f"    {line}")
    except OSError:
        pass
    return False


def start_virgl(log=_noop) -> bool:
    """Start a virgl server, so the container can use the GPU for OpenGL.

    virglrenderer-android first: it works on most devices. The zink server
    can be faster but is reported to work only on Qualcomm hardware, so it
    is the fallback rather than the default.
    """
    if virgl_running():
        log("  already running")
        return True

    # A benchmarked profile beats detection order: what is fastest here was
    # measured on this device, not inferred from what usually wins.
    profile = bench.load_profile()
    if profile is not None:
        if profile.server is None:
            log(f"  benchmark chose {profile.name}; no renderer to start")
            return True
        log(f"  using the benchmarked profile: {profile.name}")
        if _launch_virgl(profile.server, log):
            log(f"  {profile.name} renderer is up")
            return True
        log("  it did not stay up, falling back to detection")

    if run_cmd("command -v virgl_test_server_android")[0] == 0:
        if _launch_virgl("virgl_test_server_android", log):
            log("  virgl_test_server_android is up")
            return True
        log("  virgl_test_server_android did not stay up")

    if run_cmd("command -v virgl_test_server")[0] == 0:
        zink = (
            "MESA_NO_ERROR=1 MESA_GL_VERSION_OVERRIDE=4.3COMPAT "
            "MESA_GLES_VERSION_OVERRIDE=3.2 GALLIUM_DRIVER=zink "
            "ZINK_DESCRIPTORS=lazy virgl_test_server "
            "--use-egl-surfaceless --use-gles"
        )
        if _launch_virgl(zink, log):
            log("  virgl_test_server with zink is up")
            return True
        log("  zink server did not stay up (expected off Qualcomm hardware)")

    log("  no virgl server — the desktop will use software rendering")
    log("  install it with: pkg install virglrenderer-android")
    return False


def prepare_ice_dir(log=_noop) -> bool:
    """Create /tmp/.ICE-unix, which nothing else on this stack does.

    xfce4-session listens for its children on an ICE socket in that
    directory. On a normal system systemd-tmpfiles creates it as root with
    mode 1777; with --shared-tmp the container's /tmp is the Termux temp
    directory, where it simply does not exist. Without it the session manager
    starts, accepts no registrations, and never launches xfwm4 or xfdesktop —
    which looks exactly like a desktop that failed to appear.

    Made on the host on purpose: proot reports Termux-owned files as root
    inside the container, which is the ownership ICE insists on and which
    could not be set from a --user admin session.
    """
    path = os.path.join(TMPDIR, ".ICE-unix")
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o1777)
    except OSError as e:
        log(f"  could not prepare {path}: {e}")
        return False
    return True


def start_x11(log=_noop) -> bool:
    # No kill or lock-file cleanup here either: stop_desktop owns that, and
    # it has already removed the socket directory and the .X0-lock.
    flags = DRAW_PATHS[load_draw_path()]
    subprocess.Popen(
        f"termux-x11 :0 -ac {flags}".strip(), shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    rc, _ = run_cmd("pgrep -f termux-x11")
    if rc != 0 or not os.path.exists(f"{TMPDIR}/.X11-unix/X0"):
        log("  termux-x11 failed to start")
        return False

    subprocess.Popen(
        "am start -n com.termux.x11/com.termux.x11.MainActivity", shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return True


def wait_for_x11(log=_noop) -> bool:
    """Wait until the socket actually accepts a connection, not just exists."""
    socket_path = f"{TMPDIR}/.X11-unix/X0"
    for i in range(50):
        if os.path.exists(socket_path):
            try:
                s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
                s.connect(socket_path)
                s.close()
                log(f"  ready in {(i + 1) * 100}ms")
                return True
            except OSError:
                pass
        time.sleep(0.1)

    log("  socket timeout, continuing anyway")
    return True


# Run inside the container as the admin user. Written to the rootfs and
# executed by path so no quoting has to survive the trip through proot-distro.
#
# The D-Bus wrapper matters: startxfce4 only starts a bus on its Wayland
# branch. With DISPLAY already set it prints "X server already running",
# sets prog=/bin/sh and hands off to xinitrc — so if nothing brings a session
# bus, xfce4-session has none. Every reference setup wraps the session in
# dbus-launch --exit-with-session for exactly this reason.
SESSION_SCRIPT = r"""#!/bin/bash
export DISPLAY=:0
export PULSE_SERVER=@PULSE@
export NO_AT_BRIDGE=1

export XDG_RUNTIME_DIR="/tmp/runtime-$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"

@GPU@

echo "starting as $(whoami), DISPLAY=$DISPLAY, XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
echo "ICE dir: $(ls -ld /tmp/.ICE-unix 2>&1)"

# xfce4-session directly, not startxfce4. With DISPLAY already set,
# startxfce4 only prints "X server already running", sets prog=/bin/sh and
# hands off to xinitrc — and on this setup the chain ends there, silently.
# A device probe showed xfce4-session itself still running after 8 seconds,
# so the session works; the wrapper is what loses it. The termux-x11 README
# recommends xfce4-session for the same reason.
if command -v xfce4-session >/dev/null 2>&1; then
    SESSION=xfce4-session
else
    SESSION=startxfce4
fi
echo "session binary: $SESSION"

# Not exec: keeping this shell alive means the exit status reaches the log.
# Without it xfce4.log simply stopped, which said nothing about why.
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] && command -v dbus-launch >/dev/null 2>&1; then
    echo "launching under dbus-launch"
    dbus-launch --exit-with-session "$SESSION"
else
    echo "using the existing session bus: ${DBUS_SESSION_BUS_ADDRESS:-none}"
    "$SESSION"
fi

echo "session exited with status $?"
"""

SESSION_SCRIPT_NAME = "pdm-session.sh"


def start_xfce4(log=_noop) -> bool:
    # --user admin rather than `su admin -c`: proot-distro sets HOME, USER and
    # the working directory itself, and su inside proot's faked root is where
    # this has repeatedly gone wrong.
    # --shared-tmp rather than --shared-x11: every reference setup uses it, and
    # it exposes the whole Termux tmp, which is where the X socket, the D-Bus
    # socket and XDG_RUNTIME_DIR all live.
    profile = bench.load_profile()
    if profile is not None:
        gpu = bench.client_exports(profile)
        log(f"  GPU profile from the benchmark: {profile.name}")
    elif virgl_running():
        gpu = GPU_EXPORTS
        log("  virgl server found, enabling GPU rendering in the session")
    else:
        gpu = "# no virgl server running, so software rendering"
        log("  no virgl server, the session will use software rendering")

    if not write_container_script(SESSION_SCRIPT_NAME, SESSION_SCRIPT.replace("@GPU@", gpu).replace("@PULSE@", audio.load_method().server)):
        log("  could not write the session script")
        return False

    cmd = (
        container_command(SESSION_SCRIPT_NAME, user=ADMIN_USER)
    )

    log(f"  {cmd}")

    os.makedirs(os.path.dirname(XFCE_LOG), exist_ok=True)
    with open(XFCE_LOG, "w") as f:
        subprocess.Popen(cmd, shell=True, stdout=f, stderr=f)

    # Poll rather than sleeping a fixed five seconds. A cold start on a slow
    # phone routinely takes longer, and declaring failure early sends people
    # debugging a desktop that was merely still coming up.
    for waited in range(1, 31):
        time.sleep(1)
        if is_running():
            log(f"  session up after {waited}s")
            return True
        if waited in (5, 10, 20):
            log(f"  still waiting ({waited}s)...")

    log("  no xfce4-session after 30s")
    return False


# Written into the container's filesystem and run by path. Passing this as a
# quoted -c argument through proot-distro does not survive: an earlier version
# used `tr '\n' ' '` inside an already single-quoted host string and the probe
# died with "unexpected EOF", taking the most useful half of the report with it.
CONTAINER_PROBE = r"""#!/bin/bash
echo "whoami:        $(whoami)"
echo "admin user:    $(id admin 2>&1)"
echo "startxfce4:    $(command -v startxfce4 || echo MISSING)"
echo "xfce4-session: $(command -v xfce4-session || echo MISSING)"
echo "dbus-launch:   $(command -v dbus-launch || echo MISSING)"
echo "xset:          $(command -v xset || echo MISSING)"
echo "DBUS address:  ${DBUS_SESSION_BUS_ADDRESS:-(unset)}"
echo "socket dir:"
ls -la /tmp/.X11-unix 2>&1 | sed 's/^/  /'
echo "ICE dir:"
ls -ld /tmp/.ICE-unix 2>&1 | sed 's/^/  /'

export DISPLAY=:0
echo "xset q:"
xset q 2>&1 | head -3 | sed 's/^/  /'

echo "already running inside the container:"
pgrep -a 'xfce4-session|xfwm4|xfdesktop' 2>&1 | sed 's/^/  /' || echo "  (none)"

# Mirrors the real start command, so its result means something. If a session
# is already up this will say so rather than starting a second one.
echo "--- dbus-launch xfce4-session foreground run, 8s ---"
su admin -c '
export DISPLAY=:0
export XDG_RUNTIME_DIR=/tmp/runtime-probe
mkdir -p $XDG_RUNTIME_DIR
chmod 0700 $XDG_RUNTIME_DIR
timeout 8 dbus-launch --exit-with-session xfce4-session
' >/tmp/pdm-session.out 2>&1
# Capture the session's own status, not sed's: piping straight into head
# reported the pipeline's exit code and always said 0.
status=$?
head -25 /tmp/pdm-session.out | sed 's/^/  /'
echo "--- exit: $status (124 = still alive when the 8s timeout fired) ---"
"""


def _run_container_probe() -> tuple[int, str]:
    """Run the probe script inside the container.

    The flags must match how the desktop is actually started, or the probe
    diagnoses its own environment instead. An earlier version logged in
    without sharing the X socket at all and duly reported "unable to open
    display" — indistinguishable from the failure it exists to explain.
    """
    if not write_container_script("pdm-probe.sh", CONTAINER_PROBE):
        return 1, "could not write the probe script"

    return run_cmd(
        container_command("pdm-probe.sh"),
        timeout=120,
    )


def collect_diagnostics(log=_noop) -> None:
    """Gather everything needed to explain why the desktop never appeared.

    The usual symptom is an X cursor on an empty root window, which means the
    server is up and nothing connected to it. That splits into host-side
    causes (no server, no socket) and container-side ones (no session binary,
    no admin user, or the display not reachable through the --shared-x11 bind
    mount). The `xset q` probe below is the one that separates them: if it
    answers from inside the container, the display path is fine and the fault
    is in the session itself.
    """
    log("")
    log("── Host ──────────────────────────────────────")
    host_probes = (
        ("termux-x11", "pgrep -af termux-x11"),
        ("X11 socket", f"ls -la {TMPDIR}/.X11-unix 2>&1"),
        ("X lock files", f"ls -la {TMPDIR}/.X*-lock 2>&1"),
        ("PulseAudio", "pgrep -af pulseaudio"),
        ("virgl", "pgrep -af virgl_test_server"),
        ("proot sessions", f"pgrep -af 'proot.*{CONTAINER_NAME}'"),
        ("DISPLAY", "echo \"${DISPLAY:-(unset)}\""),
    )
    for label, cmd in host_probes:
        _, out = run_cmd(cmd)
        text = out.strip() or "(nothing)"
        log(f"{label}:")
        for line in text.splitlines()[:8]:
            log(f"  {line}")

    log("")
    log("── Container ─────────────────────────────────")
    rc, out = _run_container_probe()
    text = out.strip()
    if not text:
        log(f"  could not enter the container (exit {rc})")
    for line in text.splitlines()[:40]:
        log(f"  {line}")

    log("")
    log("── xfce4.log ─────────────────────────────────")
    try:
        with open(XFCE_LOG) as f:
            content = f.read().strip()
    except OSError as e:
        content = f"(unreadable: {e})"
    if not content:
        content = "(empty — the session wrote nothing at all)"
    for line in content.splitlines()[-40:]:
        log(f"  {line}")

    log("")
    log("[dim]Reading this:[/dim]")
    log("[dim]  xset q answers        -> the display path works; the fault is[/dim]")
    log("[dim]                           in the session, see the foreground run[/dim]")
    log("[dim]  unable to open display -> the socket is not reaching the[/dim]")
    log("[dim]                           container; --shared-x11 or cleanup[/dim]")
    log("[dim]  'Killed' and nothing else -> Android killed the process; see[/dim]")
    log("[dim]                           the phantom process killer note in[/dim]")
    log("[dim]                           the README[/dim]")


START_STEPS = [
    ("Acquiring wake lock", acquire_wake_lock),
    ("Starting audio server", audio.ensure_server),
    ("Starting virgl renderer", start_virgl),
    ("Preparing ICE socket directory", prepare_ice_dir),
    ("Starting X11 server", start_x11),
    ("Waiting for X11 socket", wait_for_x11),
    ("Launching Xfce4 desktop", start_xfce4),
]


def start_desktop(log=_noop) -> bool:
    """Run the full start sequence on a known-clean slate.

    stop_desktop runs unconditionally rather than only when a session is
    detected. Leftovers outlive the session that made them — a stale .X0-lock
    or an orphaned proot is exactly what makes the next start fail — and
    is_running() only ever knew about xfce4-session. It costs a few pgrep
    calls when there is nothing to stop.
    """
    log("Clearing any previous session...")
    if not stop_desktop(log):
        log("[yellow]Some processes survived the stop; starting anyway.[/yellow]")
    log("")

    for name, step in START_STEPS:
        log(f"{name}...")
        try:
            ok = step(log)
        except Exception as e:
            log(f"  failed: {e}")
            ok = False
        log("  ok" if ok else "  warning")

    if is_running():
        return True

    log("")
    log("[bold]Desktop did not come up — collecting diagnostics[/bold]")
    collect_diagnostics(log)
    return False
