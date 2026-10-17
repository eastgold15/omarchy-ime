"""lib/features.py is the one place a feature says what it is.

Everything a deploy needs to know about annotation or Japanese input - which
payload group, which pinned sources, which settings, which schemas get a patch
- used to be a table in whichever module happened to act on it. These tests
hold the new arrangement to its promise: every table the other modules consult
is derived from the feature records, nothing else in `lib/` names a feature's
schemas or settings by hand, and the files a deploy renders are byte for byte
what they were before the tables were gathered.
"""
import ast
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import assets, deploy, features, generate, settings, theme

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "payload"

# Where the golden record of the rendered files lives. It pins the sha256 of
# every file `generate` renders for each configuration below, plus the facts a
# deploy derives from the settings, and it was written from the code as it
# stood before lib/features.py existed. Regenerate it on purpose, never to make
# a red test green:
#
#     python3 -c "import tests.test_features as t; t.regenerate()"
FIXTURE = ROOT / "tests" / "fixtures" / "rendered.sha256.json"

# The dictionary path baked into the schema files. Fixed, so the hashes are.
DICTIONARY = "/cache/omarchy.ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt"

# Representative configurations. `annotation: True` is asked for explicitly:
# these hashes describe the rendering with glosses on, and the master switch
# that arrived after them defaults to off.
CASES = {
    "all-schemas-annotation-on": {"schemas": list(settings.SCHEMA_IDS),
                                  "annotation": True},
    "rime-ice-only": {"annotation": True},
    "bopomofo-only": {"schemas": ["bopomofo"], "annotation": True},
    "japanese-only": {"schemas": ["japanese_tw_eng", "sno_japanese"],
                      "annotation": True},
    "directions-off": {"annotation": True, "annotateChinese": False,
                       "annotateEnglish": False},
    "one-direction-quoted-hotkey": {"annotation": True, "annotateEnglish": False,
                                    "annotateHotkey": 'Control+"',
                                    "traditionalize": False, "pageSize": 5},
}


def _wants_dictionary(resolved: dict) -> bool:
    """Whether resolving the dictionary for these settings reaches for it."""
    calls = []

    def fetcher(url, destination):
        calls.append(url)
        raise RuntimeError("no network in a test")

    with TemporaryDirectory() as cache:
        deploy.resolve_dictionary(resolved, Path(cache), fetcher=fetcher)
    return bool(calls)


def snapshot() -> dict:
    """What the fixture records, computed from the code as it is now."""
    record = {}
    for name, overrides in CASES.items():
        resolved = settings.resolve(overrides)
        files = {}
        for relative, text in generate.render(resolved, DICTIONARY).items():
            files[f"rime/{relative}"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for relative, text in generate.render_fcitx5(resolved, theme.DIRECTORY).items():
            files[f"fcitx5/{relative}"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        record[name] = {
            "files": files,
            "active_groups": deploy.active_groups(resolved),
            "needed_sources": deploy.needed_sources(resolved),
            "wants_dictionary": _wants_dictionary(resolved),
            "notices": generate.notices(resolved),
        }
    return record


def regenerate() -> None:
    FIXTURE.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")


class RenderedFilesAreUnchangedTests(unittest.TestCase):
    """The refactor moved facts, not bytes."""

    def test_every_case_renders_exactly_what_the_fixture_records(self):
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actual = snapshot()
        self.assertEqual(set(actual), set(expected))
        for name in expected:
            with self.subTest(case=name):
                self.assertEqual(actual[name]["files"], expected[name]["files"])
                for fact in ("active_groups", "needed_sources",
                             "wants_dictionary", "notices"):
                    self.assertEqual(actual[name][fact], expected[name][fact], fact)


class FeatureRecordTests(unittest.TestCase):
    """Each record only names things that exist."""

    def test_the_features_are_declared(self):
        self.assertEqual([feature.id for feature in features.FEATURES],
                         ["annotation", "japanese", "cells"])

    def test_ids_are_unique(self):
        ids = [feature.id for feature in features.FEATURES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_group_is_a_payload_directory_with_a_target(self):
        for feature in features.FEATURES:
            for group in feature.groups:
                with self.subTest(feature=feature.id, group=group):
                    self.assertTrue((PAYLOAD / group).is_dir())
                    self.assertIn(group, deploy.PAYLOAD_GROUPS)

    def test_no_group_is_claimed_twice(self):
        owners = [group for feature in features.FEATURES for group in feature.groups]
        self.assertEqual(len(owners), len(set(owners)))

    def test_every_source_is_one_assets_knows_how_to_fetch(self):
        for feature in features.FEATURES:
            for schema, names in feature.sources.items():
                with self.subTest(feature=feature.id, schema=schema):
                    self.assertIn(schema, feature.schemas)
                    for name in names:
                        self.assertIn(name, assets.SOURCES)

    def test_every_schema_is_in_the_catalogue(self):
        for feature in features.FEATURES:
            for schema in (*feature.schemas, *feature.patched):
                with self.subTest(feature=feature.id, schema=schema):
                    self.assertIn(schema, settings.SCHEMA_IDS)

    def test_every_owned_setting_exists_and_belongs_to_one_feature(self):
        owned = [key for feature in features.FEATURES for key in feature.settings]
        self.assertEqual(len(owned), len(set(owned)))
        for key in owned:
            self.assertIn(key, settings.DEFAULTS)

    def test_a_feature_switched_by_a_setting_owns_that_setting(self):
        for feature in features.FEATURES:
            if feature.setting is not None:
                with self.subTest(feature=feature.id):
                    self.assertIn(feature.setting, feature.settings)
                    # Usually a boolean switch; a list setting is its own
                    # switch - `dictionaries` is on while it names a cell.
                    self.assertIn(settings.DEFAULTS[feature.setting][1],
                                  (bool, list))

    def test_a_feature_that_patches_schemas_has_a_renderer_and_vice_versa(self):
        for feature in features.FEATURES:
            with self.subTest(feature=feature.id):
                self.assertEqual(bool(feature.patched), feature.patch is not None)

    def test_the_japanese_feature_is_the_catalogues_japanese_family(self):
        family = tuple(schema for schema in settings.SCHEMA_IDS
                       if settings.SCHEMA_FAMILY[schema] == "日语")
        self.assertEqual(features.JAPANESE.schemas, family)
        self.assertEqual(set(features.JAPANESE.schemas), settings.NON_CHINESE_SCHEMAS)

    def test_annotation_patches_exactly_the_chinese_schemas(self):
        self.assertEqual(features.ANNOTATION.patched, frozenset(settings.CHINESE_SCHEMAS))

    def test_the_docstring_says_what_adding_translation_takes(self):
        self.assertIn("translation", features.__doc__)
        self.assertIn("one record", features.__doc__)


class EnablementTests(unittest.TestCase):
    def test_japanese_is_enabled_by_selecting_either_of_its_schemas(self):
        self.assertFalse(features.JAPANESE.enabled(settings.resolve({})))
        for schema in features.JAPANESE.schemas:
            with self.subTest(schema=schema):
                resolved = settings.resolve({"schemas": ["rime_ice", schema]})
                self.assertTrue(features.JAPANESE.enabled(resolved))

    def test_a_selection_is_repaired_before_it_is_read(self):
        # A hand-edited selection that lost its Chinese schema gains the default
        # back; a feature reading the raw list would disagree with the deploy.
        resolved = settings.resolve({"schemas": ["sno_japanese"]})
        self.assertTrue(features.JAPANESE.enabled(resolved))
        self.assertIn("rime_ice", resolved["schemas"])


class DerivedViewTests(unittest.TestCase):
    """The old tables are views of the records, and the records alone."""

    def test_schema_sources_is_the_union_of_the_features_sources(self):
        expected = {}
        for feature in features.FEATURES:
            expected.update(feature.sources)
        self.assertEqual(features.SCHEMA_SOURCES, expected)

    def test_the_retired_tables_are_gone(self):
        # Each of these was a second copy of something a feature now declares.
        self.assertFalse(hasattr(assets, "SCHEMA_SOURCES"))
        self.assertFalse(hasattr(deploy, "GROUP_SCHEMAS"))
        self.assertFalse(hasattr(generate, "ANNOTATED_SCHEMAS"))

    def test_active_groups_follow_the_feature_that_owns_each_group(self):
        resolved = settings.resolve({"schemas": ["rime_ice", "sno_japanese"],
                                     "annotation": True})
        active = deploy.active_groups(resolved)
        for group in deploy.PAYLOAD_GROUPS:
            owner = features.owner(group)
            with self.subTest(group=group):
                self.assertEqual(group in active,
                                 owner is None or owner.enabled(resolved))

    def test_a_group_nobody_owns_is_always_installed(self):
        self.assertIsNone(features.owner("fcitx5"))
        self.assertIn("fcitx5", deploy.active_groups(settings.resolve({})))

    def test_every_payload_directory_is_a_declared_group(self):
        on_disk = {path.name for path in PAYLOAD.iterdir() if path.is_dir()}
        self.assertEqual(on_disk, set(deploy.PAYLOAD_GROUPS))

    def test_needed_sources_come_from_the_feature_records(self):
        resolved = settings.resolve({"schemas": ["rime_ice", "sno_japanese"]})
        self.assertEqual(deploy.needed_sources(resolved),
                         list(features.JAPANESE.sources["sno_japanese"]))

    def test_the_japanese_schema_list_ends_are_the_features_schemas(self):
        self.assertEqual((generate.JAPANESE_HEAD, generate.JAPANESE_TAIL),
                         features.JAPANESE.schemas)


def _code_strings(path: Path) -> list[str]:
    """Every string literal in a module that is not a docstring.

    Comments and docstrings may talk about a feature all they like; a string a
    line of code compares against or writes out is the thing that must not be
    a second copy of what the feature record says.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class NoSecondCopyTests(unittest.TestCase):
    """No module that acts on a feature spells its facts out again."""

    MODULES = ("deploy.py", "generate.py", "assets.py")

    def _spelled_out(self, needle: str) -> list[str]:
        return [
            name for name in self.MODULES
            if any(needle in text for text in _code_strings(ROOT / "lib" / name))
        ]

    def test_no_feature_schema_is_named_by_hand(self):
        for feature in features.FEATURES:
            for schema in feature.schemas:
                with self.subTest(schema=schema):
                    self.assertEqual(self._spelled_out(schema), [])

    def test_no_owned_setting_is_read_by_hand(self):
        for feature in features.FEATURES:
            for key in feature.settings:
                with self.subTest(key=key):
                    self.assertEqual(self._spelled_out(key), [])

    def test_no_patch_key_is_written_outside_the_feature(self):
        self.assertEqual(self._spelled_out("annotate"), [])


class AnnotationSwitchTests(unittest.TestCase):
    """The annotation feature is switched by one setting, off by default."""

    CONTEXT = features.PatchContext(dictionary_path="/cache/cedict.txt",
                                    quote=lambda value: f'"{value}"')

    def test_it_is_switched_by_the_annotation_setting(self):
        self.assertEqual(features.ANNOTATION.setting, "annotation")
        self.assertIn("annotation", features.ANNOTATION.settings)

    def test_a_fresh_install_has_it_off(self):
        resolved = settings.resolve({})
        self.assertFalse(features.ANNOTATION.enabled(resolved))
        self.assertFalse(features.dictionary_wanted(resolved))
        self.assertNotIn("rime", deploy.active_groups(resolved))
        self.assertEqual(features.patches(resolved, "rime_ice", self.CONTEXT), [])

    def test_off_wins_over_the_directions(self):
        resolved = settings.resolve({"annotation": False, "annotateChinese": True,
                                     "annotateEnglish": True})
        self.assertFalse(features.ANNOTATION.enabled(resolved))
        self.assertFalse(features.ANNOTATION.active(resolved))
        self.assertFalse(features.dictionary_wanted(resolved))
        self.assertEqual(features.patches(resolved, "rime_ice", self.CONTEXT), [])

    def test_on_with_a_direction_is_active(self):
        resolved = settings.resolve({"annotation": True})
        self.assertTrue(features.ANNOTATION.enabled(resolved))
        self.assertTrue(features.ANNOTATION.active(resolved))
        self.assertTrue(features.dictionary_wanted(resolved))
        self.assertIn("rime", deploy.active_groups(resolved))
        lines = features.patches(resolved, "rime_ice", self.CONTEXT)
        self.assertTrue(any("lua_filter@*annotate" in line for line in lines))

    def test_on_with_no_direction_installs_the_filter_and_asks_nothing_of_it(self):
        resolved = settings.resolve({"annotation": True, "annotateChinese": False,
                                     "annotateEnglish": False})
        self.assertTrue(features.ANNOTATION.enabled(resolved))
        self.assertFalse(features.ANNOTATION.active(resolved))
        self.assertFalse(features.dictionary_wanted(resolved))
        self.assertIn("rime", deploy.active_groups(resolved))
        self.assertEqual(features.patches(resolved, "rime_ice", self.CONTEXT), [])


if __name__ == "__main__":
    unittest.main()
