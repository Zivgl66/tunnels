# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Every `up` now starts a detached watchdog (shared by every tunnel, like
  keepalive) that clears a tunnel automatically once its local port stops
  accepting connections. No flag needed; it never touches a healthy
  tunnel. Breaking: no.
- `--ttl [MINUTES]`: opt-in, on top of the above. Also auto-closes tunnels
  after this long, healthy or not. Breaking: no.

## [0.5.1] - 2026-09-02

## [0.5.0] - 2026-09-02

### Added
- `tunnels --version` prints the installed version, matching the GitHub
  release tag. Breaking: no.

## [0.4.0] - 2026-09-01

## [0.3.0] - 2026-08-25

## [0.2.0] - 2026-08-25

### Added
- Interactive picker: running `tunnels` with no subcommand now opens an
  arrow-key menu to pick an account and a target (or "all"), then starts
  that tunnel. `tunnels status` still shows live tunnels as before.
  Breaking: no.
- PR title gate: PR titles must follow conventional-commit format
  (`type(scope)?: description`), enforced in CI. Breaking: no.
- Automated releases: every merge to `main` now bumps the version, updates
  this changelog, tags, and publishes a GitHub Release with no extra PR.
  Breaking: no.

### Fixed
- Interactive picker: the menu no longer drifts/jumps when it's drawn near
  the bottom of the terminal (redraw now uses save/restore-cursor instead
  of counting lines). `q` at the target step now goes back to the account
  list instead of exiting, and both menu steps show a hint for the keys.
  Breaking: no.
