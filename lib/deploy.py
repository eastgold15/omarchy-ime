"""Write the generated configuration and remember exactly what was written.

The manifest is what makes this reversible. Without it, uninstall would have to
guess which files in a shared Rime directory belong to the plugin, and a redeploy
that stops generating a file would leave it behind for Rime to keep loading.

The manifest records not only *what* was written but whether the plugin
*created* the file or *replaced* one that was already there. That distinction is
the difference between an uninstall that puts the machine back and one that
takes things away: `~/.config/fcitx5/conf/xcb.conf` is shipped by
omarchy-settings, and deleting it because the manifest listed it left the user
without a file Omarchy had deliberately configured. A replaced file is backed up
before it is written and restored on uninstall; only a file this plugin created
is deleted.

Payload is grouped rather than copied wholesale, because a schema nobody
selected must not leave its files on disk: Rime loads what it finds, so shipping
the Japanese schemas to someone who deselected them would half-enable them.

Writing the files is only half a deploy. Rime reads its customization files at
build time, not at keystroke time, so a configuration that is on disk but not
built is a configuration that is not in effect - and one that fails to build
leaves the user with no input method at all. `redeploy` closes that half, and it
closes it by looking at what Rime *built* rather than only at what it
complained about: a build can fail without saying anything at all.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from lib import assets, features, generate, settings as settings_lib, theme as theme_lib

MANIFEST_NAME = "installed.json"

# Bumped when the manifest's shape changes. Version 1 recorded a bare list of
# paths per target and could not say whether a file had been created or written
# over; version 2 records that, so `uninstall` can put back what it replaced.
MANIFEST_VERSION = 2

# The prefix a notice wears when it is something that went wrong rather than
# something that happened. The panel and the command line both split on it:
# without it every notice was one grey line among the file paths, and the
# warnings that matter - a theme that could not be read, a source that could not
# be fetched, a setting that reaches only half the selected schemas - were shown
# as a green tick and a count.
#
# Taken from lib/assets.py rather than spelled again, because assets.py reports
# refused downloads through the same channel and two copies of a prefix the
# panel splits on is one copy too many.
WARNING = assets.WARNING

# The systemd unit fcitx5 runs under. librime logs through glog with its stderr
# threshold at ERROR, and fcitx5's stderr is this unit's journal, so a build
# error lands here and nowhere else - which is also why the journal is worth
# nothing when fcitx5 was started some other way. See `_journal_reaches_fcitx5`.
UNIT = "omarchy-fcitx5.service"

# The only systemd command this plugin runs, and it is a question: `is-active`
# prints the unit's current state and changes nothing. Nothing here starts,
# stops, enables, disables, restarts, masks or writes a unit - fcitx5's
# lifecycle belongs to the session that launched it, and a plugin that took it
# over would be a plugin that can leave the user unable to type. It is a named
# constant so that the one place it is used reads as the read it is.
UNIT_STATE_QUERY = ["systemctl", "--user", "is-active", UNIT]

# Reloading the rime addon's configuration is what rebuilds. fcitx5-rime answers
# a config reload by tearing down its librime session and calling
# start_maintenance, which recompiles every schema whose source has changed -
# verified on this machine by turning the addon's log level up and watching it
# answer with "Rime UpdateConfig" then "Rime Start".
#
# The two obvious alternatives do not work. `fcitx5-remote -r` reloads Fcitx5's
# own configuration and leaves librime alone: it produces no journal output and
# does not touch the build directory. Restarting the unit does rebuild, but it
# drops every application's input context along with it, which is a heavy price
# for a settings change.
REDEPLOY_COMMAND = (
    "gdbus", "call", "--session",
    "--dest", "org.fcitx.Fcitx5",
    "--object-path", "/controller",
    "--method", "org.fcitx.Fcitx.Controller1.ReloadAddonConfig",
    "rime",
)

# Whether fcitx5 is on the bus at all, asked of the bus rather than of fcitx5.
# `org.fcitx.Fcitx5` has a D-Bus service file, so *calling* it would start it;
# NameHasOwner only looks. This is what tells "fcitx5 is not running, so it will
# pick this configuration up when it starts" - the ordinary state of a machine
# that has just installed the plugin - apart from "fcitx5 is running and refused
# it", which is the one that has to be shouted about.
PRESENCE_COMMAND = (
    "gdbus", "call", "--session",
    "--dest", "org.freedesktop.DBus",
    "--object-path", "/org/freedesktop/DBus",
    "--method", "org.freedesktop.DBus.NameHasOwner",
    "org.fcitx.Fcitx5",
)

# What a failed build says. Matched as substrings because the surrounding glog
# timestamp, thread id and source location all vary.
#
# The first four are the config compiler's. They were the whole list, and they
# only cover the half of a build that reads yaml: every string below them comes
# from the dictionary compiler and the deployment tasks, which is where the
# failure a missing download produces actually lands. All of them were read out
# of `strings /usr/lib/librime.so.1` (librime 1.17.0) rather than guessed.
BUILD_ERRORS = (
    "error building config:",
    "unresolved dependency:",
    "error applying patch to",
    "trying to append string to non scalar",
    "Error deploying: ",
    "Cannot load dictionary '",
    "Error creating dictionary '",
    "missing dict settings.",
    "failed to build pack: ",
    "error building reversedb.",
    # `dict_compiler.cc:86] source file '<x>' does not exist.` and
    # `deployment_tasks.cc:376] dictionary '<x>' failed to compile.` - the two
    # lines a deploy with the network down produces, once the schema's
    # `import_tables:` names a table that was never fetched. The leading quote
    # keeps them off librime's unrelated regex diagnostics.
    "' does not exist.",
    "' failed to compile.",
)

# librime builds on its own thread, so the journal has to be watched rather than
# read once. A failure is reported the moment it appears.
#
# A success takes longer to be sure of. There is no line that says "built", so
# the evidence is the build directory: once every deployed schema has a compiled
# file at least as new as what the deploy just wrote, the rebuild has happened.
# SETTLE_SECONDS is how long a quiet journal is watched before that evidence is
# accepted as final; BUILD_SECONDS is how long the evidence itself is waited
# for, because a first deploy compiles rime_ice's tables from scratch and eight
# seconds is nowhere near enough for that. A repeat deploy that changed nothing
# passes the evidence test at once and still costs only the settle window.
SETTLE_SECONDS = 8.0
BUILD_SECONDS = 90.0
POLL_SECONDS = 0.5
COMMAND_TIMEOUT = 30

# Payload directory -> the target it installs into. A group a feature owns
# (see lib/features.py) is installed only while that feature is enabled, and
# removed by the ordinary stale-file path once it is not; a group nobody owns is
# part of the core and always installed. There is no themes group: the
# candidate window's theme is generated from the user's own Omarchy theme rather
# than shipped, so this plugin vendors nobody's colours.
PAYLOAD_GROUPS = {
    "rime": "rime",
    "rime-japanese": "rime",
    "fcitx5": "fcitx5",
}

# Where the generated Fcitx5 theme is installed. The directory name is the
# plugin's own and never the Omarchy theme's, so switching themes rewrites one
# directory instead of leaving a new one behind each time; it is also the name
# the generated conf/classicui.conf hands to Fcitx5 as `Theme=`.
THEME_TARGET = "themes"

# Files extracted from a fetched source install into the Rime directory, so the
# manifest records them beside the generated ones and deselecting the schema that
# pulled them in removes them by the ordinary stale-file path.
SOURCE_TARGET = "rime"

# Where the environment override is installed: ~/.config/environment.d, which
# systemd reads after /usr/lib/environment.d, so a file with the packaged file's
# own name replaces it.
#
# It is a target of its own rather than part of the fcitx5 one because it is a
# different directory with a different owner - Fcitx5 reads ~/.config/fcitx5,
# systemd reads this - and because the manifest is what an uninstall walks:
# putting the file back under the packaged behaviour means removing exactly this
# file and nothing else in that shared directory.
ENVIRONMENT_TARGET = "environment"

# The target the Fcitx5 configuration lives under.
FCITX5_TARGET = "fcitx5"

# Where the CLI symlink is installed: ~/.local/bin, which Arch puts on PATH by
# default. A deploy links `ime-manager` there back at the plugin's own copy, so
# the terminal gets the same entry the panel uses without a hard-coded plugin
# path in anyone's shell configuration. Recorded in the manifest like every
# deployed file, because a link left pointing at a removed plugin is exactly
# the litter the manifest exists to prevent.
BIN_TARGET = "bin"


def default_cache_root() -> Path:
    return assets.default_cache_root()


def resolve_dictionary(settings: dict, cache_root: Path, fetcher=assets.download,
                       notice=None) -> Path:
    """Return where the annotation dictionary lives, downloading it if wanted.

    The path is returned whether or not the download succeeded: the filter reads
    an absent file as an empty dictionary, so a deploy after a failed download is
    still a correct deploy, minus the glosses. Nothing is downloaded unless a
    feature that reads the dictionary is active (see lib/features.py); a copy
    already in the cache is left alone either way, because it is cache and not
    configuration.

    `notice` is handed straight to `assets.ensure_dictionary`, which is what
    says which CC-CEDICT release arrived, whether it is a different one than was
    cached, and why a download was refused. Passing it through rather than
    swallowing it is the difference between a dictionary that can be swapped
    under the user without a word and one that cannot.
    """
    if features.dictionary_wanted(settings):
        assets.ensure_dictionary(cache_root, fetcher, notice=notice)
    return assets.dictionary_path(cache_root)


def cached_source_provider(cache_root: Path):
    """An asset provider backed by the on-disk cache.

    Returns a callable that maps a source name to its clone directory, or None
    when the source could not be fetched.
    """
    def provide(name: str) -> Path | None:
        return assets.ensure(name, Path(cache_root))

    return provide


# --- the install manifest ----------------------------------------------------


def _entry(replaced: bool, backup: str | None = None) -> dict:
    record: dict = {"replaced": bool(replaced)}
    if backup:
        record["backup"] = backup
    return record


def _safe_relative(relative) -> str | None:
    """A manifest path that can only ever name a file inside its target, or None.

    This is what makes the manifest safe to act on. Everything that decides
    *what* this plugin may write - the payload groups, the generated file names,
    the whitelist of fetched source files - runs at deploy time, in a process
    that has since exited. `uninstall` runs later, reads paths back off disk,
    and acts on every one of them: it unlinks what the manifest calls a created
    file and copies a backup over what it calls a replaced one. By then there is
    no whitelist left, only text.

    So the text is checked here, where it is read, rather than trusted because
    some earlier process wrote it. `../../../.bashrc` as a path was a delete;
    `/etc/cron.d/x` was a delete too, because joining an absolute path onto a
    root discards the root; and either shape in a `backup` was an arbitrary read
    followed by an arbitrary write. None of them survives this.
    """
    text = str(relative)
    if not text or "\x00" in text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    cleaned = str(path)
    return cleaned if cleaned not in ("", ".") else None


def _inside(root, relative: str) -> Path | None:
    """`root/relative` when it really is inside `root`, else None.

    `_safe_relative` is the check on the text; this is the check on the
    filesystem. A backup path can be free of `..` and still leave the backup
    directory, by naming a symlink somebody planted in it - which is lexically
    inside and actually outside.
    """
    root = Path(root)
    try:
        anchor = root.resolve()
        resolved = (root / relative).resolve()
    except OSError:
        return None
    return resolved if resolved == anchor or anchor in resolved.parents else None


def replace_symlink(path) -> None:
    """Take away a symlink standing where a file is about to be written.

    Every write this plugin performs opens its destination by name, and opening
    a symlink by name follows it - so a link planted at
    `~/.config/fcitx5/conf/classicui.conf` would send a generated file wherever
    it points, and a restore would send the user's own backed-up content there
    too. The link goes and a regular file is written in its place, which is also
    the honest outcome for a path the manifest records this plugin as owning:
    whatever was at the other end was copied into the backup before it was
    replaced, and an uninstall puts that content back.
    """
    path = Path(path)
    try:
        if path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _normalise(entry) -> dict[str, dict]:
    """One target's manifest entry, whatever version wrote it.

    A version 1 manifest is a bare list of paths. It reads as a list of files
    this plugin created, which is what the code that wrote it assumed and what
    an uninstall then did - so an install made before this change behaves on
    uninstall exactly as it always would have, and the first deploy afterwards
    records the truth.

    A path that could name something outside its target is dropped, and so is
    the whole entry when its `backup` could (see `_safe_relative`). Dropped
    rather than repaired, because either repair is worse than doing nothing:
    keeping the entry without its backup turns a restore into a delete, and
    keeping it without its path is not an entry at all.
    """
    if isinstance(entry, dict):
        items = list(entry.items())
    elif isinstance(entry, list):
        items = [(relative, None) for relative in entry]
    else:
        return {}
    records: dict[str, dict] = {}
    for relative, record in items:
        safe = _safe_relative(relative)
        if safe is None:
            continue
        if not isinstance(record, dict):
            records[safe] = _entry(False)
            continue
        record = dict(record)
        if "backup" in record:
            backup = _safe_relative(record["backup"])
            if backup is None:
                continue
            record["backup"] = backup
        records[safe] = record
    return records


def read_manifest(state_dir) -> dict[str, dict[str, dict]]:
    """The manifest as `target -> relative -> record`, or empty.

    A manifest that is missing or damaged reads as empty: it is state this
    plugin owns and can rebuild, never a reason to refuse to run.
    """
    try:
        document = json.loads(
            (Path(state_dir) / MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    files = document.get("files")
    if not isinstance(files, dict):
        return {}
    return {str(target): _normalise(entry) for target, entry in files.items()}


def write_manifest(state_dir, files: dict[str, dict[str, dict]]) -> None:
    """Write the manifest, creating its directory if it is not there yet.

    The directory is created here rather than assumed: `uninstall` writes an
    empty manifest, and on a machine that never deployed there is nothing to
    write it into - which used to be a FileNotFoundError traceback out of a
    command whose whole job is to leave the machine tidy.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    _restrict(state_dir)
    document = {
        "version": MANIFEST_VERSION,
        "files": {
            target: {relative: entries[relative] for relative in sorted(entries)}
            for target, entries in sorted(files.items())
        },
    }
    (state_dir / MANIFEST_NAME).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8",
    )


def _back_up(path: Path, target: str, relative: str, state_dir: Path, stamp: str) -> str:
    """Copy a file this deploy is about to write over. Returns its backup path.

    The path is relative to `state_dir/backups`, and it goes in the manifest:
    that is what lets `uninstall` put the user's own file back rather than
    leaving a hole where it used to be.
    """
    inside = f"{stamp}/{target}/{relative}"
    destination = Path(state_dir) / "backups" / inside
    destination.parent.mkdir(parents=True, exist_ok=True)
    replace_symlink(destination)
    shutil.copy2(path, destination)
    # The user's own file, in a directory that is world-readable by default.
    # fcitx5 keeps `profile` at 0600 and copy2 carries that mode across, but a
    # file that was 0644 where it came from does not become less private by
    # being copied here either - so the directory is what is closed, once, and
    # everything under it inherits that.
    _restrict(Path(state_dir) / "backups")
    return inside


def _restrict(directory: Path) -> None:
    """Make a directory this plugin owns readable by its owner alone.

    `mkdir` takes its mode from the umask, which on an ordinary desktop is 0022
    - so every directory here would be world-traversable. The backups hold
    copies of the user's own configuration, and the state directory holds the
    manifest that says what this plugin may delete; neither is anybody else's
    business, and the manifest in particular is the file `_safe_relative` exists
    because someone might write.
    """
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


# The largest a fetched dictionary or schema file may be before this plugin
# stops treating it as one. The four Rime sources it fetches are between 30KB
# and 26MB; 64MB is generous enough that upstream can grow by a factor of two
# and still be copied, and small enough that a repository rewritten to carry a
# multi-gigabyte `japanese.dict.yaml` fills nobody's disk.
#
# The pin is what makes substitution hard - `git fetch` names the commit, so the
# bytes are the ones that hash to it - but a pin is bumped by a human reading a
# diff, and this is the bound that holds while nobody is reading. A file past it
# is treated exactly as a file the fetch did not produce: the copy from the last
# deploy is kept, and the schema that needs it is withheld with a warning.
MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024


def _usable_source(source: Path) -> bool:
    """Whether a file inside a fetched clone is one this plugin will copy out.

    A regular file, of a believable size, and never a symlink. `git checkout`
    writes whatever the tree says, symlinks included - so a repository whose
    `opencc/zhjp.json` is a link to `~/.ssh/id_ed25519` would otherwise have
    that file copied into the Rime directory under a name the manifest lists.
    """
    try:
        if source.is_symlink() or not source.is_file():
            return False
        return source.stat().st_size <= MAX_SOURCE_FILE_BYTES
    except OSError:
        return False


def _files_under(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        str(path.relative_to(directory)): path
        for path in directory.rglob("*")
        if path.is_file()
    }


def _prune_empty(root: Path, relative: str) -> None:
    """Remove the directories a removed file leaves empty, up to the target root.

    `rime/lua`, `rime/opencc` and the generated theme's own directory are all
    directories this plugin created and nothing else uses. Left behind they are
    litter, and the empty theme directory is worse than litter: fcitx5-configtool
    lists it as a theme.
    """
    # Both sides resolved, and containment checked against the resolved form.
    # `relative` reaches here from the manifest; `_safe_relative` has already
    # refused a `..` in it, and resolving is what closes the other half - a
    # symlinked directory inside the target that leads back out of it. An
    # upward walk that starts outside the root would otherwise rmdir its way up
    # somebody else's tree.
    root = Path(root).resolve()
    try:
        directory = (Path(root) / relative).parent.resolve()
    except OSError:
        return
    while directory != root and root in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _selected(settings: dict) -> list[str]:
    """The schemas this configuration deploys.

    Read through lib/settings so that a hand-edited selection is repaired the
    same way here as it is when the configuration is rendered: a payload group
    installed for a schema that never reaches schema_list is a group nothing
    removes.
    """
    return settings_lib.schema_selection(settings.get("schemas"))


def active_groups(settings: dict) -> list[str]:
    """Payload groups this configuration installs, in a stable order.

    A core group is always in; a group a feature owns is in while that feature
    is enabled. The feature decides, so that turning one off removes its files
    by the same stale-file path that deselecting a schema does.
    """
    return [
        group
        for group in PAYLOAD_GROUPS
        if (owner := features.owner(group)) is None or owner.enabled(settings)
    ]


def needed_sources(settings: dict) -> list[str]:
    """External sources this configuration needs, in a stable order."""
    ordered: list[str] = []
    for schema in _selected(settings):
        for name in features.SCHEMA_SOURCES.get(schema, ()):
            if name not in ordered:
                ordered.append(name)
    return ordered


def _resolve_theme(theme_source, notice) -> dict:
    """Read the desktop's theme, and say what was read.

    A theme file that moved, a colors.toml a hand edit broke, a compositor that
    is not Hyprland: none of those may cost anyone their input method, so every
    one of them lands on the neutral fallback palette and is announced rather
    than raised.
    """
    try:
        resolved = theme_source()
    except Exception as error:
        resolved = theme_lib.resolved(
            theme_lib.FALLBACK_PALETTE, theme_lib.FALLBACK_RADIUS, "",
            fallback=True, reason=str(error),
        )
    if notice is not None:
        if resolved["fallback"]:
            notice(
                WARNING
                + f"theme: could not read the Omarchy theme ({resolved['reason']});"
                " the candidate window uses the neutral fallback palette"
            )
        else:
            notice(
                f"theme: {resolved['source'] or 'unnamed'}"
                f" (corner radius {resolved['radius']})"
            )
    return resolved


def _source_is_satisfied(name: str, source_files: dict, targets: dict) -> bool:
    """Whether this source's files are available, fetched now or already there.

    A fetch that failed is not automatically a problem: the tables it carries
    may be sitting in the Rime directory from the last deploy that worked.
    """
    root = targets.get(SOURCE_TARGET)
    for relative in assets.SOURCE_FILES[name].values():
        if relative in source_files:
            continue
        if root is not None and (Path(root) / relative).is_file():
            continue
        return False
    return True


def deploy(settings: dict, targets: dict[str, Path], state_dir: Path, payload_dir: Path,
           asset_provider=None, dictionary_path=None, theme_source=None,
           notice=None) -> dict[str, list[str]]:
    """Install generated files, the active payload groups, and fetched assets.

    `targets` maps a target name (rime, fcitx5, themes) to its directory.
    `asset_provider` maps a source name to its clone directory, or None when the
    fetch failed. A failure is never raised - no network problem may leave the
    user unable to type - but it is no longer silent either, and it no longer
    costs them the files they already had: what a failed source provides is
    protected from the stale-file sweep, and a schema whose tables are neither
    fetched nor already on disk is withheld from this deploy rather than
    written into schema_list for Rime to fail on.
    `dictionary_path` is where the annotation filter should look for its
    dictionary; it is resolved by the caller (see `resolve_dictionary`) so that
    downloading and installing stay separable.
    `theme_source` returns the candidate window's theme, in the shape
    lib/theme.resolve produces. It is a seam rather than a caller's argument
    because reading the desktop's own appearance is part of writing the
    configuration, and because it lets the tests hand over a palette instead of
    needing an Omarchy install and a running compositor.
    `notice` is told what the deploy did that the user should see. A notice that
    reports something wrong is prefixed with `warning: `, so that the caller can
    show it as one.
    Returns the installed paths grouped by target name.
    """
    if asset_provider is None:
        asset_provider = cached_source_provider(default_cache_root())
    if theme_source is None:
        theme_source = theme_lib.resolve
    state_dir = Path(state_dir)
    payload_dir = Path(payload_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    previous = read_manifest(state_dir)
    written: dict[str, dict[str, dict]] = {}

    def announce(message: str) -> None:
        if notice is not None:
            notice(message)

    def install(target: str, relative: str, write) -> None:
        root = targets.get(target)
        if root is None:
            return
        destination = Path(root) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = previous.get(target, {}).get(relative)
        # The mode of whatever is there now, so that replacing a file does not
        # widen it. shutil.copy2 carries the *source's* mode, which is 0644 out
        # of a git checkout - and fcitx5 keeps `profile` and its conf files at
        # 0600.
        existing_mode = None
        if destination.exists():
            try:
                existing_mode = stat.S_IMODE(destination.stat().st_mode)
            except OSError:
                existing_mode = None
        if record is None:
            # The first time this plugin writes here. Whatever is already at
            # this path belongs to somebody else - Omarchy ships some of it -
            # so it is copied aside and the manifest remembers that this was a
            # replacement rather than a creation.
            if destination.exists():
                record = _entry(True, _back_up(destination, target, relative,
                                               state_dir, stamp))
            else:
                record = _entry(False)
        # After the backup and before the write: what the link pointed at has
        # been copied aside, and from here on this path is a regular file this
        # plugin writes rather than a door into someone else's.
        replace_symlink(destination)
        write(destination)
        if existing_mode is not None:
            try:
                os.chmod(destination, existing_mode)
            except OSError:
                pass
        written.setdefault(target, {})[relative] = record

    def writer(text: str):
        """Write `text`, but only when it is not already what is there.

        Rime rebuilds from mtime, so rewriting an unchanged file would ask for a
        recompile the deploy did not need.

        Reading what is there cannot be allowed to fail the deploy. This plugin
        is built to take over a hand-made Rime setup, where a GBK
        `default.custom.yaml` is not exotic, and a UnicodeDecodeError escaping
        from here stopped a deploy at its worst possible moment: after some
        files were written, before the stale-file sweep, the manifest and the
        rebuild. A file that cannot be read is a file that does not say what we
        are about to write, which is all this needs to know.
        """
        def write(destination) -> None:
            try:
                current = destination.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                current = None
            if current != text:
                destination.write_text(text, encoding="utf-8")
        return write

    # --- what the network could and could not provide -------------------------
    #
    # Resolved before anything is rendered, because a schema whose dictionary
    # tables are missing must not reach schema_list: Rime would fail to build
    # it, and a failed schema takes the input method down with it.
    source_files: dict[str, Path] = {}
    failed_sources: list[str] = []
    for name in needed_sources(settings):
        try:
            clone = asset_provider(name)
        except Exception:
            clone = None
        if clone is None:
            failed_sources.append(name)
            continue
        for inside, relative in sorted(assets.SOURCE_FILES[name].items()):
            source = Path(clone) / inside
            if _usable_source(source):
                source_files[relative] = source

    unusable = {
        name for name in failed_sources
        if not _source_is_satisfied(name, source_files, targets)
    }
    for name in failed_sources:
        if name in unusable:
            announce(
                WARNING + f"source: {name} could not be fetched and none of its"
                " files are on disk yet"
            )
        else:
            announce(
                WARNING + f"source: {name} could not be fetched; the copy from"
                " the last deploy is kept"
            )

    withheld = [
        schema for schema in _selected(settings)
        if any(source in unusable
               for source in features.SCHEMA_SOURCES.get(schema, ()))
    ]
    if withheld:
        announce(
            WARNING + "schemas: " + ", ".join(withheld)
            + " are not deployed this time, because the tables they import"
            " could not be fetched; deploy again once you are online"
        )
    effective = settings
    if withheld:
        effective = dict(settings)
        effective["schemas"] = [
            schema for schema in _selected(settings) if schema not in withheld
        ]

    # Every file a failed source owns, whether or not it was fetched this time.
    # These are never swept: the sweep exists to remove what this configuration
    # stopped asking for, and a download that did not happen is not the user
    # asking for anything.
    protected = {
        relative
        for name in failed_sources
        for relative in assets.SOURCE_FILES[name].values()
    }

    for relative, text in sorted(generate.render(effective, dictionary_path).items()):
        install("rime", relative, writer(text))

    # Generated before the payload is copied, and no longer part of it:
    # classicui.conf carries a key the settings decide (the candidate list's
    # direction) alongside the name of the generated theme, so it cannot be a
    # file that ships fixed.
    for relative, text in sorted(
            generate.render_fcitx5(effective, theme_lib.DIRECTORY).items()):
        install(FCITX5_TARGET, relative, writer(text))

    # The Fcitx5 profile, merged rather than written. It carries the user's
    # keyboard layout and every input method they have configured; this plugin's
    # business with it is to add Rime, not to replace a German keyboard with a
    # US one and throw away everything else in the group.
    fcitx5_root = targets.get(FCITX5_TARGET)
    if fcitx5_root is not None:
        current = Path(fcitx5_root) / generate.PROFILE_PATH
        try:
            existing = current.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            existing = None
        install(FCITX5_TARGET, generate.PROFILE_PATH,
                writer(generate.render_profile(existing)))

    # A setting the selection cannot honour is reported here rather than left to
    # be discovered by typing. Nothing is raised: the deploy is correct, it just
    # does less than the settings appear to ask for, and the user is the one who
    # gets to decide whether that matters.
    for message in generate.notices(effective):
        announce(WARNING + message)

    # The environment override. systemd reads environment.d at session start, so
    # this is the one deployed file that does nothing until the next login - the
    # deploy says so rather than letting the user conclude it did not work.
    qt_im_module = bool(effective["qtImModule"])
    environment = generate.render_environment(set_qt_im_module=qt_im_module)
    for relative, text in sorted(environment.items()):
        install(ENVIRONMENT_TARGET, relative, writer(text))
    if ENVIRONMENT_TARGET in written:
        announce(
            "environment: QT_IM_MODULE=fcitx is set for Qt applications"
            if qt_im_module
            else "environment: QT_IM_MODULE is unset for Qt applications"
        )
        announce("environment: this takes effect at your next login")

    for name, text in sorted(_resolve_theme(theme_source, notice)["files"].items()):
        install(THEME_TARGET, f"{theme_lib.DIRECTORY}/{name}", writer(text))

    for group in active_groups(effective):
        target = PAYLOAD_GROUPS[group]
        for relative, source in sorted(_files_under(payload_dir / group).items()):
            install(target, relative,
                    lambda destination, source=source: shutil.copy2(source, destination))

    for relative, source in sorted(source_files.items()):
        install(SOURCE_TARGET, relative,
                lambda destination, source=source: shutil.copy2(source, destination))

    # The CLI on PATH (see BIN_TARGET). Not routed through `install`: the
    # deployed artifact is the symlink itself, and `install` writes regular
    # files - its `replace_symlink` would delete a correct link on every deploy
    # just to remake it. A symlink already standing at this name is treated as
    # this plugin's to replace rather than backed up, because backing up a link
    # copies whatever it points at - and a dangling one would fail the deploy.
    bin_root = targets.get(BIN_TARGET)
    if bin_root is not None:
        manager = Path(__file__).resolve().parent.parent / "ime-manager"
        destination = Path(bin_root) / manager.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = previous.get(BIN_TARGET, {}).get(manager.name)
        if record is None:
            if destination.is_symlink():
                record = _entry(False)
            elif destination.exists():
                record = _entry(True, _back_up(destination, BIN_TARGET,
                                               manager.name, state_dir, stamp))
            else:
                record = _entry(False)
        wanted = str(manager)
        if not (destination.is_symlink() and os.readlink(destination) == wanted):
            replace_symlink(destination)
            if destination.exists():
                destination.unlink()
            destination.symlink_to(wanted)
        written.setdefault(BIN_TARGET, {})[manager.name] = record

    for target, entries in previous.items():
        root = targets.get(target)
        if root is None:
            continue
        kept = written.get(target, {})
        for relative in sorted(set(entries) - set(kept)):
            if target == SOURCE_TARGET and relative in protected:
                # A file whose source could not be resolved this time. It stays
                # where it is, and it stays in the manifest - dropping it would
                # only move the deletion to the next deploy.
                if (Path(root) / relative).is_file():
                    written.setdefault(target, {})[relative] = entries[relative]
                continue
            stale = Path(root) / relative
            if stale.is_file():
                stale.unlink()
                _prune_empty(root, relative)

    write_manifest(state_dir, written)
    return {target: sorted(entries) for target, entries in written.items()}


# A `schema_list` entry in the default.custom.yaml a deploy writes. This is how
# the rebuild check learns which schemas to expect in the build directory: it
# reads the file Rime reads, so a schema withheld from this deploy because its
# tables could not be fetched is not then reported as one Rime failed to build.
_SCHEMA_LINE = re.compile(r"^\s*-\s*\{\s*schema:\s*([A-Za-z0-9_.-]+)\s*\}\s*$")


def deployed_schemas(rime_dir) -> list[str]:
    """The schema ids in the `default.custom.yaml` on disk, in order."""
    try:
        text = (Path(rime_dir) / "default.custom.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    return [
        match.group(1)
        for match in (_SCHEMA_LINE.match(line) for line in text.splitlines())
        if match
    ]


def newest_write(installed: dict[str, list[str]], targets: dict) -> float:
    """The mtime of the most recently written of the installed files.

    This is what `redeploy` measures a build against: a compiled schema older
    than the newest source file is a schema Rime has not rebuilt yet. A deploy
    that changed nothing leaves every mtime where it was, so the check passes at
    once and costs nothing - which is exactly right, because nothing needed
    building.
    """
    newest = 0.0
    for target, relatives in installed.items():
        root = targets.get(target)
        if root is None:
            continue
        for relative in relatives:
            try:
                newest = max(newest, (Path(root) / relative).stat().st_mtime)
            except OSError:
                continue
    return newest


def _run(command) -> subprocess.CompletedProcess:
    """The real runner. Every caller in the tests replaces it."""
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
    )


def _journal_window(runner) -> list[str]:
    """Arguments that limit the journal to what happens from now on.

    A cursor is exact. `--since` is only accurate to the second, so it is the
    fallback: it can drag in a line written moments before the trigger, which
    would blame this redeploy for the previous one's failure.
    """
    try:
        result = runner(["journalctl", "--user", "-u", UNIT, "--lines", "0",
                         "--show-cursor", "--no-pager"])
    except Exception:
        result = None
    if result is not None and result.returncode == 0:
        for line in (result.stdout or "").splitlines():
            if line.startswith("-- cursor:"):
                cursor = line.split(":", 1)[1].strip()
                if cursor:
                    return ["--after-cursor", cursor]
    return ["--since", datetime.now().strftime("%Y-%m-%d %H:%M:%S")]


def _fcitx5_running(runner) -> bool | None:
    """Whether fcitx5 holds its bus name.

    None when the question could not be answered at all - no gdbus, or no
    session bus to ask, which is what a deploy run from a TTY looks like.
    """
    try:
        result = runner(list(PRESENCE_COMMAND))
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return "true" in (result.stdout or "").lower()


def _journal_reaches_fcitx5(runner) -> str:
    """Empty when this unit's journal is where fcitx5's build errors will land.

    Otherwise the reason it is not. A fcitx5 started by hand, or by the desktop
    rather than by the unit, logs somewhere else entirely - and a journal that
    was never going to contain the answer reads exactly like a build that went
    well.
    """
    try:
        result = runner(UNIT_STATE_QUERY)
    except Exception as error:
        return f"could not ask systemd about {UNIT} ({error})"
    if result.returncode != 0:
        state = (result.stdout or "").strip() or "inactive"
        return (f"fcitx5 is not running under {UNIT} ({state}), so its build"
                " log is not in that unit's journal")
    return ""


def _build_errors(runner, window: list[str]) -> tuple[list[str], str]:
    """The build failures the journal recorded, and why it could not be read.

    The reason used to be swallowed: a journalctl that was missing, slow, or
    refused returned an empty list, and an empty list was read as "nothing went
    wrong". Those are different answers and only one of them is good news.
    """
    try:
        result = runner(["journalctl", "--user", "-u", UNIT, "--no-pager",
                         "--output", "cat", *window])
    except Exception as error:
        return [], f"could not read the journal ({error})"
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"exit status {result.returncode}"
        return [], f"could not read the journal ({detail})"
    return [
        line.strip()
        for line in (result.stdout or "").splitlines()
        if any(marker in line for marker in BUILD_ERRORS)
    ], ""


def unbuilt(build_dir, schemas, since: float = 0.0) -> list[str]:
    """The deployed schemas Rime has no current compiled file for.

    This is the check that survives a failure nobody enumerated. A build can
    fail without writing a word to the journal - `default.custom.yaml` opens
    with `__include: rime_ice_suggestion:/`, and with rime-ice absent the
    deployer exits non-zero and prints nothing at all, even at GLOG_v=1 - so
    "the journal said nothing" cannot be the whole test for success. What Rime
    built is a fact about the disk.
    """
    if build_dir is None or not schemas:
        return []
    root = Path(build_dir)
    absent = []
    for schema in schemas:
        try:
            # A second of slack: the source file's mtime and the build's are
            # taken from the same clock, but a filesystem with coarse
            # timestamps can report them equal to the second either way.
            fresh = (root / f"{schema}.schema.yaml").stat().st_mtime >= since - 1.0
        except OSError:
            fresh = False
        if not fresh:
            absent.append(schema)
    return absent


def redeploy(runner=_run, sleeper=time.sleep, settle: float = SETTLE_SECONDS,
             poll: float = POLL_SECONDS, schemas=(), build_dir=None,
             since: float = 0.0, patience: float = BUILD_SECONDS) -> dict:
    """Ask Rime to rebuild, then find out whether it did.

    Writing a customization file changes nothing until Rime compiles it, and a
    file Rime refuses to compile takes the whole schema down - which is how a
    single malformed patch key cost a user their input method while every test
    still passed. So the trigger and the verdict belong together.

    `runner` runs a command and returns a CompletedProcess; `sleeper` waits.
    Both are injected so that no test ever reloads an addon or reads a real
    journal. `schemas`, `build_dir` and `since` are what the outcome check needs
    (see `unbuilt`); without them the verdict falls back to the journal alone.

    Returns `{"triggered", "succeeded", "verified", "errors", "detail"}`.

    `succeeded` is false whenever this configuration is not in effect and
    something has to be done about it. It is *true* when fcitx5 is not running:
    nothing is broken there, the files are on disk, and fcitx5 builds them at
    startup - which is the ordinary state of a machine deploying for the first
    time, and telling that user their input may be broken is how they learn to
    ignore the message that matters.

    `verified` says whether the verdict rests on evidence. A journal that could
    not be read, or one belonging to a unit fcitx5 is not running under, is not
    evidence of anything.
    """
    window = _journal_window(runner)

    present = _fcitx5_running(runner)
    if present is False:
        return {
            "triggered": False, "succeeded": True, "verified": False,
            "errors": [],
            "detail": "fcitx5 is not running, so it will build this"
                      " configuration when it starts",
        }

    unusable_journal = _journal_reaches_fcitx5(runner)

    try:
        triggered = runner(REDEPLOY_COMMAND)
    except Exception as error:  # the trigger binary may not be installed
        return {
            "triggered": False, "succeeded": False, "verified": False,
            "errors": [str(error)],
            "detail": "could not run the redeploy command",
        }
    if triggered.returncode != 0:
        message = (triggered.stderr or triggered.stdout or "").strip()
        errors = [line.strip() for line in message.splitlines() if line.strip()] \
            or [f"exit status {triggered.returncode}"]
        # A refusal is only alarming when fcitx5 was known to be there. If the
        # presence probe could not answer either - no session bus, no gdbus -
        # then nothing was running to refuse anything, and the honest report is
        # the same one a fresh machine gets: the files are on disk and fcitx5
        # builds them when it starts. The trigger's own words are still carried,
        # so nothing is hidden by saying so.
        if present is not True:
            return {
                "triggered": False, "succeeded": True, "verified": False,
                "errors": errors,
                "detail": "fcitx5 could not be reached, so it will build this"
                          " configuration when it starts",
            }
        return {
            "triggered": False, "succeeded": False, "verified": False,
            "errors": errors,
            "detail": "the redeploy could not be triggered",
        }

    patience = max(patience, settle)
    waited = 0.0
    errors: list[str] = []
    unreadable = unusable_journal
    absent: list[str] = []
    while True:
        sleeper(poll)
        waited += poll
        found, problem = _build_errors(runner, window)
        if not unusable_journal:
            unreadable = problem
        errors = found
        if errors:
            break
        absent = unbuilt(build_dir, schemas, since)
        if not absent and waited >= settle:
            break
        if waited >= patience:
            break

    checked_outcome = build_dir is not None and bool(schemas)
    verified = checked_outcome or not unreadable

    if errors:
        return {
            "triggered": True, "succeeded": False, "verified": True,
            "errors": errors,
            "detail": "Rime refused to build the configuration",
        }
    if absent:
        return {
            "triggered": True, "succeeded": False, "verified": True,
            "errors": [f"Rime did not build: {', '.join(absent)}"],
            "detail": "Rime was asked to rebuild and did not",
        }
    if not verified:
        return {
            "triggered": True, "succeeded": True, "verified": False,
            "errors": [],
            "detail": f"Rime was asked to rebuild, but {unreadable}",
        }
    return {
        "triggered": True, "succeeded": True, "verified": True,
        "errors": [],
        "detail": "Rime rebuilt the schemas",
    }


def uninstall(state_dir: Path, targets: dict[str, Path]) -> dict:
    """Put back what this plugin replaced, remove what it created.

    Returns `{"removed", "restored"}`, each grouped by target name.

    The two halves are the whole point. Deleting every path the manifest listed
    took away `~/.config/fcitx5/conf/xcb.conf` and `conf/clipboard.conf`, which
    omarchy-settings ships and Omarchy deliberately configured - the plugin had
    only written over them. A file that was there before this plugin arrived is
    restored from the backup taken when it was first written over; a file this
    plugin created is deleted, and the directories that leaves empty go with it.
    """
    state_dir = Path(state_dir)
    backups = state_dir / "backups"
    removed: dict[str, list[str]] = {}
    restored: dict[str, list[str]] = {}
    for target, entries in read_manifest(state_dir).items():
        root = targets.get(target)
        if root is None:
            continue
        for relative in sorted(entries):
            record = entries[relative]
            path = Path(root) / relative
            backup = record.get("backup") if record.get("replaced") else None
            # `read_manifest` has already refused a backup path that escapes on
            # its face; `_inside` refuses the one that escapes through a symlink
            # planted in the backup directory. Nothing outside that directory is
            # ever a source for this copy.
            source = _inside(backups, backup) if backup else None
            if record.get("replaced"):
                # A file that was here before this plugin arrived. It is put
                # back if the backup can be reached, and otherwise left exactly
                # where it is: a backup that has gone missing - deleted by hand,
                # or refused because the path it names leads out of the backup
                # directory - is a reason to touch nothing, never a licence to
                # delete something that was never ours.
                if source is not None and source.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    replace_symlink(path)
                    shutil.copy2(source, path)
                    restored.setdefault(target, []).append(relative)
            elif path.is_file() or path.is_symlink():
                path.unlink()
                _prune_empty(root, relative)
                removed.setdefault(target, []).append(relative)
    # The manifest goes rather than being blanked. It is what everything else
    # asks "has this ever been deployed?", and a file saying "nothing" still
    # answers yes to that question.
    (state_dir / MANIFEST_NAME).unlink(missing_ok=True)
    return {
        "removed": {target: sorted(paths) for target, paths in removed.items()},
        "restored": {target: sorted(paths) for target, paths in restored.items()},
    }
