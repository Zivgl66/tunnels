"""menu - a minimal arrow-key picker for the terminal, no dependencies."""

import sys

UP_KEYS = {"\x1b[A", "k"}
DOWN_KEYS = {"\x1b[B", "j"}
ENTER_KEYS = {"\r", "\n"}
CANCEL_KEYS = {"q", "\x03"}

BOLD_CYAN = "\033[1;36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _read_key(stream):
    """Read one keypress, decoding a 3-byte arrow-key escape sequence."""
    ch = stream.read(1)
    if ch == "\x1b":
        ch += stream.read(2)
    return ch


def _raw_mode(stream):
    import termios
    import tty

    fd = stream.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    return fd, old


def _restore_mode(fd, old):
    import termios

    termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _draw(title, options, index, out, use_color, first):
    if not first:
        out.write(f"\033[{len(options)}A")
    else:
        out.write(f"{title}\n")
    for i, opt in enumerate(options):
        marker = "> " if i == index else "  "
        if use_color and i == index:
            line = f"{marker}{BOLD_CYAN}{opt}{RESET}"
        elif use_color:
            line = f"{marker}{DIM}{opt}{RESET}"
        else:
            line = f"{marker}{opt}"
        out.write("\033[2K" + line + "\n")
    out.flush()


def _numbered_fallback(title, options, out):
    out.write(f"{title}\n")
    for i, opt in enumerate(options, 1):
        out.write(f"  {i}) {opt}\n")
    out.flush()
    try:
        choice = input("> ").strip()
    except EOFError:
        return None
    if not choice.isdigit() or not (1 <= int(choice) <= len(options)):
        return None
    return options[int(choice) - 1]


def menu(title, options, read_key=None, out=None):
    """Show an arrow-key picker. Returns the chosen option, or None if
    cancelled (q / Ctrl-C / an out-of-range fallback number).

    `read_key`, if given, is called with no arguments to get the next
    keypress instead of reading the real terminal - this is how tests drive
    the menu without a tty. Pass the string "fallback" to force the
    numbered-list fallback path (used for tty-less environments) instead of
    the arrow-key path.
    """
    if not options:
        raise ValueError("menu needs at least one option")

    out = out or sys.stdout
    testing = read_key is not None
    force_fallback = read_key == "fallback"

    if force_fallback or (not testing and not sys.stdin.isatty()):
        return _numbered_fallback(title, options, out)

    use_color = out.isatty()
    reader = read_key if testing else lambda: _read_key(sys.stdin)

    fd = old = None
    if not testing:
        fd, old = _raw_mode(sys.stdin)

    index = 0
    first = True
    try:
        while True:
            _draw(title, options, index, out, use_color, first)
            first = False
            key = reader()
            if key in CANCEL_KEYS:
                return None
            if key in ENTER_KEYS:
                return options[index]
            if key in UP_KEYS:
                index = (index - 1) % len(options)
            elif key in DOWN_KEYS:
                index = (index + 1) % len(options)
    finally:
        if fd is not None:
            _restore_mode(fd, old)
