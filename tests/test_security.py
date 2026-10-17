"""Adversarial tests: what a hostile input is not allowed to reach.

Each test here names an actor from the plugin's threat model and the thing that
actor should not be able to do. They are grouped by who is doing the attacking
rather than by which module is attacked, because the question a reviewer asks is
"what can whoever controls X do", and the answer usually crosses two modules.

Nothing here needs a network, a compositor, a session bus or a real home
directory: every hostile artefact is written into a temporary tree.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import assets, deploy, generate, hooks, imestate, settings, theme

ROOT = Path(__file__).resolve().parent.parent
FILTER = ROOT / "payload" / "rime" / "lua" / "annotate.lua"

# omarchy.ime is an extensionless script rather than a module on the path, so it
# is loaded by location. Two of the writes worth attacking are its own.
_spec = importlib.util.spec_from_file_location(
    "omarchy_ime_security", ROOT / "omarchy.ime",
    loader=importlib.machinery.SourceFileLoader(
        "omarchy_ime_security", str(ROOT / "omarchy.ime")),
)
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)


def run_lua(body: str, timeout: float = 30.0) -> str:
    script = f'local M = dofile("{FILTER}")\n{body}\n'
    result = subprocess.run(
        ["lua", "-e", script], capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class ManifestCase(unittest.TestCase):
    """Shared fixture: one target directory, a state directory, and an outsider.

    The outsider is a file that no manifest has any business naming. Every test
    below writes a manifest that names it anyway.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.rime = self.root / "rime"
        self.rime.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.targets = {"rime": self.rime}
        self.outsider = self.root / "bashrc"
        self.outsider.write_text("the user's own file\n", encoding="utf-8")

    def write_manifest(self, files: dict) -> None:
        (self.state / deploy.MANIFEST_NAME).write_text(
            json.dumps({"version": 2, "files": files}, indent=2) + "\n",
            encoding="utf-8",
        )

    def plant_backup(self, inside: str, text: str) -> None:
        path = self.state / "backups" / inside
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class HostileManifestTests(ManifestCase):
    """Whoever can write ~/.local/state/omarchy.ime/installed.json.

    The manifest is the plugin's own state, written by a deploy and read back by
    an uninstall in a later process. The whitelist that decides what a deploy may
    write runs at write time; by the time uninstall reads the file back there is
    no whitelist left, only paths. So the paths are checked where they are read.
    """

    def test_a_traversing_entry_does_not_delete_the_file_it_names(self):
        self.write_manifest({"rime": {"../bashrc": {"replaced": False}}})
        result = deploy.uninstall(self.state, self.targets)
        self.assertTrue(self.outsider.is_file())
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")
        self.assertEqual(result["removed"], {})

    def test_an_absolute_entry_does_not_delete_the_file_it_names(self):
        # `Path(root) / "/etc/passwd"` is `/etc/passwd`: joining an absolute
        # right-hand side discards the root entirely.
        self.write_manifest({"rime": {str(self.outsider): {"replaced": False}}})
        deploy.uninstall(self.state, self.targets)
        self.assertTrue(self.outsider.is_file())

    def test_a_traversing_entry_does_not_overwrite_the_file_it_names(self):
        # The restore half of uninstall copies a backup over the path the entry
        # names. With both halves under the attacker's control that is an
        # arbitrary write of arbitrary content.
        self.plant_backup("payload", "attacker content\n")
        self.write_manifest({
            "rime": {"../bashrc": {"replaced": True, "backup": "payload"}},
        })
        deploy.uninstall(self.state, self.targets)
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")

    def test_a_backup_from_outside_the_backup_directory_is_not_restored(self):
        # The `backup` path is joined onto state/backups and copied *from*. A
        # traversing one reads any file the user can read and writes it into the
        # Rime directory under a name of the attacker's choosing.
        secret = self.root / "secret"
        secret.write_text("id_ed25519\n", encoding="utf-8")
        (self.rime / "victim.yaml").write_text("original\n", encoding="utf-8")
        self.write_manifest({
            "rime": {"victim.yaml": {"replaced": True, "backup": "../../secret"}},
        })
        deploy.uninstall(self.state, self.targets)
        self.assertNotEqual(
            (self.rime / "victim.yaml").read_text(encoding="utf-8"), "id_ed25519\n"
        )

    def test_a_backup_reached_through_a_symlink_is_not_restored(self):
        # Lexically the path stays inside backups; only resolving it says
        # otherwise. So it is resolved.
        secret = self.root / "secret"
        secret.write_text("id_ed25519\n", encoding="utf-8")
        backups = self.state / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "elsewhere").symlink_to(self.root)
        (self.rime / "victim.yaml").write_text("original\n", encoding="utf-8")
        self.write_manifest({
            "rime": {"victim.yaml": {"replaced": True, "backup": "elsewhere/secret"}},
        })
        result = deploy.uninstall(self.state, self.targets)
        self.assertEqual(result["restored"], {})
        self.assertNotIn(
            "id_ed25519",
            "".join(path.read_text(encoding="utf-8")
                    for path in self.rime.rglob("*") if path.is_file()),
        )

    def test_a_replaced_file_whose_backup_is_gone_is_left_alone(self):
        # The other half of refusing a backup: having refused to read it, the
        # uninstall must not fall through to deleting the file it was supposed
        # to put back. That file was somebody else's before this plugin wrote
        # over it, which is the whole reason it has a backup at all.
        (self.rime / "victim.yaml").write_text("theirs\n", encoding="utf-8")
        self.write_manifest({
            "rime": {"victim.yaml": {"replaced": True, "backup": "stamp/gone"}},
        })
        result = deploy.uninstall(self.state, self.targets)
        self.assertEqual((self.rime / "victim.yaml").read_text(encoding="utf-8"),
                         "theirs\n")
        self.assertEqual(result["removed"], {})

    def test_a_restore_does_not_write_through_a_symlink_at_its_destination(self):
        # The entry is honest; the destination is not. copy2 opens the
        # destination for writing, which follows the link.
        self.plant_backup("stamp/rime/victim.yaml", "restored\n")
        (self.rime / "victim.yaml").symlink_to(self.outsider)
        self.write_manifest({
            "rime": {
                "victim.yaml": {"replaced": True, "backup": "stamp/rime/victim.yaml"},
            },
        })
        deploy.uninstall(self.state, self.targets)
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")

    def test_a_traversing_entry_does_not_survive_being_read_back(self):
        # Stated at the level the fix lives at, so that every future reader of
        # the manifest inherits it rather than each one re-checking.
        self.write_manifest({
            "rime": {
                "../bashrc": {"replaced": False},
                "/etc/passwd": {"replaced": False},
                "ok.yaml": {"replaced": False},
            },
        })
        self.assertEqual(set(deploy.read_manifest(self.state)["rime"]), {"ok.yaml"})

    def test_the_deploy_sweep_does_not_delete_what_a_traversing_entry_names(self):
        # The stale-file sweep is the other reader of the manifest: it removes
        # what the previous deploy wrote and this one did not.
        self.write_manifest({"rime": {"../bashrc": {"replaced": False}}})
        deploy.deploy(
            settings.resolve({}), self.targets, self.state, self.root / "payload",
            asset_provider=lambda name: None,
            dictionary_path="/nonexistent/cedict.txt",
            theme_source=lambda: theme.resolved(theme.FALLBACK_PALETTE, 0, ""),
        )
        self.assertTrue(self.outsider.is_file())

    def test_pruning_empty_directories_never_climbs_out_of_its_target(self):
        # _prune_empty walks upward from a removed file removing empty
        # directories. Given a relative path that starts above the root, the
        # walk starts above the root too.
        above = self.root / "empty"
        above.mkdir()
        deploy._prune_empty(self.rime, "../empty/file.yaml")
        self.assertTrue(above.is_dir())


class HostileSymlinkTests(ManifestCase):
    """Another local process that can plant a symlink where a write will land."""

    def test_a_deploy_does_not_write_through_a_symlink_at_a_destination(self):
        (self.rime / "default.custom.yaml").symlink_to(self.outsider)
        deploy.deploy(
            settings.resolve({}), self.targets, self.state, self.root / "payload",
            asset_provider=lambda name: None,
            dictionary_path="/nonexistent/cedict.txt",
            theme_source=lambda: theme.resolved(theme.FALLBACK_PALETTE, 0, ""),
        )
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")
        self.assertFalse((self.rime / "default.custom.yaml").is_symlink())
        self.assertIn("patch:",
                      (self.rime / "default.custom.yaml").read_text(encoding="utf-8"))


    def test_a_symlink_in_a_fetched_clone_is_not_copied_into_the_rime_directory(self):
        # The end-to-end version of _usable_source: a hostile pinned revision
        # whose whitelisted file is a link to something the user can read.
        secret = self.root / "id_ed25519"
        secret.write_text("PRIVATE KEY\n", encoding="utf-8")
        clone = self.root / "clone"
        clone.mkdir()
        for inside in assets.SOURCE_FILES["rime-japanese"]:
            (clone / inside).symlink_to(secret)
        deploy.deploy(
            settings.resolve({"schemas": ["rime_ice", "sno_japanese"]}),
            self.targets, self.state, self.root / "payload",
            asset_provider=lambda name: clone if name == "rime-japanese" else None,
            dictionary_path="/nonexistent/cedict.txt",
            theme_source=lambda: theme.resolved(theme.FALLBACK_PALETTE, 0, ""),
        )
        for path in self.rime.rglob("*"):
            if path.is_file():
                self.assertNotIn("PRIVATE KEY",
                                 path.read_text(encoding="utf-8", errors="replace"))

    def test_the_theme_hook_is_not_written_through_a_symlink(self):
        # theme-set.d is shared with every other plugin on the machine.
        directory = self.root / "hooks"
        directory.mkdir()
        (directory / hooks.HOOK_NAME).symlink_to(self.outsider)
        hooks.install(directory, self.root / "omarchy.ime", self.state)
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")
        self.assertFalse((directory / hooks.HOOK_NAME).is_symlink())
        self.assertIn("theme apply",
                      (directory / hooks.HOOK_NAME).read_text(encoding="utf-8"))

    def test_the_theme_hooks_own_write_does_not_follow_a_symlink(self):
        # `theme apply` runs unattended on every theme change, which makes it
        # the least supervised write the plugin makes.
        themes = self.root / "themes"
        target = themes / theme.DIRECTORY / "theme.conf"
        target.parent.mkdir(parents=True)
        target.symlink_to(self.outsider)
        deploy.write_manifest(self.state, {"rime": {"default.custom.yaml":
                                                    {"replaced": False}}})
        manager.apply_theme(
            themes, self.state,
            lambda: {f"{theme.DIRECTORY}/theme.conf": "[Metadata]\nName=x\n"},
            lambda: True,
        )
        self.assertEqual(self.outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")
        self.assertFalse(target.is_symlink())


class HostileSettingsTests(unittest.TestCase):
    """Whoever can hand-edit shell.json.

    `settings.resolve` is deliberately lenient - a typo in a hand-edited file
    must not cost anyone their input method - so it is the wrong place to trust.
    Every value it hands back still lands in a file some other program parses.
    """

    def rendered(self, **values) -> str:
        resolved = settings.resolve(values)
        return theme.render_theme_conf(
            theme.FALLBACK_PALETTE, 0,
            background=theme.background_for(theme.FALLBACK_PALETTE, resolved),
        )

    def test_a_newline_in_a_background_path_cannot_open_a_theme_key(self):
        # theme.conf is an INI file classicui parses. A path carrying a newline
        # is a path that writes the line after it as well.
        text = self.rendered(
            backgroundImageDark="/tmp/a.png\nBorderWidth=64\n[Menu/Background]\nImage=/etc/shadow"
        )
        self.assertNotIn("BorderWidth=64", text)
        self.assertNotIn("Image=/etc/shadow", text)

    def test_a_relative_background_path_is_refused(self):
        # classicui resolves a relative Image inside the theme's own directory,
        # so `../../../../etc/x.png` reads outside it.
        self.assertEqual(
            settings.resolve({"backgroundImageDark": "../../../secret.png"})[
                "backgroundImageDark"], "")

    def test_a_control_character_in_a_background_path_is_refused(self):
        self.assertEqual(
            settings.resolve({"backgroundImageDark": "/tmp/a\r\npng"})[
                "backgroundImageDark"], "")

    def test_an_ordinary_absolute_path_still_reaches_the_theme(self):
        text = self.rendered(backgroundImageDark="/home/someone/Pictures/slate.png")
        self.assertIn("Image=/home/someone/Pictures/slate.png", text)

    def test_the_hotkey_stays_one_yaml_scalar(self):
        # The sibling of the background path, on the other side of the plugin:
        # this one lands in a YAML document librime compiles. A bare `"` would
        # close the scalar early and a newline would open a patch key of its
        # own, so the property asserted is that neither can be spelled - every
        # byte of the hostile value stays on the one line that quotes it.
        hostile = 'x"\n  key_binder/bindings/+: evil\n  annotate/chinese: false'
        rendered = generate.render(
            settings.resolve({"annotateHotkey": hostile}), "/tmp/cedict.txt",
        )
        for name, text in rendered.items():
            carriers = [line for line in text.splitlines() if "evil" in line]
            for line in carriers:
                self.assertTrue(line.startswith('    - { accept: "'), (name, line))
                self.assertTrue(line.endswith("when: composing }"), (name, line))
            # And the key it tried to open is nowhere in the document.
            for line in text.splitlines():
                self.assertNotEqual(line.strip(), "key_binder/bindings/+: evil")
                self.assertNotEqual(line.strip(), "annotate/chinese: false")

    def test_the_dictionary_path_stays_one_yaml_scalar(self):
        # The other value interpolated into the same file. It is derived from
        # XDG_CACHE_HOME rather than typed, which is a reason to quote it and
        # not a reason to trust it.
        rendered = generate.render(
            settings.resolve({}), '/tmp/c.txt"\n  annotate/chinese: false',
        )
        for text in rendered.values():
            for line in text.splitlines():
                self.assertNotEqual(line.strip(), "annotate/chinese: false")


class HostileThemeNameTests(unittest.TestCase):
    """Whoever can write ~/.local/state/omarchy/current/theme.name.

    Omarchy owns that file, but it is read off disk and interpolated into the
    generated theme's metadata, so it is treated as data like anything else read
    off disk.
    """

    def test_a_newline_in_the_theme_name_cannot_open_a_theme_key(self):
        text = theme.render_theme_conf(
            theme.FALLBACK_PALETTE, 0,
            source="nord\nScaleWithDPI=False\n[InputPanel/Background]\nImage=/etc/shadow",
        )
        carriers = [line for line in text.splitlines() if "shadow" in line]
        self.assertTrue(carriers)
        for line in carriers:
            self.assertTrue(line.startswith("Name=Omarchy ("), line)
        # And nothing the name tried to say is a line of its own.
        lines = text.splitlines()
        self.assertNotIn("ScaleWithDPI=False", lines)
        self.assertNotIn("Image=/etc/shadow", lines)
        self.assertEqual(lines.count("[InputPanel/Background]"), 1)


class HostileDictionaryTests(unittest.TestCase):
    """A compromised CC-CEDICT publisher.

    The filter runs inside librime, on the user's input path, on the first
    keystroke after the glosses are switched on. A loader that takes minutes
    over one line is a desktop-wide freeze with no way out of it.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cedict.u8"

    def write_entry(self, senses: str) -> None:
        self.path.write_text(f"T S [p1] /{senses}/\n", encoding="utf-8")

    def load(self) -> str:
        return run_lua(
            f'local d = M.load_dictionary("{self.path}")\n'
            'print(type(d.cn), type(d.en))',
            timeout=20.0,
        )

    def test_a_pipe_before_a_long_run_does_not_stall_the_loader(self):
        # `[^%s%p]*|` rescans and backtracks the whole run behind every byte
        # that is not followed by a `|`: quadratic, and reachable from one line.
        self.write_entry("|" + "a" * 400_000)
        self.assertEqual(self.load(), "table\ttable")

    def test_unclosed_brackets_do_not_stall_the_loader(self):
        # `(<chinese>)%[[^%]]*%]` scans to the end of the sense for a `]` that
        # is not there, once per bracket.
        self.write_entry("一[" * 100_000)
        self.assertEqual(self.load(), "table\ttable")

    def test_an_absurdly_long_line_is_skipped_rather_than_parsed(self):
        # A bound the loader can state, rather than one every pattern in it has
        # to hold on its own.
        self.write_entry("x" * 200_000)
        out = run_lua(
            f'local d = M.load_dictionary("{self.path}")\n'
            'print(tostring(M.gloss_for(d, "S", false)))',
            timeout=20.0,
        )
        self.assertEqual(out, "nil")

    def test_an_entry_with_no_delimiters_at_all_is_skipped(self):
        self.path.write_text("一" * 50_000 + "\n", encoding="utf-8")
        self.assertEqual(self.load(), "table\ttable")

    def test_invalid_utf8_does_not_break_the_loader(self):
        self.path.write_bytes(b"\xff\xfe S [p1] /gloss/\nT S [p1] /\xc3(\xa0/\n")
        self.assertEqual(self.load(), "table\ttable")


class HostileUpstreamTests(unittest.TestCase):
    """A compromised or malicious GitHub source.

    The two Rime sources are fetched by commit, so substituting content means
    finding a SHA-1 collision. What a hostile *pinned* revision can do is a
    different question, and these are the parts of the answer this plugin can
    decide on its own.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clone = Path(self._tmp.name) / "clone"
        self.clone.mkdir()

    def test_a_symlink_in_a_clone_is_not_copied_out_of_it(self):
        # `git checkout` writes whatever the tree says, symlinks included.
        secret = Path(self._tmp.name) / "id_ed25519"
        secret.write_text("PRIVATE KEY\n", encoding="utf-8")
        link = self.clone / "japanese.dict.yaml"
        link.symlink_to(secret)
        self.assertFalse(deploy._usable_source(link))

    def test_an_absurdly_large_file_in_a_clone_is_not_copied_out_of_it(self):
        big = self.clone / "japanese.dict.yaml"
        big.write_bytes(b"\0")
        self.assertTrue(deploy._usable_source(big))
        with open(big, "wb") as handle:
            handle.truncate(deploy.MAX_SOURCE_FILE_BYTES + 1)
        self.assertFalse(deploy._usable_source(big))

    def test_nothing_fetched_is_ever_executed(self):
        # The claim a reviewer most wants backed by a mechanism. Every fetched
        # path is a data file consumed by librime or OpenCC; none of them is
        # Lua, and none of them is run.
        fetched = [
            relative
            for files in assets.SOURCE_FILES.values()
            for relative in files.values()
        ]
        fetched.append(str(assets.dictionary_path(Path("/cache")).name))
        for relative in fetched:
            with self.subTest(relative):
                self.assertNotIn(".lua", relative)
                self.assertNotIn(".sh", relative)
                self.assertRegex(relative, r"\.(yaml|json|txt|u8)$")

    def test_the_filter_never_evaluates_anything_it_reads(self):
        # annotate.lua is shipped in this repository, not fetched, and it reads
        # the dictionary with io.open. If it ever grew a `load`, a `loadstring`
        # or a `dofile`, a hostile dictionary would become code.
        source = (ROOT / "payload" / "rime" / "lua" / "annotate.lua").read_text(
            encoding="utf-8")
        for evaluator in ("loadstring", "dofile", "loadfile", "require", "os.execute",
                          "io.popen", "load("):
            self.assertNotIn(evaluator, source, evaluator)

    def test_the_filter_is_shipped_rather_than_fetched(self):
        self.assertTrue((ROOT / "payload" / "rime" / "lua" / "annotate.lua").is_file())
        for files in assets.SOURCE_FILES.values():
            for relative in files.values():
                self.assertNotIn("lua", relative)


class BusReplyTests(unittest.TestCase):
    """A hostile answer on the session bus.

    `imestate` reads gdbus's printed GVariant text. It is scanned rather than
    evaluated, and these are the shapes that would matter if it were not.
    """

    def test_a_reply_that_is_not_a_tuple_of_strings_is_refused(self):
        for hostile in (
            "(__import__('os').system('id'),)",
            "([__import__('os')],)",
            "(1,)",
            "(true,)",
            "(['a', 2],)",
            "('unterminated,)",
            "@as []",
            "",
            "()",
            "('a')",
        ):
            with self.subTest(hostile):
                self.assertIsNone(imestate.parse_string_array(hostile))

    def test_a_single_string_reply_is_read_as_text_and_nothing_else(self):
        self.assertEqual(imestate.parse_string("('rime_ice',)"), "rime_ice")
        self.assertIsNone(imestate.parse_string("(os.getenv('HOME'),)"))
        self.assertIsNone(imestate.parse_string("(3,)"))

    def test_a_schema_id_off_the_bus_cannot_name_a_file_outside_the_build(self):
        # The one place a bus reply is joined onto a path.
        with TemporaryDirectory() as directory:
            outside = Path(directory) / "escaped.schema.yaml"
            outside.write_text("schema:\n  name: gotcha\n", encoding="utf-8")
            build = Path(directory) / "build"
            build.mkdir()
            self.assertIsNone(imestate.schema_name("../escaped", build))
            self.assertIsNone(imestate.schema_name("/etc/passwd", build))
            self.assertIsNone(imestate.schema_name("a/b", build))

    def test_a_schema_id_is_quoted_as_a_gvariant_string(self):
        # gdbus parses each argument as GVariant text and only falls back to a
        # plain string when that fails, so a bare `true` would arrive as a
        # boolean. Nothing here reaches a shell - the argument list is passed to
        # execve - so this is about the type, not about metacharacters.
        self.assertEqual(imestate.quote_variant("it's"), "'it\\'s'")
        self.assertEqual(imestate.quote_variant("true"), "'true'")


class SubprocessSurfaceTests(unittest.TestCase):
    """No shell, anywhere, on any path.

    Stated as a test rather than as a sentence in a document, because the
    sentence is the kind that stops being true when somebody adds a feature.
    """

    def sources(self):
        """Every file that ships and can run something: the plugin, not its tests.

        The tests are excluded because a test that asserts `shell=True` is
        absent is a file containing the string `shell=True`, and a scan that
        cannot tell those apart is a scan that has to be weakened until it says
        nothing.
        """
        paths = sorted((ROOT / "lib").glob("*.py"))
        paths.append(ROOT / "omarchy.ime")
        paths.append(ROOT / "Panel.qml")
        for path in paths:
            yield path, path.read_text(encoding="utf-8")

    def test_every_running_source_is_actually_scanned(self):
        scanned = {path.name for path, _ in self.sources()}
        for name in ("deploy.py", "assets.py", "packages.py", "imestate.py",
                     "hyprland.py", "theme.py", "omarchy.ime", "Panel.qml"):
            self.assertIn(name, scanned)

    def test_no_subprocess_call_uses_a_shell(self):
        runners = 0
        for path, body in self.sources():
            if "subprocess" in body:
                runners += 1
            with self.subTest(path.name):
                self.assertNotIn("shell=True", body)
                self.assertNotIn("os.system", body)
                self.assertNotIn("os.popen", body)
                self.assertNotIn("subprocess.getoutput", body)
                self.assertNotIn("subprocess.getstatusoutput", body)
        self.assertGreater(runners, 0, "the scan matched no source that runs anything")

    def test_the_one_command_built_as_shell_text_carries_no_user_input(self):
        # packages.install_script is the single place this plugin composes a
        # shell one-liner, for a terminal launcher that takes one. Every name in
        # it is filtered against the plugin's own constant lists first, so
        # nothing a caller passes can reach it.
        from lib import packages

        script = packages.install_script(
            ["fcitx5", "; rm -rf ~", "rime-ice-git", "$(id)"]
        )
        self.assertNotIn("rm -rf", script)
        self.assertNotIn("$(id)", script)
        self.assertIn("fcitx5", script)
        self.assertIn("rime-ice-git", script)


class ThemeHookTests(unittest.TestCase):
    """The Omarchy theme name, which arrives as the hook's `$1`.

    `omarchy-hook` runs `bash "$hook" "$THEME_NAME"` on every theme change. The
    name is Omarchy's, lowercased with spaces dashed, but it is still an
    argument this plugin's script receives and it is worth knowing where it
    lands.
    """

    def test_the_hook_never_uses_the_argument_it_is_given(self):
        # It cannot be mis-expanded if it is never expanded. `theme apply` reads
        # whichever palette Omarchy has just made current, which is the same
        # answer and is also right when the hook is run by hand.
        body = hooks.script("/home/someone/plugins/io.github.eastgold15.omarchy.ime/omarchy.ime")
        code = [line for line in body.splitlines()
                if line.strip() and not line.startswith("#")]
        for line in code:
            self.assertNotIn("$1", line)
            self.assertNotIn("$@", line)
            self.assertNotIn("$*", line)

    def test_every_expansion_in_the_hook_is_quoted(self):
        body = hooks.script("/home/someone/A Plugin Dir/io.github.eastgold15.omarchy.ime/omarchy.ime")
        code = [line for line in body.splitlines()
                if line.strip() and not line.startswith("#")]
        for line in code:
            for unquoted in re.finditer(r'(?<!")\$[A-Za-z_][A-Za-z0-9_]*(?!")', line):
                # An assignment's right-hand side is quoted by shlex instead.
                self.assertIn("=", line[:unquoted.start()], line)
        self.assertIn("'/home/someone/A Plugin Dir/io.github.eastgold15.omarchy.ime/omarchy.ime'", body)

    def test_a_manager_path_with_metacharacters_is_quoted_whole(self):
        body = hooks.script("/tmp/x; rm -rf ~/'; touch /tmp/pwned; '")
        self.assertNotIn("rm -rf ~/'\n", body)
        assignment = [line for line in body.splitlines()
                      if line.startswith("MANAGER=")][0]
        self.assertEqual(
            assignment,
            "MANAGER=" + shlex.quote("/tmp/x; rm -rf ~/'; touch /tmp/pwned; '"),
        )


class AtomicWriteTests(unittest.TestCase):
    """The one temporary file this plugin creates, and who else can reach it."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.shell_json = self.root / "shell.json"
        self.shell_json.write_text(json.dumps({
            "bar": {"layout": {"right": [{"id": settings.PLUGIN_ID}]}},
        }), encoding="utf-8")

    def test_the_temporary_file_is_not_written_through_a_planted_symlink(self):
        # The temporary name is derived from the pid, which another process on
        # the same machine can guess. It lives in ~/.config/omarchy beside the
        # file it replaces, so a link planted at that name would take the write
        # somewhere else - and then `os.replace` would move the link over
        # shell.json.
        outsider = self.root / "bashrc"
        outsider.write_text("the user's own file\n", encoding="utf-8")
        planted = self.shell_json.with_name(
            self.shell_json.name + f".{os.getpid()}.tmp")
        planted.symlink_to(outsider)
        settings.save(self.shell_json, {"pageSize": 5})
        self.assertEqual(outsider.read_text(encoding="utf-8"),
                         "the user's own file\n")
        self.assertEqual(
            json.loads(self.shell_json.read_text(encoding="utf-8"))
            ["bar"]["layout"]["right"][0]["pageSize"], 5)

    def test_the_replacement_keeps_the_mode_the_file_already_had(self):
        self.shell_json.chmod(0o600)
        settings.save(self.shell_json, {"pageSize": 5})
        self.assertEqual(self.shell_json.stat().st_mode & 0o777, 0o600)


class PermissionTests(unittest.TestCase):
    """What the plugin leaves behind, and who can read it."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.rime = self.root / "rime"
        self.fcitx5 = self.root / "fcitx5"

    def test_the_state_directory_is_readable_by_its_owner_alone(self):
        # It holds the manifest, which says what an uninstall may delete, and
        # the backups, which are copies of the user's own configuration.
        deploy.write_manifest(self.state, {})
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_a_backup_keeps_the_mode_of_what_it_copied(self):
        private = self.fcitx5 / "profile"
        private.parent.mkdir(parents=True)
        private.write_text("secret\n", encoding="utf-8")
        private.chmod(0o600)
        deploy.deploy(
            settings.resolve({}),
            {"rime": self.rime, deploy.FCITX5_TARGET: self.fcitx5},
            self.state, self.root / "payload",
            asset_provider=lambda name: None,
            dictionary_path="/nonexistent/cedict.txt",
            theme_source=lambda: theme.resolved(theme.FALLBACK_PALETTE, 0, ""),
        )
        backups = list((self.state / "backups").rglob("profile"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / "backups").stat().st_mode & 0o777, 0o700)

    def test_replacing_a_file_never_widens_its_mode(self):
        private = self.fcitx5 / "conf" / "classicui.conf"
        private.parent.mkdir(parents=True)
        private.write_text("theirs\n", encoding="utf-8")
        private.chmod(0o600)
        deploy.deploy(
            settings.resolve({}),
            {"rime": self.rime, deploy.FCITX5_TARGET: self.fcitx5},
            self.state, self.root / "payload",
            asset_provider=lambda name: None,
            dictionary_path="/nonexistent/cedict.txt",
            theme_source=lambda: theme.resolved(theme.FALLBACK_PALETTE, 0, ""),
        )
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
