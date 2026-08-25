# tests/test_annotate_lua.py
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILTER = ROOT / "payload" / "rime" / "lua" / "annotate.lua"
# The filter reads CC-CEDICT's published line format directly, so the fixture is
# written in that format, header and all. It is not CC-CEDICT: every entry in it
# was written for these tests, and the fixture's own header says so.
FIXTURE = ROOT / "tests" / "fixtures" / "cedict-sample.u8"


def run_lua(body: str) -> str:
    script = f'local M = dofile("{FILTER}")\n{body}\n'
    result = subprocess.run(["lua", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class DictionaryTests(unittest.TestCase):
    def test_a_chinese_term_finds_its_gloss(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "早上好", false))'
        )
        self.assertEqual(out, "good morning; morning")

    def test_pinyin_is_included_when_asked_for(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "早上好", true))'
        )
        self.assertEqual(out, "zao3 shang4 hao3 · good morning; morning")

    def test_traditional_and_simplified_both_resolve(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "編碼", false), M.gloss_for(d, "编码", false))'
        )
        self.assertEqual(out.split("\t"), ["code; encoding", "code; encoding"])

    def test_an_english_word_finds_its_chinese(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "code", false))'
        )
        self.assertEqual(out, "编码")

    def test_every_sense_that_fits_is_kept(self):
        # Upstream separates senses with slashes; the annotation shows as many
        # of them as the width cap has room for, joined into one line.
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "\u63d0\u793a", false))'
        )
        self.assertEqual(out, "hint; tip; prompt")

    def test_a_later_sense_is_an_english_headword_too(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "to make whole", false))'
        )
        self.assertEqual(out, "\u5168")

    def test_the_pinyin_is_read_without_its_brackets(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "\u7f16\u7801", true))'
        )
        self.assertEqual(out, "bian1 ma3 · code; encoding")

    def test_header_comments_are_not_entries(self):
        # The fixture opens with comment lines and a `#!` metadata block, the
        # way the published file does.
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(tostring(M.gloss_for(d, "CC-CEDICT", false)),'
            ' tostring(M.gloss_for(d, "version=1", false)))'
        )
        self.assertEqual(out.split("\t"), ["nil", "nil"])

    def test_an_unknown_term_has_no_gloss(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(tostring(M.gloss_for(d, "zzz", false)))'
        )
        self.assertEqual(out, "nil")

    def test_a_missing_dictionary_loads_as_empty_rather_than_failing(self):
        out = run_lua(
            'local d = M.load_dictionary("/nonexistent/dict.txt")\n'
            'print(tostring(M.gloss_for(d, "早上好", false)))'
        )
        self.assertEqual(out, "nil")


class GlossCleanupTests(unittest.TestCase):
    """Only the meaning reaches the gloss.

    A CC-CEDICT entry packs three kinds of text into its slash-separated
    senses: meanings, apparatus that describes the entry, and cross-references
    to other entries. A gloss is read in the fraction of a second between two
    keystrokes, so only the meaning belongs there.
    """

    def gloss(self, text: str) -> str:
        return run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            f'print(tostring(M.gloss_for(d, "{text}", false)))'
        )

    def test_a_classifier_annotation_is_not_a_meaning(self):
        # 詞典 /dictionary/CL:部|部[bu4]/ - the classifier is for a reader
        # with time, not for someone mid-keystroke.
        self.assertEqual(self.gloss("\u8bcd\u5178"), "dictionary")

    def test_an_alternative_reading_is_not_a_meaning(self):
        # 候選 /candidate/Taiwan pr. [hou4 xuan4]/
        self.assertEqual(self.gloss("\u5019\u9009"), "candidate")

    def test_an_alternative_reading_at_the_end_of_a_long_entry_goes_too(self):
        # 全 ends with `also pr. [quan3] in some dialects`.
        self.assertNotIn("also pr.", self.gloss("\u5168"))

    def test_a_cross_reference_goes_when_a_meaning_remains(self):
        # 半形 /half width; narrow form/single-byte form/see also 全形[quan2 xing2]/
        self.assertEqual(
            self.gloss("\u534a\u5f62"),
            "half width; narrow form; single-byte form",
        )

    def test_bracketed_pinyin_inside_a_meaning_is_dropped(self):
        # 倉頡輸入法 names 朱邦復|朱邦复[Zhu1 Bang1 fu4] mid-sentence. The
        # meaning stays, the traditional form and the pinyin go.
        self.assertEqual(
            self.gloss("\u4ed3\u9889\u8f93\u5165\u6cd5"),
            "Cangjie input method, devised by Chu Bong-Foo \u6731\u90a6\u590d",
        )

    def test_an_entry_that_is_only_a_cross_reference_still_glosses(self):
        # 6% of the file has nothing but cross-references. No gloss at all is
        # worse than a terse one, so the reference stays when it is all there
        # is - tidied the same way a meaning would be.
        self.assertEqual(self.gloss("\u534a\u89d2"), "see \u534a\u5f62")
        self.assertEqual(
            self.gloss("\u5f02\u4f53\u5b57"), "variant of \u5f02\u5f62\u5b57"
        )
        self.assertEqual(
            self.gloss("\u4e94\u7b14"), "abbr. for \u4e94\u7b14\u5b57\u578b"
        )

    def test_a_meaning_that_reads_like_a_cross_reference_is_kept(self):
        # A cross-reference names a Chinese word. These two open with the same
        # words and are meanings, so they must survive as the entry's gloss.
        self.assertEqual(self.gloss("\u53c8\u4f5c"), "also written as")
        self.assertEqual(
            self.gloss("\u5408\u97f3"),
            "contraction of two syllables into one",
        )


# How wide a comment renders, counted the way the filter counts it: a UTF-8
# sequence of three bytes or more takes two columns, anything else one. In this
# dictionary the long sequences are Chinese characters and CJK punctuation.
def columns(text: str) -> int:
    return sum(2 if ord(character) >= 0x800 else 1 for character in text)


# The cap the filter applies, in columns. Measured from the published
# dictionary: 54 columns is the 90th percentile of an entry's first sense, so
# nine entries in ten keep their primary meaning whole, and four in five are
# never touched at all.
MAX_WIDTH = 54
MARKER = "\u2026"


class WidthTests(unittest.TestCase):
    """A comment is bounded so that the candidate window stays a window.

    fcitx5 sizes the window to its content and offers no width limit, so the
    only cap available is the one the gloss itself carries. Common characters
    have the longest entries in the published dictionary, which makes the worst
    case the most frequent one - 做's gloss there is 259 columns of text.
    設定 is this fixture's stand-in for it.
    """

    def gloss(self, text: str, show_pinyin: str = "false") -> str:
        return run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            f'print(tostring(M.gloss_for(d, "{text}", {show_pinyin})))'
        )

    def truncate(self, text: str, max_width: int) -> str:
        return run_lua(f'print(M.truncate([[{text}]], {max_width}))')

    def test_the_cap_is_the_one_measured_from_the_dictionary(self):
        self.assertEqual(run_lua("print(M.MAX_WIDTH)"), str(MAX_WIDTH))

    def test_a_long_gloss_is_cut_to_the_cap(self):
        # 設定, the fixture's longest entry: five senses of it.
        capped = self.gloss("\u8bbe\u5b9a")
        self.assertLessEqual(columns(capped), MAX_WIDTH)

    def test_the_cut_falls_on_a_sense_boundary(self):
        # A sense is the unit a reader takes in, so the cut lands between two
        # of them rather than in the middle of one.
        self.assertEqual(
            self.gloss("\u8bbe\u5b9a"),
            "to set; to configure; to establish; to lay down" + MARKER,
        )

    def test_every_entry_in_the_fixture_stays_within_the_cap(self):
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            "local seen, widest = {}, 0\n"
            "for _, entry in pairs(d.cn) do\n"
            "  if not seen[entry] then\n"
            "    seen[entry] = true\n"
            "    local w = #M.truncate(entry.gloss, M.MAX_WIDTH)\n"
            "    if w > widest then widest = w end\n"
            "  end\n"
            "end\n"
            "print(widest <= M.MAX_WIDTH * 3)"
        )
        self.assertEqual(out, "true")

    def test_a_gloss_within_the_cap_is_left_alone(self):
        # Four entries in five are never touched, and they must not pick up a
        # marker that says something was removed.
        self.assertEqual(self.gloss("\u65e9\u4e0a\u597d"), "good morning; morning")
        self.assertNotIn(MARKER, self.gloss("\u63d0\u793a"))

    def test_a_single_sense_wider_than_the_cap_is_cut_between_words(self):
        # 快捷鍵's whole gloss is one 60-column sense, so there is no sense
        # boundary to cut on and the cut falls between two words instead.
        self.assertEqual(
            self.gloss("\u5feb\u6377\u952e"),
            "a key combination that runs a command without" + MARKER,
        )

    def test_the_marker_says_that_there_is_more(self):
        for word in ("\u8bbe\u5b9a", "\u5168", "\u89c6\u7a97",
                     "\u5168\u5f62", "\u5feb\u6377\u952e"):
            self.assertTrue(self.gloss(word).endswith(MARKER), word)

    def test_the_marker_is_not_three_dots(self):
        # 209 glosses in the published dictionary use `...` as part of their
        # meaning - `either ...; or ...` - so three dots cannot say `cut here`.
        # A cut gloss and one that merely contains dots must stay distinct.
        self.assertNotIn("...", self.gloss("\u8bbe\u5b9a"))
        self.assertEqual(
            self.truncate("either ...; or ...; and so on for a while yet", 20),
            "either ...; or ..." + MARKER,
        )

    def test_a_chinese_character_costs_two_columns(self):
        # The window measures columns, not characters: 30 Chinese characters
        # are 60 columns wide, and only 9 of them fit under a cap of 20.
        chinese = "\u7684" * 30
        self.assertEqual(self.truncate(chinese, 20), "\u7684" * 9 + MARKER)

    def test_a_gloss_that_exactly_fills_the_cap_keeps_all_of_it(self):
        exact = "a" * 20
        self.assertEqual(self.truncate(exact, 20), exact)
        self.assertEqual(self.truncate(exact + "b", 20), "a" * 18 + MARKER)

    def test_a_word_longer_than_the_cap_is_cut_inside_the_word(self):
        # Nothing guarantees a space early enough, so there is a last resort.
        self.assertEqual(self.truncate("a" * 40, 10), "a" * 8 + MARKER)

    def test_no_dangling_punctuation_is_left_before_the_marker(self):
        self.assertEqual(
            self.truncate("alpha, beta gamma delta", 12), "alpha" + MARKER
        )

    def test_the_pinyin_counts_against_the_cap(self):
        # The comment is the pinyin and the gloss together, so the cap has to
        # cover both or it caps nothing.
        with_pinyin = self.gloss("\u8bbe\u5b9a", "true")
        self.assertLessEqual(columns(with_pinyin), MAX_WIDTH)
        self.assertEqual(
            with_pinyin,
            "she4 ding4 \u00b7 to set; to configure; to establish" + MARKER,
        )

    def test_a_pinyin_that_leaves_no_room_for_a_meaning_is_dropped(self):
        # 輸入法候選視窗's pinyin is 36 columns on its own. Squeezing the gloss
        # into what is left would say nothing; the pinyin only repeats what was
        # just typed, so the meaning gets the whole cap instead.
        long_word = "输入法候选视窗"
        capped = self.gloss(long_word, "true")
        self.assertNotIn("shu1", capped)
        self.assertLessEqual(columns(capped), MAX_WIDTH)
        self.assertTrue(capped.startswith("the candidate window"))

    def test_a_short_gloss_still_shows_its_pinyin_whole(self):
        self.assertEqual(
            self.gloss("\u65e9\u4e0a\u597d", "true"),
            "zao3 shang4 hao3 \u00b7 good morning; morning",
        )

    def test_the_reverse_direction_is_short_enough_to_pass_through(self):
        # A Chinese gloss on an English candidate is a headword, never a list
        # of senses: the widest in the published dictionary is 19 characters.
        self.assertEqual(self.gloss("code"), "\u7f16\u7801")

    def test_truncating_the_gloss_does_not_shrink_the_reverse_index(self):
        # 設定's later senses are cut from what is shown, but they are still
        # the English words that find 設定.
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(tostring(M.gloss_for(d, "to assign a value", false)),'
            ' tostring(M.gloss_for(d, "to fix in advance", false)))'
        )
        self.assertEqual(out.split("\t"), ["\u8bbe\u5b9a", "\u8bbe\u5b9a"])

    def test_a_leading_parenthetical_is_kept(self):
        # `(bound form)`, `(coll.)`, `(of a person)` qualify the sense they
        # open. Dropping them buys a median of nothing - 8% of senses have one
        # at all - and changes what the gloss says, so they stay.
        self.assertTrue(
            self.gloss("\u89c6\u7a97").startswith("window; frame; pane; (bound form)")
        )
        self.assertTrue(self.gloss("\u5168\u5f62").startswith("(of a character)"))

    def test_the_annotation_the_filter_writes_is_capped_too(self):
        out = run_lua(
            LUA_ENV
            + 'local env = fake_env({ ["annotate/dictionary"] = "%s" },'
            " { annotate = true })\n"
            "M.init(env)\n"
            'M.func(fake_input({ fake_candidate("\u8bbe\u5b9a") }), env)\n'
            "print(collected[1].comment)" % FIXTURE
        )
        self.assertLessEqual(columns(out), MAX_WIDTH)


class ReverseIndexTests(unittest.TestCase):
    """An English word gets the Chinese word a person actually means."""

    def chinese(self, word: str) -> str:
        return run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            f'print(tostring(M.gloss_for(d, "{word}", false)))'
        )

    def test_a_word_beats_a_single_character(self):
        # 碼 码 [ma3] /code/ sits above 編碼 编码 in the file, but a lone
        # character is a bound morpheme, not the word someone types.
        self.assertEqual(self.chinese("code"), "\u7f16\u7801")

    def test_an_earlier_sense_beats_a_later_one(self):
        # `prompt` is the whole of 提示符 and a later sense of 提示, which
        # comes first.
        self.assertEqual(self.chinese("prompt"), "\u63d0\u793a\u7b26")

    def test_a_semicolon_inside_a_sense_separates_english_headwords(self):
        # 早上好's single sense is `good morning; morning`, which is two
        # English words.
        self.assertEqual(self.chinese("good morning"), "\u65e9\u4e0a\u597d")
        self.assertEqual(self.chinese("morning"), "\u65e9\u4e0a\u597d")

    def test_semicolons_split_a_later_sense_too(self):
        # 全 has `complete; entire; total` as one slash-separated sense.
        for word in ("complete", "entire", "total"):
            self.assertEqual(self.chinese(word), "\u5168")

    def test_apparatus_is_not_an_english_headword(self):
        self.assertEqual(self.chinese("CL:\u90e8"), "nil")
        self.assertEqual(self.chinese("Taiwan pr."), "nil")

    def test_a_cross_reference_is_not_an_english_headword(self):
        # 半形's `see also 全形` must not claim anything, and 半角's
        # `see 半形` is the entry's whole gloss but still not an English word.
        self.assertEqual(self.chinese("see also \u5168\u5f62"), "nil")
        self.assertEqual(self.chinese("see \u534a\u5f62"), "nil")

    def test_the_whole_multi_word_sense_is_still_a_headword(self):
        self.assertEqual(
            self.chinese("input method"), "\u8f93\u5165\u6cd5"
        )


class DirectionTests(unittest.TestCase):
    """`gloss_for` glosses only the directions it is handed.

    Which directions to gloss is a deploy-time setting, not a runtime switch,
    so it arrives as arguments rather than as a second Rime option.
    """

    def gloss(self, text: str, chinese: str, english: str) -> str:
        return run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            f'print(tostring(M.gloss_for(d, "{text}", false, {chinese}, {english})))'
        )

    def test_chinese_off_drops_the_chinese_gloss_and_keeps_the_english_one(self):
        self.assertEqual(self.gloss("\u65e9\u4e0a\u597d", "false", "true"), "nil")
        self.assertEqual(self.gloss("code", "false", "true"), "\u7f16\u7801")

    def test_english_off_drops_the_english_gloss_and_keeps_the_chinese_one(self):
        self.assertEqual(self.gloss("code", "true", "false"), "nil")
        self.assertEqual(
            self.gloss("\u65e9\u4e0a\u597d", "true", "false"), "good morning; morning"
        )

    def test_both_directions_off_gloss_nothing(self):
        self.assertEqual(self.gloss("\u65e9\u4e0a\u597d", "false", "false"), "nil")
        self.assertEqual(self.gloss("code", "false", "false"), "nil")

    def test_both_directions_on_gloss_both_ways(self):
        self.assertEqual(
            self.gloss("\u65e9\u4e0a\u597d", "true", "true"), "good morning; morning"
        )
        self.assertEqual(self.gloss("code", "true", "true"), "\u7f16\u7801")

    def test_omitted_flags_mean_both_directions(self):
        # The flags were added to an existing three-argument function, so a call
        # without them has to keep meaning what it meant before.
        out = run_lua(
            f'local d = M.load_dictionary("{FIXTURE}")\n'
            'print(M.gloss_for(d, "\u65e9\u4e0a\u597d", false),'
            ' M.gloss_for(d, "code", false))'
        )
        self.assertEqual(out.split("\t"), ["good morning; morning", "\u7f16\u7801"])


# A stand-in for the librime-lua environment a filter is handed: `env.engine`
# carries the schema, whose `config` answers `get_string` / `get_bool`, and the
# context answers `get_option`. Calls are recorded so a test can prove the
# config is read once at init rather than per candidate.
LUA_ENV = """
local calls = {}
local function fake_env(values, options)
  local config = {
    get_string = function(self, key)
      calls[#calls + 1] = "get_string:" .. key
      return values[key]
    end,
    get_bool = function(self, key)
      calls[#calls + 1] = "get_bool:" .. key
      return values[key]
    end,
  }
  local context = {
    get_option = function(self, name) return (options or {})[name] end,
  }
  return { engine = { schema = { config = config }, context = context } }
end

local function fake_candidate(text)
  local candidate = { text = text }
  function candidate:get_genuine() return self end
  return candidate
end

local function fake_input(list)
  return {
    iter = function(self)
      local index = 0
      return function()
        index = index + 1
        return list[index]
      end
    end,
  }
end

local collected = {}
function yield(candidate) collected[#collected + 1] = candidate end
"""


class ConfigTests(unittest.TestCase):
    """The filter reads its settings from the schema, not from injected env fields."""

    def run_with_env(self, body: str) -> str:
        return run_lua(LUA_ENV + body)

    def test_the_entry_points_rime_looks_for_are_exported(self):
        # librime-lua calls `init` once and `func` per candidate set. A module
        # that exports anything else loads and then silently does nothing.
        self.assertEqual(run_lua("print(type(M.init), type(M.func))").split("\t"),
                         ["function", "function"])

    def test_nothing_is_exported_under_the_wrong_entry_point_name(self):
        self.assertEqual(run_lua("print(tostring(M.filter))"), "nil")

    def test_the_dictionary_path_comes_from_the_schema_config(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "/cache/cedict.txt" })\n'
            "M.init(env)\n"
            "print(env.dictionary_path)"
        )
        self.assertEqual(out, "/cache/cedict.txt")

    def test_show_pinyin_comes_from_the_schema_config(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/show_pinyin"] = false })\n'
            "M.init(env)\n"
            "print(tostring(env.show_pinyin))"
        )
        self.assertEqual(out, "false")

    def test_show_pinyin_reads_true_as_true(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/show_pinyin"] = true })\n'
            "M.init(env)\n"
            "print(tostring(env.show_pinyin))"
        )
        self.assertEqual(out, "true")

    def test_the_keys_read_are_the_ones_the_generator_writes(self):
        # The generator writes four keys and the filter must read exactly those
        # four: a key read but never written silently takes its default forever.
        out = self.run_with_env(
            "M.init(fake_env({}))\n"
            "table.sort(calls)\n"
            'print(table.concat(calls, ","))'
        )
        self.assertEqual(
            out,
            "get_bool:annotate/chinese,"
            "get_bool:annotate/english,"
            "get_bool:annotate/show_pinyin,"
            "get_string:annotate/dictionary",
        )

    def test_an_unconfigured_dictionary_loads_as_empty_rather_than_failing(self):
        out = self.run_with_env(
            "local env = fake_env({})\n"
            "M.init(env)\n"
            "local d = M.load_dictionary(env.dictionary_path)\n"
            'print(tostring(M.gloss_for(d, "早上好", false)))'
        )
        self.assertEqual(out, "nil")

    def test_an_unconfigured_show_pinyin_defaults_to_off(self):
        out = self.run_with_env(
            "local env = fake_env({})\n"
            "M.init(env)\n"
            "print(tostring(env.show_pinyin))"
        )
        self.assertEqual(out, "false")

    def test_the_filter_annotates_from_the_configured_dictionary(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s",'
            ' ["annotate/show_pinyin"] = true },'
            " { annotate = true })\n"
            "M.init(env)\n"
            'M.func(fake_input({ fake_candidate("早上好") }), env)\n'
            "print(collected[1].comment)" % FIXTURE
        )
        self.assertEqual(out, "zao3 shang4 hao3 · good morning; morning")

    def test_show_pinyin_off_reaches_the_annotation(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s",'
            ' ["annotate/show_pinyin"] = false },'
            " { annotate = true })\n"
            "M.init(env)\n"
            'M.func(fake_input({ fake_candidate("早上好") }), env)\n'
            "print(collected[1].comment)" % FIXTURE
        )
        self.assertEqual(out, "good morning; morning")

    def test_the_config_is_read_once_at_init_not_per_candidate(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s" },'
            " { annotate = true })\n"
            "M.init(env)\n"
            "local after_init = #calls\n"
            'M.func(fake_input({ fake_candidate("早上好"), fake_candidate("编码") }), env)\n'
            'M.func(fake_input({ fake_candidate("早上好") }), env)\n'
            "print(after_init, #calls)" % FIXTURE
        )
        # Four reads at init, and the count is unchanged after three candidates
        # across two calls to func: the per-candidate path touches no config.
        self.assertEqual(out.split("\t"), ["4", "4"])

    def test_the_directions_come_from_the_schema_config(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/chinese"] = false,'
            ' ["annotate/english"] = true })\n'
            "M.init(env)\n"
            "print(tostring(env.annotate_chinese), tostring(env.annotate_english))"
        )
        self.assertEqual(out.split("\t"), ["false", "true"])

    def test_unconfigured_directions_default_to_on(self):
        # An absent key reads as nil, which must not read as a configured false.
        out = self.run_with_env(
            "local env = fake_env({})\n"
            "M.init(env)\n"
            "print(tostring(env.annotate_chinese), tostring(env.annotate_english))"
        )
        self.assertEqual(out.split("\t"), ["true", "true"])

    def test_the_configured_directions_reach_the_annotation(self):
        # Chinese off: a Chinese candidate loses its gloss while an English one
        # keeps the Chinese it is glossed with.
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s",'
            ' ["annotate/show_pinyin"] = false, ["annotate/chinese"] = false,'
            ' ["annotate/english"] = true },'
            " { annotate = true })\n"
            "M.init(env)\n"
            "M.func(fake_input({ fake_candidate(\"\u65e9\u4e0a\u597d\"),"
            ' fake_candidate("code") }), env)\n'
            "print(tostring(collected[1].comment), tostring(collected[2].comment))"
            % FIXTURE
        )
        self.assertEqual(out.split("\t"), ["nil", "\u7f16\u7801"])

    def test_the_mirror_direction_reaches_the_annotation_too(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s",'
            ' ["annotate/show_pinyin"] = false, ["annotate/chinese"] = true,'
            ' ["annotate/english"] = false },'
            " { annotate = true })\n"
            "M.init(env)\n"
            "M.func(fake_input({ fake_candidate(\"\u65e9\u4e0a\u597d\"),"
            ' fake_candidate("code") }), env)\n'
            "print(tostring(collected[1].comment), tostring(collected[2].comment))"
            % FIXTURE
        )
        self.assertEqual(out.split("\t"), ["good morning; morning", "nil"])

    def test_candidates_pass_through_untouched_when_the_switch_is_off(self):
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s" },'
            " { annotate = false })\n"
            "M.init(env)\n"
            "M.func(fake_input({ fake_candidate(\"\u65e9\u4e0a\u597d\"),"
            " fake_candidate(\"\u7f16\u7801\") }), env)\n"
            "print(#collected, tostring(collected[1].comment),"
            " tostring(collected[2].comment))" % FIXTURE
        )
        # Every candidate still reaches the menu, just without a comment.
        self.assertEqual(out.split("\t"), ["2", "nil", "nil"])

    def test_the_switch_the_filter_watches_is_the_one_the_generator_binds(self):
        # The generator emits a single switch named `annotate`. A filter still
        # watching a per-direction option would annotate whenever that option
        # happened to be set, with nothing bound to turn it off.
        out = self.run_with_env(
            'local env = fake_env({ ["annotate/dictionary"] = "%s" },'
            " { english_comment = true, chinese_comment = true })\n"
            "M.init(env)\n"
            "M.func(fake_input({ fake_candidate(\"\u65e9\u4e0a\u597d\") }), env)\n"
            "print(tostring(collected[1].comment))" % FIXTURE
        )
        self.assertEqual(out, "nil")


if __name__ == "__main__":
    unittest.main()
