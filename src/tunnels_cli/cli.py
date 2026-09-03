"""tunnels - start AWS SSM port forward tunnels from a named config."""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from importlib.metadata import version as pkg_version
from pathlib import Path

import yaml

from tunnels_cli import ui
from tunnels_cli.menu import BACK as menu_back
from tunnels_cli.menu import menu

STATE_DIR = Path.home() / ".tunnels"
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
PORTS_FILE = STATE_DIR / "ports.json"
CONFIG_PATHS = [
    Path.home() / ".config" / "tunnels" / "config.yaml",
    Path.cwd() / "config.yaml",
]


def _version():
    try:
        return pkg_version("tunnels-cli")
    except Exception:      # running from a checkout without an install
        return "dev"


class TunnelError(Exception):
    """Any failure the user needs to read and act on."""


def load_config(path=None):
    """Read the YAML config. Prefer ~/.config/tunnels/config.yaml."""
    candidates = [Path(path)] if path else CONFIG_PATHS
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open() as handle:
                return yaml.safe_load(handle) or {}
    looked = ", ".join(str(c) for c in candidates)
    raise TunnelError(
        f"no config file found. Looked in: {looked}\n"
        "Run 'tunnels init' to create one."
    )


def config_block(config, name):
    """Return one named block, with a helpful error if it is missing."""
    if name not in config:
        known = ", ".join(sorted(config)) or "(none)"
        raise TunnelError(f"unknown config '{name}'. Known configs: {known}")
    block = config[name]
    for key in ("profile", "region", "targets"):
        if key not in block:
            raise TunnelError(f"config '{name}' is missing '{key}'")
    for target_name, target in block["targets"].items():
        validate_target(target_name, target)
        jump_for(block, target_name, target)   # raises if no jump applies
    return block


def jump_for(block, target_name, target):
    """A target's own jump wins. Otherwise the block's jump applies."""
    jump = target.get("jump") or block.get("jump")
    if not jump:
        raise TunnelError(
            f"target '{target_name}' has no jump host. Set 'jump' on the "
            "target, or once on the config block."
        )
    return jump


def validate_target(name, target):
    """A target is either an EKS cluster or a host and port. Never both."""
    has_eks = "eks" in target
    has_host = "host" in target
    if has_eks and has_host:
        raise TunnelError(f"target '{name}' has both 'eks' and 'host'. Pick one.")
    if not has_eks and not has_host:
        raise TunnelError(f"target '{name}' needs either 'eks' or 'host' and 'port'.")
    if has_host and "port" not in target:
        raise TunnelError(f"target '{name}' has 'host' but no 'port'.")


def select_targets(block, names):
    """No names means every target in the block."""
    targets = block["targets"]
    if not names:
        return dict(targets)
    missing = [n for n in names if n not in targets]
    if missing:
        known = ", ".join(sorted(targets))
        raise TunnelError(f"unknown target(s): {', '.join(missing)}. Known: {known}")
    return {n: targets[n] for n in names}


def port_is_free(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def port_holder(port):
    """Name the process listening on a port, for a clearer error message."""
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "cp"],
        capture_output=True, text=True,
    )
    pid = name = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c"):
            name = line[1:]
    return f"{name} (pid {pid})" if name else None


def remembered_port(key):
    """The port this tunnel used last time, if any."""
    try:
        with PORTS_FILE.open() as handle:
            ports = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    value = ports.get(key) if isinstance(ports, dict) else None
    return value if isinstance(value, int) else None


def remember_port(key, port):
    """Reuse the same port next time, so kubeconfig entries stay stable."""
    try:
        with PORTS_FILE.open() as handle:
            ports = json.load(handle)
        if not isinstance(ports, dict):
            ports = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        ports = {}
    ports[key] = port
    PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PORTS_FILE.open("w") as handle:
        json.dump(ports, handle, indent=2)


def pick_port(preferred, key=None):
    """A pinned port wins, then the one used last time, then any free port."""
    if preferred and port_is_free(preferred):
        return preferred
    if not preferred and key:
        last = remembered_port(key)
        if last and port_is_free(last):
            return last
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def pid_alive(pid):
    """EPERM means the process exists but is not ours. That still counts."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError):
        return False
    return True


def group_alive(pgid):
    """True while the process group still has live members.

    `killpg(pgid, 0)` is not usable here: a dead but unreaped child leaves a
    zombie in the group, and signalling a zombie answers EPERM, which reads as
    "still running" when it is not. pgrep only lists live processes.
    """
    result = subprocess.run(
        ["pgrep", "-g", str(pgid)], capture_output=True, text=True
    )
    return bool(result.stdout.split())


def terminate(pid, timeout=5):
    """Stop a session and the plugin it spawned.

    `aws ssm start-session` runs `session-manager-plugin` as a child, and the
    child is what binds the local port. Sessions are started with
    start_new_session=True, so both share a process group: killing the group
    gets both. Killing only the wrapper leaves the plugin holding the port.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, TypeError):
        return True

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not group_alive(pgid):
                return True
            time.sleep(0.1)
    return False


def prune_state(entries):
    """Drop entries whose process is gone. Keeps the state file honest."""
    return [e for e in entries if pid_alive(e.get("pid"))]


def load_state():
    """Read the state file. A missing or broken file means no tunnels."""
    try:
        with STATE_FILE.open() as handle:
            entries = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    return entries


def save_state(entries):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as handle:
        json.dump(entries, handle, indent=2)


def live_state():
    """Load, prune, write back, return."""
    entries = prune_state(load_state())
    save_state(entries)
    return entries


def endpoint_host(endpoint):
    """'https://ABC.eks.amazonaws.com' -> 'ABC.eks.amazonaws.com'"""
    return endpoint.split("://", 1)[-1].split("/", 1)[0]


def patch_kubeconfig(kubeconfig, context_name, local_port, endpoint_host):
    """Point one context's cluster at the local port, keeping TLS valid.

    `tls-server-name` makes kubectl send the real EKS hostname in the TLS
    handshake while it connects to 127.0.0.1. Without it the certificate
    check fails, and the usual workaround is an /etc/hosts entry.
    """
    contexts = {c["name"]: c for c in kubeconfig.get("contexts", [])}
    if context_name not in contexts:
        raise TunnelError(f"kubeconfig has no context '{context_name}'")
    cluster_name = contexts[context_name]["context"]["cluster"]
    for entry in kubeconfig.get("clusters", []):
        if entry["name"] == cluster_name:
            entry["cluster"]["server"] = f"https://127.0.0.1:{local_port}"
            entry["cluster"]["tls-server-name"] = endpoint_host
            return kubeconfig
    raise TunnelError(f"kubeconfig has no cluster '{cluster_name}'")


def write_kubeconfig_patch(context_name, local_port, host):
    """Read ~/.kube/config, patch it, write it back."""
    path = Path(os.environ.get("KUBECONFIG", Path.home() / ".kube" / "config"))
    with path.open() as handle:
        kubeconfig = yaml.safe_load(handle)
    patched = patch_kubeconfig(kubeconfig, context_name, local_port, host)
    with path.open("w") as handle:
        yaml.safe_dump(patched, handle, default_flow_style=False)


def aws(profile, region, *args, capture=True):
    """Run an aws CLI command. Returns parsed JSON when capture is on."""
    cmd = ["aws", "--profile", profile, "--region", region, "--output", "json", *args]
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() if capture else ""
        raise TunnelError(f"aws {' '.join(args)} failed: {detail}")
    if not capture or not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def ensure_sso(profile, region):
    """Log in only when the cached token is gone or expired."""
    probe = subprocess.run(
        ["aws", "--profile", profile, "sts", "get-caller-identity", "--output", "json"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return json.loads(probe.stdout)["Account"]
    print(f"sso token missing or expired for '{profile}'. Logging in...")
    login = subprocess.run(["aws", "--profile", profile, "sso", "login"])
    if login.returncode != 0:
        raise TunnelError(f"aws sso login failed for profile '{profile}'")
    identity = aws(profile, region, "sts", "get-caller-identity")
    return identity["Account"]


def resolve_jump(profile, region, jump):
    """Accept an instance id as is. Resolve 'tag:Key=Value' by lookup."""
    if jump.startswith("i-"):
        return jump
    if not jump.startswith("tag:") or "=" not in jump:
        raise TunnelError(
            f"jump must be an instance id or 'tag:Key=Value'. Got: {jump}"
        )
    key, value = jump[len("tag:"):].split("=", 1)
    data = aws(
        profile, region, "ec2", "describe-instances",
        "--filters", f"Name=tag:{key},Values={value}",
        "Name=instance-state-name,Values=running",
        "--query", "Reservations[].Instances[].InstanceId",
    )
    ids = data or []
    if not ids:
        raise TunnelError(
            f"no running instance with tag {key}={value} in {region} "
            f"for profile '{profile}'"
        )
    online = aws(
        profile, region, "ssm", "describe-instance-information",
        "--filters", f"Key=InstanceIds,Values={','.join(ids)}",
        "--query", "InstanceInformationList[?PingStatus=='Online'].InstanceId",
    ) or []
    ids = [i for i in ids if i in online]
    if not ids:
        raise TunnelError(
            f"no instance with tag {key}={value} in {region} has a connected "
            f"SSM agent for profile '{profile}'"
        )
    if len(ids) > 1:
        ui.warn(f"{len(ids)} instances match {key}={value}. Using {ids[0]}.")
    return ids[0]


def eks_endpoint(profile, region, cluster):
    data = aws(
        profile, region, "eks", "describe-cluster", "--name", cluster,
        "--query", "cluster.endpoint",
    )
    if not data:
        raise TunnelError(f"cluster '{cluster}' has no endpoint")
    return endpoint_host(data)


def update_kubeconfig(profile, region, cluster, alias):
    subprocess.run(
        ["aws", "--profile", profile, "--region", region, "eks", "update-kubeconfig",
         "--name", cluster, "--alias", alias],
        check=True, capture_output=True, text=True,
    )


def wait_for_port(port, timeout=20):
    """Poll a TCP connect until it works or the timeout runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def start_session(profile, region, instance_id, host, port, local_port, log_path):
    """Start a detached SSM port forward. Returns the process id."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    params = f"host={host},portNumber={port},localPortNumber={local_port}"
    cmd = [
        "aws", "--profile", profile, "--region", region,
        "ssm", "start-session", "--target", instance_id,
        "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
        "--parameters", params,
    ]
    log = log_path.open("w")
    proc = subprocess.Popen(
        cmd, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    return proc.pid


def session_id_from_log(log_path):
    """Pull the SSM session id out of the plugin's own output.

    Killing the local process frees the port, but AWS keeps the session in
    Connected until it times out. The id is needed to close it properly.
    """
    try:
        text = log_path.read_text()
    except OSError:
        return None
    match = re.search(r"Starting session with SessionId:\s*(\S+)", text)
    return match.group(1) if match else None


def terminate_session(profile, region, session_id):
    """Best effort. A tunnel is already stopped locally by this point."""
    if not session_id:
        return False
    result = subprocess.run(
        ["aws", "--profile", profile, "--region", region,
         "ssm", "terminate-session", "--session-id", session_id],
        capture_output=True, text=True,
    )
    return result.returncode == 0


HOSTS_PATH = Path("/etc/hosts")


def hosts_marker(key):
    return f"# tunnels:{key}"


def add_hosts_entry(hostname, key):
    """Point hostname at 127.0.0.1, so a client dialing the real DNS name
    (e.g. a terraform provider that hardcodes it) lands on the tunnel instead.

    /etc/hosts has no notion of port, so this only gets you halfway: the
    caller still has to point its own port config (kubeapi_port, etc.) at
    the tunnel's local_port for the connection to actually land anywhere.
    Requires root, so this shells out to sudo - expect a password prompt.
    """
    remove_hosts_entry(key)  # replace any stale line for this key first
    line = f"127.0.0.1 {hostname} {hosts_marker(key)}\n"
    result = subprocess.run(
        ["sudo", "tee", "-a", str(HOSTS_PATH)],
        input=line, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise TunnelError(
            f"could not add /etc/hosts entry for {hostname}: {result.stderr.strip()}"
        )


def remove_hosts_entry(key):
    marker = hosts_marker(key)
    try:
        text = HOSTS_PATH.read_text()
    except OSError:
        return
    if marker not in text:
        return
    kept = "\n".join(l for l in text.splitlines() if marker not in l) + "\n"
    result = subprocess.run(
        ["sudo", "tee", str(HOSTS_PATH)],
        input=kept, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise TunnelError(f"could not remove /etc/hosts entry: {result.stderr.strip()}")


def start_target(config_name, name, target, block, account, jump_cache,
                 entries, terraform):
    """Bring one target up and record it. Raises TunnelError if it cannot."""
    profile, region = block["profile"], block["region"]
    key = f"{config_name}/{name}"
    label = ui.paint(name, "bright_cyan", "bold")

    jump = jump_for(block, name, target)
    if jump not in jump_cache:
        with ui.Spinner(f"{name}: finding jump host {jump}"):
            jump_cache[jump] = resolve_jump(profile, region, jump)
    instance_id = jump_cache[jump]

    if "eks" in target:
        with ui.Spinner(f"{name}: resolving cluster {target['eks']}"):
            host, port = eks_endpoint(profile, region, target["eks"]), 443
    else:
        host, port = target["host"], int(target["port"])

    local_port = pick_port(target.get("local_port"), key=key)
    wanted = target.get("local_port")
    if wanted and local_port != wanted:
        holder = port_holder(wanted)
        ui.warn(f"{name}: port {wanted} is busy"
                + (f" ({holder})" if holder else "")
                + f", using {local_port} instead")

    log_path = LOG_DIR / f"{config_name}-{name}.log"
    pid = start_session(profile, region, instance_id, host, port,
                        local_port, log_path)

    with ui.Spinner(f"{name}: waiting for port {local_port}"):
        opened = wait_for_port(local_port)
    if not opened:
        terminate(pid)
        tail = log_path.read_text().strip().splitlines()[-5:]
        raise TunnelError(
            f"{name}: port {local_port} never opened.\n  " + "\n  ".join(tail)
        )

    entry = {
        "key": key, "config": config_name, "target": name, "pid": pid,
        "local_port": local_port, "remote_host": host, "remote_port": port,
        "profile": profile, "region": region, "account": account,
        "jump": instance_id,
        "cluster": target.get("eks"), "context": None,
        "session_id": session_id_from_log(log_path),
        "started": time.time(),
    }

    if terraform:
        add_hosts_entry(host, key)
        entry["hosts_entry"] = True

    if "eks" in target:
        alias = f"tunnels-{config_name}-{name}"
        update_kubeconfig(profile, region, target["eks"], alias)
        write_kubeconfig_patch(alias, local_port, host)
        entry["context"] = alias
        ui.ok(f"{label} {ui.paint(':' + str(local_port), 'green', 'bold')} "
              f"{ui.paint(ui.sym.dot, 'grey')} via {instance_id} "
              f"{ui.paint(ui.sym.dot, 'grey')} context "
              f"{ui.paint(alias, 'magenta')}")
    else:
        ui.ok(f"{label} {ui.paint(':' + str(local_port), 'green', 'bold')} "
              f"{ui.paint(ui.sym.arrow, 'grey')} {host}:{port} "
              f"{ui.paint(ui.sym.dot, 'grey')} via {instance_id}")

    if terraform:
        ui.ok(f"{label} /etc/hosts {host} {ui.sym.arrow} 127.0.0.1 "
              f"(point terraform's port var at {local_port})")

    entries.append(entry)
    save_state(entries)
    remember_port(key, local_port)


def cmd_up(config_name, target_names, keepalive=None, terraform=False, ttl=None):
    config = load_config()
    block = config_block(config, config_name)
    targets = select_targets(block, target_names)
    profile, region = block["profile"], block["region"]

    print(ui.rule(f"up {ui.paint(config_name, 'bold')}"))
    with ui.Spinner(f"checking credentials for {profile}"):
        account = ensure_sso(profile, region)
    ui.ok(f"account {ui.paint(account, 'bold')} "
          f"{ui.paint(ui.sym.dot, 'grey')} {profile} {ui.paint(ui.sym.dot, 'grey')} {region}")

    jump_cache = {}
    entries = live_state()
    running = {e["key"] for e in entries}
    failures = []

    for name, target in sorted(targets.items()):
        key = f"{config_name}/{name}"
        label = ui.paint(name, "bright_cyan", "bold")
        if key in running:
            ui.step(f"{label} already up, skipping")
            if terraform:
                existing = next(e for e in entries if e["key"] == key)
                add_hosts_entry(existing["remote_host"], key)
                existing["hosts_entry"] = True
                save_state(entries)
                ui.ok(f"{label} /etc/hosts {existing['remote_host']} "
                      f"{ui.sym.arrow} 127.0.0.1 "
                      f"(point terraform's port var at {existing['local_port']})")
            continue

        # One broken target must not cost you the working ones: keep going and
        # report every failure together at the end.
        try:
            start_target(config_name, name, target, block, account,
                         jump_cache, entries, terraform)
        except TunnelError as exc:
            failures.append((name, str(exc)))
            ui.fail(f"{name}: {exc}", stream=sys.stdout)

    if block.get("hud"):
        start_hud()

    interval = keepalive_interval(block, keepalive)
    if interval:
        if start_keepalive(interval):
            ui.info(f"  keepalive every {interval}s")
        else:
            ui.info("  keepalive already running")

    minutes = ttl_minutes(block, ttl)
    if start_watchdog(minutes):
        extra = f", auto-closes after {minutes}m" if minutes else ""
        ui.info(f"  watchdog clears dead tunnels automatically{extra}")

    print(ui.rule())
    if failures:
        names = ", ".join(name for name, _ in failures)
        ui.warn(f"{len(failures)} of {len(targets)} target(s) failed: {names}")
        ui.info(f"  the rest are up {ui.sym.dot} tunnels status")
        return 1
    ui.info(f"  next: tunnels status {ui.sym.dot} tunnels down {config_name}")
    return 0


def matching_entries(entries, config_name, target_names):
    if config_name == "all":
        return list(entries)
    picked = [e for e in entries if e["config"] == config_name]
    if target_names:
        picked = [e for e in picked if e["target"] in target_names]
    return picked


def stop_entry(entry, reason=None):
    """Stop one tracked tunnel and drop it from the state file.

    Shared by `down` (explicit) and the ttl watchdog (automatic), so both
    stop a tunnel the same way: kill the process group, close the AWS
    session, undo any /etc/hosts patch.
    """
    if entry.get("hosts_entry"):
        remove_hosts_entry(entry["key"])
    stopped = terminate(entry["pid"])
    closed = terminate_session(
        entry.get("profile"), entry.get("region"), entry.get("session_id")
    )
    port = entry["local_port"]
    key = ui.paint(entry["key"], "bright_cyan", "bold")
    tag = f" ({reason})" if reason else ""
    note = "" if closed else ", aws session left to time out"
    if stopped and port_is_free(port):
        ui.ok(f"stopped {key}{tag} {ui.paint(f'(port {port} released{note})', 'grey')}")
    elif stopped:
        holder = port_holder(port)
        ui.warn(f"stopped {entry['key']}{tag}, but port {port} is still held"
                + (f" by {holder}" if holder else ""))
    else:
        ui.fail(f"could not stop {entry['key']}{tag} (pid {entry['pid']})",
                stream=sys.stdout)
    save_state([e for e in load_state() if e["key"] != entry["key"]])
    return stopped


def cmd_down(config_name, target_names):
    entries = live_state()
    doomed = matching_entries(entries, config_name, target_names)
    if not doomed:
        ui.info("nothing to stop")
        return 0
    print(ui.rule(f"down {ui.paint(config_name, 'bold')}"))
    for entry in doomed:
        stop_entry(entry)
    return 0


def shorten(text, limit=44):
    """Keep the tail of a long endpoint, which is the telling part."""
    return text if len(text) <= limit else "\u2026" + text[-(limit - 1):]


def cmd_status():
    entries = live_state()
    if not entries:
        ui.info(f"no tunnels up {ui.sym.dot} start one with 'tunnels up <config>'")
        return 0
    rows = []
    for entry in sorted(entries, key=lambda e: e["key"]):
        where = entry["cluster"] or f"{entry['remote_host']}:{entry['remote_port']}"
        healthy = not port_is_free(entry["local_port"])
        rows.append([
            ui.paint(ui.sym.up, "green" if healthy else "red"),
            ui.paint(entry["key"], "bright_cyan", "bold"),
            ui.paint(f":{entry['local_port']}", "green" if healthy else "grey"),
            entry["account"],
            shorten(where),
            ui.paint(entry["context"], "magenta") if entry["context"]
            else ui.paint("-", "grey"),
            ui.paint(ui.human_age(time.time() - entry["started"]), "grey"),
        ])
    print(ui.rule(f"{len(rows)} tunnel(s) up"))
    print(ui.table(["", "TUNNEL", "PORT", "ACCOUNT", "TARGET", "CONTEXT", "AGE"],
                   rows))
    return 0


HUD_PID_FILE = STATE_DIR / "hud.pid"
KEEPALIVE_PID_FILE = STATE_DIR / "keepalive.pid"
KEEPALIVE_DEFAULT = 300


def hud_running():
    try:
        pid = int(HUD_PID_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def start_hud():
    if hud_running():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tunnels_cli.hud"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    HUD_PID_FILE.write_text(str(proc.pid))


def keepalive_running():
    try:
        pid = int(KEEPALIVE_PID_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def keepalive_interval(block, flag):
    """The flag wins over the config block. None means leave it off."""
    if flag is not None:
        return flag
    value = block.get("keepalive")
    if value in (None, False):
        return None
    return KEEPALIVE_DEFAULT if value is True else int(value)


def start_keepalive(interval):
    """One detached process serves every tunnel, like the hud does."""
    if keepalive_running():
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tunnels_cli.keepalive", str(interval)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    KEEPALIVE_PID_FILE.write_text(str(proc.pid))
    return True


WATCHDOG_PID_FILE = STATE_DIR / "watchdog.pid"
WATCHDOG_DEFAULT_MINUTES = 480


def watchdog_running():
    try:
        pid = int(WATCHDOG_PID_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def ttl_minutes(block, flag):
    """The flag wins over the config block. None means leave it off."""
    if flag is not None:
        return flag
    value = block.get("ttl")
    if value in (None, False):
        return None
    return WATCHDOG_DEFAULT_MINUTES if value is True else int(value)


def start_watchdog(minutes=None):
    """One detached process watches every tunnel, like keepalive does.

    Started automatically on every `up`, no flag needed: it always
    force-closes a tunnel whose local port has stopped accepting
    connections (the session died on the AWS side but the local process
    is still hanging around). A ttl on top of that is opt-in; `minutes`
    of None or 0 means no ttl, dead-port checking only.
    """
    if watchdog_running():
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tunnels_cli.watchdog", str(minutes or 0)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    WATCHDOG_PID_FILE.write_text(str(proc.pid))
    return True


def cmd_interactive():
    """No subcommand given: pick an account and a target, then start it."""
    print(ui.banner(f"AWS SSM tunnels {ui.sym.dot} v{_version()}"))
    config = load_config()
    accounts = sorted(config)
    if not accounts:
        raise TunnelError("no accounts configured. Run 'tunnels init'.")
    live = {e["key"] for e in live_state()}

    while True:
        account = menu("Which account?", accounts)
        if account is None:
            return 0

        block = config_block(config, account)
        target_names = sorted(block["targets"])
        choices = [
            f"{n} {ui.sym.up}" if f"{account}/{n}" in live else n
            for n in target_names
        ] + ["all"]

        target = menu(f"Which target in '{account}'?", choices,
                      allow_back=True)
        if target is menu_back:
            continue          # back / q: return to the account list
        if target is None:
            return 0          # ctrl-c: stop here
        target = target.replace(f" {ui.sym.up}", "")

        return cmd_up(account, [] if target == "all" else [target])


def cmd_hud():
    """Toggle: start it when it is off, stop it when it is on."""
    pid = hud_running()
    if pid:
        os.kill(pid, 15)
        HUD_PID_FILE.unlink(missing_ok=True)
        ui.ok("hud stopped")
        return 0
    start_hud()
    ui.ok("hud started")
    return 0


def open_in_editor(path):
    """Open the config the way the user would expect on their machine."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    try:
        if editor:
            subprocess.run([*editor.split(), str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-t", str(path)], check=False)
        else:
            return False
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# discover: build a config block by asking the account what it has
# ---------------------------------------------------------------------------

def profile_region(profile):
    """The region configured for a profile, so --region can stay optional."""
    result = subprocess.run(
        ["aws", "configure", "get", "region", "--profile", profile],
        capture_output=True, text=True,
    )
    region = result.stdout.strip()
    if not region:
        raise TunnelError(
            f"profile '{profile}' has no region. Pass --region."
        )
    return region


def ssm_instances(profile, region):
    """Instances with a live SSM agent, with their Name and cluster tags."""
    registered = aws(profile, region, "ssm", "describe-instance-information",
                     "--query", "InstanceInformationList[].InstanceId") or []
    if not registered:
        return []
    described = aws(
        profile, region, "ec2", "describe-instances",
        "--instance-ids", *registered,
        "--filters", "Name=instance-state-name,Values=running",
        "--query", "Reservations[].Instances[].[InstanceId,Tags]",
    ) or []
    instances = []
    for instance_id, tags in described:
        tags = {t["Key"]: t["Value"] for t in (tags or [])}
        instances.append({
            "InstanceId": instance_id,
            "Name": tags.get("Name"),
            "cluster": tags.get("aws:eks:cluster-name"),
        })
    return instances


def guess_jump(instances, cluster):
    """The most likely jump host for a cluster, as a config value."""
    for instance in instances:
        if instance.get("cluster") == cluster:
            return f"tag:aws:eks:cluster-name={cluster}"
    for instance in instances:
        if instance.get("Name"):
            return f"tag:Name={instance['Name']}"
    if instances:
        return instances[0]["InstanceId"]
    return None


def render_block(name, profile, region, targets):
    """Turn chosen targets into config YAML text.

    A jump shared by every target is lifted to the block, which is how a
    person would write it by hand.
    """
    jumps = {t["jump"] for t in targets}
    shared = jumps.pop() if len(jumps) == 1 else None

    block = {"profile": profile, "region": region}
    if shared:
        block["jump"] = shared
    block["hud"] = True
    block["targets"] = {}
    for target in targets:
        entry = {"eks": target["eks"]}
        if not shared:
            entry["jump"] = target["jump"]
        block["targets"][target["name"]] = entry
    return yaml.safe_dump({name: block}, default_flow_style=False, sort_keys=False)


def ask(question, default="y"):
    """A yes/no prompt that also works when answers are piped in.

    With no input at all, the default applies. The prompt that writes to the
    config defaults to no, so an unattended run never edits the file.
    """
    choices = "Y/n" if default == "y" else "y/N"
    try:
        answer = input(f"{question} [{choices}] ").strip().lower()
    except EOFError:
        return default == "y"
    if not answer:
        return default == "y"
    return answer.startswith("y")


def cmd_discover(profile, region, block_name):
    account = ensure_sso(profile, region)
    print(f"account {account} · profile {profile} · region {region}\n")

    clusters = aws(profile, region, "eks", "list-clusters",
                   "--query", "clusters") or []
    if not clusters:
        raise TunnelError(f"no EKS clusters in {region} for profile '{profile}'")

    instances = ssm_instances(profile, region)
    print(f"{len(clusters)} cluster(s), {len(instances)} SSM-registered instance(s)\n")
    if not instances:
        raise TunnelError(
            "no instances have a live SSM agent here, so there is nothing to "
            "tunnel through."
        )

    chosen = []
    for cluster in clusters:
        jump = guess_jump(instances, cluster)
        print(f"cluster {cluster}")
        print(f"    jump: {jump}")
        if ask("    add it?"):
            target = cluster.replace("_", "-").lower()
            for suffix in ("-cluster", "-eks", "eks-"):
                target = target.replace(suffix, "")
            target = target.strip("-") or cluster.lower()
            chosen.append({"name": target, "eks": cluster, "jump": jump})
            print(f"    added as target '{target}'\n")
        else:
            print("    skipped\n")

    if not chosen:
        print("nothing chosen, config left alone")
        return 0

    text = render_block(block_name, profile, region, chosen)
    print("This block will be added:\n")
    print("\n".join("    " + line for line in text.splitlines()))

    target_file = CONFIG_PATHS[0]
    existing = {}
    if target_file.exists():
        with target_file.open() as handle:
            existing = yaml.safe_load(handle) or {}
    if block_name in existing:
        print(f"\n'{block_name}' already exists in {target_file}.")
        print("Pick another name with --name, or edit the file by hand.")
        return 1

    if not ask(f"\nAppend it to {target_file}?", default="n"):
        print("config left alone")
        return 0

    target_file.parent.mkdir(parents=True, exist_ok=True)
    with target_file.open("a") as handle:
        handle.write("\n" + text)
    print(f"added to {target_file}\nRun: tunnels up {block_name}")
    return 0


# ---------------------------------------------------------------------------
# doctor: find tunnels and AWS sessions that nothing is tracking any more
# ---------------------------------------------------------------------------

def running_plugins():
    """Every session-manager-plugin on this machine: pid -> process group."""
    listed = subprocess.run(
        ["pgrep", "-fl", "session-manager-plugin"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    found = {}
    for line in listed:
        pid = int(line.split()[0])
        try:
            found[pid] = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            continue
    return found


def orphan_pids(state, running):
    """Plugin processes whose process group is not a tunnel we know about."""
    known = {entry["pid"] for entry in state}
    return sorted(pid for pid, pgid in running.items() if pgid not in known)


def our_session_ids():
    """Session ids this tool has ever started, read back from its own logs."""
    ids = set()
    if not LOG_DIR.is_dir():
        return ids
    for log in LOG_DIR.glob("*.log"):
        found = session_id_from_log(log)
        if found:
            ids.add(found)
    return ids


def orphan_sessions(sessions, live_ids, our_ids):
    """AWS sessions this tool started that no live tunnel accounts for.

    Sessions started by anything else are left alone: closing someone's
    interactive shell because it looks unfamiliar would be worse than the
    leak this cleans up.
    """
    return [
        s for s in sessions
        if s["SessionId"] in our_ids and s["SessionId"] not in live_ids
    ]


def cmd_doctor(fix):
    entries = live_state()
    problems = 0

    plugins = running_plugins()
    strays = orphan_pids(entries, plugins)
    print(ui.rule("doctor"))
    if strays:
        problems += len(strays)
        ui.warn(f"{len(strays)} port forward process(es) with no tunnel behind them")
        for pid in strays:
            ui.info(f"      pid {pid}")
        if fix:
            for pid in strays:
                terminate(pid)
            ui.ok("stopped")
    else:
        ui.ok("no stray port forward processes")

    ours = our_session_ids()
    live_ids = {e.get("session_id") for e in entries if e.get("session_id")}
    accounts = {}
    for entry in entries:
        accounts[(entry["profile"], entry["region"])] = True
    try:
        config = load_config()
    except TunnelError:
        config = {}
    for block in config.values():
        if isinstance(block, dict) and "profile" in block and "region" in block:
            accounts[(block["profile"], block["region"])] = True

    for profile, region in sorted(accounts):
        probe = subprocess.run(
            ["aws", "--profile", profile, "sts", "get-caller-identity"],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            ui.info(f"  {profile}: not logged in, skipped")
            continue
        try:
            listed = aws(profile, region, "ssm", "describe-sessions",
                         "--state", "Active", "--query", "Sessions") or []
        except TunnelError as exc:
            reason = "no permission" if "AccessDenied" in str(exc) else "call failed"
            ui.warn(f"{profile}: cannot list sessions ({reason}), skipped")
            continue
        stale = orphan_sessions(listed, live_ids, ours)
        if not stale:
            ui.ok(f"{profile}: no stale aws sessions")
            continue
        problems += len(stale)
        ui.warn(f"{profile}: {len(stale)} aws session(s) still open with no tunnel")
        for session in stale:
            ui.info(f"      {session['SessionId']}  target {session.get('Target')}")
        if fix:
            for session in stale:
                terminate_session(profile, region, session["SessionId"])
            ui.ok("terminated")

    print(ui.rule())
    if problems and not fix:
        ui.info("  run 'tunnels doctor --fix' to clean these up")
    elif not problems:
        ui.ok("nothing to clean up")
    return 0


def cmd_logs(config_name, target, follow):
    """Tail the session log for one tunnel, for when a jump host misbehaves."""
    path = LOG_DIR / f"{config_name}-{target}.log"
    if not path.is_file():
        raise TunnelError(f"no log at {path}. Has '{config_name}/{target}' been up?")
    ui.info(f"{path}")
    cmd = ["tail", "-f", str(path)] if follow else ["tail", "-n", "50", str(path)]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_init():
    """Create the config if it is missing, then open it for editing."""
    target = CONFIG_PATHS[0]
    if target.exists():
        print(f"config already at {target}")
    else:
        example = Path(__file__).resolve().parent / "config.example.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(example.read_text())
        print(f"wrote {target}")

    print(ui.rule("fill in these, one block per environment"))
    for key, what in (
        ("profile", "an SSO profile from ~/.aws/config"),
        ("region", "the account's region"),
        ("jump", "Name tag of the SSM-registered jump host, or an i-... id"),
        ("eks", "the EKS cluster name (or host + port for a database)"),
    ):
        print(f"  {ui.paint(key.ljust(9), 'bright_cyan')} {ui.paint(what, 'grey')}")

    if open_in_editor(target):
        print(f"\nopened {target}")
    else:
        print(f"\nedit it at: {target}")
    print("Then run: tunnels up <block-name>")
    return 0


def cmd_config(path_only):
    """Show where the config lives, and open it unless only the path is wanted."""
    target = next((c for c in CONFIG_PATHS if c.is_file()), None)
    if target is None:
        looked = ", ".join(str(c) for c in CONFIG_PATHS)
        raise TunnelError(
            f"no config file found. Looked in: {looked}\n"
            "Run 'tunnels init' to create one."
        )
    print(target)
    if path_only:
        return 0
    if not open_in_editor(target):
        print("no $EDITOR set - open the path above yourself")
    return 0


def cmd_profiles():
    """List the accounts configured, with their AWS profile and targets."""
    config = load_config()
    if not config:
        ui.info("no accounts configured. Run 'tunnels init'.")
        return 0
    rows = []
    for name in sorted(config):
        block = config[name]
        rows.append([
            ui.paint(name, "bright_cyan", "bold"),
            block.get("profile", "?"),
            block.get("region", "?"),
            ui.paint(", ".join(sorted(block.get("targets", {}))), "grey"),
        ])
    print(ui.rule(f"{len(rows)} config(s)"))
    print(ui.table(["CONFIG", "PROFILE", "REGION", "TARGETS"], rows))
    return 0


EPILOG = """examples:
  tunnels                      pick an account and a target from a menu
  tunnels up dev               start every target in the 'dev' block
  tunnels up dev api db        start named targets only
  tunnels status               what is up right now
  tunnels logs dev api -f      follow one tunnel's session log
  tunnels down all             stop everything

colour: off automatically when piped, or with --no-color / NO_COLOR=1
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tunnels",
        description="Open AWS SSM port forward tunnels to private clusters "
                    "and databases through a jump host.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version",
        version=f"tunnels {_version()}",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="plain output, no ANSI colour",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    up = sub.add_parser("up", help="start tunnels for a config")
    up.add_argument("config")
    up.add_argument("targets", nargs="*")
    up.add_argument(
        "--keepalive", nargs="?", type=int, const=KEEPALIVE_DEFAULT, default=None,
        metavar="SECONDS",
        help=f"poke each tunnel so the SSM session never idles out "
             f"(default {KEEPALIVE_DEFAULT}s). Off unless asked for",
    )
    up.add_argument(
        "--terraform", action="store_true",
        help="patch /etc/hosts so the real hostname resolves to 127.0.0.1, "
             "for tools (like terraform) that dial it directly instead of "
             "using the kubeconfig. Removed again on 'down'",
    )
    up.add_argument(
        "--ttl", nargs="?", type=int, const=WATCHDOG_DEFAULT_MINUTES, default=None,
        metavar="MINUTES",
        help=f"also auto-close tunnels after this many minutes, healthy or "
             f"not (default {WATCHDOG_DEFAULT_MINUTES}m). A dead tunnel is "
             f"always cleared automatically, with or without this flag",
    )

    down = sub.add_parser("down", help="stop tunnels")
    down.add_argument("config", help="config name, or 'all'")
    down.add_argument("targets", nargs="*")

    sub.add_parser("status", help="list live tunnels")
    sub.add_parser("profiles", help="list configured accounts")
    sub.add_parser("init", help="write a starter config file")

    cfg = sub.add_parser("config", help="open the config file, or print its path")
    cfg.add_argument("--path", action="store_true", help="print the path only")

    doctor = sub.add_parser("doctor", help="find leftover tunnels and sessions")
    doctor.add_argument("--fix", action="store_true", help="clean up what it finds")

    disc = sub.add_parser("discover", help="build a config block from an account")
    disc.add_argument("profile", help="an SSO profile from ~/.aws/config")
    disc.add_argument("--region", help="defaults to the profile's region")
    disc.add_argument("--name", help="config block name, defaults to the profile")
    sub.add_parser("hud", help="toggle the floating label")

    logs = sub.add_parser("logs", help="tail a tunnel's session log")
    logs.add_argument("config")
    logs.add_argument("target")
    logs.add_argument("-f", "--follow", action="store_true",
                      help="keep printing new lines")

    args = parser.parse_args(argv)
    if args.no_color:
        ui.set_color(False)
    try:
        if args.command == "up":
            return cmd_up(args.config, args.targets, args.keepalive, args.terraform, args.ttl)
        if args.command == "down":
            return cmd_down(args.config, args.targets)
        if args.command == "status":
            return cmd_status()
        if args.command == "profiles":
            return cmd_profiles()
        if args.command == "hud":
            return cmd_hud()
        if args.command == "logs":
            return cmd_logs(args.config, args.target, args.follow)
        if args.command == "config":
            return cmd_config(args.path)
        if args.command == "init":
            return cmd_init()
        if args.command == "doctor":
            return cmd_doctor(args.fix)
        if args.command == "discover":
            region = args.region or profile_region(args.profile)
            return cmd_discover(args.profile, region, args.name or args.profile)
        return cmd_interactive()
    except TunnelError as exc:
        ui.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        ui.info("cancelled")
        return 130


def app():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    app()
