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


HINT = "(↑/↓ or j/k move, enter select, q cancel)"
PAGE_SIZE = 10


def _window_start(index, page_size, total):
    """Clamp the visible window so `index` is always on screen."""
    if total <= page_size:
        return 0
    start = min(index, total - page_size)
    return max(0, start if index >= start else index)


def _draw(title, options, index, out, use_color, first, page_size):
    total = len(options)
    slots = min(page_size, total)
    start = _window_start(index, page_size, total)

    hint = HINT
    if total > page_size:
        hint = f"{HINT} — {start + 1}-{start + slots} of {total}"

    if not first:
        out.write(f"\033[{slots + 2}A")  # back up over title + hint + option slots
    out.write("\033[2K" + title + "\r\n")
    out.write("\033[2K" + hint + "\r\n")

    for i in range(start, start + slots):
        opt = options[i]
        marker = "> " if i == index else "  "
        if use_color and i == index:
            line = f"{marker}{BOLD_CYAN}{opt}{RESET}"
        elif use_color:
            line = f"{marker}{DIM}{opt}{RESET}"
        else:
            line = f"{marker}{opt}"
        out.write("\033[2K" + line + "\r\n")
    out.flush()
    return slots


def _clear(out, lines):
    """Erase a just-drawn menu block (title + hint + option slots) in place."""
    out.write(f"\033[{lines}A")
    for _ in range(lines):
        out.write("\033[2K\r\n")
    out.write(f"\033[{lines}A")
    out.flush()


def _numbered_fallback(title, options, out):
    out.write(f"{title}\n(number, blank to cancel)\n")
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


def menu(title, options, read_key=None, out=None, page_size=PAGE_SIZE):
    """Show an arrow-key picker. Returns the chosen option, or None if
    cancelled (q / Ctrl-C / an out-of-range fallback number).

    Redraws in place - the block (title, hint, visible options) is erased
    before the caller's next output, so chained menu() calls replace each
    other on screen instead of stacking. Lists longer than `page_size` show
    a scrolling window with a "start-end of total" indicator in the hint.

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
    slots = min(page_size, len(options))
    try:
        while True:
            slots = _draw(title, options, index, out, use_color, first, page_size)
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
        _clear(out, slots + 2)
