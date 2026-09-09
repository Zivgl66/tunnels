"""How each AWS profile authenticates, and how to stop it asking so often.

`tunnels` shells out to `aws sso login` whenever a profile's cached token is
gone. That opens a browser and waits for a human to approve. The approval
itself cannot be automated away -- it is an OAuth 2.0 device authorization
grant, and the whole point of that grant is that a machine holding the device
code cannot also grant consent. Anything that "auto approves" is replaying the
user's IdP password and MFA, which is phishing by another name.

What *can* go away is how often the browser opens at all:

  * A profile written in the legacy format -- `sso_start_url` and `sso_region`
    inline on the profile -- gets an access token with **no refresh token**.
    When it expires (8h by default) the browser opens again.
  * A profile pointing at an `[sso-session ...]` block gets a refresh token.
    The CLI renews silently in the background, so the browser opens once per
    SSO session lifetime, which Identity Center can set as high as 90 days.

Same identity, same permissions, same start URL. Only the config shape differs.
`migration_plan` rewrites the first form into the second.

The other half is profiles that are not SSO at all (`credential_process`,
`role_arn` + `source_profile`, static keys). Running `aws sso login` against
one of those is nonsense, and `auth_kind` is here so the caller can tell.

Nothing in this module reads a token value. It reads `expiresAt` and asks
whether the key `refreshToken` is present, and that is all it wants to know.
"""

import configparser
import hashlib
import json
import os
import time
from pathlib import Path

AWS_CONFIG = Path(os.environ.get("AWS_CONFIG_FILE") or Path.home() / ".aws" / "config")
SSO_CACHE = Path.home() / ".aws" / "sso" / "cache"

# Auth kinds, ordered roughly by how much the browser bothers you.
SSO_SESSION = "sso-session"   # refresh token, silent renewal
SSO_LEGACY = "sso-legacy"     # no refresh token, browser every expiry
PROCESS = "credential-process"
ASSUME_ROLE = "assume-role"
STATIC = "static-keys"
UNKNOWN = "unknown"

#: Kinds `aws sso login` can actually do something about.
SSO_KINDS = (SSO_SESSION, SSO_LEGACY)


def read_config(path=None):
    """Parse ~/.aws/config into {section name: {key: value}}.

    Section names are kept raw ("profile dev", "sso-session corp") because
    that distinction is the whole point -- see `profiles` and `sso_sessions`.
    """
    path = Path(path) if path else AWS_CONFIG
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(path.read_text())
    except (OSError, configparser.Error):
        return {}
    return {name: dict(parser[name]) for name in parser.sections()}


def profiles(sections):
    """{profile name: settings}. The 'default' section counts as a profile."""
    found = {}
    for name, body in sections.items():
        if name == "default":
            found["default"] = body
        elif name.startswith("profile "):
            found[name[len("profile "):].strip()] = body
    return found


def sso_sessions(sections):
    """{session name: settings} for the [sso-session x] blocks."""
    return {
        name[len("sso-session "):].strip(): body
        for name, body in sections.items()
        if name.startswith("sso-session ")
    }


def auth_kind(body):
    """Which credential mechanism one profile's settings describe.

    Checked most-specific first: a profile can carry leftovers from a previous
    setup, and what the CLI actually uses is the more specific key.
    """
    if body.get("sso_session"):
        return SSO_SESSION
    if body.get("sso_start_url"):
        return SSO_LEGACY
    if body.get("credential_process"):
        return PROCESS
    if body.get("role_arn"):
        return ASSUME_ROLE
    if body.get("aws_access_key_id"):
        return STATIC
    return UNKNOWN


def can_sso_login(body):
    """True when `aws sso login` is a meaningful thing to run for this profile."""
    return auth_kind(body) in SSO_KINDS


def can_refresh_silently(body):
    """True when the CLI can renew this profile's token with no browser.

    Only the `sso_session` shape gets a refresh token. This is a property of
    the config format, not of the account or the permission set.
    """
    return auth_kind(body) == SSO_SESSION


def renews_without_a_human(body):
    """True when this profile can get fresh credentials with nobody watching.

    Static keys do not expire. `credential_process` and an assumed role both
    renew from something already on the machine. Only the SSO kinds need a
    browser, and only when their cached token has run out.
    """
    return auth_kind(body) in (PROCESS, ASSUME_ROLE, STATIC)


def _cache_path(key):
    """AWS names the token cache file sha1(key).json -- session name, or start URL."""
    return SSO_CACHE / f"{hashlib.sha1(key.encode()).hexdigest()}.json"


def token_status(body, sessions=None, cache_dir=None, now=None):
    """When this profile's cached SSO token expires, without reading the token.

    Returns {'found', 'expires_at', 'seconds_left', 'refreshable'}. `found` is
    False for a profile that has never logged in, or is not SSO at all.
    """
    sessions = sessions or {}
    now = now if now is not None else time.time()
    kind = auth_kind(body)
    blank = {"found": False, "expires_at": None, "seconds_left": None,
             "refreshable": False}
    if kind == SSO_SESSION:
        session = sessions.get(body["sso_session"], {})
        key = body["sso_session"]
        if not session.get("sso_start_url"):
            return blank
    elif kind == SSO_LEGACY:
        key = body["sso_start_url"]
    else:
        return blank

    path = (Path(cache_dir) / _cache_path(key).name) if cache_dir else _cache_path(key)
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        return blank

    expires = cached.get("expiresAt")
    left = None
    if isinstance(expires, str):
        # AWS writes ISO 8601 with a trailing Z that fromisoformat rejects
        # before 3.11. Normalising is cheaper than a version check.
        from datetime import datetime
        try:
            stamp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            left = stamp.timestamp() - now
        except ValueError:
            left = None
    return {
        "found": True,
        "expires_at": expires,
        "seconds_left": left,
        # Presence of the key, never its value.
        "refreshable": bool(cached.get("refreshToken")),
    }


def session_name_for(start_url, existing):
    """A stable, readable [sso-session] name derived from the start URL.

    https://acme.awsapps.com/start -> "acme". Falls back to the host, then to
    a numbered suffix, so two different portals never collide.
    """
    host = start_url.split("://")[-1].split("/")[0]
    base = host.split(".")[0] or "sso"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def migration_plan(sections):
    """Group legacy SSO profiles by portal, and name a session for each group.

    Returns [{'session', 'start_url', 'sso_region', 'scopes', 'profiles'}].
    Profiles already on an `sso_session` are left alone, and so is everything
    that is not SSO. An empty list means there is nothing to migrate.
    """
    existing = set(sso_sessions(sections))
    groups = {}
    for name, body in profiles(sections).items():
        if auth_kind(body) != SSO_LEGACY:
            continue
        key = (body["sso_start_url"], body.get("sso_region", ""))
        groups.setdefault(key, []).append(name)

    plan = []
    # Sorted so the same config always produces the same session names.
    for (start_url, region), members in sorted(groups.items()):
        # Reuse a session block that already points at this portal rather than
        # adding a second one beside it.
        reuse = next(
            (s for s, b in sso_sessions(sections).items()
             if b.get("sso_start_url") == start_url), None)
        name = reuse or session_name_for(start_url, existing)
        existing.add(name)
        plan.append({
            "session": name,
            "start_url": start_url,
            "sso_region": region,
            "scopes": "sso:account:access",
            "profiles": sorted(members),
            "new_block": reuse is None,
        })
    return plan


def apply_migration(text, plan):
    """Rewrite ~/.aws/config text so the planned profiles use sso_session.

    Line-based on purpose: configparser would round-trip the file into
    canonical form and throw away the user's comments and spacing. Only the
    lines that have to change are touched.
    """
    moved = {p: g for g in plan for p in g["profiles"]}
    # Keys that live on the session block now, so they must go from the profile.
    drop = ("sso_start_url", "sso_region", "sso_registration_scopes")

    out = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped[1:-1].strip()
            if header == "default":
                current = "default"
            elif header.startswith("profile "):
                current = header[len("profile "):].strip()
            else:
                current = None
            out.append(line)
            if current in moved:
                out.append(f"sso_session = {moved[current]['session']}")
            continue
        if current in moved:
            key = stripped.split("=", 1)[0].strip().lower() if "=" in stripped else ""
            if key in drop:
                continue
        out.append(line)

    blocks = []
    for group in plan:
        if not group["new_block"]:
            continue
        blocks.append(
            f"\n[sso-session {group['session']}]\n"
            f"sso_start_url = {group['start_url']}\n"
            f"sso_region = {group['sso_region']}\n"
            f"sso_registration_scopes = {group['scopes']}\n"
        )
    body = "\n".join(out).rstrip("\n")
    return body + "\n" + "".join(blocks)


def headless(env=None):
    """True when opening a browser on this machine would not reach a human.

    An SSH session or a Linux box with no display cannot show the approval
    page, so `aws sso login --no-browser` -- which prints the URL to paste
    somewhere that can -- is the only form that works there.
    """
    env = os.environ if env is None else env
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return True
    if env.get("TUNNELS_NO_BROWSER"):
        return True
    # macOS always has a window server; Linux needs X or Wayland.
    import sys
    if sys.platform.startswith("linux"):
        return not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return False
