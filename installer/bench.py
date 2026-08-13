"""Measure which GPU configuration is fastest on this device, then keep it.

Published guidance is written for Qualcomm hardware: zink is reported to work
only there, and Turnip is Adreno-only. On anything else — Exynos, Mali — the
right answer is unknown, and guessing has already cost this project several
rounds. So measure instead: run the same benchmark under each configuration
and keep the one that wins.

glmark2 renders off-screen but still needs an X display for its context, so
termux-x11 must be running. It does not need the desktop.
"""

from __future__ import annotations

import re
from typing import Callable, NamedTuple

from . import config
from .const import CONTAINER_NAME
from .system import container_command, is_installed, run_cmd, stream_cmd, write_container_script

Log = Callable[[str], None]

# Keys in the .env. Kept with the other per-device settings rather than in a
# file of their own: this is user configuration, not app state.
PROFILE_KEY = "GPU_PROFILE"
SCORE_KEY = "GPU_PROFILE_SCORE"

ANGLE_DIR = "/data/data/com.termux/files/usr/opt/angle-android"

# Duration per scene, in seconds. Three scenes at two seconds is enough to
# separate software rendering from hardware; a full glmark2 run is minutes.
SCENES = ("build", "texture", "shading")
SCENE_SECONDS = 2


class Preset(NamedTuple):
    name: str
    description: str
    # Termux-side renderer to start, or None for software rendering.
    server: str | None
    # Exported inside the container before the GL client runs.
    client_env: dict[str, str]


# The client always speaks the virgl protocol; what differs is what renders on
# the Termux side. A software baseline is included so the result says whether
# acceleration helped at all, rather than only which server was least bad.
PRESETS = (
    Preset(
        "software",
        "llvmpipe, no renderer",
        None,
        {"LIBGL_ALWAYS_SOFTWARE": "1"},
    ),
    Preset(
        "virgl",
        "virgl_test_server_android",
        "virgl_test_server_android",
        {"GALLIUM_DRIVER": "virpipe", "MESA_GL_VERSION_OVERRIDE": "4.0"},
    ),
    Preset(
        "angle",
        "virgl_test_server through ANGLE (Mali path)",
        f"LD_LIBRARY_PATH={ANGLE_DIR}/vulkan "
        "virgl_test_server --use-egl-surfaceless --use-gles",
        {
            "GALLIUM_DRIVER": "virpipe",
            "MESA_GL_VERSION_OVERRIDE": "4.1COMPAT",
            "MESA_GLSL_VERSION_OVERRIDE": "410",
        },
    ),
    Preset(
        "angle-null",
        "ANGLE with the null Vulkan loader",
        f"LD_LIBRARY_PATH={ANGLE_DIR}/vulkan-null "
        "virgl_test_server --use-egl-surfaceless --use-gles",
        {
            "GALLIUM_DRIVER": "virpipe",
            "MESA_GL_VERSION_OVERRIDE": "4.1COMPAT",
            "MESA_GLSL_VERSION_OVERRIDE": "410",
        },
    ),
    Preset(
        "zink",
        "virgl_test_server with zink",
        "MESA_NO_ERROR=1 MESA_GL_VERSION_OVERRIDE=4.3COMPAT "
        "MESA_GLES_VERSION_OVERRIDE=3.2 GALLIUM_DRIVER=zink "
        "ZINK_DESCRIPTORS=lazy virgl_test_server --use-egl-surfaceless --use-gles",
        {"GALLIUM_DRIVER": "virpipe", "MESA_GL_VERSION_OVERRIDE": "4.0"},
    ),
)

BENCH_SCRIPT_NAME = "pdm-bench.sh"


def preset_by_name(name: str) -> Preset | None:
    return next((p for p in PRESETS if p.name == name), None)


def load_profile() -> Preset | None:
    """The configuration a previous benchmark chose, if any."""
    name = config.get(PROFILE_KEY)
    return preset_by_name(name) if name else None


def save_profile(preset: Preset, score: int) -> bool:
    return config.set_value(PROFILE_KEY, preset.name) and config.set_value(
        SCORE_KEY, str(score)
    )


def set_profile_manually(preset: Preset) -> bool:
    """A Settings override rather than a measured result — clears the score
    rather than leaving a stale one from whatever preset was measured
    before, which would misreport this pick as benchmarked."""
    return config.set_value(PROFILE_KEY, preset.name) and config.unset(SCORE_KEY)


def client_exports(preset: Preset) -> str:
    """The preset's client environment, as shell export lines."""
    return "\n".join(f"export {k}={v}" for k, v in preset.client_env.items())


def _stop_renderers() -> None:
    run_cmd("pkill -f virgl_test_server 2>/dev/null")


def _start_renderer(preset: Preset, log: Log) -> bool:
    _stop_renderers()
    if preset.server is None:
        return True

    import subprocess
    import time

    subprocess.Popen(
        preset.server, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    rc, _ = run_cmd("pgrep -f virgl_test_server")
    if rc != 0:
        log("    renderer did not stay up")
        return False
    return True


def _bench_script(preset: Preset) -> str:
    scenes = " ".join(f"-b {s}:duration={SCENE_SECONDS}" for s in SCENES)
    return f"""#!/bin/bash
export DISPLAY=:0
{client_exports(preset)}
glmark2 {scenes} --off-screen 2>&1
"""


def _parse_score(output: str) -> int | None:
    match = re.search(r"glmark2 Score:\s*(\d+)", output)
    return int(match.group(1)) if match else None


def glmark2_installed() -> bool:
    rc, _ = run_cmd(
        f"proot-distro login {CONTAINER_NAME} -- bash -c 'command -v glmark2'",
        timeout=90,
    )
    return rc == 0


def install_glmark2(log: Log) -> bool:
    script = (
        "#!/bin/bash\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update\n"
        "apt-get install -y glmark2\n"
    )
    if not write_container_script("pdm-glmark2.sh", script):
        log("[red]Could not write the install script.[/red]")
        return False
    rc = stream_cmd(
        container_command("pdm-glmark2.sh"),
        log,
        timeout=1800,
    )
    return rc == 0


def run(log: Log) -> None:
    """Benchmark each preset and keep the winner."""
    if not is_installed():
        log("[red]No container — install it from the menu first.[/red]")
        return

    if run_cmd("pgrep -f termux-x11")[0] != 0:
        log("[red]termux-x11 is not running.[/red]")
        log("glmark2 renders off-screen but still needs a display for its")
        log("context. Start the desktop first, then run this.")
        return

    if not glmark2_installed():
        log("glmark2 is not in the container, installing it...")
        if not install_glmark2(log):
            log("[red]Could not install glmark2.[/red]")
            return
        log("")

    results: list[tuple[Preset, int | None]] = []

    for preset in PRESETS:
        log(f"── {preset.name}: {preset.description}")

        if not _start_renderer(preset, log):
            results.append((preset, None))
            log("    skipped")
            log("")
            continue

        if not write_container_script(BENCH_SCRIPT_NAME, _bench_script(preset)):
            results.append((preset, None))
            continue

        captured: list[str] = []

        def collect(line: str, sink: list[str] = captured) -> None:
            sink.append(line)
            if "glmark2 Score" in line or "Error" in line:
                log(f"    {line}")

        rc = stream_cmd(
            container_command(BENCH_SCRIPT_NAME),
            collect,
            timeout=600,
        )
        score = _parse_score("\n".join(captured))
        results.append((preset, score))

        if score is None:
            log(f"    no score (exit {rc})")
            for line in captured[-3:]:
                log(f"    {line}")
        log("")

    _stop_renderers()

    log("── Results ───────────────────────────────────")
    for preset, score in results:
        log(f"  {preset.name:<10} {'failed' if score is None else score}")
    log("")

    scored = [(p, s) for p, s in results if s is not None]
    if not scored:
        log("[red]Nothing produced a score.[/red]")
        return

    best, best_score = max(scored, key=lambda pair: pair[1])
    baseline = next((s for p, s in scored if p.name == "software"), None)

    log(f"[bold green]Best: {best.name} ({best_score})[/bold green]")
    if baseline and best.name != "software":
        log(f"  {best_score / baseline:.1f}x the software baseline")
    elif best.name == "software":
        log("  Acceleration did not beat software rendering on this device.")

    if save_profile(best, best_score):
        log(f"  saved to {config.CONFIG_PATH}")
        log("  Start Desktop will use it from now on.")
    else:
        log("  [yellow]Could not save the result.[/yellow]")
