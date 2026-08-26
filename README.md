# Omarchy IME

An Omarchy bar plugin that owns the whole input method: it installs Fcitx5 and
Rime, generates their configuration from a handful of toggles, and puts a live
input-state indicator on the bar in place of `omarchy.keyboard-layout` — because
what decides whether a keystroke becomes Chinese is the Fcitx5 state, not the
keyboard layout.

On top of that it offers the feature that motivated the plugin, off by default:
turn on **Candidate annotation** and candidates are annotated with their
meaning in the other language — an English gloss on Chinese candidates, a
Chinese gloss on English ones — toggled with one key while you are looking at
the candidate list. The base install is Rime Ice with nothing extra; the
annotation filter, its dictionary and its schema patches arrive only when the
switch is on.

Plugin id: `ryuhzk.ime`.

## Features

- **Bidirectional candidate annotation** (optional, off by default) — an
  English gloss on Chinese candidates, a Chinese gloss on English ones, both
  behind one Rime switch toggled by a hotkey scoped to composing. Turn it on
  with the *Candidate annotation* switch in the panel or
  `ime-manager settings set annotation=true`
- **Input-state indicator** — the bar shows the live Fcitx5 state rather than
  the keyboard layout, and takes the place of `omarchy.keyboard-layout`. It
  prints `󰌌` when Fcitx5 is not running, `EN` while keystrokes pass through as
  latin, and, while a schema is composing, that schema's own name cut to what
  fits beside a clock — one character when the name starts with a CJK codepoint
  and two otherwise, so `Japanese` shows as `Ja`. The name is whatever Rime
  compiled into that schema; none is written here. Opening the panel freezes the
  Fcitx5 half of the reading, since a reading taken while the panel holds the
  keyboard would describe the panel's own input context rather than the window
  you came from — the schema keeps updating, because no input context owns it
- **Generated configuration** — Rime and Fcitx5 configuration written whole from
  a handful of toggles on every deploy, so a repeat deploy is a no-op
- **Optional Japanese input** — two extra Rime schemas, with their dictionaries
  cloned from two repositories pinned by commit, fetched only when the setting
  is on
- **Traditional output** — Taiwan-style traditional via the `s2twp` OpenCC
  config, on by default and switchable off
- **Candidate window follows the Omarchy theme** — the classicui theme is
  generated from whichever Omarchy palette is switched on, and an optional
  theme-set hook repaints it the moment you switch
- **The `QT_IM_MODULE` override** — the plugin owns
  `~/.config/environment.d/10-omarchy-fcitx.conf`, which is what keeps Qt on the
  same native Wayland protocol GTK uses. Deployed, backed up, and removed again
  by uninstall; it takes effect at the next login
- **Honest about what it does not own** — the two Hyprland pieces of a working
  setup, the input-method toggle key and the candidate window's blur rule, are
  detected and reported rather than written into your compositor's configuration
- **Explicit package handling** — Fcitx5 and Rime Ice are installed through
  Omarchy's own package commands, never as a side effect of a deploy, and in a
  terminal you can answer the sudo prompt in
- **Verified rebuild** — a deploy asks Rime to recompile and then checks both
  what it complained about and what it actually built, so a configuration Rime
  refuses — or one it fails to build without saying a word — is a failed deploy
  rather than a silently broken input method. Deploying before fcitx5 has ever
  run is not a failure and does not pretend to be one
- **Reversible** — every written file is recorded along with whether the plugin
  created it or wrote over something; uninstall deletes the first and restores
  the second, so files Omarchy shipped and your own Fcitx5 profile come back

## Requirements

- Arch Linux
- Omarchy with the Quickshell shell (`omarchy plugin` and `omarchy bar` available)
- Python 3 (standard library only — no third-party packages are installed)

You do **not** need to install the input method yourself. The plugin installs its
own packages through Omarchy's package commands:

| Package | Source | Command used |
|---|---|---|
| `fcitx5` | official | `omarchy pkg add` |
| `fcitx5-configtool` | official | `omarchy pkg add` |
| `fcitx5-rime` | official | `omarchy pkg add` |
| `noto-fonts-cjk` | official | `omarchy pkg add` |
| `rime-ice-git` | AUR | `omarchy pkg aur add` |

`noto-fonts-cjk` is an Omarchy base package, so on Omarchy it is already there.
It is listed because the generated `conf/classicui.conf` names one of its faces
as the candidate window's font, and a candidate window drawing CJK in a face
that has no CJK glyphs shows boxes.

Package installation is the one privileged thing here, so it never happens
implicitly: nothing is installed as a side effect of a deploy.

It also does not happen inside the panel. `omarchy pkg add` runs
`sudo pacman -S`, there is no passwordless rule for it, and a Quickshell
`Process` has no terminal for sudo to prompt on — so the Install button opens a
floating terminal and runs the install there, the same way Omarchy's own
`omarchy install app` does. The panel says that is what it did rather than
claiming the packages are installed: press the refresh button once the terminal
has finished. Running `ime-manager packages install` from a shell installs in
that shell instead, because there is already a terminal to answer in.

The plugin reports what is missing package by package — asking about each one
separately, so a machine missing only `fcitx5-rime` is told that and not that
all three are gone — and installs only on an explicit action. A refusal or a
failure still leaves the rest of the deploy to run against whatever is already
installed, and whatever pacman or the launcher said comes back with it instead
of being captured and dropped.

`librime-lua` is not a new dependency — Rime Ice already uses it, so the
annotation filter rides on what is there.

## Install

```bash
omarchy plugin add https://github.com/ryuhzk/omarchy-ime --enable --yes
```

Or from a local clone, which is how this was first installed for real:

```bash
omarchy plugin validate ~/Work/omarchy-ime
omarchy plugin add file://$HOME/Work/omarchy-ime --enable --yes
```

Either way the plugin ends up at `~/.config/omarchy/plugins/ryuhzk.ime`, which
is where the commands below expect it, and `--enable` registers the widget on
the bar.

### Putting the widget where you want it

`--enable` places the widget in the plugin's default section. Move it wherever
it belongs:

```bash
omarchy bar move ryuhzk.ime --after omarchy.clock
```

**The stock keyboard-layout indicator does not disappear on its own.** This
widget is meant to take its place, but nothing removes the old one for you: if
you were using `omarchy.keyboard-layout`, delete its entry from `bar.layout` in
`~/.config/omarchy/shell.json` by hand, or you will have both on the bar. There
is no `omarchy bar` subcommand that removes a widget.

### Settings come after the widget, not before

Change settings only once the widget is actually on the bar. Settings live
inline on the widget's own entry in `shell.json`, so if there is no entry there
is nowhere to write them, and `ime-manager settings set` refuses rather than
inventing one:

```
ime-manager: ryuhzk.ime is not on the bar in /home/you/.config/omarchy/shell.json; add it with `omarchy bar` first
```

That is the message when `shell.json` exists and does not mention this plugin.
If the file is not there at all, the read fails before the entry is looked for
and you get that instead:

```
ime-manager: cannot read /home/you/.config/omarchy/shell.json: [Errno 2] No such file or directory: '/home/you/.config/omarchy/shell.json'
```

This is deliberate, and it is the first thing a new user hits. Reading is
different: `ime-manager settings get` works at any time and answers with the
defaults when the widget is not on the bar.

### Migrating from an existing setup

**Set the features you already have before the first deploy.** A deploy writes
the configuration whole from the settings, so a feature that is off in the
settings is a feature that is gone from the generated files — no matter what
your old configuration had. The one that bites is `schemas`, which defaults to
`rime_ice` alone: deploy with defaults and every other schema drops out of
`schema_list`.

So if you use more than full pinyin today, select them first:

```bash
IME=~/.config/omarchy/plugins/ryuhzk.ime/ime-manager
$IME schema available            # every schema this Rime install offers
$IME settings set schemas=rime_ice,japanese_tw_eng,sno_japanese
```

A plugin that already had the retired `japanese: true` setting is migrated on
first read: the two Japanese schemas are carried into the selection, so an
existing install keeps what it had.

The same goes for anything else you rely on — check the
[Settings](#settings) table against what you have and set it now rather than
after the first deploy.

### Packages, then deploy

```bash
~/.config/omarchy/plugins/ryuhzk.ime/ime-manager packages install
~/.config/omarchy/plugins/ryuhzk.ime/ime-manager deploy
```

Deploy resolves your settings, downloads the annotation dictionary if candidate
annotation is on (with at least one gloss direction), fetches the pinned
sources the enabled features need, writes the Rime configuration into `~/.local/share/fcitx5/rime/`, installs
the Fcitx5 configuration into `~/.config/fcitx5/`, its theme files into
`~/.local/share/fcitx5/themes/` and the environment override into
`~/.config/environment.d/`, and prints every path it installed, grouped by
target.

One of those files does nothing until you log in again. systemd reads
`environment.d` when a session starts, so the `QT_IM_MODULE` override the deploy
writes takes effect at your **next login** and not before — the deploy says so,
and so does the panel.

Your Fcitx5 `profile` is **merged, not written**. It carries your keyboard
layout and every input method you have set up, so the deploy adds Rime to the
group that is already there and makes it the group's default; the layout, the
group's name, and every other item stay as you left them. On a machine with no
profile yet, one is written using the layout `XKBLAYOUT` in
`/etc/vconsole.conf` names — the same place Omarchy reads it for Hyprland's
`kb_layout`.

Anything the deploy could not do, it says. Lines beginning `warning:` are the
ones worth reading: a theme that could not be read, so the candidate window has
fallen back to a neutral palette; a source that could not be fetched, so a
schema was withheld from this deploy; a setting that reaches only some of the
selected schemas. The panel shows them under the deploy result rather than
folding them into a count.

Then it asks Rime to rebuild, and checks that it did. Writing the files is only
half a deploy: Rime reads its customization files at build time, not at
keystroke time, so a file on disk that has not been compiled is a file that is
not in effect — and one Rime *refuses* to compile takes the whole schema down
with it, which is how a single malformed patch key can cost you your input
method while every test still passes.

The rebuild is a `ReloadAddonConfig` call on Fcitx5's D-Bus controller, aimed at
the `rime` addon. That makes fcitx5-rime tear down its librime session and
recompile every schema whose source changed, without dropping the input context
of every open application the way restarting the unit would.

The verdict rests on two things, because neither one is enough on its own.

**What Rime complained about.** The `omarchy-fcitx5.service` journal is watched
from a cursor taken just before the trigger, and matched against the error
strings librime actually emits — the config compiler's four, plus the dictionary
compiler's and the deployment tasks', which is where a missing download lands.
All of them were read out of `strings /usr/lib/librime.so.1` rather than
guessed.

**What Rime built.** A silent journal is not proof of anything: with
`rime-ice-git` absent, `default.custom.yaml`'s opening
`__include: rime_ice_suggestion:/` makes the deployer exit non-zero and print
nothing at all, leaving `build/` empty. So the schemas the deploy just wrote into
`schema_list` are looked for in `~/.local/share/fcitx5/rime/build/`, each one
required to be at least as new as the newest file the deploy wrote. A deploy
that changed nothing passes that at once; a first deploy is waited on for as long
as compiling rime_ice's tables takes.

Three outcomes, and they are told apart:

- **Rebuilt.** Both checks agree, and `ime-manager deploy` exits 0.
- **Refused.** It exits non-zero, prints the lines Rime wrote to stderr, and says
  plainly that the configuration is on disk but is **not** in effect. That is the
  failure worth being loud about — it is the one that leaves someone unable to
  type.
- **Not yet.** fcitx5 is not on the session bus, so there is nothing to ask and
  nothing is wrong: the files are on disk and fcitx5 builds them when it starts.
  This is the ordinary state of a machine deploying for the first time, and it
  exits 0 saying so. A rebuild that was triggered but whose outcome could not be
  confirmed — no readable journal, or a fcitx5 running outside
  `omarchy-fcitx5.service` — also exits 0 and says which, rather than claiming a
  rebuild it did not see.

### The `QT_IM_MODULE` override

Omarchy ships `/usr/lib/environment.d/10-omarchy-fcitx.conf`, which sets four
variables including `QT_IM_MODULE=fcitx`. A deploy installs a file of the same
name into `~/.config/environment.d/`, which systemd reads afterwards, so it
replaces the packaged one — with `QT_IM_MODULE` deliberately left unset.

The reason is in the generated file's own header, and it is worth repeating
here. Fcitx5's self-diagnosis reports:

```
Using Wayland native input method protocol: 1
GTK_IM_MODULE=
QT_IM_MODULE=fcitx
```

The native protocol (text-input-v3) is up and GTK already uses it, but
`QT_IM_MODULE=fcitx` forces Qt down the older DBus input-context path instead.
With both routes live, a layer-shell surface such as an Omarchy panel gets
whichever one wins the focus race — which is why typing Chinese into one worked
only sometimes. Leaving `QT_IM_MODULE` unset puts Qt on the same native protocol
as GTK.

The other three variables are copied from the packaged file rather than written
out from a list, so a variable Omarchy adds upstream is carried across instead of
being silently lost to the override. `XMODIFIERS` is what XWayland and
Qt-on-xcb fall back to; `SDL_IM_MODULE` and `INPUT_METHOD` have no
Wayland-native equivalent.

It is an ordinary deployed file: recorded in the install manifest, backed up if
something was already there, and removed by `ime-manager uninstall` — which puts
the packaged behaviour back. Both directions take effect at your next login.

### What the first deploy does to files that were already there

Every file a deploy is about to replace is copied first, into

```
~/.local/state/omarchy-ime/backups/<timestamp>/<target>/<path>
```

with one timestamped directory per deploy that had anything to save. On a
machine that already had a hand-built Fcitx5 and Rime setup, the first deploy
backs up everything it overwrites — on the real first run that was dozens of
files spread across every target, the hand-written
`~/.config/environment.d/10-omarchy-fcitx.conf` among them. Only the first
deploy backs up much: after that the manifest records which files are the
plugin's, and a file the plugin already owns is rewritten without a fresh
copy.

**Rime user data is never touched.** The plugin's manifest covers only the files
it generates and the payload and pinned-source files it installs. Your
`*.userdb` directories, `sync/`, `user.yaml`, `installation.yaml`, and `build/`
are outside it — never written, never backed up because never replaced, and
never removed by `ime-manager uninstall`. Your learned phrases and your sync
state survive both a deploy and an uninstall.

### Adding a schema later

Selecting a schema takes effect on the **next** deploy, not on the setting
change. That deploy clones the two pinned repositories into
`~/.cache/omarchy-ime/` — roughly twenty seconds on the real run — installs the
Japanese schemas and their dictionaries, and adds the two schemas to
`schema_list`. Disabling it and deploying again removes those files: they are in
the manifest, so a deploy that stops installing them deletes them.

The clone is shallow, its `.git` is discarded once the checkout is done — the
pin is the cache key, so nothing ever asks that clone for history — and bumping
a pin removes the revision it replaces instead of stacking a second full copy
beside it. The fetch is bounded by a timeout and runs with
`GIT_TERMINAL_PROMPT=0`, so a stalled connection or a repository that wants
credentials fails rather than hanging a deploy that the panel shows as a
spinner with no cancel.

**If the clone fails, nothing you already had is taken away.** The files a
failed source owns are exempt from the stale-file sweep and stay in the
manifest, so deploying with the network down keeps the dictionaries the last
good deploy installed. If those files are not on disk at all, the schemas that
`import_tables:` them are withheld from that deploy rather than written into
`schema_list` for Rime to fail on — and the deploy says so, in a `warning:`
line. Deploy again once you are online.

The clone is cached by commit, so a second deploy with Japanese still on hits
the network not at all.

Saving any file under `~/.config/omarchy/plugins/` reloads plugin code
automatically, so editing the plugin in place takes effect without restarting the
shell.

### Following the Omarchy theme

The candidate window is themed from the Omarchy palette, and that theme is
generated at deploy time — so switching Omarchy themes leaves the candidate
window painted in the old one until the next deploy. Install the theme-set hook
once and it repaints itself instead:

```bash
~/.config/omarchy/plugins/ryuhzk.ime/ime-manager hook install
~/.config/omarchy/plugins/ryuhzk.ime/ime-manager hook status
```

The hook is a three-line shim in `~/.config/omarchy/hooks/theme-set.d/`, named
after the plugin so it cannot collide with another one's. It runs
`ime-manager theme apply`, which rewrites only the files whose contents changed
and reloads Fcitx5's `classicui` addon — no dictionary download, no Rime
rebuild. It cannot fail: `omarchy-hook` prints "Hook failed" for any non-zero
exit, and the ordinary reasons this has nothing to do are not worth saying on
every theme switch. Run `ime-manager theme apply` by hand to see what it would
have said.

`ime-manager hook remove` takes it away, and so does `ime-manager uninstall` —
the hook is in the install manifest like every deployed file, so no hook is left
behind pointing at a plugin that is gone.

### Verifying it works

The end-to-end check is manual:

1. Deploy — it exits non-zero if Rime refused the new configuration — then
   confirm Chinese input works in a normal application.
2. With candidate annotation on, start a composition and confirm the
   annotation appears beside the candidates.
3. Press the annotation hotkey and confirm it disappears, then press it again to
   bring it back.

Steps 2 and 3 depend on the `annotation` setting being on and on the
annotation dictionary having downloaded — see
[The dictionary](#the-dictionary). `ime-manager status` reports both, along
with missing packages, whether anything has been deployed at all, and whether
the two Hyprland pieces below are in effect:

```
packages missing: none
dictionary: present
dictionary path: /home/you/.cache/omarchy-ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt
deployed: yes
theme hook: installed
hyprland candidate blur: on
hyprland input method toggle: on
input: latin
```

`ime-manager status --json` is the same report as JSON, which is what the panel
reads.

## Remove

Order matters here. Most bar plugins keep everything inside their own directory,
so removing the plugin removes the plugin. This one does not: a deploy writes
into `~/.local/share/fcitx5/rime/`, `~/.config/fcitx5/`,
`~/.local/share/fcitx5/themes/` and `~/.config/environment.d/`, none of which
`omarchy plugin remove` knows anything about. So revert the deploy first, while
the plugin is still there to do it, and unregister it second:

```bash
~/.config/omarchy/plugins/ryuhzk.ime/ime-manager uninstall
omarchy plugin remove ryuhzk.ime
```

Every file a deploy writes is recorded in
`~/.local/state/omarchy-ime/installed.json`, so `ime-manager uninstall` puts back
exactly what was there and nothing else. Files you created in the Rime directory
are never touched, and neither is any Rime user data. The theme-set hook goes
with it, and so does the environment override — removing our
`~/.config/environment.d/10-omarchy-fcitx.conf` is what lets the packaged
`/usr/lib` one apply again, from your next login.

The manifest records, for each file, whether the deploy **created** it or wrote
over one that was already there. Uninstall treats the two differently:

- a file this plugin created is deleted, and the directories that leaves empty
  go with it;
- a file this plugin replaced is **restored** from the copy taken the first time
  it was written over, and uninstall says `restored <target>/<path>` for each one.

That distinction is not academic. `~/.config/fcitx5/conf/xcb.conf` and
`conf/clipboard.conf` are shipped by `omarchy-settings`, and your `profile`
carries your keyboard layout and every input method you have configured; an
uninstall that deleted everything in the manifest took those with it.

The backups themselves stay in `~/.local/state/omarchy-ime/backups/<timestamp>/`
after an uninstall, and are left alone by `omarchy plugin remove` too, so they
are still there after the plugin is gone.

Uninstall also removes `~/.cache/omarchy-ime/` — the CC-CEDICT download and the
cloned Rime sources, which together are the largest thing this plugin puts on a
machine by two orders of magnitude — and says how much that was. Pass
`--keep-cache` to leave it, which saves the download if you plan to reinstall.

`omarchy plugin remove` then unregisters the plugin and deletes its installed
files. Running it first strands the generated configuration in those four
directories with the tool that reads the manifest already gone — recoverable, but
only by working through `installed.json` by hand.

The packages are deliberately left alone. Removing a bar plugin should not
uninstall `fcitx5`, `fcitx5-configtool`, `fcitx5-rime`, or `rime-ice-git` out
from under a system that may now depend on them; remove them yourself if you want
them gone.

Settings live inline on the widget's entry in `~/.config/omarchy/shell.json`;
removing the widget from the bar removes them with it.

## Settings

Settings live inline on the widget's own entry in `~/.config/omarchy/shell.json`,
matched by the entry whose `id` is `ryuhzk.ime`. There is no separate config
file, and Omarchy hot-reloads that file on save. Every key is optional: a
`shell.json` that is missing, damaged, or does not mention this plugin yields the
defaults rather than an error, and a value of the wrong type falls back to its
default so a typo cannot stop the input method from deploying.

| Key | Default | What it does |
|---|---|---|
| `schemas` | `["rime_ice"]` | Which Rime schemas are written to `schema_list` and deployed. A collapsed multi-select over everything this Rime install offers — full pinyin, seven double-pinyin layouts, zhuyin, shape codes, Japanese. Names are read from each schema's own yaml, never hard-coded. Deselecting one removes its files on the next deploy; the Japanese schemas also stop fetching their pinned sources. At least one Chinese schema always survives. |
| `annotation` | `false` | The master switch over candidate annotation. Off by default: a fresh install ships Rime Ice with nothing extra. On installs the Lua filter, downloads CC-CEDICT, and patches every selected Chinese schema with the filter, its switch and the hotkey binding; off installs none of that and removes it on the next deploy, leaving the cached dictionary alone. The four `annotate*` settings below keep their values while it is off. |
| `annotateChinese` | `true` | English glosses on Chinese candidates. Written into the schema as `annotate/chinese`. |
| `annotateEnglish` | `true` | Chinese glosses on English candidates. Written into the schema as `annotate/english`. With both directions off, the filter, its switch, and the hotkey binding are not written at all. |
| `annotateShowPinyin` | `false` | Prefixes the gloss with the candidate pinyin. Off by default: the gloss is the point, and the pinyin is already in what you just typed. |
| `annotateHotkey` | `Control+e` | The key that toggles the annotation switch. Bound `when: composing` — see below. |
| `traditionalize` | `true` | Sets `traditionalize/opencc_config` to `s2twp.json`, so output is Taiwan-style traditional. Off omits the patch entirely. |
| `pageSize` | `10` | Candidates per page (`menu/page_size`). Accepted range 1–10: a hand edit outside it is clamped into range, while `ime-manager settings set` refuses it outright rather than writing back a number you never asked for. |
| `verticalCandidates` | `true` | Candidates render vertically. Two keys, because two frontends own the answer: Rime's `style/horizontal`, which is what Squirrel and Weasel read, and classicui's `Vertical Candidate List` in `conf/classicui.conf`, which is what draws the window under Fcitx5. The second is the one that acts here, and it is why that file is generated rather than shipped — it used to be a fixed payload file pinned to `True`, so the switch was wired to a key with no reader and turning it off did nothing. |
| `backgroundImageLight` | `""` | Absolute path to an image drawn behind the candidates while a *light* Omarchy theme is active. Empty means the theme's own background colour. |
| `backgroundImageDark` | `""` | The same for a *dark* theme. Two keys rather than one because the candidate window follows whichever theme is switched on, and an image picked against a dark theme is unreadable under a light one. Accepted formats are `.png`, `.svg`, `.svgz`, `.jpg`, `.jpeg`, `.jpe`, `.webp`, `.bmp` and `.gif`; the path is expanded and made absolute on write, and both the extension and the file's own first bytes are checked, because the candidate window picks its loader from the extension and would silently never load a file whose contents disagree with its name. |
| `panelWidth` | `420` | How wide the popup panel is, in the layout units `Style.space` scales by the theme's spacing. Measured from the content rather than picked: the widest row is a setting with its control on the trailing edge, and its label has to fit beside it. Accepted range 400–720; the lower bound is the narrowest panel at which no label elides. |

Only these keys are read; anything else on the widget entry is ignored — including
`englishInput`, which earlier versions accepted. There was never a way to make it
work: Rime Ice mounts `melt_eng` as a secondary translator, and librime has no
list-subtraction operator, so the `engine/translators/-` patch the setting
produced failed the schema build outright and took every other schema down with
it. Anyone who wants plain English has `ascii_mode`, which is the standard
mechanism in both Rime and Fcitx5. A stale `englishInput` left in `shell.json` is
ignored on read; `ime-manager settings set englishInput=...` now refuses it as an
unknown setting.

Two validated ways to change them. The CLI:

```bash
ime-manager settings get                       # resolved values, defaults filled in
ime-manager settings set schemas=rime_ice,double_pinyin_flypy pageSize=5
```

and the panel's own controls, which shell out to `ime-manager settings set` for
every control they show — so a value the CLI would refuse is refused there too,
and the control snaps back to the truth. A `settings set` write is atomic
(temporary file, then a rename), rewrites only the keys it was given, and keeps
Omarchy's own two-space indent, because a half-written `shell.json` takes down
the whole bar and not just this plugin.

Hand-editing `shell.json` is the third way and the only one that skips
validation: a wrongly-typed or out-of-range value there is quietly resolved to
something deployable rather than refused. That is the point — a typo in the bar's
configuration must not stop the input method from deploying.

The files the plugin generates — `default.custom.yaml` plus one
`<schema>.custom.yaml` for each selected schema that needs patching — are
written **whole** from settings on every deploy, never read and patched in
place. That makes a repeat deploy a no-op instead of a merge, and makes
uninstall a matter of deleting what was written. Deselecting a schema removes
its file. The cost is that hand edits to any of them are lost on the next
deploy; each carries a header saying so. Change the settings instead.

## Candidate annotation

Off by default. Turn it on with the *Candidate annotation* switch in the panel
or `ime-manager settings set annotation=true`, then deploy. While it is off
nothing of the feature reaches the machine: no `lua/annotate.lua` in the Rime
directory, no CC-CEDICT download, and no annotation filter, switch or hotkey
binding in any `<schema>.custom.yaml` — turning it off after a deploy removes
all three on the next one. `ime-manager status` and every deploy say when it is
off, so an absence of glosses is never a mystery.

One Rime `lua_filter` puts the meaning of each candidate into its comment. Type
`nihao` and the Chinese candidate carries its pinyin and English sense; type
`abal` and the English candidate `abalone` carries the Chinese word for it. Both
directions come from the same [CC-CEDICT](https://cc-cedict.org/) dictionary — one file, one
download, indexed both ways at load time.

There is **one** Rime switch, `annotate`, not one per direction: one key cannot
toggle two switches, so a second binding on the same key would have been dead
weight. (Measured against librime 1.17.0, a later binding wins over an earlier
one — which is why appending the hotkey works even on the schemas whose presets
already bind `Control+e` to end-of-line.) The hotkey answers
"show glosses right now"; *which* direction to gloss is a deploy-time setting
(`annotateChinese` and `annotateEnglish`), because nobody changes their mind
about that mid-word. The switch is written with `reset: 1`, so it starts on.

The filter checks the switch once per candidate batch and yields candidates
untouched when it is off, so having the feature installed but toggled off costs
essentially nothing. The dictionary loads lazily, on the first annotated
keystroke rather than at deploy time — and a dictionary file that is absent
simply loads as empty, which means candidates pass through unannotated. Typing
never depends on the download having succeeded, and a failed download never
fails a deploy.

### The hotkey, and why it is scoped

The hotkey (`Control+e` by default) is bound with `when: composing`: it applies
only while a composition is in progress and candidates are showing.

That restriction is the whole point. `Control+e` is readline's end-of-line, and a
binding with Rime's usual `when: always` would swallow it in every terminal
whenever Chinese mode is active. Scoping it to composing keeps the key yours
everywhere else, and the moment you want to toggle the annotation is exactly the
moment you are looking at candidates.

## The dictionary

The annotation dictionary is [CC-CEDICT](https://cc-cedict.org/), downloaded from
its publisher at deploy time and read exactly as published. The plugin ships no
dictionary data and redistributes none: the file arrives on your machine from
MDBG, byte for byte as MDBG published it, and the Lua filter parses the upstream
line format directly.

```
Traditional Simplified [pin1 yin1] /gloss one/gloss two/
```

`lib/assets.py` downloads
`https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz`,
decompresses it — gzip is the only thing removed — and caches it at
`~/.cache/omarchy-ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt`. A deploy fetches
it whenever candidate annotation is on with at least one gloss direction, and
skips the network entirely otherwise — a copy already in the cache is left
where it is, because it is cache rather than configuration.

**There is no pin.** MDBG publishes one URL that always serves the current
release and no URL for an older one, so there is nothing to pin to — unlike the
two Japanese Rime sources, which are pinned by commit in `sources/*.rev`.
Instead the cache is re-checked on a schedule: a copy more than 30 days old is
downloaded again on the next deploy, so you track upstream rather than a
snapshot of ours. The release stamp from the file's own header (`#! date=`) is
recorded in the cache's completion marker, so you can see which upstream release
you have.

A download that fails costs the glosses and nothing else: the deploy still
succeeds, the schema still builds, candidates simply show unannotated, and a
failed refresh keeps the copy already on disk. `ime-manager status` shows whether
the dictionary is downloaded, and the panel says so too.

## Hyprland: two pieces the plugin reports but does not manage

A comfortable setup wants two things from the compositor, and neither is written
by this plugin:

1. a key that toggles the input method, so you are not reaching for Ctrl+Space
2. the blur rule that makes the candidate window look like the rest of the
   desktop

**This is deliberate.** `~/.config/hypr/` is yours — frequently a dotfiles
module's — and the only drop-in Omarchy offers is
`~/.local/state/omarchy/toggles/hypr/*.lua`, which would mean this plugin
generating executable Lua into your compositor's configuration. Omarchy's own
`toggles.lua` carries an `exclude` list precisely because generated Lua there
once carried an injected device name. That is a poor trade for a keybind.

So the plugin detects both, reports them as status rows in the panel and in
`ime-manager status`, and leaves the writing to you. Each row has three answers —
**on**, **not on**, and **cannot tell** — and none of them is a failure: a row
that is merely absent is an optional enhancement that is not switched on, so it
wears the muted mark rather than the urgent one. On a machine with no `hyprctl`,
or a compositor that is not Hyprland, both read "cannot tell", which is a
different answer from "no".

### The toggle

Under Omarchy, put this in `~/.config/hypr/input.lua`. It is a key *handler*
rather than a bind, because Fcitx5 does not receive modifier-only release events
reliably through the Wayland input-method protocol:

```lua
-- Treat a standalone left Shift tap as the Ctrl+Space input-method toggle,
-- while preserving Shift as a modifier for every other shortcut.
local left_shift_keycode = 50
local left_shift_tap = false

hl.on("input.keyboard.key", function(key, _, state)
  if key == left_shift_keycode then
    if state == 1 then
      left_shift_tap = true
    elseif state == 0 and left_shift_tap then
      left_shift_tap = false
      hl.exec_cmd("fcitx5-remote -t")
    end
    return
  end

  if state == 1 then
    left_shift_tap = false
  end
end)
```

An ordinary bind works too, and the detection does not care which key you pick —
it looks for the dispatch, not for a modifier:

```
bind = SUPER, space, exec, fcitx5-remote -t
```

### The blur rule

Under Omarchy, in `~/.config/hypr/looknfeel.lua`:

```lua
hl.config({
  decoration = {
    blur = {
      -- Fcitx5 candidate windows are layer surfaces; blurring them keeps the
      -- input method consistent with the rest of the desktop.
      input_methods = true,
      input_methods_ignorealpha = 0.8,
    },
  },
})
```

or in a plain `hyprland.conf`:

```
decoration {
    blur {
        input_methods = true
        input_methods_ignorealpha = 0.8
    }
}
```

### How each one is detected

The blur rule is read from the running compositor with
`hyprctl getoption decoration:blur:input_methods`, which reports the *effective*
value whatever file set it — so a rule from a theme override, an Omarchy Lua
module or a drop-in all read the same. Careful if you go poking at it yourself:
`hyprctl` answers an option it does not know with the words "no such option" and
exit status **0**, so the exit status alone never says whether the answer is an
answer. Anything that does not parse as hyprctl's own JSON reads as "cannot
tell".

The toggle is looked for in `hyprctl binds -j` first, matching the dispatched
command and never the key. That is enough for a plain `bind = ..., exec,
fcitx5-remote -t`, and it is **not** enough under Omarchy: every bind Omarchy
registers comes back with the dispatcher `__lua` and an opaque index for its
argument, so the command is nowhere in the answer — and the left-Shift tap above
is not a bind at all but an `hl.on` handler, which `hyprctl binds` never lists.
So when the binds cannot say, the plugin reads your Hyprland configuration files
as the next best evidence: `*.conf` and `*.lua` under `~/.config/hypr/` and
`~/.local/state/omarchy/toggles/hypr/`, with commented-out lines and backup
files ignored. A compositor that cannot be reached at all outranks both — a file
saying the toggle is configured says nothing about whether that file is loaded,
so the answer is "cannot tell".

## Development

Python 3 standard library only; Lua for the Rime filter. Run everything the
repository knows how to check:

```bash
./check
```

That runs the whole `unittest` suite — currently 818 tests covering
settings resolution, configuration generation, the Fcitx5 profile merge, package
detection, asset caching, deploy and uninstall, the Rime rebuild and both halves
of its verdict, the candidate window's generated theme and its theme-set hook,
the Hyprland detection, the plugin manifest, the Lua filter, the `ime-manager`
CLI, and the input-state reader. The filter's tests shell out to `lua`, so that binary must be on `PATH`;
nothing else is needed beyond Python.

Generation is testable without a running Fcitx5, because it takes settings and
returns file contents rather than writing them; asset fetching is driven through
an injectable fetcher, so no test touches the network; the rebuild takes its
command runner and its sleeper as arguments, so no test reloads a real addon or
reads a real journal; and the Hyprland detection takes its command runner and
the directories it scans the same way, so no test runs `hyprctl` or reads
anyone's compositor configuration.

The dictionary needs no build step: the filter reads upstream's own format, so
there is nothing to convert and nothing to publish.

## Implementation status

An honest accounting of what runs today.

| Area | State |
|---|---|
| Repository skeleton and the `./check` test runner | Done |
| Settings resolution and persistence (`lib/settings.py`) | Done |
| Feature declarations — what annotation and Japanese input each install, fetch, own and patch (`lib/features.py`) | Done — one record per feature; deploy, generate and assets read it instead of tables of their own |
| Rime configuration generation (`lib/generate.py`) | Done |
| Package detection and installation (`lib/packages.py`) | Done |
| Asset fetching — Japanese sources pinned by commit, dictionary tracked against upstream (`lib/assets.py`) | Done |
| Lua annotation filter (`payload/rime/lua/annotate.lua`) | Done |
| Input state read and switch (`lib/imestate.py`) | Done |
| Deploy, uninstall, backups, and the install manifest (`lib/deploy.py`) | Done |
| Rime rebuild after a deploy, verified against the journal (`lib/deploy.py`) | Done |
| Candidate window theme generated from the Omarchy palette (`lib/theme.py`) | Done |
| Omarchy theme-set hook, install and removal (`lib/hooks.py`) | Done |
| The `QT_IM_MODULE` environment override (`lib/generate.py`) | Done — deployed, backed up, and removed by uninstall |
| Detecting the two Hyprland pieces (`lib/hyprland.py`) | Done — reported, never written |
| `manifest.json` and `Panel.qml` — the bar widget and its settings UI | Done |
| `ime-manager` subcommands (`deploy`, `uninstall`, `packages`, `status`, `state`, `settings`, `schema`, `theme`, `hook`) | Done |
| Annotation dictionary download | Done — CC-CEDICT is fetched from MDBG on deploy, cached at `~/.cache/omarchy-ime/dictionary/`, and refreshed once it is 30 days old |

Three pieces of a working setup live outside the files this plugin generates.
One of them it now owns: the `QT_IM_MODULE` override in
`~/.config/environment.d/`, deployed and uninstalled like everything else.

The other two are Hyprland's — the key that toggles the input method and the
blur rule for the candidate window — and they stay yours on purpose. The plugin
detects both and reports them in the panel and in `ime-manager status`, but
never writes them: see
[Hyprland: two pieces the plugin reports but does not manage](#hyprland-two-pieces-the-plugin-reports-but-does-not-manage)
for the snippets and for why.

## License

The plugin's own code is **MIT** (see [`LICENSE`](LICENSE)). What is not the
plugin's own is listed file by file in [`NOTICE`](NOTICE), with where it came
from and what is known about its terms. The short version:

**The dictionaries are fetched, never shipped.** Nothing under `payload/` is a
copy of anyone's dictionary, and this repository redistributes none. Every
Japanese table, schema and OpenCC file the plugin needs from
[gkovacs/rime-japanese](https://github.com/gkovacs/rime-japanese) and
[snomiao/rime-snomiao](https://github.com/snomiao/rime-snomiao) is downloaded
from its own publisher at a pinned commit, byte for byte, onto your machine —
see `SOURCE_FILES` in `lib/assets.py`. That is a rule rather than a
convenience: **neither repository ships a licence file**, so neither has
granted anyone permission to redistribute it. snomiao's `package.json` declares
`ISC` and publishes its `Rime` directory to npm, which is a real statement of
terms; gkovacs's repository says nothing at all, and its author has not been
contacted.

**Two Rime schemas here are derived works.**
`payload/rime-japanese/japanese_tw_eng.schema.yaml` and
`sno_japanese.schema.yaml` were composed for this plugin out of snomiao's
`sno_jpn_wubi86_eng` schema — most of their lookup, filter and reverse-lookup
blocks are his — and sno_japanese's romaji half goes back further, to
ensigma96's `japanese` schema in gkovacs/rime-japanese. Both files name their
sources in their own headers and both carry upstream's `author:` list, with the
names and addresses upstream wrote. `payload/rime-japanese/opencc/jpzh.json` is
snomiao's `zhjp.json` with one field changed; JSON cannot hold a comment, so its
attribution lives in `NOTICE`. If you are one of these authors and would rather
this plugin did not carry your work, open an issue and it will be removed.

**The annotation dictionary is CC-CEDICT, and it is not ours to license.**
CC-CEDICT is **CC-BY-SA 4.0**, published by
[MDBG](https://www.mdbg.net/chinese/dictionary?page=cc-cedict). This plugin
never redistributes it: it downloads the published file onto your machine,
unmodified, and reads it there — so the attribution and share-alike obligations
stay with the file and its publisher, exactly where they belong. Nothing in
this repository is a derived work of it, `tests/fixtures/cedict-sample.u8`
included: that is a fixture written for these tests in CC-CEDICT's *format*,
containing none of its entries. Both this README and the plugin's panel name
the source.

**One thing could not be traced.**
`payload/rime-japanese/opencc/hiragana_katakana.json` and its `.txt` are in
neither pinned upstream, and no publisher for them was found. The table is the
mechanical Unicode hiragana-to-katakana correspondence and the JSON is OpenCC's
ordinary config skeleton, so both are treated as written here — but if they are
yours, please open an issue and they will be credited or removed.
