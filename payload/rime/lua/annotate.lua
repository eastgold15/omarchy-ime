-- payload/rime/lua/annotate.lua
--
-- Annotate candidates with their meaning in the other language: English on
-- Chinese candidates, Chinese on English ones.
--
-- The dictionary is CC-CEDICT exactly as its publisher distributes it, so the
-- upstream line format is parsed here rather than converted at deploy time:
--
--   # a comment, and `#!` metadata, both skipped
--   Traditional Simplified [pin1 yin1] /gloss one/gloss two/
--
-- Nothing rewrites that file, and nothing but upstream publishes it.
--
-- The dictionary is loaded on first use rather than at deploy time, and a
-- dictionary that is absent loads as empty. Typing must keep working when the
-- download has not happened or has failed.

local M = {}

-- One upstream line: two headwords, bracketed pinyin, then slash-separated
-- senses. A line that does not match - a comment, the `#!` metadata block, or
-- the occasional malformed entry - is skipped rather than treated as an error.
local ENTRY = "^(%S+)%s+(%S+)%s+%[([^%]]*)%]%s+/(.+)/%s*$"

-- Any byte with the high bit set belongs to a multi-byte UTF-8 sequence, which
-- in this file always means a Chinese character.
local CHINESE = "[\128-\255]"

-- CC-CEDICT packs three different kinds of text into one entry's senses, and
-- only one of them is a meaning. The counts below are from the file published
-- 2026-08: 124,934 entries, 199,649 senses between them.
--
-- Apparatus describes the entry rather than meaning anything. No entry in the
-- file is made of apparatus alone, so it is always dropped.
local APPARATUS = {
  "^CL:",           -- 1,477 senses: the classifier the noun is counted with
  "^Taiwan pr%.",   --   511 senses: an alternative reading, not a sense
  "^also pr%.",     --   182 senses: ditto
}

-- Cross-references point at another entry. They are apparatus when the entry
-- also states a meaning, but 7,913 entries - 6% of the file - carry nothing
-- else, and an entry with no gloss at all is worse than a terse one, so these
-- are dropped only when a meaning survives them.
--
-- A cross-reference always names a Chinese word, and requiring that keeps real
-- glosses which happen to open with the same words: `contraction of the uterus
-- (during childbirth)` and `used in the transliteration of Greek letters` are
-- meanings. That test rescues 79 senses the prefixes alone would discard.
local REFERENCE = {
  "^see%s",                   -- 4,447, `see also` among them
  "^[%a%s]-variant of%s",     -- 3,330, incl. `old`, `erhua`, `archaic`, ...
  "^used in%s",               --   821
  "^abbr%. %a+%s",            --   414, incl. `abbr. to`, `abbr. of`
  "^also written%s",          --   216
  "^same as%s",               --   116
  "^erhua form of%s",         --    43
  "^also called%s",           --    42
  "^cf%.?%s",                 --    15
  "^[%a%s]-equivalent of%s",  --    10
  "^another name for%s",      --     6
  "^now written%s",           --     4
  "^contraction of%s",        --     2
}

local function matches(sense, patterns)
  for index = 1, #patterns do
    if sense:find(patterns[index]) then
      return true
    end
  end
  return false
end

-- Every sense in the file is tested for apparatus, so the test has to fail
-- cheaply. All three patterns are anchored on a literal, and only `CL:`,
-- `Taiwan pr.` and `also pr.` open with these bytes, so one byte rules out
-- almost every sense before a pattern is tried at all.
local APPARATUS_OPENERS = { [67] = true, [84] = true, [97] = true } -- C, T, a

local function is_apparatus(sense)
  return APPARATUS_OPENERS[sense:byte(1)] ~= nil
     and matches(sense, APPARATUS)
end

-- A cross-reference must name a Chinese word, and only 9.6% of senses contain
-- one at all. The caller has already checked the whole entry for Chinese, so
-- the senses of the other nine entries in ten never reach these patterns.
local function is_reference(sense)
  return sense:find(CHINESE) ~= nil and matches(sense, REFERENCE)
end

-- A gloss is read in the fraction of a second between two keystrokes, beside
-- the candidate it belongs to. `個|个[ge4]` is written for a reader with time:
-- the traditional form, the simplified form and the pinyin of a word that is
-- not even the one being looked up. Of that, only the simplified form says
-- anything, so the bracketed pinyin goes and the traditional half of the pair
-- goes with it.
--
-- Nine senses in ten carry neither, so each rewrite is behind a plain-text
-- search rather than run unconditionally: the pattern matcher is the whole
-- cost of loading the file.
local SURROUNDING_SPACE = "^%s*(.-)%s*$"

-- Both rewrites below used to be a `gsub` with a pattern opening on a `*`
-- repeat: `(<chinese>)%[[^%]]*%]` and `[^%s%p]*|`. A gsub retries its pattern at
-- every byte, and at every byte one of those repeats runs to the end of the
-- sense and then backtracks the whole way when the closing byte is not there.
-- That is quadratic in the length of one sense, and one dictionary line decides
-- how long a sense is: a line reading `/|` followed by 40,000 letters took 7.5
-- seconds here, 160,000 took two minutes, and the cost is squared each time the
-- line doubles.
--
-- That matters more here than the same bug would somewhere else. This loader
-- runs inside librime, on the first keystroke after the glosses are switched
-- on, on the thread the input method answers keys on - so a loader that takes
-- minutes is a desktop with no working keyboard for those minutes and no
-- visible reason why. Both rewrites are therefore done by scanning for the one
-- delimiter that matters and walking each run once. Every position either
-- advances or ends the scan, so both are linear in the sense.

-- Which bytes end the run in front of a `|`, matching `[^%s%p]` exactly. Built
-- from the pattern matcher itself rather than from a list of characters, so it
-- keeps saying what the pattern said. Bytes with the high bit set are neither
-- space nor punctuation in the C locale, which is what made `[^%s%p]*` match a
-- Chinese headword in the first place.
local RUN_ENDS_AT = {}
for byte = 0, 255 do
  RUN_ENDS_AT[byte] = string.char(byte):find("[%s%p]") ~= nil
end

-- `(<chinese>)%[[^%]]*%]` -> `%1`: drop a bracketed reading that follows a
-- Chinese character, keeping the character.
local function drop_bracketed_readings(sense)
  local pieces, from, at, close = {}, 1, sense:find("[", 1, true), nil
  while at do
    if close == nil or close <= at then
      close = sense:find("]", at + 1, true)
      -- No `]` after this `[` means none after any later one either.
      if close == nil then break end
    end
    if at - 1 >= from and sense:byte(at - 1) >= 128 then
      pieces[#pieces + 1] = sense:sub(from, at - 1)
      from = close + 1
      at = sense:find("[", from, true)
    else
      at = sense:find("[", at + 1, true)
    end
  end
  if from == 1 then
    return sense
  end
  pieces[#pieces + 1] = sense:sub(from)
  return table.concat(pieces)
end

-- `[^%s%p]*|` -> ``: drop each traditional form and the `|` that follows it.
-- The run in front of a `|` reaches back no further than the previous `|`,
-- which is what gsub's non-overlapping matches did.
local function drop_traditional_halves(sense)
  local pieces, from = {}, 1
  local at = sense:find("|", from, true)
  if at == nil then
    return sense
  end
  while at do
    local start = at
    while start > from and not RUN_ENDS_AT[sense:byte(start - 1)] do
      start = start - 1
    end
    pieces[#pieces + 1] = sense:sub(from, start - 1)
    from = at + 1
    at = sense:find("|", from, true)
  end
  pieces[#pieces + 1] = sense:sub(from)
  return table.concat(pieces)
end

local function readable(sense)
  -- Only brackets that follow a Chinese character, so that the handful of
  -- other bracketed notes - `Tai-lo pr. [ue-a-tshai]` - are left alone.
  if sense:find("[", 1, true) then
    sense = drop_bracketed_readings(sense)
  end
  -- Every `|` in the file separates a traditional form from its simplified
  -- one; the run before it is that traditional form.
  if sense:find("|", 1, true) then
    sense = drop_traditional_halves(sense)
  end
  -- Anything below a space is ASCII whitespace or a control character; a
  -- Chinese character is never mistaken for one, since all its bytes are high.
  local first, last = sense:byte(1), sense:byte(-1)
  if first == nil then
    return ""
  end
  if first <= 32 or last <= 32 then
    sense = sense:gsub(SURROUNDING_SPACE, "%1")
  end
  return sense
end

-- fcitx5 sizes the candidate window to whatever it is asked to draw:
-- `libclassicui.so` carries no max-width, wrap, elide or truncate option, so
-- the only cap available is the one the gloss itself arrives with. Without one
-- a single candidate spans the screen - 做's gloss is 259 columns - and the
-- characters with the longest entries are the common ones, which makes the
-- worst case also the most frequent one.
--
-- The cap counts columns rather than characters, because a column is what the
-- window is measuring: a Chinese character is drawn twice as wide as a letter.
-- Any UTF-8 sequence of three bytes or more counts as two columns. Here those
-- are Chinese characters and CJK punctuation, apart from 682 marks in 4.7
-- million characters of gloss - dashes, curly quotes, the odd ⅓ - which are
-- ambiguous-width and are drawn wide in a CJK font in any case.
--
-- 54 columns is the 90th percentile of an entry's first sense, measured over
-- the 121,946 entries of the file published 2026-08. The first sense is the
-- entry's primary meaning, so at this cap nine entries in ten still show
-- theirs whole, and 78% of glosses are not touched at all. Past that point
-- width buys very little meaning: the 95th percentile is 69 columns, the 99th
-- is 119, and the widest entry in the file is 648.
--
-- The cap is fixed rather than configurable. It is a bound on a window that
-- would otherwise be unusable, not a preference someone holds before seeing
-- it, and a settable one would let the bug back in. Nothing else here is
-- tunable per install either - there is no panel font setting for it to track.
local MAX_WIDTH = 54
M.MAX_WIDTH = MAX_WIDTH

-- Something has to say that there is more, or a cut gloss reads as the whole
-- meaning. `...` cannot: 209 glosses in the file use it as part of what they
-- say - `either ...; or ...` - so it would be read as their content. `…`
-- occurs in 8 characters of the entire file, costs two columns rather than
-- three, and is what elision has always been written with.
local MARKER = "…"

-- Continuation bytes are 10xxxxxx and start no character; a lead byte of 224
-- or more starts a sequence of three bytes or more, which is one wide
-- character. Both counts come from a plain byte class, which is the cheapest
-- thing the pattern matcher does.
local CONTINUATION_BYTE = "[\128-\191]"
local WIDE_LEAD_BYTE = "[\224-\255]"

local function width(text)
  local _, continuations = text:gsub(CONTINUATION_BYTE, "")
  local _, wide = text:gsub(WIDE_LEAD_BYTE, "")
  return #text - continuations + wide
end

local SENSE_SEPARATOR = "; "
local WORD_SEPARATOR = " "
-- A cut that lands just after a comma leaves it pointing at nothing.
local TRAILING_PUNCTUATION = "[%s,;:]+$"

-- Where the longest prefix of `text` ends that fits in `budget` columns and
-- stops on a `separator`. Answers nil when not even the first piece fits, so
-- that the caller can fall back to a finer separator.
local function fits(text, separator, budget)
  local gap = #separator
  local used, cut, from = 0, nil, 1
  while true do
    local found = text:find(separator, from, true)
    local stop = (found or #text + 1) - 1
    local extra = width(text:sub(from, stop)) + (from > 1 and gap or 0)
    if used + extra > budget then
      return cut
    end
    used, cut = used + extra, stop
    if not found then
      return cut
    end
    from = found + gap
  end
end

-- Last resort for a single word wider than the whole cap: cut between two
-- characters, which at least never splits one down the middle.
local function clip(text, budget)
  local used, index = 0, 1
  while index <= #text do
    local byte = text:byte(index)
    local size = byte < 128 and 1 or (byte < 224 and 2 or (byte < 240 and 3 or 4))
    local cost = size < 3 and 1 or 2
    if used + cost > budget then
      break
    end
    used, index = used + cost, index + size
  end
  return index - 1
end

-- A cut inside a parenthetical leaves its `(` unclosed - `Niue (self-governing
-- island state in free…`. Backing off to before the bracket was measured and
-- rejected: it tidies 2.5% of the file at a cost of 22 columns of meaning
-- each, and the marker already says why the sentence stops where it does.
function M.truncate(text, max_width)
  -- A column is never worth more than a byte, so anything short in bytes is
  -- short in columns, and the four entries in five that fit answer here.
  if #text <= max_width or width(text) <= max_width then
    return text
  end
  local budget = max_width - width(MARKER)
  -- A sense is the unit a reader takes in at a glance, so the cut falls
  -- between two senses where it can, between two words where it cannot.
  local cut = fits(text, SENSE_SEPARATOR, budget)
           or fits(text, WORD_SEPARATOR, budget)
           or clip(text, budget)
  return (text:sub(1, cut):gsub(TRAILING_PUNCTUATION, "")) .. MARKER
end

-- How many characters a headword has, which is not how many bytes it has.
-- Continuation bytes are 10xxxxxx; every other byte starts a character.
local function character_count(text)
  local _, count = text:gsub("[^\128-\191]", "")
  return count
end

-- Which entry keeps an English headword that several entries claim. The old
-- rule - first entry to claim it keeps it - handed `abalone` to 鲍, a bound
-- character nobody types on its own, because it sits nine lines above 鲍鱼.
-- Two signals replace it, in this order:
--
--   1. An earlier sense beats a later one. An entry's first sense is its
--      primary meaning: `rime` is the whole of 雾凇 and an aside in 霜花.
--   2. A headword of more than one character beats a single character. A lone
--      Chinese character is often a bound morpheme; the free-standing word is
--      the one someone means. This is what moves `abalone` to 鲍鱼.
--
-- Ties keep the entry that came first, as the file order did.
--
-- Both signals were measured against 62 common English words with a known
-- answer. First-come scores 27; this scores 30. Nine words improve - `city`,
-- `eye`, `money`, `movie`, `music`, `friend`, `difficult`, and the `abalone`
-- and `rime` above - and six regress, all of them a common single character
-- displaced by a compound sharing its first sense: 电 to 电气, 火 to 火儿, 霜
-- to 霜冻, 山 to 山岳, 雨 to 雨水, 茶 to 茶叶. Nothing in the file separates
-- those from the cases the rule gets right; CC-CEDICT carries no word
-- frequency. Counting an entry's senses was tried as a third signal and
-- measured worse (25), as was ranking by sense count alone (27), so neither is
-- here.
--
-- The two signals are packed into one number so that the loader can compare
-- claims without allocating a table per English headword; higher wins.
local SENSE_LIMIT = 1024

local function claim_strength(word, position)
  if position > SENSE_LIMIT then
    position = SENSE_LIMIT
  end
  return (SENSE_LIMIT - position) * 2 + word
end

-- The longest line this loader will look at. Nothing in CC-CEDICT comes close:
-- the widest entry in the file published 2026-08 is 648 columns of gloss, and
-- the longest line of the whole file is under 2KB. So this is not a limit on
-- the dictionary, it is a bound the loader can state on its own work - the one
-- thing every pattern below depends on and none of them can check for itself.
-- A line past it is skipped, exactly as a malformed line is, which costs at
-- worst an entry nobody could have read anyway.
--
-- The size of the file is a different bound and belongs to whatever downloaded
-- it; this one holds however the file arrived, including a file the user put
-- there by hand.
local MAX_LINE_BYTES = 8192
M.MAX_LINE_BYTES = MAX_LINE_BYTES

function M.load_dictionary(path)
  local dictionary = { cn = {}, en = {} }
  local handle = io.open(path, "r")
  if not handle then
    return dictionary
  end
  -- How strong the claim on each English headword is. Only the loader needs
  -- it, so it is not part of the dictionary it returns.
  local strength = {}
  for line in handle:lines() do
    if line:sub(1, 1) ~= "#" and #line <= MAX_LINE_BYTES then
      local traditional, simplified, pinyin, senses = line:match(ENTRY)
      if traditional then
        -- 94% of entries are meanings and nothing else, so the two lesser
        -- buckets are only built for the entries that need them.
        local meanings, references, apparatus = {}, nil, nil
        -- One scan of the whole entry answers for all of its senses.
        local names_chinese = senses:find(CHINESE) ~= nil
        for sense in senses:gmatch("[^/]+") do
          local text = readable(sense)
          if text ~= "" then
            if is_apparatus(text) then
              apparatus = apparatus or {}
              apparatus[#apparatus + 1] = text
            elseif names_chinese and is_reference(text) then
              references = references or {}
              references[#references + 1] = text
            else
              meanings[#meanings + 1] = text
            end
          end
        end
        -- A cross-reference is a poor gloss and no gloss at all is a worse
        -- one, so an entry falls back to whatever it still has.
        local shown = meanings
        if #shown == 0 then shown = references or apparatus end
        if shown and #shown > 0 then
          local entry = {
            pinyin = pinyin,
            gloss = table.concat(shown, "; "),
            chinese = simplified,
          }
          dictionary.cn[traditional] = entry
          dictionary.cn[simplified] = entry
          -- Only meanings are English headwords: nobody types `CL:个` or
          -- `see 基友`. Upstream separates near-synonyms inside one sense with
          -- a semicolon - 你好's only sense is `hello; hi` - so a sense is
          -- several headwords rather than one.
          local word = character_count(simplified) > 1 and 1 or 0
          for position = 1, #meanings do
            local claim = claim_strength(word, position)
            for part in meanings[position]:gmatch("[^;]+") do
              -- `hello; hi` splits into ` hi`, so the space after a semicolon
              -- has to go; the byte test keeps that off the common path.
              local key = part
              if key:byte(1) <= 32 or key:byte(-1) <= 32 then
                key = key:gsub(SURROUNDING_SPACE, "%1")
              end
              if key ~= "" and (strength[key] == nil or claim > strength[key]) then
                dictionary.en[key] = entry
                strength[key] = claim
              end
            end
          end
        end
      end
    end
  end
  handle:close()
  return dictionary
end

local PINYIN_SEPARATOR = " · "

-- The pinyin is part of the comment, so it counts against the cap and the
-- gloss gets whatever columns it leaves. When it leaves less than the median
-- first sense is wide, the meaning stops being a meaning, and the pinyin is
-- dropped rather than the gloss squeezed: the pinyin repeats what was just
-- typed, while the gloss is the whole reason the comment is there. That is one
-- entry in 105, and only with pinyin turned on, which is not the default.
local MIN_GLOSS_WIDTH = 17

-- `chinese` and `english` say which directions to gloss. Both default to on so
-- that calling this with three arguments keeps its old meaning.
function M.gloss_for(dictionary, text, show_pinyin, chinese, english)
  if chinese == nil then chinese = true end
  if english == nil then english = true end
  local entry = chinese and dictionary.cn[text] or nil
  if entry then
    if show_pinyin and entry.pinyin ~= "" then
      local prefix = entry.pinyin .. PINYIN_SEPARATOR
      local budget = MAX_WIDTH - width(prefix)
      if budget >= MIN_GLOSS_WIDTH then
        return prefix .. M.truncate(entry.gloss, budget)
      end
    end
    return M.truncate(entry.gloss, MAX_WIDTH)
  end
  entry = english and dictionary.en[text] or nil
  if entry then
    -- A Chinese gloss on an English candidate is one headword rather than a
    -- list of senses: the widest in the file is 19 characters, so this cap
    -- has never yet had anything to do. It is applied all the same, so that
    -- the bound holds for whatever the comment turns out to be.
    return M.truncate(entry.chinese, MAX_WIDTH)
  end
  return nil
end

-- Defaults for a schema that registers the filter without configuring it: no
-- dictionary at all (which loads as empty), and no pinyin, matching the
-- plugin's own default. The gloss is what the feature is for; the pinyin is
-- already visible in what was just typed.
local NO_DICTIONARY = ""
local SHOW_PINYIN = false

-- Rime entry point, called once when the filter is created. The settings live in
-- the schema, so they are read from the schema's config rather than injected:
-- `annotate/dictionary` and `annotate/show_pinyin` are what omarchy.ime writes
-- into rime_ice.custom.yaml. Reading them here rather than in `func` keeps the
-- per-candidate path free of config lookups.
function M.init(env)
  local config = env.engine.schema.config
  -- get_string answers nil for a key the schema does not set.
  env.dictionary_path = config:get_string("annotate/dictionary") or NO_DICTIONARY
  local show_pinyin = config:get_bool("annotate/show_pinyin")
  -- An absent key reads as nil, which is not the same as a configured false, so
  -- this cannot be an `or` fallback the way the string above is.
  if show_pinyin == nil then
    show_pinyin = SHOW_PINYIN
  end
  env.show_pinyin = show_pinyin
  -- Which directions to gloss is a deploy-time setting rather than a runtime
  -- switch: the hotkey answers "show glosses now", and one key cannot toggle
  -- two switches, because Rime stops at the first binding that matches.
  local chinese = config:get_bool("annotate/chinese")
  local english = config:get_bool("annotate/english")
  env.annotate_chinese = chinese == nil or chinese
  env.annotate_english = english == nil or english
end

-- Rime entry point: librime-lua calls `func` on the table a lua_filter module
-- returns, the way corrector.lua and the other filters Rime Ice loads are
-- shaped. Kept thin: everything above is testable without Rime.
function M.func(input, env)
  if not env.engine.context:get_option("annotate") then
    for candidate in input:iter() do
      yield(candidate)
    end
    return
  end
  if env.dictionary == nil then
    env.dictionary = M.load_dictionary(env.dictionary_path)
  end
  for candidate in input:iter() do
    local gloss = M.gloss_for(
      env.dictionary, candidate.text, env.show_pinyin,
      env.annotate_chinese, env.annotate_english
    )
    if gloss then
      candidate:get_genuine().comment = gloss
    end
    yield(candidate)
  end
end

return M
