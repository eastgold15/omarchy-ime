import json
import subprocess
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import assets, deploy, generate, settings, theme
from tests.test_assets import cedict

# The selection that asks for the Japanese schemas, and so for their payload
# group and the two sources their dictionaries come from.
JAPANESE = ["rime_ice", "japanese_tw_eng", "sno_japanese"]

# Two palettes that share no colour, so a rewrite is visible rather than assumed.
RISTRETTO = {
    "accent": "#f38d70", "selection": "#403e41", "muted": "#72696a",
    "background": "#2c2525", "dark_background": "#211b1b",
    "lighter_background": "#3d2f2a", "foreground": "#e6d9db",
}
NORD = {
    "accent": "#88c0d0", "selection": "#434c5e", "muted": "#616e88",
    "background": "#2e3440", "dark_background": "#242933",
    "lighter_background": "#3b4252", "foreground": "#d8dee9",
}


class DeployCase(unittest.TestCase):
    """Shared fixture: a payload tree, empty targets, and fake fetched clones."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rime = root / "rime"
        self.fcitx5 = root / "fcitx5"
        self.themes = root / "themes"
        self.state = root / "state"
        self.payload = root / "payload"
        self.environment = root / "environment.d"
        self.targets = {
            "rime": self.rime, "fcitx5": self.fcitx5, "themes": self.themes,
            deploy.ENVIRONMENT_TARGET: self.environment,
        }

        # The packaged environment file the override is generated from. Pointed
        # at a temporary copy so no test reads /usr/lib, and so the file's
        # contents are the test's to decide.
        self.packaged = root / "packaged" / generate.ENVIRONMENT_PATH
        self.packaged.parent.mkdir(parents=True)
        self.packaged.write_text(
            "INPUT_METHOD=fcitx\nQT_IM_MODULE=fcitx\nXMODIFIERS=@im=fcitx\n",
            encoding="utf-8",
        )
        patcher = mock.patch.object(generate, "PACKAGED_ENVIRONMENT", self.packaged)
        patcher.start()
        self.addCleanup(patcher.stop)

        (self.payload / "rime" / "lua").mkdir(parents=True)
        (self.payload / "rime" / "lua" / "annotate.lua").write_text("-- filter")
        (self.payload / "rime-japanese").mkdir(parents=True)
        (self.payload / "rime-japanese" / "japanese.dict.yaml").write_text("jp")
        (self.payload / "fcitx5" / "conf").mkdir(parents=True)
        (self.payload / "fcitx5" / "config").write_text("[Hotkey]")

        # Stand-in for a fetched clone: the real directory layout, no network.
        self.clones = root / "clones"
        for name, files in assets.SOURCE_FILES.items():
            for inside in files:
                path = self.clones / name / inside
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{name}:{inside}")
        self.provider_calls = []
        self.provider = self._fake_provider
        # No test in this suite reads ~/.local/state/omarchy or runs hyprctl:
        # the resolved theme is always handed in.
        self.theme = lambda: theme.resolved(RISTRETTO, 17, "ristretto")
        self.notices = []
        self.addCleanup(self._tmp.cleanup)

    def _fake_provider(self, name):
        self.provider_calls.append(name)
        return self.clones / name

    def _deploy(self, **overrides):
        # The fixture deploys with annotation on unless a test says otherwise:
        # most of what is asserted here is about the files the feature writes,
        # and the switch defaults to off. The default itself is asserted by
        # AnnotationSwitchDeployTests, which calls deploy.deploy directly.
        overrides.setdefault("annotation", True)
        return deploy.deploy(
            settings.resolve(overrides), self.targets, self.state, self.payload,
            asset_provider=self.provider, theme_source=self.theme,
            notice=self.notices.append,
        )

    def _theme_file(self, name):
        return self.themes / theme.DIRECTORY / name

    def _manifest(self):
        return json.loads((self.state / "installed.json").read_text())["files"]


class DeployTests(DeployCase):
    def test_generated_files_land_in_the_rime_directory(self):
        self._deploy()
        self.assertTrue((self.rime / "default.custom.yaml").is_file())

    def test_always_installed_payload_is_copied(self):
        self._deploy()
        self.assertTrue((self.rime / "lua" / "annotate.lua").is_file())

    def test_the_generated_classicui_config_reaches_the_fcitx5_target(self):
        # It used to ship as a payload file with the candidate list's direction
        # pinned, which left `verticalCandidates` wired to a key nothing read.
        self._deploy()
        written = (self.fcitx5 / "conf" / "classicui.conf").read_text()
        self.assertIn("Vertical Candidate List=True", written)
        self.assertIn("conf/classicui.conf", self._manifest()["fcitx5"])

    def test_turning_the_setting_off_changes_the_key_that_acts(self):
        self._deploy(verticalCandidates=False)
        written = (self.fcitx5 / "conf" / "classicui.conf").read_text()
        self.assertIn("Vertical Candidate List=False", written)

    def test_it_points_fcitx5_at_the_generated_theme_directory(self):
        self._deploy()
        written = (self.fcitx5 / "conf" / "classicui.conf").read_text()
        self.assertIn(f"Theme={theme.DIRECTORY}", written)

    def test_fcitx5_payload_reaches_its_own_target(self):
        self._deploy()
        self.assertTrue((self.fcitx5 / "config").is_file())

    def test_japanese_payload_is_withheld_until_a_japanese_schema_is_selected(self):
        self._deploy()
        self.assertFalse((self.rime / "japanese.dict.yaml").exists())
        self._deploy(schemas=JAPANESE)
        self.assertTrue((self.rime / "japanese.dict.yaml").is_file())

    def test_deselecting_the_japanese_schemas_removes_their_files_again(self):
        self._deploy(schemas=JAPANESE)
        self._deploy()
        self.assertFalse((self.rime / "japanese.dict.yaml").exists())

    def test_written_paths_are_recorded_per_target(self):
        self._deploy()
        self.assertIn("default.custom.yaml", self._manifest()["rime"])
        self.assertIn("config", self._manifest()["fcitx5"])

    def test_deploying_twice_leaves_the_same_content(self):
        self._deploy()
        first = (self.rime / "default.custom.yaml").read_text()
        self._deploy()
        self.assertEqual(first, (self.rime / "default.custom.yaml").read_text())

    def test_a_pre_existing_file_is_backed_up_before_being_replaced(self):
        self.rime.mkdir(parents=True)
        (self.rime / "default.custom.yaml").write_text("mine")
        self._deploy()
        backups = list((self.state / "backups").rglob("default.custom.yaml"))
        self.assertEqual(backups[0].read_text(), "mine")

    def test_uninstall_takes_the_generated_theme_with_it(self):
        self._deploy()
        self.assertTrue(self._theme_file("theme.conf").is_file())
        deploy.uninstall(self.state, self.targets)
        for name in ("theme.conf", "panel.svg", "highlight.svg", "radio.svg", "arrow.svg"):
            self.assertFalse(self._theme_file(name).exists(), name)

    def test_uninstall_removes_only_what_was_installed(self):
        self._deploy()
        keeper = self.rime / "user.yaml"
        keeper.write_text("keep")
        deploy.uninstall(self.state, self.targets)
        self.assertTrue(keeper.exists())
        self.assertFalse((self.rime / "default.custom.yaml").exists())
        self.assertFalse((self.fcitx5 / "config").exists())

    def test_uninstall_takes_the_manifest_with_it(self):
        # Removed rather than blanked. The manifest is what everything else asks
        # "has this ever been deployed?", and a file saying "nothing" still
        # answers yes - which hid the panel's first-run notice on exactly the
        # machine that needed it.
        self._deploy()
        deploy.uninstall(self.state, self.targets)
        self.assertFalse((self.state / deploy.MANIFEST_NAME).exists())
        self.assertEqual(deploy.read_manifest(self.state), {})

    def test_uninstall_restores_a_file_this_plugin_only_wrote_over(self):
        # Omarchy ships some of the files in ~/.config/fcitx5, and deleting them
        # because the manifest listed them was not an uninstall.
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / "config").write_text("theirs")
        self._deploy()
        self.assertNotEqual((self.fcitx5 / "config").read_text(), "theirs")
        result = deploy.uninstall(self.state, self.targets)
        self.assertEqual((self.fcitx5 / "config").read_text(), "theirs")
        self.assertIn("config", result["restored"]["fcitx5"])
        self.assertNotIn("config", result["removed"].get("fcitx5", []))

    def test_a_replaced_file_is_still_restored_after_several_deploys(self):
        # The backup is taken once, the first time we write over it; the record
        # has to survive every deploy after that or the restore has nothing to
        # copy from.
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / "config").write_text("theirs")
        self._deploy()
        self._deploy()
        self._deploy()
        deploy.uninstall(self.state, self.targets)
        self.assertEqual((self.fcitx5 / "config").read_text(), "theirs")

    def test_a_file_this_plugin_created_is_deleted(self):
        self._deploy()
        result = deploy.uninstall(self.state, self.targets)
        self.assertFalse((self.rime / "default.custom.yaml").exists())
        self.assertIn("default.custom.yaml", result["removed"]["rime"])

    def test_the_manifest_says_which_files_were_written_over(self):
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / "config").write_text("theirs")
        self._deploy()
        recorded = self._manifest()["fcitx5"]
        self.assertTrue(recorded["config"]["replaced"])
        self.assertFalse(recorded["conf/classicui.conf"]["replaced"])

    def test_replacing_a_file_does_not_widen_its_permissions(self):
        # shutil.copy2 carries the *source's* mode, which is 0644 out of a git
        # checkout - and fcitx5 keeps profile and its conf files at 0600.
        import os
        import stat

        self.fcitx5.mkdir(parents=True)
        private = self.fcitx5 / "config"
        private.write_text("theirs")
        os.chmod(private, 0o600)
        self._deploy()
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)

    def test_the_directories_a_removal_empties_go_too(self):
        # An empty ~/.local/share/fcitx5/themes/omarchy-ime is worse than
        # litter: fcitx5-configtool lists it as a theme.
        self._deploy()
        deploy.uninstall(self.state, self.targets)
        self.assertFalse((self.themes / theme.DIRECTORY).exists())
        self.assertFalse((self.rime / "lua").exists())

    def test_a_pre_existing_file_that_is_not_utf8_does_not_stop_the_deploy(self):
        # This plugin is built to take over a hand-made Rime setup, where a GBK
        # default.custom.yaml is not exotic. The UnicodeDecodeError used to
        # escape after some files were written and before the sweep, the
        # manifest and the rebuild - the worst possible stopping point.
        self.rime.mkdir(parents=True)
        (self.rime / "default.custom.yaml").write_bytes(
            "patch:\n  # \u4e2d\u6587\n".encode("gbk")
        )
        installed = self._deploy()
        self.assertIn("default.custom.yaml", installed["rime"])
        self.assertIn("schema_list",
                      (self.rime / "default.custom.yaml").read_text(encoding="utf-8"))
        self.assertTrue((self.state / "installed.json").is_file())

    def test_the_file_that_was_not_utf8_is_backed_up_intact(self):
        self.rime.mkdir(parents=True)
        original = "patch:\n  # \u4e2d\u6587\n".encode("gbk")
        (self.rime / "default.custom.yaml").write_bytes(original)
        self._deploy()
        backups = list((self.state / "backups").rglob("default.custom.yaml"))
        self.assertEqual(backups[0].read_bytes(), original)


class PerSchemaFileDeployTests(DeployCase):
    """The per-schema files ride the manifest like everything else.

    Adding a file per selected schema means deselecting a schema has to remove
    its file, or Rime keeps loading a patch for a schema the user dropped. That
    path already exists for the Japanese payload; these check the generated
    files use it too.
    """

    def test_each_selected_schema_gets_its_file_on_disk(self):
        self._deploy(schemas=["rime_ice", "double_pinyin_flypy"])
        self.assertTrue((self.rime / "rime_ice.custom.yaml").is_file())
        self.assertTrue((self.rime / "double_pinyin_flypy.custom.yaml").is_file())

    def test_the_file_carries_the_annotation_block(self):
        self._deploy(schemas=["double_pinyin_flypy"])
        content = (self.rime / "double_pinyin_flypy.custom.yaml").read_text()
        self.assertIn("lua_filter@*annotate", content)
        self.assertIn("toggle: annotate", content)
        self.assertIn("annotate/dictionary", content)

    def test_an_unselected_schema_never_gets_a_file(self):
        self._deploy(schemas=["double_pinyin_flypy"])
        self.assertFalse((self.rime / "rime_ice.custom.yaml").exists())

    def test_deselecting_a_schema_removes_its_custom_file(self):
        self._deploy(schemas=["rime_ice", "double_pinyin_flypy"])
        self.assertTrue((self.rime / "double_pinyin_flypy.custom.yaml").is_file())
        self._deploy(schemas=["rime_ice"])
        self.assertFalse((self.rime / "double_pinyin_flypy.custom.yaml").exists())
        self.assertTrue((self.rime / "rime_ice.custom.yaml").is_file())

    def test_swapping_the_selection_swaps_the_files(self):
        self._deploy(schemas=["double_pinyin_flypy"])
        self._deploy(schemas=["bopomofo"])
        self.assertFalse((self.rime / "double_pinyin_flypy.custom.yaml").exists())
        self.assertTrue((self.rime / "bopomofo.custom.yaml").is_file())

    def test_every_per_schema_file_is_recorded_in_the_manifest(self):
        self._deploy(schemas=["rime_ice", "cangjie5"])
        recorded = self._manifest()["rime"]
        self.assertIn("rime_ice.custom.yaml", recorded)
        self.assertIn("cangjie5.custom.yaml", recorded)

    def test_the_manifest_drops_a_file_it_no_longer_writes(self):
        self._deploy(schemas=["rime_ice", "cangjie5"])
        self._deploy(schemas=["rime_ice"])
        self.assertNotIn("cangjie5.custom.yaml", self._manifest()["rime"])

    def test_the_japanese_schemas_bring_no_custom_file_of_their_own(self):
        self._deploy(schemas=JAPANESE)
        self.assertFalse((self.rime / "japanese_tw_eng.custom.yaml").exists())
        self.assertFalse((self.rime / "sno_japanese.custom.yaml").exists())

    def test_uninstall_takes_every_per_schema_file_with_it(self):
        self._deploy(schemas=["rime_ice", "double_pinyin_flypy", "bopomofo"])
        deploy.uninstall(self.state, self.targets)
        for schema in ("rime_ice", "double_pinyin_flypy", "bopomofo"):
            self.assertFalse((self.rime / f"{schema}.custom.yaml").exists(), schema)

    def test_a_setting_that_cannot_reach_a_schema_is_announced(self):
        self._deploy(schemas=["rime_ice", "bopomofo"])
        self.assertTrue(
            any("bopomofo" in message for message in self.notices), self.notices
        )

    def test_nothing_is_announced_when_every_setting_lands(self):
        # Two notices are not about settings and are filtered out here: which
        # theme the candidate window followed, and that the environment
        # override waits for the next login. Both are said on every deploy.
        self._deploy(schemas=["rime_ice"])
        unrelated = ("theme", "environment")
        self.assertEqual(
            [m for m in self.notices if not m.startswith(unrelated)], []
        )


class AssetDeployTests(DeployCase):
    def test_sources_are_not_fetched_while_no_japanese_schema_is_selected(self):
        self._deploy()
        self.assertEqual(self.provider_calls, [])

    def test_selecting_the_japanese_schemas_resolves_every_source_they_need(self):
        self._deploy(schemas=JAPANESE)
        self.assertEqual(
            sorted(self.provider_calls), sorted(assets.SOURCE_FILES)
        )

    def test_extracted_files_land_at_their_mapped_paths(self):
        self._deploy(schemas=JAPANESE)
        for name, files in assets.SOURCE_FILES.items():
            for inside, relative in files.items():
                landed = self.rime / relative
                self.assertTrue(landed.is_file(), relative)
                self.assertEqual(landed.read_text(), f"{name}:{inside}")

    def test_the_snomiao_prefix_is_stripped_but_subdirectories_survive(self):
        self._deploy(schemas=JAPANESE)
        self.assertTrue((self.rime / "japanese_chars.dict.yaml").is_file())
        self.assertTrue((self.rime / "opencc" / "zhjp_daily.txt").is_file())
        self.assertFalse((self.rime / "Rime").exists())

    def test_extracted_files_are_recorded_in_the_manifest(self):
        self._deploy(schemas=JAPANESE)
        recorded = self._manifest()["rime"]
        self.assertIn("japanese.jmdict.dict.yaml", recorded)
        self.assertIn("opencc/zhjp_daily.txt", recorded)

    def test_a_failed_fetch_does_not_stop_the_rest_of_the_deploy(self):
        # The Chinese half of the deploy is untouched by a Japanese source that
        # could not be fetched: no network problem may cost anyone their input
        # method.
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        self.assertTrue((self.rime / "default.custom.yaml").is_file())
        self.assertTrue((self.rime / "lua" / "annotate.lua").is_file())
        self.assertFalse((self.rime / "japanese.jmdict.dict.yaml").exists())

    def test_a_failed_fetch_is_announced(self):
        # It used to `continue` in silence. A deploy that quietly did less than
        # it was asked to is one the user only finds out about by typing.
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        warnings = [m for m in self.notices if m.startswith(deploy.WARNING)]
        self.assertTrue(any("rime-japanese" in m for m in warnings), self.notices)

    def test_a_schema_whose_tables_are_missing_is_not_deployed(self):
        # This is the failure the old code produced and then declared a
        # success: the schema went into schema_list and its `japanese.dict.yaml`
        # went on disk naming three tables that were never fetched, so librime
        # answered with `source file '...' does not exist.` and the dictionary
        # never compiled. Withholding the schema is the honest answer, and it is
        # said out loud.
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        written = (self.rime / "default.custom.yaml").read_text()
        self.assertNotIn("japanese_tw_eng", written)
        self.assertNotIn("sno_japanese", written)
        self.assertIn("rime_ice", written)
        self.assertFalse((self.rime / "japanese.dict.yaml").exists())
        self.assertTrue(
            any("not deployed this time" in m for m in self.notices), self.notices
        )

    def test_a_schema_whose_tables_are_already_on_disk_survives_a_failed_fetch(self):
        # The tables from the last deploy that worked are still there, so
        # nothing is missing and nothing has to be withheld.
        self._deploy(schemas=JAPANESE)
        self.notices.clear()
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        self.assertIn("japanese_tw_eng", (self.rime / "default.custom.yaml").read_text())
        self.assertTrue((self.rime / "japanese.dict.yaml").is_file())

    def test_a_failed_fetch_does_not_delete_the_files_it_could_not_replace(self):
        # The bug this test exists for: those files were absent from `written`,
        # the previous manifest still listed them, and the stale-file sweep
        # unlinked them - taking a working install's dictionaries away because
        # the network was down, and exiting 0.
        self._deploy(schemas=JAPANESE)
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        self.assertTrue((self.rime / "japanese.jmdict.dict.yaml").is_file())
        self.assertTrue((self.rime / "opencc" / "zhjp_daily.txt").is_file())

    def test_files_a_failed_fetch_kept_stay_in_the_manifest(self):
        # Otherwise the deletion is not prevented, only postponed to the next
        # deploy.
        self._deploy(schemas=JAPANESE)
        self.provider = lambda name: None
        self._deploy(schemas=JAPANESE)
        self.assertIn("japanese.jmdict.dict.yaml", self._manifest()["rime"])

    def test_one_failed_source_does_not_withhold_the_other(self):
        self.provider = lambda name: None if name == "rime-japanese" else self.clones / name
        self._deploy(schemas=JAPANESE)
        self.assertFalse((self.rime / "japanese.jmdict.dict.yaml").exists())
        self.assertTrue((self.rime / "japanese_chars.dict.yaml").is_file())

    def test_a_missing_file_inside_a_clone_is_skipped(self):
        (self.clones / "rime-japanese" / "japanese.kana.dict.yaml").unlink()
        self._deploy(schemas=JAPANESE)
        self.assertFalse((self.rime / "japanese.kana.dict.yaml").exists())
        self.assertTrue((self.rime / "japanese.jmdict.dict.yaml").is_file())

    def test_a_schema_only_fetches_the_sources_its_dictionaries_come_from(self):
        # sno_japanese composes from the japanese dictionary alone; the pinyin
        # and en2jp tables japanese_tw_eng reverse-looks-up through come from
        # the other source, and are not downloaded for it.
        self._deploy(schemas=["rime_ice", "sno_japanese"])
        self.assertEqual(self.provider_calls, ["rime-japanese"])

    def test_the_mixed_schema_fetches_both_of_its_sources(self):
        self._deploy(schemas=["rime_ice", "japanese_tw_eng"])
        self.assertEqual(sorted(self.provider_calls), ["rime-japanese", "rime-snomiao"])

    def test_a_source_needed_twice_is_resolved_once(self):
        self._deploy(schemas=JAPANESE)
        self.assertEqual(len(self.provider_calls), len(set(self.provider_calls)))

    def test_deselecting_the_japanese_schemas_removes_the_extracted_files(self):
        self._deploy(schemas=JAPANESE)
        self.assertTrue((self.rime / "japanese.jmdict.dict.yaml").is_file())
        self._deploy()
        for files in assets.SOURCE_FILES.values():
            for relative in files.values():
                self.assertFalse((self.rime / relative).exists(), relative)


class ThemeDeployTests(DeployCase):
    """The candidate window's theme is generated from the active Omarchy theme."""

    def test_the_theme_lands_under_the_plugins_own_directory(self):
        self._deploy()
        for name in ("theme.conf", "panel.svg", "highlight.svg", "radio.svg", "arrow.svg"):
            self.assertTrue(self._theme_file(name).is_file(), name)

    def test_the_directory_is_the_one_fcitx5_is_told_to_use(self):
        self._deploy()
        self.assertEqual(self._theme_file("theme.conf").parent.name, theme.DIRECTORY)

    def test_the_installed_theme_carries_the_palettes_colours(self):
        self._deploy()
        self.assertIn("#f38d70", self._theme_file("theme.conf").read_text())
        self.assertIn('rx="17"', self._theme_file("panel.svg").read_text())

    def test_every_generated_file_is_recorded_in_the_manifest(self):
        self._deploy()
        recorded = self._manifest()["themes"]
        for name in ("theme.conf", "panel.svg", "highlight.svg", "radio.svg", "arrow.svg"):
            self.assertIn(f"{theme.DIRECTORY}/{name}", recorded)

    def test_a_later_deploy_with_another_palette_rewrites_the_theme(self):
        self._deploy()
        self.theme = lambda: theme.resolved(NORD, 4, "nord")
        self._deploy()
        rewritten = self._theme_file("theme.conf").read_text()
        self.assertIn("#88c0d0", rewritten)
        self.assertNotIn("#f38d70", rewritten)
        self.assertEqual(self._theme_file("panel.svg").read_text().count('rx="4"'), 1)

    def test_switching_themes_does_not_strand_the_old_directory(self):
        self._deploy()
        self.theme = lambda: theme.resolved(NORD, 4, "nord")
        self._deploy()
        self.assertEqual(
            [p.name for p in self.themes.iterdir()], [theme.DIRECTORY]
        )

    def test_the_deploy_says_which_theme_it_followed(self):
        self._deploy()
        self.assertIn("ristretto", " ".join(self.notices))
        self.assertIn("17", " ".join(self.notices))

    def test_a_missing_omarchy_theme_still_installs_a_legible_one(self):
        self.theme = lambda: theme.resolved(
            theme.FALLBACK_PALETTE, 0, "", fallback=True, reason="no colors.toml",
        )
        self._deploy()
        installed = self._theme_file("theme.conf").read_text()
        self.assertIn(theme.FALLBACK_PALETTE["foreground"], installed)

    def test_a_missing_omarchy_theme_is_said_out_loud(self):
        self.theme = lambda: theme.resolved(
            theme.FALLBACK_PALETTE, 0, "", fallback=True, reason="no colors.toml",
        )
        self._deploy()
        said = " ".join(self.notices)
        self.assertIn("no colors.toml", said)
        self.assertIn("fallback", said.lower())

    def test_a_theme_reader_that_blows_up_does_not_cost_the_user_their_input(self):
        def exploding():
            raise RuntimeError("state directory vanished mid-read")

        self.theme = exploding
        self._deploy()
        self.assertTrue(self._theme_file("theme.conf").is_file())
        self.assertTrue((self.rime / "default.custom.yaml").is_file())
        self.assertIn("fallback", " ".join(self.notices).lower())

    def test_a_deploy_without_a_themes_target_is_still_a_deploy(self):
        targets = {"rime": self.rime, "fcitx5": self.fcitx5}
        deploy.deploy(
            settings.resolve({}), targets, self.state, self.payload,
            asset_provider=self.provider, theme_source=self.theme,
        )
        self.assertTrue((self.rime / "default.custom.yaml").is_file())
        self.assertFalse(self.themes.exists())

    def test_deploying_twice_leaves_the_theme_byte_for_byte(self):
        self._deploy()
        before = self._theme_file("theme.conf").read_text()
        self._deploy()
        self.assertEqual(before, self._theme_file("theme.conf").read_text())


class EnvironmentDeployTests(DeployCase):
    """The QT_IM_MODULE override is a deployed file like any other.

    It used to be a file nobody owned: hand-written into
    ~/.config/environment.d on one machine, absent everywhere else, and
    therefore invisible until a fresh install went wrong. Putting it through the
    manifest is what makes it arrive with a deploy and leave with an uninstall.
    """

    def installed(self) -> Path:
        return self.environment / generate.ENVIRONMENT_PATH

    def test_a_deploy_writes_the_override(self):
        self._deploy()
        self.assertTrue(self.installed().is_file())

    def test_it_lands_under_the_packaged_files_own_name(self):
        # Overriding is by filename. A different name leaves both in effect.
        self._deploy()
        self.assertEqual(self.installed().name, self.packaged.name)

    def test_it_is_recorded_in_the_manifest(self):
        self._deploy()
        self.assertEqual(list(self._manifest()[deploy.ENVIRONMENT_TARGET]),
                         [generate.ENVIRONMENT_PATH])

    def test_uninstall_removes_it_so_the_packaged_file_applies_again(self):
        self._deploy()
        deploy.uninstall(self.state, self.targets)
        self.assertFalse(self.installed().exists())

    def test_a_hand_written_file_is_backed_up_before_it_is_replaced(self):
        # The machine this was diagnosed on already had one. Overwriting it
        # without a copy would throw away the only record of the diagnosis.
        self.environment.mkdir(parents=True)
        self.installed().write_text("# mine\nQT_IM_MODULE=fcitx\n", encoding="utf-8")
        self._deploy()
        backups = list(
            (self.state / "backups").rglob(generate.ENVIRONMENT_PATH)
        )
        self.assertEqual(len(backups), 1)
        self.assertIn("# mine", backups[0].read_text())

    def test_the_deployed_file_sets_qt_im_module_by_default(self):
        self._deploy()
        self.assertIn("\nQT_IM_MODULE=fcitx", self.installed().read_text())

    def test_qt_im_module_is_left_unset_when_the_setting_is_off(self):
        self._deploy(qtImModule=False)
        self.assertNotIn("\nQT_IM_MODULE=", self.installed().read_text())

    def test_it_carries_the_packaged_variables_it_did_not_drop(self):
        self._deploy()
        text = self.installed().read_text()
        self.assertIn("INPUT_METHOD=fcitx", text)
        self.assertIn("XMODIFIERS=@im=fcitx", text)

    def test_a_repeat_deploy_leaves_the_file_alone(self):
        # Same reason as every other generated file: an unchanged rewrite is a
        # changed mtime for nothing.
        self._deploy()
        before = self.installed().stat().st_mtime_ns
        self._deploy()
        self.assertEqual(self.installed().stat().st_mtime_ns, before)

    def test_a_target_that_is_not_offered_is_simply_not_written(self):
        # `deploy` writes into the targets it is handed. A caller with no
        # environment target - an older manifest, a test - is not an error.
        targets = {name: path for name, path in self.targets.items()
                   if name != deploy.ENVIRONMENT_TARGET}
        installed = deploy.deploy(
            settings.resolve({}), targets, self.state, self.payload,
            asset_provider=self.provider, theme_source=self.theme,
        )
        self.assertNotIn(deploy.ENVIRONMENT_TARGET, installed)
        self.assertFalse(self.environment.exists())


class ActiveGroupTests(unittest.TestCase):
    def test_japanese_group_follows_its_setting(self):
        off = deploy.active_groups(settings.resolve({}))
        self.assertNotIn("rime-japanese", off)
        on = deploy.active_groups(settings.resolve({"japanese": True}))
        self.assertIn("rime-japanese", on)

    def test_unconditional_groups_are_always_present(self):
        groups = deploy.active_groups(settings.resolve({}))
        self.assertIn("fcitx5", groups)

    def test_the_annotation_group_follows_its_switch(self):
        # `rime` holds the annotation filter and nothing else, so it is the
        # annotation feature's group rather than an unconditional one.
        self.assertNotIn("rime", deploy.active_groups(settings.resolve({})))
        self.assertIn("rime", deploy.active_groups(settings.resolve({"annotation": True})))

    def test_no_payload_group_ships_a_theme_any_more(self):
        # The candidate window's theme is generated, not shipped.
        self.assertNotIn("themes", deploy.PAYLOAD_GROUPS)



class DictionaryDeployTests(DeployCase):
    def test_the_generated_schema_carries_the_dictionary_path_it_was_given(self):
        deploy.deploy(
            settings.resolve({"annotation": True}), self.targets, self.state, self.payload,
            asset_provider=self.provider,
            dictionary_path="/cache/omarchy-ime/dictionary/v0-test/cedict.txt",
        )
        content = (self.rime / "rime_ice.custom.yaml").read_text()
        self.assertIn("/cache/omarchy-ime/dictionary/v0-test/cedict.txt", content)
        self.assertIn("annotate/dictionary", content)

    def test_the_dictionary_is_not_copied_into_the_rime_directory(self):
        self._deploy()
        self.assertFalse((self.rime / "cedict.txt").exists())


class ResolveDictionaryTests(unittest.TestCase):
    """Resolving the dictionary never reaches the network: the fetcher is injected."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.calls = []
        self.addCleanup(self._tmp.cleanup)
        # A download is only accepted if it is shaped like CC-CEDICT, so the
        # fixture has to be. The entry floor is the one part of that policy a
        # three-line fixture cannot satisfy, so it is lowered rather than
        # padded out with a hundred thousand invented entries.
        floor = mock.patch.object(assets, "MIN_ENTRIES", 1)
        floor.start()
        self.addCleanup(floor.stop)

    def _fetcher(self, url, destination):
        self.calls.append(url)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cedict().encode("utf-8"))

    def _resolve(self, **overrides):
        # With the master switch on, unless a test says otherwise; the switch
        # itself is exercised by AnnotationSwitchDictionaryTests.
        overrides.setdefault("annotation", True)
        return deploy.resolve_dictionary(
            settings.resolve(overrides), self.cache, fetcher=self._fetcher
        )

    def test_the_path_is_the_one_assets_owns(self):
        self.assertEqual(self._resolve(), assets.dictionary_path(self.cache))

    def test_annotation_on_downloads_the_dictionary(self):
        self.assertTrue(self._resolve().is_file())
        self.assertEqual(self.calls, [assets.dictionary_url()])

    def test_either_direction_alone_is_enough_to_want_it(self):
        self._resolve(annotateChinese=False)
        self._resolve(annotateEnglish=False)
        self.assertTrue(assets.dictionary_path(self.cache).is_file())

    def test_annotation_off_does_not_download_anything(self):
        path = self._resolve(annotateChinese=False, annotateEnglish=False)
        self.assertEqual(self.calls, [])
        self.assertFalse(path.exists())

    def test_the_path_is_still_returned_when_the_download_fails(self):
        def failing(url, destination):
            raise OSError("network down")

        path = deploy.resolve_dictionary(settings.resolve({"annotation": True}), self.cache, fetcher=failing)
        self.assertEqual(path, assets.dictionary_path(self.cache))
        self.assertFalse(path.exists())

    def test_what_arrived_is_reported_rather_than_swapped_in_silently(self):
        notices = []
        deploy.resolve_dictionary(settings.resolve({"annotation": True}), self.cache,
                                  fetcher=self._fetcher, notice=notices.append)
        self.assertTrue(any("2026-08-25T08:12:38Z" in line for line in notices),
                        notices)

    def test_a_refused_download_says_why(self):
        def hostile(url, destination):
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("<html>503 Service Unavailable</html>", encoding="utf-8")

        notices = []
        deploy.resolve_dictionary(settings.resolve({"annotation": True}), self.cache,
                                  fetcher=hostile, notice=notices.append)
        self.assertTrue(any(line.startswith(deploy.WARNING) for line in notices),
                        notices)
        self.assertFalse(assets.dictionary_path(self.cache).exists())

    def test_nothing_is_said_when_no_download_was_wanted(self):
        notices = []
        deploy.resolve_dictionary(
            settings.resolve({"annotateChinese": False, "annotateEnglish": False}),
            self.cache, fetcher=self._fetcher, notice=notices.append)
        self.assertEqual(notices, [])

    def test_a_deploy_after_a_failed_download_still_writes_a_usable_schema(self):
        def failing(url, destination):
            raise OSError("network down")

        resolved = settings.resolve({"annotation": True})
        path = deploy.resolve_dictionary(resolved, self.cache, fetcher=failing)
        rendered = generate.render(resolved, path)["rime_ice.custom.yaml"]
        self.assertIn("annotate/dictionary", rendered)
        self.assertIn(str(path), rendered)


# The three lines librime writes when a patch will not resolve. Copied from a
# real journal: this is the failure that took Chinese input down.
BUILD_FAILURE_JOURNAL = """\
Loaded addon rime
E20260825 20:01:26.932223 140197794674368 config_compiler.cc:97] trying to append string to non scalar
E20260825 20:01:26.932230 140197794674368 config_compiler.cc:216] error applying patch to engine/filters/+
E20260825 20:01:26.932238 140197794674368 config_compiler.cc:534] unresolved dependency: Patch(rime_ice.custom:patch <optional>)
E20260825 20:01:26.932239 140197794674368 config_component.cc:250] error building config: rime_ice.schema
"""

# The same window when the schema builds: librime says nothing at all, so the
# only evidence of success is the absence of the lines above.
BUILD_SUCCESS_JOURNAL = """\
Loaded addon rime
Wayland self diagnose information
Using Wayland native input method protocol: 1
"""


def _mentions(command, word: str) -> bool:
    """Whether any argument of `command` carries `word`.

    D-Bus method names arrive as one long argument
    (`org.fcitx.Fcitx.Controller1.ReloadAddonConfig`), so an `in` test against
    the list itself never matches.
    """
    return any(word in str(part) for part in command)


class FakeRunner:
    """Stands in for subprocess. No journal is read, no service is touched.

    It answers four different commands, because a redeploy asks four different
    questions: is fcitx5 on the bus at all, is its journal the unit's journal,
    where does the window start, and what does the window say. Defaults are the
    ordinary machine - fcitx5 running under its unit - so a test only says the
    part it is about.
    """

    def __init__(self, journal="", cursor="s=abc;i=1", trigger_returncode=0,
                 trigger_stderr="", cursor_returncode=0, running=True,
                 unit_active=True, journal_returncode=0, journal_stderr=""):
        self.journal = journal
        self.cursor = cursor
        self.trigger_returncode = trigger_returncode
        self.trigger_stderr = trigger_stderr
        self.cursor_returncode = cursor_returncode
        self.running = running
        self.unit_active = unit_active
        self.journal_returncode = journal_returncode
        self.journal_stderr = journal_stderr
        self.commands = []

    def __call__(self, command):
        command = list(command)
        self.commands.append(command)
        if command[0] == "journalctl":
            if "--show-cursor" in command:
                stdout = f"-- cursor: {self.cursor}\n" if self.cursor else ""
                return subprocess.CompletedProcess(
                    command, self.cursor_returncode, stdout, ""
                )
            return subprocess.CompletedProcess(
                command, self.journal_returncode, self.journal, self.journal_stderr
            )
        if command[0] == "systemctl":
            return subprocess.CompletedProcess(
                command, 0 if self.unit_active else 3,
                "active\n" if self.unit_active else "inactive\n", ""
            )
        if _mentions(command, "NameHasOwner"):
            return subprocess.CompletedProcess(
                command, 0, f"({str(self.running).lower()},)\n", ""
            )
        return subprocess.CompletedProcess(
            command, self.trigger_returncode, "", self.trigger_stderr
        )

    def journal_reads(self):
        return [c for c in self.commands if c[0] == "journalctl" and "--show-cursor" not in c]

    def triggers(self):
        """Only the rebuild trigger - not the two questions asked before it."""
        return [c for c in self.commands if _mentions(c, "ReloadAddonConfig")]

    def probes(self):
        return [c for c in self.commands
                if c[0] != "journalctl" and not _mentions(c, "ReloadAddonConfig")]


class RedeployCase(unittest.TestCase):
    """Every redeploy in the suite is driven through injected doubles."""

    def setUp(self):
        self.slept = []

    def _redeploy(self, runner, **overrides):
        options = {"settle": 1.0, "poll": 0.5}
        options.update(overrides)
        return deploy.redeploy(runner=runner, sleeper=self.slept.append, **options)


class RedeployTriggerTests(RedeployCase):
    """Part one: something has to ask Rime to rebuild."""

    def test_the_rebuild_is_actually_triggered(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL)
        self._redeploy(runner)
        self.assertEqual(runner.triggers(), [list(deploy.REDEPLOY_COMMAND)])

    def test_the_trigger_reaches_the_rime_addon(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL)
        self._redeploy(runner)
        trigger = " ".join(runner.triggers()[0])
        self.assertIn("org.fcitx.Fcitx5", trigger)
        self.assertIn("rime", trigger)

    def test_no_service_is_restarted(self):
        # Restarting fcitx5 rebuilds too, but it drops every client's input
        # context. Reloading the addon keeps the session alive.
        #
        # systemctl is not forbidden outright any more: `is-active` is how the
        # redeploy finds out whether the unit's journal is the journal fcitx5 is
        # writing to, and a read is not a restart. What is forbidden is any
        # command that stops, starts or restarts something.
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL)
        self._redeploy(runner)
        for command in runner.commands:
            for word in ("restart", "stop", "start", "kill"):
                self.assertNotIn(word, command, command)

    def test_the_trigger_comes_before_the_journal_is_read(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL)
        self._redeploy(runner)
        kinds = [
            "journal" if command[0] == "journalctl"
            else "trigger" if _mentions(command, "ReloadAddonConfig")
            else "probe"
            for command in runner.commands
        ]
        trigger = kinds.index("trigger")
        self.assertLess(trigger, kinds.index("journal", trigger))

    def test_a_trigger_that_fails_is_not_a_success(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL, trigger_returncode=1,
                            trigger_stderr="Error: fcitx5 is not running")
        result = self._redeploy(runner)
        self.assertFalse(result["succeeded"])
        self.assertFalse(result["triggered"])
        self.assertTrue(result["errors"])
        self.assertIn("fcitx5 is not running", " ".join(result["errors"]))

    def test_a_trigger_that_fails_does_not_go_on_to_read_the_journal(self):
        runner = FakeRunner(trigger_returncode=1, trigger_stderr="no session bus")
        self._redeploy(runner)
        self.assertEqual(runner.journal_reads(), [])


class RedeployVerificationTests(RedeployCase):
    """Part two: a schema that failed to build must not look like one that did."""

    def test_a_quiet_journal_is_a_success(self):
        result = self._redeploy(FakeRunner(journal=BUILD_SUCCESS_JOURNAL))
        self.assertTrue(result["triggered"])
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["errors"], [])

    def test_an_empty_journal_is_a_success(self):
        self.assertTrue(self._redeploy(FakeRunner(journal=""))["succeeded"])

    def test_a_failed_build_is_reported(self):
        result = self._redeploy(FakeRunner(journal=BUILD_FAILURE_JOURNAL))
        self.assertFalse(result["succeeded"])
        self.assertTrue(result["triggered"])

    def test_the_reported_errors_are_the_lines_rime_wrote(self):
        result = self._redeploy(FakeRunner(journal=BUILD_FAILURE_JOURNAL))
        joined = "\n".join(result["errors"])
        self.assertIn("error building config: rime_ice.schema", joined)
        self.assertIn("unresolved dependency: Patch(rime_ice.custom:patch", joined)
        self.assertIn("error applying patch to engine/filters/+", joined)

    def test_only_the_failing_lines_are_reported(self):
        result = self._redeploy(FakeRunner(journal=BUILD_FAILURE_JOURNAL))
        self.assertNotIn("Loaded addon rime", "\n".join(result["errors"]))

    def test_each_signature_alone_is_enough_to_fail(self):
        for line in (
            "E20260825 config_component.cc:250] error building config: rime_ice.schema",
            "E20260825 config_compiler.cc:534] unresolved dependency: Patch(x <optional>)",
            "E20260825 config_compiler.cc:216] error applying patch to engine/filters/+",
        ):
            with self.subTest(line=line):
                result = self._redeploy(FakeRunner(journal=line + "\n"))
                self.assertFalse(result["succeeded"])
                self.assertEqual(result["errors"], [line])

    def test_ordinary_noise_is_not_mistaken_for_a_build_error(self):
        noisy = (
            "warning: config does not conform to schema: document path\n"
            "Service name change: org.fcitx.Fcitx5\n"
            "Found 753 input method(s) in addon keyboard\n"
        )
        self.assertTrue(self._redeploy(FakeRunner(journal=noisy))["succeeded"])


class RedeployJournalWindowTests(RedeployCase):
    """Only the lines this redeploy produced may count against it."""

    def test_the_journal_is_bounded_by_a_cursor_taken_before_the_trigger(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL, cursor="s=abc;i=42")
        self._redeploy(runner)
        first = runner.commands[0]
        self.assertEqual(first[0], "journalctl")
        self.assertIn("--show-cursor", first)
        read = runner.journal_reads()[0]
        self.assertIn("--after-cursor", read)
        self.assertIn("s=abc;i=42", read)

    def test_a_missing_cursor_falls_back_to_a_timestamp(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL, cursor="", cursor_returncode=1)
        self._redeploy(runner)
        read = runner.journal_reads()[0]
        self.assertIn("--since", read)
        self.assertNotIn("--after-cursor", read)

    def test_the_journal_is_read_for_the_fcitx5_unit_only(self):
        runner = FakeRunner(journal=BUILD_SUCCESS_JOURNAL)
        self._redeploy(runner)
        for read in runner.journal_reads():
            self.assertIn("--user", read)
            self.assertIn("-u", read)
            self.assertIn(deploy.UNIT, read)


class RedeployWaitTests(RedeployCase):
    """The rebuild runs on librime's own thread, so the result needs waiting for."""

    def test_it_waits_before_deciding_a_build_succeeded(self):
        self._redeploy(FakeRunner(journal=BUILD_SUCCESS_JOURNAL), settle=2.0, poll=0.5)
        self.assertEqual(self.slept, [0.5, 0.5, 0.5, 0.5])

    def test_it_stops_waiting_as_soon_as_the_build_has_failed(self):
        runner = FakeRunner(journal=BUILD_FAILURE_JOURNAL)
        self._redeploy(runner, settle=10.0, poll=0.5)
        self.assertEqual(self.slept, [0.5])
        self.assertEqual(len(runner.journal_reads()), 1)

    def test_an_error_that_arrives_late_is_still_caught(self):
        runner = FakeRunner(journal="")

        def slow(seconds):
            self.slept.append(seconds)
            if len(self.slept) == 3:
                runner.journal = BUILD_FAILURE_JOURNAL

        result = deploy.redeploy(runner=runner, sleeper=slow, settle=5.0, poll=0.5)
        self.assertFalse(result["succeeded"])
        self.assertEqual(len(self.slept), 3)

    def test_nothing_sleeps_when_the_trigger_never_fired(self):
        self._redeploy(FakeRunner(trigger_returncode=1, trigger_stderr="boom"))
        self.assertEqual(self.slept, [])


class ProfileDeployTests(DeployCase):
    """The Fcitx5 profile is merged into, never written over."""

    GERMAN = (
        "[Groups/0]\n"
        "Name=Default\n"
        "Default Layout=de\n"
        "DefaultIM=keyboard-de\n"
        "\n"
        "[Groups/0/Items/0]\n"
        "Name=keyboard-de\n"
        "Layout=\n"
        "\n"
        "[GroupOrder]\n"
        "0=Default\n"
    )

    def written(self):
        return (self.fcitx5 / generate.PROFILE_PATH).read_text(encoding="utf-8")

    def test_a_profile_is_written_where_there_was_none(self):
        self._deploy()
        self.assertIn("Name=rime", self.written())

    def test_an_existing_layout_is_not_replaced_with_a_us_one(self):
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / generate.PROFILE_PATH).write_text(self.GERMAN, encoding="utf-8")
        self._deploy()
        written = self.written()
        self.assertIn("Default Layout=de", written)
        self.assertIn("Name=keyboard-de", written)
        self.assertNotIn("keyboard-us", written)
        self.assertIn("Name=rime", written)

    def test_the_profile_is_in_the_manifest_as_a_replacement(self):
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / generate.PROFILE_PATH).write_text(self.GERMAN, encoding="utf-8")
        self._deploy()
        self.assertTrue(self._manifest()["fcitx5"][generate.PROFILE_PATH]["replaced"])

    def test_uninstall_gives_the_user_their_own_profile_back(self):
        self.fcitx5.mkdir(parents=True)
        (self.fcitx5 / generate.PROFILE_PATH).write_text(self.GERMAN, encoding="utf-8")
        self._deploy()
        deploy.uninstall(self.state, self.targets)
        self.assertEqual(self.written(), self.GERMAN)

    def test_deploying_twice_does_not_add_rime_twice(self):
        self._deploy()
        self._deploy()
        self.assertEqual(self.written().count("Name=rime"), 1)


class RedeployOutcomeTests(RedeployCase):
    """A quiet journal is not proof that anything was built."""

    def setUp(self):
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.build = Path(self._tmp.name) / "build"
        self.build.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _built(self, *schemas, age: float = 0.0):
        import os
        import time as clock

        for schema in schemas:
            path = self.build / f"{schema}.schema.yaml"
            path.write_text("built", encoding="utf-8")
            if age:
                stamp = clock.time() - age
                os.utime(path, (stamp, stamp))

    def test_a_schema_with_no_built_file_is_not_a_success(self):
        # The failure a fresh machine actually produces: with rime-ice absent,
        # `__include: rime_ice_suggestion:/` makes the deployer exit non-zero and
        # print nothing at all, leaving build/ empty.
        result = self._redeploy(
            FakeRunner(journal=BUILD_SUCCESS_JOURNAL),
            schemas=["rime_ice"], build_dir=self.build,
        )
        self.assertFalse(result["succeeded"])
        self.assertTrue(result["verified"])
        self.assertIn("rime_ice", " ".join(result["errors"]))

    def test_a_built_schema_is_a_success(self):
        self._built("rime_ice")
        result = self._redeploy(
            FakeRunner(journal=BUILD_SUCCESS_JOURNAL),
            schemas=["rime_ice"], build_dir=self.build,
        )
        self.assertTrue(result["succeeded"])
        self.assertTrue(result["verified"])

    def test_a_build_older_than_what_was_just_written_does_not_count(self):
        import time as clock

        self._built("rime_ice", age=600)
        result = self._redeploy(
            FakeRunner(journal=BUILD_SUCCESS_JOURNAL),
            schemas=["rime_ice"], build_dir=self.build,
            since=clock.time(), patience=1.0,
        )
        self.assertFalse(result["succeeded"])

    def test_only_the_schemas_that_were_deployed_are_looked_for(self):
        self._built("rime_ice")
        result = self._redeploy(
            FakeRunner(journal=BUILD_SUCCESS_JOURNAL),
            schemas=["rime_ice"], build_dir=self.build,
        )
        self.assertEqual(result["errors"], [])

    def test_a_journal_error_still_wins_over_the_build_check(self):
        self._built("rime_ice")
        result = self._redeploy(
            FakeRunner(journal=BUILD_FAILURE_JOURNAL),
            schemas=["rime_ice"], build_dir=self.build,
        )
        self.assertFalse(result["succeeded"])
        self.assertIn("error building config", " ".join(result["errors"]))

    def test_the_dictionary_failures_a_missing_download_produces_are_caught(self):
        # These are the lines librime writes when a schema's import_tables names
        # a table that was never fetched. None of them was in BUILD_ERRORS, so
        # the failure the plugin's own network handling produced was invisible
        # to the check that exists to catch exactly that.
        for line in (
            "E20260825 dict_compiler.cc:86] source file"
            " '/home/x/.local/share/fcitx5/rime/japanese.jmdict.dict.yaml'"
            " does not exist.",
            "E20260825 deployment_tasks.cc:376] dictionary 'japanese'"
            " failed to compile.",
            "E20260825 deployment_tasks.cc:200] Error deploying: rime_ice",
            "E20260825 dictionary.cc:401] Cannot load dictionary 'japanese'.",
            "E20260825 dict_compiler.cc:120] missing dict settings.",
        ):
            with self.subTest(line=line):
                result = self._redeploy(FakeRunner(journal=line + "\n"))
                self.assertFalse(result["succeeded"], line)
                self.assertEqual(result["errors"], [line])


class RedeployEvidenceTests(RedeployCase):
    """A verdict with nothing behind it says so rather than saying "rebuilt"."""

    def test_fcitx5_not_on_the_bus_is_not_a_failure(self):
        # The natural sequence on a fresh machine. Nothing is broken: the files
        # are on disk and fcitx5 builds them at startup.
        result = self._redeploy(FakeRunner(running=False))
        self.assertTrue(result["succeeded"])
        self.assertFalse(result["triggered"])
        self.assertFalse(result["verified"])
        self.assertIn("when it starts", result["detail"])

    def test_nothing_is_triggered_when_fcitx5_is_not_there(self):
        runner = FakeRunner(running=False)
        self._redeploy(runner)
        self.assertEqual(runner.triggers(), [])
        self.assertEqual(self.slept, [])

    def test_a_journal_that_cannot_be_read_is_not_evidence_of_success(self):
        result = self._redeploy(
            FakeRunner(journal="", journal_returncode=1,
                       journal_stderr="No journal files were found."))
        self.assertTrue(result["succeeded"])
        self.assertFalse(result["verified"])
        self.assertIn("could not read the journal", result["detail"])

    def test_a_fcitx5_outside_the_unit_is_not_evidence_either(self):
        # librime logs through fcitx5's stderr, which is this unit's journal and
        # nowhere else - so a fcitx5 started some other way leaves a journal
        # that was never going to hold the answer, and that reads exactly like a
        # build that went well.
        result = self._redeploy(FakeRunner(journal="", unit_active=False))
        self.assertTrue(result["succeeded"])
        self.assertFalse(result["verified"])
        self.assertIn(deploy.UNIT, result["detail"])

    def test_the_build_directory_is_evidence_even_when_the_journal_is_not(self):
        with TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "rime_ice.schema.yaml").write_text("built", encoding="utf-8")
            result = self._redeploy(
                FakeRunner(journal="", unit_active=False),
                schemas=["rime_ice"], build_dir=build,
            )
        self.assertTrue(result["succeeded"])
        self.assertTrue(result["verified"])

    def test_a_quiet_readable_journal_is_still_verified(self):
        result = self._redeploy(FakeRunner(journal=BUILD_SUCCESS_JOURNAL))
        self.assertTrue(result["verified"])


# What the annotation feature writes into a schema file and nothing else does:
# its filter registration, its switch, its hotkey binding, and the `annotate/*`
# keys the filter reads. Every line a deploy with the feature off drops, and
# every line it adds back when the feature returns, has to be one of these.
ANNOTATION_LINE_HEADS = (
    '  "engine/filters/+":',
    '    - "lua_filter@*annotate"',
    "  switches/+:",
    "    - { name: annotate,",
    "  key_binder/bindings/+:",
    "    - { accept: ",
    "  annotate/",
)


def _annotation_line(line: str) -> bool:
    return any(line.startswith(head) for head in ANNOTATION_LINE_HEADS)


class AnnotationSwitchDeployTests(DeployCase):
    """`annotation: false` leaves nothing of the feature on disk.

    Every assertion here reads the sandbox tree back: which files exist, what
    they say, and what the manifest lists. Nothing asks the plugin what it
    thinks it installed.
    """

    ALL_CHINESE = list(settings.CHINESE_SCHEMAS)

    def _rime_files(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.rime)): path.read_text(encoding="utf-8")
            for path in sorted(self.rime.rglob("*")) if path.is_file()
        }

    def test_a_fresh_install_installs_no_filter_at_all(self):
        # deploy.deploy called directly, with nothing but the defaults: this is
        # what a machine that has never seen the settings gets.
        deploy.deploy(settings.resolve({}), self.targets, self.state, self.payload,
                      asset_provider=self.provider, theme_source=self.theme)
        self.assertFalse((self.rime / "lua" / "annotate.lua").exists())
        self.assertFalse((self.rime / "lua").exists())
        self.assertNotIn("lua/annotate.lua", self._manifest().get("rime", {}))
        for relative, text in self._rime_files().items():
            self.assertNotIn("annotate", text, relative)

    def test_the_filter_is_installed_while_the_switch_is_on(self):
        self._deploy(annotation=True)
        self.assertTrue((self.rime / "lua" / "annotate.lua").is_file())
        self.assertIn("lua/annotate.lua", self._manifest()["rime"])

    def test_turning_it_off_removes_the_filter_a_previous_deploy_installed(self):
        self._deploy(annotation=True)
        self.assertTrue((self.rime / "lua" / "annotate.lua").is_file())
        self._deploy(annotation=False)
        self.assertFalse((self.rime / "lua" / "annotate.lua").exists())
        # The directory the filter lived in goes with it: left behind it is
        # litter nothing else uses.
        self.assertFalse((self.rime / "lua").exists())
        self.assertNotIn("lua/annotate.lua", self._manifest().get("rime", {}))

    def test_turning_it_back_on_brings_the_filter_back(self):
        self._deploy(annotation=True)
        self._deploy(annotation=False)
        self._deploy(annotation=True)
        self.assertTrue((self.rime / "lua" / "annotate.lua").is_file())
        self.assertIn("lua/annotate.lua", self._manifest()["rime"])

    def test_off_writes_no_annotation_key_into_any_schema_file(self):
        self._deploy(annotation=False, schemas=self.ALL_CHINESE)
        files = self._rime_files()
        self.assertIn("default.custom.yaml", files)
        for relative, text in files.items():
            with self.subTest(file=relative):
                self.assertNotIn("annotate", text)
                self.assertNotIn("lua_filter", text)
                self.assertNotIn("key_binder", text)
                self.assertNotIn("switches", text)

    def test_the_directions_change_nothing_while_it_is_off(self):
        self._deploy(annotation=False, schemas=self.ALL_CHINESE,
                     annotateChinese=True, annotateEnglish=True,
                     annotateShowPinyin=True, annotateHotkey="Control+g")
        for relative, text in self._rime_files().items():
            self.assertNotIn("annotate", text, relative)
            self.assertNotIn("Control+g", text, relative)

    def test_the_only_difference_between_on_and_off_is_the_annotation_block(self):
        self._deploy(annotation=True, schemas=self.ALL_CHINESE)
        on = self._rime_files()
        self._deploy(annotation=False, schemas=self.ALL_CHINESE)
        off = self._rime_files()

        # Off never has a file on lacks, and the filter itself is gone.
        self.assertEqual(set(off) - set(on), set())
        self.assertIn("lua/annotate.lua", on)
        self.assertNotIn("lua/annotate.lua", off)

        seen_heads = set()
        for relative in sorted(set(on) - {"lua/annotate.lua"}):
            with self.subTest(file=relative):
                on_lines = on[relative].splitlines()
                if relative in off:
                    off_lines = off[relative].splitlines()
                    # Nothing is added by turning the feature off...
                    self.assertEqual([line for line in off_lines
                                      if line not in on_lines], [])
                    removed = [line for line in on_lines if line not in off_lines]
                else:
                    # ...and a schema file that vanished entirely held nothing
                    # but the header, `patch:` and the block.
                    removed = [line for line in on_lines
                               if line and not line.startswith("#")
                               and line != "patch:"]
                    self.assertTrue(removed, relative)
                for line in removed:
                    self.assertTrue(_annotation_line(line), line)
                    seen_heads.update(
                        head for head in ANNOTATION_LINE_HEADS if line.startswith(head)
                    )
        # And every part of the block was actually seen going, not just some.
        self.assertEqual(seen_heads, set(ANNOTATION_LINE_HEADS))

    def test_default_custom_yaml_is_identical_either_way(self):
        # The switch owns the per-schema files. schema_list, page size and the
        # traditionalize patch are not its business.
        self._deploy(annotation=True, schemas=self.ALL_CHINESE)
        on = (self.rime / "default.custom.yaml").read_text(encoding="utf-8")
        self._deploy(annotation=False, schemas=self.ALL_CHINESE)
        off = (self.rime / "default.custom.yaml").read_text(encoding="utf-8")
        self.assertEqual(on, off)

    def test_the_japanese_feature_is_not_touched_by_the_switch(self):
        self._deploy(annotation=False, schemas=JAPANESE)
        self.assertTrue((self.rime / "japanese.dict.yaml").is_file())
        self.assertTrue((self.rime / "japanese.jmdict.dict.yaml").is_file())
        self.assertIn("japanese_tw_eng", (self.rime / "default.custom.yaml").read_text())


class AnnotationSwitchDictionaryTests(unittest.TestCase):
    """The dictionary is fetched only for a feature that is on."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.calls = []
        self.notices = []
        self.addCleanup(self._tmp.cleanup)
        floor = mock.patch.object(assets, "MIN_ENTRIES", 1)
        floor.start()
        self.addCleanup(floor.stop)

    def _fetcher(self, url, destination):
        self.calls.append(url)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cedict().encode("utf-8"))

    def _resolve(self, **overrides):
        return deploy.resolve_dictionary(
            settings.resolve(overrides), self.cache, fetcher=self._fetcher,
            notice=self.notices.append,
        )

    def test_a_fresh_install_downloads_nothing(self):
        path = self._resolve()
        self.assertEqual(self.calls, [])
        self.assertFalse(path.exists())
        self.assertEqual(self.notices, [])

    def test_off_downloads_nothing_even_with_both_directions_on(self):
        path = self._resolve(annotation=False, annotateChinese=True, annotateEnglish=True)
        self.assertEqual(self.calls, [])
        self.assertFalse(path.exists())
        self.assertFalse(self.cache.joinpath("dictionary").exists())

    def test_on_downloads_it(self):
        self.assertTrue(self._resolve(annotation=True).is_file())
        self.assertEqual(self.calls, [assets.dictionary_url()])

    def test_off_leaves_a_cached_dictionary_exactly_where_it_was(self):
        # Cache, not configuration: turning the feature off must not cost the
        # user the download when they turn it back on.
        cached = self._resolve(annotation=True)
        marker = cached.parent / assets.COMPLETE_MARKER
        before = (cached.read_bytes(), cached.stat().st_mtime_ns,
                  marker.read_bytes())
        self.calls.clear()
        path = self._resolve(annotation=False)
        self.assertEqual(path, cached)
        self.assertTrue(cached.is_file())
        self.assertEqual(
            (cached.read_bytes(), cached.stat().st_mtime_ns, marker.read_bytes()),
            before,
        )
        self.assertEqual(self.calls, [])

    def test_the_path_is_still_the_one_the_schema_would_name(self):
        self.assertEqual(self._resolve(annotation=False),
                         assets.dictionary_path(self.cache))


if __name__ == "__main__":
    unittest.main()
