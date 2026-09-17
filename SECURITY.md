# Security

This document is for someone deciding whether to trust `omarchy.ime` on a machine
they are responsible for. It says what the plugin touches, with what authority,
what it fetches from the network and how that is bounded, and what it
deliberately does not do. Every claim below names the mechanism that makes it
true, so that it can be checked rather than believed; where something is not
checked, it says so instead.

There is no sandbox here. This is a bar widget plus a Python command line, both
running as the desktop user. What follows is an account of a small blast radius,
not of an isolated one.

## What runs, and where

| Piece                                            | What it is                                         | When it runs                                        |
| ------------------------------------------------ | -------------------------------------------------- | --------------------------------------------------- |
| `Panel.qml`                                      | The bar widget                                     | While the bar is up                                 |
| `ime-manager`                                    | A Python 3 command line, stdlib only               | When a person or the panel asks it to               |
| `~/.config/omarchy/hooks/theme-set.d/omarchy.ime` | A three-line shell shim, installed only on request | On every Omarchy theme change                       |
| `payload/rime/lua/annotate.lua`                  | A librime-lua filter                               | Inside `fcitx5`, on every keystroke while composing |

`Panel.qml` performs no filesystem writes at all. Everything the plugin does to
a machine goes through `ime-manager`, which is one file tree to audit rather
than two. The panel's only privilege is to run `ime-manager` and a handful of
read-only queries (`fcitx5-remote`, `gdbus call … GetCurrentSchema`), each as an
argument vector; there is no shell anywhere in the plugin.

## What it writes

`ime-manager deploy` writes into exactly five directories, and records every
file it writes in an install manifest so that `ime-manager uninstall` can undo
it:

| Target        | Directory                                               |
| ------------- | ------------------------------------------------------- |
| `rime`        | `$XDG_DATA_HOME/fcitx5/rime`                            |
| `fcitx5`      | `$XDG_CONFIG_HOME/fcitx5`                               |
| `themes`      | `$XDG_DATA_HOME/fcitx5/themes/omarchy-ime`              |
| `environment` | `$XDG_CONFIG_HOME/environment.d/10-omarchy-fcitx.conf`  |
| `hooks`       | `$XDG_CONFIG_HOME/omarchy/hooks/theme-set.d/omarchy.ime` |

It also writes `$XDG_CONFIG_HOME/omarchy/shell.json` — its own entry on the bar,
and only the keys it was asked to change — and caches downloads under
`$XDG_CACHE_HOME/omarchy-ime`. State lives in `$XDG_STATE_HOME/omarchy-ime`.

Nothing is written outside the user's home. The only path under `/` the plugin
names at all is `/etc/vconsole.conf`, which it reads to find the console keymap
so that a freshly written Fcitx5 profile does not put a German keyboard back on
a US layout; `tests/test_packages.py` asserts that this is the only such path in
the tree and that the function reading it contains no write call.

**Files this plugin did not create are restored, not deleted.** Omarchy ships
some of the files under `~/.config/fcitx5`, and the Fcitx5 `profile` is the
user's own. When a deploy writes over a file that was already there, it copies
the original into `$XDG_STATE_HOME/omarchy-ime/backups` first and records in the
manifest that this was a replacement. `uninstall` puts those back and deletes
only what the plugin created. If a backup cannot be reached, the file is left
exactly where it is — a missing backup is a reason to touch nothing.

**Modes are preserved, never widened.** `lib/deploy.install` reads the mode of
whatever is at a destination before writing and restores it afterwards, because
`fcitx5` keeps `profile` and its conf files at `0600` while a git checkout is
`0644`. The state directory and the backups under it are `0700`: they hold
copies of the user's own configuration and the manifest that says what an
uninstall may delete. `tests/test_security.py::PermissionTests` covers all
three.

**Symlinks at a destination are not followed.** Every write goes through
`lib/deploy.replace_symlink` first, which removes a symlink standing where a
regular file is about to be written. Without it a link planted at, say,
`~/.config/fcitx5/conf/classicui.conf` would send a generated file wherever it
pointed — and, on uninstall, would send the user's own backed-up content there
too. The rule holds for the deploy, the restore, the backup destination, the
theme hook's unattended write, and the hook file itself.

`settings.save` replaces `shell.json` through a temporary file in the same
directory, created with `O_EXCL | O_NOFOLLOW` so that a symlink planted at the
predictable `shell.json.<pid>.tmp` cannot redirect the write, and with the
mode the existing file had so that a save never tightens or loosens it.

## The install manifest is validated when it is read

This is the part most worth a reviewer's attention, because it is the part that
was wrong.

`$XDG_STATE_HOME/omarchy-ime/installed.json` records every file the plugin
wrote, whether it created or replaced each one, and where the backup of a
replaced file lives. `uninstall` reads it in a later process and acts on every
path it names: it unlinks what the manifest calls a created file, and copies a
backup over what it calls a replaced one. The whitelist that decides what a
deploy may write runs at write time and is long gone by then.

So the paths are checked where they are read, in `lib/deploy._safe_relative`:
an entry whose path is absolute, contains `..`, or contains a NUL is dropped,
and an entry whose `backup` has any of those shapes is dropped whole. At restore
time the backup path is additionally resolved and required to land inside the
backups directory (`lib/deploy._inside`), which is what catches a lexically
innocent path that leaves through a planted symlink. `_prune_empty`, which
removes the directories a deleted file leaves empty, resolves both sides and
checks containment before it walks upward.

Before this, a manifest entry naming `../../../.bashrc` was a delete, and one
naming a `backup` outside the backup directory was an arbitrary read followed by
an arbitrary write. `tests/test_security.py::HostileManifestTests` covers each of
those, plus the deploy-time stale-file sweep, which is the manifest's other
reader.

## What it downloads

Three things, all onto the user's own machine, none of them redistributed by
this repository.

**Two Rime sources, pinned by commit.**
`github.com/gkovacs/rime-japanese` and `github.com/snomiao/rime-snomiao`, fetched
only when a Japanese schema is selected — a user who never picks one never
touches the network for them. The fetch is `git fetch --depth 1 <url> <sha>`
followed by `git checkout FETCH_HEAD`: the object name is the pin, so what
arrives is what hashes to that commit, and substituting content means finding a
SHA-1 collision. The pin is part of the cache path, so bumping it is a new
directory rather than an update in place. `GIT_TERMINAL_PROMPT=0` and an askpass
override mean a repository that asks for credentials fails rather than hangs a
deploy behind an invisible prompt, and `FETCH_TIMEOUT` bounds a stalled one.

Only a named list of files is copied out of each clone
(`lib/assets.SOURCE_FILES`) — the repositories carry schemas this plugin does not
deploy, and Rime loads whatever it finds. Each file must be a regular file, not
a symlink, and no larger than `MAX_SOURCE_FILE_BYTES` (64 MB), or it is treated
exactly as a file the fetch did not produce (`lib/deploy._usable_source`). The
symlink check is what stops a hostile pinned revision whose `opencc/zhjp.json`
is a link to `~/.ssh/id_ed25519` from having that file copied into the Rime
directory.

**CC-CEDICT, from its publisher MDBG.** The annotation dictionary, downloaded
only while at least one annotation direction is on. The URL is required to be
`https` on `mdbg.net` or `www.mdbg.net` (`lib/assets.require_secure_url`,
`DICTIONARY_SCHEME`, `DICTIONARY_HOSTS`), through an opener
that permits no redirect to another scheme or host. The transfer is bounded on
both sides of decompression — `MAX_COMPRESSED_BYTES` (32 MB) and
`MAX_DECOMPRESSED_BYTES` (96 MB) — so neither a very large file nor a zip bomb
can fill a disk. What lands is then checked structurally before it is accepted:
the CC-CEDICT banner, the required `#!` metadata fields, the declared format and
charset, and a minimum number of lines that parse as entries. Only then is it
moved into place, and the completion marker is written last, so a directory
without one is a download that died partway and is discarded rather than
trusted. A refused or failed download keeps the copy already on disk.

MDBG publishes one URL that always serves the current release and no URL for an
older one, so there is nothing to pin to. The header's own `#! date=` is
recorded as a note of what is cached, not as a pin, and the cache is re-checked
after `MAX_AGE_DAYS` (30).

**Nothing downloaded is ever executed.** Every fetched path is a `.yaml`,
`.json`, `.txt` or `.u8` data file consumed by librime or OpenCC. No Lua is
fetched: `payload/rime/lua/annotate.lua` ships in this repository, and it reads
the dictionary with `io.open` and contains no `load`, `loadstring`, `loadfile`,
`dofile`, `require`, `os.execute` or `io.popen`. Both halves of that are
asserted in `tests/test_security.py::HostileUpstreamTests`.

## The annotation filter runs on the input path

`annotate.lua` is the one piece of this plugin that runs inside `fcitx5`, on the
thread that answers keystrokes. A loader that takes minutes over a hostile line
is a desktop with no working keyboard for those minutes and no visible reason
why, so the file it parses is treated as hostile input rather than as data from
a publisher.

Two of its rewrites were quadratic in the length of a single dictionary sense —
`(<chinese>)%[[^%]]*%]` and `[^%s%p]*|`, each a `gsub` whose leading `*` repeat
scans to the end of the sense and backtracks the whole way at every byte. One
crafted line of 40,000 bytes took 7.5 seconds; 160,000 took two minutes, and the
cost squares with the line. Both are now single linear scans, verified identical
to the patterns they replace over 300,000 random strings. On top of that the
loader skips any line longer than `MAX_LINE_BYTES` (8 KB) — the longest line of
the published file is under 2 KB — which is a bound it can state on its own work
rather than one every pattern in it has to hold alone.

The filter decodes no UTF-8: every test it makes is a byte-class test, so
invalid sequences are skipped rather than raising. Gloss width is capped at 54
columns because `classicui` has no wrap or elide option and sizes its window to
whatever it is asked to draw.

## Settings are data, everywhere they land

`shell.json` is hand-editable by design, and `settings.resolve` is deliberately
lenient — a typo there must not stop someone's input method from deploying. That
makes it the wrong place to trust, so every value is escaped or checked at the
point where it lands in a file another program parses:

- **`annotateHotkey`** is interpolated into a YAML document librime compiles. It
  goes through `generate._quote`, which emits a double-quoted YAML scalar with
  `\`, `"`, newline, carriage return and tab escaped. A bare `"` used to close
  the scalar early and fail the build of every selected Chinese schema.
- **`backgroundImageLight` / `backgroundImageDark`** are absolute paths written
  verbatim into `theme.conf`, which `classicui` parses one line at a time — so a
  newline in one is the next key, and a newline and a `[` is the next section.
  `resolve` now answers "no image" for any stored path that is not absolute or
  that carries a control character (`settings.usable_image_path`); `validate`
  refuses both loudly when someone asks for one on purpose, on top of checking
  that the file exists and that its first bytes match its extension.
- **The Omarchy theme name**, read from
  `~/.local/state/omarchy/current/theme.name`, lands in the same file's
  metadata. Every value written into `theme.conf` now goes through
  `theme._ini_value`, which collapses control characters to a space, so a key
  added to that file later inherits the escaping rather than having to remember
  it.
- **The dictionary path** is derived from `XDG_CACHE_HOME` and is quoted the
  same way the hotkey is.
- **Schema ids** are checked against a fixed catalogue before they reach
  `schema_list`; an id read back off the D-Bus interface is held to
  `[A-Za-z0-9][A-Za-z0-9_.-]*` before it is joined onto a path.

`tests/test_security.py::HostileSettingsTests` and `HostileThemeNameTests` cover
these.

## The D-Bus surface

`lib/imestate` talks to `fcitx5` through `gdbus call` and reads the GVariant
text it prints. That text is **scanned, never evaluated**: `_quoted_strings`
accepts quoted strings and the punctuation between them and returns `None` for
anything else — a number, a boolean, an unterminated quote, a Python
expression — and every caller treats `None` as "not the shape that was asked
for". A schema id off the bus is then held to the character class above before
it names a file. `tests/test_security.py::BusReplyTests` feeds it the shapes
that would matter if it were an `eval`.

Arguments _to_ `gdbus` are quoted as GVariant string literals
(`imestate.quote_variant`) so that a schema called `true` arrives as a string
rather than a boolean. That is about the type, not about metacharacters: the
argument list is handed to `execve`, and no shell is involved.

## The theme hook

`ime-manager hook install` writes a three-line shim into
`~/.config/omarchy/hooks/theme-set.d/`, which `omarchy-hook` runs as
`bash "$hook" "$THEME_NAME"` on every theme change. The theme name arrives as
`$1` **and is never expanded**: the shim runs `ime-manager theme apply`, which
reads whichever palette Omarchy has just made current — the same answer, and one
that is also right when the hook is run by hand. The only expansion in the
script is the manager's own path, written through `shlex.quote` and used only
inside double quotes.

Installing the hook is part of the plugin's lifecycle, so it is recorded in the
same manifest as everything else and `ime-manager uninstall` takes it away. No
hook is left behind pointing at a plugin that is gone.

## What this plugin deliberately does not do

- **No sudoers rule, no drop-in, no `visudo`.** No `sudo`, `pkexec` or `pacman`
  invocation of its own, on any path. Package installation is Omarchy's own
  commands, run the way a user would run them, inheriting whatever policy the
  system has: `omarchy pkg add` / `omarchy pkg aur add` from a terminal, and
  `omarchy-pkg-add` / `omarchy-pkg-aur-add` inside the one-liner handed to
  Omarchy's floating-terminal launcher when there is no tty for `sudo` to prompt
  on. The elevation is theirs, not this plugin's. Nothing privileged happens as
  part of a deploy; it is behind an explicit `ime-manager packages install`.
  Asserted in `tests/test_packages.py::PrivilegeSurfaceTests`.
- **No service lifecycle changes.** The only `systemctl` command anywhere is
  `systemctl --user is-active omarchy-fcitx5.service`, which asks and changes
  nothing. No start, stop, restart, enable, disable, mask or daemon-reload:
  `fcitx5`'s lifecycle belongs to the session that launched it, and a plugin
  that could take it over is a plugin that could leave someone unable to type.
- **No execution of downloaded content.** See above.
- **No `shell=True`, `os.system`, `os.popen` or `subprocess.getoutput`
  anywhere.** Every subprocess is an argument vector.
  `tests/test_security.py::SubprocessSurfaceTests` scans `lib/`, `ime-manager`
  and `Panel.qml` for all four, and asserts that the scan really matched those
  files. The single place the plugin composes shell _text_ is
  `packages.install_script`, a one-liner for a terminal launcher that takes one;
  every package name in it is filtered against the plugin's own constant lists
  before it is `shlex.quote`d, so nothing a caller passes can reach it.
- **No writes to `~/.config/hypr`.** The keybind and the blur rule a complete
  setup wants are detected and reported, never generated. The only drop-in
  Omarchy offers for those is executable Lua in the compositor's configuration,
  which is not a thing this plugin will write for a keybind.
- **No telemetry, no analytics, no network call other than the three fetches
  described above.**

## Known limits

Stated because they are real, not because they are comfortable.

- **What a hostile _pinned_ Rime source can do inside librime is not bounded by
  this plugin.** The fetch is content-addressed and the file list is a
  whitelist, and this plugin has verified that no fetched file is Lua and that
  none is a symlink or absurdly large. But a `.dict.yaml` or `.schema.yaml` is
  then compiled by librime, and an OpenCC `.json` is loaded by OpenCC; whether
  either of those can be made to write outside the Rime directory by a crafted
  `dictionary:` or `import_tables:` name has **not** been verified here. The
  practical protection is that changing what those files say requires either
  compromising the two upstream repositories _and_ a maintainer bumping the pin
  after reading the diff, or a SHA-1 collision.
- **A same-uid attacker is not defended against in general.** The measures above
  — validating the manifest on read, refusing to follow symlinks at
  destinations, `O_EXCL` on the temporary file — raise the cost of a local race
  and close the specific paths where this plugin would have amplified one into a
  write outside its own directories. They do not make the plugin safe against a
  process that already runs as the user, which could simply edit the files
  directly.
- **The `annotate.lua` bounds are on line length and on the two rewrites that
  were quadratic.** The loader was not exhaustively proven linear; the other
  patterns in it were reasoned about and measured on crafted inputs, not
  formally analysed. A file within `MAX_LINE_BYTES` per line and within the
  download bounds cannot reach the two known cases, and the adversarial tests
  run in well under a second where they previously timed out.
- **`Panel.qml` is QML running in the bar's own process.** Its behaviour is
  bounded by Quickshell, not by anything here. It performs no filesystem writes
  and builds every command as an argument vector, which is the whole of what
  this repository can assert about it.
- **`SECURITY.md` describes the tree as it stands.** If a claim here and the
  code disagree, the code is the fact and the claim is the bug — please report
  it as one.

## Reporting a vulnerability

Open a security advisory on the repository:
<https://github.com/eastgold15/omarchy-ime/security/advisories/new>. That keeps the
report private until there is something to say publicly.

If advisories are unavailable to you, open an issue that says only that you have
a security report and how to reach you — please do not put the details in a
public issue.

Please include what you were able to make happen, on which paths, and the
smallest input that does it. A failing test against `tests/test_security.py` is
the most useful possible form of a report, and the fastest to act on.

There is no bounty. Reports are acknowledged, and a fix ships with a test that
would have caught it.
