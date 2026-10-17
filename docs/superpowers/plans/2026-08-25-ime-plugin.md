# omarchy.ime Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Omarchy plugin that owns the whole input method — packages, Fcitx5 config, Rime config, dictionary assets — driven by toggles, plus bidirectional candidate annotation.

**Architecture:** `Panel.qml` is display and intent only; every filesystem write goes through the `omarchy.ime` CLI. `omarchy.ime` regenerates Rime configuration whole from settings rather than editing it, records what it wrote, and fetches heavy assets only for enabled features.

**Tech Stack:** Python 3 (stdlib only), Lua 5.4 (Rime filter), QML (Quickshell), `omarchy pkg` for packages.

**Spec:** `docs/superpowers/specs/2026-08-25-input-method-plugin-design.md`

## Global Constraints

- Python 3 stdlib only. No third-party packages, no PyYAML — Rime config is generated as text from templates.
- All code, comments, strings, and commit messages in English. No CJK except dictionary content and test fixtures.
- Plugin id is `io.github.eastgold15.omarchy.ime`. Cache root `~/.cache/omarchy.ime/`. State root `~/.local/state/omarchy.ime/`.
- Settings live inline on the widget's entry in `~/.config/omarchy/shell.json` under `bar.layout.<section>[]`, matched by `id == "io.github.eastgold15.omarchy.ime"`.
- Pinned sources: `rime-japanese` = `4c1e65135459175136f380e90ba52acb40fdfb2d`, `rime-snomiao` = `a2f3e7c7505abe567ec282ce61f754885c413e88`.
- Network failure must never leave the user unable to type. Missing assets degrade the feature, not the input method.
- Every generated file starts with a header marking it generated and naming the plugin.

## Task Dependencies

Task 1 first. Tasks 2, 5, 6, 7, 8 are independent of each other and may run in
parallel. Task 3 needs Task 2. Task 4 needs Task 3. Task 9 needs Tasks 2-8.

---

### Task 1: Repository skeleton and test runner

**Files:**

- Create: `omarchy.ime`, `lib/__init__.py`, `check`, `tests/test_smoke.py`, `.gitignore`, `README.md`

**Interfaces:**

- Produces: `./check` runs the whole suite; `omarchy.ime <command>` dispatches subcommands.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_smoke.py
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_manager_reports_version():
    result = subprocess.run(
        [sys.executable, str(ROOT / "omarchy.ime"), "--version"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_smoke -v`
Expected: FAIL — `omarchy.ime` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""Deploy and manage the Omarchy input method."""
import argparse
import sys

VERSION = "0.1.0"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="omarchy.ime", description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_subparsers(dest="command")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `lib/__init__.py` empty. Create `check`:

```bash
#!/usr/bin/env bash
# Run every check this repository knows how to run.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
echo "All checks passed."
```

`.gitignore`:

```
__pycache__/
*.pyc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `chmod +x omarchy.ime check && ./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: repository skeleton and test runner"
```

---

### Task 2: Settings resolution

**Files:**

- Create: `lib/settings.py`, `tests/test_settings.py`

**Interfaces:**

- Produces: `DEFAULTS: dict`, `resolve(raw: dict) -> dict`, `load(shell_json_path: Path) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import settings


class ResolveTests(unittest.TestCase):
    def test_defaults_apply_when_nothing_is_set(self):
        result = settings.resolve({})
        self.assertFalse(result["japanese"])
        self.assertTrue(result["englishInput"])
        self.assertEqual(result["pageSize"], 10)
        self.assertEqual(result["annotateHotkey"], "Control+e")

    def test_explicit_values_win(self):
        result = settings.resolve({"japanese": True, "pageSize": 5})
        self.assertTrue(result["japanese"])
        self.assertEqual(result["pageSize"], 5)

    def test_unknown_keys_are_dropped(self):
        result = settings.resolve({"id": "io.github.eastgold15.omarchy.ime", "nonsense": 1})
        self.assertNotIn("id", result)
        self.assertNotIn("nonsense", result)

    def test_page_size_is_clamped_to_what_rime_accepts(self):
        self.assertEqual(settings.resolve({"pageSize": 0})["pageSize"], 1)
        self.assertEqual(settings.resolve({"pageSize": 99})["pageSize"], 10)

    def test_wrong_types_fall_back_to_the_default(self):
        self.assertEqual(settings.resolve({"pageSize": "many"})["pageSize"], 10)
        self.assertFalse(settings.resolve({"japanese": "yes"})["japanese"])


class LoadTests(unittest.TestCase):
    def _shell_json(self, directory, entry):
        path = Path(directory) / "shell.json"
        path.write_text(json.dumps({
            "bar": {"layout": {"center": [{"id": "omarchy.clock"}, entry]}},
        }))
        return path

    def test_settings_come_from_the_widget_entry(self):
        with TemporaryDirectory() as directory:
            path = self._shell_json(directory, {"id": "io.github.eastgold15.omarchy.ime", "japanese": True})
            self.assertTrue(settings.load(path)["japanese"])

    def test_missing_widget_yields_defaults(self):
        with TemporaryDirectory() as directory:
            path = self._shell_json(directory, {"id": "other.widget"})
            self.assertFalse(settings.load(path)["japanese"])

    def test_missing_file_yields_defaults(self):
        self.assertFalse(settings.load(Path("/nonexistent/shell.json"))["japanese"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_settings -v`
Expected: FAIL — no module named `lib.settings`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/settings.py
"""Plugin settings: defaults, validation, and where they are read from.

Omarchy stores a bar widget's settings as extra keys on the widget's own entry
in shell.json, so `load` looks for the entry whose id is ours and treats the
rest of that object as settings.
"""
from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ID = "io.github.eastgold15.omarchy.ime"

# value, type, and for integers the accepted range
DEFAULTS = {
    "japanese": (False, bool, None),
    "englishInput": (True, bool, None),
    "annotateChinese": (True, bool, None),
    "annotateEnglish": (True, bool, None),
    "annotateShowPinyin": (True, bool, None),
    "annotateHotkey": ("Control+e", str, None),
    "traditionalize": (True, bool, None),
    "pageSize": (10, int, (1, 10)),
    "verticalCandidates": (True, bool, None),
}


def resolve(raw: dict) -> dict:
    """Merge caller-supplied values over the defaults.

    A value of the wrong type falls back to the default rather than raising:
    settings come from a hand-editable JSON file, and a typo there must not
    stop the input method from deploying.
    """
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
        resolved[key] = value
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
    layout = (document.get("bar") or {}).get("layout") or {}
    for section in layout.values():
        if not isinstance(section, list):
            continue
        for entry in section:
            if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                return resolve(entry)
    return resolve({})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/settings.py tests/test_settings.py
git commit -m "feat: resolve and load plugin settings"
```

---

### Task 3: Rime configuration generation

**Files:**

- Create: `lib/generate.py`, `tests/test_generate.py`

**Interfaces:**

- Consumes: `lib.settings.resolve`
- Produces: `render(settings: dict) -> dict[str, str]` mapping relative path to file content.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate.py
import unittest

from lib import generate, settings


def render(**overrides):
    return generate.render(settings.resolve(overrides))


class RenderTests(unittest.TestCase):
    def test_always_produces_the_two_customization_files(self):
        files = render()
        self.assertIn("default.custom.yaml", files)
        self.assertIn("rime_ice.custom.yaml", files)

    def test_generated_files_are_marked_as_generated(self):
        for content in render().values():
            self.assertIn("generated by io.github.eastgold15.omarchy.ime", content)

    def test_rendering_twice_is_identical(self):
        self.assertEqual(render(japanese=True), render(japanese=True))

    def test_japanese_schemas_appear_only_when_enabled(self):
        self.assertNotIn("japanese_tw_eng", render()["default.custom.yaml"])
        self.assertIn("japanese_tw_eng", render(japanese=True)["default.custom.yaml"])

    def test_rime_ice_is_always_in_the_schema_list(self):
        self.assertIn("rime_ice", render()["default.custom.yaml"])

    def test_page_size_is_carried_through(self):
        self.assertIn("page_size: 7", render(pageSize=7)["default.custom.yaml"])

    def test_traditionalize_patch_is_conditional(self):
        self.assertIn("s2twp.json", render()["rime_ice.custom.yaml"])
        self.assertNotIn("s2twp.json", render(traditionalize=False)["rime_ice.custom.yaml"])

    def test_annotation_filter_is_registered_only_when_a_direction_is_on(self):
        both_off = render(annotateChinese=False, annotateEnglish=False)["rime_ice.custom.yaml"]
        self.assertNotIn("annotate", both_off)
        one_on = render(annotateChinese=True, annotateEnglish=False)["rime_ice.custom.yaml"]
        self.assertIn("lua_filter@*annotate", one_on)

    def test_hotkey_binds_both_switches_while_composing(self):
        content = render(annotateHotkey="Control+j")["rime_ice.custom.yaml"]
        self.assertIn("Control+j", content)
        self.assertIn("when: composing", content)

    def test_english_input_can_be_removed(self):
        self.assertNotIn("melt_eng", render()["rime_ice.custom.yaml"])
        without = render(englishInput=False)["rime_ice.custom.yaml"]
        self.assertIn("melt_eng", without)

    def test_vertical_candidates_map_to_rime_style(self):
        self.assertIn("horizontal: false", render()["default.custom.yaml"])
        self.assertIn("horizontal: true", render(verticalCandidates=False)["default.custom.yaml"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_generate -v`
Expected: FAIL — no module named `lib.generate`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/generate.py
"""Turn settings into Rime customization files.

Files are rendered whole. Nothing here reads an existing file and edits part of
it: a partial write leaves Rime with a schema that silently stops loading, and
whole rendering makes a repeat deploy a no-op instead of a merge.
"""
from __future__ import annotations

HEADER = (
    "# This file is generated by io.github.eastgold15.omarchy.ime. Edits are lost on the next deploy.\n"
    "# Change the plugin's settings instead.\n"
)

BASE_SCHEMAS = ["rime_ice"]
JAPANESE_SCHEMAS = ["japanese_tw_eng", "sno_japanese"]


def _schema_list(settings: dict) -> list[str]:
    schemas = list(BASE_SCHEMAS)
    if settings["japanese"]:
        # Japanese first matches the previous dotfiles order, where the mixed
        # Japanese schema was the one reached by default.
        schemas = [JAPANESE_SCHEMAS[0]] + schemas + [JAPANESE_SCHEMAS[1]]
    return schemas


def _default_custom(settings: dict) -> str:
    lines = [HEADER, "patch:", "  __include: rime_ice_suggestion:/", "  __patch:", "    schema_list:"]
    for schema in _schema_list(settings):
        lines.append(f"      - {{ schema: {schema} }}")
    lines.append(f"    menu/page_size: {settings['pageSize']}")
    lines.append(f"    style/horizontal: {'false' if settings['verticalCandidates'] else 'true'}")
    if settings["traditionalize"]:
        lines.append('    "traditionalize/opencc_config": s2twp.json')
    return "\n".join(lines) + "\n"


def _rime_ice_custom(settings: dict) -> str:
    annotating = settings["annotateChinese"] or settings["annotateEnglish"]
    lines = [HEADER, "patch:"]
    if settings["traditionalize"]:
        lines.append("  traditionalize/opencc_config: s2twp.json")
    if not settings["englishInput"]:
        # Rime Ice enables melt_eng in the base schema, so turning English input
        # off means removing that translator rather than adding anything.
        lines.append('  "engine/translators/-": "table_translator@melt_eng"')
    if annotating:
        lines.append('  "engine/filters/+": "lua_filter@*annotate"')
        lines.append("  switches/+:")
        if settings["annotateChinese"]:
            lines.append("    - { name: english_comment, reset: 1, states: [ EN off, EN on ] }")
        if settings["annotateEnglish"]:
            lines.append("    - { name: chinese_comment, reset: 1, states: [ CN off, CN on ] }")
        lines.append("  key_binder/bindings/+:")
        hotkey = settings["annotateHotkey"]
        for switch, enabled in (
            ("english_comment", settings["annotateChinese"]),
            ("chinese_comment", settings["annotateEnglish"]),
        ):
            if enabled:
                lines.append(
                    f'    - {{ accept: "{hotkey}", toggle: {switch}, when: composing }}'
                )
        lines.append(f"  annotate/show_pinyin: {str(settings['annotateShowPinyin']).lower()}")
    return "\n".join(lines) + "\n"


def render(settings: dict) -> dict[str, str]:
    """Return every Rime file this configuration needs, keyed by relative path."""
    return {
        "default.custom.yaml": _default_custom(settings),
        "rime_ice.custom.yaml": _rime_ice_custom(settings),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/generate.py tests/test_generate.py
git commit -m "feat: generate Rime customization from settings"
```

---

### Task 4: Deployment with an install manifest

**Files:**

- Create: `lib/deploy.py`, `tests/test_deploy.py`
- Modify: `omarchy.ime`

**Interfaces:**

- Consumes: `lib.generate.render`
- Produces: `deploy(settings, rime_dir, state_dir, payload_dir) -> list[str]`, `uninstall(state_dir) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy.py
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import deploy, settings


class DeployTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rime = root / "rime"
        self.state = root / "state"
        self.payload = root / "payload"
        (self.payload / "opencc").mkdir(parents=True)
        (self.payload / "opencc" / "jpzh.json").write_text("{}")
        self.addCleanup(self._tmp.cleanup)

    def _deploy(self, **overrides):
        return deploy.deploy(
            settings.resolve(overrides), self.rime, self.state, self.payload
        )

    def test_generated_files_land_in_the_rime_directory(self):
        self._deploy()
        self.assertTrue((self.rime / "default.custom.yaml").is_file())

    def test_payload_files_are_copied(self):
        self._deploy()
        self.assertTrue((self.rime / "opencc" / "jpzh.json").is_file())

    def test_written_paths_are_recorded(self):
        self._deploy()
        recorded = json.loads((self.state / "installed.json").read_text())["files"]
        self.assertIn("default.custom.yaml", recorded)

    def test_deploying_twice_leaves_the_same_content(self):
        self._deploy()
        first = (self.rime / "default.custom.yaml").read_text()
        self._deploy()
        self.assertEqual(first, (self.rime / "default.custom.yaml").read_text())

    def test_a_file_no_longer_generated_is_removed_on_redeploy(self):
        self._deploy()
        stale = self.rime / "stale.yaml"
        stale.write_text("x")
        recorded = json.loads((self.state / "installed.json").read_text())
        recorded["files"].append("stale.yaml")
        (self.state / "installed.json").write_text(json.dumps(recorded))
        self._deploy()
        self.assertFalse(stale.exists())

    def test_a_pre_existing_file_is_backed_up_before_being_replaced(self):
        self.rime.mkdir(parents=True)
        (self.rime / "default.custom.yaml").write_text("mine")
        self._deploy()
        backups = list((self.state / "backups").rglob("default.custom.yaml"))
        self.assertEqual(backups[0].read_text(), "mine")

    def test_uninstall_removes_only_what_was_installed(self):
        self._deploy()
        keeper = self.rime / "user.yaml"
        keeper.write_text("keep")
        deploy.uninstall(self.state, self.rime)
        self.assertTrue(keeper.exists())
        self.assertFalse((self.rime / "default.custom.yaml").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_deploy -v`
Expected: FAIL — no module named `lib.deploy`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/deploy.py
"""Write the generated configuration and remember exactly what was written.

The manifest is what makes this reversible. Without it, uninstall would have to
guess which files in a shared Rime directory belong to the plugin, and a redeploy
that stops generating a file would leave it behind for Rime to keep loading.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from lib import generate

MANIFEST_NAME = "installed.json"


def _read_manifest(state_dir: Path) -> dict:
    try:
        return json.loads((state_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": []}


def _back_up(path: Path, relative: str, state_dir: Path, stamp: str) -> None:
    destination = state_dir / "backups" / stamp / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)


def _payload_files(payload_dir: Path) -> dict[str, Path]:
    if not payload_dir.is_dir():
        return {}
    return {
        str(path.relative_to(payload_dir)): path
        for path in payload_dir.rglob("*")
        if path.is_file()
    }


def deploy(settings: dict, rime_dir: Path, state_dir: Path, payload_dir: Path) -> list[str]:
    """Install generated and payload files into the Rime directory.

    Returns the relative paths written, in sorted order.
    """
    rime_dir = Path(rime_dir)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    contents = generate.render(settings)
    payload = _payload_files(Path(payload_dir))
    previous = set(_read_manifest(state_dir).get("files", []))
    written: list[str] = []

    for relative, text in sorted(contents.items()):
        target = rime_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and relative not in previous:
            _back_up(target, relative, state_dir, stamp)
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
        written.append(relative)

    for relative, source in sorted(payload.items()):
        target = rime_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and relative not in previous:
            _back_up(target, relative, state_dir, stamp)
        shutil.copy2(source, target)
        written.append(relative)

    for relative in sorted(previous - set(written)):
        stale = rime_dir / relative
        if stale.is_file():
            stale.unlink()

    (state_dir / MANIFEST_NAME).write_text(
        json.dumps({"files": sorted(written)}, indent=2) + "\n", encoding="utf-8"
    )
    return sorted(written)


def uninstall(state_dir: Path, rime_dir: Path) -> list[str]:
    """Remove every file this plugin installed, and nothing else."""
    state_dir, rime_dir = Path(state_dir), Path(rime_dir)
    removed = []
    for relative in _read_manifest(state_dir).get("files", []):
        target = rime_dir / relative
        if target.is_file():
            target.unlink()
            removed.append(relative)
    (state_dir / MANIFEST_NAME).write_text(
        json.dumps({"files": []}, indent=2) + "\n", encoding="utf-8"
    )
    return removed
```

Wire it into `omarchy.ime` by adding a `deploy` and an `uninstall` subparser that
call these with the real paths (`~/.local/share/fcitx5/rime`,
`~/.local/state/omarchy.ime`, `<plugin>/payload/rime`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/deploy.py tests/test_deploy.py omarchy.ime
git commit -m "feat: deploy configuration and track what was installed"
```

---

### Task 5: Package management

**Files:**

- Create: `lib/packages.py`, `tests/test_packages.py`

**Interfaces:**

- Produces: `REQUIRED: list[str]`, `AUR: list[str]`, `missing(runner) -> list[str]`, `install(names, runner) -> bool`

`runner` is a callable taking a command list and returning `(returncode, stdout)`, so tests
drive it without touching the system.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_packages.py
import unittest

from lib import packages


class FakeRunner:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        for prefix, result in self.results.items():
            if " ".join(command).startswith(prefix):
                return result
        return (0, "")


class MissingTests(unittest.TestCase):
    def test_nothing_missing_when_all_present(self):
        runner = FakeRunner({"omarchy pkg present": (0, "")})
        self.assertEqual(packages.missing(runner), [])

    def test_absent_packages_are_reported(self):
        runner = FakeRunner({"omarchy pkg present": (1, "")})
        self.assertEqual(packages.missing(runner), packages.REQUIRED + packages.AUR)


class InstallTests(unittest.TestCase):
    def test_official_and_aur_go_to_different_commands(self):
        runner = FakeRunner({})
        packages.install(["fcitx5", "rime-ice-git"], runner)
        joined = [" ".join(call) for call in runner.calls]
        self.assertIn("omarchy pkg add fcitx5", joined)
        self.assertIn("omarchy pkg aur add rime-ice-git", joined)

    def test_aur_is_skipped_when_unreachable(self):
        runner = FakeRunner({"omarchy pkg aur accessible": (1, "")})
        self.assertFalse(packages.install(["rime-ice-git"], runner))
        joined = [" ".join(call) for call in runner.calls]
        self.assertNotIn("omarchy pkg aur add rime-ice-git", joined)

    def test_installing_nothing_succeeds_without_calling_out(self):
        runner = FakeRunner({})
        self.assertTrue(packages.install([], runner))
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_packages -v`
Expected: FAIL — no module named `lib.packages`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/packages.py
"""Detect and install the packages the input method needs.

This is the one privileged thing the plugin does, so it is kept behind an
explicit call: nothing here runs as part of a deploy. Omarchy's own package
commands are used rather than pacman, so the plugin inherits whatever policy
the system has for AUR access and confirmation.
"""
from __future__ import annotations

import subprocess

REQUIRED = ["fcitx5", "fcitx5-configtool", "fcitx5-rime"]
AUR = ["rime-ice-git"]


def _run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout


def missing(runner=_run) -> list[str]:
    """Return the packages that are not installed.

    `omarchy pkg present` answers for a whole set at once, so a single call per
    group tells us whether that group needs attention.
    """
    absent = []
    for group in (REQUIRED, AUR):
        code, _ = runner(["omarchy", "pkg", "present"] + group)
        if code != 0:
            absent.extend(group)
    return absent


def install(names: list[str], runner=_run) -> bool:
    """Install the named packages. Returns False if any group could not be done."""
    official = [name for name in names if name in REQUIRED]
    aur = [name for name in names if name in AUR]
    ok = True
    if official:
        code, _ = runner(["omarchy", "pkg", "add"] + official)
        ok = ok and code == 0
    if aur:
        code, _ = runner(["omarchy", "pkg", "aur", "accessible"])
        if code != 0:
            return False
        code, _ = runner(["omarchy", "pkg", "aur", "add"] + aur)
        ok = ok and code == 0
    return ok
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/packages.py tests/test_packages.py
git commit -m "feat: detect and install required packages"
```

---

### Task 6: Pinned asset fetching

**Files:**

- Create: `lib/assets.py`, `tests/test_assets.py`, `sources/rime-japanese.rev`, `sources/rime-snomiao.rev`

**Interfaces:**

- Produces: `SOURCES: dict`, `cache_path(name, revision, cache_root) -> Path`, `ensure(name, cache_root, fetcher) -> Path | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assets.py
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import assets


class CachePathTests(unittest.TestCase):
    def test_revision_is_part_of_the_path(self):
        path = assets.cache_path("rime-japanese", "abc123", Path("/cache"))
        self.assertIn("abc123", str(path))

    def test_different_revisions_do_not_share_a_directory(self):
        one = assets.cache_path("rime-japanese", "aaa", Path("/cache"))
        two = assets.cache_path("rime-japanese", "bbb", Path("/cache"))
        self.assertNotEqual(one, two)


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_populated_cache_is_not_fetched_again(self):
        revision = assets.SOURCES["rime-japanese"]["revision"]
        target = assets.cache_path("rime-japanese", revision, self.cache)
        target.mkdir(parents=True)
        (target / ".complete").write_text("")
        calls = []
        assets.ensure("rime-japanese", self.cache, lambda *a: calls.append(a))
        self.assertEqual(calls, [])

    def test_a_fetch_marks_the_cache_complete(self):
        def fetcher(url, revision, destination):
            Path(destination).mkdir(parents=True, exist_ok=True)

        path = assets.ensure("rime-japanese", self.cache, fetcher)
        self.assertTrue((path / ".complete").is_file())

    def test_a_failed_fetch_returns_none_and_leaves_no_complete_marker(self):
        def fetcher(url, revision, destination):
            raise RuntimeError("network down")

        revision = assets.SOURCES["rime-japanese"]["revision"]
        self.assertIsNone(assets.ensure("rime-japanese", self.cache, fetcher))
        marker = assets.cache_path("rime-japanese", revision, self.cache) / ".complete"
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_assets -v`
Expected: FAIL — no module named `lib.assets`.

- [ ] **Step 3: Write minimal implementation**

Write `sources/rime-japanese.rev` containing `4c1e65135459175136f380e90ba52acb40fdfb2d`
and `sources/rime-snomiao.rev` containing `a2f3e7c7505abe567ec282ce61f754885c413e88`,
each a single line.

```python
# lib/assets.py
"""Fetch pinned external assets into a content-addressed cache.

Every asset is pinned, and the pin is part of its cache path, so changing a pin
is a new directory rather than an update in place - there is no state where half
the old revision and half the new one are on disk.

A fetch that fails returns None. Callers treat that as "this feature has no data
yet" and carry on: no network problem may stop the user from typing.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SOURCES = {
    "rime-japanese": {
        "url": "https://github.com/gkovacs/rime-japanese.git",
        "revision": "4c1e65135459175136f380e90ba52acb40fdfb2d",
    },
    "rime-snomiao": {
        "url": "https://github.com/snomiao/rime-snomiao.git",
        "revision": "a2f3e7c7505abe567ec282ce61f754885c413e88",
    },
}

COMPLETE_MARKER = ".complete"


def cache_path(name: str, revision: str, cache_root: Path) -> Path:
    return Path(cache_root) / "sources" / name / revision


def _git_fetch(url: str, revision: str, destination: str) -> None:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "fetch", "-q", "--depth", "1", url, revision],
                   cwd=destination, check=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=destination, check=True)


def ensure(name: str, cache_root: Path, fetcher=_git_fetch) -> Path | None:
    """Return the cached asset directory, fetching it if needed.

    The completion marker is written last. A directory without it is a fetch that
    died partway, and is discarded rather than trusted.
    """
    source = SOURCES[name]
    target = cache_path(name, source["revision"], cache_root)
    if (target / COMPLETE_MARKER).is_file():
        return target
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        fetcher(source["url"], source["revision"], str(target))
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        return None
    (target / COMPLETE_MARKER).write_text("", encoding="utf-8")
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/assets.py tests/test_assets.py sources/
git commit -m "feat: fetch pinned external sources into a cache"
```

---

### Task 7: Dictionary builder

**Files:**

- Create: `tools/build-dict.py`, `tests/test_build_dict.py`, `tests/fixtures/cedict-sample.u8`

**Interfaces:**

- Produces: `parse_line(line: str) -> tuple | None`, `build(lines) -> str`

Output format is one entry per line: `traditional<TAB>simplified<TAB>pinyin<TAB>gloss1; gloss2`.

- [ ] **Step 1: Write the failing test**

Fixture `tests/fixtures/cedict-sample.u8`:

```
# CC-CEDICT sample
你好 你好 [ni3 hao3] /hello/hi/
鮑魚 鲍鱼 [bao4 yu2] /abalone/
一 一 [yi1] /one/1/single/
```

```python
# tests/test_build_dict.py
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_dict", ROOT / "tools" / "build-dict.py")
build_dict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_dict)


class ParseTests(unittest.TestCase):
    def test_comments_are_skipped(self):
        self.assertIsNone(build_dict.parse_line("# a comment"))

    def test_blank_lines_are_skipped(self):
        self.assertIsNone(build_dict.parse_line("   "))

    def test_an_entry_yields_all_four_fields(self):
        entry = build_dict.parse_line("你好 你好 [ni3 hao3] /hello/hi/")
        self.assertEqual(entry, ("你好", "你好", "ni3 hao3", ["hello", "hi"]))

    def test_traditional_and_simplified_are_kept_apart(self):
        entry = build_dict.parse_line("鮑魚 鲍鱼 [bao4 yu2] /abalone/")
        self.assertEqual(entry[0], "鮑魚")
        self.assertEqual(entry[1], "鲍鱼")

    def test_malformed_lines_are_skipped_rather_than_raising(self):
        self.assertIsNone(build_dict.parse_line("nonsense without brackets"))


class BuildTests(unittest.TestCase):
    def test_output_is_tab_separated_with_glosses_joined(self):
        output = build_dict.build(["你好 你好 [ni3 hao3] /hello/hi/"])
        self.assertEqual(output.strip(), "你好\t你好\tni3 hao3\thello; hi")

    def test_sample_file_produces_one_line_per_entry(self):
        lines = (ROOT / "tests" / "fixtures" / "cedict-sample.u8").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(build_dict.build(lines).strip().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_build_dict -v`
Expected: FAIL — `tools/build-dict.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""Convert CC-CEDICT into the flat dictionary the annotation filter reads.

Run by hand when refreshing the dictionary; the result is published as a release
asset rather than committed, because it is several megabytes and changes on its
own schedule.

CC-CEDICT is CC-BY-SA 4.0. The output stays CC-BY-SA 4.0 and must ship with
attribution.
"""
from __future__ import annotations

import argparse
import re
import sys

# traditional simplified [pinyin] /gloss/gloss/
ENTRY = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")


def parse_line(line: str):
    """Return (traditional, simplified, pinyin, glosses) or None.

    Anything that does not match is skipped rather than raising: CC-CEDICT
    carries comments and the occasional malformed line, and one bad line must
    not cost the whole build.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    match = ENTRY.match(line)
    if not match:
        return None
    traditional, simplified, pinyin, glosses = match.groups()
    parts = [part.strip() for part in glosses.split("/") if part.strip()]
    if not parts:
        return None
    return traditional, simplified, pinyin, parts


def build(lines) -> str:
    out = []
    for line in lines:
        entry = parse_line(line)
        if entry is None:
            continue
        traditional, simplified, pinyin, glosses = entry
        out.append(f"{traditional}\t{simplified}\t{pinyin}\t{'; '.join(glosses)}")
    return "\n".join(out) + "\n" if out else ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="CC-CEDICT text file")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args(argv)
    with open(args.source, encoding="utf-8") as handle:
        text = build(handle)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/build-dict.py tests/test_build_dict.py tests/fixtures/
git commit -m "feat: convert CC-CEDICT into the annotation dictionary"
```

---

### Task 8: Lua annotation filter

**Files:**

- Create: `payload/rime/lua/annotate.lua`, `tests/test_annotate_lua.py`, `tests/fixtures/dict-sample.txt`

**Interfaces:**

- Produces: a Lua module exposing `load_dictionary(path)`, `gloss_for(entry, text, show_pinyin)`, and `filter(input, env)`.

The Python test shells out to `lua` so the filter is exercised as Lua, not as a
description of Lua.

- [ ] **Step 1: Write the failing test**

Fixture `tests/fixtures/dict-sample.txt`:

```
你好	你好	ni3 hao3	hello; hi
鮑魚	鲍鱼	bao4 yu2	abalone
```

```python
# tests/test_annotate_lua.py
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILTER = ROOT / "payload" / "rime" / "lua" / "annotate.lua"
FIXTURE = ROOT / "tests" / "fixtures" / "dict-sample.txt"


def run_lua(body: str) -> str:
    script = f'local M = dofile("{FILTER}")\n{body}\n'
    result = subprocess.run(["lua", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class DictionaryTests(unittest.TestCase):
    def test_a_chinese_term_finds_its_gloss(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "你好", false))'
        )
        self.assertEqual(out, "hello; hi")

    def test_pinyin_is_included_when_asked_for(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "你好", true))'
        )
        self.assertEqual(out, "ni3 hao3 · hello; hi")

    def test_traditional_and_simplified_both_resolve(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "鮑魚", false), M.gloss_for(d, "鲍鱼", false))'
        )
        self.assertEqual(out.split("\t"), ["abalone", "abalone"])

    def test_an_english_word_finds_its_chinese(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "abalone", false))'
        )
        self.assertEqual(out, "鲍鱼")

    def test_an_unknown_term_has_no_gloss(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(tostring(M.gloss_for(d, "zzz", false)))'
        )
        self.assertEqual(out, "nil")

    def test_a_missing_dictionary_loads_as_empty_rather_than_failing(self):
        out = run_lua(
            'local d = M.load_dictionary("/nonexistent/dict.txt")\n'
            'print(tostring(M.gloss_for(d, "你好", false)))'
        )
        self.assertEqual(out, "nil")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_annotate_lua -v`
Expected: FAIL — `annotate.lua` does not exist.

- [ ] **Step 3: Write minimal implementation**

```lua
-- payload/rime/lua/annotate.lua
--
-- Annotate candidates with their meaning in the other language: English on
-- Chinese candidates, Chinese on English ones.
--
-- The dictionary is loaded on first use rather than at deploy time, and a
-- dictionary that is absent loads as empty. Typing must keep working when the
-- download has not happened or has failed.

local M = {}

function M.load_dictionary(path)
  local dictionary = { cn = {}, en = {} }
  local handle = io.open(path, "r")
  if not handle then
    return dictionary
  end
  for line in handle:lines() do
    local traditional, simplified, pinyin, gloss =
      line:match("^([^\t]+)\t([^\t]+)\t([^\t]*)\t(.+)$")
    if traditional then
      local entry = { pinyin = pinyin, gloss = gloss, chinese = simplified }
      dictionary.cn[traditional] = entry
      dictionary.cn[simplified] = entry
      -- Only the first sense claims an English headword, so the common word
      -- wins over a rare homograph further down the file.
      for word in gloss:gmatch("[^;]+") do
        local key = word:match("^%s*(.-)%s*$")
        if key ~= "" and dictionary.en[key] == nil then
          dictionary.en[key] = entry
        end
      end
    end
  end
  handle:close()
  return dictionary
end

function M.gloss_for(dictionary, text, show_pinyin)
  local entry = dictionary.cn[text]
  if entry then
    if show_pinyin and entry.pinyin ~= "" then
      return entry.pinyin .. " · " .. entry.gloss
    end
    return entry.gloss
  end
  entry = dictionary.en[text]
  if entry then
    return entry.chinese
  end
  return nil
end

-- Rime entry point. Kept thin: everything above is testable without Rime.
function M.filter(input, env)
  local context = env.engine.context
  local want_english = context:get_option("english_comment")
  local want_chinese = context:get_option("chinese_comment")
  if not want_english and not want_chinese then
    for candidate in input:iter() do
      yield(candidate)
    end
    return
  end
  if env.dictionary == nil then
    env.dictionary = M.load_dictionary(env.dictionary_path)
  end
  for candidate in input:iter() do
    local gloss = M.gloss_for(env.dictionary, candidate.text, env.show_pinyin)
    if gloss then
      candidate:get_genuine().comment = gloss
    end
    yield(candidate)
  end
end

return M
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add payload/rime/lua/annotate.lua tests/test_annotate_lua.py tests/fixtures/dict-sample.txt
git commit -m "feat: annotate candidates with the other language"
```

---

### Task 9: Plugin manifest and panel

**Files:**

- Create: `manifest.json`, `Panel.qml`
- Modify: `README.md`

**Interfaces:**

- Consumes: `omarchy.ime status --json`, `omarchy.ime deploy`, `omarchy.ime packages install`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest.py
import json
import unittest
from pathlib import Path

from lib import settings

ROOT = Path(__file__).resolve().parent.parent


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    def test_identity_matches_the_settings_module(self):
        self.assertEqual(self.manifest["id"], settings.PLUGIN_ID)

    def test_declares_a_bar_widget_entry_point(self):
        self.assertIn("bar-widget", self.manifest["kinds"])
        self.assertEqual(self.manifest["entryPoints"]["barWidget"], "Panel.qml")

    def test_every_setting_is_exposed_with_a_matching_default(self):
        schema = {item["key"]: item for item in self.manifest["barWidget"]["schema"]}
        self.assertEqual(set(schema), set(settings.DEFAULTS))
        for key, (default, _kind, _bounds) in settings.DEFAULTS.items():
            self.assertEqual(schema[key]["defaultValue"], default, key)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_manifest -v`
Expected: FAIL — `manifest.json` does not exist.

- [ ] **Step 3: Write minimal implementation**

Write `manifest.json` with `id` `io.github.eastgold15.omarchy.ime`, `kinds` `["bar-widget"]`,
`entryPoints.barWidget` `Panel.qml`, and a `barWidget.schema` entry for every key
in `lib.settings.DEFAULTS`, each with the same `defaultValue`, a `boolean` type
for the flags, `integer` with `min` 1 and `max` 10 for `pageSize`, and `string`
for `annotateHotkey`.

Write `Panel.qml` following the structure of `ryuhzk.translation`'s panel: a
status section showing which packages are missing and whether the dictionary is
present, an install action, a deploy action, and the plugin's settings surfaced
by Omarchy. The panel shells out to `omarchy.ime`; it performs no filesystem work
of its own.

Add a `status --json` subcommand to `omarchy.ime` reporting missing packages,
dictionary presence, and the last deploy time, so the panel has one call to make.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add manifest.json Panel.qml README.md tests/test_manifest.py omarchy.ime
git commit -m "feat: plugin manifest and bar panel"
```

---

### Task 10: Input state indicator and switcher

**Files:**

- Create: `lib/imestate.py`, `tests/test_imestate.py`
- Modify: `Panel.qml`, `omarchy.ime`

**Interfaces:**

- Produces: `read(runner) -> dict` with keys `state` (`"closed"|"latin"|"active"`),
  `method` (str or None), `group` (str or None); `switch(name, runner) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_imestate.py
import unittest

from lib import imestate


class FakeRunner:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        for prefix, result in self.results.items():
            if " ".join(command).startswith(prefix):
                return result
        return (0, "")


class ReadTests(unittest.TestCase):
    def test_state_zero_is_closed(self):
        runner = FakeRunner({"fcitx5-remote": (0, "0\n")})
        self.assertEqual(imestate.read(runner)["state"], "closed")

    def test_state_one_is_latin(self):
        runner = FakeRunner({"fcitx5-remote": (0, "1\n")})
        self.assertEqual(imestate.read(runner)["state"], "latin")

    def test_state_two_is_active_and_carries_the_method_name(self):
        runner = FakeRunner({
            "fcitx5-remote -n": (0, "rime\n"),
            "fcitx5-remote -q": (0, "Default\n"),
            "fcitx5-remote": (0, "2\n"),
        })
        result = imestate.read(runner)
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["method"], "rime")
        self.assertEqual(result["group"], "Default")

    def test_fcitx_not_running_reads_as_closed_rather_than_raising(self):
        runner = FakeRunner({"fcitx5-remote": (1, "")})
        self.assertEqual(imestate.read(runner)["state"], "closed")


class SwitchTests(unittest.TestCase):
    def test_switching_calls_fcitx_with_the_name(self):
        runner = FakeRunner({})
        self.assertTrue(imestate.switch("rime", runner))
        self.assertIn(["fcitx5-remote", "-s", "rime"], runner.calls)

    def test_a_failed_switch_is_reported(self):
        runner = FakeRunner({"fcitx5-remote -s": (1, "")})
        self.assertFalse(imestate.switch("rime", runner))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_imestate -v`
Expected: FAIL — no module named `lib.imestate`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/imestate.py
"""Read and change the live Fcitx5 input state.

The bar indicator replaces omarchy.keyboard-layout, which shows the keyboard
layout - not what actually decides whether a keystroke becomes Chinese. That is
the Fcitx5 state, and `fcitx5-remote` is the supported way to ask for it.

Every failure reads as "closed". Fcitx5 not running is a normal state for a bar
item to render, not an error to propagate.
"""
from __future__ import annotations

import subprocess

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
```

Add an `omarchy.ime state --json` subcommand that prints `read()` as JSON, and
have `Panel.qml` poll it while open and once at construction. The bar item shows
`EN` for `latin`, the method name for `active`, and a muted dot for `closed`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/imestate.py tests/test_imestate.py Panel.qml omarchy.ime
git commit -m "feat: show and switch the live input state from the bar"
```

---

### Task 11: Adopt on this machine and retire the dotfiles module

**Files:**

- Modify: `~/.dotfiles/install`, `~/.dotfiles/check`, `~/.dotfiles/packages/official.txt`, `~/.dotfiles/packages/aur.txt`
- Delete: `~/.dotfiles/config/fcitx5/`, `~/.dotfiles/share/fcitx5/`, `~/.dotfiles/sources/rime-*.rev`

This task runs only after Tasks 1-10 are merged and a real deploy has been verified.

- [ ] **Step 1: Verify the plugin reproduces the current setup**

Deploy with settings matching today's dotfiles state (`japanese` on,
`traditionalize` on, `pageSize` 10, `verticalCandidates` on) into a scratch
directory and diff against `~/.local/share/fcitx5/rime`. Investigate every
difference before continuing.

- [ ] **Step 2: Deploy for real and confirm typing works**

Run `omarchy.ime deploy`, then type Chinese, switch schemas, and toggle the
annotation hotkey. All three must work before the fallback is removed.

- [ ] **Step 3: Remove the dotfiles module**

Delete `install_fcitx5` and its dispatch entry, the `fcitx5` case in `check`, the
payload directories, the pinned revision files, and the four package entries.

- [ ] **Step 4: Verify dotfiles still passes**

Run: `cd ~/.dotfiles && ./check`
Expected: PASS, with no `fcitx5` case remaining.

- [ ] **Step 5: Commit both repositories**

```bash
git -C ~/Work/omarchy.ime commit --allow-empty -m "chore: adopted on this machine"
git -C ~/.dotfiles add -A
git -C ~/.dotfiles commit -m "chore: retire the fcitx5 module in favour of io.github.eastgold15.omarchy.ime"
```
