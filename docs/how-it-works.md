# How it works

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

`down` also calls `ssm terminate-session` on the AWS side. Killing the local
process frees the port, but AWS keeps the session in `Connected` until it times
out, and those sessions count against your account's limits. The session id is
read from the plugin's own log output when the tunnel starts.

`down` does not touch two things. Your kubectl context is left in place: a
stale one fails loudly on a dead port, which is clearer than a context that
quietly disappears, and the next `up` repairs it. `/etc/hosts` is never
involved at all, because `tls-server-name` does that job.

## Keepalive

An SSM session with no traffic is closed by AWS after the account's
`idleSessionTimeout`. `tunnels up --keepalive [SECONDS]` starts one detached
process that opens and closes a connection to every live tunnel's local port
on that interval, which is enough for the session to count as active. It
reads `~/.tunnels/state.json` like every other command and exits once no
tunnels are left, so it adds no daemon to babysit. Off unless asked for.
See [configuration](configuration.md#idle-timeouts).

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

## Keeping things tidy

`tunnels doctor` looks for two kinds of leftovers: port forward processes with
no tunnel behind them, and AWS sessions still `Connected` after their tunnel
went away. Both happen when a laptop sleeps or a process is killed by hand.

```console
$ tunnels doctor
no stray port forward processes
my-profile: 2 aws session(s) still open with no tunnel:
    someone@example.com-abc123  target i-0a1b2c3d4e5f6a7b8
    someone@example.com-def456  target i-09f8e7d6c5b4a3210

Run 'tunnels doctor --fix' to clean these up.
```

It only ever closes sessions this tool started, matched against its own logs.
Your interactive `aws ssm start-session` shells are left alone. Accounts you
are not logged into, or that deny `ssm:DescribeSessions`, are skipped with a
note rather than failing the run.
