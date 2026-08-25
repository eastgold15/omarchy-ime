import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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


# --- Rime schema switching --------------------------------------------------
#
# The GVariant texts below are what gdbus printed on the machine this was
# written against. The annotated empty array is not a guess: gdbus prints
# `(@a{sv} {},)` for an empty dictionary, because an empty container inside a
# one-element tuple has no sibling to infer its type from.
LIST_OUTPUT = "(['japanese_tw_eng', 'rime_ice', 'sno_japanese'],)\n"
ANNOTATED_EMPTY_OUTPUT = "(@as [],)\n"
BARE_EMPTY_OUTPUT = "([],)\n"
CURRENT_OUTPUT = "('rime_ice',)\n"


class FakeGdbus:
    """A runner that answers by which D-Bus method the command asked for."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        method = ""
        if "--method" in command:
            method = command[command.index("--method") + 1]
        return self.answers.get(method.rsplit(".", 1)[-1], (0, ""))

    def argument(self, method):
        """The trailing arguments of the call that named `method`."""
        for command in self.calls:
            if command[command.index("--method") + 1].endswith("." + method):
                return command[command.index("--method") + 2:]
        raise AssertionError(f"{method} was never called")


class VariantParsingTests(unittest.TestCase):
    """gdbus prints GVariant text, which is not JSON and is never evaluated."""

    def test_parses_the_array_gdbus_prints_for_three_schemas(self):
        self.assertEqual(
            imestate.parse_string_array(LIST_OUTPUT),
            ["japanese_tw_eng", "rime_ice", "sno_japanese"],
        )

    def test_parses_the_annotated_empty_array_gdbus_really_prints(self):
        self.assertEqual(imestate.parse_string_array(ANNOTATED_EMPTY_OUTPUT), [])

    def test_parses_a_bare_empty_array_too(self):
        self.assertEqual(imestate.parse_string_array(BARE_EMPTY_OUTPUT), [])

    def test_parses_the_single_string_tuple_a_getter_returns(self):
        self.assertEqual(imestate.parse_string(CURRENT_OUTPUT), "rime_ice")

    def test_keeps_a_backslash_escaped_quote(self):
        self.assertEqual(imestate.parse_string_array(r"(['it\'s'],)"), ["it's"])

    def test_reads_a_double_quoted_string(self):
        self.assertEqual(imestate.parse_string('("it\'s",)'), "it's")

    def test_an_empty_string_is_still_a_string(self):
        self.assertEqual(imestate.parse_string("('',)"), "")

    def test_rejects_text_that_is_not_a_tuple(self):
        self.assertIsNone(imestate.parse_string_array("nonsense"))
        self.assertIsNone(imestate.parse_string("nonsense"))

    def test_rejects_an_unterminated_string(self):
        self.assertIsNone(imestate.parse_string_array("(['japanese_tw_eng],)"))

    def test_rejects_a_tuple_holding_something_that_is_not_a_string(self):
        self.assertIsNone(imestate.parse_string("(42,)"))
        self.assertIsNone(imestate.parse_string_array("([42],)"))

    def test_rejects_an_array_where_a_single_string_was_asked_for(self):
        self.assertIsNone(imestate.parse_string(LIST_OUTPUT))

    def test_does_not_evaluate_its_input(self):
        """The whole reason this parser exists instead of eval."""
        self.assertIsNone(imestate.parse_string("(__import__('os').getcwd(),)"))


class SchemaListTests(unittest.TestCase):
    def test_lists_the_schemas_rime_reports(self):
        runner = FakeGdbus({"ListAllSchemas": (0, LIST_OUTPUT)})
        self.assertEqual(
            imestate.schemas(runner),
            ["japanese_tw_eng", "rime_ice", "sno_japanese"],
        )

    def test_asks_the_rime_object_on_the_fcitx_bus_name(self):
        runner = FakeGdbus({"ListAllSchemas": (0, LIST_OUTPUT)})
        imestate.schemas(runner)
        command = runner.calls[0]
        self.assertEqual(command[0], "gdbus")
        self.assertIn("org.fcitx.Fcitx5", command)
        self.assertIn("/rime", command)
        self.assertIn("org.fcitx.Fcitx.Rime1.ListAllSchemas", command)

    def test_no_schemas_configured_is_an_empty_list(self):
        runner = FakeGdbus({"ListAllSchemas": (0, ANNOTATED_EMPTY_OUTPUT)})
        self.assertEqual(imestate.schemas(runner), [])

    def test_a_failed_call_is_an_empty_list_rather_than_an_error(self):
        runner = FakeGdbus({"ListAllSchemas": (1, "")})
        self.assertEqual(imestate.schemas(runner), [])

    def test_unparseable_output_is_an_empty_list(self):
        runner = FakeGdbus({"ListAllSchemas": (0, "Error: no such interface\n")})
        self.assertEqual(imestate.schemas(runner), [])


class CurrentSchemaTests(unittest.TestCase):
    def test_reports_the_schema_rime_is_using(self):
        runner = FakeGdbus({"GetCurrentSchema": (0, CURRENT_OUTPUT)})
        self.assertEqual(imestate.current_schema(runner), "rime_ice")

    def test_a_failed_call_reads_as_no_schema(self):
        runner = FakeGdbus({"GetCurrentSchema": (1, "")})
        self.assertIsNone(imestate.current_schema(runner))

    def test_an_empty_answer_reads_as_no_schema(self):
        runner = FakeGdbus({"GetCurrentSchema": (0, "('',)\n")})
        self.assertIsNone(imestate.current_schema(runner))


class SetSchemaTests(unittest.TestCase):
    def test_passes_the_id_as_a_quoted_gvariant_string(self):
        """A bare `10` or `true` would be parsed as a number or a boolean."""
        runner = FakeGdbus()
        self.assertTrue(imestate.set_schema("rime_ice", runner))
        self.assertEqual(runner.argument("SetSchema"), ["'rime_ice'"])

    def test_escapes_a_quote_in_the_id(self):
        runner = FakeGdbus()
        imestate.set_schema("it's", runner)
        self.assertEqual(runner.argument("SetSchema"), [r"'it\'s'"])

    def test_a_failed_switch_is_reported(self):
        runner = FakeGdbus({"SetSchema": (1, "")})
        self.assertFalse(imestate.set_schema("rime_ice", runner))

    def test_refuses_an_empty_id_without_calling_anything(self):
        runner = FakeGdbus()
        self.assertFalse(imestate.set_schema("", runner))
        self.assertEqual(runner.calls, [])


# What Rime compiles into a built schema. Trimmed to the shape that matters:
# top-level keys at column zero, their children two spaces in.
BUILT_SCHEMA = """__build_info:
  rime_version: 1.17.0
menu:
  name: not the schema name
schema:
  author:
    - ensigma96
  description: |
    A mixed schema.

  name: "Mixed ZH-JA-EN"
  schema_id: japanese_tw_eng
  version: 2.1.0
"""

BUILT_UNQUOTED = """schema:
  description: "Focused Japanese Romaji input."
  name: Japanese
  schema_id: sno_japanese
"""


class SchemaNameTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.build = Path(self._tmp.name)

    def write(self, schema_id, text):
        (self.build / f"{schema_id}.schema.yaml").write_text(text, encoding="utf-8")

    def test_reads_the_name_rime_built_into_the_schema(self):
        self.write("japanese_tw_eng", BUILT_SCHEMA)
        self.assertEqual(
            imestate.schema_name("japanese_tw_eng", self.build), "Mixed ZH-JA-EN"
        )

    def test_reads_an_unquoted_name(self):
        self.write("sno_japanese", BUILT_UNQUOTED)
        self.assertEqual(imestate.schema_name("sno_japanese", self.build), "Japanese")

    def test_a_blank_line_inside_the_block_does_not_end_it(self):
        """rime_ice's description is a block scalar with a blank line in it."""
        self.write("japanese_tw_eng", BUILT_SCHEMA)
        self.assertEqual(
            imestate.schema_name("japanese_tw_eng", self.build), "Mixed ZH-JA-EN"
        )

    def test_a_name_under_another_top_level_key_is_not_the_schema_name(self):
        self.write("only_menu", "menu:\n  name: not the schema name\n")
        self.assertIsNone(imestate.schema_name("only_menu", self.build))

    def test_a_non_ascii_name_survives_the_read(self):
        self.write("accented", 'schema:\n  name: "Rimé Ice"\n')
        self.assertEqual(imestate.schema_name("accented", self.build), "Rimé Ice")

    def test_a_schema_that_was_never_built_has_no_name(self):
        self.assertIsNone(imestate.schema_name("missing", self.build))

    def test_no_build_directory_means_no_name(self):
        self.assertIsNone(imestate.schema_name("japanese_tw_eng", None))

    def test_an_id_cannot_reach_outside_the_build_directory(self):
        self.assertIsNone(imestate.schema_name("../../etc/passwd", self.build))


class DescribeSchemasTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.build = Path(self._tmp.name)
        (self.build / "japanese_tw_eng.schema.yaml").write_text(
            BUILT_SCHEMA, encoding="utf-8"
        )
        self.runner = FakeGdbus({
            "ListAllSchemas": (0, LIST_OUTPUT),
            "GetCurrentSchema": (0, CURRENT_OUTPUT),
        })

    def test_carries_the_id_the_name_and_which_one_is_current(self):
        described = imestate.describe_schemas(self.runner, self.build)
        self.assertEqual([entry["id"] for entry in described],
                         ["japanese_tw_eng", "rime_ice", "sno_japanese"])
        self.assertEqual(described[0]["name"], "Mixed ZH-JA-EN")
        self.assertEqual([entry["current"] for entry in described],
                         [False, True, False])

    def test_a_schema_with_no_built_name_shows_its_id(self):
        described = imestate.describe_schemas(self.runner, self.build)
        self.assertEqual(described[1]["name"], "rime_ice")

    def test_nothing_to_describe_when_rime_cannot_be_reached(self):
        runner = FakeGdbus({"ListAllSchemas": (1, "")})
        self.assertEqual(imestate.describe_schemas(runner, self.build), [])


class ReloadAddonTests(unittest.TestCase):
    def test_reloads_the_named_addon_through_the_controller(self):
        runner = FakeGdbus()
        self.assertTrue(imestate.reload_addon("classicui", runner))
        command = runner.calls[0]
        self.assertIn("/controller", command)
        self.assertIn("org.fcitx.Fcitx.Controller1.ReloadAddonConfig", command)
        self.assertEqual(command[-1], "'classicui'")

    def test_a_failed_reload_is_reported(self):
        runner = FakeGdbus({"ReloadAddonConfig": (1, "")})
        self.assertFalse(imestate.reload_addon("classicui", runner))


if __name__ == "__main__":
    unittest.main()
