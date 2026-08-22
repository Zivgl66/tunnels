# tunnels

Start AWS SSM port forward tunnels from a named config. One command per
environment. It logs in with SSO, finds the jump server, opens a tunnel per
target, fixes the kubeconfig, and shows a floating label with the account and
cluster you are pointed at.

## Requirements

macOS, plus:

- `aws` CLI v2 with SSO profiles in `~/.aws/config`
- `session-manager-plugin` (`brew install --cask session-manager-plugin`)
- `kubectl`
- Python 3.9 or newer

`pyyaml` and `pyobjc` are installed for you as package dependencies.

## Install

One line:

```bash
pipx install git+https://github.com/Zivgl66/tunnels.git && tunnels init
```

`tunnels init` writes `~/.config/tunnels/config.yaml` and opens it in your
editor. Fill in the profile, region, jump host tag and cluster name, save, and
you are done.

No `pipx`? `brew install pipx`. Plain `pip install` works too, but pipx keeps
the tool in its own environment.

Behind a TLS-inspecting proxy, prefix the command with `UV_NATIVE_TLS=1` so the
installer trusts your system certificates.

### From a checkout

```bash
git clone https://github.com/Zivgl66/tunnels.git && cd tunnels
pipx install --editable .    # edits take effect with no reinstall
```

## Use

```bash
tunnels up dev            # start every target in the 'dev' config
tunnels up dev eks-main   # start one target
tunnels status            # what is live now
tunnels down dev          # stop one config
tunnels down all          # stop everything
tunnels hud               # toggle the floating label
tunnels init              # write a starter config
```

## Develop

```bash
git clone <repo> && cd tunnels
pip install -e ".[dev]"
pytest -q
```

## How it works

Each target becomes its own `aws ssm start-session` process, forwarding a local
port to a host the jump server can reach. Many run at the same time.

For EKS targets the tool runs `aws eks update-kubeconfig`, then rewrites that
cluster entry to `server: https://127.0.0.1:<port>` and adds
`tls-server-name: <real eks hostname>`. The TLS handshake stays valid over
localhost, so no `/etc/hosts` edit and no sudo are needed.

Live tunnels are tracked in `~/.tunnels/state.json`. Every command prunes dead
processes from it first, so there is no daemon to babysit. Session output goes
to `~/.tunnels/logs/`.

## The floating label

`hud.py` draws a small borderless window in the top right corner, one short
line per tunnel:

```
● dev/eks-main :9443 ·3333
● dev/db :15432 ·3333
● prd/eks-main :9444 ·4444
○ tst/db :15433 ·5555
```

The colour is picked from the config name, so `dev` and `prd` never look alike,
and the same config keeps its colour across restarts. A grey line with `○` is a
session whose process has died. The last four digits are the account id.

The window joins every Space and is marked `fullScreenAuxiliary`, so it stays
visible while another app is fullscreen. It ignores mouse events, so clicks
pass through to whatever is underneath.

It uses Cocoa through pyobjc, not tkinter: the system Tk on macOS 26 no longer
draws windows at all. The label runs with the same interpreter the package is
installed under, so pyobjc always comes from the right environment.

## Known limits

- No auto-reconnect. If a session drops, `status` shows it grey. Run `up` again.
- `down` leaves the kubectl context in place. It fails loudly if used, and `up`
  repairs it.
- The label position and width are fixed: top right, sized to the text.

## Architecture

See `docs/architecture.excalidraw`, opened in the local Excalidraw app on port
3010.
