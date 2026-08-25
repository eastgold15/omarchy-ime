"""Report the two Hyprland pieces of a working input method, without owning them.

A usable setup needs three things this plugin does not generate from settings:
a key that toggles the input method, a blur rule that makes the candidate window
look like the rest of the desktop, and the environment override. The third one
the plugin now writes (see lib/generate.render_environment). The first two it
deliberately does not.

`~/.config/hypr/` is the user's own, frequently a dotfiles module's, and the only
drop-in Omarchy offers is `~/.local/state/omarchy/toggles/hypr/*.lua` - which
would mean this plugin generating executable Lua into somebody's compositor
configuration. Omarchy's own `toggles.lua` carries an exclude list precisely
because generated Lua there once carried an injected device name. That is a poor
trade for a keybind.

So: detect, report, and document. Every answer here is one of three - it is on,
it is not on, or we cannot tell - and the third is not a failure. A machine with
no hyprctl, or a compositor that is not Hyprland, is a machine this module has
nothing to say about, which is different from one that said no.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ON = "on"
OFF = "off"
UNKNOWN = "unknown"

# The effective value of the blur rule, asked of the running compositor rather
# than read out of a file. The rule can be set from hyprland.conf, from an
# Omarchy Lua module, or from a drop-in, and to the user those are the same
# answer; only the compositor knows which one won.
BLUR_OPTION = "decoration:blur:input_methods"

# What the toggle has to run. The modifier and the key are the user's business -
# a left-Shift tap here, Super+Space elsewhere - so it is the dispatch that is
# matched and never the key.
#
# Bounded to one command: `-t` has to belong to this fcitx5-remote invocation
# and not to something after a `;`, `&&` or a pipe, or `fcitx5-remote -o &&
# notify-send -t 500` would read as a toggle.
TOGGLE_PATTERN = re.compile(r"\bfcitx5-remote\b[^\n;&|]*?(?<![\w-])-t(?![\w-])")

# Suffixes a Hyprland configuration file has. Exact, so that the
# `hyprland.lua.bak.1787290304` sitting in a real ~/.config/hypr is not read as
# configuration: what a backup says is what the configuration used to say.
CONFIG_SUFFIXES = (".conf", ".lua")

# A configuration file is small. Anything past this is something else that
# happens to live in the directory, and reading it is not worth the stall.
MAX_CONFIG_BYTES = 512 * 1024

# Line prefixes that mean "not in effect", in the two languages these files are
# written in: Lua and Hyprland's own configuration syntax.
COMMENT_PREFIXES = ("--", "#", "//")

TIMEOUT = 5


def _run(command) -> tuple[int, str] | None:
    """Run a command, or answer None when it could not be run at all.

    None and a non-zero exit are different facts and both end up as UNKNOWN, but
    only one of them means hyprctl is not installed.
    """
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode, result.stdout


def _answered(result) -> str | None:
    """The stdout of a call that actually answered, or None."""
    if result is None:
        return None
    code, out = result
    if code != 0:
        return None
    return out


def blur(runner=_run) -> str:
    """Whether the compositor blurs input-method layer surfaces.

    hyprctl answers an option it does not know with the words "no such option"
    and exit status 0, so the exit status alone never says whether the answer is
    an answer - the output has to parse as the JSON object hyprctl prints for a
    real option, or the answer is that we cannot tell.
    """
    out = _answered(runner(["hyprctl", "getoption", "-j", BLUR_OPTION]))
    if not out:
        return UNKNOWN
    try:
        document = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return UNKNOWN
    if not isinstance(document, dict):
        return UNKNOWN
    # Hyprland prints a boolean option as `bool` and some booleans as `int`.
    # Reading only one of the two would turn a rule that is on into "cannot
    # tell" on the version that spells it the other way.
    for key in ("bool", "int"):
        if key in document:
            return ON if document[key] else OFF
    return UNKNOWN


def _dispatches_toggle(bind) -> bool:
    """Whether one entry of `hyprctl binds -j` runs the toggle."""
    if not isinstance(bind, dict):
        return False
    return any(
        isinstance(bind.get(field), str) and TOGGLE_PATTERN.search(bind[field])
        for field in ("arg", "description")
    )


def default_config_dirs(environ=None) -> list[Path]:
    """Every directory a Hyprland configuration this user wrote may live in.

    Two of them: the user's own configuration, and the one drop-in directory
    Omarchy generates into. A toggle written in either is equally in effect, and
    neither is somewhere this plugin ever writes.
    """
    if environ is None:
        environ = os.environ
    home = Path.home()
    config = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    state = Path(environ.get("XDG_STATE_HOME") or home / ".local/state")
    return [config / "hypr", state / "omarchy" / "toggles" / "hypr"]


def _configures_toggle(directories) -> bool:
    """Whether any Hyprland configuration file asks for the toggle.

    This exists because under Omarchy the compositor cannot answer the question.
    Every bind Omarchy registers comes back from `hyprctl binds -j` with the
    dispatcher `__lua` and an opaque index for its argument, so the command is
    nowhere in the answer; and the left-Shift tap itself is not a bind at all
    but an `hl.on("input.keyboard.key", ...)` handler, which `hyprctl binds`
    never lists. Detection that stopped at the binds would report "not on" on
    every Omarchy machine that has it, which is worse than saying nothing.

    A commented-out line does not count, and neither does a file whose name says
    it is a backup - both are things the configuration is not doing.
    """
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in CONFIG_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_CONFIG_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(COMMENT_PREFIXES):
                    continue
                if TOGGLE_PATTERN.search(stripped):
                    return True
    return False


def toggle(runner=_run, config_dirs=None) -> str:
    """Whether some key toggles the input method.

    The compositor is asked first, because a loaded bind is proof. When it
    cannot say - which is the ordinary case under Omarchy, see
    `_configures_toggle` - the user's own Hyprland configuration is read as the
    next best evidence.

    A compositor that cannot be reached at all outranks both: a file saying the
    toggle is configured says nothing about whether that file is loaded.
    """
    out = _answered(runner(["hyprctl", "binds", "-j"]))
    if not out:
        return UNKNOWN
    try:
        loaded = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return UNKNOWN
    if not isinstance(loaded, list):
        return UNKNOWN
    if any(_dispatches_toggle(bind) for bind in loaded):
        return ON
    if config_dirs is None:
        config_dirs = default_config_dirs()
    return ON if _configures_toggle(config_dirs) else OFF


def report(runner=_run, config_dirs=None) -> dict[str, str]:
    """Both answers, as `ime-manager status` carries them."""
    return {"blur": blur(runner), "toggle": toggle(runner, config_dirs)}
