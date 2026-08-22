"""tunnels - start AWS SSM port forward tunnels from a named config."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

STATE_DIR = Path.home() / ".tunnels"
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
CONFIG_PATHS = [
    Path.home() / ".config" / "tunnels" / "config.yaml",
    Path.cwd() / "config.yaml",
]


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


def pick_port(preferred):
    """Use the wanted port if it is free, else let the OS choose one."""
    if preferred and port_is_free(preferred):
        return preferred
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
    if len(ids) > 1:
        print(f"warning: {len(ids)} instances match {key}={value}. Using {ids[0]}.")
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


def cmd_up(config_name, target_names):
    config = load_config()
    block = config_block(config, config_name)
    targets = select_targets(block, target_names)
    profile, region = block["profile"], block["region"]

    account = ensure_sso(profile, region)
    print(f"account {account}")

    jump_cache = {}
    entries = live_state()
    running = {e["key"] for e in entries}

    for name, target in sorted(targets.items()):
        key = f"{config_name}/{name}"
        if key in running:
            print(f"  {name}: already up, skipping")
            continue

        jump = jump_for(block, name, target)
        if jump not in jump_cache:
            jump_cache[jump] = resolve_jump(profile, region, jump)
        instance_id = jump_cache[jump]

        if "eks" in target:
            host, port = eks_endpoint(profile, region, target["eks"]), 443
        else:
            host, port = target["host"], int(target["port"])

        local_port = pick_port(target.get("local_port"))
        wanted = target.get("local_port")
        if wanted and local_port != wanted:
            holder = port_holder(wanted)
            print(f"  {name}: port {wanted} is busy"
                  + (f" ({holder})" if holder else "")
                  + f", using {local_port} instead")

        log_path = LOG_DIR / f"{config_name}-{name}.log"
        pid = start_session(profile, region, instance_id, host, port,
                            local_port, log_path)

        if not wait_for_port(local_port):
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
            "started": time.time(),
        }

        if "eks" in target:
            alias = f"tunnels-{config_name}-{name}"
            update_kubeconfig(profile, region, target["eks"], alias)
            write_kubeconfig_patch(alias, local_port, host)
            entry["context"] = alias
            print(f"  {name}: up on {local_port} via {instance_id} "
                  f"\u00b7 context {alias}")
        else:
            print(f"  {name}: up on {local_port} via {instance_id} "
                  f"-> {host}:{port}")

        entries.append(entry)
        save_state(entries)

    if block.get("hud"):
        start_hud()
    return 0


def matching_entries(entries, config_name, target_names):
    if config_name == "all":
        return list(entries)
    picked = [e for e in entries if e["config"] == config_name]
    if target_names:
        picked = [e for e in picked if e["target"] in target_names]
    return picked


def cmd_down(config_name, target_names):
    entries = live_state()
    doomed = matching_entries(entries, config_name, target_names)
    if not doomed:
        print("nothing to stop")
        return 0
    for entry in doomed:
        stopped = terminate(entry["pid"])
        port = entry["local_port"]
        if stopped and port_is_free(port):
            print(f"stopped {entry['key']} (port {port} released)")
        elif stopped:
            holder = port_holder(port)
            print(f"stopped {entry['key']}, but port {port} is still held"
                  + (f" by {holder}" if holder else ""))
        else:
            print(f"could not stop {entry['key']} (pid {entry['pid']})")
    keys = {e["key"] for e in doomed}
    save_state([e for e in entries if e["key"] not in keys])
    return 0


def cmd_status():
    entries = live_state()
    if not entries:
        print("no tunnels up")
        return 0
    width = max(len(e["key"]) for e in entries)
    for entry in sorted(entries, key=lambda e: e["key"]):
        where = entry["cluster"] or f"{entry['remote_host']}:{entry['remote_port']}"
        age = int(time.time() - entry["started"])
        print(
            f"  {entry['key']:<{width}}  :{entry['local_port']:<6} "
            f"{entry['account']}  {where}  up {age}s"
        )
        if entry["context"]:
            print(f"  {'':<{width}}  kubectl context: {entry['context']}")
    return 0


HUD_PID_FILE = STATE_DIR / "hud.pid"


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


def cmd_hud():
    """Toggle: start it when it is off, stop it when it is on."""
    pid = hud_running()
    if pid:
        os.kill(pid, 15)
        HUD_PID_FILE.unlink(missing_ok=True)
        print("hud stopped")
        return 0
    start_hud()
    print("hud started")
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

    print("\nFill in these, one block per environment:")
    print("  profile     an SSO profile from ~/.aws/config")
    print("  region      the account's region")
    print("  jump        Name tag of the SSM-registered jump host, or an i-... id")
    print("  eks         the EKS cluster name (or use host + port for a database)")

    if open_in_editor(target):
        print(f"\nopened {target}")
    else:
        print(f"\nedit it at: {target}")
    print("Then run: tunnels up <block-name>")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tunnels")
    sub = parser.add_subparsers(dest="command")

    up = sub.add_parser("up", help="start tunnels for a config")
    up.add_argument("config")
    up.add_argument("targets", nargs="*")

    down = sub.add_parser("down", help="stop tunnels")
    down.add_argument("config", help="config name, or 'all'")
    down.add_argument("targets", nargs="*")

    sub.add_parser("status", help="list live tunnels")
    sub.add_parser("init", help="write a starter config file")
    sub.add_parser("hud", help="toggle the floating label")

    args = parser.parse_args(argv)
    try:
        if args.command == "up":
            return cmd_up(args.config, args.targets)
        if args.command == "down":
            return cmd_down(args.config, args.targets)
        if args.command == "hud":
            return cmd_hud()
        if args.command == "init":
            return cmd_init()
        return cmd_status()
    except TunnelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def app():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    app()
