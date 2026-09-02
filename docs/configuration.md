# Configuration

The file lives at `~/.config/tunnels/config.yaml`. `tunnels config` opens it,
`tunnels config --path` prints the path. Each top level key is an environment
name you pass to `tunnels up`.

```yaml
dev:                                    # tunnels up dev
  profile: my-sso-profile
  region: eu-west-1
  jump: "tag:Name=shared-bastion"           # default jump for the targets below
  hud: true
  keepalive: 300                            # optional, see "Idle timeouts" below
  ttl: 480                                  # optional, minutes before auto-close
  targets:
    platform:
      eks: platform-cluster                 # an EKS target
    argo:
      eks: argocd-cluster
      jump: "tag:Name=argo-bastion"         # this one sits behind another host
    orders-db:
      host: orders.abc.eu-west-1.rds.amazonaws.com   # a plain host target
      port: 5432
      local_port: 15432                     # pinned, because psql needs it fixed
```

## Keys

| Key | Where | Required | Meaning |
| --- | --- | --- | --- |
| `profile` | block | yes | SSO profile from `~/.aws/config` |
| `region` | block | yes | The account's region |
| `jump` | block or target | yes, in one of the two | Instance id (`i-0abc...`) or tag lookup (`tag:Key=Value`) |
| `hud` | block | no | `true` starts the floating label with the tunnels |
| `keepalive` | block | no | Seconds between pokes, or `true` for 300. Omit it and keepalive stays off |
| `ttl` | block | no | Minutes before a tunnel auto-closes, or `true` for 480. Omit it and ttl stays off |
| `targets` | block | yes | One entry per cluster or host |
| `eks` | target | one of the two | EKS cluster name. The endpoint is looked up |
| `host` + `port` | target | one of the two | Any host the jump can reach |
| `local_port` | target | no | Fixed local port. Omit it and a free one is picked, reusing last time's |

A target has either `eks`, or `host` and `port`. Never both, never neither.
A target with no jump host anywhere is rejected before any AWS call.

## Ports

Leave `local_port` out unless you need it. A free port is picked at startup and
the kubeconfig is rewritten to match, so `kubectl` always works.

The port a tunnel used last time is remembered in `~/.tunnels/ports.json` and
reused whenever it is still free, so your kubeconfig entries stay stable across
restarts without pinning anything.

Pin a port only for tools that do not read your kubeconfig: `psql`, a
`DATABASE_URL`, a saved Postman collection. If a pinned port is busy, the tool
takes a free one instead and tells you which process held it.

## Several clusters in one account

Add one target per cluster. Each gets its own SSM session, its own local port
and its own kubectl context, named `tunnels-<env>-<target>`. Nothing collides.

`tunnels up dev` starts all of them. `tunnels up dev platform` starts
one. Each distinct jump host is looked up once per run, however many targets
use it.

Clusters in different accounts need separate blocks, because `profile` is set
per block.

## Filling the config automatically

`tunnels discover` reads an account and offers one cluster at a time:

```console
$ tunnels discover my-profile --region eu-west-1
account 111122223333 · profile my-profile · region eu-west-1

3 cluster(s), 45 SSM-registered instance(s)

cluster platform-cluster
    jump: tag:aws:eks:cluster-name=platform-cluster
    add it? [Y/n] y
    added as target 'platform'

cluster argocd-cluster
    jump: tag:aws:eks:cluster-name=argocd-cluster
    add it? [Y/n] n
    skipped

This block will be added:
    ...
Append it to /Users/you/.config/tunnels/config.yaml? [y/N] y
```

It picks the jump host for you: a node of that cluster if there is one,
otherwise any SSM-registered instance with a `Name` tag. Change it afterwards
if the guess is wrong.

`--region` matters. Without it the profile's own region is used, which is often
not where the clusters are. `--name` sets the block name, and defaults to the
profile name.

Nothing is written until the last question, and that one defaults to no.
An existing block with the same name is never overwritten.

## Idle timeouts

By default `tunnels` sets no wall-clock timeout of its own. A healthy tunnel
lives until AWS ends it or you run `tunnels down` — unless you opt into
`--ttl` (below), which closes it after a fixed time regardless. A tunnel
whose local port has already died is always cleared automatically; see
[auto-close](#auto-close-watchdog-and-ttl).

AWS ends it on two account-wide Session Manager settings, both in the
`SSM-SessionManagerRunShell` document:

| Setting | Default | What it does |
| --- | --- | --- |
| `idleSessionTimeout` | 20 minutes | Closes a session with no traffic. Settable 1-60 |
| `maxSessionDuration` | not set | Closes a session whatever it is doing. Settable 1-1440 |

Read the real numbers for an account:

```bash
aws --profile my-profile --region eu-west-1 ssm get-document \
  --name SSM-SessionManagerRunShell --query Content --output text
```

Neither can be set per session, so `tunnels` cannot change them for one
environment. What it can do is keep the session busy:

```bash
tunnels up dev --keepalive        # every 300 seconds
tunnels up dev --keepalive 120    # every 120 seconds
```

Or set `keepalive` on the block to have it start with every `up`. The flag
wins over the config, so `--keepalive 60` overrides `keepalive: 300`.

Keepalive is **off unless you ask for it**. When on, one detached process
opens and closes a connection to each live tunnel's local port on the
interval. That single stream is enough traffic for the session to count as
active. The process reads the same `~/.tunnels/state.json` as every command,
and exits by itself once the last tunnel is gone.

Pick an interval below `idleSessionTimeout`, with room to spare: 300 seconds
against the 20 minute default. It does not defeat `maxSessionDuration`, which
ignores activity.

## Auto-close (watchdog and ttl)

A tunnel's local process does not always exit when the AWS side of the
session ends — it can hang around holding the port with nothing behind it.
Every `up` starts one detached watchdog process (like keepalive's) that
checks every live tunnel once a minute and stops it the moment its local
port stops accepting connections. It stops a tunnel the same way
`tunnels down` does: kills the process group, closes the AWS session,
undoes any `--terraform` `/etc/hosts` patch. **This always runs, no flag
needed** — it never touches a healthy tunnel.

`--ttl` adds a second, opt-in check on top: the same watchdog also stops a
tunnel once it is older than the ttl, healthy or not.

```bash
tunnels up dev --ttl              # also auto-close after 480 minutes
tunnels up dev --ttl 60           # also auto-close after 60 minutes
```

Or set `ttl` on the block. The flag wins over the config, same as keepalive.
Omit both and only the always-on dead-port check applies.
