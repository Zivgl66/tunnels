"""Is a tunnel alive? One answer, shared by status, the hud and the watchdog.

The three used to disagree. `status` bind-tested the port ("is it free?"),
the hud checked whether the local pid was still around, and only the
watchdog actually connected. For the failure that really happens - the AWS
session ends while the local plugin keeps holding the port - the first two
both answer "healthy" and the third answers "dead", so the same tunnel
showed green in the hud, red in status, and got closed a minute later.

Connecting is the only check that reflects whether the tunnel carries
traffic, so everything asks this.
"""

import socket

TIMEOUT = 2.0


def port_answers(port, timeout=TIMEOUT):
    """True when something accepts a TCP connection on 127.0.0.1:port.

    A refused connection on loopback comes back immediately, so the timeout
    only costs anything in the rare case of a port that accepts the SYN and
    then stalls.
    """
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
