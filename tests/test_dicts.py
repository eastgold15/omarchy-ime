"""lib/dicts.py and the cells feature end to end.

The catalogue is the one place the dictionaries are named; these tests hold
the wiring to it: the ids are the file names Rime requires, a selection is
repaired rather than trusted, the wrapper imports base-then-cells with the
base's own ranking flag, the patch reaches exactly the pinyin schemas, and a
deploy whose fetch failed ships no wrapper rather than an unbuildable one.
"""
import unittest

from lib import assets, deploy, dicts, features, generate, settings

from tests.test_deploy import DeployCase


class CatalogueTests(unittest.TestCase):
    def test_ids_are_unique(self):
        self.assertEqual(len(dicts.DICT_IDS), len(set(dicts.DICT_IDS)))

    def test_every_catalogue_entry_has_a_source_file_and_vice_versa(self):
        # Rime requires the file name, the `name:` inside it, and the
        # import_tables entry to be the same string, so the id is all three.
        self.assertEqual(
            set(dicts.SOURCE_FILES.values()),
            {f"{dict_id}.dict.yaml" for dict_id in dicts.DICT_IDS},
        )

    def test_the_source_is_one_assets_knows_how_to_fetch(self):
        self.assertIn(dicts.SOURCE, assets.SOURCES)
        self.assertEqual(assets.SOURCE_FILES[dicts.SOURCE], dicts.SOURCE_FILES)

    def test_options_feed_a_multiselect(self):
        options = dicts.options()
        self.assertEqual([option["value"] for option in options],
                         list(dicts.DICT_IDS))
        for option in options:
            self.assertTrue(option["label"])
            self.assertTrue(option["description"])


class SelectionTests(unittest.TestCase):
    def test_unknown_ids_are_dropped(self):
        self.assertEqual(dicts.selection(["idiom", "nonsense"]), ["idiom"])

    def test_the_survivors_come_back_in_catalogue_order(self):
        self.assertEqual(dicts.selection(["food", "exthot"]), ["exthot", "food"])

    def test_anything_but_a_list_is_no_selection(self):
        for value in (None, "idiom", 42, {"idiom": True}):
            with self.subTest(value=value):
                self.assertEqual(dicts.selection(value), [])

    def test_empty_is_a_normal_answer(self):
        self.assertEqual(dicts.selection([]), [])


class WrapperTests(unittest.TestCase):
    def test_the_wrapper_name_matches_its_file(self):
        self.assertEqual(dicts.wrapper_name("rime_ice"), "rime_ice_cells")

    def test_the_base_table_is_imported_first(self):
        text = dicts.render_wrapper("rime_ice", ["idiom", "food"])
        imports = [line.strip() for line in text.splitlines()
                   if line.startswith("  - ")]
        self.assertEqual(imports, ["- rime_ice", "- idiom", "- food"])

    def test_the_wrapper_declares_the_name_its_file_will_carry(self):
        text = dicts.render_wrapper("double_pinyin_flypy", ["game"])
        self.assertIn("name: double_pinyin_flypy_cells", text)
        # The double-pinyin family mounts rime_ice's table: checked against the
        # packaged schemas, and the reason full-pinyin cells work for them.
        self.assertIn("  - rime_ice", text)

    def test_the_preset_vocabulary_flag_follows_the_base(self):
        self.assertIn("use_preset_vocabulary: false",
                      dicts.render_wrapper("rime_ice", ["idiom"]))
        self.assertIn("use_preset_vocabulary: true",
                      dicts.render_wrapper("luna_pinyin", ["idiom"]))
        self.assertIn("use_preset_vocabulary: true",
                      dicts.render_wrapper("terra_pinyin", ["idiom"]))

    def test_every_cell_schema_has_a_base_dictionary(self):
        for schema in dicts.CELL_SCHEMAS:
            with self.subTest(schema=schema):
                base, _preset = dicts.BASE_DICTIONARY[schema]
                self.assertTrue(base)


class PatchTests(unittest.TestCase):
    CONTEXT = features.PatchContext(dictionary_path="/cache/cedict.txt",
                                    quote=lambda value: f'"{value}"')

    def _patched(self, settings_dict, schema):
        return features.patches(settings.resolve(settings_dict), schema,
                                self.CONTEXT)

    def test_a_pinyin_schema_is_pointed_at_its_wrapper(self):
        lines = self._patched({"dictionaries": ["idiom"]}, "rime_ice")
        self.assertIn("translator/dictionary: rime_ice_cells",
                      [line.strip() for line in lines])

    def test_non_pinyin_schemas_get_no_patch(self):
        for schema in ("bopomofo", "cangjie5", "terra_pinyin_12345",
                       "japanese_tw_eng", "sno_japanese"):
            with self.subTest(schema=schema):
                lines = self._patched({"schemas": ["rime_ice", schema],
                                       "dictionaries": ["idiom"]}, schema)
                self.assertNotIn("translator/dictionary: ",
                                 [line.strip() for line in lines])

    def test_no_patch_without_a_selection(self):
        self.assertEqual(self._patched({}, "rime_ice"), [])


class SettingsTests(unittest.TestCase):
    def test_resolve_repairs_the_selection(self):
        resolved = settings.resolve({"dictionaries": ["food", "nonsense",
                                                      "exthot"]})
        self.assertEqual(resolved["dictionaries"], ["exthot", "food"])

    def test_the_default_is_no_cells(self):
        self.assertEqual(settings.resolve({})["dictionaries"], [])

    def test_validate_refuses_an_unknown_id(self):
        with self.assertRaises(ValueError):
            settings.validate("dictionaries", ["nonsense"])

    def test_validate_allows_an_empty_selection(self):
        self.assertEqual(settings.validate("dictionaries", []), [])

    def test_validate_deduplicates(self):
        self.assertEqual(settings.validate("dictionaries", ["idiom", "idiom"]),
                         ["idiom"])


class RenderTests(unittest.TestCase):
    def test_a_wrapper_is_rendered_for_each_selected_pinyin_schema(self):
        resolved = settings.resolve({"dictionaries": ["idiom"]})
        files = generate.render(resolved, "/cache/cedict.txt")
        self.assertIn("rime_ice_cells.dict.yaml", files)
        self.assertIn("translator/dictionary: rime_ice_cells",
                      files["rime_ice.custom.yaml"])

    def test_no_selection_renders_no_wrapper(self):
        resolved = settings.resolve({})
        files = generate.render(resolved, "/cache/cedict.txt")
        self.assertFalse(any(name.endswith("_cells.dict.yaml") for name in files))

    def test_a_non_pinyin_schema_gets_neither_wrapper_nor_patch(self):
        resolved = settings.resolve({"schemas": ["bopomofo"],
                                     "dictionaries": ["idiom"]})
        files = generate.render(resolved, "/cache/cedict.txt")
        self.assertNotIn("bopomofo_cells.dict.yaml", files)
        custom = files.get("bopomofo.custom.yaml", "")
        self.assertNotIn("translator/dictionary", custom)

    def test_selecting_cells_for_a_non_pinyin_schema_is_announced(self):
        resolved = settings.resolve({"schemas": ["bopomofo"],
                                     "dictionaries": ["idiom"]})
        messages = generate.notices(resolved)
        self.assertTrue(any("bopomofo" in message for message in messages))

    def test_the_wrapper_carries_the_generated_file_header(self):
        resolved = settings.resolve({"dictionaries": ["idiom"]})
        files = generate.render(resolved, "/cache/cedict.txt")
        self.assertIn("generated by eastgold15.omarchy.ime",
                      files["rime_ice_cells.dict.yaml"])


class CellsDeployTests(DeployCase):
    """The deploy half: files in, files out, and a failed fetch means no cells."""

    def test_selecting_a_cell_installs_it_and_the_wrapper(self):
        self._deploy(dictionaries=["idiom"])
        self.assertTrue((self.rime / "idiom.dict.yaml").is_file())
        wrapper = self.rime / "rime_ice_cells.dict.yaml"
        self.assertTrue(wrapper.is_file())
        self.assertIn("  - idiom", wrapper.read_text())

    def test_the_whole_catalogue_is_installed_while_any_cell_is_selected(self):
        # The clone is one fetch either way; only the selected cells reach an
        # import_tables, so only those are compiled.
        self._deploy(dictionaries=["idiom"])
        for dict_id in dicts.DICT_IDS:
            self.assertTrue((self.rime / f"{dict_id}.dict.yaml").is_file(),
                            dict_id)

    def test_deselecting_removes_the_files(self):
        self._deploy(dictionaries=["idiom"])
        self._deploy()
        self.assertFalse((self.rime / "idiom.dict.yaml").exists())
        self.assertFalse((self.rime / "rime_ice_cells.dict.yaml").exists())

    def test_a_failed_fetch_deploys_without_cells_and_says_so(self):
        def failing(name):
            raise RuntimeError("offline")
        self.provider = failing
        deploy.deploy(
            settings.resolve({"dictionaries": ["idiom"]}),
            self.targets, self.state, self.payload,
            asset_provider=self.provider, theme_source=self.theme,
            notice=self.notices.append,
        )
        self.assertFalse((self.rime / "rime_ice_cells.dict.yaml").exists())
        # The schema stays on its base table: no patch naming the wrapper.
        custom = (self.rime / "rime_ice.custom.yaml")
        if custom.exists():
            self.assertNotIn("translator/dictionary", custom.read_text())
        self.assertTrue(any("cells" in line for line in self.notices))
        # The setting itself is untouched: the next online deploy picks it up.
        self.assertEqual(dicts.selection(["idiom"]), ["idiom"])

    def test_a_failed_fetch_after_a_success_keeps_the_old_copy(self):
        self._deploy(dictionaries=["idiom"])

        def failing(name):
            raise RuntimeError("offline")
        self.provider = failing
        self._deploy(dictionaries=["idiom"])
        self.assertTrue((self.rime / "idiom.dict.yaml").is_file())
        self.assertTrue((self.rime / "rime_ice_cells.dict.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
