"""Generate the Fcitx5 candidate window's theme from the active Omarchy theme.

The candidate window is the one piece of desktop chrome this plugin draws, and
for a long time it drew someone else's: a fixed third-party colour scheme that
looked like a different product the moment the user picked an Omarchy theme.
This module removes that fixed scheme. The window's colours are the theme's
colours and its corners are Hyprland's corners, so it belongs to the desktop
rather than to whoever wrote the theme we used to vendor.

Everything that turns a palette into files is pure: `render` takes a palette and
a radius and returns text. The reading of real paths lives in `read_palette`,
`read_name`, `read_radius` and the `resolve` that ties them together, so the
tests never need an Omarchy install or a running compositor.

Two things the format forces, both learned from Fcitx5's classicui rather than
guessed:

Rounding is not a colour key. A flat `Color=` fills a rectangle, corners and
all; the only rounded surface classicui can draw is a nine-slice `Image=`. So
the radius is drawn into panel.svg and highlight.svg, and the `Margin` block
underneath each background tells classicui where to cut them.

The cut has to clear the arc. classicui slices the image at `Margin` and
stretches only what is left in the middle (`paintTile` in src/ui/classic). A cut
that lands exactly on the arc's tangent point smears a half-transparent pixel
across the entire edge, and a cut narrower than the arc slices the corner off.
`SLICE_PAD` is what keeps the cut strictly inside the straight part of the edge.

An `Image` replaces `Color`; it does not sit on top of it. classicui's
ThemeImage builds one surface per background: when `Image` names a file that
loads it is that file, and only when there is no such surface does it create a
blank one and fill it with `Color` and the border (checked by disassembling
`Theme::loadBackground` in libclassicui.so - the colour fill is inside the
`if (!image_)` branch, which is also what the shipped option help means by
"This option is only effective if image is not set"). Two things follow. A
partially transparent image shows the desktop through it, never the theme
colour. And `Color` is still worth writing beside an image, because it is
exactly what the window falls back to if that file is moved or deleted.

The border is in that same branch. `BackgroundImageConfig` does carry border
options - the real names are `BorderColor` (default `#ffffff00`, fully
transparent) and `BorderWidth` (default 0), read out of libclassicui.so beside
`Image`, `Color` and `Margin`. But the disassembly of `Theme::loadBackground`
shows how they are drawn: inside the same `if (!image_)` blank-surface branch,
as `cairoSetSourceColor(borderColor); cairo_paint()` over the whole surface,
followed by a clip to `(w, h)` inset by the border width and a second paint in
`Color`. A border painted under the fill of a surface that is only created when
no image loads is a border no theme of ours will ever show, because this theme
always names an image. So the window's edge is drawn into panel.svg the same way
its rounding is, and `BorderColor` / `BorderWidth` are written beside `Color`
for the same reason `Color` is: they are the fallback, not the drawing.

A nine-slice keeps that edge. classicui stretches only the middle of each strip,
so the hairline in the top strip stretches along the window's width while
staying one pixel tall - which is the whole reason a border can live in the
image at all.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from lib.settings import BACKGROUND_IMAGE_KEYS, usable_image_path

# The directory the theme is installed into, and therefore the name Fcitx5 is
# told to use. Deliberately the plugin's own name and not the Omarchy theme's:
# a directory named after the theme would be a new directory on every switch,
# and every previous one would be left behind for the user to find.
DIRECTORY = "omarchy-ime"

# What is used when there is no Omarchy theme to read. Neutral greys, no
# borrowed palette: a light accent so the selected candidate reads as selected,
# and a foreground/background pair at about 14:1 so nothing is a guess.
FALLBACK_PALETTE = {
    "mode": "dark",
    "accent": "#a8a8a8",
    "selection": "#333333",
    "muted": "#9a9a9a",
    "background": "#1c1c1c",
    "dark_background": "#121212",
    "lighter_background": "#282828",
    "foreground": "#e8e8e8",
}

# Square corners. A machine with no Hyprland to ask - a headless session, a
# different compositor - still has to get a deployable theme, and square is the
# one answer that is never wrong-looking, only plain.
FALLBACK_RADIUS = 0

# How far past the arc the nine-slice cut is made, in pixels. One would do; two
# leaves room for the rasteriser's antialiasing, and is what the hand-written
# themes that ship with rounded corners use.
SLICE_PAD = 2

# The stretched middle of the nine-slice. A single pixel is the textbook
# nine-patch, but it is also a single pixel being resampled across the width of
# the whole panel; a few pixels of the same flat colour costs nothing and gives
# the filter something to work with.
CENTER = 8

# The smallest gap between the panel's edge and the highlight inside it. The
# gap itself is derived rather than chosen (see `content_inset`); this is only
# the floor, for a square theme where the two radii are both zero and their
# difference would leave the first candidate touching the window's edge. It is
# what the inset used to be for every theme.
MIN_CONTENT_INSET = 6

# How far the window's own surface is nudged off the page colour when the
# palette carries no distinct rung to nudge to. A tenth of the way to white or
# black is the smallest step that still reads as a separate surface once the
# border below is drawn around it.
SURFACE_LIFT = 0.1

# How much of the foreground is mixed into the surface to draw the window's
# edge. Enough to be a visible hairline against the fill it encloses, not so
# much that a popup gains an outline louder than its own text.
BORDER_BLEND = 0.35

# The window's edge, in pixels. One: it is a separation, not a frame.
BORDER_WIDTH = 1

# Padding around a candidate's text, which is also the padding inside the
# highlight, since the highlight is drawn around the text plus this margin.
TEXT_MARGIN = (12, 12, 6, 6)
MENU_TEXT_MARGIN = (8, 8, 4, 4)

# How much of the accent is mixed back into the annotation shown on the selected
# candidate. The gloss is a supporting detail even when its row is the chosen
# one, so it is pulled towards the fill it sits on rather than left at full
# strength beside the candidate itself.
COMMENT_BLEND = 0.35

# Role -> the palette keys to try, in order. Omarchy themes are not obliged to
# carry every key, and a theme that omits one must still produce a window rather
# than an exception. Whatever survives the chain falls back to FALLBACK_PALETTE.
ROLE_SOURCES = {
    "accent": ("accent", "foreground"),
    "background": ("background",),
    "dark_background": ("dark_background", "background"),
    "lighter_background": ("lighter_background", "background"),
    "foreground": ("foreground",),
    "muted": ("muted", "foreground"),
    "selection": ("selection", "lighter_background", "background"),
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# The two light/dark values an Omarchy theme's colors.toml may declare as
# `mode`. Every theme installed on this machine declares one.
MODES = ("dark", "light")


# --------------------------------------------------------------------- colour


def _normalise(value) -> str | None:
    """A palette entry as `#rrggbb`, or None when it is not a colour at all."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _HEX.match(text):
        return None
    digits = text[1:].lower()
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return "#" + digits


def _channels(color: str) -> tuple[int, int, int]:
    digits = color[1:]
    return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))


def _luminance(color: str) -> float:
    """WCAG relative luminance."""
    total = 0.0
    for value, weight in zip(_channels(color), (0.2126, 0.7152, 0.0722)):
        channel = value / 255.0
        linear = (
            channel / 12.92 if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )
        total += linear * weight
    return total


def contrast(one: str, other: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    first, second = _luminance(one), _luminance(other)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _readable_on(background: str, options) -> str:
    """The candidate colour that is easiest to read on `background`.

    Which of a theme's colours reads on its accent is not something a fixed
    mapping can know: a pale coral accent wants near-black text and a deep blue
    one wants near-white, and both are ordinary Omarchy themes. So it is
    measured rather than assumed.
    """
    return max(options, key=lambda option: contrast(background, option))


def _mix(color: str, towards: str, amount: float) -> str:
    start, end = _channels(color), _channels(towards)
    return "#" + "".join(
        f"{int(a + (b - a) * amount + 0.5):02x}" for a, b in zip(start, end)
    )


def roles(palette: dict) -> dict:
    """Resolve an Omarchy palette into the roles this theme draws with."""
    resolved_roles = {}
    for role, keys in ROLE_SOURCES.items():
        found = None
        for key in keys:
            found = _normalise(palette.get(key))
            if found is not None:
                break
        resolved_roles[role] = found or _normalise(FALLBACK_PALETTE[role])
    return resolved_roles


def surface(palette: dict) -> str:
    """The candidate window's own fill: a rung off the page, never the page.

    The window used to be painted in `background`, on the reasoning that
    Omarchy's own popups use `background` too. That was true and it was half the
    picture: Omarchy's popups also carry `Color.popups.border` and sit over a
    dimmed layer, so they read as surfaces floating above the page. Copying the
    fill without the separation produced a window the same colour as the
    terminal underneath it - byte-identical, edge invisible.

    So the surface is picked off the palette's own ladder by mode, in the
    direction that reads as "above": lighter than the page under a dark theme,
    darker than it under a light one. `roles` already carries the fallback chain
    for a palette missing a rung, and that chain ends at `background` - which is
    exactly the colour this must not be, so a palette with no rung to take gets
    one derived instead.
    """
    role = roles(palette)
    page = role["background"]
    if mode(palette) == "dark":
        chosen, towards = role["lighter_background"], "#ffffff"
    else:
        chosen, towards = role["dark_background"], "#000000"
    if chosen != page:
        return chosen
    lifted = _mix(page, towards, SURFACE_LIFT)
    if lifted != page:
        return lifted
    # A page colour that is already pure white or pure black cannot be nudged
    # further that way. Step the other way rather than hand back the one colour
    # this function exists to differ from.
    away = "#000000" if towards == "#ffffff" else "#ffffff"
    return _mix(page, away, SURFACE_LIFT)


def surface_border(palette: dict) -> str:
    """The hairline that keeps the window's edge legible over anything.

    A popup floats over whatever the user happens to be looking at - a photo, a
    video, a light web page - and no fill can be relied on to differ from all of
    them. The edge is what makes the window a window. Mixed from the surface
    towards the theme's own foreground, so it is a palette colour in both modes
    rather than a constant that happens to suit one of them.
    """
    return _mix(surface(palette), roles(palette)["foreground"], BORDER_BLEND)


def colors(palette: dict) -> dict:
    """The whole mapping, as `theme.conf` key -> colour.

    Each entry is the Omarchy role that means the same thing on screen:

    text            an unselected candidate is body text            foreground
    panel           the window floats above the page, so off it     see surface
    panel_border    the edge that survives an arbitrary backdrop    see above
    highlight       the selected candidate is the one affordance    accent
    on_highlight    measured against the accent, not assumed        see above
    preedit         what is being typed, marked as live             accent
    preedit_back    the backdrop behind it is a selection           selection
    comment         the gloss is supporting text                    muted
    separator       a hairline, not a surface                       muted
    """
    role = roles(palette)
    on_highlight = _readable_on(
        role["accent"],
        (role["dark_background"], role["background"], role["foreground"]),
    )
    return {
        "text": role["foreground"],
        "panel": surface(palette),
        "panel_border": surface_border(palette),
        "highlight": role["accent"],
        "on_highlight": on_highlight,
        "preedit": role["accent"],
        "preedit_background": role["selection"],
        "comment": role["muted"],
        "highlight_comment": _mix(on_highlight, role["accent"], COMMENT_BLEND),
        "separator": role["muted"],
    }


# ----------------------------------------------------------------- background


def mode(palette: dict) -> str:
    """Whether this theme is a light one or a dark one, as it says itself.

    Every Omarchy theme's colors.toml carries `mode = "dark"` or
    `mode = "light"`, and `read_palette` keeps it because it keeps every string
    key. A theme that somehow does not say is treated as dark, which is where
    the fallback palette already lives, so an unlabelled theme is not also an
    unrecognisable one.
    """
    declared = palette.get("mode")
    if isinstance(declared, str):
        cleaned = declared.strip().lower()
        if cleaned in MODES:
            return cleaned
    return FALLBACK_PALETTE["mode"]


def background_for(palette: dict, settings: dict | None) -> str:
    """The user's background image for this theme's mode, or "" for none.

    Two images rather than one, chosen by the theme's own mode, is what makes a
    personal background safe here: an image picked against a dark theme becomes
    unreadable the moment the user switches to a light one, and the generated
    theme exists to follow that switch rather than to survive it.

    The file is not opened. lib/settings.validate refuses a path that cannot be
    opened at the moment it is written, and a file that disappears afterwards
    costs a background and nothing else - classicui falls back to `Color` for an
    image it cannot load.

    The path's *shape* is checked, because this function is not always handed
    settings that went through `validate`: `resolve` reads a hand-edited
    shell.json, and this value is written verbatim into an INI file. A path that
    is relative, or that carries a newline, is answered as no image at all - the
    same answer `resolve` gives it, asked again here so that neither of the two
    entry points into rendering can be reached around the other.
    """
    if not settings:
        return ""
    chosen = settings.get(BACKGROUND_IMAGE_KEYS[mode(palette)], "")
    if not isinstance(chosen, str) or not usable_image_path(chosen):
        return ""
    return chosen


# ------------------------------------------------------------------- geometry


def panel_radius(radius: int) -> int:
    """The window's own rounding: Hyprland's, unchanged."""
    return max(0, int(radius))


def highlight_radius(radius: int) -> int:
    """The rounding of the selected candidate inside the window.

    Omarchy's own kit nests shapes with `Math.max(2, Style.cornerRadius / 2)`
    (Ui/MultiSelect.qml), and the same reasoning applies here for a second
    reason: a candidate row is only a couple of dozen pixels tall, and a radius
    larger than half its height cannot be drawn as a rounded rectangle at all -
    the nine-slice corners would overlap and pinch the edge.
    """
    outer = panel_radius(radius)
    return 0 if outer == 0 else max(2, outer // 2)


def content_inset(radius: int) -> int:
    """The gap between the panel's edge and the highlight inside it.

    Concentric rather than merely nested, which is a measurement and not a
    preference: two rounded rectangles share a centre of curvature exactly when
    the gap between them equals the difference of their radii. The panel is
    `panel_radius` and the highlight is `highlight_radius`, so the inset follows
    from those two rather than being picked.

    A fixed 6 was wrong for every rounded theme and wrong by more the rounder it
    got: at Hyprland's default 17 the answer is 17 - 8 = 9, so the highlighted
    first candidate was crowded three pixels into the window's top corner, and a
    user who raised `decoration:rounding` made it worse rather than better.
    """
    return max(MIN_CONTENT_INSET, panel_radius(radius) - highlight_radius(radius))


def slice_for(radius: int) -> int:
    """Where classicui should cut the nine-slice for a given corner radius."""
    return max(0, int(radius)) + SLICE_PAD


def _number(value: float) -> str:
    """A length as SVG wants it: no trailing `.0` on a whole number."""
    return f"{value:g}"


def _svg(radius: int, color: str, border: str = "", border_width: int = 0) -> str:
    """A rounded rectangle sized so that `slice_for(radius)` cuts it correctly.

    The optional border is a second, inset rectangle rather than a stroke on the
    first: the fill rectangle keeps the exact radius and the exact bounds the
    nine-slice geometry is measured against, and the edge is drawn inside them.
    """
    edge = slice_for(radius)
    size = edge * 2 + CENTER
    shapes = [
        f'  <rect x="0" y="0" width="{size}" height="{size}"'
        f' rx="{radius}" ry="{radius}" fill="{color}"/>'
    ]
    if border and border_width > 0:
        # Half a stroke width in, so the whole hairline lands inside the shape
        # instead of straddling its edge and losing half of itself to the crop.
        inset = border_width / 2
        inner = max(0.0, radius - inset)
        shapes.append(
            f'  <rect x="{_number(inset)}" y="{_number(inset)}"'
            f' width="{_number(size - border_width)}"'
            f' height="{_number(size - border_width)}"'
            f' rx="{_number(inner)}" ry="{_number(inner)}" fill="none"'
            f' stroke="{border}" stroke-width="{_number(border_width)}"/>'
        )
    body = "\n".join(shapes)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1"'
        f' width="{size}" height="{size}" viewBox="0 0 {size} {size}">\n'
        f"{body}\n"
        f"</svg>\n"
    )


def _radio_svg(color: str) -> str:
    """The dot beside a checked menu entry, at the size classicui expects."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"'
        ' width="24" height="24" viewBox="0 0 24 24">\n'
        f'  <circle cx="12" cy="12" r="4" fill="{color}"/>\n'
        "</svg>\n"
    )


def _arrow_svg(color: str) -> str:
    """The triangle marking a submenu."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"'
        ' width="6" height="12" viewBox="0 0 6 12">\n'
        f'  <path d="M0 0 L6 6 L0 12 Z" fill="{color}"/>\n'
        "</svg>\n"
    )


# --------------------------------------------------------------------- render


def _margin(value: int) -> list[tuple[str, int]]:
    return [("Left", value), ("Right", value), ("Top", value), ("Bottom", value)]


def _sides(values) -> list[tuple[str, int]]:
    return list(zip(("Left", "Right", "Top", "Bottom"), values))


# Anything that would end a line or a value in the file being written. Two of
# the values below come off disk rather than out of this module - the Omarchy
# theme's own name, and the path the user put in `backgroundImage*` - and
# theme.conf is an INI file classicui parses one line at a time. A newline in a
# value is the next key; a newline and a `[` is the next section. Every value
# goes through `_ini_value` rather than only those two, so that a key added to
# this file later inherits the escaping instead of having to remember it.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _ini_value(value) -> str:
    """One value as it may appear in the generated theme: on a single line.

    Replaced with a space rather than dropped, so that what the user sees in the
    file still resembles what they put in the setting, and so that a mangled
    name is visibly mangled instead of silently shortened.
    """
    return _CONTROL.sub(" ", str(value))


def _ini(sections) -> str:
    lines = [
        "# This file is generated by io.github.eastgold15.omarchy.ime from the active Omarchy theme.",
        "# Edits are lost on the next deploy; change the Omarchy theme instead.",
    ]
    for name, entries in sections:
        lines.append("")
        lines.append(f"[{name}]")
        lines.extend(f"{key}={_ini_value(value)}" for key, value in entries)
    return "\n".join(lines) + "\n"


def render_theme_conf(palette: dict, radius: int, source: str = "",
                      background: str = "") -> str:
    """The classicui theme description, as text.

    `background` is the user's own image for this theme's mode, or "" for none.
    It is named by absolute path and takes the place of the generated panel.svg,
    because an `Image` is an alternative to the generated background rather than
    a layer over it (see the module docstring). Only the candidate window gets
    it: Fcitx5's right-click menu is a different window, and dressing it in
    someone's photo was not what was asked for.
    """
    color = colors(palette)
    outer, inner = panel_radius(radius), highlight_radius(radius)
    panel_slice, highlight_slice = slice_for(outer), slice_for(inner)
    named = f"Omarchy ({source})" if source else "Omarchy"
    panel_image = background or "panel.svg"

    return _ini([
        ("Metadata", [
            ("Name", named),
            ("Version", "1"),
            ("Author", "io.github.eastgold15.omarchy.ime"),
            ("Description",
             "Generated from the active Omarchy theme. Do not edit by hand."),
            ("ScaleWithDPI", "True"),
        ]),
        ("InputPanel", [
            ("Spacing", 3),
            ("NormalColor", color["text"]),
            ("HighlightCandidateColor", color["on_highlight"]),
            ("HighlightCandidateLabelColor", color["on_highlight"]),
            ("HighlightCandidateCommentColor", color["highlight_comment"]),
            ("CandidateCommentColor", color["comment"]),
            ("HighlightColor", color["preedit"]),
            ("HighlightBackgroundColor", color["preedit_background"]),
        ]),
        ("InputPanel/ContentMargin", _margin(content_inset(radius))),
        ("InputPanel/TextMargin", _sides(TEXT_MARGIN)),
        ("InputPanel/Background", [
            # Color first and always: it is dead while the image loads, and it
            # is the whole background the moment the image does not. The two
            # border keys are the same kind of insurance - classicui only paints
            # them on the blank surface it builds when no image loads, so the
            # edge that is actually seen is the one drawn into panel.svg.
            ("Color", color["panel"]),
            ("BorderColor", color["panel_border"]),
            ("BorderWidth", BORDER_WIDTH),
            ("Image", panel_image),
        ]),
        ("InputPanel/Background/Margin", _margin(panel_slice)),
        ("InputPanel/Highlight", [
            ("Color", color["highlight"]),
            ("Image", "highlight.svg"),
        ]),
        ("InputPanel/Highlight/Margin", _margin(highlight_slice)),
        ("Menu", [
            ("Spacing", 3),
            ("NormalColor", color["text"]),
            ("HighlightCandidateColor", color["on_highlight"]),
        ]),
        # The menu is the same kind of thing as the candidate window: a popup
        # floating over whatever the user is looking at, with the same outer
        # radius and the same inner one. Both the surface and the inset follow
        # from that, so it takes the same answer rather than one of its own.
        # Only its text margins differ, because it is a denser list.
        ("Menu/Background", [
            ("Color", color["panel"]),
            ("BorderColor", color["panel_border"]),
            ("BorderWidth", BORDER_WIDTH),
            ("Image", "panel.svg"),
        ]),
        ("Menu/Background/Margin", _margin(panel_slice)),
        ("Menu/ContentMargin", _margin(content_inset(radius))),
        ("Menu/Highlight", [
            ("Color", color["highlight"]),
            ("Image", "highlight.svg"),
        ]),
        ("Menu/Highlight/Margin", _margin(highlight_slice)),
        ("Menu/Separator", [("Color", color["separator"])]),
        ("Menu/CheckBox", [("Image", "radio.svg")]),
        ("Menu/SubMenu", [("Image", "arrow.svg")]),
        ("Menu/TextMargin", _sides(MENU_TEXT_MARGIN)),
    ])


def render(palette: dict, radius: int, source: str = "",
           background: str = "") -> dict[str, str]:
    """The whole theme directory, as filename -> text. Pure.

    The user's background image is never one of these files. Fcitx5 opens an
    absolute `Image` path directly - `themes/<name>/<image>` is built with
    std::filesystem's `/`, which discards everything to the left of an absolute
    right-hand side, and StandardPaths::open then opens it as it stands - so the
    file stays where the user put it. Copying it in would be a second copy to
    keep in sync, a second entry in the manifest, and a silent divergence the
    first time the user edited their own file.
    """
    color = colors(palette)
    return {
        "theme.conf": render_theme_conf(palette, radius, source, background),
        "panel.svg": _svg(panel_radius(radius), color["panel"],
                          color["panel_border"], BORDER_WIDTH),
        "highlight.svg": _svg(highlight_radius(radius), color["highlight"]),
        "radio.svg": _radio_svg(color["highlight"]),
        "arrow.svg": _arrow_svg(color["text"]),
    }


# --------------------------------------------------------------------- loaders


def omarchy_current() -> Path:
    """Where Omarchy points at the theme that is switched on right now."""
    root = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(root) / "omarchy" / "current"


def default_colors_path() -> Path:
    return omarchy_current() / "theme" / "colors.toml"


def default_name_path() -> Path:
    return omarchy_current() / "theme.name"


def read_palette(path) -> dict | None:
    """The theme's colours as written, or None when there is nothing to read.

    Omarchy writes a flat table of `key = "#rrggbb"`. Nothing here requires any
    particular key to be present; `roles` decides what to do about the gaps.
    """
    try:
        import tomllib

        with open(path, "rb") as handle:
            document = tomllib.load(handle)
    except Exception:
        return None
    palette = {
        key: value for key, value in document.items() if isinstance(value, str)
    }
    return palette or None


def read_name(path) -> str:
    """The active theme's name, for the metadata. Absence is not a failure."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_radius(runner=subprocess.run) -> int:
    """Hyprland's `decoration:rounding`, which Omarchy's Style.cornerRadius mirrors.

    `runner` is injected so no test ever shells out. A compositor that is not
    there is not an error: a headless session, or one that is not Hyprland at
    all, still has to end up with a theme it can deploy.
    """
    try:
        result = runner(
            ["hyprctl", "getoption", "decoration:rounding"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return FALLBACK_RADIUS
    if result.returncode != 0:
        return FALLBACK_RADIUS
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("int:"):
            try:
                return max(0, int(stripped.split(":", 1)[1].strip()))
            except ValueError:
                return FALLBACK_RADIUS
    return FALLBACK_RADIUS


def resolved(palette: dict, radius: int, source: str, fallback: bool = False,
             reason: str = "", background: str = "") -> dict:
    """Package a palette and a radius as the answer a deploy consumes."""
    return {
        "files": render(palette, radius, source, background),
        "source": source,
        "radius": panel_radius(radius),
        "mode": mode(palette),
        "background": background,
        "fallback": fallback,
        "reason": reason,
    }


def resolve(colors_path=None, name_path=None, runner=None,
            settings: dict | None = None) -> dict:
    """Read the machine's own theme, falling back rather than failing.

    A theme file that moved, or a colors.toml that a hand edit made unparsable,
    must cost the user a colour scheme and nothing else. Losing an input method
    over it would be absurd.

    `settings` is the plugin's resolved settings, and is passed in rather than
    read here so this module never learns where shell.json lives. Without it
    there is no background image, which is also what an install that has not
    asked for one gets.
    """
    source_path = Path(colors_path) if colors_path is not None else default_colors_path()
    named_path = Path(name_path) if name_path is not None else default_name_path()
    radius = read_radius(runner or subprocess.run)

    palette = read_palette(source_path)
    if palette is None:
        # The fallback palette declares its own mode, so an install with no
        # readable theme still gets whichever of the two images matches the
        # colours it is about to be drawn in.
        return resolved(
            FALLBACK_PALETTE, radius, "", fallback=True,
            reason=f"no readable Omarchy palette at {source_path}",
            background=background_for(FALLBACK_PALETTE, settings),
        )
    return resolved(palette, radius, read_name(named_path),
                    background=background_for(palette, settings))
