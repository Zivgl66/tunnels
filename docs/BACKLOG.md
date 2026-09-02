# tunnels backlog

Ideas that were considered and deliberately left out of the first version.
Each one names why it is not built yet, so a later decision has the reasoning.

Shipped so far: `up`, `down`, `status`, `init`, `discover`, `doctor`, `hud`,
sticky ports, per-target jump hosts, AWS-side session cleanup.

## Worth doing next

### 1. Reconnect dropped sessions
An SSM session dies when the laptop sleeps or the network changes. Today the
floating label turns grey, the local process can hang around holding a dead
port, and you run `tunnels up` again yourself.

The cleanup half of this is done: `up` now always starts a watchdog process
that clears a tunnel automatically once its port stops accepting
connections (`--ttl` adds an opt-in wall-clock close on top). What is still
missing is restarting it - the watchdog only stops a dead tunnel, it does
not bring it back.

Do the restart when a dropped session actually interrupts work often enough
to notice. Cheaper first step: have the label flash or notify on a drop, and
keep the restart manual.

### 2. Better region handling for `discover`
`--region` is easy to get wrong. A profile's configured region is often not
where its clusters live, and the wrong one fails with `AccessDenied` on
`eks:ListClusters`, which reads like a permissions problem rather than a typo.

Try the profile's region first, then scan a short list of likely regions, and
show what was found where.

### 3. `tunnels logs <env> <target>`
Tail `~/.tunnels/logs/<env>-<target>.log`. Two lines of code. Only useful while
debugging a jump host, so it has not earned its place yet.

### 4. Several environments in one command
`tunnels up dev prd`. Straightforward, just not needed yet.

### 5. Shell completion
Complete environment and target names from the config. Pleasant, not important.

## Considered and rejected

- **Menu bar indicator (SwiftBar).** The menu bar hides in fullscreen apps. The
  floating Cocoa label already draws over fullscreen, so this would be a second
  way to show the same thing.
- **Auto-down after an idle period.** No evidence anyone leaves tunnels open
  long enough for it to matter, and a wrong guess closes a tunnel mid-use.
- **Linux support.** The label is Cocoa. The rest would port, but nobody has
  asked.
- **Removing kubectl contexts on `down`.** A stale context fails loudly on a
  dead port, which is clearer than a context that silently disappears, and the
  next `up` repairs it.

## Known limits, by choice

- No daemon. State is a file that every command prunes.
- The label sits in the top right corner and cannot be moved. It ignores mouse
  clicks so it never gets in the way, which also means it cannot be dragged.
- macOS only, because of the label.
