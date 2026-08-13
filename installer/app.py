"""PDM TUI.

Textual app. Every menu entry runs its work in a thread worker and streams
output into a log pane, so the UI stays responsive while apt or proot-distro
takes minutes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)

from . import audio, backup, bench, doctor, packages
from . import start as desktop
from .const import CACHE_DIR, CONTAINER_NAME, REPO_DIR
from .system import (
    get_version,
    human_size,
    is_installed,
    pull_custom_image,
    pull_image,
    stream_cmd,
    unlink_launcher,
    valid_image_ref,
)

# ── Copying output ─────────────────────────────────────────

# A copy is always mirrored to a file, so the text survives even when no
# clipboard is reachable — the usual reason to copy this is to paste it
# somewhere else for help.
EXPORT_NAME = "pdm-last-output.txt"


def _to_clipboard(app, text: str) -> str | None:
    """Put `text` on a clipboard. Returns how it got there, or None.

    termux-clipboard-set reaches the real Android clipboard but needs the
    termux-api package and the Termux:API app. Textual's own path uses an
    OSC 52 escape, which only lands if the terminal honours it.
    """
    if shutil.which("termux-clipboard-set"):
        try:
            result = subprocess.run(
                ["termux-clipboard-set"], input=text, text=True, timeout=10
            )
            if result.returncode == 0:
                return "the Android clipboard"
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        app.copy_to_clipboard(text)
        return "the terminal clipboard"
    except Exception:
        return None


def _write_export(text: str) -> str | None:
    """Mirror the copy to a file, next to the repo when there is one.

    Deliberately does not create the repo directory: off-device there is no
    checkout, and a copy action has no business making one.
    """
    directory = REPO_DIR if os.path.isdir(REPO_DIR) else tempfile.gettempdir()
    path = os.path.join(directory, EXPORT_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except OSError:
        return None


class ScrollableTable(DataTable):
    """A DataTable with Left/Right repurposed to horizontal scroll.

    Every table here uses cursor_type="row", which has no column cursor —
    DataTable's own Left/Right bindings call cursor_left/cursor_right, which
    are no-ops for a row cursor, so a wide column (a description, a URI) was
    unreachable with no working way to see the rest of it. Overriding the
    same keys wins: bindings merge by key across the MRO, most-derived last.
    """

    BINDINGS = [
        Binding("left", "scroll_left", "Scroll left", show=False),
        Binding("right", "scroll_right", "Scroll right", show=False),
    ]


class CopyableScreen(Screen):
    """A screen whose visible output can be copied out as plain text.

    Subclasses return their content from `copy_payload`. The button is
    labelled "C" rather than a clipboard glyph: Termux's font cannot be
    relied on to have one.
    """

    BINDINGS = [("c", "copy", "Copy")]

    def copy_payload(self) -> str:
        raise NotImplementedError

    @on(Button.Pressed, "#copy")
    def _copy_pressed(self) -> None:
        self.action_copy()

    def action_copy(self) -> None:
        text = self.copy_payload().strip()
        if not text:
            self.notify("Nothing to copy yet.", severity="warning")
            return

        where = _to_clipboard(self.app, text)
        path = _write_export(text)

        if where and path:
            self.notify(f"Copied to {where}. Also saved to {path}")
        elif where:
            self.notify(f"Copied to {where}.")
        elif path:
            self.notify(f"No clipboard available — saved to {path}", severity="warning")
        else:
            self.notify("Could not copy or save the output.", severity="error")


# ── Confirmation ───────────────────────────────────────────


def when_confirmed(app: App, build_screen: Callable[[], Screen]) -> Callable[[bool | None], None]:
    """push_screen callback that opens a screen only if the user confirmed.

    Written out rather than inlined as a lambda: the conditional-expression
    form discarded push_screen's return value, which mypy correctly objected
    to, and this reads better at five call sites.
    """

    def handler(confirmed: bool | None) -> None:
        if confirmed:
            app.push_screen(build_screen())

    return handler


class ConfirmScreen(ModalScreen[bool]):
    """Modal yes/no. Destructive actions route through this."""

    BINDINGS = [("escape", "dismiss(False)", "Cancel")]

    def __init__(self, title: str, body: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Static(self._body, id="dialog-body")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, id="confirm", variant="error")

    @on(Button.Pressed, "#confirm")
    def _confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


# ── Generic action runner ──────────────────────────────────


class _Logger:
    """A callable log that also carries an in-place progress line.

    stream_cmd looks for a `.progress()` method to route carriage-return
    redraws to. Without it a download either shows nothing at all or fills
    the pane with thousands of near-identical lines.
    """

    def __init__(self, write, progress) -> None:
        self._write = write
        self._progress = progress

    def __call__(self, message: str = "") -> None:
        self._write(message)

    def progress(self, message: str) -> None:
        self._progress(message)


class ActionScreen(CopyableScreen):
    """Runs `runner(log)` in a thread and streams its output."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, title: str, runner, offer_restart: bool = False) -> None:
        super().__init__()
        self._title = title
        self._runner = runner
        self._offer_restart = offer_restart
        self._log: RichLog | None = None
        # RichLog keeps rendered strips, not text, so the plain lines are kept
        # alongside it for copying.
        self._lines: list[str] = []
        # Not _running: MessagePump uses that name for its own loop state, and
        # shadowing it stops the screen ever processing its mount.
        self._busy = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(self._title, classes="screen-title")
        yield RichLog(id="log", markup=True, wrap=True, auto_scroll=True)
        yield Static("", id="progress")
        with Horizontal(id="action-buttons"):
            yield Button("C", id="copy")
            if self._offer_restart:
                yield Button("Restart", id="restart", variant="success", disabled=True)
            yield Button("Back", id="back", variant="primary", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._log = self.query_one("#log", RichLog)
        self.query_one("#copy", Button).tooltip = "Copy this log"
        if self._offer_restart:
            self.query_one("#restart", Button).tooltip = "Relaunch pdm on the new code"
        self.run_task()

    def copy_payload(self) -> str:
        return "\n".join(self._lines)

    def _set_progress(self, message: str) -> None:
        self.query_one("#progress", Static).update(message)

    @work(thread=True)
    def run_task(self) -> None:
        def write(message: str = "") -> None:
            assert self._log is not None
            # Markup is for the pane; the copy should be readable as text.
            self._lines.append(Text.from_markup(message).plain)
            self.app.call_from_thread(self._log.write, message)

        def progress(message: str) -> None:
            self.app.call_from_thread(self._set_progress, message)

        log = _Logger(write, progress)

        try:
            self._runner(log)
        except Exception as e:
            log(f"[bold red]Error:[/bold red] {e}")
        self.app.call_from_thread(self._finish)

    def _finish(self) -> None:
        self._busy = False
        self._set_progress("")
        button = self.query_one("#back", Button)
        button.disabled = False
        if self._offer_restart:
            restart = self.query_one("#restart", Button)
            restart.disabled = False
            restart.focus()
        else:
            button.focus()

    @on(Button.Pressed, "#restart")
    def _restart(self) -> None:
        if self._busy:
            self.notify("Still working — wait for it to finish.", severity="warning")
            return
        # Narrowed rather than assumed: this screen is generic and only this
        # app knows how to relaunch itself.
        if isinstance(self.app, PDMApp):
            self.app.request_restart()

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        # The Back button is disabled while the worker runs, but the escape
        # binding calls this same action. Without the guard, escape walks out
        # of a screen the UI says you cannot leave — mid image pull, that
        # leaves a half-installed container with nothing on screen to say so.
        if self._busy:
            self.notify("Still working — this cannot be interrupted yet.", severity="warning")
            return
        self.app.pop_screen()




# ── Runners ────────────────────────────────────────────────


def run_start(log) -> None:
    if desktop.start_desktop(log):
        log("")
        log("[bold green]Desktop started.[/bold green] Open the Termux:X11 app to see it.")
    else:
        log("")
        log("[bold red]Desktop did not start.[/bold red] See xfce4.log in the repo.")


def run_stop(log) -> None:
    desktop.stop_desktop(log)


def run_update(log) -> None:
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        log(f"[red]{REPO_DIR} is not a git repository.[/red]")
        return

    log("Pulling latest changes...")
    rc = stream_cmd(f"git -C {REPO_DIR} pull --ff-only", log, timeout=120)
    if rc != 0:
        log("")
        log("Fast-forward failed; resetting to origin/main...")
        stream_cmd(f"git -C {REPO_DIR} fetch origin main", log, timeout=120)
        rc = stream_cmd(f"git -C {REPO_DIR} reset --hard origin/main", log, timeout=60)

    log("")
    if rc == 0:
        log("[green]Up to date.[/green]")
        log("Press Restart to relaunch on the new code, or Back to keep")
        log("running the version already loaded.")
    else:
        log("[red]Update failed.[/red]")


def run_reset(log) -> None:
    # One teardown path. stop_desktop already kills this container's proot
    # tree and verifies, so there is nothing to repeat here.
    log("Stopping the desktop...")
    desktop.stop_desktop(log)
    log("")

    log("Removing container...")
    rc = stream_cmd(f"proot-distro remove {CONTAINER_NAME}", log, timeout=300)
    if rc != 0:
        log("[yellow]Container could not be removed cleanly; continuing.[/yellow]")

    log("")
    ok = pull_image(log)
    if ok:
        packages.reapply_saved_mirror(log)

    log("")
    if ok:
        log("[bold green]Reset complete.[/bold green] Start the desktop from the menu.")
    else:
        log("[bold red]Install failed.[/bold red] Check your connection and try again.")


def run_manual_install(image_ref: str):
    """Bound to a specific image_ref at menu time (see ManualInstallScreen),
    same shape as every other runner ActionScreen calls with just `log`.

    Unlike Reset, this never touches apt's mirror (reapply_saved_mirror)
    — that repair assumes a Debian sources.list exists, which most images
    a manual install pulls will not have."""

    def run(log) -> None:
        log("Stopping the desktop...")
        desktop.stop_desktop(log)
        log("")

        ok = pull_custom_image(image_ref, log)

        log("")
        if ok:
            log(
                "[bold green]Installed.[/bold green] Most images have no desktop "
                "environment — install one from Store, or use this as a plain shell."
            )
        else:
            log("[bold red]Install failed.[/bold red] Check the image reference and try again.")

    return run


def run_uninstall(log) -> None:
    # Same teardown as Reset, minus the reinstall, plus the launcher — Reset
    # leaves `pdm` on PATH because it always reinstalls; uninstall does not.
    log("Stopping the desktop...")
    desktop.stop_desktop(log)
    log("")

    log("Removing container...")
    rc = stream_cmd(f"proot-distro remove {CONTAINER_NAME}", log, timeout=300)
    if rc != 0:
        log("[yellow]Container could not be removed cleanly; continuing.[/yellow]")
    log("")

    if os.path.exists(CACHE_DIR):
        log("Removing cached image layers...")
        try:
            shutil.rmtree(CACHE_DIR)
        except OSError as e:
            log(f"[yellow]Could not remove cache: {e}[/yellow]")
    log("")

    log("Removing launcher...")
    removed = unlink_launcher()
    for link in removed:
        log(f"  removed {link}")
    if not removed:
        log("  nothing on PATH to remove")

    log("")
    log("[bold green]Uninstalled.[/bold green] The container, cache, and "
        f"launcher are gone. {REPO_DIR} was left in place — remove it "
        "yourself with `rm -rf ~/pdm` if you want it gone too.")


def run_clean_cache(log) -> None:
    if not os.path.exists(CACHE_DIR):
        log("No cache directory — nothing to clean.")
        return

    log(f"Cache size: {human_size(CACHE_DIR)}")

    log("Stopping the desktop first...")
    desktop.stop_desktop(log)
    log("")

    log("Removing cached image layers...")
    try:
        shutil.rmtree(CACHE_DIR)
    except OSError as e:
        log(f"[red]Failed: {e}[/red]")
        return

    log("")
    log("[green]Cache cleared.[/green] The next install re-downloads the image.")


# ── Main menu ──────────────────────────────────────────────


class MainScreen(Screen):
    BINDINGS = [("q", "app.quit", "Quit")]

    # Short labels keep three to a row; the full meaning lives in the tooltip.
    TOOLTIPS = {
        "update": "Pull the latest PDM",
        "manual-install": "Install any distro image, not just PDM's own",
        "store": "Search and install packages in the container",
        "settings": "Per-device preferences, saved to .env",
        "doctor": "Diagnose and repair the environment",
        "backup": "Back up or restore your home directory",
        "reset": "Delete the container and reinstall it",
        "cache": "Delete downloaded image layers",
    }

    def on_mount(self) -> None:
        for button_id, text in self.TOOLTIPS.items():
            self.query_one(f"#{button_id}", Button).tooltip = text

    def compose(self) -> ComposeResult:
        yield Header()
        # Grid rather than Horizontal: a row of 1fr children in a Horizontal
        # gave every button the full remaining width instead of a share, so
        # three-button rows ran off the side of the screen.
        # The two actions that carry a full sentence get half the width each;
        # the rest are one word and fit three to a row even on a narrow phone.
        with VerticalScroll(id="menu"):
            with Grid(classes="row2"):
                yield Button("Start Desktop", id="start", variant="success")
                yield Button("Stop Desktop", id="stop", variant="warning")
            with Grid(classes="row1"):
                yield Button("Manual Install", id="manual-install")
            with Grid(classes="row3"):
                yield Button("Update", id="update")
                yield Button("Store", id="store")
                yield Button("Settings", id="settings")
            with Grid(classes="row2"):
                yield Button("Doctor", id="doctor")
                yield Button("Backup", id="backup")
            with Grid(classes="row2"):
                yield Button("Reset", id="reset", variant="error")
                yield Button("Cache", id="cache")
        yield Footer()

    @on(Button.Pressed, "#start")
    def _start(self) -> None:
        if not is_installed():
            self.app.push_screen(
                ConfirmScreen(
                    "Container not installed",
                    "No Debian container found. Pull it now?\n\n"
                    "This downloads the container image and takes a few "
                    "minutes.",
                    confirm_label="Install",
                ),
                when_confirmed(self.app, lambda: ActionScreen("Install", run_reset)),
            )
            return
        self.app.push_screen(ActionScreen("Start Desktop", run_start))

    @on(Button.Pressed, "#stop")
    def _stop(self) -> None:
        self.app.push_screen(ActionScreen("Stop Desktop", run_stop))

    @on(Button.Pressed, "#manual-install")
    def _manual_install(self) -> None:
        self.app.push_screen(ManualInstallScreen())

    @on(Button.Pressed, "#update")
    def _update(self) -> None:
        self.app.push_screen(ActionScreen("Update", run_update, offer_restart=True))

    @on(Button.Pressed, "#store")
    def _store(self) -> None:
        self.app.push_screen(StoreScreen())

    @on(Button.Pressed, "#settings")
    def _settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    @on(Button.Pressed, "#doctor")
    def _doctor(self) -> None:
        self.app.push_screen(DoctorScreen())

    @on(Button.Pressed, "#backup")
    def _backup(self) -> None:
        self.app.push_screen(BackupScreen())

    @on(Button.Pressed, "#reset")
    def _reset(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Reset (Clean Install)",
                "This deletes the entire container and pulls a fresh image.\n\n"
                "Every file, setting, and package inside the container is lost "
                "permanently. Your Termux home is untouched. Back up first from "
                "the Backup screen if you want to keep your files.",
                confirm_label="Delete and reinstall",
            ),
            when_confirmed(self.app, lambda: ActionScreen("Reset", run_reset)),
        )

    @on(Button.Pressed, "#cache")
    def _cache(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Clean Image Cache",
                "Deletes downloaded OCI image layers. The container itself is "
                "kept; the next install re-downloads the image.",
                confirm_label="Delete cache",
            ),
            when_confirmed(self.app, lambda: ActionScreen("Clean Image Cache", run_clean_cache)),
        )


# The one maintained, ready-to-use build — Debian Trixie + XFCE, the same
# image XLabs itself publishes, not a vanilla rootfs. Kept separate from
# the picks below rather than folded in as just another row: those are
# raw distros with no desktop environment; this one boots straight to
# Start Desktop working, same as PDM's own default.
FLAGSHIP_PRESET = ("xlabs", "XLabs", "ghcr.io/arinadi/xlabs:latest")
FLAGSHIP_DESCRIPTION = "Debian Trixie, XFCE, by Arinano"

# Quick-pick starting points only — proot-distro takes any OCI reference
# directly (ubuntu:24.04, ghcr.io/org/image:tag, ...), so this is not a
# curated list PDM has to keep in sync with what upstream actually offers.
#
# id is a slug, not the ref itself: Textual widget ids only allow letters,
# digits, underscores and hyphens, and a ref like "debian:13" has a colon.
MANUAL_INSTALL_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("debian", "Debian", "debian:13"),
    ("ubuntu", "Ubuntu", "ubuntu:24.04"),
    ("alpine", "Alpine", "alpine:latest"),
    ("arch", "Arch Linux", "archlinux:latest"),
    ("fedora", "Fedora", "fedora:latest"),
)

ALL_INSTALL_PRESETS: tuple[tuple[str, str, str], ...] = (FLAGSHIP_PRESET, *MANUAL_INSTALL_PRESETS)


class ManualInstallScreen(Screen):
    """Install any distro image instead of PDM's prebuilt Debian + XFCE
    one — the same container slot Reset uses, just pointed at a different
    image. Not a second, independent container: everything else in this
    app (Start, Doctor, Backup, ...) still only knows about the one named
    in installer/const.py, so this replaces what is there rather than
    adding to it."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Manual Install", classes="screen-title")
        with VerticalScroll(id="manual-install-form"):
            yield Static(
                "Replaces whatever is currently installed.",
                id="manual-install-note",
            )
            yield Label(f"★ Flagship — {FLAGSHIP_DESCRIPTION}")
            with Grid(classes="row1"):
                yield Button(
                    "Install XLabs", id=f"preset-{FLAGSHIP_PRESET[0]}", variant="success"
                )
            yield Label(
                "Or pick a distro directly — no desktop environment; install "
                "one afterward from Store, or use it as a plain shell"
            )
            with Grid(classes="row3"):
                for slug, label, _ref in MANUAL_INSTALL_PRESETS:
                    yield Button(label, id=f"preset-{slug}")
            yield Label("Image reference")
            yield Input(
                placeholder="e.g. ubuntu:24.04, alpine:latest, ghcr.io/org/img:tag",
                id="image-ref",
            )
            yield Static("", id="manual-install-status")
        with Grid(classes="row2"):
            yield Button("Install", id="submit", variant="error")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def _status(self, message: str) -> None:
        self.status_text = message
        self.query_one("#manual-install-status", Static).update(message)

    @on(Button.Pressed)
    def _preset(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("preset-"):
            return
        slug = button_id.removeprefix("preset-")
        for preset_slug, _label, ref in ALL_INSTALL_PRESETS:
            if preset_slug == slug:
                self.query_one("#image-ref", Input).value = ref
                return

    @on(Button.Pressed, "#submit")
    def _submit(self) -> None:
        image_ref = self.query_one("#image-ref", Input).value.strip()
        if not image_ref:
            self._status("An image reference is required.")
            return
        if not valid_image_ref(image_ref):
            self._status(
                "That doesn't look like a valid image reference — letters, "
                "digits, and . _ / : - only."
            )
            return

        self.app.push_screen(
            ConfirmScreen(
                f"Install {image_ref}",
                f"This deletes whatever is currently installed and pulls "
                f"{image_ref} in its place.\n\n"
                "Every file, setting, and package inside the current "
                "container is lost permanently. Your Termux home is "
                "untouched. Back up first from the Backup screen if you "
                "want to keep your files.",
                confirm_label="Delete and install",
            ),
            when_confirmed(
                self.app,
                lambda: ActionScreen(f"Install {image_ref}", run_manual_install(image_ref)),
            ),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class SettingsScreen(CopyableScreen):
    """Per-device preferences, stored in .env.

    Each value is owned by whatever module actually uses it — audio.py,
    bench.py, start.py, packages.py — this screen only reads and writes
    them through those modules' own functions. Mirror is shown but not
    edited here: Store -> Mirror already measures candidates and picking
    one there saves it the same way, so a second editable copy here would
    just be two places that could disagree.
    """

    BINDINGS = [("escape", "back", "Back")]

    DRAW_PATH_OPTIONS = [
        ("Normal", "normal"),
        ("Legacy drawing (fixes some black screens)", "legacy-drawing"),
        ("Force BGRA (fixes swapped colors)", "force-bgra"),
        ("Legacy drawing + force BGRA", "legacy-drawing+force-bgra"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.status_text = ""
        # Last value _refresh() itself set, per Select — Select.Changed is
        # a posted message, dispatched after _refresh() has already
        # returned, so a "loading" flag reset at the end of _refresh()
        # cannot reliably guard against it. Comparing against what was
        # just assigned works regardless of when the message lands.
        self._last_audio = ""
        self._last_gpu = ""
        self._last_x11 = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Settings", classes="screen-title")
        with VerticalScroll(id="settings-form"):
            yield Static(
                "Per-device preferences, saved to .env. Restart the desktop "
                "for a change to take effect.",
                id="settings-note",
            )
            yield Label("Debian mirror (change from Store → Mirror)")
            yield Static("", id="settings-mirror")
            yield Label("Audio method")
            yield Select(
                [(m.description, m.name) for m in audio.METHODS],
                id="settings-audio",
                allow_blank=False,
            )
            yield Label("GPU profile")
            yield Select(
                [(p.description, p.name) for p in bench.PRESETS],
                id="settings-gpu",
                allow_blank=False,
            )
            yield Label("termux-x11 rendering")
            yield Select(self.DRAW_PATH_OPTIONS, id="settings-x11", allow_blank=False)
            yield Static("", id="settings-status")
            yield Button("Uninstall PDM", id="uninstall", variant="error")
        with Grid(classes="row2"):
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#copy", Button).tooltip = "Copy these settings"
        self.query_one("#uninstall", Button).tooltip = (
            "Remove the container, cache, and pdm launcher"
        )
        self._refresh()

    def on_screen_resume(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#settings-mirror", Static).update(
            packages.current_mirror() or "No sources file in the container"
        )
        self._last_audio = audio.load_method().name
        self.query_one("#settings-audio", Select).value = self._last_audio
        # No benchmark has necessarily run yet, unlike audio/X11 which
        # always have a real default — fall back to the same baseline
        # Bench itself would rather than leave the select blank.
        self._last_gpu = (bench.load_profile() or bench.PRESETS[0]).name
        self.query_one("#settings-gpu", Select).value = self._last_gpu
        self._last_x11 = desktop.load_draw_path()
        self.query_one("#settings-x11", Select).value = self._last_x11

    def _status(self, message: str) -> None:
        self.status_text = message
        self.query_one("#settings-status", Static).update(message)

    @on(Select.Changed, "#settings-audio")
    def _audio_changed(self, event: Select.Changed) -> None:
        if str(event.value) == self._last_audio:
            return
        method = audio.method_by_name(str(event.value))
        if method is not None:
            self._last_audio = method.name
            audio.save_method(method)
            self._status(f"Audio method set to {method.name}.")

    @on(Select.Changed, "#settings-gpu")
    def _gpu_changed(self, event: Select.Changed) -> None:
        if str(event.value) == self._last_gpu:
            return
        preset = bench.preset_by_name(str(event.value))
        if preset is not None:
            self._last_gpu = preset.name
            bench.set_profile_manually(preset)
            self._status(f"GPU profile set to {preset.name}.")

    @on(Select.Changed, "#settings-x11")
    def _x11_changed(self, event: Select.Changed) -> None:
        if str(event.value) == self._last_x11:
            return
        self._last_x11 = str(event.value)
        desktop.save_draw_path(self._last_x11)
        self._status("termux-x11 rendering mode saved.")

    def copy_payload(self) -> str:
        return "\n".join(
            [
                f"PDM settings - {get_version()}",
                "",
                f"Mirror:  {packages.current_mirror() or 'unknown'}",
                f"Audio:   {audio.load_method().name}",
                f"GPU:     {(bench.load_profile() or bench.PRESETS[0]).name}",
                f"X11:     {desktop.load_draw_path()}",
            ]
        )

    @on(Button.Pressed, "#uninstall")
    def _uninstall(self) -> None:
        self.app.push_screen(
            ConfirmScreen(
                "Uninstall PDM",
                "This deletes the container and its cached image layers, and "
                "removes the pdm launcher from PATH.\n\n"
                f"{REPO_DIR} and any backups in ~/pdm-backups are left in "
                "place. Every file, setting, and package inside the "
                "container is lost permanently — back up first from the "
                "Backup screen if you want to keep your files.",
                confirm_label="Uninstall",
            ),
            when_confirmed(self.app, lambda: ActionScreen("Uninstall", run_uninstall)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class BackupScreen(CopyableScreen):
    """Back up and restore the container's home directory.

    This is the user's own files and settings — not apt packages, which a
    plain Reset already reinstalls on its own.
    """

    BINDINGS = [("escape", "back", "Back")]

    NOTE = (
        f"Archives {backup.HOME_IN_CONTAINER} — files, the Firefox profile, "
        "editor settings, the panel layout. Not apt packages; those come "
        "back with a normal install."
    )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Backup", classes="screen-title")
        yield Static(self.NOTE, id="backup-note")
        yield ScrollableTable(id="backup-table", cursor_type="row", zebra_stripes=True)
        with Grid(classes="row3"):
            yield Button("Backup now", id="create", variant="success")
            yield Button("Restore", id="restore")
            yield Button("Delete", id="delete", variant="error")
        with Grid(classes="row2"):
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def __init__(self) -> None:
        super().__init__()
        self._backups: list[backup.Backup] = []
        # Kept alongside the widget: Static does not expose its text back.
        self.status_text = self.NOTE

    def _note(self, message: str) -> None:
        self.status_text = message
        self.query_one("#backup-note", Static).update(message)

    def on_mount(self) -> None:
        self.query_one("#backup-table", DataTable).add_columns("Name", "Size", "Created")
        self.query_one("#copy", Button).tooltip = "Copy this list"
        self._fill()

    def on_screen_resume(self) -> None:
        self._fill()

    def _fill(self) -> None:
        self._backups = backup.list_backups()
        table = self.query_one("#backup-table", DataTable)
        table.clear()
        for b in self._backups:
            table.add_row(b.name, backup.human_size(b.size_bytes), b.created.strftime("%Y-%m-%d %H:%M"))
        # The list just changed shape, so any earlier highlight no longer
        # points at what it used to.
        self._note(self.NOTE)

    def _selected(self) -> backup.Backup | None:
        row = self.query_one("#backup-table", DataTable).cursor_row
        if row is None or not (0 <= row < len(self._backups)):
            return None
        return self._backups[row]

    @on(DataTable.RowHighlighted, "#backup-table")
    def _row_highlighted(self) -> None:
        # Reuses the note line rather than adding a row for this: on a
        # phone-height screen there is no free row to spare, and a table
        # row is thin enough to mistap, so naming the pick here — not only
        # inside the confirm dialog — catches that before Restore/Delete is
        # even pressed. Restore is the one that can undo real work if it
        # lands on the wrong archive.
        b = self._selected()
        if b is not None:
            self._note(f"Selected: {b.name} ({backup.human_size(b.size_bytes)})")

    def copy_payload(self) -> str:
        lines = [f"PDM backups - {get_version()}", ""]
        lines += [
            f"{b.created.strftime('%Y-%m-%d %H:%M')}  {backup.human_size(b.size_bytes):>8}  {b.name}"
            for b in self._backups
        ]
        return "\n".join(lines)

    @on(Button.Pressed, "#create")
    def _create(self) -> None:
        if not is_installed():
            self.notify("No container to back up.", severity="warning")
            return

        def run(log) -> None:
            backup.create_backup(log)

        self.app.push_screen(
            ConfirmScreen(
                "Back up home",
                f"Archives {backup.HOME_IN_CONTAINER} to {backup.BACKUP_DIR} on "
                "Termux's own storage.\n\n"
                "Can take a while for a large Firefox cache or node_modules.",
                confirm_label="Back up",
            ),
            when_confirmed(self.app, lambda: ActionScreen("Backup", run)),
        )

    @on(Button.Pressed, "#restore")
    def _restore(self) -> None:
        b = self._selected()
        if b is None:
            self.notify("Highlight a backup first.", severity="warning")
            return

        def run(log) -> None:
            backup.restore_backup(b, log)

        self.app.push_screen(
            ConfirmScreen(
                f"Restore {b.name}",
                f"Replaces {backup.HOME_IN_CONTAINER} with this backup's contents.\n\n"
                "The current home is kept as a .bak inside the container "
                "rather than deleted, in case this turns out to be the wrong "
                "pick.",
                confirm_label="Restore",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Restore {b.name}", run)),
        )

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        b = self._selected()
        if b is None:
            self.notify("Highlight a backup first.", severity="warning")
            return

        def run(log) -> None:
            backup.delete_backup(b, log)

        self.app.push_screen(
            ConfirmScreen(
                f"Delete {b.name}",
                "This only removes the saved archive — it does not touch the "
                "container.",
                confirm_label="Delete",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Delete {b.name}", run)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class DoctorScreen(CopyableScreen):
    """Environment diagnosis with a one-press repair for what is fixable."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._issues: list[doctor.Issue] = []
        self._info = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Doctor", classes="screen-title")
        yield Static("", id="doctor-info")
        yield ScrollableTable(id="doctor-table", cursor_type="row", zebra_stripes=True)
        # Three to a row, with Back on its own full-width row: it is the
        # most-used control and deserves the biggest target.
        with Grid(classes="row3"):
            yield Button("Re-scan", id="rescan")
            yield Button("Fix", id="fix", variant="success", disabled=True)
            yield Button("Diagnose", id="diagnose")
        with Grid(classes="row3"):
            yield Button("Dupes", id="dupes")
            yield Button("Audio", id="audio")
            yield Button("Bench", id="bench")
        with Grid(classes="row2"):
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#doctor-table", DataTable).add_columns("Check", "State", "Detail")
        self.query_one("#copy", Button).tooltip = "Copy this report"

    def copy_payload(self) -> str:
        lines = [f"PDM doctor — {get_version()}", self._info, ""]
        for issue in self._issues:
            if issue.ok:
                mark = "ok "
            elif issue.unknown:
                mark = "?? "
            elif issue.fix is not None:
                mark = "FIX"
            else:
                mark = "-- "
            lines.append(f"{mark} {issue.name:<14} {issue.detail}")
        return "\n".join(lines)

    def on_screen_resume(self) -> None:
        """Also runs after returning from a fix, so the table is never stale."""
        self.scan()

    @work(thread=True)
    def scan(self) -> None:
        issues = doctor.diagnose()
        # Folded in from the old Status screen: not diagnosable problems,
        # just facts worth having on the same screen as everything else.
        info = (
            f"Desktop: {'running' if desktop.is_running() else 'stopped'}"
            f"  ·  Cache: {human_size(CACHE_DIR)}  ·  {get_version()}"
        )
        self.app.call_from_thread(self._show_issues, issues, info)

    def _show_issues(self, issues: list[doctor.Issue], info: str) -> None:
        self._issues = issues
        self._info = info
        self.query_one("#doctor-info", Static).update(info)
        table = self.query_one("#doctor-table", DataTable)
        table.clear()
        for issue in issues:
            if issue.ok:
                mark = "[green]●[/green]"
            elif issue.unknown:
                mark = "[yellow]?[/yellow]"
            elif issue.fix is not None:
                mark = "[yellow]○[/yellow]"
            else:
                mark = "[red]○[/red]"
            table.add_row(issue.name, mark, issue.detail)

        count = len(doctor.fixable(issues))
        button = self.query_one("#fix", Button)
        button.disabled = count == 0
        button.label = f"Fix ({count})" if count else "Fix"

    @on(Button.Pressed, "#rescan")
    def _rescan(self) -> None:
        self.scan()

    @on(Button.Pressed, "#diagnose")
    def _diagnose(self) -> None:
        """Same report the start sequence prints on failure, on demand."""
        self.app.push_screen(ActionScreen("Desktop Diagnostics", desktop.collect_diagnostics))

    @on(Button.Pressed, "#dupes")
    def _dupes(self) -> None:
        self.app.push_screen(DupesScreen())

    @on(Button.Pressed, "#audio")
    def _audio(self) -> None:
        self.app.push_screen(ActionScreen("Audio Test", audio.test))

    @on(Button.Pressed, "#bench")
    def _bench(self) -> None:
        self.app.push_screen(ActionScreen("GPU Benchmark", bench.run))

    @on(Button.Pressed, "#fix")
    def _fix(self) -> None:
        issues = self._issues

        def runner(log) -> None:
            repaired, attempted = doctor.run_fixes(issues, log)
            if attempted:
                log(f"[bold]{repaired} of {attempted} repaired.[/bold]")
                if repaired < attempted:
                    log("Remaining problems need you — see the detail column.")

        self.app.push_screen(ActionScreen("Doctor — Fix", runner))

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class DupesScreen(CopyableScreen):
    """Termux packages the container already provides.

    Removal is never folded into Doctor's Fix: uninstalling from Termux is
    the user's call about their own environment, not a repair.
    """

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._dupes: list[doctor.Duplicate] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Termux duplicates", classes="screen-title")
        yield Static(
            "proot-distro binds Termux's $PREFIX into the container and adds "
            "its bin directory to the guest PATH, so these tools exist twice. "
            "The container's copy wins; the Termux one only runs when Debian "
            "lacks the tool.",
            id="dupes-note",
        )
        yield ScrollableTable(id="dupes-table", cursor_type="row", zebra_stripes=True)
        with Grid(classes="row2"):
            yield Button("Re-scan", id="rescan")
            yield Button("Remove", id="remove", variant="error", disabled=True)
        with Horizontal(id="action-buttons"):
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#dupes-table", DataTable).add_columns(
            "Termux package", "Container provides"
        )
        self.query_one("#copy", Button).tooltip = "Copy this list"
        self.query_one("#remove", Button).tooltip = "Uninstall these from Termux only"

    def on_screen_resume(self) -> None:
        self.scan()

    @work(thread=True)
    def scan(self) -> None:
        self.app.call_from_thread(self._show, doctor.termux_duplicates())

    def _show(self, dupes: list[doctor.Duplicate]) -> None:
        self._dupes = dupes
        table = self.query_one("#dupes-table", DataTable)
        table.clear()
        for dupe in dupes:
            table.add_row(dupe.package, dupe.binary)

        button = self.query_one("#remove", Button)
        button.disabled = not dupes
        button.label = f"Remove ({len(dupes)})" if dupes else "Remove"

    def copy_payload(self) -> str:
        lines = [f"PDM Termux duplicates — {get_version()}", ""]
        if not self._dupes:
            lines.append("(none)")
        lines += [f"{d.package:<14} -> container has {d.binary}" for d in self._dupes]
        return "\n".join(lines)

    @on(Button.Pressed, "#rescan")
    def _rescan(self) -> None:
        self.scan()

    @on(Button.Pressed, "#remove")
    def _remove(self) -> None:
        packages = [d.package for d in self._dupes]
        if not packages:
            return

        def run(log) -> None:
            if doctor.remove_termux_packages(packages, log):
                log("")
                log("[green]Removed.[/green] The container's copies are unaffected.")
            else:
                log("")
                log("[red]Removal failed or was refused.[/red]")

        self.app.push_screen(
            ConfirmScreen(
                "Remove from Termux",
                "Uninstall these from Termux, keeping the container's copies:\n\n"
                + ", ".join(packages)
                + "\n\nThe container is unaffected. Anything you run directly in "
                "Termux that needs these will stop working.",
                confirm_label="Uninstall",
            ),
            when_confirmed(self.app, lambda: ActionScreen("Remove duplicates", run)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class StoreScreen(CopyableScreen):
    """Search the container's package lists and install from them."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._results: list[packages.Package] = []
        # Kept alongside the widget: Static does not expose its text back.
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Store", classes="screen-title")
        yield Input(placeholder="Search packages, e.g. neovim", id="query")
        yield Static("", id="store-status")
        yield ScrollableTable(id="store-table", cursor_type="row", zebra_stripes=True)
        with Grid(classes="row3"):
            yield Button("Mirror", id="mirror")
            yield Button("Repos", id="repos")
            yield Button("Installed", id="installed")
        with Grid(classes="row3"):
            yield Button("Install", id="install", variant="success", disabled=True)
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#store-table", DataTable).add_columns("", "Package", "Description")
        self.query_one("#copy", Button).tooltip = "Copy these results"
        self.query_one("#install", Button).tooltip = "Install the highlighted package"
        self.query_one("#installed", Button).tooltip = "List everything installed"
        self.query_one("#query", Input).focus()
        self._status("Loading curated tools...")
        self.load_curated()

    def _status(self, message: str) -> None:
        self.status_text = message
        self.query_one("#store-status", Static).update(message)

    @on(Input.Submitted, "#query")
    def _submitted(self, event: Input.Submitted) -> None:
        term = event.value.strip()
        if not term:
            self._status("Loading curated tools...")
            self.load_curated()
            return
        self._status(f"Searching for '{term}'...")
        self.run_search(term)

    @on(Button.Pressed, "#installed")
    def _show_installed(self) -> None:
        self._status("Loading installed packages...")
        self.load_installed()

    @work(thread=True)
    def load_curated(self) -> None:
        results, error = packages.curated()
        self.app.call_from_thread(self._show, results, error, kind="curated")

    @work(thread=True)
    def load_installed(self) -> None:
        results, error = packages.installed()
        self.app.call_from_thread(self._show, results, error, kind="installed")

    @work(thread=True)
    def run_search(self, term: str) -> None:
        results, error = packages.search(term)
        self.app.call_from_thread(self._show, results, error)

    def _show(
        self, results: list[packages.Package], error: str | None, kind: str = "search"
    ) -> None:
        self._results = results
        table = self.query_one("#store-table", DataTable)
        table.clear()

        for pkg in results:
            mark = "[green]I[/green]" if pkg.installed else ""
            table.add_row(mark, pkg.name, pkg.description[:70])

        button = self.query_one("#install", Button)
        button.disabled = not results

        if error:
            self._status(error)
            self.notify(error, title="Store", severity="error")
        elif kind == "curated":
            installed = sum(1 for p in results if p.installed)
            self._status(
                f"{len(results)} curated tool(s), {installed} already installed "
                "(marked I). Search to find more, or highlight one and press Install."
            )
        elif kind == "installed":
            self._status(f"{len(results)} package(s) installed in the container.")
        else:
            installed = sum(1 for p in results if p.installed)
            self._status(
                f"{len(results)} result(s), {installed} already installed "
                "(marked I). Highlight one and press Install."
            )

    def _selected(self) -> packages.Package | None:
        table = self.query_one("#store-table", DataTable)
        if not self._results:
            return None
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._results)):
            return None
        return self._results[row]

    @on(DataTable.RowHighlighted, "#store-table")
    def _row_highlighted(self) -> None:
        # A table row is a thin touch target; naming the pick here — not
        # only inside the confirm dialog — catches a mistap before Install
        # is even pressed, not only before it runs.
        pkg = self._selected()
        if pkg is not None:
            state = "installed" if pkg.installed else "not installed"
            self._status(f"Selected: {pkg.name} ({state})")

    @on(Button.Pressed, "#install")
    def _install(self) -> None:
        pkg = self._selected()
        if pkg is None:
            self._status("Highlight a row first.")
            return

        if pkg.installed:
            self._status(f"{pkg.name} is already installed.")
            return

        def run(log) -> None:
            if packages.install([pkg.name], log):
                log("")
                log(f"[green]{pkg.name} installed.[/green]")
            else:
                log("")
                log(f"[red]Could not install {pkg.name}.[/red]")

        self.app.push_screen(
            ConfirmScreen(
                f"Install {pkg.name}",
                f"{pkg.description}\n\n"
                "This installs into the container with apt. "
                "Termux is not touched.",
                confirm_label="Install",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Install {pkg.name}", run)),
        )

    def copy_payload(self) -> str:
        lines = [f"PDM package search — {get_version()}", ""]
        lines.append(self.status_text)
        lines.append("")
        if not self._results:
            lines.append("(no results)")
        lines += [
            f"{'I' if p.installed else ' '} {p.name:<28} {p.description}"
            for p in self._results
        ]
        return "\n".join(lines)

    @on(Button.Pressed, "#mirror")
    def _mirror(self) -> None:
        self.app.push_screen(MirrorScreen())

    @on(Button.Pressed, "#repos")
    def _repos(self) -> None:
        self.app.push_screen(ReposScreen())

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class MirrorScreen(CopyableScreen):
    """Pick which Debian mirror the container fetches from.

    The list comes from Debian's own deb822 masterlist rather than being
    hardcoded, and candidates are measured by downloading from them. Latency
    ranking, which is what netselect-apt does, says little about throughput
    on mobile data.
    """

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._mirrors: list[tuple[str, str, str]] = list(packages.SEED_MIRRORS)
        self._speeds: dict[str, float | None] = {}
        # Kept alongside the widget: Static does not expose its text back.
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Debian mirror", classes="screen-title")
        yield Static("", id="mirror-current")
        yield ScrollableTable(id="mirror-table", cursor_type="row", zebra_stripes=True)
        with Grid(classes="row3"):
            yield Button("Refresh", id="refresh")
            yield Button("Measure", id="measure")
            yield Button("Use", id="use", variant="success")
        with Grid(classes="row2"):
            yield Button("C", id="copy")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#mirror-table", DataTable).add_columns(
            "Mirror", "Where", "KB/s", "URI"
        )
        self._fill()
        self._refresh_current()

    def on_screen_resume(self) -> None:
        self._refresh_current()

    def _set_current(self, message: str) -> None:
        self.status_text = message
        self.query_one("#mirror-current", Static).update(message)

    def _refresh_current(self) -> None:
        current = packages.current_mirror()
        self._set_current(
            f"Currently: {current}" if current else "No sources file in the container"
        )

    def _fill(self) -> None:
        table = self.query_one("#mirror-table", DataTable)
        table.clear()
        for name, where, uri in self._mirrors:
            speed = self._speeds.get(uri)
            if speed is None:
                shown = "-" if uri not in self._speeds else "failed"
            else:
                shown = f"{speed:,.0f}"
            table.add_row(name, where, shown, uri)

    def _selected_mirror(self) -> tuple[str, str, str] | None:
        row = self.query_one("#mirror-table", DataTable).cursor_row
        if row is None or not (0 <= row < len(self._mirrors)):
            return None
        return self._mirrors[row]

    @on(DataTable.RowHighlighted, "#mirror-table")
    def _row_highlighted(self) -> None:
        # Reuses the "Currently" line rather than adding a row for this: on
        # a phone-height screen there is no free row to spare, and a table
        # row is thin enough to mistap, so naming the pick here — not only
        # inside the confirm dialog — catches that before Use is pressed.
        # on_screen_resume and every fetch/measure already restore this
        # line afterward, so nothing is permanently lost.
        mirror = self._selected_mirror()
        if mirror is not None:
            name, where, _uri = mirror
            self._set_current(f"Selected: {name} — {where}")

    @on(Button.Pressed, "#refresh")
    def _refresh(self) -> None:
        self._set_current("Fetching Debian's mirror list...")
        self.fetch()

    @work(thread=True)
    def fetch(self) -> None:
        mirrors = packages.fetch_mirrors()
        self.app.call_from_thread(self._took_list, mirrors)

    def _took_list(self, mirrors: list[tuple[str, str, str]]) -> None:
        self._mirrors = mirrors
        self._speeds = {}
        self._fill()
        self._refresh_current()

    @on(Button.Pressed, "#measure")
    def _measure(self) -> None:
        self._set_current(f"Measuring {len(self._mirrors)} mirrors, this takes a moment...")
        self.measure_all()

    @work(thread=True)
    def measure_all(self) -> None:
        for _name, _where, uri in list(self._mirrors):
            speed = packages.measure_mirror(uri)
            self.app.call_from_thread(self._took_speed, uri, speed)
        self.app.call_from_thread(self._sort_by_speed)

    def _took_speed(self, uri: str, speed: float | None) -> None:
        self._speeds[uri] = speed
        self._fill()

    def _sort_by_speed(self) -> None:
        self._mirrors.sort(key=lambda m: -(self._speeds.get(m[2]) or -1))
        self._fill()
        fastest = next(
            ((n, self._speeds[u]) for n, _, u in self._mirrors if self._speeds.get(u)),
            None,
        )
        self._set_current(
            f"Fastest: {fastest[0]} at {fastest[1]:,.0f} KB/s — highlight it and press Use"
            if fastest
            else "No mirror answered."
        )

    def copy_payload(self) -> str:
        lines = [f"PDM mirrors - {get_version()}", ""]
        lines.append(f"current: {packages.current_mirror()}")
        lines.append("")
        for name, where, uri in self._mirrors:
            speed = self._speeds.get(uri)
            rate = f"{speed:,.0f} KB/s" if speed else ("failed" if uri in self._speeds else "-")
            lines.append(f"  {rate:>12}  {name:<34} {where}")
        return "\n".join(lines)

    @on(Button.Pressed, "#use")
    def _use(self) -> None:
        mirror = self._selected_mirror()
        if mirror is None:
            self.notify("Highlight a mirror first.", severity="warning")
            return
        name, where, uri = mirror

        def run(log) -> None:
            packages.set_mirror(uri, log)

        self.app.push_screen(
            ConfirmScreen(
                f"Use {name}",
                f"{where}\n\n{uri}\n\n"
                "The container's sources are rewritten and the package lists "
                "refetched. Termux keeps its own mirror.",
                confirm_label="Switch",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Mirror: {name}", run)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class ReposScreen(CopyableScreen):
    """Third-party apt repositories, added with their signing keys."""

    BINDINGS = [("escape", "back", "Back")]

    NOTE = (
        "Each is written with its own signing key under /etc/apt/keyrings, "
        "so apt verifies it the same way it verifies Debian."
    )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Extra repositories", classes="screen-title")
        yield Static(self.NOTE, id="repos-note")
        yield ScrollableTable(id="repos-table", cursor_type="row", zebra_stripes=True)
        with Grid(classes="row3"):
            yield Button("Add", id="add", variant="success")
            yield Button("Enable", id="enable")
            yield Button("Remove", id="remove", variant="error")
        with Grid(classes="row2"):
            yield Button("Re-scan", id="rescan")
            yield Button("C", id="copy")
        yield Button("Back", id="back", variant="primary")
        yield Footer()

    def __init__(self) -> None:
        super().__init__()
        self._repos: list[packages.Repo] = []
        # Kept alongside the widget: Static does not expose its text back.
        self.status_text = self.NOTE

    def _note(self, message: str) -> None:
        self.status_text = message
        self.query_one("#repos-note", Static).update(message)

    def on_mount(self) -> None:
        self.query_one("#repos-table", DataTable).add_columns(
            "", "Repository", "What it gives you"
        )
        self._fill()

    def on_screen_resume(self) -> None:
        self._fill()

    def _fill(self) -> None:
        # Built-in repos first, then anything added by hand or in an earlier
        # session that this screen would otherwise have no record of.
        self._repos = list(packages.REPOS) + packages.discovered_custom_repos()
        table = self.query_one("#repos-table", DataTable)
        table.clear()
        for repo in self._repos:
            mark = "[green]on[/green]" if packages.repo_enabled(repo) else ""
            table.add_row(mark, repo.name, repo.description)
        # The list just changed shape, so any earlier highlight no longer
        # points at what it used to.
        self._note(self.NOTE)

    def _selected(self):
        row = self.query_one("#repos-table", DataTable).cursor_row
        if row is None or not (0 <= row < len(self._repos)):
            return None
        return self._repos[row]

    @on(DataTable.RowHighlighted, "#repos-table")
    def _row_highlighted(self) -> None:
        # Reuses the note line rather than adding a row for this: on a
        # phone-height screen there is no free row to spare, and a table
        # row is thin enough to mistap, so naming the pick here — not only
        # inside the confirm dialog — catches that before Enable/Remove is
        # even pressed.
        repo = self._selected()
        if repo is not None:
            state = "enabled" if packages.repo_enabled(repo) else "not enabled"
            self._note(f"Selected: {repo.name} ({state})")

    def copy_payload(self) -> str:
        lines = [f"PDM repositories - {get_version()}", ""]
        lines += [
            f"{'on ' if packages.repo_enabled(r) else '   '} {r.name:<12} {r.description}"
            for r in self._repos
        ]
        return "\n".join(lines)

    @on(Button.Pressed, "#add")
    def _add(self) -> None:
        self.app.push_screen(AddRepoScreen())

    @on(Button.Pressed, "#rescan")
    def _rescan(self) -> None:
        self._fill()

    @on(Button.Pressed, "#enable")
    def _enable(self) -> None:
        repo = self._selected()
        if repo is None:
            self.notify("Highlight a repository first.", severity="warning")
            return
        if packages.repo_enabled(repo):
            self.notify(f"{repo.name} is already enabled.", severity="warning")
            return

        def run(log) -> None:
            packages.add_repo(repo, log)

        if repo.key_url:
            provenance = f"\n\nIts signing key comes from:\n{repo.key_url}"
        else:
            provenance = "\n\nNo new key: this is the Debian archive you already trust."

        self.app.push_screen(
            ConfirmScreen(
                f"Enable {repo.name}",
                repo.description
                + provenance
                + "\n\nA repository you enable can install software on this system.",
                confirm_label="Enable",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Enable {repo.name}", run)),
        )

    @on(Button.Pressed, "#remove")
    def _remove(self) -> None:
        repo = self._selected()
        if repo is None:
            self.notify("Highlight a repository first.", severity="warning")
            return

        def run(log) -> None:
            packages.remove_repo(repo, log)

        self.app.push_screen(
            ConfirmScreen(
                f"Remove {repo.name}",
                "The repository and its key are deleted. Packages already "
                "installed from it stay, but stop receiving updates.",
                confirm_label="Remove",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Remove {repo.name}", run)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class AddRepoScreen(Screen):
    """A custom repository: name, URI, suites, components, signing key."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Add repository", classes="screen-title")
        with VerticalScroll(id="add-repo-form"):
            yield Static(
                "A signing key is required — nothing here is added to apt's "
                "trusted set otherwise, and packages from it would fail to verify.",
                id="add-repo-note",
            )
            yield Label("Name (used as the filename)")
            yield Input(placeholder="e.g. syncthing", id="repo-name")
            yield Label("Repository URI")
            yield Input(placeholder="https://apt.syncthing.net/", id="repo-uri")
            yield Label("Suites")
            yield Input(placeholder="syncthing", id="repo-suites")
            yield Label("Components")
            yield Input(placeholder="release", value="main", id="repo-components")
            yield Label("Signing key URL")
            yield Input(placeholder="https://.../key.gpg", id="repo-key")
            yield Static("", id="add-repo-status")
        with Grid(classes="row2"):
            yield Button("Add", id="submit", variant="success")
            yield Button("Back", id="back", variant="primary")
        yield Footer()

    def _status(self, message: str) -> None:
        self.status_text = message
        self.query_one("#add-repo-status", Static).update(message)

    @on(Button.Pressed, "#submit")
    def _submit(self) -> None:
        name = self.query_one("#repo-name", Input).value.strip()
        uri = self.query_one("#repo-uri", Input).value.strip()
        suites = self.query_one("#repo-suites", Input).value.strip()
        components = self.query_one("#repo-components", Input).value.strip()
        key_url = self.query_one("#repo-key", Input).value.strip()

        problems = packages.validate_custom_repo(name, uri, suites, components, key_url)
        if problems:
            self._status("\n".join(f"- {p}" for p in problems))
            return

        repo = packages.build_custom_repo(name, uri, suites, components, key_url)

        def run(log) -> None:
            packages.add_repo(repo, log)

        self.app.push_screen(
            ConfirmScreen(
                f"Add {repo.name}",
                f"{uri}\nSuites: {suites}  Components: {components}\n\n"
                f"Signing key from:\n{key_url}\n\n"
                "A repository you add can install software on this system.",
                confirm_label="Add",
            ),
            when_confirmed(self.app, lambda: ActionScreen(f"Add {repo.name}", run)),
        )

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.app.pop_screen()


class PDMApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "pdm"

    def __init__(self) -> None:
        super().__init__()
        self.restart_requested = False

    def on_mount(self) -> None:
        self.sub_title = get_version()
        self.push_screen(MainScreen())

    def request_restart(self) -> None:
        """Leave Textual first, then relaunch from main().

        Exiting before re-executing matters: it restores the terminal out of
        the alternate screen and raw mode. Replacing the process from inside a
        running app would leave the terminal wedged.
        """
        self.restart_requested = True
        self.exit()


def main() -> None:
    app = PDMApp()
    app.run()

    if not app.restart_requested:
        return

    # execv, not another App().run(): the point of restarting is to load code
    # that git just changed, and the old modules are already imported.
    try:
        os.execv(sys.executable, [sys.executable, "-m", "installer.app"])
    except OSError as e:
        print(f"Could not restart automatically ({e}).")
        print("Run pdm again to pick up the update.")


if __name__ == "__main__":
    main()
