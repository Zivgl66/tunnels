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
why no `/etc/hosts` entry and no `sudo` are needed by default. Tools that
dial the real hostname directly instead of reading the kubeconfig (e.g.
terraform's kubernetes/helm providers) can opt into an `/etc/hosts` patch
with `--terraform` — see below.

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
quietly disappears, and the next `up` repairs it. `/etc/hosts` is not
involved by default, because `tls-server-name` does that job.

## Terraform / hostname-dialing tools

Some tools hardcode the cluster's real DNS name in their host config instead
of reading the kubeconfig this tool patches — terraform's kubernetes/helm
providers, among others. `tunnels up <env> [target] --terraform` adds a
tagged `/etc/hosts` line (`127.0.0.1 <hostname> # tunnels:<key>`) pointing
that hostname at `127.0.0.1`; `tunnels down` removes it again. This needs
`sudo`, so expect a password prompt.

`/etc/hosts` has no notion of port, so this only gets you halfway there: the
caller still has to point its own port config (e.g. terraform's
`kubeapi_port` variable) at the tunnel's `local_port` for the connection to
land anywhere.

## Keepalive

An SSM session with no traffic is closed by AWS after the account's
`idleSessionTimeout`. `tunnels up --keepalive [SECONDS]` starts one detached
process that opens and closes a connection to every live tunnel's local port
on that interval, which is enough for the session to count as active. It
reads `~/.tunnels/state.json` like every other command and exits once no
tunnels are left, so it adds no daemon to babysit. Off unless asked for.
See [configuration](configuration.md#idle-timeouts).

## Auto-close (watchdog and ttl)

A tunnel's local process (`aws ssm start-session` plus the
`session-manager-plugin` child it spawns) does not always exit when the AWS
side of the session ends, so it can hang around holding the port with
nothing behind it — the case `tunnels doctor` calls a stray port forward
process. Every `up` starts one detached watchdog process, shared by every
tunnel like keepalive's, that checks each tunnel once a minute and stops
it — kills the process group, closes the AWS session, undoes any
`--terraform` `/etc/hosts` patch — the moment its local port stops
accepting connections. This part needs no flag; it always runs.

`tunnels up --ttl [MINUTES]` adds a second, opt-in check on top: the same
watchdog also stops a tunnel once it is older than the ttl, healthy or not.
See [configuration](configuration.md#auto-close-watchdog-and-ttl).

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

## Logging in without a browser

`tunnels` runs `aws sso login` when a profile's cached token is gone. Two
separate things make that annoying, and only one of them is fixable.

**The approval click is not removable.** AWS SSO uses the OAuth 2.0 device
authorization grant. The CLI gets a device code, and a human approves it in a
browser session that the CLI has no access to. That split is the point of the
grant: whatever holds the device code must not also be able to grant consent,
or a stolen code would be enough to mint credentials. "Auto approving" means
driving the identity provider with the user's password and MFA, which is
phishing with extra steps and is not something this tool will do.

**How often it happens is very fixable.** A profile written the legacy way,
with `sso_start_url` and `sso_region` set directly on the profile, receives an
access token and no refresh token. When it expires — eight hours by default —
the browser opens again. A profile pointing at an `[sso-session]` block
receives a refresh token, and the CLI renews it in the background. The browser
then opens only when the SSO session itself expires, which Identity Center can
set as high as 90 days.

```ini
# before: no refresh token, browser every 8h
[profile dev]
sso_start_url = https://acme.awsapps.com/start
sso_region = eu-west-1
sso_account_id = 111122223333
sso_role_name = AdministratorAccess

# after: refreshes silently
[sso-session acme]
sso_start_url = https://acme.awsapps.com/start
sso_region = eu-west-1
sso_registration_scopes = sso:account:access

[profile dev]
sso_session = acme
sso_account_id = 111122223333
sso_role_name = AdministratorAccess
```

Same portal, same account, same permission set. `tunnels auth` reports which
form each profile uses and how long its token has left; `tunnels auth
--migrate` rewrites the first form into the second, writing a timestamped
backup of `~/.aws/config` first. Profiles on one portal share a single session
block, so one login covers all of them.

The token cache is keyed by session name rather than start URL, so the first
login after migrating cannot reuse the old cache entry. Expect one browser
trip, then silence.

**Over SSH there is no browser to open.** `aws sso login --no-browser` prints
the URL and code to paste into a browser somewhere else. `tunnels` adds that
flag on its own when it detects an SSH session, or Linux with no display; set
`TUNNELS_NO_BROWSER=1` to force it, or pass `tunnels auth --no-browser`.

**Profiles that are not SSO are left alone.** `credential_process`, `role_arn`
with a `source_profile`, and static keys all renew by their own means, and
running `aws sso login` against them does nothing useful. `tunnels` now says so
instead of shelling out and failing.
