import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lib import assets

ROOT = Path(__file__).resolve().parent.parent

# ime-manager is an extensionless script rather than a module on the path, so it
# is loaded by location with an explicit loader.
_spec = importlib.util.spec_from_file_location(
    "ime_manager", ROOT / "ime-manager",
    loader=importlib.machinery.SourceFileLoader("ime_manager", str(ROOT / "ime-manager")),
)
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)


# What lib/deploy.redeploy returns, in its two shapes. The error lines are the
# ones a real journal carried when a malformed patch key broke the schema.
REBUILT = {
    "triggered": True, "succeeded": True, "verified": True, "errors": [],
    "detail": "Rime rebuilt the schemas",
}
# Triggered, nothing complained, and no evidence either way - a journal that
# could not be read, or one belonging to a unit fcitx5 is not running under.
# Not a failure, and not something to call "rebuilt".
UNVERIFIED = {
    "triggered": True, "succeeded": True, "verified": False, "errors": [],
    "detail": "Rime was asked to rebuild, but could not read the journal (no journalctl)",
}
# Nothing to rebuild yet: the files are on disk and fcitx5 builds them when it
# starts. The natural state of a machine deploying for the first time, and for
# a long time the one that printed "input may be broken" and exited 1.
NOT_RUNNING = {
    "triggered": False, "succeeded": True, "verified": False, "errors": [],
    "detail": "fcitx5 is not running, so it will build this configuration when it starts",
}
NOT_REBUILT = {
    "triggered": True, "succeeded": False, "verified": True,
    "errors": [
        "E20260825 config_compiler.cc:216] error applying patch to engine/filters/+",
        "E20260825 config_compiler.cc:534] unresolved dependency: Patch(rime_ice.custom:patch <optional>)",
        "E20260825 config_component.cc:250] error building config: rime_ice.schema",
    ],
    "detail": "Rime refused to build the configuration",
}


class SmokeTests(unittest.TestCase):
    def test_manager_reports_version(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ime-manager"), "--version"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.strip())


class ManagerDictionaryTests(unittest.TestCase):
    """The manager reports the dictionary; assets.py decides where it lives."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "cache"
        # Every XDG home is redirected, not only the cache. `status` reads the
        # settings to say whether the dictionary is wanted at all, so no test
        # may read the machine's own shell.json - and `cmd_deploy` ends with
        # `hooks.sync`, which rewrites the install manifest wherever
        # XDG_STATE_HOME points. Redirecting the config home alone made the
        # hook look absent and dropped it from the machine's own manifest.
        self.config = Path(self._tmp.name) / "config"
        patcher = mock.patch.dict("os.environ", {
            "XDG_CACHE_HOME": str(self.cache), "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(Path(self._tmp.name) / "state"),
            "XDG_DATA_HOME": str(Path(self._tmp.name) / "data"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_settings(self, **values) -> None:
        path = self.config / "omarchy" / "shell.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bar": {"layout": {"right": [
            {"id": "io.github.eastgold15.omarchy.ime", **values},
        ]}}}), encoding="utf-8")

    def expected(self) -> Path:
        return assets.dictionary_path(manager.cache_dir())

    def test_the_manager_does_not_invent_its_own_dictionary_location(self):
        self.assertEqual(manager.dictionary_path(), self.expected())

    def test_the_path_carries_upstreams_own_filename(self):
        # The dictionary is CC-CEDICT as its publisher distributes it, cached
        # under the published name. There is no pin of ours in the path.
        self.assertEqual(manager.dictionary_path().name, assets.DICTIONARY["filename"])

    def _status(self) -> dict:
        with mock.patch.object(manager.packages, "missing", return_value=[]), \
             mock.patch.object(manager.imestate, "read", return_value={"state": "closed"}):
            return manager.status()

    def test_status_reports_a_missing_dictionary_as_absent(self):
        result = self._status()
        self.assertFalse(result["dictionary"])
        self.assertEqual(result["dictionary_path"], str(self.expected()))

    def test_status_reports_a_downloaded_dictionary_as_present(self):
        target = self.expected()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("downloaded dictionary", encoding="utf-8")
        self.assertTrue(self._status()["dictionary"])

    def test_status_reports_which_dictionary_release_is_in_use(self):
        target = self.expected()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("downloaded dictionary", encoding="utf-8")
        (target.parent / assets.COMPLETE_MARKER).write_text(json.dumps({
            "version": "1", "subversion": "0", "date": "2026-08-25T08:12:38Z",
            "entries": 124934, "sha256": "641f22aa333d20e5" + "0" * 48,
        }), encoding="utf-8")
        release = self._status()["dictionary_release"]
        self.assertEqual(release["date"], "2026-08-25T08:12:38Z")
        self.assertEqual(release["entries"], 124934)

    def test_status_reports_no_release_when_nothing_is_cached(self):
        self.assertIsNone(self._status()["dictionary_release"])

    def test_status_says_annotation_is_off_on_a_fresh_install(self):
        result = self._status()
        self.assertIs(result["annotation"], False)
        self.assertIs(result["dictionary_needed"], False)

    def test_status_says_the_dictionary_is_needed_once_annotation_is_on(self):
        self.write_settings(annotation=True)
        result = self._status()
        self.assertIs(result["annotation"], True)
        self.assertIs(result["dictionary_needed"], True)

    def test_a_cached_dictionary_is_still_reported_present_while_not_needed(self):
        target = self.expected()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("downloaded dictionary", encoding="utf-8")
        result = self._status()
        self.assertTrue(result["dictionary"])
        self.assertFalse(result["dictionary_needed"])

    def _printed(self, **report) -> str:
        base = {
            "packages_missing": [], "packages_error": "",
            "dictionary": False, "dictionary_path": "/x",
            "dictionary_release": None, "dictionary_needed": True,
            "annotation": True, "deployed": True, "hook": True,
            "hypr": {"blur": "on", "toggle": "on"},
            "input": {"state": "latin"},
        }
        base.update(report)
        printed = io.StringIO()
        with mock.patch.object(manager, "status", return_value=base), \
                contextlib.redirect_stdout(printed):
            manager.cmd_status(mock.Mock(json=False))
        return printed.getvalue()

    def test_the_printed_status_says_annotation_is_off_and_why_there_is_no_dictionary(self):
        out = self._printed(annotation=False, dictionary_needed=False)
        self.assertIn("annotation: off", out)
        self.assertIn("dictionary: not needed", out)
        self.assertNotIn("not downloaded", out)
        # And how to turn it on, so nobody has to read the docs to find out.
        self.assertIn("annotation=true", out)

    def test_the_printed_status_reports_a_missing_dictionary_only_when_it_is_wanted(self):
        out = self._printed(annotation=True, dictionary_needed=True, dictionary=False)
        self.assertIn("annotation: on", out)
        self.assertIn("dictionary: not downloaded", out)

    def test_the_printed_status_reports_a_present_dictionary(self):
        out = self._printed(annotation=True, dictionary_needed=True, dictionary=True)
        self.assertIn("dictionary: present", out)

    def test_the_json_status_carries_both_facts_for_the_panel(self):
        printed = io.StringIO()
        with mock.patch.object(manager.packages, "missing", return_value=[]), \
             mock.patch.object(manager.imestate, "read", return_value={"state": "closed"}), \
             contextlib.redirect_stdout(printed):
            manager.cmd_status(mock.Mock(json=True))
        report = json.loads(printed.getvalue())
        self.assertIn("annotation", report)
        self.assertIn("dictionary_needed", report)

    def test_the_release_reaches_the_printed_status(self):
        printed = io.StringIO()
        with mock.patch.object(manager, "status", return_value={
                "packages_missing": [], "packages_error": "",
                "dictionary": True, "dictionary_path": "/x",
                "dictionary_release": {"version": "1", "subversion": "0",
                                       "date": "2026-08-25T08:12:38Z",
                                       "entries": 124934, "sha256": "a" * 64},
                "deployed": True, "hook": True,
                "hypr": {"blur": "on", "toggle": "on"},
                "input": {"state": "latin"}}), \
                contextlib.redirect_stdout(printed):
            manager.cmd_status(mock.Mock(json=False))
        self.assertIn("2026-08-25T08:12:38Z", printed.getvalue())

    def test_deploy_asks_to_be_told_what_the_dictionary_did(self):
        # Without a notice the download is silent, which is exactly the state
        # a mutable upstream must not be left in.
        with mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/x")) as resolve, \
             mock.patch.object(manager.deploy_lib, "deploy", return_value={}), \
             mock.patch.object(manager.deploy_lib, "redeploy", return_value=REBUILT), \
             mock.patch.object(manager.settings, "load",
                               return_value=manager.settings.resolve({})), \
             contextlib.redirect_stdout(io.StringIO()):
            manager.cmd_deploy(None)
        self.assertIsNotNone(resolve.call_args.kwargs.get("notice"))

    def test_deploy_hands_the_resolved_dictionary_path_to_the_deploy_step(self):
        resolved = self.cache / "somewhere" / "cedict.txt"
        recorded = {}

        def fake_deploy(*args, **kwargs):
            recorded.update(kwargs)
            return {}

        with mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=resolved) as resolve, \
             mock.patch.object(manager.deploy_lib, "deploy", side_effect=fake_deploy), \
             mock.patch.object(manager.deploy_lib, "redeploy", return_value=REBUILT), \
             mock.patch.object(manager.settings, "load",
                               return_value=manager.settings.resolve({})), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(manager.cmd_deploy(None), 0)
        self.assertEqual(recorded["dictionary_path"], resolved)
        self.assertEqual(resolve.call_args.args[1], manager.cache_dir())


class ManagerSettingsCommandTests(unittest.TestCase):
    """`ime-manager settings` is the only way the panel changes anything.

    The panel passes every value as a string, so the parsing here is what turns
    a clicked toggle into a real boolean in shell.json.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name)
        self.shell_json = self.config / "omarchy" / "shell.json"
        self.shell_json.parent.mkdir(parents=True)
        self.write_shell_json({
            "bar": {"layout": {"right": [
                {"id": "io.github.eastgold15.omarchy.ime", "pageSize": 7},
                {"id": "omarchy.battery", "warnAt": 15},
            ]}},
        })
        patcher = mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.config)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_shell_json(self, document: dict) -> None:
        self.shell_json.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def entry(self) -> dict:
        document = json.loads(self.shell_json.read_text(encoding="utf-8"))
        for candidate in document["bar"]["layout"]["right"]:
            if candidate.get("id") == "io.github.eastgold15.omarchy.ime":
                return candidate
        raise AssertionError("the widget entry disappeared")

    def run_manager(self, *arguments) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "ime-manager"), *arguments],
            capture_output=True, text=True,
            env={**os.environ, "XDG_CONFIG_HOME": str(self.config)},
        )

    def test_get_prints_the_resolved_settings_as_json(self):
        result = self.run_manager("settings", "get", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["pageSize"], 7)
        self.assertEqual(payload["annotateHotkey"], "Control+e")
        self.assertEqual(payload["schemas"], ["rime_ice"])
        self.assertIs(payload["annotation"], False)

    def test_set_turns_annotation_on(self):
        result = self.run_manager("settings", "set", "annotation=true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.entry()["annotation"], True)

    def test_set_parses_true_and_false_as_booleans(self):
        result = self.run_manager(
            "settings", "set", "annotateShowPinyin=true", "traditionalize=false"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entry()
        self.assertIs(entry["annotateShowPinyin"], True)
        self.assertIs(entry["traditionalize"], False)

    def test_set_parses_a_comma_separated_selection_as_a_list(self):
        result = self.run_manager("settings", "set", "schemas=rime_ice,bopomofo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.entry()["schemas"], ["rime_ice", "bopomofo"])

    def test_set_refuses_a_selection_with_nothing_to_compose_with(self):
        result = self.run_manager("settings", "set", "schemas=")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        self.assertNotIn("schemas", self.entry())

    def test_set_refuses_the_retired_english_switch(self):
        result = self.run_manager("settings", "set", "englishInput=false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown setting", result.stderr)
        self.assertNotIn("englishInput", self.entry())

    def test_set_parses_digits_as_integers(self):
        self.assertEqual(self.run_manager("settings", "set", "pageSize=5").returncode, 0)
        self.assertIs(type(self.entry()["pageSize"]), int)
        self.assertEqual(self.entry()["pageSize"], 5)

    def test_set_leaves_anything_else_a_string(self):
        self.assertEqual(self.run_manager("settings", "set", "annotateHotkey=Control+g").returncode, 0)
        self.assertEqual(self.entry()["annotateHotkey"], "Control+g")

    def test_set_prints_the_resolved_result(self):
        result = self.run_manager("settings", "set", "--json", "pageSize=3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["pageSize"], 3)

    def test_set_refuses_an_unknown_key(self):
        result = self.run_manager("settings", "set", "nonsense=true")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        self.assertNotIn("nonsense", self.entry())

    def test_set_refuses_a_bad_value(self):
        result = self.run_manager("settings", "set", "pageSize=99")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())
        self.assertEqual(self.entry()["pageSize"], 7)

    def test_set_refuses_an_argument_without_a_value(self):
        result = self.run_manager("settings", "set", "traditionalize")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())

    def test_set_without_an_assignment_is_refused(self):
        result = self.run_manager("settings", "set")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())

    def test_set_reports_a_widget_that_is_not_on_the_bar(self):
        self.write_shell_json({"bar": {"layout": {"right": [{"id": "omarchy.battery"}]}}})
        result = self.run_manager("settings", "set", "traditionalize=false")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip())

    def test_a_write_leaves_the_other_widget_alone(self):
        self.assertEqual(self.run_manager("settings", "set", "traditionalize=false").returncode, 0)
        document = json.loads(self.shell_json.read_text(encoding="utf-8"))
        self.assertEqual(document["bar"]["layout"]["right"][1], {"id": "omarchy.battery", "warnAt": 15})


class ManagerRedeployTests(unittest.TestCase):
    """A deploy that does not rebuild has not deployed.

    Writing the files is invisible to Rime until it recompiles them, and a
    recompile that fails leaves the user with no input method. So `deploy` has
    to trigger the rebuild, and it has to say so when the rebuild failed - the
    one failure that must never be quiet.
    """

    def setUp(self):
        # `cmd_deploy` is run for real here with only its heavy steps mocked,
        # and it ends with `hooks.sync`, which writes the install manifest under
        # XDG_STATE_HOME. Every home is redirected so that nothing here reads
        # or rewrites the machine's own.
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        patcher = mock.patch.dict("os.environ", {
            variable: str(root / name) for variable, name in (
                ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
                ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
            )
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _deploy(self, redeploy_result):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/cache/cedict.txt")), \
             mock.patch.object(manager.packages, "check",
                               return_value={"missing": [], "error": ""}), \
             mock.patch.object(manager.deploy_lib, "deploy", return_value={}), \
             mock.patch.object(manager.deploy_lib, "redeploy",
                               return_value=redeploy_result) as redeploy, \
             mock.patch.object(manager.settings, "load",
                               return_value=manager.settings.resolve({})), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manager.cmd_deploy(None)
        return code, out.getvalue(), err.getvalue(), redeploy

    def test_a_deploy_triggers_the_rebuild(self):
        _, _, _, redeploy = self._deploy(REBUILT)
        self.assertEqual(redeploy.call_count, 1)

    def test_a_rebuilt_schema_is_a_successful_deploy(self):
        code, out, err, _ = self._deploy(REBUILT)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("rime", out.lower())

    def test_a_failed_rebuild_exits_non_zero(self):
        code, _, _, _ = self._deploy(NOT_REBUILT)
        self.assertNotEqual(code, 0)

    def test_a_failed_rebuild_names_the_lines_rime_wrote(self):
        _, _, err, _ = self._deploy(NOT_REBUILT)
        self.assertIn("error building config: rime_ice.schema", err)
        self.assertIn("error applying patch to engine/filters/+", err)

    def test_a_failed_rebuild_says_so_on_stderr_not_stdout(self):
        _, out, err, _ = self._deploy(NOT_REBUILT)
        self.assertTrue(err.strip())
        self.assertNotIn("error building config", out)

    def test_a_rebuild_that_could_not_be_triggered_also_fails(self):
        code, _, err, _ = self._deploy({
            "triggered": False, "succeeded": False, "verified": False,
            "errors": ["Error: org.fcitx.Fcitx5 refused the call"],
            "detail": "the redeploy could not be triggered",
        })
        self.assertNotEqual(code, 0)
        self.assertIn("refused the call", err)

    def test_fcitx5_not_running_is_not_a_failure(self):
        # The natural sequence on a fresh machine: deploy, then start fcitx5.
        # This printed "the configuration is on disk but is NOT in effect; input
        # may be broken until this is fixed" and exited 1 - which is how a user
        # is taught to ignore the one message that matters.
        code, out, err, _ = self._deploy(NOT_RUNNING)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("when it starts", out)

    def test_fcitx5_not_running_does_not_claim_a_rebuild(self):
        _, out, _, _ = self._deploy(NOT_RUNNING)
        self.assertNotIn("rime: rebuilt", out)

    def test_a_deploy_with_annotation_off_says_so_and_how_to_turn_it_on(self):
        # The default. A user wondering why there are no glosses should get the
        # answer from the deploy that produced the configuration.
        _, out, err, _ = self._deploy(REBUILT)
        self.assertIn("annotation: off", out)
        self.assertIn("annotation=true", out)
        self.assertEqual(err, "")

    def test_a_deploy_with_annotation_on_does_not_say_it_is_off(self):
        with mock.patch.object(manager.settings, "load",
                               return_value=manager.settings.resolve({"annotation": True})), \
             mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/cache/cedict.txt")), \
             mock.patch.object(manager.packages, "check",
                               return_value={"missing": [], "error": ""}), \
             mock.patch.object(manager.deploy_lib, "deploy", return_value={}), \
             mock.patch.object(manager.deploy_lib, "redeploy", return_value=REBUILT):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                manager.cmd_deploy(None)
        self.assertNotIn("annotation: off", out.getvalue())

    def test_a_verdict_with_no_evidence_says_so_rather_than_rebuilt(self):
        code, out, err, _ = self._deploy(UNVERIFIED)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertNotIn("rime: rebuilt", out)
        self.assertIn("could not read the journal", out)

    def test_the_files_are_still_written_before_the_rebuild_is_judged(self):
        # A failed rebuild is reported, not rolled back: the user needs the
        # generated files on disk to see what Rime objected to.
        with mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/cache/cedict.txt")), \
             mock.patch.object(manager.packages, "check",
                               return_value={"missing": [], "error": ""}), \
             mock.patch.object(manager.deploy_lib, "deploy",
                               return_value={"rime": ["rime_ice.custom.yaml"]}) as written, \
             mock.patch.object(manager.deploy_lib, "redeploy", return_value=NOT_REBUILT), \
             mock.patch.object(manager.settings, "load",
                               return_value=manager.settings.resolve({})), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            manager.cmd_deploy(None)
        self.assertEqual(written.call_count, 1)


class ManagerValueParsingTests(unittest.TestCase):
    """The type guessing on its own, without spawning a process per case."""

    def test_booleans_integers_and_strings(self):
        self.assertIs(manager.parse_value("true"), True)
        self.assertIs(manager.parse_value("false"), False)
        self.assertEqual(manager.parse_value("10"), 10)
        self.assertIs(type(manager.parse_value("10")), int)
        self.assertEqual(manager.parse_value("Control+e"), "Control+e")
        self.assertEqual(manager.parse_value("True"), True)
        self.assertEqual(manager.parse_value(""), "")

    def test_an_assignment_splits_on_the_first_equals_sign(self):
        self.assertEqual(manager.parse_assignment("annotateHotkey=Control+e"), ("annotateHotkey", "Control+e"))
        self.assertEqual(manager.parse_assignment("annotateHotkey=a=b"), ("annotateHotkey", "a=b"))

    def test_an_assignment_without_an_equals_sign_is_refused(self):
        with self.assertRaises(ValueError):
            manager.parse_assignment("traditionalize")


if __name__ == "__main__":
    unittest.main()
