"""Keep SSM sessions from going idle.

Session Manager closes an idle session after the account's
`idleSessionTimeout` (20 minutes by default), and that setting is
account-wide: it cannot be set per session. Opening a TCP connection to a
forwarded local port pushes a stream through the session, which counts as
activity, so a short connect on a timer keeps the tunnel alive.

One process serves every tunnel. It reads the same state file the CLI does,
and exits on its own once the last tunnel is gone, so there is still nothing
to babysit.
"""

import socket
import sys
import time

from . import cli


def poke(port):
    """Open and close a connection, so the session sees traffic."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    except OSError:
        return False
    return True


def run(interval):
    while True:
        entries = cli.live_state()
        if not entries:
            return 0
        for entry in entries:
            poke(entry.get("local_port"))
        time.sleep(interval)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    interval = int(argv[0]) if argv else cli.KEEPALIVE_DEFAULT
    cli.KEEPALIVE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        return run(interval)
    finally:
        cli.KEEPALIVE_PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
