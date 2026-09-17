"""Install and remove this plugin's Omarchy theme-set hook.

The candidate window is themed from the Omarchy palette, and that theme is
generated at deploy time - so switching Omarchy themes used to leave the
candidate window painted in the old one until the next deploy. Omarchy's own
answer to that is a hook: `omarchy-theme-set` ends with

    omarchy-hook theme-set "$THEME_NAME"

and `omarchy-hook` runs `~/.config/omarchy/hooks/theme-set` plus every file in
`~/.config/omarchy/hooks/theme-set.d/`. Read from omarchy-hook's source, the
contract is:

  * each script is run as `bash "$hook" "$@"`, so it does not have to be
    executable and does not need a shebang to work - it gets one anyway,
    because `omarchy hook install` chmods what it installs and a reader should
    not have to know which of the two put a file there;
  * the arguments are whatever omarchy-theme-set passed, which is the new
    theme's name, lowercased with spaces turned into dashes;
  * `*.sample` files are skipped, and only regular files are run;
  * a non-zero exit prints "Hook failed: <path>" and the remaining hooks still
    run - a failure is reported, never fatal.

Installing the hook is part of this plugin's lifecycle rather than a step in a
README, which means it has to come back out. It is recorded in the same install
manifest as every deployed file, so `ime-manager uninstall` removes it with
everything else and no hook is left behind pointing at a plugin that is gone.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from lib import deploy
from lib.deploy import MANIFEST_NAME  # noqa: F401  (re-exported for readers)

# The hook this plugin attaches to, and the manifest target the installed file
# is recorded under. The target is not one a deploy writes into: see
# `sync` for why that separation has to hold.
HOOK_TYPE = "theme-set"
TARGET = "hooks"

# The file name inside theme-set.d. Named after the plugin, not after the hook:
# the directory is shared with every other plugin on the machine, and a file
# called `theme-set` in there already belongs to one of them.
HOOK_NAME = "eastgold15.omarchy.ime"

MODE = 0o755


def hook_dir(config_home) -> Path:
    """Omarchy's drop-in directory for theme-set hooks."""
    return Path(config_home) / "omarchy" / "hooks" / f"{HOOK_TYPE}.d"


def script(manager_path) -> str:
    """The hook, as text.

    It is deliberately three lines. Everything the hook has to decide -
    which palette, which files, whether anything changed - is `theme apply`'s
    job, so this stays a shim that keeps working when that logic changes.

    The theme name arrives as $1 and is ignored: `theme apply` reads whichever
    palette Omarchy has just made current, which is the same answer and one
    that is also right when the hook is run by hand.
    """
    return f"""#!/usr/bin/env bash
# Installed by the eastgold15.omarchy.ime Omarchy plugin (`ime-manager hook install`).
# Removed by `ime-manager hook remove` and by `ime-manager uninstall`.
#
# Runs when the Omarchy theme changes, with the new theme's name as $1, and
# repaints the Fcitx5 candidate window to match.
#
# It cannot fail. omarchy-hook prints "Hook failed" for any non-zero exit, and
# the ordinary reasons this has nothing to do - the plugin was moved, Fcitx5 is
# not running yet at login - are not worth saying on every theme switch. Run
# `ime-manager theme apply` by hand to see what it would have said.
MANAGER={shlex.quote(str(manager_path))}
[ -x "$MANAGER" ] || exit 0
"$MANAGER" theme apply >/dev/null 2>&1 || exit 0
"""


def installed(directory) -> bool:
    return (Path(directory) / HOOK_NAME).is_file()


def install(directory, manager_path, state_dir) -> Path:
    """Write the hook and record it, so an uninstall will take it away again."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / HOOK_NAME
    # theme-set.d is shared with every other plugin on the machine, so it is a
    # directory this plugin writes into and does not own. A symlink standing at
    # this name would take the hook - and the 0755 below - somewhere else.
    deploy.replace_symlink(path)
    path.write_text(script(manager_path), encoding="utf-8")
    os.chmod(path, MODE)
    _record(state_dir, present=True)
    return path


def remove(directory, state_dir) -> bool:
    """Delete this plugin's hook and forget it. Returns whether one was there.

    Only the one file this plugin wrote is touched: another plugin's hook lives
    in the same directory and is none of this plugin's business.
    """
    path = Path(directory) / HOOK_NAME
    existed = path.is_file()
    if existed:
        path.unlink()
    _record(state_dir, present=False)
    return existed


def sync(directory, state_dir) -> None:
    """Make the manifest agree with what is on disk.

    A deploy rewrites the manifest from the files it wrote, and the hook is not
    one of them - so a deploy silently drops the hook's entry, and an uninstall
    afterwards would walk straight past a hook that is still installed. Calling
    this at the end of a deploy puts the entry back.
    """
    _record(state_dir, present=installed(directory))


def _record(state_dir, present: bool) -> None:
    """Add or drop this plugin's hook in the install manifest.

    The manifest is deploy.py's, and it is read and written through deploy.py's
    own helpers so there is one format and one place that knows it. It is merged
    rather than rewritten, because everything else in it was put there by a
    deploy that knows nothing about hooks.

    The hook is always recorded as a file this plugin created: nothing else can
    own a file in theme-set.d named after this plugin.
    """
    files = deploy.read_manifest(state_dir)
    entry = {HOOK_NAME: {"replaced": False}}
    if present:
        if files.get(TARGET) == entry:
            return
        files[TARGET] = entry
    else:
        if TARGET not in files:
            return
        del files[TARGET]
    deploy.write_manifest(state_dir, files)
