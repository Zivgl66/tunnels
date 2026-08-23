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
