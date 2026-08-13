"""installer/packages.py: Store search/install, mirrors, custom repos.

    python tests/test_packages.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import packages


def _sample_sources(security_uri: str = "https://security.debian.org/debian-security") -> str:
    return (
        "Types: deb\n"
        "URIs: http://deb.debian.org/debian/\n"
        "Suites: trixie trixie-updates\n"
        "Components: main contrib non-free non-free-firmware\n"
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
        "\n"
        "Types: deb\n"
        f"URIs: {security_uri}\n"
        "Suites: trixie-security\n"
        "Components: main contrib non-free non-free-firmware\n"
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
    )


def test_set_mirror_protects_security() -> None:
    """Regression, two rounds.

    Round 1 shipped: switching the mirror repointed security at it too,
    which most mirrors do not carry, and apt exited 100. The fix repointed
    security to the canonical URI whenever it recognised the stanza — by
    guessing from the URI's own content.

    Round 2 was reported from a device: guessing from the URI stopped
    working the moment the URI was already wrong, which is exactly the
    state a container corrupted by round 1 was in. A stanza is identified
    by its Suites field now, which a bad URI cannot obscure, and every
    switch repairs security regardless of what it currently says.
    """
    fake_root = tempfile.mkdtemp()
    target = os.path.join(fake_root, "etc", "apt", "sources.list.d", "debian.sources")
    os.makedirs(os.path.dirname(target))

    original_container_path = packages.container_path
    original_update_lists = packages.update_lists
    packages.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))

    def write(content: str) -> None:
        with open(target, "w", newline="\n") as f:
            f.write(content)

    try:
        # Round 1: a healthy file, switching the mirror must not touch
        # security's URI beyond normalising it to the canonical form.
        write(_sample_sources())
        packages.update_lists = lambda log: True
        check(
            packages.set_mirror("http://kartolo.sby.datautama.net.id/debian/", lambda m: None),
            "set_mirror reported failure on a working update",
        )
        result = open(target).read()
        check(
            "http://kartolo.sby.datautama.net.id/debian/" in result,
            "the main archive was not repointed",
        )
        check(
            packages.CANONICAL_SECURITY_URI in result,
            "security did not end up at the canonical URI",
        )

        # Round 2: the exact shape reported from a device — security already
        # corrupted to an unrelated mirror by an earlier, buggy switch, with
        # nothing about its URI left to suggest it was ever security.
        write(_sample_sources(security_uri="http://werog.interkoneksimedia.co.id/debian/"))
        check(
            packages.security_uri() == "http://werog.interkoneksimedia.co.id/debian/",
            "test setup did not reproduce the corrupted state",
        )
        check(
            packages.set_mirror("http://kartolo.sby.datautama.net.id/debian/", lambda m: None),
            "set_mirror reported failure on a working update",
        )
        check(
            packages.security_uri() == packages.CANONICAL_SECURITY_URI,
            "pre-existing corruption survived the switch",
        )

        # repair_security() must also fix it standalone, since Doctor offers
        # it independently of switching mirrors.
        write(_sample_sources(security_uri="http://werog.interkoneksimedia.co.id/debian/"))
        check(packages.repair_security(lambda m: None), "repair_security reported failure")
        check(
            packages.security_uri() == packages.CANONICAL_SECURITY_URI,
            "repair_security did not fix a corrupted stanza",
        )

        # Failure case: a mirror apt cannot use must roll back rather than
        # leave the container stuck.
        write(_sample_sources())
        packages.update_lists = lambda log: False
        check(
            not packages.set_mirror("http://bad-mirror.invalid/debian/", lambda m: None),
            "set_mirror reported success despite update_lists failing",
        )
        restored = open(target).read()
        check(
            "bad-mirror.invalid" not in restored,
            "the bad mirror was left in place instead of rolling back",
        )
        check(
            "http://deb.debian.org/debian/" in restored,
            "the original mirror was not restored",
        )
    finally:
        packages.container_path = original_container_path
        packages.update_lists = original_update_lists


def test_mirror_reapplied_after_container_install() -> None:
    """A Reset reinstalls the image, which reverts sources.list to
    DEFAULT_MIRROR — a previously measured mirror choice (Settings) must
    come back on its own rather than silently reverting every time."""
    from installer import config

    fake_root = tempfile.mkdtemp()
    target = os.path.join(fake_root, "etc", "apt", "sources.list.d", "debian.sources")
    os.makedirs(os.path.dirname(target))

    original_container_path = packages.container_path
    original_update_lists = packages.update_lists
    original_mirror_key = config.get(packages.MIRROR_KEY)
    packages.container_path = lambda p: os.path.join(fake_root, p.lstrip("/"))
    packages.update_lists = lambda log: True

    def write(content: str) -> None:
        with open(target, "w", newline="\n") as f:
            f.write(content)

    try:
        config.unset(packages.MIRROR_KEY)
        write(_sample_sources())
        check(
            packages.reapply_saved_mirror(lambda m: None),
            "a no-op reapply (nothing saved yet) reported failure",
        )
        check(
            packages.current_mirror() == "http://deb.debian.org/debian/",
            "a no-op reapply touched the sources file",
        )

        fast_mirror = "http://kartolo.sby.datautama.net.id/debian/"
        check(
            packages.set_mirror(fast_mirror, lambda m: None),
            "set_mirror reported failure on a working update",
        )
        check(
            config.get(packages.MIRROR_KEY) == fast_mirror,
            "set_mirror did not remember the choice for later",
        )

        # A fresh container image always starts back at the default mirror.
        write(_sample_sources())
        check(
            packages.current_mirror() == "http://deb.debian.org/debian/",
            "test setup did not reproduce a freshly reinstalled container",
        )
        check(
            packages.reapply_saved_mirror(lambda m: None),
            "reapply reported failure",
        )
        check(
            packages.current_mirror() == fast_mirror,
            "the saved mirror was not reapplied after reinstall",
        )
    finally:
        packages.container_path = original_container_path
        packages.update_lists = original_update_lists
        if original_mirror_key is None:
            config.unset(packages.MIRROR_KEY)
        else:
            config.set_value(packages.MIRROR_KEY, original_mirror_key)


def test_package_terms_reject_shell_metacharacters() -> None:
    """Search terms and package names reach a shell, so they are validated."""
    for good in ("neovim", "python3-pip", "libgl1-mesa-dri", "g++", "bat"):
        check(packages.valid_term(good), f"{good!r} should be accepted")

    hostile = [
        "a; rm -rf /", "a && whoami", "a | tee x", "a`id`", "a$(id)",
        "a\nb", "../etc/passwd", "a b", "'", '"', "$PATH", "a>b", "",
        "x" * 80,
    ]
    for term in hostile:
        check(not packages.valid_term(term), f"{term!r} should be rejected")

    lines: list[str] = []
    check(
        not packages.install(["neovim; rm -rf /"], lines.append),
        "install accepted a name with shell syntax",
    )
    check(any("Refusing" in line for line in lines), f"no refusal logged: {lines}")

    results, error = packages.search("a; rm -rf /")
    check(results == [], "a hostile search returned results")
    check(error is not None, "a hostile search reported no error")


def test_search_notices_missing_package_lists() -> None:
    """Regression: the image ships with no apt lists.

    Every Dockerfile layer ends with rm -rf /var/lib/apt/lists/*, so on a
    fresh container apt-cache matches nothing and every search looked like
    "no such package" rather than "nothing to search".
    """
    check(
        isinstance(packages.lists_present(), bool),
        "lists_present must answer with a bool",
    )

    body = packages.SEARCH_SCRIPT
    check("apt-cache search" in body, "the search no longer uses apt-cache")
    check("apt-get update" in packages.UPDATE_SCRIPT, "the update script does not update")

    # Both paths that install must refresh first, or they fail on a fresh
    # container the same way search did.
    check("apt-get update" in packages.INSTALL_SCRIPT, "install does not refresh lists")


def test_validate_custom_repo() -> None:
    """The Add Repo form is a shell-adjacent surface: values land in a
    written apt config and one field's URL is later handed to curl."""
    check(
        packages.validate_custom_repo("", "", "", "", "") != [],
        "empty fields must be rejected",
    )
    check(
        packages.validate_custom_repo(
            "syncthing",
            "https://apt.syncthing.net/",
            "syncthing",
            "release",
            "https://syncthing.net/release-key.gpg",
        )
        == [],
        "a well-formed custom repo was rejected",
    )

    # A name colliding with a built-in repo, or one already added, must be
    # refused rather than silently overwriting it.
    for builtin in packages.REPOS:
        check(
            packages.validate_custom_repo(
                builtin.name, "https://x.invalid/", "a", "main", "https://x.invalid/key"
            )
            != [],
            f"the built-in name '{builtin.name}' was accepted for a custom repo",
        )

    # A repo with no key cannot be verified, so it must not be
    # constructible — this is not optional the way it is for backports.
    check(
        packages.validate_custom_repo("nokey", "https://x.invalid/", "a", "main", "") != [],
        "a repo with no signing key was accepted",
    )

    # A newline in any field could smuggle a second apt directive into the
    # stanza (e.g. an extra Signed-By: pointing somewhere else).
    for hostile in (
        "https://x.invalid/\nSigned-By: /etc/shadow",
        "https://x.invalid/ extra",
    ):
        check(
            packages.validate_custom_repo("evil", hostile, "a", "main", "https://x.invalid/key")
            != [],
            f"a hostile URI was accepted: {hostile!r}",
        )

    stanza = packages.build_custom_repo(
        "syncthing",
        "https://apt.syncthing.net/",
        "syncthing",
        "release",
        "https://syncthing.net/release-key.gpg",
    ).stanza
    check("URIs: https://apt.syncthing.net/" in stanza, "URI missing from the built stanza")
    check(
        "Signed-By: /etc/apt/keyrings/pdm-syncthing.asc" in stanza,
        "the stanza does not point at the fetched key",
    )


TESTS = [
    test_set_mirror_protects_security,
    test_mirror_reapplied_after_container_install,
    test_package_terms_reject_shell_metacharacters,
    test_search_notices_missing_package_lists,
    test_validate_custom_repo,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
