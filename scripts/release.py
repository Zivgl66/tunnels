#!/usr/bin/env python3
"""Cut a release from the commits since the last tag.

Run on every push to main by .github/workflows/release.yml. Bumps
pyproject.toml's version, turns the CHANGELOG's [Unreleased] section into a
dated release section, commits, tags, and prints the release notes on
stdout so the workflow can hand them to `gh release create`.
"""

import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"


def run(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def last_tag():
    try:
        return run("git", "describe", "--tags", "--abbrev=0")
    except subprocess.CalledProcessError:
        return None


def commit_subjects(since):
    range_spec = f"{since}..HEAD" if since else "HEAD"
    out = run("git", "log", range_spec, "--pretty=%s")
    return [line for line in out.splitlines() if line]


def bump_kind(subjects):
    breaking = re.compile(r"^\w+(\([^)]*\))?!:")
    if any(breaking.match(s) for s in subjects):
        return "major"
    if any(s.startswith("feat") for s in subjects):
        return "minor"
    return "patch"


def bump_version(current, kind):
    major, minor, patch = (int(p) for p in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def current_version():
    text = PYPROJECT.read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    return match.group(1)


def write_version(new_version):
    text = PYPROJECT.read_text()
    text = re.sub(r'^version = "[^"]+"', f'version = "{new_version}"', text, count=1, flags=re.MULTILINE)
    PYPROJECT.write_text(text)


def release_changelog(new_version, subjects):
    """Rename [Unreleased] to a dated version heading, insert a fresh empty
    one above it. Returns the release-notes text for the new section."""
    text = CHANGELOG.read_text()
    today = datetime.date.today().isoformat()
    heading = "## [Unreleased]"
    idx = text.index(heading)
    insert_at = idx + len(heading)
    new_heading = f"\n\n## [{new_version}] - {today}"
    text = text[:insert_at] + new_heading + text[insert_at:]
    CHANGELOG.write_text(text)

    start = text.index(new_heading) + len(new_heading)
    rest = text[start:]
    next_heading = re.search(r"\n## \[", rest)
    body = rest[: next_heading.start()] if next_heading else rest
    body = body.strip()
    if not body:
        body = "\n".join(f"- {s}" for s in subjects) or "No changes recorded."
    return body


def main():
    if "--dry-run" in sys.argv:
        subjects = commit_subjects(last_tag())
        print(bump_kind(subjects))
        return

    subjects = commit_subjects(last_tag())
    kind = bump_kind(subjects)
    new_version = bump_version(current_version(), kind)
    write_version(new_version)
    notes = release_changelog(new_version, subjects)
    breaking = "yes" if kind == "major" else "no"

    print(f"v{new_version}", file=sys.stderr)
    notes_file = ROOT / ".release-notes.md"
    notes_file.write_text(f"Breaking: {breaking}\n\n{notes}\n")
    print(new_version)


if __name__ == "__main__":
    main()
