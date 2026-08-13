"""Search and install Debian packages inside the container.

Search terms and package names reach a shell, so they are validated against a
strict pattern and rejected rather than escaped. The scripts themselves are
written into the container and run by path, which is how everything else here
avoids nested quoting.
"""

from __future__ import annotations

import os
import re
from typing import Callable, NamedTuple

from . import config
from .system import (
    container_command,
    container_path,
    is_installed,
    run_cmd,
    stream_cmd,
    write_container_script,
)

Log = Callable[[str], None]

# Debian package names are lowercase alphanumerics plus . + - and must start
# with an alphanumeric. Search terms are held to the same shape: it costs a
# little expressiveness and removes shell metacharacters entirely.
SAFE_TERM = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}$")

SEARCH_LIMIT = 60

SEARCH_SCRIPT = f"""#!/bin/bash
# $1 = search term. Output: <mark>|<name>|<description>
apt-cache search --names-only "$1" 2>/dev/null | head -{SEARCH_LIMIT} \\
    > /tmp/pdm-search.out
dpkg-query -W -f='${{Package}}\\n' 2>/dev/null | sort -u \\
    > /tmp/pdm-installed.out

while IFS= read -r line; do
    name="${{line%% - *}}"
    desc="${{line#* - }}"
    if grep -qxF "$name" /tmp/pdm-installed.out; then
        printf 'I|%s|%s\\n' "$name" "$desc"
    else
        printf ' |%s|%s\\n' "$name" "$desc"
    fi
done < /tmp/pdm-search.out
"""

UPDATE_SCRIPT = """#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
"""

INSTALL_SCRIPT = """#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y "$@"
"""


class Package(NamedTuple):
    name: str
    description: str
    installed: bool


def valid_term(term: str) -> bool:
    return bool(SAFE_TERM.match(term.strip().lower()))


def _noop(_message: str) -> None:
    pass


INSTALLED_SCRIPT = """#!/bin/bash
dpkg-query -W -f='${Package}\\n' 2>/dev/null
"""

# $1 = summary field to use — binary:Summary exists on the dpkg shipped with
# Debian 12+; falling back would need parsing the multi-line Description
# field, which is not worth it for a container that always ships current
# dpkg. Output: <name>|<summary>
INSTALLED_LIST_SCRIPT = """#!/bin/bash
dpkg-query -W -f='${Package}|${binary:Summary}\\n' 2>/dev/null | sort
"""

# Shown before a search is typed, so an empty query is not just a blank
# table. Dev tools come first — this is a dev-focused container — followed
# by a few simple GUI programs (this ships an XFCE desktop), then general
# CLI utilities.
CURATED_PACKAGES: tuple[tuple[str, str], ...] = (
    ("git", "Distributed version control"),
    ("build-essential", "GCC, make and friends — C/C++ toolchain"),
    ("python3", "Python 3 interpreter"),
    ("python3-pip", "Python package installer"),
    ("nodejs", "JavaScript runtime"),
    ("npm", "Node.js package manager"),
    ("neovim", "Vim-based text editor"),
    ("vim", "Text editor"),
    ("tmux", "Terminal multiplexer"),
    ("openjdk-17-jdk", "Java Development Kit 17"),
    ("golang", "Go programming language"),
    ("cmake", "Build system generator"),
    ("gdb", "GNU debugger"),
    ("sqlite3", "SQLite command-line shell"),
    ("ripgrep", "Fast recursive search (rg)"),
    # Simple GUI programs
    ("geany", "Lightweight GUI code editor/IDE"),
    ("mousepad", "Simple GUI text editor (XFCE)"),
    ("git-gui", "Git GUI — stage, commit, browse"),
    ("gitk", "Git commit history viewer (GUI)"),
    ("meld", "Visual diff and merge tool (GUI)"),
    ("xarchiver", "GUI archive manager (7z/zip/tar)"),
    ("ristretto", "Lightweight image viewer (XFCE)"),
    ("galculator", "GTK calculator (GUI)"),
    # General utilities
    ("p7zip-full", "7-Zip archiver (7z)"),
    ("unzip", "Extract .zip archives"),
    ("zip", "Create .zip archives"),
    ("curl", "Transfer data from URLs"),
    ("wget", "Download files from the web"),
    ("htop", "Interactive process viewer"),
    ("tree", "List directories as a tree"),
    ("jq", "Command-line JSON processor"),
    ("rsync", "Fast file copy/sync"),
    ("ffmpeg", "Audio/video conversion"),
)


def curated(log: Log = _noop) -> tuple[list[Package], str | None]:
    """The curated list above, with install status filled in.

    Descriptions come from CURATED_PACKAGES, not apt-cache, so this works
    even before the package lists have been fetched — only install status
    needs the container, via dpkg-query rather than a search.
    """
    if not is_installed():
        return [], "No container yet — install it from the menu first."

    if not write_container_script("pdm-installed.sh", INSTALLED_SCRIPT):
        return [], "Could not write the installed-check script."

    rc, out = run_cmd(container_command("pdm-installed.sh"), timeout=30)
    log(f"exit {rc}, {len(out.splitlines())} installed package(s)")
    installed = set(out.split()) if rc == 0 else set()

    return [Package(name, desc, name in installed) for name, desc in CURATED_PACKAGES], None


def installed(log: Log = _noop) -> tuple[list[Package], str | None]:
    """Every package installed in the container, name and one-line summary.

    Reads dpkg's own database directly rather than apt-cache, so — like
    curated() — this needs no package lists to have been fetched.
    """
    if not is_installed():
        return [], "No container yet — install it from the menu first."

    if not write_container_script("pdm-installed-list.sh", INSTALLED_LIST_SCRIPT):
        return [], "Could not write the installed-list script."

    rc, out = run_cmd(container_command("pdm-installed-list.sh"), timeout=30)
    log(f"exit {rc}, {len(out.splitlines())} installed package(s)")
    if rc != 0:
        return [], "The container could not be queried — see the output below."

    results = []
    for line in out.splitlines():
        name, _, summary = line.partition("|")
        if not name:
            continue
        results.append(Package(name, summary.strip(), True))

    if not results:
        return [], "No packages found."
    return results, None


def lists_present() -> bool:
    """Whether the container has any apt package lists.

    The image ships without them: every Dockerfile layer ends with
    `rm -rf /var/lib/apt/lists/*` to keep the download small. apt-cache
    searches those lists, so on a fresh container every search matches
    nothing — which reads as "the package does not exist" rather than "there
    is nothing to search".
    """
    try:
        entries = os.listdir(container_path("/var/lib/apt/lists"))
    except OSError:
        return False
    return any(name not in {"lock", "partial", "auxfiles"} for name in entries)


def update_lists(log: Log) -> bool:
    """Fetch the package lists. Slow, so it streams."""
    if not write_container_script("pdm-update.sh", UPDATE_SCRIPT):
        log("Could not write the update script.")
        return False
    log("Fetching package lists (the image ships without them)...")
    rc = stream_cmd(container_command("pdm-update.sh"), log, timeout=900)
    log(f"apt-get update exit {rc}")
    return rc == 0


def search(term: str, log: Log = _noop) -> tuple[list[Package], str | None]:
    """Search the container's package lists.

    Returns (results, error). An error is a sentence for the user, not a
    traceback — the raw command and its output go to `log`, so a failure can
    be read rather than guessed at.
    """
    term = term.strip().lower()
    if not term:
        return [], "Type something to search for."
    if not valid_term(term):
        return [], "Use letters, digits, dot, plus or dash only."
    if not is_installed():
        return [], "No container yet — install it from the menu first."

    if not lists_present() and not update_lists(log):
        return [], "Could not fetch the package lists — see the output below."

    if not write_container_script("pdm-search.sh", SEARCH_SCRIPT):
        return [], "Could not write the search script."

    command = f"{container_command('pdm-search.sh')} {term}"
    log(f"$ {command}")
    rc, out = run_cmd(command, timeout=120)
    log(f"exit {rc}, {len(out.splitlines())} line(s)")
    for line in out.strip().splitlines()[:6]:
        log(f"  {line}")

    if rc != 0:
        return [], "The container could not be queried — see the output below."

    results = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        mark, name, description = parts
        if not name:
            continue
        results.append(Package(name, description.strip(), mark.strip() == "I"))

    if not results:
        return [], f"Nothing matched '{term}'."
    return results, None


def install(names: list[str], log: Log) -> bool:
    """Install packages into the container."""
    rejected = [n for n in names if not valid_term(n)]
    if rejected:
        log(f"[red]Refusing these names: {', '.join(rejected)}[/red]")
        return False
    if not names:
        log("Nothing selected.")
        return True

    if not write_container_script("pdm-install.sh", INSTALL_SCRIPT):
        log("[red]Could not write the install script.[/red]")
        return False

    log(f"Installing into the container: {', '.join(names)}")
    log("")
    rc = stream_cmd(
        f"{container_command('pdm-install.sh')} {' '.join(names)}",
        log,
        timeout=1800,
    )
    return rc == 0


# ── Mirrors ────────────────────────────────────────────────

# Debian 13 writes deb822 to this file; the older single-line format is kept
# as a fallback because a restored or hand-edited container may still use it.
SOURCES_DEB822 = "/etc/apt/sources.list.d/debian.sources"
SOURCES_LEGACY = "/etc/apt/sources.list"

DEFAULT_MIRROR = "http://deb.debian.org/debian/"

# Remembered so a Reset can put it back — the fresh image otherwise reverts
# to DEFAULT_MIRROR and silently throws away a measured choice.
MIRROR_KEY = "MIRROR_URI"

# Debian publishes a deb822 mirror masterlist, and netselect-apt exists to
# pick from it. Neither is used directly here: netselect-apt writes the old
# sources.list format and ranks by ICMP latency, which says little about
# throughput on mobile data. The list is fetched instead, and candidates are
# measured by downloading from them.
#
# Note the masterlist, not www.debian.org/mirror/mirrors_full: that page is
# HTML meant for people, which netselect-apt scrapes. This one is deb822.
#
# The seed below is the fallback for when the list cannot be fetched — on a
# container whose current mirror is unreachable, which is exactly when someone
# opens this screen. Verified against debian.org/mirror/list.
MIRROR_LIST_URL = "http://mirror-master.debian.org/status/Mirrors.masterlist"

# Only these countries are offered. The full list is hundreds of entries and
# a mirror three continents away is not a useful suggestion.
NEARBY_COUNTRIES = ("Indonesia", "Singapore", "Malaysia", "Australia")

SEED_MIRRORS = (
    ("deb.debian.org", "Global CDN (default)", DEFAULT_MIRROR),
    ("kartolo", "Indonesia — Surabaya", "http://kartolo.sby.datautama.net.id/debian/"),
    ("unair", "Indonesia — Universitas Airlangga", "http://mirror.unair.ac.id/debian/"),
    ("heru", "Indonesia", "http://mr.heru.id/debian/"),
    ("djvg", "Singapore", "http://mirror.djvg.sg/debian/"),
    ("ossmirror", "Singapore", "http://ossmirror.mycloud.services/debian/"),
)

MIRRORS = SEED_MIRRORS


def fetch_mirrors(log: Log = _noop) -> list[tuple[str, str, str]]:
    """Debian's own mirror list, filtered to nearby countries.

    Falls back to the seed list rather than failing: this screen is often
    opened precisely because the current mirror is unreachable.
    """
    log(f"Fetching {MIRROR_LIST_URL}")
    rc, body = run_cmd(f'curl -fsSL --max-time 20 "{MIRROR_LIST_URL}"', timeout=40)
    if rc != 0 or not body.strip():
        log("Could not fetch the list, using the built-in one.")
        return list(SEED_MIRRORS)

    found: list[tuple[str, str, str]] = [SEED_MIRRORS[0]]
    country = ""
    site = ""
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("Country:"):
            country = line.split(":", 1)[1].strip()
        elif line.startswith("Site:"):
            site = line.split(":", 1)[1].strip()
        elif line.startswith("Archive-http:") and site:
            if any(c in country for c in NEARBY_COUNTRIES):
                path = line.split(":", 1)[1].strip()
                where = country.split(None, 1)[-1] if country else "unknown"
                found.append((site, where, f"http://{site}{path}"))
            site = ""

    if len(found) == 1:
        log("The list had no nearby mirrors, using the built-in one.")
        return list(SEED_MIRRORS)

    log(f"{len(found)} nearby mirrors")
    return found


def measure_mirror(uri: str, seconds: int = 8) -> float | None:
    """Throughput in KB/s, or None if the mirror did not answer.

    Measured by fetching a real index file rather than pinging: latency is a
    poor proxy for how fast apt will actually pull packages over mobile data.
    """
    url = uri.rstrip("/") + "/dists/trixie/Release"
    rc, out = run_cmd(
        f'curl -fsS --max-time {seconds} -o /dev/null '
        f'-w "%{{speed_download}}" "{url}"',
        timeout=seconds + 5,
    )
    if rc != 0:
        return None
    try:
        return float(out.strip().replace(",", ".")) / 1024
    except ValueError:
        return None


def _sources_file() -> str | None:
    """Whichever sources file this container actually uses."""
    for path in (SOURCES_DEB822, SOURCES_LEGACY):
        if os.path.exists(container_path(path)):
            return path
    return None


def current_mirror() -> str | None:
    """The URI the container fetches Debian from."""
    path = _sources_file()
    if path is None:
        return None
    try:
        with open(container_path(path), encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("URIs:"):
            return stripped.split(":", 1)[1].strip().split()[0]
        if stripped.startswith("deb ") and "://" in stripped:
            return stripped.split()[1]
    return None


# The only correct value. Regular mirrors are not required to carry
# debian-security at all, so this is never repointed at a chosen mirror —
# only ever restored to this.
CANONICAL_SECURITY_URI = "https://security.debian.org/debian-security"


def _is_security_suite(suite: str) -> bool:
    return suite == "security" or suite.endswith("-security")


def _parse_deb822_stanzas(content: str) -> list[list[str]]:
    """Split a deb822 sources file on its blank-line stanza boundaries."""
    stanzas: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip() == "":
            if current:
                stanzas.append(current)
                current = []
        else:
            current.append(line)
    if current:
        stanzas.append(current)
    return stanzas


def _deb822_stanza_is_security(stanza: list[str]) -> bool:
    for line in stanza:
        stripped = line.strip()
        if stripped.startswith("Suites:"):
            return any(_is_security_suite(s) for s in stripped.split(":", 1)[1].split())
    return False


def _legacy_line_is_security(stripped: str) -> bool:
    # deb URI SUITE [COMPONENTS...] — the suite is the third token.
    parts = stripped.split()
    return len(parts) >= 3 and _is_security_suite(parts[2])


def security_uri() -> str | None:
    """The URI the container's security stanza currently points at.

    Identified by Suites, not by guessing from the URI's content: a URI that
    has already been repointed at an unrelated mirror carries no hint that
    it was meant to be the security archive, which is exactly the state a
    corrupted container is in.
    """
    path = _sources_file()
    if path is None:
        return None
    try:
        with open(container_path(path), encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    if path == SOURCES_DEB822:
        for stanza in _parse_deb822_stanzas(content):
            if not _deb822_stanza_is_security(stanza):
                continue
            for line in stanza:
                stripped = line.strip()
                if stripped.startswith("URIs:"):
                    return stripped.split(":", 1)[1].strip().split()[0]
        return None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("deb ", "deb-src ")) and _legacy_line_is_security(stripped):
            return stripped.split()[1]
    return None


def _repair_deb822(content: str) -> tuple[str, int]:
    stanzas = _parse_deb822_stanzas(content)
    changed = 0
    for stanza in stanzas:
        if not _deb822_stanza_is_security(stanza):
            continue
        for i, line in enumerate(stanza):
            if line.strip().startswith("URIs:"):
                repaired = f"URIs: {CANONICAL_SECURITY_URI}"
                if line != repaired:
                    stanza[i] = repaired
                    changed += 1
    return "\n\n".join("\n".join(s) for s in stanzas) + "\n", changed


def _repair_legacy(content: str) -> tuple[str, int]:
    lines = content.splitlines()
    changed = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("deb ", "deb-src ")) and _legacy_line_is_security(stripped):
            parts = stripped.split()
            if parts[1] != CANONICAL_SECURITY_URI:
                parts[1] = CANONICAL_SECURITY_URI
                lines[i] = " ".join(parts)
                changed += 1
    return "\n".join(lines) + "\n", changed


def repair_security(log: Log) -> bool:
    """Force the security stanza's URI back to CANONICAL_SECURITY_URI.

    Exists on its own, not only inside set_mirror: a container can have a
    broken security URI without anyone touching the mirror this session —
    it could have shipped that way, or been broken by an older build of
    this tool from before Suites-based detection existed, back when the
    check merely inspected the URI and stopped working the moment the URI
    was already wrong.
    """
    path = _sources_file()
    if path is None:
        log("[red]No Debian sources file in the container.[/red]")
        return False

    target = container_path(path)
    try:
        with open(target, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        log(f"[red]Could not read {path}: {e}[/red]")
        return False

    repair = _repair_deb822 if path == SOURCES_DEB822 else _repair_legacy
    new_content, changed = repair(content)

    if not changed:
        log("Security already points at the canonical URI.")
        return True

    try:
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
    except OSError as e:
        log(f"[red]Could not write {path}: {e}[/red]")
        return False

    log(f"security now points at {CANONICAL_SECURITY_URI}")
    return update_lists(log)


def _repoint_deb822_main(content: str, uri: str) -> tuple[str, int]:
    stanzas = _parse_deb822_stanzas(content)
    changed = 0
    for stanza in stanzas:
        if _deb822_stanza_is_security(stanza):
            for i, line in enumerate(stanza):
                if line.strip().startswith("URIs:"):
                    repaired = f"URIs: {CANONICAL_SECURITY_URI}"
                    if line != repaired:
                        stanza[i] = repaired
                        changed += 1
            continue
        for i, line in enumerate(stanza):
            if line.strip().startswith("URIs:"):
                stanza[i] = f"URIs: {uri}"
                changed += 1
    return "\n\n".join("\n".join(s) for s in stanzas) + "\n", changed


def _repoint_legacy_main(content: str, uri: str) -> tuple[str, int]:
    lines = content.splitlines()
    changed = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith(("deb ", "deb-src ")) and "://" in stripped):
            continue
        parts = stripped.split()
        if _legacy_line_is_security(stripped):
            if parts[1] != CANONICAL_SECURITY_URI:
                parts[1] = CANONICAL_SECURITY_URI
                lines[i] = " ".join(parts)
                changed += 1
            continue
        parts[1] = uri
        lines[i] = " ".join(parts)
        changed += 1
    return "\n".join(lines) + "\n", changed


def set_mirror(uri: str, log: Log) -> bool:
    """Point the container's main Debian archive at `uri`.

    Rewritten from the host through the rootfs: no container login, and it
    works on a container that cannot currently reach any mirror at all.

    The security stanza is identified by its Suites field and always forced
    to CANONICAL_SECURITY_URI, never to the chosen mirror. An earlier version
    repointed it at whatever mirror was chosen, which 404s on most mirrors —
    regular mirrors are not required to carry debian-security. A version
    after that left it "as found" when it looked unrelated to security, which
    silently kept a once-corrupted container corrupted forever after, because
    a URI that has already been repointed carries no hint that it was meant
    to be the security archive. Suites does not change, so it is the only
    signal used now.

    The previous sources file is kept and restored if apt cannot use the new
    one. A mirror that does not carry this release leaves apt unusable
    otherwise, and the person who would have to fix it by hand is on a phone.
    """
    path = _sources_file()
    if path is None:
        log("[red]No Debian sources file in the container.[/red]")
        return False

    target = container_path(path)
    try:
        with open(target, encoding="utf-8") as f:
            original = f.read()
    except OSError as e:
        log(f"[red]Could not read {path}: {e}[/red]")
        return False

    repoint = _repoint_deb822_main if path == SOURCES_DEB822 else _repoint_legacy_main
    new_content, changed = repoint(original, uri)

    if not changed:
        log(f"[red]No archive line found in {path}.[/red]")
        return False

    def write(content: str) -> bool:
        try:
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            return True
        except OSError as e:
            log(f"[red]Could not write {path}: {e}[/red]")
            return False

    if not write(new_content):
        return False

    log(f"{path} now points at {uri} ({changed} line(s) changed)")
    log("")

    if update_lists(log):
        config.set_value(MIRROR_KEY, uri)
        return True

    log("")
    log("[yellow]apt could not use that mirror, putting the old sources back.[/yellow]")
    log("[dim]It may not carry this release, or not all of its components.[/dim]")
    if write(original):
        update_lists(log)
    return False


def reapply_saved_mirror(log: Log) -> bool:
    """Put back whatever mirror was chosen last time. Meant to run right
    after a fresh container install — a plain Reset otherwise reverts
    silently to DEFAULT_MIRROR."""
    uri = config.get(MIRROR_KEY)
    if not uri or uri == DEFAULT_MIRROR:
        return True
    log(f"Reapplying the saved mirror: {uri}")
    return set_mirror(uri, log)


# ── Third-party repositories ───────────────────────────────

KEYRING_DIR = "/etc/apt/keyrings"


class Repo(NamedTuple):
    name: str
    description: str
    # deb822 stanza, written to /etc/apt/sources.list.d/<name>.sources
    stanza: str
    # Signing key to fetch, or None when the Debian keyring already covers it.
    key_url: str | None = None


def _repo_file(repo: Repo) -> str:
    return f"/etc/apt/sources.list.d/pdm-{repo.name}.sources"


def _key_file(repo: Repo) -> str:
    return f"{KEYRING_DIR}/pdm-{repo.name}.asc"


REPOS = (
    Repo(
        "backports",
        "Debian backports — newer packages, same archive and key",
        "Types: deb\n"
        "URIs: @MIRROR@\n"
        "Suites: trixie-backports\n"
        "Components: main contrib non-free-firmware\n"
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n",
    ),
    Repo(
        "mozilla",
        "Mozilla — Firefox, tracking release rather than ESR",
        "Types: deb\n"
        "URIs: https://packages.mozilla.org/apt\n"
        "Suites: mozilla\n"
        "Components: main\n"
        f"Signed-By: {KEYRING_DIR}/pdm-mozilla.asc\n",
        "https://packages.mozilla.org/apt/repo-signing-key.gpg",
    ),
    Repo(
        "vscode",
        "Microsoft — Visual Studio Code",
        "Types: deb\n"
        "URIs: https://packages.microsoft.com/repos/code\n"
        "Suites: stable\n"
        "Components: main\n"
        f"Signed-By: {KEYRING_DIR}/pdm-vscode.asc\n",
        "https://packages.microsoft.com/keys/microsoft.asc",
    ),
)


def repo_by_name(name: str) -> Repo | None:
    return next((r for r in REPOS if r.name == name), None)


def repo_enabled(repo: Repo) -> bool:
    return os.path.exists(container_path(_repo_file(repo)))


# ── Custom repositories ─────────────────────────────────────

# apt lines are one line each: a newline in any field lets a value smuggle
# in a second directive. None of these characters belong in a URI, suite or
# component name either, so rejecting them costs nothing real.
SAFE_URI = re.compile(r"^https?://[^\s]+$")
SAFE_WORDS = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.+~_-]*(\s[a-zA-Z0-9][a-zA-Z0-9.+~_-]*)*$")

CUSTOM_PREFIX = "pdm-"
CUSTOM_SUFFIX = ".sources"


def valid_repo_name(name: str) -> bool:
    """Same shape as a package name: it becomes a filename component."""
    return valid_term(name)


def validate_custom_repo(
    name: str, uri: str, suites: str, components: str, key_url: str
) -> list[str]:
    """Field-by-field problems, or an empty list when the repo can be added."""
    problems = []

    if not valid_repo_name(name):
        problems.append("Name: letters, digits, dot, plus or dash only.")
    elif repo_by_name(name) is not None:
        problems.append(f"Name: '{name}' is a built-in repository.")
    elif name in discovered_custom_names():
        problems.append(f"Name: '{name}' is already added.")

    if not SAFE_URI.match(uri.strip()):
        problems.append("URI: must start with http:// or https://, no spaces.")

    if not suites.strip() or not SAFE_WORDS.match(suites.strip()):
        problems.append("Suites: one or more words, e.g. 'stable' or 'trixie main'.")

    if not components.strip() or not SAFE_WORDS.match(components.strip()):
        problems.append("Components: one or more words, e.g. 'main'.")

    # Not optional. A repo signed by nothing apt already trusts cannot be
    # verified, and pointing it at Debian's own key would make apt reject
    # every package from it — the signature simply would not match.
    if not key_url.strip():
        problems.append("Signing key URL: required for a repository apt does not already trust.")
    elif not SAFE_URI.match(key_url.strip()):
        problems.append("Signing key URL: must start with http:// or https://, no spaces.")

    return problems


def build_custom_repo(name: str, uri: str, suites: str, components: str, key_url: str) -> Repo:
    """Assumes validate_custom_repo() already passed."""
    key_path = f"{KEYRING_DIR}/{CUSTOM_PREFIX}{name}.asc"
    stanza = (
        "Types: deb\n"
        f"URIs: {uri.strip()}\n"
        f"Suites: {suites.strip()}\n"
        f"Components: {components.strip()}\n"
        f"Signed-By: {key_path}\n"
    )
    return Repo(name, f"Custom: {uri.strip()}", stanza, key_url.strip())


def discovered_custom_names() -> list[str]:
    """Names of pdm-managed repos in the container beyond REPOS.

    Covers a repo added by an earlier run of this feature, or added by hand
    following the same naming convention.
    """
    directory = container_path("/etc/apt/sources.list.d")
    try:
        entries = os.listdir(directory)
    except OSError:
        return []

    names = []
    for entry in entries:
        if entry.startswith(CUSTOM_PREFIX) and entry.endswith(CUSTOM_SUFFIX):
            name = entry[len(CUSTOM_PREFIX) : -len(CUSTOM_SUFFIX)]
            if repo_by_name(name) is None:
                names.append(name)
    return names


def discovered_custom_repos() -> list[Repo]:
    """Custom repos as Repo objects, read back from what is on disk.

    `.stanza` is left empty: these are only ever passed to repo_enabled() and
    remove_repo(), which use `.name` alone, never to add_repo() again.
    """
    repos = []
    for name in discovered_custom_names():
        path = container_path(f"/etc/apt/sources.list.d/{CUSTOM_PREFIX}{name}{CUSTOM_SUFFIX}")
        uri = ""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("URIs:"):
                        uri = line.split(":", 1)[1].strip().split()[0]
                        break
        except OSError:
            pass
        repos.append(Repo(name, f"Custom: {uri}" if uri else "Custom repository", "", None))
    return repos


def add_repo(repo: Repo, log: Log) -> bool:
    """Write the repository and its key into the container.

    The key is fetched from Termux, which has curl; the vanilla image does
    not, and installing one to install another is a poor trade. deb822
    accepts an ASCII-armoured key directly, so nothing has to be dearmoured.
    """
    if repo.key_url:
        key_target = container_path(_key_file(repo))
        try:
            os.makedirs(os.path.dirname(key_target), exist_ok=True)
        except OSError as e:
            log(f"[red]Could not create {KEYRING_DIR}: {e}[/red]")
            return False

        log(f"Fetching the signing key from {repo.key_url}")
        rc = stream_cmd(f'curl -fsSL "{repo.key_url}" -o "{key_target}"', log, timeout=120)
        if rc != 0 or not os.path.exists(key_target):
            log("[red]Could not fetch the key — the repository was not added.[/red]")
            return False

    stanza = repo.stanza.replace("@MIRROR@", current_mirror() or DEFAULT_MIRROR)
    target = container_path(_repo_file(repo))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(stanza)
    except OSError as e:
        log(f"[red]Could not write {_repo_file(repo)}: {e}[/red]")
        return False

    log(f"Added {_repo_file(repo)}")
    log("")
    return update_lists(log)


def remove_repo(repo: Repo, log: Log) -> bool:
    removed = False
    for path in (_repo_file(repo), _key_file(repo)):
        target = container_path(path)
        if os.path.exists(target):
            try:
                os.remove(target)
                log(f"Removed {path}")
                removed = True
            except OSError as e:
                log(f"[red]Could not remove {path}: {e}[/red]")
                return False
    if not removed:
        log("Nothing to remove.")
        return True
    log("")
    return update_lists(log)
