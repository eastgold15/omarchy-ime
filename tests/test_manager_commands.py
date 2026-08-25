"""The subcommands that switch Rime schemas and follow the Omarchy theme.

No test here reaches D-Bus, runs gdbus, or writes anywhere near ~/.config: the
runner, the theme renderer, the reloader, and every directory are injected or
redirected at XDG_* into a temporary tree.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lib import deploy as deploy_lib
from lib import hooks, hyprland

ROOT = Path(__file__).resolve().parent.parent

# ime-manager is an extensionless script rather than a module on the path, so it
# is loaded by location with an explicit loader.
_spec = importlib.util.spec_from_file_location(
    "ime_manager", ROOT / "ime-manager",
    loader=importlib.machinery.SourceFileLoader("ime_manager", str(ROOT / "ime-manager")),
)
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)

DESCRIBED = [
    {"id": "japanese_tw_eng", "name": "Mixed ZH-JA-EN", "current": False},
    {"id": "rime_ice", "name": "Rime Ice", "current": True},
    {"id": "sno_japanese", "name": "Japanese", "current": False},
]


def run(*argv):
    """Run the manager in-process and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = manager.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SchemaCommandTests(unittest.TestCase):
    """`ime-manager schema` is the panel's only way to change schema."""

    def test_list_json_carries_the_id_the_name_and_the_current_mark(self):
        with mock.patch.object(manager.imestate, "describe_schemas",
                               return_value=DESCRIBED):
            code, out, _ = run("schema", "list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), DESCRIBED)

    def test_list_looks_for_names_in_the_rime_build_directory(self):
        with mock.patch.object(manager.imestate, "describe_schemas",
                               return_value=DESCRIBED) as describe:
            run("schema", "list", "--json")
        self.assertEqual(describe.call_args.kwargs["build_dir"],
                         manager.rime_dir() / "build")

    def test_list_marks_the_current_schema_in_plain_output(self):
        with mock.patch.object(manager.imestate, "describe_schemas",
                               return_value=DESCRIBED):
            _, out, _ = run("schema", "list")
        current = [line for line in out.splitlines() if line.startswith("*")]
        self.assertEqual(len(current), 1)
        self.assertIn("rime_ice", current[0])

    def test_list_shows_the_readable_name_beside_the_id(self):
        with mock.patch.object(manager.imestate, "describe_schemas",
                               return_value=DESCRIBED):
            _, out, _ = run("schema", "list")
        self.assertIn("Mixed ZH-JA-EN", out)

    def test_an_unreachable_rime_lists_nothing_without_failing(self):
        """The panel polls this; a closed Fcitx5 is a normal state, not an error."""
        with mock.patch.object(manager.imestate, "describe_schemas", return_value=[]):
            code, out, _ = run("schema", "list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_get_prints_the_current_schema(self):
        with mock.patch.object(manager.imestate, "current_schema",
                               return_value="rime_ice"):
            code, out, _ = run("schema", "get")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "rime_ice")

    def test_get_fails_when_there_is_no_current_schema(self):
        with mock.patch.object(manager.imestate, "current_schema", return_value=None):
            code, _, _ = run("schema", "get")
        self.assertEqual(code, 1)

    def test_set_switches_to_the_named_schema(self):
        with mock.patch.object(manager.imestate, "set_schema",
                               return_value=True) as switch:
            code, _, _ = run("schema", "set", "sno_japanese")
        self.assertEqual(code, 0)
        self.assertEqual(switch.call_args.args[0], "sno_japanese")

    def test_a_refused_switch_is_reported_on_stderr(self):
        with mock.patch.object(manager.imestate, "set_schema", return_value=False):
            code, _, err = run("schema", "set", "sno_japanese")
        self.assertEqual(code, 1)
        self.assertIn("sno_japanese", err)


class ApplyThemeTests(unittest.TestCase):
    """`theme apply` is what the Omarchy hook calls, so it does the least it can."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # Laid out so that the XDG redirection below makes manager.themes_dir()
        # and manager.state_dir() resolve to exactly these two directories.
        self.themes = root / "data" / "fcitx5" / "themes"
        self.state = root / "omarchy-ime"
        self.state.mkdir(parents=True)
        patcher = mock.patch.dict("os.environ", {
            "XDG_STATE_HOME": str(root),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reloads = []
        self.deployed()

    def test_the_redirection_these_tests_rely_on_holds(self):
        self.assertEqual(manager.themes_dir(), self.themes)
        self.assertEqual(manager.state_dir(), self.state)

    def deployed(self, files=None):
        (self.state / deploy_lib.MANIFEST_NAME).write_text(
            json.dumps({"files": files if files is not None
                        else {"themes": ["omarchy/theme.conf"]}}, indent=2) + "\n",
            encoding="utf-8",
        )

    def reloader(self, answer=True):
        def reload():
            self.reloads.append(True)
            return answer
        return reload

    def apply(self, rendered, reloader=None):
        return manager.apply_theme(
            self.themes, self.state, lambda: rendered,
            reloader or self.reloader(),
        )

    def test_it_writes_the_rendered_theme(self):
        result = self.apply({"omarchy/theme.conf": "[Metadata]\n"})
        self.assertEqual(result["written"], ["omarchy/theme.conf"])
        self.assertEqual(
            (self.themes / "omarchy" / "theme.conf").read_text(encoding="utf-8"),
            "[Metadata]\n",
        )

    def test_it_asks_fcitx_to_pick_the_theme_up(self):
        self.apply({"omarchy/theme.conf": "[Metadata]\n"})
        self.assertEqual(len(self.reloads), 1)

    def test_an_unchanged_theme_is_neither_rewritten_nor_reloaded(self):
        self.apply({"omarchy/theme.conf": "[Metadata]\n"})
        self.reloads.clear()
        result = self.apply({"omarchy/theme.conf": "[Metadata]\n"})
        self.assertEqual(result["written"], [])
        self.assertEqual(self.reloads, [])

    def test_it_does_nothing_before_the_plugin_has_ever_been_deployed(self):
        """Files written outside a deploy are files no uninstall would remove."""
        (self.state / deploy_lib.MANIFEST_NAME).unlink()
        result = self.apply({"omarchy/theme.conf": "[Metadata]\n"})
        self.assertEqual(result["written"], [])
        self.assertEqual(self.reloads, [])
        self.assertFalse(self.themes.exists())

    def test_a_refused_reload_is_reported(self):
        result = self.apply({"omarchy/theme.conf": "[Metadata]\n"},
                            self.reloader(answer=False))
        self.assertFalse(result["reloaded"])

    def test_the_command_never_runs_a_deploy(self):
        """It is on the theme-switch path, so it must not download or rebuild."""
        rendered = {"omarchy-ime/theme.conf": "[Metadata]\n"}
        with mock.patch.object(manager, "theme_renderer",
                               return_value=lambda: rendered), \
             mock.patch.object(manager.imestate, "reload_addon", return_value=True), \
             mock.patch.object(manager.deploy_lib, "deploy") as deploy, \
             mock.patch.object(manager.deploy_lib, "redeploy") as redeploy, \
             mock.patch.object(manager.assets, "ensure_dictionary") as fetch:
            code, _, _ = run("theme", "apply")
        self.assertEqual(code, 0)
        deploy.assert_not_called()
        redeploy.assert_not_called()
        fetch.assert_not_called()
        self.assertTrue((self.themes / "omarchy-ime" / "theme.conf").is_file())

    def test_a_missing_theme_generator_is_reported_rather_than_traced(self):
        with mock.patch.object(manager, "theme_renderer",
                               side_effect=ImportError("no module named theme")):
            code, _, err = run("theme", "apply")
        self.assertEqual(code, 1)
        self.assertIn("theme", err)

    def test_the_reload_targets_the_addon_that_draws_the_candidate_window(self):
        """classicui is what paints the candidate window and loads the theme."""
        rendered = {"omarchy-ime/theme.conf": "[Metadata]\n"}
        with mock.patch.object(manager, "theme_renderer",
                               return_value=lambda: rendered), \
             mock.patch.object(manager.imestate, "reload_addon",
                               return_value=True) as reload:
            run("theme", "apply")
        reload.assert_called_once()
        self.assertEqual(reload.call_args.args[0], "classicui")


class ManagerHookCommandTests(unittest.TestCase):
    """`hook install` and `hook remove` run against a redirected XDG tree."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.config = root / "config"
        self.state_home = root / "state"
        self.config.mkdir()
        self.state_home.mkdir()
        patcher = mock.patch.dict("os.environ", {
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(self.state_home),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        manager.state_dir().mkdir(parents=True, exist_ok=True)

    def hook_path(self) -> Path:
        return (self.config / "omarchy" / "hooks" / "theme-set.d" / hooks.HOOK_NAME)

    def manifest(self) -> dict:
        path = manager.state_dir() / deploy_lib.MANIFEST_NAME
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))["files"]

    def test_install_writes_the_hook_into_the_redirected_config(self):
        code, out, _ = run("hook", "install")
        self.assertEqual(code, 0)
        self.assertTrue(self.hook_path().is_file())
        self.assertIn(str(self.hook_path()), out)

    def test_the_installed_hook_points_at_this_checkout(self):
        run("hook", "install")
        self.assertIn(str(ROOT / "ime-manager"),
                      self.hook_path().read_text(encoding="utf-8"))

    def test_install_records_the_hook_so_uninstall_will_remove_it(self):
        run("hook", "install")
        self.assertEqual(sorted(self.manifest()[hooks.TARGET]), [hooks.HOOK_NAME])

    def test_remove_deletes_it_again(self):
        run("hook", "install")
        code, _, _ = run("hook", "remove")
        self.assertEqual(code, 0)
        self.assertFalse(self.hook_path().exists())
        self.assertNotIn(hooks.TARGET, self.manifest())

    def test_status_says_whether_the_hook_is_there(self):
        _, before, _ = run("hook", "status")
        run("hook", "install")
        _, after, _ = run("hook", "status")
        self.assertNotEqual(before.strip(), after.strip())

    def test_uninstall_takes_the_hook_with_everything_else(self):
        run("hook", "install")
        code, _, _ = run("uninstall")
        self.assertEqual(code, 0)
        self.assertFalse(self.hook_path().exists())

    def test_a_deploy_does_not_leave_the_hook_out_of_the_manifest(self):
        """deploy rewrites the manifest from what it wrote, and the hook is not
        one of the things it writes."""
        run("hook", "install")
        with mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/nowhere/cedict.txt")), \
             mock.patch.object(manager.deploy_lib, "deploy", return_value={}), \
             mock.patch.object(manager.packages, "check",
                               return_value={"missing": [], "error": ""}), \
             mock.patch.object(manager.deploy_lib, "redeploy",
                               return_value={"succeeded": True, "verified": True,
                                             "errors": [], "detail": "",
                                             "triggered": True}), \
             mock.patch.object(manager.settings, "load", return_value={}):
            run("deploy")
        self.assertEqual(sorted(self.manifest()[hooks.TARGET]), [hooks.HOOK_NAME])

    def test_a_deploy_is_never_handed_the_hook_directory(self):
        """Handed a directory it never writes into, deploy would delete the
        hook as a stale file."""
        self.assertNotIn(hooks.TARGET, manager.deploy_targets())
        self.assertIn(hooks.TARGET, manager.targets())


class EnvironmentTargetTests(unittest.TestCase):
    """Where the environment override is installed and removed from.

    The file only overrides the packaged one by living in the directory systemd
    reads later under the packaged file's own name, so both halves of that -
    the directory and the name - have to be the ones systemd uses, not ones
    that merely look right.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.config = Path(self._tmp.name)
        patcher = mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.config)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_the_target_is_the_directory_systemd_reads_after_usr_lib(self):
        self.assertEqual(manager.targets()[deploy_lib.ENVIRONMENT_TARGET],
                         self.config / "environment.d")

    def test_a_deploy_writes_into_it(self):
        self.assertIn(deploy_lib.ENVIRONMENT_TARGET, manager.deploy_targets())

    def test_an_uninstall_walks_it(self):
        # Restoring the packaged behaviour means removing our file, so the
        # target an uninstall walks has to include this one.
        self.assertIn(deploy_lib.ENVIRONMENT_TARGET, manager.targets())

    def test_an_uninstall_that_removed_it_says_when_that_takes_effect(self):
        # Removing the file restores the packaged behaviour, and that is
        # another thing systemd only reads at session start. Saying nothing
        # would leave the user testing a change that cannot have happened yet.
        with mock.patch.object(
            manager.deploy_lib, "uninstall",
            return_value={
                "removed": {deploy_lib.ENVIRONMENT_TARGET: ["10-omarchy-fcitx.conf"]},
                "restored": {},
            },
        ), mock.patch.object(manager, "drop_cache", return_value=0):
            code, out, _ = run("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("next login", out)

    def test_an_uninstall_that_removed_nothing_of_the_sort_stays_quiet(self):
        with mock.patch.object(
            manager.deploy_lib, "uninstall",
            return_value={"removed": {"rime": ["default.custom.yaml"]},
                          "restored": {}},
        ), mock.patch.object(manager, "drop_cache", return_value=0):
            _, out, _ = run("uninstall")
        self.assertNotIn("next login", out)

    def test_a_file_this_plugin_only_wrote_over_is_reported_as_restored(self):
        # Omarchy ships some of the files in ~/.config/fcitx5. Deleting them
        # because the manifest listed them was not an uninstall.
        with mock.patch.object(
            manager.deploy_lib, "uninstall",
            return_value={"removed": {},
                          "restored": {"fcitx5": ["conf/xcb.conf"]}},
        ), mock.patch.object(manager, "drop_cache", return_value=0):
            code, out, _ = run("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("restored fcitx5/conf/xcb.conf", out)


class HyprlandStatusTests(unittest.TestCase):
    """The two Hyprland pieces ride the ordinary status report.

    They are reported and never written: `~/.config/hypr` belongs to the user,
    and the only drop-in Omarchy offers would mean this plugin generating
    executable Lua into a compositor configuration. So the plugin's whole
    contribution is to say whether each one is in effect - and to say "cannot
    tell" rather than "no" when there is no compositor to ask.
    """

    def status(self, blur, toggle) -> dict:
        with mock.patch.object(manager.packages, "missing", return_value=[]), \
             mock.patch.object(manager.imestate, "read",
                               return_value={"state": "closed"}), \
             mock.patch.object(manager.hyprland, "report",
                               return_value={"blur": blur, "toggle": toggle}):
            return manager.status()

    def test_the_report_carries_both_pieces(self):
        result = self.status(hyprland.ON, hyprland.OFF)
        self.assertEqual(result["hypr"], {"blur": "on", "toggle": "off"})

    def test_a_machine_without_hyprland_reports_unknown_and_still_succeeds(self):
        result = self.status(hyprland.UNKNOWN, hyprland.UNKNOWN)
        self.assertEqual(result["hypr"], {"blur": "unknown", "toggle": "unknown"})

    def test_the_command_exits_zero_whatever_the_two_say(self):
        # Neither is a failure. An absent enhancement is an absent enhancement.
        for blur in (hyprland.ON, hyprland.OFF, hyprland.UNKNOWN):
            with mock.patch.object(manager, "status",
                                   return_value={"hypr": {"blur": blur,
                                                          "toggle": blur},
                                                 "packages_missing": [],
                                                 "dictionary": True,
                                                 "dictionary_path": "/x",
                                                 "deployed": True,
                                                 "hook": True,
                                                 "input": {"state": "closed"}}):
                code, _, _ = run("status")
            self.assertEqual(code, 0, blur)

    def test_the_json_report_carries_them_for_the_panel(self):
        with mock.patch.object(manager.packages, "missing", return_value=[]), \
             mock.patch.object(manager.imestate, "read",
                               return_value={"state": "closed"}), \
             mock.patch.object(manager.hyprland, "report",
                               return_value={"blur": "on", "toggle": "unknown"}):
            code, out, _ = run("status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hypr"], {"blur": "on", "toggle": "unknown"})

    def test_plain_output_names_each_piece_and_its_state(self):
        with mock.patch.object(manager.packages, "missing", return_value=[]), \
             mock.patch.object(manager.imestate, "read",
                               return_value={"state": "closed"}), \
             mock.patch.object(manager.hyprland, "report",
                               return_value={"blur": "off", "toggle": "unknown"}):
            _, out, _ = run("status")
        self.assertIn("blur", out)
        self.assertIn("off", out)
        self.assertIn("toggle", out)
        self.assertIn("unknown", out)


class SchemaAvailableTests(unittest.TestCase):
    """`ime-manager schema available` is what fills the settings picker.

    It is the counterpart of `schema list`: `list` reports what Rime actually
    has built and is composing with, while `available` reports what the settings
    could ask for. The panel needs both, and they answer different questions.
    """

    def options(self):
        code, out, _ = run("schema", "available", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_every_catalogue_schema_is_offered_in_catalogue_order(self):
        self.assertEqual(
            [option["value"] for option in self.options()],
            list(manager.settings.SCHEMA_IDS),
        )

    def test_each_option_carries_its_family(self):
        for option in self.options():
            self.assertEqual(
                option["description"], manager.settings.SCHEMA_FAMILY[option["value"]]
            )

    def test_the_label_is_the_name_read_from_the_schema_yaml(self):
        with mock.patch.object(manager, "schema_display_name",
                               side_effect=lambda schema_id: "name of " + schema_id):
            options = self.options()
        self.assertEqual(options[0]["label"], "name of " + options[0]["value"])

    def test_a_schema_with_no_readable_name_is_offered_under_its_id(self):
        with mock.patch.object(manager, "schema_display_name", return_value=None):
            for option in self.options():
                self.assertEqual(option["label"], option["value"])

    def test_the_name_is_looked_for_where_schemas_actually_live(self):
        directories = manager.schema_source_dirs()
        self.assertIn(Path("/usr/share/rime-data"), directories)
        self.assertIn(ROOT / "payload" / "rime-japanese", directories)
        self.assertIn(manager.rime_dir(), directories)

    def test_the_plain_listing_names_one_schema_per_line(self):
        code, out, _ = run("schema", "available")
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), len(manager.settings.SCHEMA_IDS))
        self.assertTrue(lines[0].startswith("rime_ice"))


class SettingsSelectionTests(unittest.TestCase):
    """A list-valued setting on a command line the panel can produce.

    QML hands every value over as a string, so the selection arrives as one
    comma-separated argument and is split here rather than being guessed at by
    lib/settings, which is handed real lists by every other caller.
    """

    def test_a_comma_separated_selection_becomes_a_list(self):
        self.assertEqual(
            manager.parse_value("rime_ice,bopomofo", "schemas"),
            ["rime_ice", "bopomofo"],
        )

    def test_surrounding_space_is_not_part_of_a_schema_id(self):
        self.assertEqual(
            manager.parse_value("rime_ice, bopomofo ", "schemas"),
            ["rime_ice", "bopomofo"],
        )

    def test_an_empty_selection_stays_empty_so_the_write_is_refused(self):
        self.assertEqual(manager.parse_value("", "schemas"), [])

    def test_other_settings_are_still_read_the_way_they_were(self):
        self.assertIs(manager.parse_value("true", "traditionalize"), True)
        self.assertEqual(manager.parse_value("7", "pageSize"), 7)
        self.assertEqual(manager.parse_value("Control+e", "annotateHotkey"), "Control+e")

    def test_a_comma_in_a_string_setting_is_left_alone(self):
        self.assertEqual(manager.parse_value("a,b", "annotateHotkey"), "a,b")

    def test_writing_a_selection_lands_in_shell_json(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "shell.json"
            path.write_text(json.dumps({"bar": {"layout": {"right": [
                {"id": manager.settings.PLUGIN_ID},
            ]}}}), encoding="utf-8")
            with mock.patch.object(manager, "shell_json", return_value=path):
                code, out, _ = run("settings", "set", "--json",
                                   "schemas=rime_ice,double_pinyin_flypy")
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(out)["schemas"], ["rime_ice", "double_pinyin_flypy"]
            )

    def test_an_unknown_schema_is_refused_and_nothing_is_written(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "shell.json"
            document = {"bar": {"layout": {"right": [{"id": manager.settings.PLUGIN_ID}]}}}
            path.write_text(json.dumps(document), encoding="utf-8")
            with mock.patch.object(manager, "shell_json", return_value=path):
                code, _, err = run("settings", "set", "schemas=rime_ice,rime_icc")
            self.assertEqual(code, 1)
            self.assertIn("rime_icc", err)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)

    def test_a_selection_with_no_chinese_schema_is_refused(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "shell.json"
            path.write_text(json.dumps(
                {"bar": {"layout": {"right": [{"id": manager.settings.PLUGIN_ID}]}}}
            ), encoding="utf-8")
            with mock.patch.object(manager, "shell_json", return_value=path):
                code, _, err = run("settings", "set", "schemas=sno_japanese")
            self.assertEqual(code, 1)
            self.assertTrue(err.strip())


class BackgroundImageWiringTests(unittest.TestCase):
    """Both paths that render the candidate window carry the user's own image.

    lib/theme.py is pure about this: it is handed the settings rather than
    reading shell.json itself. So the wiring - deploy on one side, the
    theme-switch hook on the other - is what these check.
    """

    IMAGE = "/home/someone/Pictures/slate.png"

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.shell = self.root / "shell.json"
        self.shell.write_text(json.dumps({"bar": {"layout": {"right": [
            {"id": manager.settings.PLUGIN_ID, "backgroundImageDark": self.IMAGE},
        ]}}}), encoding="utf-8")
        patcher = mock.patch.object(manager, "shell_json", return_value=self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_theme_switch_hook_renders_with_the_stored_image(self):
        from lib import theme as theme_lib

        with mock.patch.object(theme_lib, "resolve",
                               return_value=theme_lib.resolved(
                                   theme_lib.FALLBACK_PALETTE, 0, "")) as resolve:
            manager.theme_renderer()()
        self.assertEqual(
            resolve.call_args.kwargs["settings"]["backgroundImageDark"], self.IMAGE
        )

    def test_a_deploy_renders_with_the_stored_image(self):
        from lib import theme as visible

        with mock.patch.object(manager.deploy_lib, "deploy",
                               return_value={}) as deploy, \
             mock.patch.object(manager.deploy_lib, "redeploy",
                               return_value={"succeeded": True, "verified": True,
                                             "errors": [], "detail": ""}), \
             mock.patch.object(manager.packages, "check",
                               return_value={"missing": [], "error": ""}), \
             mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/tmp/none")), \
             mock.patch.object(manager, "hooks"):
            self.assertEqual(run("deploy")[0], 0)
        source = deploy.call_args.kwargs["theme_source"]
        with mock.patch.object(visible, "resolve",
                               return_value=visible.resolved(
                                   visible.FALLBACK_PALETTE, 0, "")) as resolve:
            source()
        self.assertEqual(
            resolve.call_args.kwargs["settings"]["backgroundImageDark"], self.IMAGE
        )


class PackagesCommandTests(unittest.TestCase):
    """Where an install runs, and what is said when it cannot."""

    def test_a_package_check_that_could_not_run_is_not_a_clean_bill(self):
        with mock.patch.object(
                manager.packages, "check",
                return_value={"missing": [], "error": "omarchy: not found"}):
            code, _, err = run("packages", "list")
        self.assertEqual(code, 1)
        self.assertIn("omarchy: not found", err)

    def test_the_panel_path_hands_the_work_to_a_terminal(self):
        # A Quickshell Process has no tty, so sudo has nothing to prompt on.
        with mock.patch.object(
                manager.packages, "check",
                return_value={"missing": ["fcitx5"], "error": ""}), \
             mock.patch.object(manager.packages, "install",
                               return_value=(True, "a terminal is installing fcitx5")) as terminal, \
             mock.patch.object(manager.packages, "install_here") as here, \
             mock.patch.object(manager.sys.stdin, "isatty", return_value=False):
            code, out, _ = run("packages", "install")
        self.assertEqual(code, 0)
        self.assertEqual(terminal.call_count, 1)
        here.assert_not_called()
        self.assertIn("terminal", out)

    def test_the_command_line_path_installs_where_it_was_typed(self):
        with mock.patch.object(
                manager.packages, "check",
                return_value={"missing": ["fcitx5"], "error": ""}), \
             mock.patch.object(manager.packages, "install") as terminal, \
             mock.patch.object(manager.packages, "install_here",
                               return_value=(True, "packages installed")) as here, \
             mock.patch.object(manager.sys.stdin, "isatty", return_value=True):
            code, _, _ = run("packages", "install")
        self.assertEqual(code, 0)
        self.assertEqual(here.call_count, 1)
        terminal.assert_not_called()

    def test_a_failed_install_says_why_on_stderr(self):
        # It printed nothing at all, which left the panel showing a bare
        # "Package installation failed" with no package and no reason.
        with mock.patch.object(
                manager.packages, "check",
                return_value={"missing": ["fcitx5"], "error": ""}), \
             mock.patch.object(
                 manager.packages, "install",
                 return_value=(False, "could not open a terminal: no such file")), \
             mock.patch.object(manager.sys.stdin, "isatty", return_value=False):
            code, _, err = run("packages", "install")
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)


class DeployPreflightTests(unittest.TestCase):
    """Deploying without the packages produces a build that fails in silence."""

    def _deploy(self, missing):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(manager.packages, "check",
                               return_value={"missing": missing, "error": ""}), \
             mock.patch.object(manager.deploy_lib, "resolve_dictionary",
                               return_value=Path("/cache/cedict.txt")), \
             mock.patch.object(manager.deploy_lib, "deploy", return_value={}), \
             mock.patch.object(manager.deploy_lib, "redeploy",
                               return_value={"triggered": True, "succeeded": True,
                                             "verified": True, "errors": [],
                                             "detail": "Rime rebuilt the schemas"}), \
             mock.patch.object(manager.settings, "load", return_value={}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manager.cmd_deploy(None)
        return code, out.getvalue()

    def test_missing_packages_are_warned_about_before_the_work(self):
        code, out = self._deploy(["rime-ice-git"])
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)
        self.assertIn("rime-ice-git", out)

    def test_nothing_is_said_when_everything_is_installed(self):
        _, out = self._deploy([])
        self.assertNotIn("warning: packages", out)


if __name__ == "__main__":
    unittest.main()
