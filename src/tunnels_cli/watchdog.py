"""Auto-close tunnels that outlive their ttl or go dead underneath you.

A tunnel's local process (aws ssm start-session + session-manager-plugin)
does not always exit when the AWS side of the session ends - it can hang
around holding the port with nothing behind it. This watchdog force-closes
any tunnel whose port stops accepting connections, and any tunnel older
than its ttl, regardless of port health.

One process serves every tunnel, like keepalive. It reads the same state
file the CLI does, and exits on its own once the last tunnel is gone.
"""

import socket
import sys
import time

from . import cli

CHECK_INTERVAL = 60


def port_dead(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return False
    except OSError:
        return True


def sweep(ttl_seconds):
    """Stop any entry that is dead or has outlived its ttl. Returns count stopped."""
    entries = cli.live_state()
    now = time.time()
    stopped = 0
    for entry in entries:
        if port_dead(entry.get("local_port")):
            cli.stop_entry(entry, reason="port went dead")
            stopped += 1
        elif ttl_seconds and now - entry["started"] > ttl_seconds:
            cli.stop_entry(entry, reason="ttl expired")
            stopped += 1
    return stopped


def run(ttl_minutes, interval=CHECK_INTERVAL):
    ttl_seconds = ttl_minutes * 60 if ttl_minutes else None
    while True:
        if not cli.live_state():
            return 0
        sweep(ttl_seconds)
        time.sleep(interval)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ttl_minutes = int(argv[0]) if argv else 0
    cli.WATCHDOG_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        return run(ttl_minutes or None)
    finally:
        cli.WATCHDOG_PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
