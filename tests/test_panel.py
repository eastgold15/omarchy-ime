"""What Panel.qml declares about the settings it shows.

The panel is QML and nothing here runs it. What can be read is the table of
on/off settings it lays out - each row's key, group, fallback and what it
depends on - and that table is where the master switch has to be an everyday
setting and the gloss settings have to answer to it.
"""
import json
import re
import unittest
from pathlib import Path

from lib import settings

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "Panel.qml").read_text(encoding="utf-8")

_TABLE = re.compile(r"readonly property var booleanSettings: \[(.*?)\n  \]", re.S)
_ENTRY = re.compile(r"\{(.*?)\}", re.S)
_FIELD = re.compile(r'(\w+):\s*("(?:[^"\\]|\\.)*"|true|false)')


def boolean_settings() -> dict[str, dict]:
    """The `booleanSettings` table, keyed by setting."""
    table = _TABLE.search(PANEL)
    assert table, "Panel.qml no longer declares booleanSettings"
    rows = {}
    for entry in _ENTRY.finditer(table.group(1)):
        fields = {
            name: json.loads(value) for name, value in _FIELD.findall(entry.group(1))
        }
        rows[fields["key"]] = fields
    return rows


class BooleanSettingTableTests(unittest.TestCase):
    def setUp(self):
        self.rows = boolean_settings()

    def test_every_row_is_a_real_setting_with_the_settings_modules_default(self):
        for key, row in self.rows.items():
            with self.subTest(key=key):
                self.assertIn(key, settings.DEFAULTS)
                self.assertIs(settings.DEFAULTS[key][1], bool)
                self.assertEqual(row["fallback"], settings.DEFAULTS[key][0])

    def test_every_boolean_setting_has_a_row(self):
        expected = {key for key, (_, kind, _) in settings.DEFAULTS.items() if kind is bool}
        self.assertEqual(set(self.rows), expected)


class AnnotationSwitchTests(unittest.TestCase):
    GOVERNED = ("annotateChinese", "annotateEnglish", "annotateShowPinyin")

    def setUp(self):
        self.rows = boolean_settings()

    def test_the_master_switch_is_an_everyday_setting(self):
        self.assertEqual(self.rows["annotation"]["group"], "settings")
        self.assertIs(self.rows["annotation"]["fallback"], False)
        self.assertNotIn("requires", self.rows["annotation"])

    def test_it_comes_before_the_switches_it_governs(self):
        keys = list(self.rows)
        for key in self.GOVERNED:
            self.assertLess(keys.index("annotation"), keys.index(key), key)

    def test_the_gloss_switches_answer_to_it(self):
        for key in self.GOVERNED:
            with self.subTest(key=key):
                self.assertEqual(self.rows[key].get("requires"), "annotation")

    def test_nothing_else_answers_to_it(self):
        for key, row in self.rows.items():
            if key not in self.GOVERNED:
                self.assertNotIn("requires", row, key)

    def test_the_row_component_honours_requires(self):
        component = PANEL[PANEL.index("component BooleanSetting"):]
        component = component[:component.index("\n  }")]
        self.assertRegex(component, re.compile(r"enabled:.*requires", re.S))

    def test_the_hotkey_field_answers_to_it_too(self):
        field = PANEL[PANEL.index("id: hotkeyField"):]
        field = field[:field.index("onEditingFinished")]
        self.assertRegex(field, re.compile(r'enabled:.*settingValue\("annotation"', re.S))


if __name__ == "__main__":
    unittest.main()
