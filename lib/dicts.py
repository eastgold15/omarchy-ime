# lib/dicts.py
"""Extra vocabulary dictionaries ("细胞词库"): the catalogue and the wiring.

A cell dictionary is a plain Rime table - `词\\t全拼\\t权重` - that an existing
schema mounts beside its own dictionary, so selecting one needs no new schema:
every pinyin-family schema keeps its base table and gains the selected cells
through a generated wrapper dictionary.

The catalogue is the `cn_dicts_cell/` collection from rime-frost (白霜拼音),
fetched from the pinned source of the same name in lib/assets - by category
rather than as one blob, so the user picks 医药 without also taking 游戏. Every
id below was checked against the file's own `name:` field at the pinned
revision, because Rime requires the file name, the `name:` inside it, and the
entry in `import_tables` to be the same string.

Rime mounts a dictionary by replacing `translator/dictionary`, which names one
table - so the generated wrapper is what "base plus cells" means: it imports
the schema's own dictionary first, then the selected cells, and the schema's
custom patch points at the wrapper instead of the base. The base each schema
imports was read out of the packaged *.schema.yaml in /usr/share/rime-data;
the double-pinyin family genuinely uses rime_ice's table (their algebra maps
full pinyin onto double-pinyin codes), which is why full-pinyin cells work for
them unchanged.

Nothing here reaches schemas whose codes are not full pinyin: bopomofo types
zhuyin, cangjie types shapes, terra_pinyin_12345 types stroke counts, and the
Japanese schemas compose Japanese. A cell mounted on any of those would compile
and silently produce nothing - the same class of lie generate.TRADITIONALIZE_SCHEMAS
exists to refuse.
"""
from __future__ import annotations

# The pinned source (lib/assets.SOURCES) the cell dictionaries come from.
SOURCE = "rime-frost"

# id -> (中文名, 一句话说明). The id is the dictionary's own `name:` and file
# basename; the path inside the clone is derived from it in lib/assets.
CATALOG = (
    ("exthot", "网络热词", "互联网流行词和新词"),
    ("name2", "人名", "中文姓名"),
    ("place", "地名", "中国地名"),
    ("history", "历史", "历史人物与事件"),
    ("idiom", "成语", "成语和四字词语"),
    ("literature", "文学", "诗词与文学作品词汇"),
    ("game", "游戏", "游戏名称与术语"),
    ("food", "美食", "菜名与食品"),
    ("medication", "医药", "药品与医学术语"),
    ("sport", "体育", "运动项目与体育术语"),
    ("animal", "动物", "动物名称"),
    ("computer", "计算机", "计算机与互联网术语"),
    ("shulihua", "数理化", "数学、物理与化学术语"),
)

DICT_IDS = tuple(dict_id for dict_id, _label, _description in CATALOG)
_LABEL = {dict_id: label for dict_id, label, _description in CATALOG}
_DESCRIPTION = {dict_id: description for dict_id, _label, description in CATALOG}

# Path inside the pinned clone -> path relative to the Rime directory, in the
# shape lib/assets.SOURCE_FILES reads. The target name is the id itself because
# Rime requires the file name to equal the `name:` inside it.
SOURCE_FILES = {
    f"cn_dicts_cell/{dict_id}.dict.yaml": f"{dict_id}.dict.yaml"
    for dict_id in DICT_IDS
}

# The schemas whose translator reads full-pinyin tables, mapped to the base
# dictionary each one's packaged schema names - checked against
# /usr/share/rime-data. The luna family members with no translator section of
# their own (luna_quanpin, the simp/tw/fluency variants) inherit luna_pinyin's
# through YAML anchors, so they mount the same base.
#
# `use_preset_vocabulary` follows the base dictionary's own flag: luna_pinyin
# and terra_pinyin set it (the 八股文 preset vocabulary is part of how they
# rank), rime_ice does not. An import does not carry the flag across, so the
# wrapper has to restate it or a luna schema would quietly lose its ranking.
BASE_DICTIONARY = {
    "rime_ice": ("rime_ice", False),
    "double_pinyin": ("rime_ice", False),
    "double_pinyin_flypy": ("rime_ice", False),
    "double_pinyin_mspy": ("rime_ice", False),
    "double_pinyin_sogou": ("rime_ice", False),
    "double_pinyin_jiajia": ("rime_ice", False),
    "double_pinyin_ziguang": ("rime_ice", False),
    "double_pinyin_abc": ("rime_ice", False),
    "luna_pinyin": ("luna_pinyin", True),
    "luna_quanpin": ("luna_pinyin", True),
    "luna_pinyin_simp": ("luna_pinyin", True),
    "luna_pinyin_tw": ("luna_pinyin", True),
    "luna_pinyin_fluency": ("luna_pinyin", True),
    "terra_pinyin": ("terra_pinyin", True),
}

# The schemas a cell selection can reach. bopomofo, cangjie5, radical_pinyin,
# terra_pinyin_12345 and the Japanese two are absent on purpose: see the module
# docstring.
CELL_SCHEMAS = tuple(BASE_DICTIONARY)

# The wrapper's name - and therefore its file name, Rime requiring the two to
# agree - is derived from the schema, so a deploy for rime_ice and one for
# double_pinyin_flypy never share a file even though both import rime_ice.
WRAPPER_SUFFIX = "_cells"


def wrapper_name(schema: str) -> str:
    return f"{schema}{WRAPPER_SUFFIX}"


def options() -> list[dict]:
    """The catalogue in the shape a MultiSelect (and `dict list --json`) reads."""
    return [
        {"value": dict_id, "label": _LABEL[dict_id],
         "description": _DESCRIPTION[dict_id]}
        for dict_id in DICT_IDS
    ]


def selection(value) -> list:
    """Repair a dictionary selection the way schema_selection repairs schemas.

    Unknown ids are dropped rather than deployed: a typo here is an
    `import_tables` entry Rime cannot resolve, and an unbuildable table fails
    the schema that mounts it. What survives comes back in catalogue order, so
    the same selection always produces the same wrapper file. Unlike schemas an
    empty selection is fine - it simply means no cells, and the feature is off.
    """
    if not isinstance(value, list):
        return []
    wanted = {item for item in value if isinstance(item, str)}
    return [dict_id for dict_id in DICT_IDS if dict_id in wanted]


def render_wrapper(schema: str, dict_ids: list, header: str = "") -> str:
    """The `<schema>_cells.dict.yaml` body: the schema's base table plus cells.

    The base goes first so its entries keep their weight context; the cells
    follow in catalogue order. `header` is the generated-file warning, passed
    in by the caller (lib/generate) so this module never imports it.
    """
    base, preset = BASE_DICTIONARY[schema]
    lines = [
        "# Rime dictionary",
        "# encoding: utf-8",
        header.rstrip("\n"),
        "---",
        f"name: {wrapper_name(schema)}",
        'version: "1"',
        "sort: by_weight",
        f"use_preset_vocabulary: {'true' if preset else 'false'}",
        "import_tables:",
        f"  - {base}",
        *(f"  - {dict_id}" for dict_id in dict_ids),
        "...",
    ]
    return "\n".join(lines) + "\n"
