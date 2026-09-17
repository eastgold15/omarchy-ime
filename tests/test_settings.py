import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lib import settings


class ResolveTests(unittest.TestCase):
    def test_defaults_apply_when_nothing_is_set(self):
        result = settings.resolve({})
        self.assertEqual(result["schemas"], ["rime_ice"])
        self.assertTrue(result["traditionalize"])
        self.assertEqual(result["pageSize"], 10)
        self.assertEqual(result["annotateHotkey"], "Control+e")

    def test_explicit_values_win(self):
        result = settings.resolve({"schemas": ["luna_pinyin"], "pageSize": 5})
        self.assertEqual(result["schemas"], ["luna_pinyin"])
        self.assertEqual(result["pageSize"], 5)

    def test_unknown_keys_are_dropped(self):
        result = settings.resolve({"id": "ryuhzk.ime", "nonsense": 1})
        self.assertNotIn("id", result)
        self.assertNotIn("nonsense", result)

    def test_the_retired_english_switch_is_read_as_nothing_at_all(self):
        # englishInput was removed rather than migrated. Unlike `japanese`,
        # which stood for a schema selection that still exists, this one asked
        # for something that never worked - so an install that still carries it
        # simply has it ignored, and no default of it comes back.
        result = settings.resolve({"englishInput": False})
        self.assertNotIn("englishInput", result)
        with self.assertRaisesRegex(ValueError, "unknown setting"):
            settings.validate("englishInput", False)

    def test_page_size_is_clamped_to_what_rime_accepts(self):
        self.assertEqual(settings.resolve({"pageSize": 0})["pageSize"], 1)
        self.assertEqual(settings.resolve({"pageSize": 99})["pageSize"], 10)

    def test_wrong_types_fall_back_to_the_default(self):
        self.assertEqual(settings.resolve({"pageSize": "many"})["pageSize"], 10)
        self.assertTrue(settings.resolve({"traditionalize": "yes"})["traditionalize"])


class PanelWidthTests(unittest.TestCase):
    """How wide the popup panel is, in the layout units Style.space scales.

    The default is measured from the content: the widest row is a setting with
    its control on the trailing edge, and the label beside it has to fit without
    eliding. The panel reads the same key with the same bounds.
    """

    def test_it_has_a_default_and_a_range(self):
        low, high = settings.PANEL_WIDTH_BOUNDS
        self.assertLessEqual(low, settings.DEFAULT_PANEL_WIDTH)
        self.assertLessEqual(settings.DEFAULT_PANEL_WIDTH, high)
        self.assertEqual(
            settings.resolve({})["panelWidth"], settings.DEFAULT_PANEL_WIDTH
        )

    def test_a_hand_edited_value_out_of_range_is_clamped_rather_than_obeyed(self):
        low, high = settings.PANEL_WIDTH_BOUNDS
        self.assertEqual(settings.resolve({"panelWidth": 10})["panelWidth"], low)
        self.assertEqual(settings.resolve({"panelWidth": 9000})["panelWidth"], high)

    def test_a_value_that_is_not_a_number_falls_back(self):
        self.assertEqual(
            settings.resolve({"panelWidth": "wide"})["panelWidth"],
            settings.DEFAULT_PANEL_WIDTH,
        )

    def test_a_write_out_of_range_is_refused_rather_than_clamped(self):
        # resolve is forgiving because it reads a hand-edited file; a write is
        # someone asking for this exact value.
        with self.assertRaisesRegex(ValueError, "must be between"):
            settings.validate("panelWidth", 10)

    def test_a_write_inside_the_range_is_accepted(self):
        self.assertEqual(settings.validate("panelWidth", 500), 500)


class LoadTests(unittest.TestCase):
    def _shell_json(self, directory, entry):
        path = Path(directory) / "shell.json"
        path.write_text(json.dumps({
            "bar": {"layout": {"center": [{"id": "omarchy.clock"}, entry]}},
        }))
        return path

    def test_settings_come_from_the_widget_entry(self):
        with TemporaryDirectory() as directory:
            path = self._shell_json(directory, {"id": "ryuhzk.ime", "schemas": ["bopomofo"]})
            self.assertEqual(settings.load(path)["schemas"], ["bopomofo"])

    def test_missing_widget_yields_defaults(self):
        with TemporaryDirectory() as directory:
            path = self._shell_json(directory, {"id": "other.widget"})
            self.assertEqual(settings.load(path)["schemas"], ["rime_ice"])

    def test_missing_file_yields_defaults(self):
        self.assertEqual(settings.load(Path("/nonexistent/shell.json"))["schemas"], ["rime_ice"])


class SaveTests(unittest.TestCase):
    """Writing settings back onto the widget's own entry in shell.json.

    shell.json belongs to the whole Omarchy bar and is hand-edited, so these
    tests care as much about what a write leaves alone as about what it changes.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.path = self.directory / "shell.json"
        self.path.write_text(json.dumps(self.document(), indent=2) + "\n", encoding="utf-8")

    def document(self) -> dict:
        return {
            "version": 1,
            "bar": {
                "position": "top",
                "layout": {
                    "left": ["omarchy.workspaces"],
                    "center": [{"id": "omarchy.clock", "format": "HH:mm", "seconds": False}],
                    "right": [
                        {"id": settings.PLUGIN_ID, "traditionalize": False, "pageSize": 7},
                        {"id": "omarchy.battery", "warnAt": 15},
                    ],
                },
            },
            "notifications": {"position": "top-right"},
        }

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def entry(self, document: dict) -> dict:
        for candidate in document["bar"]["layout"]["right"]:
            if isinstance(candidate, dict) and candidate.get("id") == settings.PLUGIN_ID:
                return candidate
        raise AssertionError("the widget entry disappeared")

    def test_a_write_lands_on_the_widget_entry(self):
        settings.save(self.path, {"schemas": ["bopomofo"], "pageSize": 5})
        entry = self.entry(self.read())
        self.assertEqual(entry["schemas"], ["bopomofo"])
        self.assertEqual(entry["pageSize"], 5)

    def test_a_write_returns_the_resolved_settings(self):
        result = settings.save(self.path, {"schemas": ["bopomofo"]})
        self.assertEqual(result["schemas"], ["bopomofo"])
        self.assertEqual(result["pageSize"], 7)
        self.assertEqual(result["annotateHotkey"], "Control+e")

    def test_a_write_keeps_the_entrys_other_keys(self):
        settings.save(self.path, {"schemas": ["bopomofo"]})
        self.assertEqual(self.entry(self.read())["id"], settings.PLUGIN_ID)
        self.assertEqual(self.entry(self.read())["pageSize"], 7)

    def test_a_write_leaves_other_widgets_alone(self):
        settings.save(self.path, {"schemas": ["bopomofo"]})
        layout = self.read()["bar"]["layout"]
        self.assertEqual(layout["left"], ["omarchy.workspaces"])
        self.assertEqual(layout["center"][0], {"id": "omarchy.clock", "format": "HH:mm", "seconds": False})
        self.assertEqual(layout["right"][1], {"id": "omarchy.battery", "warnAt": 15})

    def test_a_write_leaves_unrelated_configuration_alone(self):
        settings.save(self.path, {"schemas": ["bopomofo"]})
        document = self.read()
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["bar"]["position"], "top")
        self.assertEqual(document["notifications"], {"position": "top-right"})

    def test_the_file_is_still_valid_json_and_stays_readable(self):
        settings.save(self.path, {"verticalCandidates": False})
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertIn('\n  "bar"', text)  # two-space indent, as Omarchy writes it
        self.assertFalse(settings.load(self.path)["verticalCandidates"])

    def test_an_unknown_key_is_refused(self):
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            settings.save(self.path, {"nonsense": True})
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_the_id_key_cannot_be_written_through_settings(self):
        with self.assertRaises(ValueError):
            settings.save(self.path, {"id": "someone.else"})
        self.assertEqual(self.entry(self.read())["id"], settings.PLUGIN_ID)

    def test_a_wrongly_typed_value_is_refused_rather_than_written(self):
        before = self.path.read_text(encoding="utf-8")
        for values in ({"traditionalize": "yes"}, {"pageSize": "many"}, {"annotateHotkey": 5}):
            with self.assertRaises(ValueError):
                settings.save(self.path, values)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_boolean_is_not_accepted_where_an_integer_belongs(self):
        with self.assertRaises(ValueError):
            settings.save(self.path, {"pageSize": True})

    def test_an_out_of_range_value_is_refused_rather_than_clamped(self):
        for value in (0, 99):
            with self.assertRaises(ValueError):
                settings.save(self.path, {"pageSize": value})
        self.assertEqual(self.entry(self.read())["pageSize"], 7)

    def test_an_empty_string_is_refused(self):
        with self.assertRaises(ValueError):
            settings.save(self.path, {"annotateHotkey": ""})

    def test_a_missing_widget_entry_raises(self):
        document = self.document()
        document["bar"]["layout"]["right"] = [{"id": "omarchy.battery"}]
        self.path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            settings.save(self.path, {"schemas": ["bopomofo"]})

    def test_a_missing_file_raises(self):
        with self.assertRaises(ValueError):
            settings.save(self.directory / "absent.json", {"schemas": ["bopomofo"]})

    def test_a_damaged_file_raises_and_is_left_untouched(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            settings.save(self.path, {"schemas": ["bopomofo"]})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json")

    def test_a_refused_write_leaves_no_temporary_files_behind(self):
        with self.assertRaises(ValueError):
            settings.save(self.path, {"nonsense": True})
        self.assertEqual([p.name for p in self.directory.iterdir()], ["shell.json"])

    def test_a_successful_write_leaves_no_temporary_files_behind(self):
        settings.save(self.path, {"schemas": ["bopomofo"]})
        self.assertEqual([p.name for p in self.directory.iterdir()], ["shell.json"])

    def test_entries_that_are_not_objects_do_not_stop_the_search(self):
        document = self.document()
        document["bar"]["layout"]["left"] = ["omarchy.workspaces", 42, None]
        self.path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        settings.save(self.path, {"schemas": ["bopomofo"]})
        self.assertEqual(self.entry(self.read())["schemas"], ["bopomofo"])

    def test_writing_nothing_is_allowed_and_changes_nothing(self):
        before = json.loads(self.path.read_text(encoding="utf-8"))
        result = settings.save(self.path, {})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), before)
        self.assertEqual(result["pageSize"], 7)


class CatalogueTests(unittest.TestCase):
    """The list of schemas the picker offers.

    Nothing here checks a display name: a schema's readable name lives in its
    own schema yaml and is read at runtime, so a name asserted here would be a
    second, hand-written answer to a question the yaml already answers.
    """

    def test_every_family_id_is_unique(self):
        self.assertEqual(len(settings.SCHEMA_IDS), len(set(settings.SCHEMA_IDS)))

    def test_the_families_are_ordered_the_way_the_picker_shows_them(self):
        families = [family for family, _ids in settings.SCHEMA_FAMILIES]
        self.assertEqual(families[:4], ["全拼", "双拼", "注音", "形码"])

    def test_the_default_selection_is_rime_ice_alone(self):
        self.assertEqual(settings.DEFAULT_SCHEMAS, ["rime_ice"])

    def test_the_japanese_schemas_are_a_family_like_any_other(self):
        self.assertIn("japanese_tw_eng", settings.SCHEMA_IDS)
        self.assertIn("sno_japanese", settings.SCHEMA_IDS)
        self.assertEqual(settings.SCHEMA_FAMILY["sno_japanese"], "日语")

    def test_the_japanese_schemas_do_not_count_as_chinese(self):
        for schema_id in ("japanese_tw_eng", "sno_japanese"):
            self.assertNotIn(schema_id, settings.CHINESE_SCHEMAS, schema_id)

    def test_the_standalone_english_schema_is_not_offered(self):
        # melt_eng exists in rime-data, but English words are already reachable
        # without leaving the Chinese schema - Rime Ice mounts it inside it as a
        # secondary translator. Offering it as a schema would say the opposite.
        self.assertNotIn("melt_eng", settings.SCHEMA_IDS)

    def test_the_catalogue_falls_back_to_the_id_when_no_name_is_readable(self):
        entries = {entry["value"]: entry for entry in settings.catalogue()}
        self.assertEqual(entries["luna_pinyin"]["label"], "luna_pinyin")
        self.assertEqual(entries["luna_pinyin"]["description"], "全拼")

    def test_the_catalogue_carries_the_name_its_lookup_returns(self):
        entries = {e["value"]: e for e in settings.catalogue(lambda i: i.upper())}
        self.assertEqual(entries["bopomofo"]["label"], "BOPOMOFO")

    def test_a_lookup_that_fails_costs_the_name_and_nothing_else(self):
        def broken(schema_id):
            raise OSError("no rime-data on this machine")

        entries = settings.catalogue(broken)
        self.assertEqual(len(entries), len(settings.SCHEMA_IDS))
        self.assertEqual(entries[0]["label"], entries[0]["value"])

    def test_the_catalogue_is_in_catalogue_order(self):
        self.assertEqual(
            [entry["value"] for entry in settings.catalogue()], list(settings.SCHEMA_IDS)
        )


class SchemaSelectionTests(unittest.TestCase):
    """Resolving the selection is forgiving; writing it is not.

    A hand-edited shell.json must always still produce something Rime can
    compose with, so resolve repairs. A write is the opposite case - the caller
    asked for exactly this - so validate refuses.
    """

    def test_unknown_ids_are_dropped_rather_than_deployed(self):
        resolved = settings.resolve({"schemas": ["rime_ice", "rime_icc"]})
        self.assertEqual(resolved["schemas"], ["rime_ice"])

    def test_duplicates_are_collapsed(self):
        resolved = settings.resolve({"schemas": ["bopomofo", "bopomofo"]})
        self.assertEqual(resolved["schemas"], ["bopomofo"])

    def test_the_selection_comes_back_in_catalogue_order(self):
        resolved = settings.resolve({"schemas": ["sno_japanese", "bopomofo", "rime_ice"]})
        self.assertEqual(resolved["schemas"], ["rime_ice", "bopomofo", "sno_japanese"])

    def test_a_selection_without_a_chinese_schema_falls_back_to_the_default(self):
        for selection in ([], ["sno_japanese"], ["japanese_tw_eng", "sno_japanese"]):
            with self.subTest(selection=selection):
                resolved = settings.resolve({"schemas": list(selection)})
                self.assertIn("rime_ice", resolved["schemas"])

    def test_the_fallback_keeps_what_was_selected_beside_it(self):
        resolved = settings.resolve({"schemas": ["sno_japanese"]})
        self.assertEqual(resolved["schemas"], ["rime_ice", "sno_japanese"])

    def test_a_value_that_is_not_a_list_falls_back(self):
        for value in ("rime_ice", 5, None, {"rime_ice": True}):
            with self.subTest(value=value):
                self.assertEqual(settings.resolve({"schemas": value})["schemas"], ["rime_ice"])

    def test_a_string_is_not_read_as_a_list_of_characters(self):
        self.assertEqual(settings.resolve({"schemas": "rime_ice"})["schemas"], ["rime_ice"])

    def test_resolve_returns_a_copy_the_caller_cannot_mutate_into_the_defaults(self):
        first = settings.resolve({})["schemas"]
        first.append("bopomofo")
        self.assertEqual(settings.resolve({})["schemas"], ["rime_ice"])

    def test_validate_accepts_a_known_selection(self):
        self.assertEqual(
            settings.validate("schemas", ["rime_ice", "double_pinyin_flypy"]),
            ["rime_ice", "double_pinyin_flypy"],
        )

    def test_validate_refuses_an_unknown_id_by_name(self):
        with self.assertRaises(ValueError) as raised:
            settings.validate("schemas", ["rime_ice", "rime_icc"])
        self.assertIn("rime_icc", str(raised.exception))

    def test_validate_refuses_an_empty_selection(self):
        with self.assertRaises(ValueError):
            settings.validate("schemas", [])

    def test_validate_refuses_a_selection_with_no_chinese_schema(self):
        with self.assertRaises(ValueError):
            settings.validate("schemas", ["sno_japanese", "japanese_tw_eng"])

    def test_validate_refuses_the_standalone_english_schema(self):
        with self.assertRaises(ValueError):
            settings.validate("schemas", ["rime_ice", "melt_eng"])

    def test_validate_refuses_a_string_where_a_list_belongs(self):
        with self.assertRaises(ValueError):
            settings.validate("schemas", "rime_ice")

    def test_validate_refuses_a_non_string_member(self):
        with self.assertRaises(ValueError):
            settings.validate("schemas", ["rime_ice", 5])


class LegacyJapaneseTests(unittest.TestCase):
    """`japanese: true` was the old way to ask for the Japanese schemas.

    An install that has it must not lose them on upgrade, so the old key is read
    once and translated into a selection. It is not a setting any more: nothing
    writes it, and it stops being consulted the moment a selection exists.
    """

    def test_the_old_key_is_no_longer_a_setting(self):
        self.assertNotIn("japanese", settings.DEFAULTS)
        self.assertNotIn("japanese", settings.resolve({}))

    def test_the_old_key_brings_the_japanese_schemas_along(self):
        resolved = settings.resolve({"japanese": True})
        self.assertEqual(
            resolved["schemas"], ["rime_ice", "japanese_tw_eng", "sno_japanese"]
        )

    def test_the_old_key_switched_off_changes_nothing(self):
        self.assertEqual(settings.resolve({"japanese": False})["schemas"], ["rime_ice"])

    def test_an_explicit_selection_wins_over_the_old_key(self):
        resolved = settings.resolve({"japanese": True, "schemas": ["bopomofo"]})
        self.assertEqual(resolved["schemas"], ["bopomofo"])

    def test_the_old_key_cannot_be_written_any_more(self):
        with self.assertRaises(ValueError):
            settings.validate("japanese", True)


class LegacyJapaneseWriteTests(SaveTests):
    """Writing a selection retires the old key instead of leaving it to rot."""

    def document(self) -> dict:
        document = super().document()
        for entry in document["bar"]["layout"]["right"]:
            if entry.get("id") == settings.PLUGIN_ID:
                entry["japanese"] = True
        return document

    def test_an_install_with_the_old_key_still_gets_its_japanese_schemas(self):
        self.assertEqual(
            settings.load(self.path)["schemas"],
            ["rime_ice", "japanese_tw_eng", "sno_japanese"],
        )

    def test_writing_a_selection_removes_the_old_key(self):
        settings.save(self.path, {"schemas": ["rime_ice", "sno_japanese"]})
        self.assertNotIn("japanese", self.entry(self.read()))
        self.assertEqual(
            settings.load(self.path)["schemas"], ["rime_ice", "sno_japanese"]
        )

    def test_writing_something_else_leaves_the_old_key_to_keep_migrating(self):
        settings.save(self.path, {"pageSize": 5})
        self.assertTrue(self.entry(self.read())["japanese"])
        self.assertIn("sno_japanese", settings.load(self.path)["schemas"])


# A one-pixel PNG, a one-line SVG and a JPEG header, so a test can hand validate
# a file whose bytes really are the format its name claims - and one whose bytes
# are not.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d4948445200000001000000010806000000"
    "1f15c489"
    "0000000a49444154789c6300010000050001"
    "0d0a2db4"
    "0000000049454e44ae426082"
)
SVG_TEXT = (
    '<?xml version="1.0"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>\n'
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8


class BackgroundImageSettingTests(unittest.TestCase):
    """The candidate window's own background image, one per theme mode.

    Empty is the default and means "no image", so this setting has to accept an
    empty string where every other string setting refuses one. Anything that is
    not empty is a path Fcitx5 will be told to open, so it is checked here
    rather than written and then silently ignored.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)

    def write(self, name: str, content) -> Path:
        path = self.directory / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_both_modes_have_a_setting_and_both_default_to_nothing(self):
        for key in ("backgroundImageLight", "backgroundImageDark"):
            with self.subTest(key=key):
                self.assertIn(key, settings.DEFAULTS)
                self.assertEqual(settings.DEFAULTS[key][0], "")
                self.assertEqual(settings.resolve({})[key], "")

    def test_a_stored_path_is_read_back_unchanged(self):
        resolved = settings.resolve({"backgroundImageDark": "/home/a/b.png"})
        self.assertEqual(resolved["backgroundImageDark"], "/home/a/b.png")

    def test_a_hand_edited_non_string_costs_the_image_and_nothing_else(self):
        # resolve reads a hand-editable file and must always stay deployable.
        resolved = settings.resolve({"backgroundImageLight": 5})
        self.assertEqual(resolved["backgroundImageLight"], "")

    def test_a_path_that_no_longer_exists_is_still_read_back(self):
        # Reading is not the moment to refuse: the file may come back, and a
        # missing one costs a background rather than an input method.
        resolved = settings.resolve({"backgroundImageLight": "/gone/away.png"})
        self.assertEqual(resolved["backgroundImageLight"], "/gone/away.png")

    def test_an_empty_value_is_accepted_and_means_no_image(self):
        self.assertEqual(settings.validate("backgroundImageLight", ""), "")

    def test_a_real_png_is_accepted_and_comes_back_absolute(self):
        path = self.write("wallpaper.png", PNG_BYTES)
        self.assertEqual(settings.validate("backgroundImageDark", str(path)), str(path))

    def test_an_svg_is_accepted(self):
        path = self.write("panel.svg", SVG_TEXT)
        self.assertEqual(settings.validate("backgroundImageLight", str(path)), str(path))

    def test_a_jpeg_is_accepted(self):
        path = self.write("photo.jpg", JPEG_BYTES)
        self.assertEqual(settings.validate("backgroundImageLight", str(path)), str(path))

    def test_a_tilde_is_expanded_because_fcitx5_does_not_expand_it(self):
        home = self.directory / "home"
        home.mkdir()
        (home / "bg.png").write_bytes(PNG_BYTES)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertEqual(
                settings.validate("backgroundImageDark", "~/bg.png"),
                str(home / "bg.png"),
            )

    def test_a_relative_path_is_refused(self):
        self.write("wallpaper.png", PNG_BYTES)
        with self.assertRaises(ValueError) as raised:
            settings.validate("backgroundImageDark", "wallpaper.png")
        self.assertIn("absolute", str(raised.exception))

    def test_a_path_that_does_not_exist_is_refused_by_name(self):
        with self.assertRaises(ValueError) as raised:
            settings.validate("backgroundImageDark", str(self.directory / "absent.png"))
        self.assertIn("absent.png", str(raised.exception))

    def test_a_directory_is_refused(self):
        with self.assertRaises(ValueError):
            settings.validate("backgroundImageDark", str(self.directory))

    def test_a_file_that_cannot_be_read_is_refused(self):
        if os.geteuid() == 0:
            self.skipTest("root can read anything, so there is nothing to refuse")
        path = self.write("secret.png", PNG_BYTES)
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o600)
        with self.assertRaises(ValueError) as raised:
            settings.validate("backgroundImageDark", str(path))
        self.assertIn("readable", str(raised.exception))

    def test_a_format_classicui_cannot_load_is_refused_and_the_message_says_what_is(self):
        path = self.write("notes.txt", "not an image")
        with self.assertRaises(ValueError) as raised:
            settings.validate("backgroundImageDark", str(path))
        message = str(raised.exception)
        self.assertIn(".txt", message)
        self.assertIn(".png", message)

    def test_a_file_whose_bytes_do_not_match_its_name_is_refused(self):
        # classicui picks its loader from the extension - .png goes to cairo's
        # own PNG reader and everything else to gdk-pixbuf - so a JPEG called
        # .png is a file that will never load however good its bytes are.
        path = self.write("lying.png", JPEG_BYTES)
        with self.assertRaises(ValueError) as raised:
            settings.validate("backgroundImageDark", str(path))
        self.assertIn("PNG", str(raised.exception).upper())

    def test_a_non_string_is_refused(self):
        with self.assertRaises(ValueError):
            settings.validate("backgroundImageLight", 5)

    def test_the_accepted_extensions_include_the_two_the_theme_already_uses(self):
        self.assertIn(".png", settings.BACKGROUND_IMAGE_EXTENSIONS)
        self.assertIn(".svg", settings.BACKGROUND_IMAGE_EXTENSIONS)


class BackgroundImageWriteTests(SaveTests):
    """A write persists the checked, absolute path rather than what was typed."""

    def setUp(self):
        super().setUp()
        self._images = TemporaryDirectory()
        self.addCleanup(self._images.cleanup)
        self.images = Path(self._images.name)

    def test_a_write_stores_the_normalised_absolute_path(self):
        home = self.images / "home"
        home.mkdir()
        (home / "bg.png").write_bytes(PNG_BYTES)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            settings.save(self.path, {"backgroundImageDark": "~/bg.png"})
        self.assertEqual(
            self.entry(self.read())["backgroundImageDark"], str(home / "bg.png")
        )

    def test_a_refused_image_is_not_written(self):
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            settings.save(self.path, {"backgroundImageLight": "/nowhere/at/all.png"})
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_an_empty_value_clears_a_stored_image(self):
        (self.images / "bg.png").write_bytes(PNG_BYTES)
        settings.save(self.path, {"backgroundImageLight": str(self.images / "bg.png")})
        settings.save(self.path, {"backgroundImageLight": ""})
        self.assertEqual(self.entry(self.read())["backgroundImageLight"], "")
        self.assertEqual(settings.load(self.path)["backgroundImageLight"], "")


class AnnotationSwitchTests(unittest.TestCase):
    """One boolean over the whole annotation feature, off until asked for."""

    def test_annotation_is_off_by_default(self):
        self.assertIs(settings.resolve({})["annotation"], False)
        self.assertIs(settings.DEFAULTS["annotation"][0], False)

    def test_it_is_a_boolean(self):
        self.assertIs(settings.DEFAULTS["annotation"][1], bool)
        self.assertIs(settings.validate("annotation", True), True)
        with self.assertRaises(ValueError):
            settings.validate("annotation", "yes")

    def test_a_wrong_type_falls_back_to_off(self):
        self.assertIs(settings.resolve({"annotation": "on"})["annotation"], False)
        self.assertIs(settings.resolve({"annotation": 1})["annotation"], False)

    def test_the_gloss_settings_keep_their_values_while_it_is_off(self):
        # Turning the feature off is not forgetting how it was configured.
        resolved = settings.resolve({
            "annotation": False, "annotateChinese": False,
            "annotateShowPinyin": True, "annotateHotkey": "Control+g",
        })
        self.assertIs(resolved["annotateChinese"], False)
        self.assertIs(resolved["annotateEnglish"], True)
        self.assertIs(resolved["annotateShowPinyin"], True)
        self.assertEqual(resolved["annotateHotkey"], "Control+g")


if __name__ == "__main__":
    unittest.main()
