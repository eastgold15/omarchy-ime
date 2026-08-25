"""Read and change the live Fcitx5 input state.

The bar indicator replaces omarchy.keyboard-layout, which shows the keyboard
layout - not what actually decides whether a keystroke becomes Chinese. That is
the Fcitx5 state, and `fcitx5-remote` is the supported way to ask for it.

Every failure reads as "closed". Fcitx5 not running is a normal state for a bar
item to render, not an error to propagate.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

STATES = {"0": "closed", "1": "latin", "2": "active"}


def _run(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


def read(runner=_run) -> dict:
    code, out = runner(["fcitx5-remote"])
    if code != 0:
        return {"state": "closed", "method": None, "group": None}
    state = STATES.get(out.strip(), "closed")
    method = group = None
    if state == "active":
        code, out = runner(["fcitx5-remote", "-n"])
        method = out.strip() or None if code == 0 else None
        code, out = runner(["fcitx5-remote", "-q"])
        group = out.strip() or None if code == 0 else None
    return {"state": state, "method": method, "group": group}


def switch(name: str, runner=_run) -> bool:
    code, _ = runner(["fcitx5-remote", "-s", name])
    return code == 0


# --- Rime schemas -----------------------------------------------------------
#
# fcitx5-remote knows nothing about schemas; it switches input methods. The
# schema is Rime's own idea, and fcitx5-rime exposes it on the session bus at
# /rime: ListAllSchemas, GetCurrentSchema, SetSchema. That is the same switch
# Rime's own F4 menu operates, so the panel and the F4 menu cannot disagree.
#
# gdbus is the caller rather than a Python D-Bus binding because the plugin is
# stdlib-only, and because deploy.py already talks to Fcitx5 the same way.

BUS = "org.fcitx.Fcitx5"
RIME_OBJECT = "/rime"
RIME_INTERFACE = "org.fcitx.Fcitx.Rime1"
CONTROLLER_OBJECT = "/controller"
CONTROLLER_INTERFACE = "org.fcitx.Fcitx.Controller1"

# A schema id names a file in Rime's build directory, so it is held to the
# characters a schema id may contain before it is ever joined to a path.
_SCHEMA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _call(object_path: str, interface: str, method: str, *arguments: str) -> list[str]:
    return [
        "gdbus", "call", "--session",
        "--dest", BUS,
        "--object-path", object_path,
        "--method", f"{interface}.{method}",
        *arguments,
    ]


def quote_variant(value: str) -> str:
    """A GVariant text literal for a string argument.

    gdbus parses each argument as GVariant text and only falls back to treating
    it as a plain string when that parse fails - so a bare `true` would arrive
    as a boolean and a bare `10` as a number. Quoting says "string" outright.
    """
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _quoted_strings(text: str) -> list[str] | None:
    """Every quoted string in a run of GVariant text, or None.

    Only strings and the punctuation between them are accepted. Anything else -
    a number, a boolean, an unterminated quote, a Python expression - means the
    answer was not the shape that was asked for, and None says so. This is the
    whole reason the output is scanned rather than evaluated: nothing that
    arrives over the bus is ever executed.
    """
    values: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character in "'\"":
            index += 1
            collected = []
            while index < len(text) and text[index] != character:
                # GVariant escapes with a backslash, so the character after one
                # is data even when it is the closing quote.
                if text[index] == "\\" and index + 1 < len(text):
                    index += 1
                collected.append(text[index])
                index += 1
            if index >= len(text):
                return None
            values.append("".join(collected))
            index += 1
        elif character in ", \t\r\n":
            index += 1
        else:
            return None
    return values


def _tuple_body(text: str) -> str | None:
    """What is inside the one-element tuple gdbus prints, or None.

    Every `gdbus call` result is a tuple, and each of these methods returns a
    single value, so the shape is always `(<value>,)`.
    """
    stripped = text.strip()
    if len(stripped) < 3 or not stripped.startswith("(") or not stripped.endswith(")"):
        return None
    inner = stripped[1:-1].rstrip()
    if not inner.endswith(","):
        return None
    return inner[:-1].strip()


def parse_string_array(text: str) -> list[str] | None:
    """`(['a', 'b'],)` -> ['a', 'b'], or None when the text is not that.

    An empty array arrives as `(@as [],)`, not as `([],)`: gdbus prints with
    GVariant's type annotation turned on, and a container with no children
    inside a one-element tuple has no sibling to infer its type from. Both are
    accepted, because the annotation is a printing detail and not a promise.
    """
    body = _tuple_body(text)
    if body is None:
        return None
    if body.startswith("@"):
        _, _, body = body.partition(" ")
        body = body.strip()
    if not body.startswith("[") or not body.endswith("]"):
        return None
    return _quoted_strings(body[1:-1])


def parse_string(text: str) -> str | None:
    """`('a',)` -> 'a', or None when the text is not a tuple holding one string."""
    body = _tuple_body(text)
    if body is None:
        return None
    values = _quoted_strings(body)
    return values[0] if values is not None and len(values) == 1 else None


def schemas(runner=_run) -> list[str]:
    """Every schema Rime has built, in the order Rime lists them.

    An empty list is the answer to every failure - Fcitx5 not running, the Rime
    addon not loaded, gdbus not installed - for the same reason `read` calls
    those "closed": a panel has to render something, and none of them is an
    error the user can act on from here.
    """
    code, out = runner(_call(RIME_OBJECT, RIME_INTERFACE, "ListAllSchemas"))
    if code != 0:
        return []
    return parse_string_array(out) or []


def current_schema(runner=_run) -> str | None:
    """The schema Rime is composing with, or None when there is not one."""
    code, out = runner(_call(RIME_OBJECT, RIME_INTERFACE, "GetCurrentSchema"))
    if code != 0:
        return None
    return parse_string(out) or None


def set_schema(schema_id: str, runner=_run) -> bool:
    """Switch Rime to `schema_id`. This is what F4 does, from a click."""
    if not schema_id:
        return False
    code, _ = runner(_call(RIME_OBJECT, RIME_INTERFACE, "SetSchema",
                           quote_variant(schema_id)))
    return code == 0


def schema_name(schema_id: str, build_dir) -> str | None:
    """The readable name Rime compiled into a schema, or None.

    The D-Bus interface only ever hands out ids - there is no name on it, and
    none in the profile either - so the only name that exists without inventing
    one is the `schema/name` Rime writes into
    <build_dir>/<schema_id>.schema.yaml when it builds. Reading it costs one
    small file read and no subprocess, which is cheap enough to do while
    listing.

    A built schema is machine-written YAML with a fixed shape: top-level keys at
    column zero, their children two spaces in. So the block is found by
    indentation rather than by parsing YAML, which the stdlib cannot do.
    """
    if build_dir is None or not _SCHEMA_ID.fullmatch(str(schema_id)):
        return None
    try:
        text = (Path(build_dir) / f"{schema_id}.schema.yaml").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None

    inside = False
    for line in text.splitlines():
        if not line.strip():
            # A block scalar may hold a blank line - rime_ice's description
            # does - and that is not the end of the block it sits in.
            continue
        if not line[0].isspace():
            inside = line.rstrip() == "schema:"
            continue
        if not inside or len(line) - len(line.lstrip(" ")) != 2:
            continue
        key, separator, value = line.strip().partition(":")
        if key == "name" and separator:
            return _yaml_scalar(value) or None
    return None


def _yaml_scalar(text: str) -> str:
    """Unwrap the one quoting style a built schema's name may arrive in."""
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        body = value[1:-1]
        if value[0] == '"':
            return body.replace('\\"', '"').replace("\\\\", "\\")
        return body.replace("''", "'")
    return value


def describe_schemas(runner=_run, build_dir=None) -> list[dict]:
    """Every schema as the panel needs it: id, readable name, and which is on.

    The name falls back to the id rather than to a table of hand-written
    translations: a schema this plugin has never heard of is still a schema the
    user can switch to, and calling it by its id is honest.
    """
    available = schemas(runner)
    if not available:
        return []
    active = current_schema(runner)
    return [
        {
            "id": schema_id,
            "name": schema_name(schema_id, build_dir) or schema_id,
            "current": schema_id == active,
        }
        for schema_id in available
    ]


def reload_addon(name: str, runner=_run) -> bool:
    """Make one Fcitx5 addon re-read its configuration.

    This is how a regenerated theme reaches the screen: classicui draws the
    candidate window and loads its theme when its configuration is reloaded, so
    reloading that one addon repaints the window without restarting Fcitx5 and
    without dropping every application's input context.
    """
    code, _ = runner(_call(CONTROLLER_OBJECT, CONTROLLER_INTERFACE,
                           "ReloadAddonConfig", quote_variant(name)))
    return code == 0
