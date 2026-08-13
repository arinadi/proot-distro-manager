"""Manual Install: install any distro image into the one container slot
PDM manages, not just the prebuilt Debian + XFCE one.

    python tests/test_manual_install.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run
from textual.widgets import Input

from installer import system
from installer.app import ConfirmScreen, MainScreen, ManualInstallScreen, PDMApp


def test_valid_image_ref_accepts_oci_refs_rejects_shell_syntax() -> None:
    """This ref reaches a shell command (proot-distro install <ref> --name
    ...), same exposure as Store's package-name validation — just a wider
    charset, since an OCI ref has slashes and colons a package name never
    does."""
    for good in (
        "debian:13", "ubuntu:24.04", "alpine:latest", "archlinux",
        "ghcr.io/org/image:tag", "quay.io/user/app:1.0", "fedora",
    ):
        check(system.valid_image_ref(good), f"{good!r} should be accepted")

    hostile = [
        "a; rm -rf /", "a && whoami", "a | tee x", "a`id`", "a$(id)",
        "a\nb", "a b", "'", '"', "$PATH", "-x", "", "x" * 200,
    ]
    for term in hostile:
        check(not system.valid_image_ref(term), f"{term!r} should be rejected")


def test_pull_custom_image_refuses_invalid_ref() -> None:
    """The screen already validates before this is ever called, but the
    function itself must refuse too — it is not the only caller."""
    original_run_cmd = system.run_cmd
    original_stream_cmd = system.stream_cmd
    calls: list[str] = []

    def fake_run_cmd(cmd, timeout=60):
        calls.append(cmd)
        return 0, ""

    def fake_stream_cmd(cmd, log, timeout=1800):
        calls.append(cmd)
        return 0

    system.run_cmd = fake_run_cmd
    system.stream_cmd = fake_stream_cmd
    try:
        ok = system.pull_custom_image("a; rm -rf /", lambda m: None)
        check(not ok, "a hostile ref must not be accepted")
        check(calls == [], "a hostile ref must never reach a shell command")
    finally:
        system.run_cmd = original_run_cmd
        system.stream_cmd = original_stream_cmd


async def test_manual_install_screen() -> None:
    """Navigation, the preset quick-picks, and validation on the form —
    each preset must fill the field with its own ref, not some other
    preset's (a copy-paste risk with five near-identical button handlers
    collapsed into one)."""
    app = PDMApp()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#manual-install")
        await pilot.pause()
        check(isinstance(app.screen, ManualInstallScreen), f"got {app.screen!r}")

        # Empty submit must not proceed.
        await pilot.click("#submit")
        await pilot.pause()
        check(isinstance(app.screen, ManualInstallScreen), "empty submit left the form")
        check(app.screen.status_text, "no validation message for an empty ref")

        # Each preset fills the field with its own ref.
        await pilot.click("#preset-alpine")
        await pilot.pause()
        check(
            app.screen.query_one("#image-ref", Input).value == "alpine:latest",
            "the Alpine preset did not fill in alpine:latest",
        )
        await pilot.click("#preset-fedora")
        await pilot.pause()
        check(
            app.screen.query_one("#image-ref", Input).value == "fedora:latest",
            "the Fedora preset overwrote with the wrong ref",
        )

        # A hostile value must be rejected, not reach a shell command.
        # Small sleeps around each #submit click, not just pilot.pause():
        # two clicks on the same button in quick succession is a known
        # pacing issue in Textual's test pilot (see test_add_repo_screen).
        app.screen.query_one("#image-ref", Input).value = "a; rm -rf /"
        await asyncio.sleep(0.15)
        await pilot.click("#submit")
        await asyncio.sleep(0.15)
        await pilot.pause()
        check(isinstance(app.screen, ManualInstallScreen), "a hostile ref was not rejected")

        # A valid ref reaches confirmation.
        app.screen.query_one("#image-ref", Input).value = "ubuntu:24.04"
        await asyncio.sleep(0.15)
        await pilot.click("#submit")
        await asyncio.sleep(0.15)
        await pilot.pause()
        check(isinstance(app.screen, ConfirmScreen), "a valid ref did not reach confirmation")
        await pilot.click("#cancel")
        await pilot.pause()
        check(isinstance(app.screen, ManualInstallScreen), "cancel discarded the form")

        await pilot.click("#back")
        await pilot.pause()
        check(isinstance(app.screen, MainScreen), "back did not return to the menu")


TESTS = [
    test_valid_image_ref_accepts_oci_refs_rejects_shell_syntax,
    test_pull_custom_image_refuses_invalid_ref,
    test_manual_install_screen,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
