---
name: tunnels
description: Open an AWS SSM tunnel to a private EKS cluster or database, then run kubectl or a database client through it. Use when a cluster or host is unreachable, kubectl times out or returns a connection error, or the user asks to connect to an environment by name.
license: MIT
metadata:
  argument-hint: <environment name, e.g. dev>
---

# tunnels

`tunnels` opens AWS SSM port forwards to private EKS clusters and databases
through a jump host, and rewrites the kubeconfig so `kubectl` works over the
tunnel. macOS only.

## Request

$ARGUMENTS

If that is non-empty, treat it as the environment to bring up. If it is
empty, work out which environment is needed from the conversation, and read
the config before guessing.

## Rules that prevent the common failures

1. **Never invent an environment or target name.** They come from the user's
   config file. Read it first with `cat "$(tunnels config --path)"`. Top level
   keys are environments; keys under `targets` are targets.
2. **Never hardcode a local port.** Ports are chosen at startup and change.
   Read the live port from `tunnels status` every time you need one.
3. **Never edit the kubeconfig yourself.** `tunnels up` does it, including the
   `tls-server-name` that keeps TLS valid over localhost. Editing it by hand
   breaks the certificate check.
4. **Always pass `--context`** to kubectl. The context is
   `tunnels-<env>-<target>`. Without it you hit whatever context is current,
   which is usually the wrong cluster.
5. **Do not run `tunnels down` unless the user asks.** Other work may be using
   the tunnel. It is safe to leave tunnels up.
6. **A tunnel is not a login.** `tunnels up` triggers an SSO browser login only
   when the cached token has expired. If it opens a browser, wait for it.

## Workflow

```bash
tunnels config --path        # where the config lives
cat "$(tunnels config --path)"   # environments and targets
tunnels status               # what is already up, and on which ports
tunnels up <env>             # start every target in that environment
tunnels up <env> <target>    # start one target only
```

`up` skips targets that are already running, so re-running it is safe and is
the correct way to repair a dropped tunnel. It starts an environment's targets
in parallel.

**`up` can partly succeed.** One target failing no longer stops the others, so
an exit code of 1 can still mean "3 of 4 came up". Read the summary line - it
names which failed - and carry on with the ones that are up rather than
treating the whole command as a failure.

**A dead tunnel closes itself.** A watchdog started by `up` clears any tunnel
whose port stops answering, so one can disappear between commands without
anyone running `down`. If a context that worked stops working, run `up` again.

### Reaching an EKS cluster

```bash
tunnels up dev
kubectl --context tunnels-dev-platform get nodes
```

`tunnels status` prints a row per tunnel, with the context for EKS targets:

```
── 2 tunnel(s) up ──────────────────────────────────────────
     TUNNEL        PORT    ACCOUNT       TARGET            CONTEXT               AGE
  ●  dev/platform  :52344  111122223333  platform-cluster  tunnels-dev-platform  18s
  ●  dev/orders    :15432  111122223333  db.internal:5432  -                     2m04s
```

The dot is health: green when the port still answers, red when the session
has died under it. Prefer `~/.tunnels/state.json` over parsing this.

### Reaching a database or plain host

There is no context for these. Read the local port from `tunnels status` and
connect to `127.0.0.1` on that port:

```bash
tunnels up dev orders-db
tunnels status          # -> dev/orders-db  :15432 ...
psql -h 127.0.0.1 -p 15432 -U app orders
```

### Long-running work

An idle SSM session is closed by AWS after the account's
`idleSessionTimeout`, 20 minutes by default. If the tunnel must survive a
long gap between commands:

```bash
tunnels up dev --keepalive        # poke every 300s
tunnels up dev --keepalive 120
```

## Reading state without running commands

`~/.tunnels/state.json` is a JSON list of the live tunnels, with `config`,
`target`, `local_port`, `context`, `account` and `cluster` on each entry.
Parse it when you need a port programmatically rather than scraping
`tunnels status`.

## When something fails

| What you see | What it means | What to do |
| --- | --- | --- |
| `no config file found` | No config yet | Tell the user to run `tunnels init`. Do not write the config for them |
| `unknown config '<name>'` | Wrong environment name | Re-read the config; the error lists the known names |
| `port <n> never opened` | The jump host cannot reach the target | Report the log tail printed with the error, or read more with `tunnels logs <env> <target>`. Do not retry blindly |
| `certificate is valid for ...` | The kubeconfig patch did not land | `tunnels down <env> && tunnels up <env>` |
| `the config changed since it started` | The running tunnel points at the old cluster | `tunnels down <env> <target>`, then `up` again to apply it |
| kubectl times out on a context that used to work | The session dropped | `tunnels up <env>` again |
| Leftover sessions after a crash or sleep | Stale processes | `tunnels doctor`, then `tunnels doctor --fix` with the user's agreement |

Errors from this tool are written to be acted on. Read the whole message
before retrying, and never retry a failing `up` more than once without
reporting why it failed.

## Commands

| Command | What it does |
| --- | --- |
| `tunnels status` | Live tunnels: health, ports, contexts and age |
| `tunnels profiles` | The configured environments, without opening the file |
| `tunnels up <env> [target...]` | Start tunnels. `--keepalive [SECONDS]` stops idle timeouts; `--ttl [MINUTES]` auto-closes; `--terraform` also patches `/etc/hosts` (needs sudo) |
| `tunnels down <env> [target...]` | Stop them. `down all` stops everything |
| `tunnels logs <env> <target>` | Tail that tunnel's session log. `-f` follows |
| `tunnels config` | Open the config. `--path` prints its path |
| `tunnels discover <profile>` | Build a config block by asking the account |
| `tunnels doctor` | Find leftovers. `--fix` cleans them up |
| `tunnels hud` | Toggle the floating label |

**Never run bare `tunnels`.** With no subcommand it opens an interactive
arrow-key picker and waits for a keypress, which will hang you. Always name a
subcommand.
