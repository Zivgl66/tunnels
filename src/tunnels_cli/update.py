"""Is there a newer tunnels, and can this install take it?

There is no PyPI package: tunnels is installed straight from the git URL,
so "what is the latest version" is a question for the GitHub releases API,
and "install it" is a pipx reinstall rather than an upgrade - pip sees the
same URL and calls the requirement satisfied, so upgrade quietly no-ops.
"""

import json
import re
import subprocess
import urllib.request
from collections import namedtuple
from pathlib import Path

REPO = "Zivgl66/tunnels"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PACKAGE = "tunnels-cli"
SOURCE = f"git+https://github.com/{REPO}.git"
TIMEOUT = 3.0


class UpdateError(Exception):
    """Anything that stops us finding out, said in one line."""


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3). Invalid versions sort below releases.

    A checkout with no install reports its version as "dev", which has to
    lose to any real release rather than crash the comparison.
    """
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _fetch(url, timeout):
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": f"tunnels/{PACKAGE}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode()


def latest_release(url=LATEST_URL, timeout=TIMEOUT):
    """(tag, notes url) for the newest published release."""
    try:
        body = json.loads(_fetch(url, timeout))
    except Exception as exc:                     # network, DNS, HTTP, JSON
        raise UpdateError(f"could not reach GitHub: {exc}") from exc
    if not isinstance(body, dict):
        raise UpdateError("GitHub returned an invalid release")
    tag = body.get("tag_name")
    if not isinstance(tag, str) or not parse_version(tag):
        raise UpdateError("GitHub returned an invalid release")
    notes = body.get("html_url")
    if not isinstance(notes, str):
        notes = f"https://github.com/{REPO}/releases"
    return tag, notes


def source_checkout():
    """The git checkout this code runs from, if it runs from one."""
    source = Path(__file__).resolve()
    for parent in source.parents:
        candidate = parent / "src" / "tunnels_cli" / "update.py"
        if (parent / ".git").exists() and candidate.resolve() == source:
            return parent
    return None


# kind is one of: git (pipx from the repo URL), editable (pipx -e a local
# checkout), local (pipx from a local path, copied), checkout (no pipx),
# unknown.
Install = namedtuple("Install", "kind path")


def _pipx_metadata():
    """pipx's own record of how this package was installed, or None."""
    try:
        out = subprocess.run(["pipx", "list", "--json"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def install_method(package=PACKAGE):
    """How this copy was installed, which decides what updating it means.

    pipx records the spec it installed from, so this is a lookup rather
    than a guess. Without pipx - running straight from a clone - the
    checkout itself is the install.
    """
    checkout = source_checkout()
    data = _pipx_metadata()
    venvs = data.get("venvs") if isinstance(data, dict) else {}
    if not isinstance(venvs, dict):
        venvs = {}
    for venv in venvs.values():
        metadata = venv.get("metadata") if isinstance(venv, dict) else {}
        if not isinstance(metadata, dict):
            continue
        main = metadata.get("main_package") or {}
        if not isinstance(main, dict):
            continue
        if main.get("package") != package:
            continue
        spec = main.get("package_or_url")
        if not isinstance(spec, str):
            continue
        if spec.startswith(("git+", "http://", "https://")):
            if not checkout:
                return Install("git", None)
            continue
        path = Path(spec).expanduser()
        if not path.is_absolute():
            continue
        if checkout and path.resolve() != checkout.resolve():
            continue
        args = main.get("pip_args")
        editable = isinstance(args, list) and any(
            "-e" == arg or "--editable" == arg for arg in args)
        return Install("editable" if editable else "local", path)

    if checkout:
        # No pipx record, but the code is in a clone: the clone is the
        # install, and pulling it is the update.
        return Install("checkout", checkout)
    return Install("unknown", None)


def is_dirty(path):
    """True when the checkout has uncommitted changes (or cannot be read)."""
    try:
        out = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True
    return out.returncode != 0 or bool(out.stdout.strip())


def pull(path):
    """Fast-forward the checkout. Returns the command's exit code.

    --ff-only on purpose: a diverged branch or local commits should stop
    the update with git's own message, not be merged behind your back.
    """
    try:
        return subprocess.run(["git", "-C", str(path), "pull", "--ff-only"]).returncode
    except OSError as exc:
        raise UpdateError(f"could not run git: {exc}") from exc


def reinstall():
    """Reinstall through pipx. Returns the command's exit code."""
    try:
        return subprocess.run(["pipx", "reinstall", PACKAGE]).returncode
    except OSError as exc:
        raise UpdateError(
            f"could not run pipx: {exc}\n"
            f"  install the new version with: pipx install --force {SOURCE}"
        ) from exc
