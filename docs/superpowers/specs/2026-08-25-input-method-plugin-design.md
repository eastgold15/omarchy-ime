# Omarchy Input Method Plugin — Design

Date: 2026-08-25
Status: built, and deployed on this machine. Reconciled against the code
2026-08-25.
Repo: `~/Work/omarchy-ime`
Plugin id: `ryuhzk.ime`

This is the design record, not the manual — the README is the manual. Several
decisions here were superseded during implementation. Each one says what was
decided first and what displaced it, because the reason is the part worth
keeping.

## Why

Input-method configuration is currently spread across a dotfiles module that
copies files into place. It works, but it is not configurable: enabling or
disabling Japanese, or changing the candidate layout, means editing YAML by
hand and re-running an installer. Nothing surfaces what is installed or why a
deploy failed.

This plugin makes the whole input method one owned, configurable unit: toggles
in the Omarchy settings panel, generated Rime configuration, and heavy assets
fetched only when the feature that needs them is on.

It also adds the feature that motivated the work: show the meaning of each
candidate in the other language — English for Chinese candidates, Chinese for
English ones — toggleable with a hotkey.

## Scope

In, for the first version:

1. Package installation — `fcitx5`, `fcitx5-configtool`, `fcitx5-rime`, `rime-ice-git`
2. Fcitx5 configuration — `config`, `profile`, `conf/*.conf`
3. Rime configuration — schemas, patches, opencc data
4. The candidate window theme, generated from the active Omarchy palette and
   Hyprland's corner radius rather than shipped
5. External data fetched rather than shipped — the two Japanese Rime sources,
   pinned by commit, and the annotation dictionary, downloaded from its
   publisher unmodified
6. Bidirectional candidate annotation — English glosses on Chinese candidates,
   Chinese glosses on English ones
7. The bar indicator — replaces `omarchy.keyboard-layout`, shows the live input
   state, and opens this plugin's status, settings, and deploy action on click
8. Rime schema selection from outside the candidate window, and repainting the
   candidate window when the Omarchy theme changes. Both landed after this
   document was first written; they are described under the bar indicator and
   under Deployment.

Deferred, agreed to revisit after the plugin works:

- The Hyprland left-Shift tap that toggles the input method (`config/hypr/input.lua`)
- The layer rule that keeps candidate windows unblurred (`config/hypr/looknfeel.lua`)
- The `QT_IM_MODULE` override in `~/.config/environment.d/`

The first two live in files the dotfiles `hypr` module owns, so moving them
needs a plan for split ownership rather than a copy. The third is a one-line
environment file that currently belongs to nobody.

## Architecture

```
~/Work/omarchy-ime/
  manifest.json             plugin declaration + settings schema
  Panel.qml                 bar widget: live state, status, toggles, deploy
  ime-manager               the CLI, and the only thing that writes outside
                            the plugin directory
  lib/                      what the CLI does, one concern per module
    settings.py             defaults, validation, reading and writing shell.json
    generate.py             settings -> Rime customization files, rendered whole
    deploy.py               install, backups, install manifest, uninstall,
                            and the Rime rebuild with its journal verdict
    assets.py               the dictionary download and the pinned source clones
    theme.py                Omarchy palette + Hyprland radius -> Fcitx5 theme
    hooks.py                the Omarchy theme-set hook this plugin installs
    imestate.py             live Fcitx5 state, Rime schemas, addon reloads
    packages.py             package detection and installation
  payload/
    fcitx5/                 config, profile, conf/*.conf
    rime/lua/annotate.lua   the annotation filter
    rime-japanese/          the Japanese schemas and their opencc data
  sources/*.rev             the commits the two Japanese sources are pinned to
  tests/                    the unittest suite, plus a CC-CEDICT fixture
  check                     runs every check the repository knows how to run
  docs/
```

There is no `payload/themes/`: the candidate window's theme is generated from
the desktop's own, not shipped. There is no `tools/`: the dictionary is read in
its publisher's format, so there is nothing to convert. Both are decisions that
were taken the other way first, and both are recorded below.

`Panel.qml` never touches the filesystem outside the plugin directory. It reads
settings, shows state, and invokes `ime-manager`. All filesystem work — writing
Rime config, fetching assets, triggering deploys — happens in `ime-manager` and
the `lib/` modules it calls, so there is one place to audit, test, and reason
about failure.

### Generated, not edited

`ime-manager` writes `default.custom.yaml` and `rime_ice.custom.yaml` **whole**,
from templates plus the current settings. It never reads an existing file and
edits parts of it.

This matters because the alternative fails in a specific way: a read-modify-write
pass that is interrupted, or that runs against a file a previous version wrote
differently, leaves configuration that is neither the old state nor the new one,
and Rime's failure mode for that is a schema that silently stops loading. Whole
generation is idempotent — running it twice changes nothing — and uninstall is
deleting the files it wrote.

The cost is that hand edits to those two files are lost on the next deploy. Both
generated files carry a header saying so, and the README says it too; the panel
says the narrower thing it can be sure of, that a repeat deploy rewrites the
configuration from the settings below it.

### Settings

Settings use Omarchy's own plugin schema in `manifest.json`, and live inline on
the widget's own entry in `bar.layout` in `~/.config/omarchy/shell.json`, matched
by the entry whose `id` is `ryuhzk.ime`. Omarchy hot-reloads that file on save.
No separate config file.

Every key is flat and camelCase — the same names in `manifest.json` and in
`DEFAULTS` in `lib/settings.py`, so there is one spelling to get right:

| Setting | Default | Effect when off |
|---|---|---|
| `japanese` | off | The Japanese schemas are not deployed, the two pinned sources are not fetched, and `schema_list` omits them |
| `annotateChinese` | on | Chinese candidates carry no English gloss |
| `annotateEnglish` | on | English candidates carry no Chinese gloss. With both directions off, the filter, its switch and the hotkey binding are not written at all, and no dictionary is downloaded |
| `annotateShowPinyin` | off | The gloss on a Chinese candidate is shown without its pinyin |
| `annotateHotkey` | `Control+e` | — |
| `traditionalize` | on | No `traditionalize/opencc_config: s2twp.json` patch |
| `pageSize` | 10 | — (whole number, 1 to 10) |
| `verticalCandidates` | on | Candidates render horizontally (`style/horizontal: true`) |

The two annotation directions reach Rime as the deploy-time config keys
`annotate/chinese` and `annotate/english`; see the annotation section for why
they are settings rather than switches.

A setting change is a write to `shell.json` and nothing else. Applying it is the
deploy, which regenerates every file and ends in a Rime rebuild — the earlier
plan had each change redeploy on its own, but the rebuild is where a bad
configuration is caught, and it is reported as a failed deploy, so it is an
action the user takes rather than a side effect of moving a toggle. The panel
says so beside the controls: a change takes effect on the next deploy.

## Candidate annotation

### What Rime Ice already does

Rime Ice ships English word input as `table_translator@melt_eng`, already active
in the main schema, backed by roughly 23,000 words. Typing `abal` offers
`abalone` without leaving the Chinese schema. The plugin does not rebuild any of
this, and no longer offers to switch it off either — see decision 9.

What is missing is meaning. The English dictionary is `word<TAB>word` with no
glosses, and Rime Ice strips candidate comments in both `melt_eng` and `cn_en`
via `comment_format`. So a candidate tells you the spelling and nothing else, in
either direction.

### What this adds

One `lua_filter` annotates candidates with the meaning in the other language:

```
nihao  →  你好      nǐ hǎo · hello / hi
abal   →  abalone   鲍鱼
```

Both directions come from the same CC-CEDICT dictionary, which maps Chinese to
pinyin and English definitions; the English-to-Chinese direction is that same
data indexed the other way. One asset, one download, one filter.

**One switch, not one per direction.** The first design gave each direction its
own Rime switch — `english_comment` for glosses on Chinese candidates,
`chinese_comment` for glosses on English ones — both bound to the hotkey, so a
user who only wanted one direction would not pay for the other. That does not
work, and fails quietly: one key cannot toggle two switches, so one of them was
dead while the key still appeared to do something, because the other direction
was still toggling.

The reasoning first written here was wrong even though the conclusion held. It
claimed `key_binder` stops at the first match. Measured later against librime
1.17.0, a *later* binding wins over an earlier one — which is why appending the
hotkey works on the schemas whose presets already bind `Control+e` to
end-of-line, and why no removal is needed there. Worth recording: a right answer
reached by wrong reasoning is harder to catch than a wrong answer, because
nothing about it invites a second look.

What replaced it splits the two questions by how often each is asked. There is
one switch, `annotate`, and the hotkey toggles it: that answers *show glosses
right now*, which is worth reaching for mid-word. *Which direction to gloss* is a
deploy-time setting, written into the schema as `annotate/chinese` and
`annotate/english` and read by the filter when it is created, because nobody
changes their mind about that while typing. The switch is written with
`reset: 1`, so it starts on.

The filter reads the switch once per candidate batch, before it iterates, and
yields the batch untouched when it is off — so having the feature installed and
toggled off costs one option lookup per batch, not one check per candidate. The
dictionary loads lazily on first annotated keystroke, not at deploy time.

`librime-lua` is present at `/usr/lib/rime-plugins/librime-lua.so`, and Rime Ice
already uses it (`lua_filter@*reduce_english_filter`), so this adds no new
dependency. The alternatives were rejected: the built-in `reverse_lookup_filter`
cannot be toggled at runtime, and a second schema would mean switching schemas
rather than pressing one key, plus a second copy of the word list to maintain.

### Hotkey scope

The binding is `Control+e` with `when: composing` — it only applies while a
composition is in progress and candidates are showing.

This is deliberate. Rime's other option toggles use `when: always`, but they are
bound to `Control+Shift+<digit>`, which nothing else wants. `Control+e` is
readline's end-of-line, and `when: always` would swallow it in every terminal
whenever Chinese mode is active. Restricting it to composing keeps the key
available everywhere else, and the moment you want to toggle the annotation is
exactly when you are looking at candidates.

### Dictionary

Source is CC-CEDICT, downloaded from its publisher, MDBG, and read exactly as
published. `lib/assets.py` fetches
`https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz`,
decompresses it — gzip is the only thing removed — and caches it at
`~/.cache/omarchy-ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt`. The filter parses
upstream's own line format, `Traditional Simplified [pin1 yin1] /gloss/gloss/`,
and builds both lookup directions from it at load time, so there is one file to
download regardless of which directions are enabled.

Superseded 2026-08-25: the plan was `tools/build-dict.py`, converting CC-CEDICT
into a flat file of our own and publishing it as a GitHub release asset pinned by
tag — on the grounds that a several-megabyte file that changes on its own
schedule should not be committed, and that a release tag gives the plugin a
version to check for updates. That was rejected on licensing: converting the
file and publishing the result meant modifying and redistributing someone else's
CC-BY-SA dictionary under our name, for a conversion the Lua filter can do
itself while loading. Parsing upstream's format directly removes the build step,
the release, and the obligation in one move. `tools/build-dict.py` is gone from
the tree.

**There is no pin,** and that is a considered difference from the two Japanese
sources rather than an omission. MDBG publishes one URL that always serves the
current release and no URL for an older one, so a recorded version could be
checked but never requested — a pin that cannot be honoured is worse than none.
The cache is re-checked on a schedule instead: a copy more than 30 days old is
downloaded again on the next deploy, which keeps a desktop reasonably current
without a download every time. Upstream's own release stamp, the `#! date=` line
in the file's header, is written into the cache's completion marker as a record
of what is on disk. A refresh that fails keeps the copy that is already there.

**Licensing.** CC-CEDICT is CC-BY-SA 4.0, and this plugin redistributes none of
it. The file arrives on the user's machine from its publisher, unmodified, so the
attribution and share-alike obligations stay with the file and its publisher, and
nothing in this repository is a derived work of it. The plugin's own code is MIT.
The README names the source.

## The bar indicator

The widget takes the place of `omarchy.keyboard-layout`. Showing the layout was
never quite the truth on this machine: what actually determines whether a
keystroke becomes Chinese is the Fcitx5 state, and the layout indicator cannot
see it.

It displays the live state from `fcitx5-remote` — closed, Latin, or the active
input method's name — and clicking it opens the panel: current state, whatever
needs attention (packages missing, dictionary not downloaded, nothing deployed
yet), the settings, and the deploy action.

Switching is layered, because two different things are called switching:

- Between Fcitx5 input methods — `fcitx5-remote -s <name>`, and the list comes
  from `fcitx5-remote -q`/`-n`. This is what turns Chinese input on and off.
  `lib/imestate.py` has it; nothing surfaces it yet, neither a subcommand nor a
  panel control.
- Between Rime schemas — Chinese, Japanese, and the rest live inside one Fcitx5
  input method, so this is Rime's own schema selection rather than an Fcitx5
  call. This document left the mechanism to implementation, with a fallback of
  presenting the list and leaving selection to Rime's own menu; the fallback was
  not needed. fcitx5-rime exposes `ListAllSchemas`, `GetCurrentSchema` and
  `SetSchema` on the session bus at `/rime` (`org.fcitx.Fcitx.Rime1`), and that
  is the same switch Rime's F4 menu operates, so a click and the F4 menu cannot
  disagree. `lib/imestate.py` calls them through `gdbus` — the plugin is
  stdlib-only, and `lib/deploy.py` already talks to Fcitx5 that way — and
  `ime-manager schema list|get|set` exposes them.

  The interface hands out schema ids and no names. The only name that exists
  without inventing one is the `schema/name` Rime compiles into
  `<rime>/build/<id>.schema.yaml`, so that is what is read, falling back to the
  id: a schema this plugin has never heard of is still one the user can switch
  to, and calling it by its id is honest.

Schema switching is reachable from `ime-manager` today; the panel does not show
a switcher for either layer yet.

State is read once when the bar item is constructed and then on a timer — every
two seconds while the panel is open, every five while it is not. There is no
daemon and no subscription: the cost of a `fcitx5-remote` call is negligible next
to the complexity of keeping a DBus signal connection alive across Fcitx5
restarts.

## Asset acquisition

Three things are fetched rather than shipped: the CC-CEDICT annotation
dictionary, and the two external Rime dictionary sources for Japanese
(`rime-japanese`, `rime-snomiao`). The two Japanese sources are pinned by commit
— the pins the fetch uses are `SOURCES` in `lib/assets.py`, and `sources/*.rev`
records the same two commits — and the pin is part of the cache path,
`~/.cache/omarchy-ime/sources/<name>/<revision>/`, so re-enabling is free and a
pin change is a distinct directory rather than an in-place update. A `.complete`
marker is written last, so a directory without one is a fetch that died partway
and is discarded rather than trusted.

The dictionary is not pinned, for the reason given above, and lives at one path
that is refreshed in place. Only named files are copied out of a clone, rather
than the clone wholesale: those repositories carry schemas the plugin ships its
own versions of, and an unwanted schema left in the Rime directory is a schema
Rime will load.

Fetching happens when the feature that needs the asset is switched on, at the
next deploy — a user who never enables Japanese never touches those two
repositories, and a user with both annotation directions off never downloads the
dictionary.

Failure is non-fatal by design. A dictionary that cannot be downloaded leaves
the annotation filter reading an absent file, which it treats as an empty
dictionary and passes candidates through. Typing keeps working, the deploy still
succeeds, and the panel shows the dictionary as not downloaded and says the next
deploy will fetch it — the retry is the next deploy rather than a button of its
own. The rule is that no network failure may leave the user unable to type.

## Deployment

`ime-manager deploy` is the single entry point:

1. Resolve settings from the widget's entry in `shell.json`
2. Download the annotation dictionary if either direction is on, and clone the
   pinned sources the enabled features need
3. Generate the Rime customization files and the candidate window's theme, and
   install them along with the payload groups the settings select and the files
   named out of the fetched sources — into `~/.local/share/fcitx5/rime/`,
   `~/.config/fcitx5/`, and `~/.local/share/fcitx5/themes/omarchy-ime/`
4. Delete whatever the previous manifest listed and this deploy did not write,
   and put the theme-set hook's entry back in the manifest
5. Ask Rime to rebuild, and read the journal to find out whether it did

### The rebuild, and its verdict

Writing the files is half a deploy. Rime reads its customization files when it
builds, not at keystroke time, so a file on disk that has not been compiled is a
file that is not in effect — and one Rime refuses to compile takes the whole
schema down with it, which is how a single malformed patch key cost an input
method while every test still passed. The trigger and the verdict therefore
belong together, in `lib/deploy.redeploy`.

The trigger is a `ReloadAddonConfig` call on Fcitx5's D-Bus controller
(`/controller`, `org.fcitx.Fcitx.Controller1`), aimed at the `rime` addon and
made with `gdbus`. fcitx5-rime answers a config reload by tearing down its
librime session and calling `start_maintenance`, which recompiles every schema
whose source has changed. Two obvious alternatives do not do the job:
`fcitx5-remote -r` reloads Fcitx5's own configuration and leaves librime alone,
and restarting the unit does rebuild but drops every application's input context
with it.

The verdict comes from the journal, because that is the only place it appears:
librime logs through glog with its stderr threshold at ERROR, and fcitx5's stderr
is the `omarchy-fcitx5.service` journal. A cursor is taken from that journal
immediately before the trigger, so only lines from this rebuild are read; when no
cursor can be had, the fallback is `--since`, which is accurate only to the
second and can therefore blame this deploy for the previous one's failure.
librime builds on its own thread, so the window is polled rather than read once:
every half second for up to eight seconds. A failure is reported the moment one
of the known build-error lines appears; success is only declared once the window
closes without one, because a successful build says nothing at all.

A rebuild that failed, or that could not be triggered, makes `ime-manager deploy`
exit non-zero, print what Rime wrote, and say plainly that the configuration is
on disk but is not in effect. That is the failure worth being loudest about: it
is the one that leaves someone unable to type.

### Following the Omarchy theme

The candidate window's theme is generated at deploy time from the palette at
`~/.local/state/omarchy/current/theme/colors.toml` and Hyprland's
`decoration:rounding`. That alone would leave the window painted in the old
theme until the next deploy, so switching themes is handled on its own path.

`ime-manager theme apply` is the narrow version of a deploy: it regenerates the
theme files, writes only the ones whose contents actually changed, and reloads a
single Fcitx5 addon — `classicui`, which draws the candidate window — instead of
restarting Fcitx5 and dropping every application's input context. It does no
download, no Rime rebuild and no package check, and it does nothing at all before
the first deploy, because files written while nothing is in the manifest are
files an uninstall would leave behind.

`ime-manager hook install` attaches that to Omarchy. `omarchy-theme-set` ends by
running `omarchy-hook theme-set <name>`, which runs every file in
`~/.config/omarchy/hooks/theme-set.d/`, so the hook is one file there, named
`ryuhzk.ime` after the plugin rather than after the hook — the directory is
shared with every other plugin on the machine. It is a three-line shim that runs
`ime-manager theme apply` and cannot fail: `omarchy-hook` prints "Hook failed"
for any non-zero exit, and the ordinary reasons this has nothing to do — the
plugin was moved, Fcitx5 is not up yet at login — are not worth saying on every
theme switch. The theme name arrives as `$1` and is ignored, because `theme
apply` reads whichever palette Omarchy has just made current, which is the same
answer and is also the right one when the command is run by hand.

The hook is recorded in the same install manifest as every deployed file, so
`ime-manager uninstall` takes it away with everything else and no hook is left
pointing at a plugin that is gone. A deploy rebuilds the manifest from the files
it wrote, and the hook is not one of them, so a deploy re-records it afterwards
rather than dropping it — and the hook's directory is kept out of the targets a
deploy writes into, or every deploy would delete the hook as a stale file.

### The manifest

Every file a deploy writes is recorded in a manifest under
`~/.local/state/omarchy-ime/installed.json`, so `ime-manager uninstall`
removes exactly what was installed and nothing else. Files the user created are
never touched.

Existing files are backed up on first write, the same way the dotfiles installer
does it today, so adopting the plugin on a machine that already has a working
setup is reversible.

## Dotfiles migration

The dotfiles `fcitx5` module is removed once the plugin is proven: the
`install_fcitx5` function, `config/fcitx5/`, `share/fcitx5/`,
`sources/rime-*.rev`, the package entries, and the `check fcitx5` case.

This is the last step, not the first. Until the plugin can deploy a working
setup from scratch on this machine, the dotfiles module stays as the fallback.
Running both is safe in one direction only — the plugin writes the same paths
the module does, so after migration, running `./install fcitx5` would overwrite
the plugin's generated files. Removing the module is what closes that hazard.

## Testing

`ime-manager` is testable without a running Fcitx5, and `./check` runs the whole
suite. Generation takes settings and returns file contents rather than writing
them, so the tests assert on generated content for each toggle combination that
matters — Japanese on and off, each annotation direction on and off, pinyin on
and off — and that generating twice is byte-identical.

The seams are the same everywhere: asset fetching takes its fetcher as an
argument, so no test touches the network and the missing-asset path is asserted
to leave a deployable configuration; the rebuild takes its command runner and its
sleeper, so no test reloads a real addon or reads a real journal; and the theme
takes its palette, so no test needs an Omarchy install or a running compositor.

The Lua filter is tested by shelling out to `lua` against a small CC-CEDICT
fixture in upstream's own format — the format the real file arrives in, since
nothing converts it.

The end-to-end check is manual and stated in the README: deploy, confirm Chinese
input works, toggle the hotkey, confirm the annotation appears and disappears.

## Decisions taken

1. **Name.** `omarchy-ime` / `ryuhzk.ime`.
2. **Package installation is the plugin's job.** It calls `omarchy pkg add` for
   the official packages and `omarchy pkg aur add` for `rime-ice-git` when any
   are missing. A solution that configures an input method but leaves the user
   to install it is not the complete thing this is meant to be.

   The obligation that comes with it: package installation is the one privileged
   thing here, so it never runs implicitly. The panel reports what is missing and
   installs only on an explicit action, and a refusal or failure leaves the rest
   of the deploy to proceed against whatever is already installed.
3. **The candidate window follows the desktop.** Superseded 2026-08-25: the
   plugin shipped `catppuccin-macchiato-maroon` and the candidate window stayed
   navy while the desktop was warm brown - it read as a different product. The
   theme is now generated from `colors.toml` and `decoration:rounding`, so the
   plugin vendors no third-party colours at all and a theme switch is followed
   rather than ignored.
4. **Annotation hotkey.** `Control+e`, scoped to `when: composing` for the
   reasons in the hotkey section above.
5. **One annotation switch, not one per direction.** Superseded 2026-08-25: two
   switches bound to the same key meant one of them never ran. The hotkey now
   toggles the one `annotate` switch, and the directions are deploy-time
   settings. The stated cause was wrong — see the annotation section; librime
   gives a later binding priority, not an earlier one.
6. **The dictionary is downloaded, not derived.** Superseded 2026-08-25: a
   converted dictionary published from this repository would have meant
   modifying and redistributing a CC-BY-SA work under our name. The published
   file is downloaded unmodified and the filter parses it as published, which
   removes the build step and the obligation together.
7. **Rime schemas are switched over fcitx5-rime's D-Bus interface.** It turned
   out to expose exactly the schema selection Rime's own F4 menu uses, so the
   documented fallback — show the list, let Rime's menu do the switching — was
   not needed, and no synthetic keystrokes are involved.
8. **The candidate window is repainted by a theme-set hook.** Generating the
   theme at deploy time is only half an answer while themes can change between
   deploys, so the plugin installs one file into Omarchy's `theme-set.d` and
   records it in its own install manifest — a hook whose lifecycle the plugin
   owns, rather than a step in a README that nothing ever undoes.
9. **English word input is a setting.** Superseded 2026-08-25: the setting was
   removed, because it never worked and could not be made to work. Turning it
   off emitted `engine/translators/-`, and librime has no list-subtraction
   operator — the patch failed with `error applying patch to
   engine/translators/-` and took the whole schema build down with it, so the
   one thing the switch reliably did was break Chinese input.

   It could not be repaired either. Rime Ice mounts `melt_eng` as a *secondary*
   translator by design — upstream's own comment is `melt_eng  #
   英文输入，作为次翻译器挂载到拼音方案` — competing with pinyin by
   `initial_quality` weight (1.2 against 1.1). There is no upstream switch or
   hotkey to disable it, and none of Rime Ice's seven switches covers it.
   Removing it would mean rewriting the engine list the schema owns.

   Nothing is lost. Anyone who wants plain English already has `ascii_mode`,
   which is the standard mechanism in both Rime and Fcitx5. The key is not
   migrated: unlike `japanese`, which stood for a selection that still exists, it
   asked for something that never happened, so it is simply ignored on read and
   refused on write. Worth recording rather than quietly deleting — a switch
   that shipped and never worked is the kind of thing someone re-proposes.

## Open decisions

None. One decided thing is not built out: the panel shows no switcher for either
input methods or Rime schemas, though `lib/imestate.py` and `ime-manager schema`
have what one would need. Reassess the deferred Hyprland and environment items
now that the plugin deploys a working setup.
