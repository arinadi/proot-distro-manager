"""Audio: the PulseAudio server runs in Termux, clients run in the container.

There is no single method that works everywhere, and this project has now
tried three that did not: TCP where the module loaded but nothing listened, a
Unix socket that connected and then failed its handshake with "Protocol
error", and shared memory that cannot cross the proot boundary cleanly.

So the methods are declared and measured, the way the GPU presets are. Each
one is configured, tested end to end from inside the container, and the first
that works is written to the .env and used by every later start.

Recording is not supported and cannot be: the Termux app does not declare
android.permission.RECORD_AUDIO, module-sles-source fails to initialise, and
forcing it yields silence rather than audio.
"""

from __future__ import annotations

import math
import os
import struct
import time
import wave
from typing import Callable, NamedTuple

from . import config
from .const import TMPDIR
from .system import (
    container_command,
    container_path,
    is_installed,
    run_cmd,
    stream_cmd,
    write_container_script,
)

Log = Callable[[str], None]

METHOD_KEY = "AUDIO_METHOD"

PULSE_SOCKET = f"{TMPDIR}/pulse-socket"
CLIENT_CONF = "/etc/pulse/client.conf"
TEST_TONE_NAME = "pdm-test-tone.wav"
PROBE_SCRIPT_NAME = "pdm-audio.sh"


class Method(NamedTuple):
    name: str
    description: str
    # Passed to the daemon as --load, so no client connection is needed to
    # apply it.
    module: str
    # What clients inside the container point at.
    server: str
    # PulseAudio moves samples through shared memory when it can, setting it
    # up by passing file descriptors over the connection. proot intercepts
    # syscalls, and that is a poor bet across the boundary — but it is worth
    # measuring rather than assuming.
    shm: bool


UNIX_MODULE = f"module-native-protocol-unix socket={PULSE_SOCKET} auth-anonymous=1"
TCP_MODULE = "module-native-protocol-tcp auth-ip-acl=127.0.0.1 auth-anonymous=1"

# Ordered by how likely they are to work here. The socket comes first because
# --shared-tmp already puts it inside the container as an ordinary file, with
# no networking involved.
METHODS = (
    Method("unix", "Unix socket, shared memory off", UNIX_MODULE, "unix:/tmp/pulse-socket", False),
    Method("unix-shm", "Unix socket, shared memory on", UNIX_MODULE, "unix:/tmp/pulse-socket", True),
    Method("tcp", "TCP, shared memory off", TCP_MODULE, "tcp:127.0.0.1", False),
    Method("tcp-shm", "TCP, shared memory on", TCP_MODULE, "tcp:127.0.0.1", True),
)

DEFAULT_METHOD = METHODS[0]


def method_by_name(name: str) -> Method | None:
    return next((m for m in METHODS if m.name == name), None)


def load_method() -> Method:
    """The method a previous test proved, or the most likely one."""
    name = config.get(METHOD_KEY)
    return (method_by_name(name) if name else None) or DEFAULT_METHOD


def save_method(method: Method) -> bool:
    return config.set_value(METHOD_KEY, method.name)


# ── Server and client configuration ────────────────────────


def server_running() -> bool:
    rc, _ = run_cmd("pgrep -f pulseaudio")
    return rc == 0


def sinks() -> list[str]:
    """Output devices PulseAudio knows about. No sink means no sound."""
    rc, out = run_cmd("pactl list sinks short 2>/dev/null")
    if rc != 0:
        return []
    return [line.split("\t")[1] for line in out.splitlines() if "\t" in line]


def client_conf_body(method: Method) -> str:
    shm = "yes" if method.shm else "no"
    return f"""# PDM — PulseAudio client settings for proot.
# Written by the audio test for method "{method.name}".
#
# autospawn is off because there is no daemon in the container and none
# should be started; daemon-binary points at true so nothing tries.
autospawn = no
daemon-binary = /bin/true
enable-shm = {shm}
enable-memfd = {shm}
default-server = {method.server}
"""


def write_client_conf(method: Method, log: Log) -> bool:
    """Place the client settings inside the container.

    Written through the rootfs from the host, so no container login is needed
    and it applies before the desktop has ever started.
    """
    target = container_path(CLIENT_CONF)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(client_conf_body(method))
        return True
    except OSError as e:
        log(f"  could not write {CLIENT_CONF}: {e}")
        return False


def client_conf_present() -> bool:
    return os.path.exists(container_path(CLIENT_CONF))


def reachable(method: Method) -> bool:
    """Whether the server answers on this method's address, from Termux.

    Checked by connecting, not by listing modules: a module can be loaded and
    accept nothing, which is exactly how the TCP method failed while every
    check reported success.
    """
    address = method.server.replace("unix:/tmp/", f"unix:{TMPDIR}/")
    rc, _ = run_cmd(f"pactl -s {address} info")
    return rc == 0


def start_with(method: Method, log: Log) -> bool:
    """Restart PulseAudio carrying this method's module."""
    if server_running():
        run_cmd("pulseaudio --kill")
        time.sleep(1)
    run_cmd(f"rm -f {PULSE_SOCKET}")

    stream_cmd(
        f'pulseaudio --start --exit-idle-time=-1 --load="{method.module}"',
        log,
        timeout=60,
    )
    time.sleep(1)
    return reachable(method)


def ensure_server(log: Log) -> bool:
    """Bring up audio using whichever method was proved to work."""
    method = load_method()
    log(f"  method: {method.name} ({method.description})")

    if is_installed():
        write_client_conf(method, log)

    if server_running() and reachable(method):
        log("  already running and reachable")
        return True

    if start_with(method, log):
        log(f"  server ready on {method.server}")
        return True

    log("  [red]the server is not reachable on this method[/red]")
    log("  run Doctor -> Audio to test the others")
    return False


# ── Test tone ──────────────────────────────────────────────


def write_test_tone(path: str, seconds: float = 1.0, hz: int = 440) -> bool:
    """Write a short sine wave. Generated rather than shipped: the vanilla
    image has no sound files and this avoids adding a package for one beep."""
    rate = 16000
    try:
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            frames = bytearray()
            for i in range(int(rate * seconds)):
                # Fade the last 10% so it ends without a click.
                progress = i / (rate * seconds)
                gain = 0.3 * (1.0 if progress < 0.9 else (1.0 - progress) * 10)
                sample = gain * 32767 * math.sin(2 * math.pi * hz * i / rate)
                frames += struct.pack("<h", int(sample))
            out.writeframes(bytes(frames))
        return True
    except (OSError, wave.Error):
        return False


def probe_script(method: Method) -> str:
    return f"""#!/bin/bash
export PULSE_SERVER={method.server}
if ! pactl info >/dev/null 2>&1; then
    echo "FAIL connect"
    pactl info 2>&1 | sed 's/^/    /'
    exit 1
fi
echo "connected"
pactl info 2>&1 | grep -E "Server String|Default Sink" | sed 's/^/    /'
paplay /tmp/{TEST_TONE_NAME} 2>&1 | sed 's/^/    /'
status=${{PIPESTATUS[0]}}
echo "play exit: $status"
[ "$status" = "0" ] && echo "OK play"
exit "$status"
"""


# ── The test ───────────────────────────────────────────────


def test(log: Log) -> None:
    """Try each method end to end and keep the first that plays."""
    log("── Termux side ───────────────────────────────")
    log(f"sinks: {', '.join(sinks()) if sinks() else 'NONE — nothing can play'}")

    tone = os.path.join(TMPDIR, TEST_TONE_NAME)
    if not write_test_tone(tone):
        log("[red]Could not write the test tone.[/red]")
        return
    log(f"tone:  {tone}")
    log("")

    log("Playing from Termux (tests the Android audio path)...")
    rc = stream_cmd(f"paplay {tone}", log, timeout=30)
    if rc != 0:
        log("[red]Termux itself cannot play.[/red] Nothing in the container will help.")
        return
    log("  ok")
    log("")

    if not is_installed():
        log("No container, so the methods cannot be tested.")
        return

    working: Method | None = None

    for method in METHODS:
        log(f"── {method.name}: {method.description}")

        if not start_with(method, lambda _m: None):
            log("  server not reachable from Termux, skipping")
            log("")
            continue

        if not write_client_conf(method, log):
            log("")
            continue
        if not write_container_script(PROBE_SCRIPT_NAME, probe_script(method)):
            log("")
            continue

        captured: list[str] = []

        def collect(line: str, sink: list[str] = captured) -> None:
            sink.append(line)
            log(f"  {line}")

        stream_cmd(container_command(PROBE_SCRIPT_NAME), collect, timeout=90)

        if any("OK play" in line for line in captured):
            log("  [green]this one works[/green]")
            working = method
            log("")
            break
        log("")

    log("── Result ────────────────────────────────────")
    if working is None:
        log("[red]No method played from inside the container.[/red]")
        log("The Termux side is fine, so the fault is in the boundary.")
        log("Copy this report with C — the per-method errors above are the")
        log("useful part.")
        return

    log(f"[bold green]Using {working.name}: {working.description}[/bold green]")
    if save_method(working):
        log(f"  saved to {config.CONFIG_PATH}; Start Desktop will use it")
    else:
        log("  [yellow]could not save the choice[/yellow]")

    log("")
    log("[dim]Recording is not possible on this stack: the Termux app does[/dim]")
    log("[dim]not declare RECORD_AUDIO, so there is no microphone source.[/dim]")
