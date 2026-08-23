# tunnels

<p align="center">
  <a href="#install">install</a> ·
  <a href="#quick-start">quick start</a> ·
  <a href="#commands">commands</a> ·
  <a href="docs/configuration.md">configuration</a> ·
  <a href="docs/how-it-works.md">how it works</a> ·
  <a href="docs/troubleshooting.md">troubleshooting</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-666666?labelColor=333333" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-666666?labelColor=333333" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/platform-macOS-666666?labelColor=333333" alt="macOS" />
  <a href="https://github.com/Zivgl66/tunnels/stargazers"><img src="https://img.shields.io/github/stars/Zivgl66/tunnels?labelColor=333333&color=666666&logo=github" alt="GitHub stars" /></a>
</p>

---

**Reach private EKS clusters and databases through an AWS SSM tunnel, one
command per environment.**

- **one command per environment** — `tunnels up dev` opens every cluster and
  database in that block at once, and skips the ones already running.
- **kubectl just works** — the kubeconfig is rewritten to point at the local
  port, with `tls-server-name` so the certificate still checks out. No
  `/etc/hosts`, no `sudo`.
- **no daemon** — each tunnel is a plain `aws ssm start-session` process,
  tracked in a state file that every command prunes. Nothing to babysit.
- **the jump host can be replaced** — look it up by tag, not by instance id, so
  a recycled node does not break your config.
- **it cleans up after itself** — `down` frees the port *and* terminates the
  AWS session; `doctor --fix` sweeps up what a sleeping laptop left behind.
- **you always know where you are** — a floating label shows account, cluster
  and port, one colour per tunnel, on top of fullscreen apps.
- **build the config by asking AWS** — `tunnels discover <profile>` lists the
  clusters in an account and writes the block for you.

```console
$ tunnels up dev
account 111122223333
  argo: up on 52341 via i-0a1b2c3d4e5f6a7b8 · context tunnels-dev-argo
  platform: up on 52344 via i-09f8e7d6c5b4a3210 · context tunnels-dev-platform

$ kubectl --context tunnels-dev-platform get nodes
NAME                                      STATUS   ROLES    AGE   VERSION
ip-10-0-1-42.eu-west-1.compute.internal   Ready    <none>   18m   v1.31.0-eks-1234567
```

Behind that one command: SSO login only when the cached token has expired, a
jump host found by tag, one SSM port forward per target in the background, a
patched kubeconfig, and the floating label.

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
editor. To edit it again later, run `tunnels config`. `tunnels config --path`
prints the path on its own, so you can pipe it somewhere.

Behind a TLS-inspecting proxy, put `UV_NATIVE_TLS=1` in front of the command so
the installer trusts your system certificates.

## Quick start

Let `tunnels discover my-profile --region eu-west-1` write the block for you,
or fill in the template `tunnels init` left behind. Four values per environment:

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

Full config reference: [docs/configuration.md](docs/configuration.md).

## Commands

| Command | What it does |
| --- | --- |
| `tunnels up <env>` | Start every target in that config block |
| `tunnels up <env> <target>...` | Start only the named targets |
| `tunnels status` | List live tunnels. Same as bare `tunnels` |
| `tunnels down <env>` | Stop that config's tunnels, free the ports, close the AWS sessions |
| `tunnels down all` | Stop everything |
| `tunnels hud` | Turn the floating label on or off |
| `tunnels init` | Create and open the config file |
| `tunnels config` | Open the existing config file. `--path` prints the path only |
| `tunnels discover <profile>` | Build a config block by asking the account what it has |
| `tunnels doctor` | Find leftover tunnels and AWS sessions. `--fix` cleans them up |

`up` skips targets that are already running, so it is safe to repeat after you
add a new one.

## Docs

- [configuration](docs/configuration.md) — the config file, every key, ports,
  several clusters in one account, and `tunnels discover`
- [how it works](docs/how-it-works.md) — the kubeconfig patch, process and
  session lifecycle, the floating label, `tunnels doctor`
- [troubleshooting](docs/troubleshooting.md) — every error message, and the
  known limits
- [backlog](docs/BACKLOG.md) — what is not built yet

## Develop

```bash
git clone https://github.com/Zivgl66/tunnels.git && cd tunnels
pipx install --editable .     # your edits take effect with no reinstall
pip install -e ".[dev]" && pytest -q
```

The tests cover the pure parts: config validation, port choice, state pruning,
the kubeconfig patch, and process group cleanup. They make no AWS calls.

## License

MIT. See [LICENSE](LICENSE).
