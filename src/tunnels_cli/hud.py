#!/usr/bin/env python3
"""Floating label showing the live tunnels. Draws over fullscreen apps.

Run with /usr/bin/python3: it is the interpreter that has pyobjc here.
System Tk is broken on macOS 26, so this uses a native borderless NSWindow
instead. The window joins every Space and is marked fullScreenAuxiliary, so
it stays visible while another app is fullscreen. It ignores mouse events,
so clicks pass through to whatever is underneath.

Reads ~/.tunnels/state.json every 2 seconds and quits when it is empty.
"""

import json
import math
import os
import zlib
from pathlib import Path

import objc
from Cocoa import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSAttributedString,
    NSFont,
    NSFontAttributeName,
    NSMakeRect,
    NSObject,
    NSScreen,
    NSTextField,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)

STATE_FILE = Path.home() / ".tunnels" / "state.json"
POLL_SECONDS = 2.0

FONT_SIZE = 11.0
LINE = 14
PAD = 6
MARGIN = 16          # gap from the screen edges

MARK = 20            # the logo, drawn at this many points square
MARK_GAP = 8         # space between the logo and the tunnel rows

DEAD = (0.55, 0.57, 0.60)       # a dropped session: grey, not shouting

# The logo: four arcs receding, outermost first. Radii and widths are the
# same ratios as assets/logo.svg, expressed as a fraction of MARK so the
# mark keeps its proportions at any size. The colours are the artwork's
# cool sweep lifted to L=0.70, because the artwork's own L=0.52 goes muddy
# on the panel's near-black background.
LOGO_ARCS = [
    (0.400, 0.100, (0.404, 0.637, 0.893)),
    (0.283, 0.079, (0.273, 0.668, 0.853)),
    (0.175, 0.063, (0.143, 0.693, 0.784)),
    (0.083, 0.050, (0.123, 0.708, 0.693)),
]

# One colour per tunnel, so two clusters in the same account still differ.
PALETTE = [
    (0.22, 0.72, 0.36),   # green
    (0.30, 0.60, 0.95),   # blue
    (0.95, 0.55, 0.20),   # orange
    (0.72, 0.45, 0.95),   # purple
    (0.25, 0.80, 0.78),   # teal
    (0.95, 0.40, 0.55),   # pink
]


def color_for(key):
    """A tunnel's preferred colour. Stable across restarts.

    Keyed on "config/target", not on the config alone: two clusters in one
    account are the common case and they must not look alike. crc32 rather
    than hash(), which is salted per process and would change every run.
    """
    return PALETTE[zlib.crc32(key.encode()) % len(PALETTE)]


def assign_colors(keys):
    """Give every visible tunnel a different colour where the palette allows.

    Each key keeps its preferred colour when it is free. Ties are broken in
    sorted order, so the result only changes when the set of tunnels changes.
    """
    taken = {}
    for key in sorted(keys):
        preferred = PALETTE.index(color_for(key))
        for step in range(len(PALETTE)):
            slot = (preferred + step) % len(PALETTE)
            if slot not in taken.values():
                taken[key] = slot
                break
        else:
            taken[key] = preferred      # more tunnels than colours, reuse
    return {key: PALETTE[slot] for key, slot in taken.items()}


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


def read_entries():
    try:
        with STATE_FILE.open() as handle:
            entries = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return entries if isinstance(entries, list) else []


def lines_for(entries):
    """One short line per tunnel: dot, config/target, port, account tail."""
    rows = []
    colors = assign_colors(e["key"] for e in entries)
    for entry in sorted(entries, key=lambda e: e["key"]):
        live = pid_alive(entry.get("pid"))
        account = str(entry.get("account", "?"))[-4:]
        text = (
            f"{'●' if live else '○'} {entry['config']}/{entry['target']} "
            f":{entry['local_port']} ·{account}"
        )
        rows.append((text, colors[entry["key"]] if live else DEAD))
    return rows


def row_font():
    return NSFont.monospacedSystemFontOfSize_weight_(FONT_SIZE, 0.3)


def text_width(texts, font):
    """Measure the widest row, rather than guessing a character advance.

    The rows carry the dot and separator glyphs, which do not advance at a
    plain monospaced width. Asking the font is exact and costs nothing at
    this size.
    """
    widest = 0.0
    for text in texts:
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font}
        )
        widest = max(widest, attributed.size().width)
    return math.ceil(widest)


def fit_panel(visible, width, height, margin=MARGIN):
    """Measure first, then apply: size the panel to the screen it lands on.

    Returns the panel rect, capped so it always fits between the margins of
    that screen whatever the rows contain, and never wider or taller than
    the screen itself.
    """
    max_width = max(1, int(visible["width"]) - margin * 2)
    max_height = max(1, int(visible["height"]) - margin * 2)
    return min(width, max_width), min(height, max_height)


def top_right_origin(visible, width, height, margin=MARGIN):
    """Where a panel of this size sits in the screen's top right corner.

    Screen coordinates are GLOBAL, not per screen: a display to the left of
    the built-in one starts at a negative x. Positioning from the size alone
    computed a coordinate for the active screen and then applied it on the
    primary one, which put the panel over the right edge of the laptop.

    The result is clamped into the screen so a panel can never hang off an
    edge, whatever its size. visibleFrame already excludes the menu bar and
    the Dock, so there is no fudge factor for either.
    """
    left, bottom = visible["x"], visible["y"]
    right = left + visible["width"]
    top = bottom + visible["height"]

    x = min(right - width - margin, right - width)
    y = min(top - height - margin, top - height)
    return max(x, left), max(y, bottom)


def make_field(text, rgb, y, width):
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, y, width, LINE))
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setAlignment_(2)  # NSTextAlignmentRight
    field.setFont_(row_font())
    field.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, 1.0))
    return field


class LogoView(NSView):
    """The mark, stroked with NSBezierPath rather than loaded from a file.

    Drawing it keeps the package free of a binary asset and stays sharp on
    any display scale. The arcs are concentric half circles sharing a centre
    on the mark's horizontal axis.
    """

    def drawRect_(self, _rect):
        size = self.bounds().size
        span = min(size.width, size.height)
        cx = size.width / 2.0
        cy = size.height / 2.0 - span * 0.20   # the arcs sit above the centre

        for radius, width, rgb in LOGO_ARCS:
            path = NSBezierPath.bezierPath()
            path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
                (cx, cy), span * radius, 0.0, 180.0,
            )
            path.setLineWidth_(span * width)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, 1.0).set()
            path.stroke()


class Hud(NSObject):
    def initWithWindow_(self, window):
        self = objc.super(Hud, self).init()
        self.window = window
        return self

    def tick_(self, _timer):
        entries = read_entries()
        if not entries:
            NSApplication.sharedApplication().terminate_(None)
            return
        self.draw(entries)

    @objc.python_method
    def draw(self, entries):
        rows = lines_for(entries)

        # Measure the screen the panel will land on before sizing anything to
        # it. mainScreen follows the focused window, so this is re-read every
        # tick rather than assumed.
        frame_rect = NSScreen.mainScreen().visibleFrame()
        visible = {
            "x": frame_rect.origin.x, "y": frame_rect.origin.y,
            "width": frame_rect.size.width, "height": frame_rect.size.height,
        }

        # Then measure the content.
        font = row_font()
        width = text_width([text for text, _ in rows], font) + \
            MARK + MARK_GAP + PAD * 3
        height = max(PAD * 2 + LINE * len(rows), PAD * 2 + MARK)

        # Only then apply, capped and clamped to that screen.
        width, height = fit_panel(visible, width, height)
        x, y_origin = top_right_origin(visible, width, height)
        frame = NSMakeRect(x, y_origin, width, height)
        self.window.setFrame_display_(frame, True)

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(7.0)
        layer.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.06, 0.08, 0.10, 0.82).CGColor()
        )

        logo = LogoView.alloc().initWithFrame_(
            NSMakeRect(PAD, height - PAD - MARK, MARK, MARK)
        )
        content.addSubview_(logo)

        y = height - PAD
        for text, rgb in rows:
            y -= LINE
            content.addSubview_(make_field(text, rgb, y, width - PAD))

        self.window.setContentView_(content)
        self.window.orderFrontRegardless()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 200, 40), NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered, False,
    )
    window.setOpaque_(False)
    window.setBackgroundColor_(NSColor.clearColor())
    window.setHasShadow_(True)
    window.setIgnoresMouseEvents_(True)
    window.setLevel_(25)  # NSStatusWindowLevel: above normal and floating windows
    window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )

    hud = Hud.alloc().initWithWindow_(window)
    entries = read_entries()
    if not entries:
        return
    hud.draw(entries)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        POLL_SECONDS, hud, "tick:", None, True
    )
    app.run()


if __name__ == "__main__":
    main()
