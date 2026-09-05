# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `tunnels update` checks GitHub for a newer release and installs it. What
  "install it" means is read from pipx's own record rather than assumed:
  remote pipx installs are reinstalled, local pipx installs are pulled and
  reinstalled, and a checkout without pipx is only pulled. An unrecognised
  install is told what to run instead of being guessed at. `--yes` skips the
  prompt. Breaking: no.

### Fixed
- The README undercounted the project (it claimed 1,300 lines over three
  files; it is about 2,800) and never documented `tunnels profiles`.

## [0.10.0] - 2026-09-05

### Added
- Picking a target that is already up asks what to do with it - `restart`
  or `stop` - instead of running `up` on a tunnel that is already running.
  A target that is not up starts as before, with no extra prompt. Closes
  #12. Breaking: no.
- Accounts with a live tunnel are marked in the picker's account list, the
  way targets already were. Breaking: no.

## [0.9.0] - 2026-09-05

### Added
- The interactive picker filters. Press `/` and type to narrow the list;
  backspace edits the query and backspacing past the start returns to the
  full list. It is behind `/` because the letters are already bound —
  `j`/`k` move, `g`/`G` jump, `q` cancels — so typing a name into an
  unfiltered list would move, jump and quit. Both the account list and the
  target list get it. Breaking: no.

### Fixed
- The picker left rows on screen when a list got shorter between frames.
  It backed up over the old block and drew a shorter one without erasing
  the tail, so the dropped rows stayed visible. Only reachable through the
  new filter, but the redraw was wrong on its own.

## [0.8.2] - 2026-09-05

## [0.8.1] - 2026-09-05

### Added
- `tunnels up` starts a config block's targets in parallel instead of one
  after another, so an environment with several clusters comes up in about
  the time one used to take. Output is still printed in config order.
  Breaking: no.
- `up` warns when a target is already running but the config has changed
  underneath it, instead of silently skipping and leaving you pointed at
  the old cluster. Breaking: no.

### Fixed
- A failure that was not a `TunnelError` - a kubeconfig the aws CLI could
  not write, a permission error - ended the whole `up` in a traceback and
  skipped every remaining target. Every failure is now caught per target
  and reported in the summary.
- A tunnel whose kubeconfig patch failed was left running with nothing
  tracking it. The state entry is now recorded as soon as the port opens.
- `status`, the floating label and the watchdog each decided "is this
  tunnel alive?" differently, so a session that died AWS-side showed green
  in the label and red in `status`. All three now use one connect-based
  check.
- The watchdog closed a tunnel on a single missed check, so a wifi switch
  or a laptop waking up could take down healthy tunnels. It now needs three
  consecutive misses.
- `aws sso login` printed its browser prompt underneath a running spinner.
  The spinner now covers only the cached-token probe.
- A target whose port never opened left its AWS session open for `doctor`
  to find later; it is closed straight away.

## [0.8.0] - 2026-09-03

### Added
- The floating HUD label fades to low opacity while the cursor hovers it,
  so it no longer blocks the view of what is underneath. Breaking: no.

## [0.7.0] - 2026-09-03

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
