# tunnels

Start AWS SSM port forward tunnels from a named config. One command per
environment. It logs in with SSO, finds the jump server, opens a tunnel per
target, fixes the kubeconfig, and shows a floating label with the account and
cluster you are pointed at.

## Requirements

- `aws` CLI v2 with SSO profiles in `~/.aws/config`
- `session-manager-plugin`
- `kubectl`
- Python 3 with `pyyaml`
- For the floating label: `pyobjc-framework-Cocoa` on `/usr/bin/python3`

```bash
/usr/bin/python3 -m pip install --user pyobjc-framework-Cocoa
```

## Install

```bash
./install.sh
```

It checks for `aws`, `session-manager-plugin`, `kubectl` and `pyyaml`, installs
pyobjc for the floating label if it is missing, symlinks `tunnels` into the
first writable directory on your PATH, and seeds `~/.config/tunnels/config.yaml`
if you do not have one.

It is a symlink, not a copy, so `git pull` updates the command.

Then edit `~/.config/tunnels/config.yaml` and run `tunnels status` from
anywhere.

To remove it: `rm ~/.local/bin/tunnels` (or wherever the script linked it).

## Use

```bash
tunnels up dev            # start every target in the 'dev' config
tunnels up dev eks-main   # start one target
tunnels status            # what is live now
tunnels down dev          # stop one config
tunnels down all          # stop everything
tunnels hud               # toggle the floating label
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
draws windows at all.

## Tests

```bash
/usr/bin/python3 -m pytest test_tunnels.py -q
```

`/usr/bin/python3` is used because it is the interpreter on this machine with
pytest available. The tool itself runs on any Python 3 with `pyyaml`.

## Known limits

- No auto-reconnect. If a session drops, `status` shows it grey. Run `up` again.
- `down` leaves the kubectl context in place. It fails loudly if used, and `up`
  repairs it.
- The label position and width are fixed: top right, sized to the text.

## Architecture

See `docs/architecture.excalidraw`, opened in the local Excalidraw app on port
3010.
