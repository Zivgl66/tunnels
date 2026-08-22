# tunnels

Reach private EKS clusters and databases through an AWS SSM tunnel, with one
command per environment.

```console
$ tunnels up dev
account 111122223333
  argo: up on 52341 via i-0a1b2c3d4e5f6a7b8 · context tunnels-dev-argo
  platform: up on 52344 via i-09f8e7d6c5b4a3210 · context tunnels-dev-platform

$ kubectl --context tunnels-dev-platform get nodes
NAME                                      STATUS   ROLES    AGE   VERSION
ip-10-0-1-42.eu-west-1.compute.internal   Ready    <none>   18m   v1.31.0-eks-1234567
```

It does the whole sequence for you:

1. Logs in with SSO, but only when the cached token has expired.
2. Finds the jump host by tag, so a replaced instance does not break anything.
3. Opens one SSM port forward per target, in the background.
4. Rewrites your kubeconfig so `kubectl` works over the tunnel.
5. Shows a small floating label with the account and cluster you are on.

## Requirements

macOS, plus:

| Tool | Install |
| --- | --- |
| `aws` CLI v2, with SSO profiles in `~/.aws/config` | `brew install awscli` |
| `session-manager-plugin` | `brew install --cask session-manager-plugin` |
| `kubectl` | `brew install kubectl` |
| `pipx` | `brew install pipx` |

Python 3.9 or newer. `pyyaml` and `pyobjc` come with the package.

The jump host must have the SSM agent running and network access to the
cluster or database you want to reach.

## Install

```bash
pipx install git+https://github.com/Zivgl66/tunnels.git && tunnels init
```

`tunnels init` writes `~/.config/tunnels/config.yaml` and opens it in your
editor.

Behind a TLS-inspecting proxy, put `UV_NATIVE_TLS=1` in front of the command so
the installer trusts your system certificates.

## Quick start

`tunnels init` gives you a template. You need four values per environment.

**1. The SSO profile.** Any profile name from `~/.aws/config`.

**2. The region.** The one the cluster lives in, not your default.

**3. The cluster name.** Exactly as EKS knows it, not the ARN:

```bash
aws sso login --profile my-profile
aws --profile my-profile --region eu-west-1 eks list-clusters
```

**4. The jump host.** An instance with a live SSM agent that can reach the
cluster:

```bash
# instances the SSM agent has registered
aws --profile my-profile --region eu-west-1 ssm describe-instance-information \
  --query "InstanceInformationList[].[InstanceId,ComputerName]" --output table

# their Name tags
aws --profile my-profile --region eu-west-1 ec2 describe-instances \
  --filters Name=instance-state-name,Values=running \
  --query "Reservations[].Instances[].[InstanceId,Tags[?Key=='Name']|[0].Value]" \
  --output table
```

Pick an instance that appears in both lists. Use its `Name` tag.

Then:

```bash
tunnels up my-env
kubectl --context tunnels-my-env-<target> get nodes
```

## Commands

| Command | What it does |
| --- | --- |
| `tunnels up <env>` | Start every target in that config block |
| `tunnels up <env> <target>...` | Start only the named targets |
| `tunnels status` | List live tunnels. Same as bare `tunnels` |
| `tunnels down <env>` | Stop that config's tunnels |
| `tunnels down all` | Stop everything |
| `tunnels hud` | Turn the floating label on or off |
| `tunnels init` | Create and open the config file |

`up` skips targets that are already running, so it is safe to repeat after you
add a new one.

## Config

The file lives at `~/.config/tunnels/config.yaml`. Each top level key is an
environment name you pass to `tunnels up`.

```yaml
dev:                                    # tunnels up dev
  profile: my-sso-profile
  region: eu-west-1
  jump: "tag:Name=shared-bastion"           # default jump for the targets below
  hud: true
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

### Keys

| Key | Where | Required | Meaning |
| --- | --- | --- | --- |
| `profile` | block | yes | SSO profile from `~/.aws/config` |
| `region` | block | yes | The account's region |
| `jump` | block or target | yes, in one of the two | Instance id (`i-0abc...`) or tag lookup (`tag:Key=Value`) |
| `hud` | block | no | `true` starts the floating label with the tunnels |
| `targets` | block | yes | One entry per cluster or host |
| `eks` | target | one of the two | EKS cluster name. The endpoint is looked up |
| `host` + `port` | target | one of the two | Any host the jump can reach |
| `local_port` | target | no | Fixed local port. Omit it and a free one is picked |

A target has either `eks`, or `host` and `port`. Never both, never neither.
A target with no jump host anywhere is rejected before any AWS call.

### Ports

Leave `local_port` out unless you need it. A free port is picked at startup and
the kubeconfig is rewritten to match, so `kubectl` always works.

Pin a port only for tools that do not read your kubeconfig: `psql`, a
`DATABASE_URL`, a saved Postman collection. If a pinned port is busy, the tool
takes a free one instead and tells you which process held it.

### Several clusters in one account

Add one target per cluster. Each gets its own SSM session, its own local port
and its own kubectl context, named `tunnels-<env>-<target>`. Nothing collides.

`tunnels up dev` starts all of them. `tunnels up dev platform` starts
one. Each distinct jump host is looked up once per run, however many targets
use it.

Clusters in different accounts need separate blocks, because `profile` is set
per block.

## The floating label

One short line per tunnel, in the top right corner:

```
● dev/platform :52344 ·3333
● dev/argo :52341 ·3333
○ tst/orders-db :15433 ·5555
```

The last four digits are the account id. Every tunnel gets its own colour, so
two clusters in the same account never look alike. A tunnel keeps its colour
across restarts. Grey with `○` means the session died.

The window stays visible while another app is fullscreen, and it ignores mouse
clicks, so it never gets in your way. Start or stop it with `tunnels hud`, or
set `hud: true` on a block to have it appear with the tunnels. It closes itself
when the last tunnel stops.

## How it works

Each target becomes its own `aws ssm start-session` process, forwarding a local
port to a host the jump server can reach. Many run at the same time.

For EKS targets the tool runs `aws eks update-kubeconfig`, then rewrites that
cluster entry:

```yaml
server: https://127.0.0.1:52344
tls-server-name: EXAMPLE1234ABCD.gr7.eu-west-1.eks.amazonaws.com
```

`tls-server-name` makes `kubectl` send the real cluster hostname during the TLS
handshake while it connects to localhost. The certificate check passes. This is
why no `/etc/hosts` entry and no `sudo` are needed.

Live tunnels are tracked in `~/.tunnels/state.json`. Every command drops dead
processes from that file first, so there is no daemon to babysit. Session
output goes to `~/.tunnels/logs/<env>-<target>.log`.

Stopping a tunnel kills the whole process group. `aws ssm start-session` starts
`session-manager-plugin` as a child, and the child is what holds the port, so
killing only the parent would leave the port taken.

## Troubleshooting

| What you see | What it means | What to do |
| --- | --- | --- |
| `no config file found` | No config yet | `tunnels init` |
| `no running instance with tag ...` | The tag matches nothing running | Check the tag with the `describe-instances` command above |
| `warning: 9 instances match ...` | The tag matches a node group | Fine. It uses the first one. Use a narrower tag to be exact |
| `port <n> never opened` | The jump host cannot reach the target, or has no SSM agent | Read the log tail printed with the error, and `~/.tunnels/logs/` |
| `port 9443 is busy (com.docker.backend)` | Another program owns that port | Remove `local_port` and let the tool choose |
| `certificate is valid for ...` from kubectl | The kubeconfig patch did not land | `tunnels down <env> && tunnels up <env>` |
| A tunnel shows grey in the label | The session dropped | `tunnels up <env>` again |
| SSO opens a browser every time | The token is genuinely expiring | Normal. It only logs in when the cached token has gone |

## Known limits

- No auto-reconnect. A dropped session shows grey until you run `up` again.
- `down` leaves the kubectl context in place. It fails loudly if you use it,
  and `up` repairs it.
- The label sits in the top right corner and cannot be moved.
- macOS only. The label uses Cocoa, because the system Tk on macOS 26 no longer
  draws windows at all.

## Develop

```bash
git clone https://github.com/Zivgl66/tunnels.git && cd tunnels
pipx install --editable .     # your edits take effect with no reinstall
pip install -e ".[dev]" && pytest -q
```

The tests cover the pure parts: config validation, port choice, state pruning,
the kubeconfig patch, and process group cleanup. They make no AWS calls.

## Architecture

`docs/architecture.excalidraw`, viewable in any Excalidraw app.
