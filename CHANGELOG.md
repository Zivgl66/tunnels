# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
