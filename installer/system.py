"""Subprocess and filesystem helpers.

Everything here blocks. Call it from a Textual thread worker, never from the
event loop.
"""

import contextlib
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone

from .const import (
    CONTAINER_NAME,
    HOME_BIN,
    IMAGE_REF,
    IMAGE_REF_FALLBACK,
    LAUNCHER_SRC,
    PREFIX_BIN,
    PROOT_DIR,
    REPO_DIR,
    TMPDIR,
)


def run_cmd(cmd: str, timeout: int = 60) -> tuple[int, str]:
    """Run a shell command, returning (returncode, combined output).

    Decoded as UTF-8 with replacement rather than the system locale: apt and
    pactl emit UTF-8, and a locale that cannot represent it turned readable
    output into an exception rather than text.
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
    except Exception as e:
        return 1, str(e)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the command and everything it spawned.

    proc.kill() only reaches the shell started by shell=True. Its children
    keep running and keep the stdout pipe open, so the reader never sees EOF
    and a timeout has no effect — which is exactly how the watchdog here
    failed the first time.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()


PROGRESS_INTERVAL = 1.0


def stream_cmd(cmd: str, log, timeout: int = 900) -> int:
    """Run a shell command, sending its output to `log`.

    Returns the exit code, or 1 if the command outran `timeout`.

    Output is split on carriage returns as well as newlines. Downloaders
    redraw a single progress line with \\r and never emit a newline until
    they finish, so a line-based reader shows nothing at all for the whole
    transfer — which is why pulling the image looked frozen.

    If `log` provides a `.progress()` method, carriage-return segments go
    there to be shown in place. Without one they are written to the log at
    most once every PROGRESS_INTERVAL seconds, so a long download reports
    movement instead of thousands of near-identical lines.

    The deadline is enforced by a watchdog timer rather than checked while
    reading. A stalled download produces no output at all, so a check that
    only runs per line never fires.
    """
    progress = getattr(log, "progress", None)

    # Binary pipe: os.read returns whatever has arrived rather than waiting
    # for a full buffer, which is what makes live progress possible.
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        # Own process group, so the whole tree can be killed at once.
        start_new_session=os.name != "nt",
    )

    timed_out = threading.Event()

    def _expire() -> None:
        timed_out.set()
        _kill_tree(proc)

    watchdog = threading.Timer(timeout, _expire)
    watchdog.start()

    last_progress = 0.0

    def emit(text: str, is_progress: bool) -> None:
        nonlocal last_progress
        text = text.rstrip()
        if not text:
            return
        if not is_progress:
            log(text)
            return
        if progress is not None:
            progress(text)
            return
        now = time.monotonic()
        if now - last_progress >= PROGRESS_INTERVAL:
            last_progress = now
            log(text)

    try:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        buffer = b""
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while True:
                position = min(
                    (i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0),
                    default=-1,
                )
                if position < 0:
                    break
                segment, terminator = buffer[:position], buffer[position : position + 1]
                buffer = buffer[position + 1 :]
                # \r\n is one break, not two.
                if terminator == b"\r" and buffer[:1] == b"\n":
                    buffer = buffer[1:]
                    terminator = b"\n"
                emit(segment.decode(errors="replace"), terminator == b"\r")

        if buffer:
            emit(buffer.decode(errors="replace"), False)
        rc = proc.wait()
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        log(f"[red]Timed out after {timeout}s[/red]")
        return 1
    return rc


def is_installed() -> bool:
    """True when the proot container rootfs exists."""
    return os.path.isdir(os.path.join(PROOT_DIR, "rootfs"))


def pull_image(log, timeout: int = 1800) -> bool:
    """Install the container, trying IMAGE_REF then IMAGE_REF_FALLBACK.

    GHCR first: it has no pull-rate limit for a public package, and most
    installs happen over mobile data behind carrier-grade NAT, where
    Docker Hub's anonymous limit is shared with every other subscriber on
    the same IP, not just this tool's own pulls. Docker Hub stays as the
    fallback for the ISPs where ghcr.io's Fastly-backed CDN routes badly —
    a real, reported failure mode, just not the one to default to.
    """
    for attempt, ref in enumerate((IMAGE_REF, IMAGE_REF_FALLBACK)):
        if attempt > 0:
            log(f"[yellow]{IMAGE_REF} did not work — trying {ref} instead.[/yellow]")
            # A failed install can leave a partial container behind, which
            # would fail the retry with "already exists" rather than
            # actually retrying it against the fallback registry.
            run_cmd(f"proot-distro remove {CONTAINER_NAME}", timeout=60)
        log(f"Pulling {ref}...")
        rc = stream_cmd(
            f"proot-distro install {ref} --name {CONTAINER_NAME}", log, timeout=timeout
        )
        if rc == 0 and is_installed():
            return True

    log("[red]Could not pull the image from either registry.[/red]")
    return False


# Allowlist, not a blocklist: an OCI reference is registry/org/name:tag, and
# this ends up in a shell command (proot-distro install <ref> --name ...),
# same exposure as Store's package-name validation (packages.SAFE_TERM) —
# just a different, wider charset since a ref has slashes and colons a
# package name never does.
SAFE_IMAGE_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]{0,127}$")


def valid_image_ref(ref: str) -> bool:
    return bool(SAFE_IMAGE_REF.match(ref.strip()))


def pull_custom_image(ref: str, log, timeout: int = 1800) -> bool:
    """Manual Install: replace whatever is in CONTAINER_NAME with any image
    proot-distro can pull, not just PDM's own prebuilt one. No GHCR/Docker
    Hub fallback pairing here — that only exists for PDM's own published
    image (see pull_image above); a manual install is exactly the one ref
    the user typed, nothing to fall back to."""
    if not valid_image_ref(ref):
        log(f"[red]Refusing to run: {ref!r} is not a valid image reference.[/red]")
        return False

    if is_installed():
        log("Removing existing container...")
        rc = run_cmd(f"proot-distro remove {CONTAINER_NAME}", timeout=300)[0]
        if rc != 0:
            log("[yellow]Container could not be removed cleanly; continuing.[/yellow]")

    log(f"Pulling {ref}...")
    rc = stream_cmd(f"proot-distro install {ref} --name {CONTAINER_NAME}", log, timeout=timeout)
    return rc == 0 and is_installed()


def get_version() -> str:
    """Version string as YYYYMMDD.<short sha>."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR if os.path.isdir(REPO_DIR) else None,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        sha = "unknown"
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.{sha}"


def link_launcher() -> tuple[bool, str]:
    """Put `pdm` on PATH. Returns (ok, message).

    Prefers $PREFIX/bin because that is Termux's entire default PATH — no
    shell startup file has to be edited, and the command works immediately in
    the session that ran the installer. Falls back to ~/bin elsewhere.
    """
    if not os.path.exists(LAUNCHER_SRC):
        return False, f"{LAUNCHER_SRC} not found"

    with contextlib.suppress(OSError):
        os.chmod(LAUNCHER_SRC, 0o755)

    for directory in (PREFIX_BIN, HOME_BIN):
        # Only create ~/bin; a missing $PREFIX/bin means this is not Termux.
        if directory == PREFIX_BIN and not os.path.isdir(directory):
            continue
        try:
            os.makedirs(directory, exist_ok=True)
            link = os.path.join(directory, "pdm")
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            try:
                os.symlink(LAUNCHER_SRC, link)
            except OSError:
                # Some filesystems reject symlinks; a copy still works, it
                # just needs re-linking after an update.
                shutil.copy2(LAUNCHER_SRC, link)
                os.chmod(link, 0o755)
            return True, link
        except OSError:
            continue

    return False, "no writable directory on PATH"


def unlink_launcher() -> list[str]:
    """Remove the `pdm` launcher from every directory link_launcher used.

    Leaves any `export PATH=...` line ensure_home_bin_on_path added in rc
    files alone — a stray PATH entry pointing at nothing is harmless, and
    rewriting shell startup files back out is its own source of breakage.
    """
    removed = []
    for directory in (PREFIX_BIN, HOME_BIN):
        link = os.path.join(directory, "pdm")
        if os.path.islink(link) or os.path.exists(link):
            with contextlib.suppress(OSError):
                os.remove(link)
                removed.append(link)
    return removed


def ensure_home_bin_on_path() -> list[str]:
    """Add ~/bin to PATH in both login and interactive startup files.

    Only needed on the ~/bin fallback. Termux starts shells through `login`,
    so .bashrc alone is not enough — bash reads .profile for login shells.
    """
    touched = []
    for rc in (os.path.expanduser("~/.bashrc"), os.path.expanduser("~/.profile")):
        try:
            existing = open(rc).read() if os.path.exists(rc) else ""
        except OSError:
            continue
        if "$HOME/bin" in existing:
            continue
        try:
            with open(rc, "a") as f:
                f.write('\n# PDM\nexport PATH="$HOME/bin:$PATH"\n')
            touched.append(rc)
        except OSError:
            continue
    return touched


def human_size(path: str) -> str:
    """Human-readable size of a directory tree, or '-' if absent."""
    if not os.path.exists(path):
        return "-"
    rc, out = run_cmd(f"du -sh {path} 2>/dev/null")
    if rc == 0 and out.strip():
        return out.split()[0]
    return "unknown"


def write_container_script(name: str, content: str) -> bool:
    """Place a script where the container will see it at /tmp/<name>.

    Written to the Termux tmp, not the container rootfs: --shared-tmp binds
    the Termux tmp over /tmp, so anything left in rootfs/tmp is hidden the
    moment the session starts.
    """
    try:
        os.makedirs(TMPDIR, exist_ok=True)
        path = os.path.join(TMPDIR, name)
        with open(path, "w", newline="\n") as f:
            f.write(content)
        os.chmod(path, 0o755)
        return True
    except OSError:
        return False


def container_command(script_name: str, user: str | None = None) -> str:
    """Command that runs a script placed by write_container_script.

    --shared-tmp is not optional here: the script is written to Termux's tmp,
    and without the flag the container's /tmp is its own rootfs directory
    where the file does not exist. Four call sites forgot it independently,
    which is why building the command is no longer left to callers.
    """
    user_flag = f"--user {user} " if user else ""
    return (
        f"proot-distro login {CONTAINER_NAME} {user_flag}--shared-tmp "
        f"-- bash /tmp/{script_name}"
    )


def container_path(path: str) -> str:
    """Where a path inside the container lives on the host."""
    return os.path.join(PROOT_DIR, "rootfs", path.lstrip("/"))
