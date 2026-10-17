# Extension mechanism — design

**Status:** deferred. Written 2026-08-26, then set aside: every planned extension is first-party, so an external mechanism solves a problem nobody has yet. Revisit when a third party asks to add something. The internal feature declaration in `lib/features.py` is the seed this would grow from.

**Supersedes nothing.** v0.1.0 (one plugin, everything built in) stays as it is
and is what the marketplace is reviewing. This is the shape of v0.2.

## Goal

Shrink the plugin to a core — Fcitx5, Rime Ice, the panel, the theme, the
deploy transaction — and move everything else behind one extension mechanism,
so that Japanese, candidate annotation, and the planned LLM translation are
each an extension a user installs through the plugin's own manager, and so
that a third party can write one without touching the core.

## Non-goals

- Not a marketplace. There is no browse UI, no discovery, no ratings. An
  extension is a git URL and a commit.
- Not a sandbox for Lua. An extension that declares `lua` runs code in
  librime's process, in the keystroke path, as the user. The mechanism makes
  that fact loud and consented to; it does not make it safe.
- Not compatible with plum recipes or with Omarchy's plugin format. Both were
  considered and rejected — see _Alternatives_.
- Not auto-updating. Ever.

## Decisions already taken

These were settled with the owner before this document and are not reopened
here.

| Decision                          | Choice                                                                                                                               |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Who can publish an extension      | Anyone. An extension is any git repository at a pinned commit.                                                                       |
| May an extension ship Lua         | Yes, if it declares the `lua` capability; the user is told and must confirm at install. Undeclared Lua is refused, not warned about. |
| Management surface, first version | CLI does install, remove, update. The panel lists installed extensions and renders their declared settings; it does not install.     |
| What happens to v0.1.0            | Ships as submitted. This is v0.2, developed on a branch, unpublished until done.                                                     |

## Alternatives considered

**Extensions as Omarchy plugins** — each extension goes through `omarchy plugin
add`, the core scans `~/.config/omarchy/plugins/*/manifest.json` for a marker.
Rejected: Omarchy's validator refuses unknown `kinds`, so every extension would
have to be a bar widget or nothing; there is no place in that flow for a
capability confirmation; and it contradicts the requirement that the IME's own
manager installs them.

**Extensions as Rime packages (plum)** — the core becomes a plum-style
installer and can install existing recipes. Rejected: plum recipes are shell
scripts, which is a worse trust story than Lua; settings could only be Rime
switches, so nothing reaches the panel; and the core would inherit plum's
layout assumptions instead of the deploy transaction it already has.

**Chosen: extensions as payload groups with a manifest.** The core already
deploys in groups — a directory of files, a target, the schemas that need it,
the sources it fetches — with an install manifest that records every path as
created or replaced. An extension is one more group whose directory lives
outside the repository. Almost all of the machinery exists; what is new is
where the group comes from, what it is allowed to say, and how the user agrees
to it.

## Architecture

```
core (this repository)
  omarchy.ime                 CLI; gains `extension add|remove|update|list`
  lib/extensions.py           NEW: fetch, validate, confirm, register, resolve
  lib/deploy.py               treats registered extensions as payload groups
  lib/generate.py             renders extension patches and settings mirrors
  lib/settings.py             stores extension settings under one namespace
  Panel.qml                   renders extension settings from their manifests
  payload/                    core only: fcitx5 config, rime_ice patches, theme

~/.local/share/omarchy.ime/extensions/
  registry.json               what is installed, from where, at which commit,
                              which capabilities were confirmed
  <id>/                       checkout at the pinned commit, .git removed
    extension.json
    rime/                     → ~/.local/share/fcitx5/rime/
    lua/                      → ~/.local/share/fcitx5/rime/lua/<id>/

~/.config/omarchy/shell.json
  … widget settings …
  "extensions": { "<id>": { "<key>": value } }
```

Data flow at deploy:

```
settings.read()  ──┐
registry.json    ──┼──▶ extensions.resolve()  ──▶  [ExtensionGroup]
extension.json   ──┘             │
                                 ▼
           generate.render()  patches + settings mirrors into <schema>.custom.yaml
                                 │
                                 ▼
           deploy.install()   core groups + extension groups, one manifest,
                              per-path created/replaced, outcome-verified
```

## What an extension is

A git repository whose root holds `extension.json`. Everything else is
optional and must be under one of two directories.

```
extension.json
rime/            files installed into the Rime user directory, verbatim
lua/             Lua installed into rime/lua/<id>/, verbatim
```

Nothing outside those two directories is installed. A README, a LICENSE,
tests, whatever else — ignored by the installer, kept in the checkout.

### `extension.json`

```json
{
  "extensionApi": 1,
  "id": "annotation",
  "name": "Candidate annotation",
  "version": "0.2.0",
  "author": "ryuhzk",
  "license": "MIT",
  "core": ">=0.2.0",

  "capabilities": ["lua", "sources"],

  "sources": {
    "cedict": {
      "kind": "https",
      "url": "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
      "hosts": ["www.mdbg.net", "mdbg.net"],
      "gzip": true,
      "maxCompressedBytes": 33554432,
      "maxDecompressedBytes": 100663296,
      "verify": "cedict",
      "files": { ".": "annotation/cedict.u8" }
    },
    "rime-japanese": {
      "kind": "git",
      "url": "https://github.com/gkovacs/rime-japanese",
      "revision": "4c1e6513…",
      "files": {
        "japanese.dict.yaml": "japanese.dict.yaml",
        "japanese.jmdict.dict.yaml": "japanese.jmdict.dict.yaml"
      }
    }
  },

  "packages": [],
  "aur": [],

  "schemas": [],

  "patches": {
    "*": {
      "filters+": ["lua_filter@annotation/annotate"],
      "bindings+": [{ "accept": "$annotateHotkey", "toggle": "annotate", "when": "composing" }],
      "switches+": [{ "name": "annotate", "reset": 1, "states": ["", "gloss"] }]
    }
  },

  "settings": [
    {
      "key": "annotateChinese",
      "type": "boolean",
      "default": true,
      "label": "English gloss on Chinese",
      "description": "Show what a Chinese candidate means in English."
    },
    {
      "key": "annotateHotkey",
      "type": "string",
      "default": "Control+e",
      "label": "Annotation hotkey"
    }
  ]
}
```

Field by field:

| Field                                  | Required          | Meaning                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extensionApi`                         | yes               | The manifest format. Core refuses a value it does not know.                                                                                                                                                                                                                                                                       |
| `id`                                   | yes               | `[a-z][a-z0-9-]{1,31}`. Names the directory, the Lua namespace, the settings namespace, the manifest group. Must not collide with a core group name.                                                                                                                                                                              |
| `name`, `version`, `author`, `license` | yes               | Shown at install and in the panel. Not interpreted.                                                                                                                                                                                                                                                                               |
| `core`                                 | yes               | A version constraint on the core, `>=x.y.z` only.                                                                                                                                                                                                                                                                                 |
| `capabilities`                         | yes, may be empty | See _Capabilities_.                                                                                                                                                                                                                                                                                                               |
| `sources`                              | no                | Pinned downloads, by name. `kind` is `git` or `https`. A `git` source has `url` and `revision` (40 hex); an `https` source has `url`, `hosts`, optional `gzip`, size caps, and a `verify` name. Every source has `files`: a map from a path inside the fetched content to a destination under the Rime directory — see _Sources_. |
| `packages`, `aur`                      | no                | Package names the extension needs. Installed through the same terminal flow as the core's own.                                                                                                                                                                                                                                    |
| `schemas`                              | no                | Schema ids this extension makes available to the picker. Each must exist as `rime/<id>.schema.yaml` in the extension or come from one of its sources.                                                                                                                                                                             |
| `patches`                              | no                | Per-schema patches, keyed by schema id, or `*` for every deployed schema the core lists as Chinese (today's `ANNOTATED_SCHEMAS`), or `*all*` for every deployed schema. See _Patches_.                                                                                                                                            |
| `settings`                             | no                | Declarative settings. See _Settings_.                                                                                                                                                                                                                                                                                             |

Anything not in this table is an error, not ignored. An unknown key is the
most likely sign of a manifest written for a later `extensionApi`.

### Sources

An extension may install files it does not carry: each source's `files` map
names a path inside the fetched content and the destination it lands at under
the Rime directory, exactly as the core's `SOURCE_FILES` does for the Japanese
tables today. For an `https` source the fetched content is one file, so the
only valid left-hand side is `.`. Destinations obey the same rules as files
under `rime/`: relative, no `..`, no collision with any other file the same
extension or another one installs.

This is the mechanism that lets the Japanese extension say "install
`japanese.dict.yaml` from gkovacs at commit X" without redistributing it. It is
the same rule the core lives by: nothing is copied into the repository that its
publisher did not license for redistribution.

`https` sources reuse the bounded, verified, atomic download built for
CC-CEDICT. `verify` names a verifier the core knows (`cedict`, `none`); an
extension cannot ship its own verifier, because a verifier is code. `none` is
allowed but the install summary says so in as many words.

## Installing

```
omarchy.ime extension add https://github.com/ryuhzk/omarchy-ime-annotation@<sha>
omarchy.ime extension add --local ~/src/my-extension        # developers only
```

The `@<sha>` is mandatory. A URL without one is refused with the reason: a
commit is the only thing that makes "what I reviewed" and "what I installed"
the same object. Tags and branches move.

Accepted URL schemes: `https://` and `ssh://` / `git@host:path`. `file://` is
refused; `--local <path>` exists for developing an extension and is recorded in
the registry as local, with no revision, and the panel marks it as such.

Steps, in order, and each stops the install on failure:

1. **Fetch** to a temporary directory under the extensions root, never in
   place: `git init`, `git -c fetch.fsckObjects=true fetch --depth 1 <url>
<sha>`, `git checkout FETCH_HEAD`, then `git rev-parse HEAD` compared to the
   sha. Credential prompting disabled, 300 s timeout. `.git` removed after the
   comparison succeeds.
2. **Validate the manifest** against the table above: shape, id syntax,
   `extensionApi`, `core` constraint against the running core, no unknown keys.
3. **Validate the tree.** Every file is a regular file (no symlinks, no
   devices), under `rime/` or `lua/`, at most 64 MB, and no path component is
   `..`. A Lua file present without the `lua` capability is a refusal.
   Every `files` destination is checked like a file under `rime/`. A
   `.schema.yaml` under `rime/` or among the `files` destinations whose id is
   not in `schemas` is a refusal — an extension does not get to install
   schemas it does not admit to.
4. **Check for conflicts** with everything already registered: same `id`,
   same setting key across extensions, same `lua/` path, same `rime/` path,
   same schema id. Any of these is a refusal naming the other extension.
5. **Print the summary** and require confirmation:

   ```
   Candidate annotation 0.2.0 by ryuhzk (MIT)
   from https://github.com/ryuhzk/omarchy-ime-annotation at 3f2a9c1

   This extension will:
     RUN CODE in the input method  — 1 Lua file, runs on every keystroke
     DOWNLOAD  cedict_1_0_ts_utf-8_mdbg.txt.gz from www.mdbg.net (verified as CC-CEDICT)
     PATCH     every Chinese schema: adds a filter, a key binding (Control+e), a switch
     ADD       2 settings to the panel

   It installs no packages and provides no schemas.

   Install? [y/N]
   ```

   `--yes` skips the prompt for scripts. The panel never reaches this step,
   which is the whole reason the panel does not install.

6. **Move into place** — `os.rename` of the temporary directory to
   `extensions/<id>/` — and **register**: id, name, version, url, sha, the
   capabilities that were confirmed, the install time. Registration is an
   atomic write of `registry.json` (temp file, `O_EXCL`, `os.replace`).
7. **Do not deploy.** Installing an extension records intent. The next deploy
   makes it real, the same way a settings change does. The CLI says so:
   `Installed. Run 'omarchy.ime deploy' to apply it.` The panel shows the
   pending-deploy footer.

## Deploying with extensions

`deploy.active_groups()` today returns the core payload groups a configuration
needs. It gains the registered extensions, each as a group:

- **Target:** `rime/` → the Rime user directory; `lua/` → `rime/lua/<id>/`.
  Nothing else.
- **Manifest namespace:** every path an extension installs is recorded under
  the group name `ext:<id>`, so `extension remove` can sweep exactly its files
  and restore exactly what it replaced, through the same v2 manifest.
- **Sources** are fetched at deploy time, not install time, so a deploy with
  the network down behaves the way the core's own Japanese deploy does: the
  fetch is announced as failed, the files that depend on it are exempt from the
  sweep, and a schema whose tables are missing is withheld from `schema_list`
  rather than handed to Rime to fail on.
- **Schemas** an extension provides join the picker's catalogue. The core's
  `SCHEMA_IDS` becomes "core schemas from `rime-data`" plus "schemas from
  registered extensions". Selecting one that comes from an extension that is
  later removed drops it from the selection with a notice, never a crash.
- **Patches** are rendered into each affected schema's `<id>.custom.yaml`
  after the core's own patch for that schema, in extension registration order.
- **Settings mirror** — see below — is written into the same file.
- **Outcome verification** is unchanged and now covers extension schemas: a
  deploy that asked for `sno_japanese` and does not find `build/sno_japanese.
schema.yaml` newer than the deploy is a failed deploy, whoever the schema
  came from.

## Settings

An extension declares settings; the core owns them. The extension never reads
`shell.json`.

**Types:** `boolean`, `integer` (with `min`, `max`), `string`, `select` (with
`options: [{value, label}]`). No paths, no files, no custom widgets. A type
the core does not know is a manifest error. `string` values are escaped at
every place they land, as `annotateHotkey` is today.

**Storage:** `shell.json`, the widget's settings, under
`"extensions": {"<id>": {"<key>": value}}`. The core's `settings.resolve` is
extended with the same lenient read: an unknown key is ignored, a wrong-typed
value falls back to the declared default, and only `validate` is strict.
Removing an extension removes its settings block.

**Panel:** one section per registered extension, headed by its `name`, holding
its settings rendered by the same controls the core uses for its own — the
toggle row, the number field, the text field, the dropdown. The panel reads
each manifest through `omarchy.ime extension list --json`, once at open and
after an install or removal. There is no way for an extension to put QML,
colours, sizes, or anything but those four controls into the panel.

**Mirror into Rime:** every setting is written into each affected schema's
custom.yaml under the extension's namespace, `<id>/<key>: value`, so Lua reads
it as `env.engine.schema.config:get_bool("annotation/annotateChinese")`. This is
exactly how the core's own `annotate/*` keys reach the filter today, made
general. A `$key` reference inside a patch (`"accept": "$annotateHotkey"`) is
substituted with the setting's current value at render time, quoted.

## Patches

A patch says what an extension adds to a schema. It is constrained to the
operations the core knows are safe to compose, because the one time a patch
was written freehand it broke every schema at once.

| Operation      | Value                                              | Rendered as                    |
| -------------- | -------------------------------------------------- | ------------------------------ |
| `filters+`     | list of strings                                    | `engine/filters/+: [...]`      |
| `translators+` | list of strings                                    | `engine/translators/+: [...]`  |
| `bindings+`    | list of `{accept, when, toggle\|send\|set_option}` | `key_binder/bindings/+: [...]` |
| `switches+`    | list of `{name, reset?, states}`                   | `switches/+: [...]`            |
| `set`          | mapping of `path: scalar`                          | `path: value`, one per line    |

Rules, enforced when the manifest is validated, not at deploy:

- Every `+` operation's value is a list. A scalar is a manifest error with the
  librime reason in the message.
- `set` may not target `engine/`, `key_binder/`, `switches`, `schema/`, or
  `menu/page_size` — those are either owned by the list operations above or by
  the core's own settings. It may not contain `/+`, `/-`, or `__include`.
- Keys and string values are quoted through the core's YAML scalar quoting.
  A patch cannot inject a line.
- A `filters+` entry of the form `lua_filter@<id>/<file>` must name a file the
  extension actually ships under `lua/`, and the `<id>` must be its own.

Anything a patch cannot express is a reason to extend the table, deliberately,
in the core — not a reason to allow raw YAML.

## Capabilities

| Capability | Granted                                                                              | Refused without it     |
| ---------- | ------------------------------------------------------------------------------------ | ---------------------- |
| `lua`      | Files under `lua/` are installed and may be referenced by `filters+`/`translators+`. | Any file under `lua/`. |
| `sources`  | `sources` may be declared and fetched.                                               | Any `sources` entry.   |
| `packages` | `packages`/`aur` may be declared.                                                    | Any package entry.     |

The summary at install spells each one out in terms of what it lets the
extension do to the user, not in terms of the capability name. `lua` is
described as running code inside the input method on every keystroke, because
that is what it is.

A capability confirmed at install is recorded in the registry. An `update`
whose new manifest asks for a capability the old one did not have goes through
the summary and confirmation again; one that drops a capability does not.

## Security model

What the mechanism guarantees, and what it does not.

**Guaranteed by mechanism:**

- Nothing is installed that was not fetched by commit and verified to be that
  commit. Tags, branches, `HEAD`, and unpinned URLs are refused.
- Nothing lands outside `rime/` and `rime/lua/<id>/`. Path components are
  validated on the way in; the manifest that records them is validated on the
  way back out, so a tampered registry or manifest cannot turn `remove` into an
  arbitrary delete. (This is the same `_safe_relative` discipline the core
  adopted in v0.1 after finding exactly that bug.)
- No symlinks are installed, no destination symlink is followed, no file over
  64 MB is copied, no `.git` survives the install.
- Lua is namespaced by directory and cannot be installed without a declared,
  confirmed capability. An extension cannot name another extension's Lua.
- Settings values cannot inject YAML or INI, because every landing site quotes.
- Patches cannot express includes, deletions, or raw documents.
- The panel executes nothing from an extension. It renders four control types
  from JSON the core produced.
- The install summary is generated from the validated manifest and the
  validated tree, not from the manifest's own description of itself. An
  extension that says "no Lua" and ships Lua is refused before the summary.

**Explicitly not guaranteed:**

- What confirmed Lua does. It runs in librime as the user. It can read any file
  the user can read and block the keystroke thread. The line cap and linear
  scans in the core's own filter are the core's; an extension's filter has
  whatever discipline its author gave it.
- What a hostile `.dict.yaml` or `.schema.yaml` does inside librime. The core
  bounds what reaches librime; it does not audit librime.
- Anything about a `--local` extension. That path is for the developer's own
  code.

`SECURITY.md` gains a section saying this.

## Removing, updating, listing

```
omarchy.ime extension remove <id>       sweeps ext:<id> from the manifest (restore
                                        replaced, delete created), removes the
                                        settings block, the checkout, and the
                                        registry entry; then says to deploy
omarchy.ime extension update <id>@<sha> fetch + validate + conflict check as for
                                        add; re-confirm only if capabilities grew;
                                        swap the checkout atomically; say to deploy
omarchy.ime extension list [--json]     id, name, version, source, sha, capabilities,
                                        local?, and whether the last deploy included it
```

A removed extension's schemas leave the picker; a selection naming one is
trimmed with a notice at the next deploy. Its settings are removed from
`shell.json`, not kept "in case".

## Versioning

The core declares `extensionApi: 1` and its own semantic version. An extension
declares which `extensionApi` it was written for and a `>=` constraint on the
core. Both are checked at `add` and at `update`, and again at deploy — a core
upgrade that drops an API version refuses to deploy an extension written for it
and says which one, rather than deploying something it no longer understands.

## What leaves the core

| Today                                                                                                                 | v0.2                                                                                                                             |
| --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `payload/rime/lua/annotate.lua`, CC-CEDICT fetch and verification, `annotate*` settings, the annotation patch         | **`omarchy-ime-annotation`** — first-party extension, its own repository                                                         |
| `payload/rime-japanese/*`, `rime-japanese`/`rime-snomiao` sources, `japanese_tw_eng` and `sno_japanese` in the picker | **`omarchy-ime-japanese`** — first-party extension, its own repository; the derived schemas and their attribution travel with it |
| —                                                                                                                     | **`omarchy-ime-translation`** — later, per `docs/TODO.md`                                                                        |

The core keeps: Fcitx5 configuration, `rime_ice` and its patches,
`traditionalize`, `pageSize`, `verticalCandidates`, the theme and its hook, the
background images, the profile merge, the environment override, the schema
families that ship in `rime-data` (they need no download, so they are not
extensions), the panel, the deploy transaction, packages.

The CC-CEDICT download machinery — bounded, verified, atomic — stays in the
core as the `https` source kind with the `cedict` verifier, because it is the
core's promise to the user about what any extension may download.

### Migration from v0.1

A v0.1 user has `annotateChinese` and friends at the top level of the widget
settings and may have Japanese schemas selected. v0.2's first deploy:

- moves `annotate*` keys into `extensions.annotation.*` if the annotation
  extension is registered, and otherwise leaves them in place and prints that
  the annotation extension is no longer built in, with the `add` command to
  get it back;
- trims `japanese_tw_eng` / `sno_japanese` from the selection with the same
  kind of notice if the Japanese extension is not registered;
- sweeps the v0.1-installed annotation and Japanese files through the existing
  manifest, since they are recorded there under the old group names.

Nothing is deleted from the user's Rime directory that the manifest does not
say v0.1 installed. Backups from v0.1 are untouched.

## CLI and panel, summarised

CLI: `extension add|remove|update|list`, plus `deploy` and `status` learning
about extensions (`status` lists them; `deploy` announces each one it
installs, and each source it fetches).

Panel: the existing settings area gains one section per extension after the
core's own; the Advanced area gains an "Extensions" list — name, version,
source, a "not yet deployed" mark when the registry is newer than the last
deploy — with no install control. The pending-deploy footer already covers
"you installed something, now deploy it".

## Testing

The v0.1 lesson stands: a green suite proved nothing five times. The extension
mechanism is tested at three levels, and the third is not optional.

1. **Unit** — manifest validation table-driven over good and hostile
   manifests; patch rendering compared byte-for-byte; settings mirror; registry
   read/write; conflict detection.
2. **Adversarial fixtures** — a directory of hostile extensions, each a real
   tree: undeclared Lua, symlink under `rime/`, `..` in a path, a `.schema.yaml`
   not listed in `schemas`, a `set` targeting `engine/`, a `filters+` given as a
   scalar, a manifest with an unknown key, a `lua_filter@other-id/…`
   reference, a 65 MB file, a registry entry pointing outside the extensions
   root, a `$key` reference to an undeclared setting. Every one must be refused
   at the step this document says refuses it, with the reason it says.
3. **Sandbox deploy** — `HOME` and all four XDG directories redirected, the
   real first-party annotation and Japanese extensions installed from local
   checkouts, a real deploy, `rime_deployer --build` on the result, then
   `extension remove` for each and a tree diff against the pre-install
   snapshot. The build must succeed; the diff must be empty.

## Open questions

- **Extension-to-extension dependencies.** Translation wants the annotation
  filter's dictionary loader? Probably not — it should not. Left out until a
  real need appears; the manifest has no `requires`.
- **Per-schema settings.** An extension might want a setting that applies to
  one schema only. Not in v0.2; a setting is global to the extension.
- **What `list` shows for an extension whose commit is no longer reachable
  upstream.** Nothing changes for the user — the checkout is local — but the
  panel could say "source unreachable" if it ever checks. It does not, in
  v0.2.

## Out of scope for v0.2

Browsing, ratings, signatures, a Lua sandbox, per-schema settings, extension
dependencies, panel-side install, auto-update, Omarchy marketplace listing of
extensions, and the translation extension itself.
