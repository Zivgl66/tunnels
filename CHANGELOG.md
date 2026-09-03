# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Coloured output across every command, a banner on the interactive picker
  pairing an ASCII drawing of the logo's nested arches with the wordmark
  (letters only on a narrow or non-UTF8 terminal), spinners for the slow AWS calls, and aligned tables for `status`
  and `profiles`. Colour turns itself off when the output is not a terminal,
  and `--no-color` / `NO_COLOR=1` forces plain text. Breaking: no.
- `tunnels status` now shows a per-tunnel health dot (green while the local
  port answers, red when it has stopped), plus the kubectl context and a
  readable age. Breaking: no.
- `tunnels logs <env> <target> [-f]` tails a tunnel's session log.
  Breaking: no.
- The picker marks targets that are already up, and `g` / `G` jump to the
  first and last entry. Breaking: no.
- The target step of the picker can be left again: `←`, `b`, `h` or `q` step
  back to the account list, and the hint line says so. Ctrl-C still quits
  outright from any level. Breaking: no.

### Fixed
- `tunnels status` ran the interactive picker instead of printing the status
  table: the subcommand was never dispatched. Breaking: no.
- `up` no longer stops at the first target it cannot start. Every other target
  in the block still comes up, the failures are listed together at the end,
  and the exit code is 1. Breaking: no.
- A warning raised during a spinner (for example "15 instances match this
  tag") no longer prints on top of the spinner's own line.

## [0.6.0] - 2026-09-02

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
