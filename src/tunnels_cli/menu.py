"""menu - a minimal arrow-key picker for the terminal, no dependencies."""

import sys

from tunnels_cli import ui

UP_KEYS = {"\x1b[A", "k"}
DOWN_KEYS = {"\x1b[B", "j"}
ENTER_KEYS = {"\r", "\n"}
CANCEL_KEYS = {"q", "\x03"}
HOME_KEYS = {"g", "\x1b[H"}
END_KEYS = {"G", "\x1b[F"}
BACK_KEYS = {"\x1b[D", "h", "b"}

# Returned instead of a choice when the user asks to step back a level, so a
# caller can tell "go back" apart from "cancel the whole thing" (None).
BACK = "\x00back"

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


HINT = "↑/↓ or j/k move · g/G first/last · enter select · q cancel"
BACK_HINT = "↑/↓ or j/k move · enter select · ←/b/q back · ctrl-c quit"
PAGE_SIZE = 10


def _window_start(index, page_size, total):
    """Clamp the visible window so `index` is always on screen."""
    if total <= page_size:
        return 0
    start = min(index, total - page_size)
    return max(0, start if index >= start else index)


def _draw(title, options, index, out, use_color, first, page_size,
          allow_back=False):
    total = len(options)
    slots = min(page_size, total)
    start = _window_start(index, page_size, total)

    hint = BACK_HINT if allow_back else HINT
    if total > page_size:
        hint = f"{hint} — {start + 1}-{start + slots} of {total}"

    if not first:
        out.write(f"\033[{slots + 2}A")  # back up over title + hint + option slots
    out.write("\033[2K" + (f"{BOLD_CYAN}{title}{RESET}" if use_color else title)
              + "\r\n")
    out.write("\033[2K" + (f"{DIM}{hint}{RESET}" if use_color else hint) + "\r\n")

    pointer = ui.sym.arrow if ui.unicode_on() else ">"
    for i in range(start, start + slots):
        opt = options[i]
        selected = i == index
        marker = f"{pointer} " if selected else "  "
        if use_color and selected:
            line = f"{BOLD_CYAN}{marker}{opt}{RESET}"
        elif use_color:
            line = f"{DIM}{marker}{opt}{RESET}"
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


def _numbered_fallback(title, options, out, allow_back=False):
    tail = "b or blank to go back" if allow_back else "blank to cancel"
    out.write(f"{title}\n(number, {tail})\n")
    for i, opt in enumerate(options, 1):
        out.write(f"  {i}) {opt}\n")
    out.flush()
    try:
        choice = input("> ").strip()
    except EOFError:
        return None
    if allow_back and (not choice or choice.lower() == "b"):
        return BACK
    if not choice.isdigit() or not (1 <= int(choice) <= len(options)):
        return None
    return options[int(choice) - 1]


def menu(title, options, read_key=None, out=None, page_size=PAGE_SIZE,
         allow_back=False):
    """Show an arrow-key picker. Returns the chosen option, or None if
    cancelled (q / Ctrl-C / an out-of-range fallback number).

    With `allow_back`, left arrow / b / h / q return the BACK sentinel
    instead, so a wrong turn one level down costs one keypress rather than
    the whole run. Ctrl-C still quits outright at any level.

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
        return _numbered_fallback(title, options, out, allow_back)

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
            slots = _draw(title, options, index, out, use_color, first,
                          page_size, allow_back)
            first = False
            key = reader()
            if key == "\x03":                     # ctrl-c always quits outright
                return None
            if allow_back and (key in BACK_KEYS or key in CANCEL_KEYS):
                return BACK
            if key in CANCEL_KEYS:
                return None
            if key in ENTER_KEYS:
                return options[index]
            if key in UP_KEYS:
                index = (index - 1) % len(options)
            elif key in DOWN_KEYS:
                index = (index + 1) % len(options)
            elif key in HOME_KEYS:
                index = 0
            elif key in END_KEYS:
                index = len(options) - 1
    finally:
        if fd is not None:
            _restore_mode(fd, old)
        _clear(out, slots + 2)
