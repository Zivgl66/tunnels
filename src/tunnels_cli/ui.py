"""ui - colors, banner and small drawing helpers for the terminal.

No dependencies: everything here is ANSI escapes and str formatting, the
same approach menu.py already takes. Colour is off automatically when the
output is not a terminal, when NO_COLOR is set, or on a dumb TERM, so piping
`tunnels status` into grep still gives plain text.
"""

import itertools
import os
import shutil
import sys
import threading
import time

RESET = "\033[0m"
STYLES = {
    "bold": "1", "dim": "2", "italic": "3", "underline": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "grey": "90",
    "bright_cyan": "96", "bright_green": "92", "bright_magenta": "95",
}

# Colour and unicode are decided once per process, from the real stdout.
_COLOR = None
_UNICODE = None


def supports_color(stream=None):
    """Colour when a human is watching, and never when NO_COLOR is set."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def supports_unicode(stream=None):
    stream = stream or sys.stdout
    encoding = (getattr(stream, "encoding", "") or "").lower()
    return "utf" in encoding


def set_color(enabled):
    """Force colour on or off, for --no-color and for tests."""
    global _COLOR
    _COLOR = enabled


def color_on():
    global _COLOR
    if _COLOR is None:
        _COLOR = supports_color()
    return _COLOR


def unicode_on():
    global _UNICODE
    if _UNICODE is None:
        _UNICODE = supports_unicode()
    return _UNICODE


def paint(text, *styles):
    """Wrap text in ANSI styles, or return it untouched when colour is off."""
    if not styles or not color_on():
        return text
    codes = ";".join(STYLES[s] for s in styles if s in STYLES)
    return f"\033[{codes}m{text}{RESET}" if codes else text


class _Symbols:
    """Unicode when the terminal can show it, ASCII when it cannot."""

    FANCY = {"ok": "✔", "bad": "✘", "warn": "▲", "dot": "·",
             "arrow": "→", "bullet": "•", "line": "─",
             "up": "●", "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"}
    PLAIN = {"ok": "+", "bad": "x", "warn": "!", "dot": "-", "arrow": "->",
             "bullet": "*", "line": "-", "up": "o", "spin": "|/-\\"}

    def __getattr__(self, name):
        table = self.FANCY if unicode_on() else self.PLAIN
        try:
            return table[name]
        except KeyError:
            raise AttributeError(name)


sym = _Symbols()

BANNER = r"""
 _                            _
| |_ _   _ _ __  _ __   ___  | |___
| __| | | | '_ \| '_ \ / _ \ | / __|
| |_| |_| | | | | | | |  __/ | \__ \
 \__|\__,_|_| |_|_| |_|\___| |_|___/
"""

BANNER_COLORS = ("cyan", "cyan", "bright_cyan", "blue", "blue")

# The four nested arches of assets/logo.svg, one digit per ring, drawn from
# the same radii. Colour tells the rings apart when the terminal has it;
# without colour each ring falls back to its own shade block.
LOGO = (
    "     00000000000000000",
    "    0000  1111111  0000",
    "  0000 11111   11111 0000",
    " 0000 111 2222222 111 0000",
    " 000 11 222 333 222 11 000",
    " 000 11 222 333 222 11 000",
)
RING_COLORS = ("blue", "cyan", "bright_cyan", "green")
RING_SHADES = ("\u2588", "\u2593", "\u2592", "\u2591")


def logo():
    """The tunnel mouth, one colour per ring."""
    out = []
    for row in LOGO:
        line = ""
        for cell in row:
            if cell == " ":
                line += " "
                continue
            ring = int(cell)
            block = "\u2588" if color_on() else RING_SHADES[ring]
            line += paint(block, RING_COLORS[ring])
        out.append(line)
    return out


def banner(subtitle=""):
    """The logo beside the block letters, or just the letters when narrow."""
    letters = [paint(line, style, "bold")
               for line, style in zip(BANNER.strip("\n").splitlines(),
                                      BANNER_COLORS)]
    art_width = max((len(row) for row in LOGO), default=0)
    side_by_side = unicode_on() and width() >= art_width + 40

    if side_by_side:
        art = logo()
        # bottom-align the two blocks: the logo is one row taller
        letters = [""] * (len(art) - len(letters)) + letters
        rows = [f"{a}{' ' * (art_width - len(LOGO[i]))}   {t}"
                for i, (a, t) in enumerate(zip(art, letters))]
        indent = " " * (art_width + 3)
    else:
        rows = letters
        indent = "  "

    if subtitle:
        rows.append(paint(f"{indent}{subtitle}", "grey"))
    return "\n".join(rows) + "\n"


def width(default=80):
    return shutil.get_terminal_size((default, 24)).columns


def rule(title="", char=None):
    """A horizontal line, with an optional label at the left."""
    char = char or sym.line
    total = min(width(), 72)
    if not title:
        return paint(char * total, "grey")
    label = f"{char}{char} {title} "
    return paint(label + char * max(0, total - len(label)), "grey")


# The spinner owns the current line while it runs. Printing helpers wipe that
# line first, so a warning raised mid-call cannot land on top of the frame.
_ACTIVE_SPINNER = None


def _clear_line():
    spinner = _ACTIVE_SPINNER
    if spinner is not None and spinner.active:
        spinner.stream.write("\r\033[2K")
        spinner.stream.flush()


def step(text):
    _clear_line()
    print(f"  {paint(sym.dot, 'grey')} {text}")


def ok(text):
    _clear_line()
    print(f"  {paint(sym.ok, 'green')} {text}")


def warn(text):
    _clear_line()
    print(f"  {paint(sym.warn, 'yellow')} {paint(text, 'yellow')}")


def fail(text, stream=None):
    _clear_line()
    print(f"{paint(sym.bad, 'red')} {paint(text, 'red')}",
          file=stream or sys.stderr)


def info(text):
    _clear_line()
    print(paint(text, "grey"))


def human_age(seconds):
    """43 -> '43s', 3700 -> '1h1m'. Short enough for a status column."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _visible_len(text):
    """Length without ANSI escapes, so coloured cells still line up."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            i = text.find("m", i) + 1 or len(text)
            continue
        out += 1
        i += 1
    return out


def table(headers, rows, indent="  "):
    """Aligned columns with a dim header rule. Cells may already be coloured."""
    if not rows:
        return ""
    columns = len(headers)
    widths = [max(_visible_len(str(r[i])) for r in rows) for i in range(columns)]
    widths = [max(w, len(str(headers[i]))) for i, w in enumerate(widths)]

    def line(cells, style=None):
        parts = []
        for i, cell in enumerate(cells):
            text = str(cell)
            pad = " " * (widths[i] - _visible_len(text))
            parts.append((paint(text, style) if style else text) + pad)
        return indent + "  ".join(parts).rstrip()

    out = [line(headers, "bold"),
           indent + paint("  ".join(sym.line * w for w in widths), "grey")]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


class Spinner:
    """A one-line 'working on it' marker for slow AWS calls.

    Silent when the output is not a terminal, so logs and pipes stay clean.
    Use as a context manager; the line is replaced by a tick on success.
    """

    def __init__(self, text, stream=None):
        self.text = text
        self.stream = stream or sys.stdout
        self.active = color_on() and getattr(self.stream, "isatty", lambda: False)()
        self._stop = threading.Event()
        self._thread = None

    def _spin(self):
        for frame in itertools.cycle(sym.spin):
            if self._stop.is_set():
                return
            self.stream.write(
                f"\r\033[2K  {paint(frame, 'cyan')} {paint(self.text, 'grey')}"
            )
            self.stream.flush()
            time.sleep(0.08)

    def __enter__(self):
        global _ACTIVE_SPINNER
        _ACTIVE_SPINNER = self
        if self.active:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            step(self.text)
        return self

    def __exit__(self, exc_type, exc, tb):
        global _ACTIVE_SPINNER
        _ACTIVE_SPINNER = None
        if self.active:
            self._stop.set()
            self._thread.join(timeout=0.5)
            self.stream.write("\r\033[2K")
            self.stream.flush()
        return False
