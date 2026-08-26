import json
import unittest
from pathlib import Path

from lib import settings

ROOT = Path(__file__).resolve().parent.parent


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.widget = self.manifest["barWidget"]
        self.schema = {item["key"]: item for item in self.widget["schema"]}

    def test_identity_matches_the_settings_module(self):
        self.assertEqual(self.manifest["id"], settings.PLUGIN_ID)

    def test_declares_a_bar_widget_entry_point(self):
        self.assertIn("bar-widget", self.manifest["kinds"])
        self.assertEqual(self.manifest["entryPoints"]["barWidget"], "Panel.qml")

    def test_the_entry_point_file_exists(self):
        self.assertTrue((ROOT / self.manifest["entryPoints"]["barWidget"]).is_file())

    def test_every_setting_is_exposed(self):
        self.assertEqual(set(self.schema), set(settings.DEFAULTS))

    def test_schema_defaults_match_the_settings_module(self):
        for key, (default, _kind, _bounds) in settings.DEFAULTS.items():
            self.assertEqual(self.schema[key]["defaultValue"], default, key)

    def test_widget_defaults_match_the_settings_module(self):
        for key, (default, _kind, _bounds) in settings.DEFAULTS.items():
            self.assertEqual(self.widget["defaults"][key], default, key)

    def test_integer_bounds_match_what_resolve_clamps_to(self):
        for key, (_default, kind, bounds) in settings.DEFAULTS.items():
            if kind is int and bounds is not None:
                low, high = bounds
                self.assertEqual(self.schema[key]["min"], low, key)
                self.assertEqual(self.schema[key]["max"], high, key)

    def test_every_setting_carries_a_label_and_description(self):
        for key, item in self.schema.items():
            self.assertTrue(item.get("label"), key)
            self.assertTrue(item.get("description"), key)


class SchemaPickerTests(unittest.TestCase):
    """The schema selection is one collapsed control, not twenty switches.

    Omarchy's own Indicators widget declares `type: "multiselect"` and the shell
    ships Ui/MultiSelect.qml to render it: a trigger that shows the selection and
    a popup holding the list. That is what a choice among twenty schemas needs,
    so the plugin uses the platform's control rather than a wall of booleans.
    """

    def setUp(self):
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.entry = {
            item["key"]: item for item in self.manifest["barWidget"]["schema"]
        }["schemas"]

    def test_the_selection_is_declared_as_a_multiselect(self):
        self.assertEqual(self.entry["type"], "multiselect")

    def test_every_catalogue_schema_is_offered(self):
        values = [option["value"] for option in self.entry["options"]]
        self.assertEqual(values, list(settings.SCHEMA_IDS))

    def test_the_options_are_grouped_by_family_in_order(self):
        families = [option["description"] for option in self.entry["options"]]
        self.assertEqual(
            families,
            [settings.SCHEMA_FAMILY[option["value"]] for option in self.entry["options"]],
        )
        # Each family appears in one run, so the list reads as families.
        seen = []
        for family in families:
            if not seen or seen[-1] != family:
                self.assertNotIn(family, seen, family)
                seen.append(family)

    def test_the_default_selection_is_rime_ice_alone(self):
        self.assertEqual(self.entry["defaultValue"], ["rime_ice"])
        self.assertEqual(
            self.manifest["barWidget"]["defaults"]["schemas"], ["rime_ice"]
        )

    def test_no_option_carries_a_hand_written_display_name(self):
        # A schema's readable name lives in its own yaml and is read at runtime.
        # An option here that named one would be a second, stale answer.
        for option in self.entry["options"]:
            self.assertNotIn("label", option, option["value"])

    def test_the_retired_japanese_switch_is_gone(self):
        keys = {item["key"] for item in self.manifest["barWidget"]["schema"]}
        self.assertNotIn("japanese", keys)
        self.assertNotIn("japanese", self.manifest["barWidget"]["defaults"])


class AnnotationSwitchTests(unittest.TestCase):
    def setUp(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.widget = manifest["barWidget"]
        self.entry = {item["key"]: item for item in self.widget["schema"]}["annotation"]

    def test_it_is_a_boolean_that_defaults_to_off(self):
        self.assertEqual(self.entry["type"], "boolean")
        self.assertIs(self.entry["defaultValue"], False)
        self.assertIs(self.widget["defaults"]["annotation"], False)

    def test_the_description_says_what_turning_it_off_removes(self):
        description = self.entry["description"]
        for word in ("filter", "dictionary", "hotkey", "off by default"):
            self.assertIn(word, description.lower(), word)

    def test_it_comes_before_the_settings_it_governs(self):
        keys = [item["key"] for item in self.widget["schema"]]
        for key in ("annotateChinese", "annotateEnglish", "annotateShowPinyin",
                    "annotateHotkey"):
            self.assertLess(keys.index("annotation"), keys.index(key), key)


if __name__ == "__main__":
    unittest.main()
