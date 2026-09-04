import configparser
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import features, generate, settings


class YamlError(Exception):
    """The generated text is not the block YAML this parser accepts."""


# Rime's + and - patch operators merge a list into an existing list. Handed a
# scalar the whole patch becomes unresolvable, the schema stops building, and
# input dies - which is exactly the shape of bug a substring assertion cannot
# see, because the scalar contains the same characters the list does.
MERGE_SUFFIXES = ("/+", "/-")


def _unescape(text: str) -> str:
    result, escaped = [], False
    for character in text:
        if escaped:
            result.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            result.append(character)
    return "".join(result)


def _split_flow(body: str) -> list[str]:
    """Split a flow collection body on its top-level commas."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    for character in body:
        if quoted:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
            current.append(character)
            continue
        if character in "{[":
            depth += 1
        elif character in "}]":
            depth -= 1
        elif character == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(character)
    if quoted or depth:
        raise YamlError(f"unterminated flow collection: {body!r}")
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _parse_value(text: str):
    """A scalar or a flow collection. The distinction the merge check needs."""
    if text.startswith("{"):
        if not text.endswith("}"):
            raise YamlError(f"unterminated flow mapping: {text!r}")
        mapping = {}
        for item in _split_flow(text[1:-1]):
            key, separator, value = item.partition(":")
            if not separator:
                raise YamlError(f"flow mapping entry without a value: {item!r}")
            mapping[key.strip()] = _parse_value(value.strip())
        return mapping
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError(f"unterminated flow sequence: {text!r}")
        return [_parse_value(item) for item in _split_flow(text[1:-1])]
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise YamlError(f"unterminated quoted scalar: {text!r}")
        return _unescape(text[1:-1])
    if text in ("true", "false"):
        return text == "true"
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def _split_key(content: str) -> tuple[str, str]:
    if content.startswith('"'):
        end = content.find('"', 1)
        if end < 0:
            raise YamlError(f"unterminated quoted key: {content!r}")
        rest = content[end + 1:]
        if not rest.startswith(":"):
            raise YamlError(f"quoted key is not followed by a colon: {content!r}")
        return _unescape(content[1:end]), rest[1:].strip()
    key, separator, value = content.partition(":")
    if not separator:
        raise YamlError(f"mapping entry without a colon: {content!r}")
    return key.strip(), value.strip()


def _parse_block(lines, index, indent):
    if lines[index][2].startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines, index, indent):
    items = []
    while index < len(lines):
        _, column, content = lines[index]
        if column < indent or not content.startswith("- "):
            break
        if column > indent:
            raise YamlError(f"unexpected indent in sequence: {content!r}")
        items.append(_parse_value(content[2:].strip()))
        index += 1
    return items, index


def _parse_mapping(lines, index, indent):
    mapping = {}
    while index < len(lines):
        _, column, content = lines[index]
        if column < indent:
            break
        if column > indent:
            raise YamlError(f"unexpected indent in mapping: {content!r}")
        if content.startswith("- "):
            break
        key, inline = _split_key(content)
        index += 1
        if inline:
            mapping[key] = _parse_value(inline)
            continue
        if index < len(lines) and lines[index][1] > column:
            mapping[key], index = _parse_block(lines, index, lines[index][1])
        else:
            # `key:` with nothing under it. YAML calls that null; naming it
            # here keeps a merge key that lost its list from parsing as a list.
            mapping[key] = None
    return mapping, index


def parse_block_yaml(text: str):
    """Parse the block YAML lib/generate.py emits, and only that.

    Python's standard library has no YAML parser and this project takes no
    dependencies, so this reads the subset the generator can produce: block
    mappings, block sequences, flow collections, double-quoted scalars and
    whole-line comments. Anything outside that raises rather than being
    guessed at, so a generator that starts emitting an unfamiliar shape fails
    the suite instead of quietly going unchecked.
    """
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((len(lines), len(raw) - len(raw.lstrip(" ")), stripped))
    if not lines:
        return {}
    document, index = _parse_block(lines, 0, lines[0][1])
    if index != len(lines):
        raise YamlError(f"trailing content at {lines[index][2]!r}")
    return document


def merge_key_violations(document, path: str = "") -> list[str]:
    """Every `.../+` or `.../-` key whose value is not a non-empty list."""
    problems: list[str] = []
    if isinstance(document, dict):
        for key, value in document.items():
            here = f"{path}{key}"
            if key.endswith(MERGE_SUFFIXES) and not (isinstance(value, list) and value):
                problems.append(here)
            problems.extend(merge_key_violations(value, f"{here}."))
    elif isinstance(document, list):
        for position, item in enumerate(document):
            problems.extend(merge_key_violations(item, f"{path}[{position}]."))
    return problems

# An arbitrary absolute path standing in for the cached dictionary. Rendering
# takes it as an argument, so no test needs a real cache or a real $HOME.
DICTIONARY = "/cache/omarchy-ime/dictionary/v0-test/cedict.txt"

# The selection an install that used to say `japanese: true` migrates to.
JAPANESE = ["rime_ice", "japanese_tw_eng", "sno_japanese"]


def render(**overrides):
    # With candidate annotation on unless a test says otherwise: most of what
    # is asserted here is the shape of the annotation block, and the switch
    # defaults to off. The default is asserted by tests/test_features.py and
    # tests/test_deploy.py against the settings module and the deployed tree.
    overrides.setdefault("annotation", True)
    return generate.render(settings.resolve(overrides), DICTIONARY)


class RenderTests(unittest.TestCase):
    def test_always_produces_the_two_customization_files(self):
        files = render()
        self.assertIn("default.custom.yaml", files)
        self.assertIn("rime_ice.custom.yaml", files)

    def test_generated_files_are_marked_as_generated(self):
        for content in render().values():
            self.assertIn("generated by ryuhzk.ime", content)

    def test_rendering_twice_is_identical(self):
        self.assertEqual(render(schemas=JAPANESE), render(schemas=JAPANESE))

    def test_japanese_schemas_appear_only_when_they_are_selected(self):
        self.assertNotIn("japanese_tw_eng", render()["default.custom.yaml"])
        self.assertIn("japanese_tw_eng", render(schemas=JAPANESE)["default.custom.yaml"])

    def test_rime_ice_is_the_default_selection(self):
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

    def test_the_hotkey_is_bound_while_composing(self):
        content = render(annotateHotkey="Control+j")["rime_ice.custom.yaml"]
        self.assertIn('accept: "Control+j"', content)
        self.assertIn("when: composing", content)

    def test_the_english_translator_is_never_patched(self):
        # Rime Ice mounts melt_eng as a secondary translator by design, and
        # librime has no list-subtraction operator to take it back out: an
        # `engine/translators/-` key fails the schema build outright. Nothing
        # here writes one.
        for content in render().values():
            self.assertNotIn("melt_eng", content)
            self.assertNotIn("engine/translators/-", content)

    def test_vertical_candidates_still_reach_the_rime_style_key(self):
        # Correct for the Rime frontends that read it - Squirrel, Weasel - and
        # kept for them. It is no longer the only place the setting lands.
        self.assertIn("horizontal: false", render()["default.custom.yaml"])
        self.assertIn("horizontal: true", render(verticalCandidates=False)["default.custom.yaml"])


def classicui(**overrides):
    """The generated classicui.conf, parsed. It has no section header."""
    rendered = generate.render_fcitx5(
        settings.resolve(overrides), "omarchy-ime")[generate.CLASSICUI_PATH]
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string("[classicui]" + chr(10) + rendered)
    return parser["classicui"]


class ClassicUiTests(unittest.TestCase):
    """The candidate window's layout belongs to Fcitx5, not to Rime.

    `verticalCandidates` used to write `style/horizontal` and stop there. Under
    Fcitx5 that key reaches nothing: classicui draws the candidate window and
    reads its own `Vertical Candidate List`, which was pinned to True in a
    payload file the setting could not touch. The switch was wired to a key with
    no reader - turning it off did nothing and looked like a bug in Rime.
    """

    def test_the_setting_drives_the_key_that_actually_acts(self):
        self.assertEqual(classicui()["Vertical Candidate List"], "True")
        self.assertEqual(
            classicui(verticalCandidates=False)["Vertical Candidate List"], "False"
        )

    def test_it_names_the_generated_theme_directory_it_was_handed(self):
        rendered = generate.render_fcitx5(settings.resolve({}), "some-theme")
        self.assertIn("Theme=some-theme", rendered[generate.CLASSICUI_PATH])
        self.assertIn("DarkTheme=some-theme", rendered[generate.CLASSICUI_PATH])

    def test_it_lands_where_fcitx5_reads_it(self):
        self.assertEqual(generate.CLASSICUI_PATH, "conf/classicui.conf")

    def test_the_font_that_covers_the_cjk_it_has_to_draw_survives(self):
        # A candidate window in a font without those glyphs draws boxes.
        self.assertIn(generate.CLASSICUI_FONT, classicui()["Font"])

    def test_the_named_font_is_one_the_machine_can_actually_have(self):
        # This wrote "HanaMinA" into every user's classicui.conf while nothing
        # installed it: not omarchy-base.packages, not packages.REQUIRED. A
        # font nobody has is a font nobody is using, whatever the file says.
        from lib import packages

        self.assertIn("noto-fonts-cjk", packages.REQUIRED)
        self.assertIn("Noto Sans CJK", generate.CLASSICUI_FONT)

    def test_it_says_it_is_generated(self):
        rendered = generate.render_fcitx5(
            settings.resolve({}), "omarchy-ime")[generate.CLASSICUI_PATH]
        self.assertTrue(rendered.startswith("#"))
        self.assertIn("generated", rendered.splitlines()[0])

    def test_rendering_it_is_pure(self):
        resolved = settings.resolve({})
        self.assertEqual(
            generate.render_fcitx5(resolved, "omarchy-ime"),
            generate.render_fcitx5(resolved, "omarchy-ime"),
        )


class PerSchemaFileTests(unittest.TestCase):
    """Every selected schema that wants the patches gets its own file.

    Rime reads `<schema_id>.custom.yaml` and nothing else. A patch written to
    `rime_ice.custom.yaml` while the user composes with `double_pinyin_flypy`
    is a file Rime never opens: the glosses, the hotkey and the traditional
    output all vanish and nothing anywhere says so.
    """

    def test_a_selected_schema_gets_a_file_of_its_own(self):
        files = render(schemas=["double_pinyin_flypy"])
        self.assertIn("double_pinyin_flypy.custom.yaml", files)

    def test_an_unselected_schema_gets_no_file(self):
        files = render(schemas=["double_pinyin_flypy"])
        self.assertNotIn("rime_ice.custom.yaml", files)

    def test_every_selected_chinese_schema_gets_its_own_file(self):
        selection = ["rime_ice", "luna_pinyin", "bopomofo", "cangjie5"]
        files = render(schemas=selection)
        for schema in selection:
            with self.subTest(schema=schema):
                self.assertIn(f"{schema}.custom.yaml", files)

    def test_the_annotation_block_reaches_a_schema_that_is_not_rime_ice(self):
        content = render(schemas=["double_pinyin_flypy"])["double_pinyin_flypy.custom.yaml"]
        document = parse_block_yaml(content)["patch"]
        self.assertEqual(document["engine/filters/+"], ["lua_filter@*annotate"])
        self.assertEqual(document["switches/+"][0]["name"], "annotate")
        self.assertEqual(document["key_binder/bindings/+"][0]["toggle"], "annotate")
        self.assertIn(DICTIONARY, document["annotate/dictionary"])

    def test_every_selected_schema_carries_the_same_annotation_block(self):
        selection = ["rime_ice", "bopomofo", "terra_pinyin"]
        files = render(schemas=selection)
        blocks = {
            schema: {
                key: value for key, value in
                parse_block_yaml(files[f"{schema}.custom.yaml"])["patch"].items()
                if key.startswith("annotate") or key == "engine/filters/+"
            }
            for schema in selection
        }
        first = blocks[selection[0]]
        for schema in selection[1:]:
            self.assertEqual(blocks[schema], first, schema)


class JapaneseSchemaTests(unittest.TestCase):
    """The Japanese schemas are deliberately left alone.

    Neither of them has a `traditionalize` component - `japanese_tw_eng` does
    its own s2twp through `simplifier@s2tw_converter`, scoped to the Rime Ice
    pinyin tag, and `sno_japanese` composes no Chinese at all. And a CC-CEDICT
    Chinese-English gloss on a Japanese candidate is not a translation of it:
    both schemas already fill the comment with a Japanese-appropriate reading
    or gloss of their own, which the annotation filter would fight over.
    """

    def test_neither_japanese_schema_gets_a_custom_file(self):
        files = render(schemas=JAPANESE)
        self.assertNotIn("japanese_tw_eng.custom.yaml", files)
        self.assertNotIn("sno_japanese.custom.yaml", files)

    def test_the_chinese_schema_beside_them_still_gets_one(self):
        self.assertIn("rime_ice.custom.yaml", render(schemas=JAPANESE))


class SchemaSetTests(unittest.TestCase):
    """The hand-written schema sets have to stay in step with the catalogue.

    RIME_ICE_FAMILY names ids by hand because it records a fact about those
    schemas' contents rather than about the picker. An id that stops being in
    the catalogue - renamed upstream, dropped from the family - would otherwise
    sit here matching nothing, which is a patch that quietly stops being written.
    """

    def test_every_named_family_member_is_a_schema_the_picker_offers(self):
        unknown = generate.RIME_ICE_FAMILY - set(settings.SCHEMA_IDS)
        self.assertEqual(unknown, set())

    def test_the_family_is_all_chinese(self):
        self.assertTrue(generate.RIME_ICE_FAMILY <= set(settings.CHINESE_SCHEMAS))

    def test_the_annotated_set_is_exactly_the_chinese_schemas(self):
        self.assertEqual(features.ANNOTATION.patched, set(settings.CHINESE_SCHEMAS))

    def test_no_japanese_schema_is_annotated(self):
        for schema in ("japanese_tw_eng", "sno_japanese"):
            with self.subTest(schema=schema):
                self.assertNotIn(schema, features.ANNOTATION.patched)


class TraditionalizeReachTests(unittest.TestCase):
    """s2twp is written only where a `traditionalize` component reads it.

    Checked against /usr/share/rime-data: `rime_ice` and the seven
    `double_pinyin*` schemas mount `simplifier@traditionalize`; no other schema
    in the catalogue does. On the rest the key would sit in the built schema
    doing nothing, which is the failure mode this change exists to remove.
    """

    def test_the_rime_ice_family_carries_the_patch(self):
        for schema in ("rime_ice", "double_pinyin_flypy", "double_pinyin_abc"):
            with self.subTest(schema=schema):
                content = render(schemas=[schema])[f"{schema}.custom.yaml"]
                self.assertIn("s2twp.json", content)

    def test_a_schema_without_the_component_gets_no_dead_key(self):
        for schema in ("luna_pinyin", "bopomofo", "cangjie5", "terra_pinyin"):
            with self.subTest(schema=schema):
                content = render(schemas=["rime_ice", schema])[f"{schema}.custom.yaml"]
                self.assertNotIn("s2twp", content)
                self.assertNotIn("traditionalize", content)

    def test_turning_it_off_removes_it_from_the_family_too(self):
        content = render(schemas=["double_pinyin_flypy"], traditionalize=False)
        self.assertNotIn("s2twp", content["double_pinyin_flypy.custom.yaml"])


class EmptyPatchTests(unittest.TestCase):
    """A file with nothing in it is not written at all.

    An empty `patch:` is harmless to Rime, but it is still a file in the
    manifest that uninstall has to carry and that says nothing. Omitting it
    also means a setting turning off removes the file rather than blanking it.
    """

    def test_a_schema_with_nothing_to_patch_gets_no_file(self):
        files = render(
            schemas=["rime_ice", "bopomofo"],
            annotateChinese=False, annotateEnglish=False,
        )
        self.assertNotIn("bopomofo.custom.yaml", files)

    def test_the_family_still_gets_one_for_the_traditionalize_key_alone(self):
        files = render(
            schemas=["rime_ice"], annotateChinese=False, annotateEnglish=False,
        )
        self.assertIn("s2twp.json", files["rime_ice.custom.yaml"])

    def test_nothing_at_all_leaves_only_the_default_file(self):
        files = render(
            schemas=["bopomofo"], annotateChinese=False, annotateEnglish=False,
            traditionalize=False,
        )
        self.assertEqual(sorted(files), ["default.custom.yaml"])


class NoticeTests(unittest.TestCase):
    """A setting that cannot reach a selected schema is said out loud.

    Silence is the failure this whole change is about. When traditional output
    has no component to act on in a schema the user picked, the deploy says so
    rather than letting the setting look applied.
    """

    def test_nothing_is_said_when_every_selection_can_honour_the_settings(self):
        self.assertEqual(generate.notices(settings.resolve({})), [])

    def test_traditional_output_reports_the_schemas_it_cannot_reach(self):
        messages = generate.notices(
            settings.resolve({"schemas": ["rime_ice", "bopomofo", "cangjie5"]})
        )
        joined = " ".join(messages)
        self.assertIn("bopomofo", joined)
        self.assertIn("cangjie5", joined)
        self.assertNotIn("rime_ice", joined)

    def test_nothing_is_said_when_the_setting_is_off(self):
        self.assertEqual(
            generate.notices(settings.resolve({
                "schemas": ["rime_ice", "bopomofo"],
                "traditionalize": False,
            })),
            [],
        )

    def test_the_japanese_schemas_are_never_named(self):
        # They are out of scope by design, not by accident, so they are not
        # reported as something a setting failed to reach.
        messages = generate.notices(settings.resolve({
            "schemas": JAPANESE, "traditionalize": True,
        }))
        self.assertNotIn("sno_japanese", " ".join(messages))
        self.assertNotIn("japanese_tw_eng", " ".join(messages))


class SwitchTests(unittest.TestCase):
    """One switch, one binding.

    Rime's key_binder applies the first binding that matches a key and stops, so
    a second binding on the same key is dead - and dead quietly, because the key
    still appears to work through the first one.
    """

    def bindings(self, content: str) -> list[str]:
        return [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("- { accept:")
        ]

    def switches(self, content: str) -> list[str]:
        return [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("- { name:")
        ]

    def test_the_hotkey_gets_exactly_one_binding(self):
        content = render(annotateHotkey="Control+j")["rime_ice.custom.yaml"]
        bindings = self.bindings(content)
        self.assertEqual(len(bindings), 1, bindings)
        # Not merely one binding line: the key is named exactly once in the
        # whole file, so no second binding can hide in another shape.
        self.assertEqual(content.count("Control+j"), 1)
        self.assertIn('accept: "Control+j"', bindings[0])
        self.assertIn("toggle: annotate", bindings[0])
        self.assertIn("when: composing", bindings[0])

    def test_exactly_one_switch_is_declared_and_it_is_annotate(self):
        switches = self.switches(render()["rime_ice.custom.yaml"])
        self.assertEqual(len(switches), 1, switches)
        self.assertIn("name: annotate", switches[0])

    def test_a_single_direction_still_gets_one_switch_and_one_binding(self):
        # The directions are deploy-time settings, so neither of them adds a
        # switch of its own however they are combined.
        for overrides in ({"annotateChinese": False}, {"annotateEnglish": False}):
            with self.subTest(**overrides):
                content = render(**overrides)["rime_ice.custom.yaml"]
                self.assertEqual(len(self.switches(content)), 1)
                self.assertEqual(len(self.bindings(content)), 1)

    def test_no_switch_and_no_binding_when_annotation_is_off(self):
        content = render(annotateChinese=False, annotateEnglish=False)["rime_ice.custom.yaml"]
        self.assertEqual(self.switches(content), [])
        self.assertEqual(self.bindings(content), [])

    def test_no_per_direction_switch_is_written(self):
        # The superseded model had one switch per direction, both bound to the
        # same key. Nothing may reintroduce them.
        content = render()["rime_ice.custom.yaml"]
        self.assertNotIn("english_comment", content)
        self.assertNotIn("chinese_comment", content)


class AnnotationConfigTests(unittest.TestCase):
    """The Lua filter reads its settings from the schema, so they must be written."""

    def annotate_lines(self, content: str) -> dict[str, str]:
        found = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("annotate/"):
                key, _, value = stripped.partition(":")
                found[key] = value.strip()
        return found

    def test_every_key_the_filter_reads_is_emitted(self):
        keys = self.annotate_lines(render()["rime_ice.custom.yaml"])
        self.assertEqual(
            sorted(keys),
            [
                "annotate/chinese",
                "annotate/dictionary",
                "annotate/english",
                "annotate/show_pinyin",
            ],
        )

    def test_the_direction_keys_follow_their_settings(self):
        keys = self.annotate_lines(render(annotateChinese=False)["rime_ice.custom.yaml"])
        self.assertEqual(keys["annotate/chinese"], "false")
        self.assertEqual(keys["annotate/english"], "true")
        keys = self.annotate_lines(render(annotateEnglish=False)["rime_ice.custom.yaml"])
        self.assertEqual(keys["annotate/chinese"], "true")
        self.assertEqual(keys["annotate/english"], "false")

    def test_both_directions_on_is_the_default(self):
        keys = self.annotate_lines(render()["rime_ice.custom.yaml"])
        self.assertEqual(keys["annotate/chinese"], "true")
        self.assertEqual(keys["annotate/english"], "true")

    def test_the_dictionary_key_carries_the_path_it_was_given(self):
        keys = self.annotate_lines(render()["rime_ice.custom.yaml"])
        self.assertIn(DICTIONARY, keys["annotate/dictionary"])

    def test_a_different_path_is_carried_through_unchanged(self):
        content = generate.render(settings.resolve({"annotation": True}),
                                  "/elsewhere/cedict.txt")
        keys = self.annotate_lines(content["rime_ice.custom.yaml"])
        self.assertIn("/elsewhere/cedict.txt", keys["annotate/dictionary"])
        self.assertNotIn(DICTIONARY, content["rime_ice.custom.yaml"])

    def test_the_default_path_is_absolute(self):
        keys = self.annotate_lines(
            generate.render(settings.resolve({"annotation": True}))["rime_ice.custom.yaml"])
        self.assertTrue(Path(keys["annotate/dictionary"].strip('"')).is_absolute())

    def test_show_pinyin_still_follows_its_setting(self):
        keys = self.annotate_lines(render(annotateShowPinyin=False)["rime_ice.custom.yaml"])
        self.assertEqual(keys["annotate/show_pinyin"], "false")

    def test_no_annotate_key_is_written_when_annotation_is_off(self):
        content = render(annotateChinese=False, annotateEnglish=False)["rime_ice.custom.yaml"]
        self.assertEqual(self.annotate_lines(content), {})

    def test_rendering_twice_with_the_same_path_is_identical(self):
        self.assertEqual(render(), render())


class BlockYamlParserTests(unittest.TestCase):
    """The parser is the test's instrument, so it is measured first."""

    def test_a_block_sequence_under_a_key_is_a_list(self):
        document = parse_block_yaml('a:\n  "b/+":\n    - one\n    - two\n')
        self.assertEqual(document, {"a": {"b/+": ["one", "two"]}})

    def test_a_scalar_on_the_key_line_stays_a_scalar(self):
        document = parse_block_yaml('a:\n  "b/+": one\n')
        self.assertEqual(document, {"a": {"b/+": "one"}})

    def test_a_quoted_scalar_is_not_mistaken_for_a_list(self):
        document = parse_block_yaml('a:\n  "b/+": "lua_filter@*annotate"\n')
        self.assertEqual(document["a"]["b/+"], "lua_filter@*annotate")

    def test_flow_collections_survive_their_inner_commas(self):
        document = parse_block_yaml(
            "a:\n  - { name: annotate, reset: 1, states: [ gloss off, gloss on ] }\n"
        )
        self.assertEqual(document["a"], [
            {"name": "annotate", "reset": 1, "states": ["gloss off", "gloss on"]},
        ])

    def test_a_value_may_contain_a_colon(self):
        self.assertEqual(
            parse_block_yaml("__include: rime_ice_suggestion:/\n"),
            {"__include": "rime_ice_suggestion:/"},
        )

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(parse_block_yaml("# note\n\na: 1\n"), {"a": 1})

    def test_an_unreadable_shape_is_refused_rather_than_guessed(self):
        with self.assertRaises(YamlError):
            parse_block_yaml("a\n")


class MergeKeyCheckTests(unittest.TestCase):
    """The check itself: it has to fail on the bug it exists to catch."""

    def test_a_scalar_merge_key_is_reported(self):
        # The exact configuration that took Chinese input down.
        document = parse_block_yaml(
            'patch:\n  "engine/filters/+": "lua_filter@*annotate"\n'
        )
        self.assertEqual(merge_key_violations(document), ["patch.engine/filters/+"])

    def test_a_list_merge_key_is_accepted(self):
        document = parse_block_yaml(
            'patch:\n  "engine/filters/+":\n    - "lua_filter@*annotate"\n'
        )
        self.assertEqual(merge_key_violations(document), [])

    def test_a_removal_key_is_held_to_the_same_rule(self):
        document = parse_block_yaml('patch:\n  "engine/translators/-": melt_eng\n')
        self.assertEqual(merge_key_violations(document), ["patch.engine/translators/-"])

    def test_an_empty_merge_key_is_reported(self):
        document = parse_block_yaml('patch:\n  "engine/filters/+":\n')
        self.assertEqual(merge_key_violations(document), ["patch.engine/filters/+"])

    def test_a_nested_merge_key_is_reached(self):
        document = parse_block_yaml("a:\n  b:\n    c/+: scalar\n")
        self.assertEqual(merge_key_violations(document), ["a.b.c/+"])

    def test_a_plain_key_is_left_alone(self):
        document = parse_block_yaml("patch:\n  annotate/chinese: true\n")
        self.assertEqual(merge_key_violations(document), [])


# Every configuration that changes the shape of the emitted patch. Rendering is
# cheap, so the structural check runs over all of them rather than the default.
SHAPES = [
    {},
    {"schemas": JAPANESE},
    {"schemas": ["double_pinyin_flypy"]},
    {"schemas": ["rime_ice", "bopomofo", "cangjie5", "terra_pinyin"]},
    {"traditionalize": False},
    {"annotateChinese": False},
    {"annotateEnglish": False},
    {"annotateChinese": False, "annotateEnglish": False},
    {"schemas": JAPANESE, "traditionalize": False},
    {"annotateHotkey": "Control+j", "pageSize": 1, "verticalCandidates": False},
]


class GeneratedStructureTests(unittest.TestCase):
    """Parse what we write, instead of searching it for familiar substrings.

    A substring assertion cannot tell `key: value` from `key:` followed by
    `- value`: both contain the value. Rime can, and rejects the first.
    """

    def test_every_generated_file_parses(self):
        for overrides in SHAPES:
            for name, content in render(**overrides).items():
                with self.subTest(file=name, **overrides):
                    self.assertIsInstance(parse_block_yaml(content), dict)

    def test_every_merge_key_carries_a_list(self):
        for overrides in SHAPES:
            for name, content in render(**overrides).items():
                with self.subTest(file=name, **overrides):
                    document = parse_block_yaml(content)
                    self.assertEqual(merge_key_violations(document), [])

    def test_both_files_are_a_patch(self):
        for name, content in render().items():
            with self.subTest(file=name):
                self.assertIn("patch", parse_block_yaml(content))

    def test_the_annotation_filter_is_one_entry_of_a_list(self):
        document = parse_block_yaml(render()["rime_ice.custom.yaml"])
        self.assertEqual(
            document["patch"]["engine/filters/+"], ["lua_filter@*annotate"]
        )

    def test_the_schema_list_is_a_list_of_schema_mappings(self):
        document = parse_block_yaml(render(schemas=JAPANESE)["default.custom.yaml"])
        self.assertEqual(
            document["patch"]["__patch"]["schema_list"],
            [
                {"schema": "japanese_tw_eng"},
                {"schema": "rime_ice"},
                {"schema": "sno_japanese"},
            ],
        )

    def test_the_switch_and_the_binding_are_list_entries(self):
        document = parse_block_yaml(render()["rime_ice.custom.yaml"])["patch"]
        self.assertEqual(document["switches/+"][0]["name"], "annotate")
        self.assertEqual(document["key_binder/bindings/+"][0]["accept"], "Control+e")


class SchemaListTests(unittest.TestCase):
    """What ends up in Rime's schema_list, and in what order."""

    def schema_list(self, **overrides):
        document = parse_block_yaml(render(**overrides)["default.custom.yaml"])
        return [entry["schema"] for entry in document["patch"]["__patch"]["schema_list"]]

    def test_the_default_install_composes_with_rime_ice_alone(self):
        self.assertEqual(self.schema_list(), ["rime_ice"])

    def test_a_schema_from_each_family_reaches_the_list(self):
        for schema_id in ("luna_pinyin", "double_pinyin_flypy", "bopomofo",
                          "cangjie5", "terra_pinyin", "sno_japanese"):
            with self.subTest(schema=schema_id):
                self.assertIn(schema_id, self.schema_list(schemas=["rime_ice", schema_id]))

    def test_a_double_pinyin_schema_needs_no_full_pinyin_schema_beside_it(self):
        # double_pinyin_flypy mounts the shared rime_ice *dictionary*, not the
        # rime_ice *schema*, so it stands on its own in schema_list.
        self.assertEqual(self.schema_list(schemas=["double_pinyin_flypy"]),
                         ["double_pinyin_flypy"])

    def test_the_selection_keeps_catalogue_order(self):
        self.assertEqual(
            self.schema_list(schemas=["cangjie5", "bopomofo", "rime_ice"]),
            ["rime_ice", "bopomofo", "cangjie5"],
        )

    def test_japanese_brackets_the_chinese_schemas(self):
        self.assertEqual(
            self.schema_list(schemas=["rime_ice", "bopomofo", "japanese_tw_eng", "sno_japanese"]),
            ["japanese_tw_eng", "rime_ice", "bopomofo", "sno_japanese"],
        )

    def test_each_japanese_schema_takes_its_own_end_of_the_list(self):
        self.assertEqual(self.schema_list(schemas=["rime_ice", "japanese_tw_eng"]),
                         ["japanese_tw_eng", "rime_ice"])
        self.assertEqual(self.schema_list(schemas=["rime_ice", "sno_japanese"]),
                         ["rime_ice", "sno_japanese"])

    def test_no_japanese_selected_leaves_the_chinese_schemas_alone(self):
        self.assertEqual(self.schema_list(schemas=["rime_ice", "luna_pinyin"]),
                         ["rime_ice", "luna_pinyin"])

    def test_an_empty_selection_still_leaves_rime_something_to_compose_with(self):
        # An empty schema_list is a Rime with no schema at all: the panel and
        # the CLI refuse to write one (see test_settings), and a hand-edited
        # shell.json that manages it falls back rather than costing the user
        # their input method. Both layers are checked - resolve's repair, and
        # the generator's own fallback when it is handed the empty list raw.
        self.assertEqual(self.schema_list(schemas=[]), ["rime_ice"])
        raw = dict(settings.resolve({}), schemas=[])
        document = parse_block_yaml(generate.render(raw, DICTIONARY)["default.custom.yaml"])
        self.assertEqual(
            [entry["schema"] for entry in document["patch"]["__patch"]["schema_list"]],
            ["rime_ice"],
        )

    def test_a_japanese_only_selection_keeps_a_chinese_schema(self):
        self.assertEqual(self.schema_list(schemas=["japanese_tw_eng", "sno_japanese"]),
                         ["japanese_tw_eng", "rime_ice", "sno_japanese"])

    def test_an_unknown_id_never_reaches_the_schema_list(self):
        # A typo here is a schema Rime cannot build, which takes down every
        # schema in the list with it.
        self.assertEqual(self.schema_list(schemas=["rime_ice", "rime_icc"]), ["rime_ice"])

    def test_an_unknown_id_is_dropped_even_when_settings_are_handed_over_raw(self):
        rendered = generate.render(
            dict(settings.resolve({}), schemas=["rime_ice", "rime_icc"]), DICTIONARY
        )
        self.assertNotIn("rime_icc", rendered["default.custom.yaml"])

    def test_a_missing_selection_falls_back_rather_than_raising(self):
        rendered = generate.render(
            {key: value for key, value in settings.resolve({}).items() if key != "schemas"},
            DICTIONARY,
        )
        self.assertIn("rime_ice", rendered["default.custom.yaml"])


class EnvironmentTests(unittest.TestCase):
    """The QT_IM_MODULE override the plugin now owns.

    Fcitx5 reaches Qt twice on Wayland: through the native text-input protocol
    and, when QT_IM_MODULE is set, through the older DBus input-context path as
    well. Both live at once is what made typing into a layer-shell surface work
    only sometimes, so this file is the packaged one with that variable removed.
    """

    def packaged(self, *lines) -> Path:
        directory = Path(self._tmp.name)
        path = directory / "packaged.conf"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stock = self.packaged(
            "INPUT_METHOD=fcitx",
            "QT_IM_MODULE=fcitx",
            "XMODIFIERS=@im=fcitx",
            "SDL_IM_MODULE=fcitx",
        )

    def rendered(self, packaged=None) -> str:
        files = generate.render_environment(packaged or self.stock)
        return files[generate.ENVIRONMENT_PATH]

    def assignments(self, text: str) -> dict:
        return dict(
            line.split("=", 1)
            for line in text.splitlines()
            if line and not line.startswith("#")
        )

    def test_it_installs_under_the_packaged_files_own_name(self):
        # The override works by filename: same name, a directory systemd reads
        # later. A different name would leave both files in effect.
        self.assertEqual(generate.ENVIRONMENT_PATH, "10-omarchy-fcitx.conf")
        self.assertEqual(generate.PACKAGED_ENVIRONMENT.name, generate.ENVIRONMENT_PATH)

    def rendered_off(self, packaged=None) -> str:
        files = generate.render_environment(packaged or self.stock,
                                            set_qt_im_module=False)
        return files[generate.ENVIRONMENT_PATH]

    def test_qt_im_module_is_set_by_default(self):
        # The default serves XWayland Qt apps such as WeChat, which cannot use
        # the native protocol and get no input method without it.
        self.assertEqual(
            self.assignments(self.rendered())["QT_IM_MODULE"], "fcitx")

    def test_qt_im_module_is_dropped_when_the_setting_is_off(self):
        self.assertNotIn("QT_IM_MODULE", self.assignments(self.rendered_off()))

    def test_it_is_added_when_wanted_even_if_the_packaged_file_lacks_it(self):
        packaged = self.packaged("INPUT_METHOD=fcitx", "XMODIFIERS=@im=fcitx")
        self.assertEqual(
            self.assignments(self.rendered(packaged))["QT_IM_MODULE"], "fcitx")

    def test_the_other_packaged_variables_survive(self):
        self.assertEqual(
            self.assignments(self.rendered_off()),
            {
                "INPUT_METHOD": "fcitx",
                "XMODIFIERS": "@im=fcitx",
                "SDL_IM_MODULE": "fcitx",
            },
        )

    def test_a_variable_added_upstream_is_carried_rather_than_dropped(self):
        # Our file replaces the packaged one whole, so anything it gains and
        # this one does not copy is a variable the user silently loses.
        packaged = self.packaged(
            "INPUT_METHOD=fcitx",
            "QT_IM_MODULE=fcitx",
            "GLFW_IM_MODULE=ibus",
            "XMODIFIERS=@im=fcitx",
        )
        self.assertEqual(
            self.assignments(self.rendered_off(packaged)),
            {
                "INPUT_METHOD": "fcitx",
                "GLFW_IM_MODULE": "ibus",
                "XMODIFIERS": "@im=fcitx",
            },
        )

    def test_comments_and_blank_lines_in_the_packaged_file_are_not_copied(self):
        packaged = self.packaged("# packaged header", "", "INPUT_METHOD=fcitx")
        self.assertEqual(self.assignments(self.rendered_off(packaged)),
                         {"INPUT_METHOD": "fcitx"})

    def test_a_missing_packaged_file_falls_back_to_the_known_variables(self):
        missing = Path(self._tmp.name) / "not-here.conf"
        self.assertEqual(
            self.assignments(self.rendered_off(missing)),
            {
                "INPUT_METHOD": "fcitx",
                "XMODIFIERS": "@im=fcitx",
                "SDL_IM_MODULE": "fcitx",
            },
        )

    def test_the_diagnosis_that_produced_the_file_survives_in_it(self):
        # The reason is the whole value of the file: without it the next reader
        # sees a variable set or unset for no stated reason and changes it.
        text = self.rendered()
        self.assertIn("text-input-v3", text)
        self.assertIn("QT_IM_MODULE=fcitx", text)
        self.assertIn("focus race", text)
        self.assertIn("layer-shell", text)
        self.assertIn("XWayland", text)

    def test_the_active_choice_is_recorded_in_the_file(self):
        # A reader must be able to tell which way this deploy set it without
        # running the plugin.
        self.assertIn("qtImModule is ON", self.rendered())
        self.assertIn("qtImModule is OFF", self.rendered_off())

    def test_every_explanatory_line_is_a_comment(self):
        for line in self.rendered().splitlines():
            if line and "=" in line and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z_][A-Za-z0-9_]*=")

    def test_it_says_it_is_generated(self):
        self.assertTrue(self.rendered().startswith("#"))
        self.assertIn("generated", self.rendered().splitlines()[0])

    def test_it_says_when_it_takes_effect(self):
        # systemd reads environment.d at session start, so a deploy changes
        # nothing about the session the user is deploying from.
        self.assertIn("next login", self.rendered())

    def test_it_says_how_to_get_the_packaged_behaviour_back(self):
        self.assertIn("uninstall", self.rendered())

    def test_rendering_it_is_pure(self):
        self.assertEqual(
            generate.render_environment(self.stock),
            generate.render_environment(self.stock),
        )


class HostileHotkeyTests(unittest.TestCase):
    """The hotkey is text out of a field, so it is data and not syntax.

    `settings.validate` accepts any non-empty string, and this used to be
    interpolated into the YAML raw. A single `"` produced `accept: "Control+""`,
    which fails the build of every selected Chinese schema; a newline could open
    a patch key of its own inside a file Rime applies without asking.
    """

    # A quote to close the scalar, a brace to close the flow mapping, and a
    # newline carrying a key that would delete every binding in the schema.
    HOSTILE = 'Control+" }\n  key_binder/bindings/-: all'

    def rendered(self, hotkey):
        return render(annotateHotkey=hotkey)["rime_ice.custom.yaml"]

    def test_a_quote_does_not_close_the_scalar(self):
        self.assertNotIn('accept: "Control+""', self.rendered('Control+"'))

    def test_a_quote_is_escaped_inside_the_scalar(self):
        self.assertIn('accept: "Control+\\""', self.rendered('Control+"'))

    def test_a_hostile_hotkey_still_parses_as_the_yaml_rime_reads(self):
        document = parse_block_yaml(self.rendered(self.HOSTILE))
        self.assertEqual(merge_key_violations(document), [])

    def test_a_hostile_hotkey_injects_no_key_of_its_own(self):
        patch = parse_block_yaml(self.rendered(self.HOSTILE))["patch"]
        self.assertNotIn("key_binder/bindings/-", patch)

    def test_the_whole_hotkey_stays_on_one_line(self):
        lines = [line for line in self.rendered(self.HOSTILE).splitlines()
                 if "accept:" in line]
        self.assertEqual(len(lines), 1, lines)

    def test_a_backslash_survives_as_a_backslash(self):
        self.assertIn('accept: "Control+\\\\"', self.rendered("Control+\\"))


# A profile from a machine that is not a fresh US-keyboard install: a German
# layout, a group the user renamed, and an input method they added themselves.
GERMAN_PROFILE = """\
[Groups/0]
Name=Default
Default Layout=de
DefaultIM=keyboard-de

[Groups/0/Items/0]
Name=keyboard-de
Layout=

[Groups/0/Items/1]
Name=mozc
Layout=

[GroupOrder]
0=Default
"""


class ProfileTests(unittest.TestCase):
    """The Fcitx5 profile belongs to the user, so it is merged and not written.

    It used to ship as a payload file and be copied straight over the top,
    which pinned `Default Layout=us` and `keyboard-us` on everybody and threw
    away every input method they had configured.
    """

    def section(self, rendered, name):
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        parser.read_string(rendered)
        return parser[name]

    def items(self, rendered):
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        parser.read_string(rendered)
        return [parser[section]["Name"] for section in parser.sections()
                if section.startswith("Groups/0/Items/")]

    def test_an_existing_layout_survives(self):
        rendered = generate.render_profile(GERMAN_PROFILE)
        self.assertEqual(self.section(rendered, "Groups/0")["Default Layout"], "de")

    def test_an_existing_keyboard_item_survives(self):
        self.assertIn("keyboard-de", self.items(generate.render_profile(GERMAN_PROFILE)))
        self.assertNotIn("keyboard-us",
                         self.items(generate.render_profile(GERMAN_PROFILE)))

    def test_an_input_method_the_user_added_survives(self):
        self.assertIn("mozc", self.items(generate.render_profile(GERMAN_PROFILE)))

    def test_rime_is_added(self):
        self.assertIn("rime", self.items(generate.render_profile(GERMAN_PROFILE)))

    def test_rime_becomes_the_default_input_method(self):
        rendered = generate.render_profile(GERMAN_PROFILE)
        self.assertEqual(self.section(rendered, "Groups/0")["DefaultIM"], "rime")

    def test_merging_twice_adds_rime_once(self):
        once = generate.render_profile(GERMAN_PROFILE)
        twice = generate.render_profile(once)
        self.assertEqual(self.items(twice).count("rime"), 1)
        self.assertEqual(self.items(once), self.items(twice))

    def test_the_group_name_the_user_chose_survives(self):
        renamed = GERMAN_PROFILE.replace("Name=Default", "Name=Arbeit", 1)
        rendered = generate.render_profile(renamed)
        self.assertEqual(self.section(rendered, "Groups/0")["Name"], "Arbeit")

    def test_with_no_profile_the_system_layout_is_used(self):
        rendered = generate.render_profile(None, layout="fr")
        self.assertEqual(self.section(rendered, "Groups/0")["Default Layout"], "fr")
        self.assertEqual(self.items(rendered), ["keyboard-fr", "rime"])

    def test_an_unparseable_profile_still_yields_a_working_one(self):
        rendered = generate.render_profile("this is not a profile", layout="us")
        self.assertIn("rime", self.items(rendered))

    def test_it_lands_where_fcitx5_reads_it(self):
        self.assertEqual(generate.PROFILE_PATH, "profile")


class SystemLayoutTests(unittest.TestCase):
    """Where a from-scratch profile gets its keyboard layout."""

    def layout(self, text):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "vconsole.conf"
            path.write_text(text, encoding="utf-8")
            return generate.system_layout(path)

    def test_it_reads_the_same_file_omarchy_reads(self):
        self.assertEqual(self.layout('KEYMAP=de\nXKBLAYOUT="de"\n'), "de")

    def test_only_the_first_layout_of_a_list_is_taken(self):
        # A Fcitx5 group carries one layout, and Omarchy prepends `us` to a
        # non-latin one - so the first entry is the one that leads.
        self.assertEqual(self.layout('XKBLAYOUT="us,ru"\n'), "us")

    def test_a_file_without_the_key_falls_back(self):
        self.assertEqual(self.layout("KEYMAP=us\n"), generate.DEFAULT_LAYOUT)

    def test_a_missing_file_falls_back(self):
        self.assertEqual(
            generate.system_layout(Path("/nonexistent/vconsole.conf")),
            generate.DEFAULT_LAYOUT,
        )


if __name__ == "__main__":
    unittest.main()
