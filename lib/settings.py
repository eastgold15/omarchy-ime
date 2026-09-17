"""Plugin settings: defaults, validation, and where they are read from.

Omarchy stores a bar widget's settings as extra keys on the widget's own entry
in shell.json, so `load` looks for the entry whose id is ours and treats the
rest of that object as settings.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import stat
from pathlib import Path

PLUGIN_ID = "ryuhzk.ime"

# Every Rime schema the plugin can deploy, grouped into the families a person
# actually chooses between. The family is what makes a list of twenty
# scannable; the order here is the order the picker shows and the order the
# schemas end up in Rime's schema_list, so the first Chinese schema selected is
# the one Rime starts composing with.
#
# No display name is written here. A schema's readable name is the `name:` in
# its own schema yaml, and it is read from there at runtime (see `catalogue`).
# A name transliterated into this file would be a second answer to a question
# the schema already answers, and the wrong one as soon as upstream renames
# anything.
#
# Every id was checked against /usr/share/rime-data; the Japanese two are
# shipped in this plugin's own payload.
SCHEMA_FAMILIES = (
    ("全拼", (
        "rime_ice", "luna_pinyin", "luna_quanpin", "luna_pinyin_simp",
        "luna_pinyin_tw", "luna_pinyin_fluency",
    )),
    ("双拼", (
        "double_pinyin", "double_pinyin_flypy", "double_pinyin_mspy",
        "double_pinyin_sogou", "double_pinyin_jiajia", "double_pinyin_ziguang",
        "double_pinyin_abc",
    )),
    ("注音", ("bopomofo", "bopomofo_tw", "bopomofo_express")),
    ("形码", ("cangjie5", "cangjie5_express", "radical_pinyin")),
    ("其他", ("terra_pinyin", "terra_pinyin_12345")),
    ("日语", ("japanese_tw_eng", "sno_japanese")),
)

SCHEMA_IDS = tuple(
    schema_id for _family, ids in SCHEMA_FAMILIES for schema_id in ids
)
SCHEMA_FAMILY = {
    schema_id: family for family, ids in SCHEMA_FAMILIES for schema_id in ids
}

# melt_eng is deliberately not in the catalogue above, though it is a schema
# /usr/share/rime-data ships. English words are already reachable without
# leaving the Chinese schema - Rime Ice mounts melt_eng as a secondary
# table_translator inside it - so offering it here would tell the user something
# false: that typing English means switching schema. Anyone who wants nothing
# but English has ascii_mode, which is Rime's and Fcitx5's own answer to that.
#
# The schemas that compose no Chinese. A schema_list holding only these leaves a
# Chinese keyboard with no way to type Chinese, which is why one Chinese schema
# always survives.
NON_CHINESE_SCHEMAS = frozenset({"japanese_tw_eng", "sno_japanese"})
CHINESE_SCHEMAS = tuple(
    schema_id for schema_id in SCHEMA_IDS if schema_id not in NON_CHINESE_SCHEMAS
)

# What a fresh install composes with. One schema, so an upgrade from the version
# that hard-coded rime_ice changes nothing.
DEFAULT_SCHEMAS = ["rime_ice"]

# `japanese: true` was the old way to ask for the Japanese schemas, before they
# became two entries in the picker like any other. An install that still says it
# must not lose them on upgrade, so it is read once and translated (see
# `_migrated`). Nothing writes it any more.
LEGACY_JAPANESE_KEY = "japanese"
LEGACY_JAPANESE_SCHEMAS = ("japanese_tw_eng", "sno_japanese")

class ImagePath(str):
    """Marker type for a setting holding a filesystem path to an image.

    Its own type rather than plain `str` because an empty value means something
    here - no image - while every other string setting refuses one. Nothing is
    ever an instance of this; it is used only as the type tag in DEFAULTS.
    """


# The candidate window's background image, one per Omarchy theme mode. Two
# rather than one because a single fixed image is unreadable the moment the user
# switches from a dark theme to a light one, and the whole point of the
# generated theme is that it follows the desktop.
BACKGROUND_IMAGE_KEYS = {
    "light": "backgroundImageLight",
    "dark": "backgroundImageDark",
}

# What classicui can actually load, and how to recognise each one from its first
# bytes. classicui picks its loader from the extension - a name ending in .png
# goes to cairo's own PNG reader and everything else to gdk-pixbuf (verified in
# libclassicui.so: `path.extension() == ".png"` then
# `cairo_image_surface_create_from_png_stream`, else `gdk_pixbuf_new_from_stream`)
# - so a file whose contents disagree with its name is a file that will never
# load however good its bytes are, and both halves are checked.
#
# gdk-pixbuf on a given machine may well load more than this (avif, heic, jxl,
# tiff and others were present here), but what it loads depends on which loader
# modules are installed, and a setting that is accepted on one machine and
# silently ignored on the next is exactly what this validation exists to stop.
# These are the formats that are either built into cairo or come with any
# ordinary desktop: SVG in particular is what the generated theme already draws
# its own panel with, so it is known to work.
def _magic(*prefixes):
    return lambda head, path: any(head.startswith(prefix) for prefix in prefixes)


def _is_webp(head, path):
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _is_svg(head, path):
    return b"<svg" in head


def _is_svgz(head, path):
    if not head.startswith(b"\x1f\x8b"):
        return False
    try:
        with gzip.open(path, "rb") as handle:
            return b"<svg" in handle.read(8192)
    except OSError:
        return False


BACKGROUND_IMAGE_FORMATS = {
    ".png": ("PNG", _magic(b"\x89PNG\r\n\x1a\n")),
    ".svg": ("SVG", _is_svg),
    ".svgz": ("gzipped SVG", _is_svgz),
    ".jpg": ("JPEG", _magic(b"\xff\xd8\xff")),
    ".jpeg": ("JPEG", _magic(b"\xff\xd8\xff")),
    ".jpe": ("JPEG", _magic(b"\xff\xd8\xff")),
    ".webp": ("WebP", _is_webp),
    ".bmp": ("BMP", _magic(b"BM")),
    ".gif": ("GIF", _magic(b"GIF87a", b"GIF89a")),
}

BACKGROUND_IMAGE_EXTENSIONS = tuple(BACKGROUND_IMAGE_FORMATS)

# How much of a file is read to recognise it. Enough for an SVG's XML
# declaration, doctype and opening comment to come before the `<svg` element.
SNIFF_BYTES = 8192

# How wide the popup panel is, in the layout units Style.space multiplies by the
# theme's spacing scale. The default is measured from the content rather than
# picked: the widest row in the panel is a setting with its control pinned to
# the trailing edge, and the label beside it has to fit without eliding. 420
# leaves the longest of those labels about a tenth of its width in slack; the
# 460 it replaces was sized for labels that have since been shortened.
#
# The lower bound is not a round number chosen for looks: it is the narrowest
# panel at which every label in the settings groups still fits its column beside
# its control. Measured against JetBrainsMono Nerd Font, whose advance is
# 600/1000 em, the tightest of them - "Light theme background" beside a 150-unit
# path field - has about 8px of slack at 400 and none at all below it. The upper
# bound is where a popup stops reading as a popup.
DEFAULT_PANEL_WIDTH = 420
PANEL_WIDTH_BOUNDS = (400, 720)

# value, type, and for integers the accepted range
DEFAULTS = {
    "schemas": (DEFAULT_SCHEMAS, list, None),
    # The master switch over candidate annotation: the Lua filter, the
    # CC-CEDICT download, and the patch every Chinese schema carries. Off by
    # default - the base install is Rime Ice with nothing extra, and glosses
    # are an add-on the user asks for. The four `annotate*` settings below keep
    # their values while it is off. To flip the default, change this line and
    # the two matching values in manifest.json; tests/test_manifest.py holds
    # the three together.
    "annotation": (False, bool, None),
    "annotateChinese": (True, bool, None),
    "annotateEnglish": (True, bool, None),
    "annotateShowPinyin": (False, bool, None),
    "annotateHotkey": ("Control+e", str, None),
    "traditionalize": (True, bool, None),
    # Set QT_IM_MODULE=fcitx in the environment override. XWayland Qt apps
    # (WeChat) need it to reach Fcitx5 at all; native Wayland Qt apps prefer it
    # unset. One global value cannot be both, so this chooses. On by default -
    # an XWayland app that types only latin is the more visible failure. Takes
    # effect at the next login. See lib/generate.render_environment.
    "qtImModule": (True, bool, None),
    "pageSize": (10, int, (1, 10)),
    "verticalCandidates": (True, bool, None),
    "backgroundImageLight": ("", ImagePath, None),
    "backgroundImageDark": ("", ImagePath, None),
    "panelWidth": (DEFAULT_PANEL_WIDTH, int, PANEL_WIDTH_BOUNDS),
}


# Characters a path may not carry, whatever the filesystem thinks. A file name
# on Linux may hold any byte but NUL and `/`, newlines included - so this is not
# a check on what can exist, it is a check on what this value is allowed to do
# once it is stored. The stored path is written into the classicui theme, which
# is an INI file parsed a line at a time: a newline in it is the next key, and
# the key after that can open a section. That is the same class of hole the
# annotation hotkey had in the schema yaml, on the other side of the plugin, and
# it is closed the same way - at the point where the value is admitted.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def usable_image_path(value: str) -> bool:
    """Whether a stored background path is one Fcitx5 could be handed at all.

    Empty is the answer "no image" and is always usable. Anything else has to be
    absolute, because Fcitx5 resolves a relative `Image` inside the theme's own
    directory - so a relative path is either a file that is not there or a read
    reaching out of the theme directory - and free of control characters, for
    the reason above.

    This is the shape check, and it is the half that has to hold for a value
    that was never written through `validate`: shell.json is hand-editable, and
    `resolve` reads whatever is in it. Whether the file exists and whether its
    bytes match its extension are `image_path`'s questions, asked only when
    someone is asking for this value on purpose.
    """
    if not isinstance(value, str):
        return False
    if not value:
        return True
    return value.startswith("/") and not _CONTROL_CHARACTERS.search(value)


def image_path(key: str, value) -> str:
    """Check one background image path hard enough that Fcitx5 can open it.

    Returns the path as it should be stored: expanded and absolute, because
    Fcitx5 does not expand `~` and resolves a relative name inside the theme's
    own directory, where the user's Pictures are not.

    An empty value is the answer "no image", and is the only one that skips
    every check below.
    """
    if not isinstance(value, str):
        raise ValueError(f"{key} takes a path to an image file, not {value!r}")
    text = value.strip()
    if not text:
        return ""

    path = Path(text).expanduser()
    if _CONTROL_CHARACTERS.search(text):
        # A file whose name really does carry a newline is a file this refuses
        # to store, because storing it would write a line of the user's choosing
        # into the generated theme. Refused here with a sentence rather than
        # dropped quietly, because someone typed it.
        raise ValueError(
            f"{key}: a path written into the candidate window's theme cannot"
            " contain control characters or newlines"
        )
    if not path.is_absolute():
        # Relative would not be an error at write time and a mystery at paint
        # time: Fcitx5 would resolve it inside the theme directory and quietly
        # find nothing. Refused here so the user hears about it now.
        raise ValueError(
            f"{key} needs an absolute path, so Fcitx5 can find the file from"
            f" wherever it is looking; got {text!r}"
        )
    if not path.exists():
        raise ValueError(f"{key}: no such file: {path}")
    if not path.is_file():
        raise ValueError(f"{key}: not a file: {path}")

    suffix = path.suffix.lower()
    if suffix not in BACKGROUND_IMAGE_FORMATS:
        raise ValueError(
            f"{key}: the candidate window cannot load {suffix or path.name}; it"
            " loads " + ", ".join(BACKGROUND_IMAGE_EXTENSIONS)
        )
    name, recognise = BACKGROUND_IMAGE_FORMATS[suffix]

    try:
        with open(path, "rb") as handle:
            head = handle.read(SNIFF_BYTES)
    except OSError as error:
        raise ValueError(f"{key}: {path} is not readable: {error}") from error
    if not recognise(head, path):
        raise ValueError(
            f"{key}: {path} is named {suffix} but its contents are not {name}."
            " The candidate window picks its loader from the extension, so it"
            " would never load this file."
        )
    return str(path)


def catalogue(name_for=None) -> list[dict]:
    """Every schema the picker offers, in the shape a MultiSelect reads.

    `name_for` maps a schema id to the readable name in its schema yaml, and is
    passed in rather than looked up here because where those files live is the
    caller's knowledge, not this module's. Without it - and for any schema whose
    yaml cannot be read - the id stands in for the name, which is honest: it is
    what Rime itself calls the schema.
    """
    options = []
    for family, ids in SCHEMA_FAMILIES:
        for schema_id in ids:
            name = None
            if name_for is not None:
                try:
                    name = name_for(schema_id)
                except Exception:
                    # A machine without rime-data, an unreadable file: worth a
                    # missing name, never worth an empty picker.
                    name = None
            options.append({
                "value": schema_id,
                "label": str(name) if name else schema_id,
                "description": family,
            })
    return options


def schema_selection(value, default: list | None = None) -> list:
    """Repair a selection into something Rime can actually build.

    Unknown ids are dropped rather than deployed: a typo in schema_list is a
    schema Rime cannot build, and a failed build takes every other schema in the
    list down with it. What survives comes back in catalogue order so that the
    same selection always produces the same schema_list.

    A selection with no Chinese schema left in it gains the default one back.
    That is the hand-edited-file half of the rule; `validate` is the other half,
    and refuses such a selection outright when someone asks for it on purpose.
    """
    default = DEFAULT_SCHEMAS if default is None else default
    if not isinstance(value, list):
        return list(default)
    selected = {item for item in value if isinstance(item, str)}
    ordered = [schema_id for schema_id in SCHEMA_IDS if schema_id in selected]
    if not any(schema_id in CHINESE_SCHEMAS for schema_id in ordered):
        ordered = [schema_id for schema_id in SCHEMA_IDS
                   if schema_id in set(default) or schema_id in ordered]
    return ordered


def _migrated(raw: dict) -> dict:
    """Translate the retired `japanese` switch into a selection.

    Read only when no selection has been written yet: the first time the picker
    is used it writes `schemas`, and from then on that is the whole answer. The
    old key is left in place until then rather than rewritten behind the user's
    back - reading shell.json is not a licence to edit it.
    """
    if not isinstance(raw, dict) or "schemas" in raw:
        return raw
    if raw.get(LEGACY_JAPANESE_KEY) is not True:
        return raw
    return dict(raw, schemas=list(DEFAULT_SCHEMAS) + list(LEGACY_JAPANESE_SCHEMAS))


def resolve(raw: dict) -> dict:
    """Merge caller-supplied values over the defaults.

    A value of the wrong type falls back to the default rather than raising:
    settings come from a hand-editable JSON file, and a typo there must not
    stop the input method from deploying.
    """
    raw = _migrated(raw)
    resolved = {}
    for key, (default, kind, bounds) in DEFAULTS.items():
        value = raw.get(key, default)
        # bool is a subclass of int, so check it first or True passes as 1
        if kind is bool:
            value = value if isinstance(value, bool) else default
        elif kind is int:
            value = value if isinstance(value, int) and not isinstance(value, bool) else default
            if bounds is not None:
                low, high = bounds
                value = max(low, min(high, value))
        elif kind is str:
            value = value if isinstance(value, str) and value else default
        elif kind is ImagePath:
            # Never opened here. A path whose file has since been moved or
            # deleted costs the user a background - Fcitx5 falls back to the
            # theme colour for an image it cannot open - and must not cost
            # anyone a deploy.
            #
            # Its shape is checked all the same, and that is not the same
            # leniency question. Everything else this function repairs is a
            # value that would produce a wrong-looking window; this one is a
            # value that is written verbatim into an INI file some other program
            # parses, and shell.json is hand-editable by design. So a path that
            # could be more than a path falls back to "no image", which is what
            # an install that never asked for one gets.
            value = value if usable_image_path(value) else default
        elif kind is list:
            value = schema_selection(value, default)
        # A default that is a list is shared with DEFAULTS, so it is copied
        # rather than handed out: a caller that appends to what it was given
        # must not be editing the defaults of every later call.
        resolved[key] = list(value) if isinstance(value, list) else value
    return resolved


def load(shell_json_path: Path) -> dict:
    """Read settings from the widget's entry in shell.json.

    Every failure path returns defaults. A shell.json that is missing, damaged,
    or does not mention this plugin is a normal state - the plugin may simply
    not be on the bar yet - and must still produce a deployable configuration.
    """
    try:
        document = json.loads(Path(shell_json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return resolve({})
    entry = _widget_entry(document)
    return resolve(entry if entry is not None else {})


def validate(key: str, value):
    """Check one value hard enough that it is safe to persist.

    `resolve` is deliberately forgiving because it reads a hand-edited file and
    must always produce something deployable. A write is the opposite case: the
    caller is asking for this exact value, so a wrong type or an out-of-range
    number is refused instead of quietly becoming the default. Clamping here
    would write a number the user never asked for.

    Returns the value as it should be stored, which is not always the value
    handed in: a path is expanded and made absolute, because that is the form
    Fcitx5 can act on.
    """
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting: {key}")
    _, kind, bounds = DEFAULTS[key]
    # bool is a subclass of int, so it is checked first or True passes as 1
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key} takes true or false, not {value!r}")
    elif kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} takes a whole number, not {value!r}")
        if bounds is not None and not (bounds[0] <= value <= bounds[1]):
            raise ValueError(f"{key} must be between {bounds[0]} and {bounds[1]}, not {value}")
    elif kind is str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} takes a non-empty string, not {value!r}")
    elif kind is ImagePath:
        return image_path(key, value)
    elif kind is list:
        # A string is a sequence too, and iterating one would turn "rime_ice"
        # into eight unknown ids, so the type is checked before the members.
        if not isinstance(value, list):
            raise ValueError(f"{key} takes a list of schema ids, not {value!r}")
        unknown = [
            item for item in value
            if not isinstance(item, str) or item not in SCHEMA_IDS
        ]
        if unknown:
            raise ValueError(
                f"{key}: no such schema: " + ", ".join(repr(item) for item in unknown)
            )
        if not any(item in CHINESE_SCHEMAS for item in value):
            # An empty schema_list leaves Rime with nothing to compose with, and
            # a Japanese-or-English-only one leaves a Chinese keyboard unable to
            # type Chinese. Refused here rather than repaired, because the
            # caller asked for exactly this and should be told it is not on.
            raise ValueError(
                f"{key} must keep at least one Chinese schema, so there is "
                "something to compose with"
            )
    return value


def save(shell_json_path, values: dict) -> dict:
    """Write settings onto the widget's own entry in shell.json.

    shell.json is the whole bar's configuration and is edited by hand, so this
    touches as little as it can: it rewrites only the keys it was given, on the
    one entry that is ours, and re-serializes with Omarchy's own two-space
    indent so a write does not churn the file into a diff nobody asked for.

    The write is atomic - a temporary file in the same directory, then a
    rename - because a half-written shell.json takes down the entire bar and
    not just this plugin.

    Returns the resolved settings as they now stand on disk.
    """
    path = Path(shell_json_path)
    # What is stored is what validate hands back rather than what was typed: a
    # `~/Pictures/foo.png` has to reach shell.json expanded, because Fcitx5 does
    # not expand it and would look for a directory called `~`.
    values = {key: validate(key, value) for key, value in values.items()}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error

    entry = _widget_entry(document)
    if entry is None:
        raise ValueError(
            f"{PLUGIN_ID} is not on the bar in {path}; add it with `omarchy bar` first"
        )
    entry.update(values)
    if "schemas" in values:
        # The selection now says everything the retired switch used to say, so
        # the old key is retired with it rather than left to be read forever.
        entry.pop(LEGACY_JAPANESE_KEY, None)

    _write_atomically(path, json.dumps(document, indent=2) + "\n")
    return resolve(entry)


def _widget_entry(document):
    """The widget's own object in bar.layout, or None if it is not on the bar."""
    if not isinstance(document, dict):
        return None
    bar = document.get("bar")
    layout = (bar or {}).get("layout") if isinstance(bar, dict) else None
    if not isinstance(layout, dict):
        return None
    for section in layout.values():
        if not isinstance(section, list):
            continue
        for entry in section:
            # A section may hold bare id strings alongside objects, so anything
            # that is not an object is skipped rather than treated as an error.
            if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                return entry
    return None


def _write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`, via a temporary file beside it.

    Beside it rather than in /tmp, because a rename is only atomic within one
    filesystem, and a half-written shell.json takes down the whole bar and not
    just this plugin.

    The temporary name carries this process's pid, which is a name another
    process on the machine can work out - so it is created with O_EXCL and
    O_NOFOLLOW rather than with `open(..., "w")`. Plain truncating open follows
    a symlink standing at that name, which would send this write somewhere else
    and then rename the link over shell.json. Any leftover from a crashed write
    is removed first, so the exclusive create is a guard against a link and not
    a way for one stale file to block every future write.

    The mode is taken from the file being replaced. A rename carries the
    temporary file's own mode across, so creating it at a default 0600 would
    quietly tighten shell.json - a file this plugin edits and Omarchy owns.
    """
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = 0o644
    try:
        temporary.unlink(missing_ok=True)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_TRUNC,
            mode,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"cannot write {path}: {error}") from error
