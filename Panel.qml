import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons
import qs.Ui

// The bar item this widget replaces is omarchy.keyboard-layout, so it shows the
// live input state rather than the keyboard layout: "EN" for a latin keystroke,
// a character or two of the Rime schema once one is composing, a muted mark
// when Fcitx5 is not there at all.
//
// The panel itself never touches the filesystem. Everything it reads and every
// effect it asks for goes through the plugin's own `ime-manager` command, so
// there is exactly one place to audit what this plugin does to a machine.
//
// The one exception is the bar's own poll, which talks to `fcitx5-remote` and
// gdbus directly - see `sampleInput` for why that reading may not go through
// Python.
Panel {
  id: root

  moduleName: "io.github.eastgold15.omarchy.ime"
  ipcTarget: "io.github.eastgold15.omarchy.ime"

  // Live input state. Two readings, from two places, with different rules.
  //
  // `inputState` is Fcitx5's latin/composing/closed state, and it belongs to
  // the input context of the focused window: the user's Fcitx5 runs with
  // ActiveByDefault=False and ShareInputState=No, so every program gets its
  // own and a fresh context starts in latin. `currentSchema` is Rime's, and
  // Rime has exactly one current schema for the whole session - no context
  // owns it. That difference is what `stateFrozen` below is about.
  property string inputState: "closed"
  property string currentSchema: ""

  // Attention state, from `ime-manager status --json`.
  property var packagesMissing: []
  property bool dictionaryReady: false
  // Whether anything would read the dictionary: false while candidate
  // annotation is off, which is the default, and then its absence is not a
  // fault. Read as true until the manager says otherwise.
  property bool dictionaryNeeded: true
  property bool deployed: false
  property bool statusLoaded: false

  // The two Hyprland pieces the plugin reports and never writes, each one of
  // "on", "off" or "unknown". They start at "unknown" because that is what is
  // true before the first report arrives, and because it is the one value that
  // claims nothing.
  property string hyprToggle: "unknown"
  property string hyprBlur: "unknown"

  // Each result string is paired with how the run that produced it ended. The
  // string alone cannot say that: "Deployed - 12 files installed" and "Deploy
  // failed: no such schema" are both just text, and both used to be painted the
  // same muted grey. The flag is what lets the line wear the right glyph and
  // the right palette role.
  property string deployResult: ""
  property bool deployOk: true
  property string installResult: ""
  property bool installOk: true

  // What a deploy said that was not a file path. `ime-manager` prints one
  // `target/relative` line per installed file, and everything else it prints is
  // the part the user cannot work out for themselves: a theme that could not be
  // read, so the candidate window has stopped following the desktop; a source
  // that could not be fetched, so a schema was withheld; a setting that reaches
  // only some of the selected schemas. All of that used to be filtered out and
  // dropped, and the panel showed a green tick and a count over the top of it.
  //
  // Two lists, because they are two different things. `deployWarnings` is what
  // went wrong, and wears the urgent role. `deployNotes` is what happened that
  // is worth knowing - so far, a rebuild whose outcome could not be verified.
  property var deployWarnings: []
  property var deployNotes: []

  // How many settings writes have been made. A deploy snapshots this when it
  // starts and compares on the way out: a value that was saved while the deploy
  // was running is not a value that deploy applied, and clearing `settingsDirty`
  // unconditionally reported it as applied anyway.
  property int settingsWrites: 0

  // The value `settingsWrites` had when the running deploy began.
  property int deployPending: 0

  // Settings as `ime-manager settings get --json` resolved them. The bar also
  // injects the raw entry into `settings`, but that copy has no defaults filled
  // in and arrives whenever the bar happens to reload shell.json, so the
  // manager's answer is the one the controls follow.
  property var settingValues: ({})
  property bool settingsLoaded: false
  property bool settingsDirty: false
  property string settingsResult: ""
  property bool settingsOk: true

  property string inputStateOutput: ""
  property string currentSchemaOutput: ""
  property string statusOutput: ""
  property string deployOutput: ""
  property string deployError: ""
  property string installOutput: ""
  property string installError: ""
  property string settingsGetOutput: ""
  property string settingsSetOutput: ""
  property string settingsSetError: ""
  property string pendingSettingLabel: ""

  // How many of the background image fields below are being typed into. A
  // count rather than a flag per field, because focus moving from one of the
  // two straight to the other fires two activeFocusChanged signals in no
  // guaranteed order: a flag cleared by the field being left could land after
  // the field being entered set it, and the key catcher would take keys back
  // from a field that is still typing.
  property int backgroundImageFocus: 0
  readonly property bool editingBackgroundImage: backgroundImageFocus > 0

  // The Rime schemas, from `ime-manager schema list --json`. A schema is a
  // different thing from the Fcitx5 input method the header reports: it is what
  // Rime's own F4 menu switches, and it is what decides whether a keystroke
  // becomes pinyin, romaji, or English.
  //
  // The list is read when the panel opens, after a switch, after a deploy, and
  // on the refresh key - never polled. Learning of an F4 switch a moment late
  // costs nothing, and two D-Bus round trips every two seconds would buy that
  // nothing dearly.
  property var schemas: []
  property bool schemasLoaded: false
  property string schemaResult: ""
  property bool schemaOk: true
  property string pendingSchema: ""
  property string schemaListOutput: ""
  property string schemaSetError: ""

  // Label, description, default, and group for each on/off setting. Held as a
  // constant so a value change repaints the switches instead of rebuilding the
  // rows under the pointer.
  //
  // `group` is which block of the settings section the row is laid out in, and
  // the blocks are split by how often a person touches them rather than by the
  // type of the value behind them. "settings" is the everyday half, which is
  // always on screen and needs no header of its own beyond SETTINGS. The rest
  // sit behind the advanced disclosure, each under the PanelSectionHeader named
  // after the choice being made; the number and text fields join the group they
  // belong to, so "candidates per page" sits with the candidate window and the
  // hotkey sits with the glosses it toggles.
  //
  // A description is here only when it says something the label cannot. Three
  // of the five carried a sentence that restated their own label - "Show what a
  // Chinese candidate means in English" under "English gloss on Chinese
  // candidates" - and a row of those teaches the reader to stop reading the
  // ones that matter. What is left is the facts a label has no room for:
  // which conversion profile the traditional switch uses, where the pinyin
  // lands, and what the annotation switch costs to turn on.
  //
  // `requires` names the switch a row answers to. The whole annotation
  // feature - the filter, the dictionary download, the patch in every Chinese
  // schema - hangs off one master switch, off by default; the rows that only
  // mean something while it is on keep their values but take no clicks until
  // it is. The master switch itself is an everyday setting, because it is the
  // one a person reaches for.
  readonly property var booleanSettings: [
    {
      key: "annotation",
      group: "settings",
      fallback: false,
      label: "候选词注释",
      description: "默认关闭。开启会下载 CC-CEDICT 词典用于释义。"
    },
    {
      key: "annotateChinese",
      group: "settings",
      fallback: true,
      requires: "annotation",
      label: "中文候选词标英文",
      description: ""
    },
    {
      key: "annotateEnglish",
      group: "settings",
      fallback: true,
      requires: "annotation",
      label: "英文候选词标中文",
      description: ""
    },
    {
      key: "traditionalize",
      group: "settings",
      fallback: true,
      label: "繁体输出",
      description: "使用 s2twp 方案转换。"
    },
    {
      key: "annotateShowPinyin",
      group: "annotations",
      fallback: false,
      requires: "annotation",
      label: "释义中带拼音",
      description: "加在释义之前。"
    },
    {
      key: "verticalCandidates",
      group: "candidates",
      fallback: true,
      label: "候选词竖排",
      description: ""
    },
    {
      key: "qtImModule",
      group: "candidates",
      fallback: true,
      label: "XWayland Qt 应用的输入法",
      description: "设置 QT_IM_MODULE=fcitx，让微信等应用能输入中文。下次登录生效。"
    }
  ]

  readonly property var everydaySettings: booleanSettings.filter(function(entry) {
    return entry.group === "settings"
  })
  readonly property var annotationSettings: booleanSettings.filter(function(entry) {
    return entry.group === "annotations"
  })
  readonly property var candidateSettings: booleanSettings.filter(function(entry) {
    return entry.group === "candidates"
  })

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string pluginDir: decodeURIComponent(
    String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  )
  readonly property string managerPath: pluginDir + "/ime-manager"

  // Settable, the way the sibling translation plugin's panel is: the same
  // `panelWidth` integer on the widget's own entry, with the same bounded read
  // so a hand-edited shell.json cannot produce a panel narrower than its own
  // controls. The default is measured rather than chosen - see
  // lib/settings.DEFAULT_PANEL_WIDTH, which is the same number and carries the
  // reasoning. It is read straight from the entry rather than through
  // `settingValues`, because the panel has to have a width before the manager
  // has answered.
  readonly property int panelWidth: boundedInt(setting("panelWidth", 420), 400, 720)

  // The one mark this panel draws, in one size. Both the status rows and the
  // active schema row wear it, so its diameter, its gap and the glyph inside it
  // are tokens here rather than numbers repeated at each site: a mark that is
  // 20px in one row and 18px in another is exactly the wandering the column was
  // reported for.
  readonly property int markDiameter: Style.space(20)
  readonly property int markGap: Style.space(10)
  // Omarchy's Style.qml has spacing, colour, radius and font tokens but no
  // animation tokens, so the timing below is matched to the shell's own
  // components rather than invented here. 140ms on an OutCubic curve is what
  // Ui/KeyboardPanel and Ui/PopupCard fade their cards with, what Ui/PanelSlider
  // and Ui/WidgetButton move with, and what the built-in network panel collapses
  // its band section with - so anything this panel grows or fades lasts exactly
  // as long as the surface it is growing inside.
  readonly property int revealDuration: 140
  readonly property bool busy: deployProcess.running || installProcess.running
  readonly property bool writingSetting: settingsSetProcess.running

  // Whether the settings controls accept input. A deploy takes eight and a half
  // seconds at its very fastest and far longer on a first run, and every
  // control was gated on `writingSetting` alone - so a switch flipped while one
  // was running wrote to shell.json, the deploy that was already reading the
  // old value finished, and the footer collapsed reporting success for a value
  // that was never applied.
  readonly property bool settingsLocked: writingSetting || busy
  readonly property bool switchingSchema: schemaSetProcess.running
  readonly property int missingCount: packagesMissing instanceof Array ? packagesMissing.length : 0

  // Whether the pinned footer has anything to say. Both halves belong to it:
  // the button, which is what a pending change needs, and the line reporting
  // the write that produced it - including a refused one, which is the case
  // where there is no pending change and still something the user has to be
  // told.
  readonly property bool footerShown: settingsDirty || settingsResult !== ""

  // Whether the advanced half of the panel is showing. This is panel state and
  // not a setting: it is false on every open, so the panel a user meets is
  // always the short one, and nothing about it is written to shell.json. What
  // is behind it is everything chosen once - which schemas get deployed, the
  // candidate window's shape, the background images - plus the status report,
  // which is read once after an install and never again.
  property bool advancedShown: false

  // A reading of Fcitx5's state taken while this panel holds the keyboard is a
  // reading of *this panel's* input context, not of the window the user came
  // from. With ShareInputState=No every surface gets its own context and a new
  // one starts in latin, so an open panel would always report "EN" no matter
  // what the user was typing a moment ago - and the old poll made that certain
  // rather than likely by sampling faster while open.
  //
  // So it does not sample at all while open. The last pre-open reading stands,
  // which is the state of the window the user came from, which is the answer
  // they are looking at the panel to get. Rime's current schema is not frozen
  // with it: no input context owns it, focus cannot change it, and the ACTIVE
  // SCHEMA section below has to keep up with clicks.
  readonly property bool stateFrozen: opened

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The bar needs two things at startup and asks for each exactly once.
  //
  // The input state is the cheap one, and it is asked for again on every poll.
  // The schema list is the expensive one - it is the only Python this bar ever
  // runs - and it is asked for once because it is a map from schema id to the
  // name Rime compiled into that schema, and nothing but a deploy can change
  // which schemas exist. From here on the bar polls the current id over D-Bus
  // and looks its name up in this map.
  //
  // The status report shells out to Omarchy's package commands, so it waits
  // until the panel is actually open and asking for it.
  Component.onCompleted: {
    sampleInput()
    refreshSchemas()
  }

  // A whole number from the widget's entry, clamped to what the panel can
  // actually lay out. Same shape as the sibling translation plugin's, because
  // it is the same job: shell.json is hand-editable, so the read has to be.
  function boundedInt(value, minimum, maximum) {
    var parsed = parseInt(String(value), 10)
    if (!isFinite(parsed)) parsed = minimum
    return Math.max(minimum, Math.min(maximum, parsed))
  }

  // What the bar paints. Kept to a couple of glyphs so it sits in the same
  // slot the keyboard-layout indicator used.
  function barLabel() {
    if (inputState === "latin") return "EN"
    if (inputState === "active") return schemaLabel()
    return "󰌌"
  }

  // Whether a character carries a whole word on its own, the way a latin
  // letter does not. One ideograph or one kana says as much in the bar's two
  // cells as two letters do, so the label takes one of those and two of
  // anything else.
  //
  // The ranges are written out rather than hidden inside a regex so each one
  // can be read for what it is:
  //   3040-30FF   hiragana and katakana, for a Japanese schema's name
  //   3400-4DBF   CJK unified ideographs, extension A
  //   4E00-9FFF   CJK unified ideographs, where every Chinese schema name lives
  //   F900-FAFF   CJK compatibility ideographs
  //   20000-3FFFF the supplementary ideograph planes, extension B onwards
  // Hangul is deliberately absent: this plugin deploys no Korean schema, and a
  // range nothing can reach is a claim the code cannot back up.
  readonly property var cjkRanges: [
    [0x3040, 0x30FF],
    [0x3400, 0x4DBF],
    [0x4E00, 0x9FFF],
    [0xF900, 0xFAFF],
    [0x20000, 0x3FFFF]
  ]

  function isCjk(codePoint) {
    for (var index = 0; index < cjkRanges.length; index++) {
      if (codePoint >= cjkRanges[index][0] && codePoint <= cjkRanges[index][1]) return true
    }
    return false
  }

  // The readable name Rime compiled into the current schema, which is what the
  // panel already asks `ime-manager schema list --json` for. Two fallbacks, in
  // the order of how much they still say: the schema's own id, which is what
  // Rime calls it and is honest when the name map has not been read yet, and
  // then the empty string, which only happens when Rime has not named a schema
  // at all.
  function schemaName(id) {
    var wanted = String(id || "")
    if (wanted === "") return ""
    for (var index = 0; index < schemas.length; index++) {
      if (String(schemas[index].id) === wanted) return String(schemas[index].name || wanted)
    }
    return wanted
  }

  // The bar's label while a schema is composing: the schema's own name, cut to
  // what fits beside a clock. A Chinese name becomes its first ideograph, and
  // everything else its first two letters - "Japanese" becomes Ja, "Mixed
  // ZH-JA-EN" becomes Mi. The rule is the same for all of them, and no name
  // gets a special case to make it prettier.
  //
  // "IM" when there is no name to cut. It is the honest answer: something is
  // composing, and this panel cannot say what.
  function schemaLabel() {
    var name = schemaName(currentSchema).trim()
    if (name === "") return "IM"
    var first = name.codePointAt(0)
    if (isCjk(first)) return String.fromCodePoint(first)
    return name.charAt(0).toUpperCase() + name.charAt(1)
  }

  // The panel has room for the whole name where the bar has room for one or
  // two characters, so it spends it.
  function schemaTitle() {
    var name = schemaName(currentSchema).trim()
    return name === "" ? "组字中" : name
  }

  function stateLabel() {
    if (inputState === "latin") return "英文"
    if (inputState === "active") return schemaTitle()
    return "已关闭"
  }

  // The hero's meta line, which elides rather than wraps, so it has to say its
  // whole piece inside one line of a panel this wide.
  function stateDescription() {
    if (inputState === "latin") return "按键以拉丁字母直接通过"
    if (inputState === "active") return schemaTitle() + " 正在组字"
    return "Fcitx5 未运行"
  }

  function stateColor() {
    if (inputState === "active") return Color.accent
    if (inputState === "latin") return root.foreground
    return root.dim
  }

  function barTooltip() {
    if (inputState === "latin") return "输入法 · 英文"
    if (inputState === "active") return "输入法 · " + schemaTitle()
    return "输入法 · 已关闭"
  }

  // The shell's own yes/no pair, so this panel's marks read as the rest of
  // Omarchy's. dev-gallery pairs 󰄬 (confirm) with 󰅙 in the urgent role
  // (refuse) on adjacent buttons, and the network panel forgets a saved network
  // with the same 󰅙. This file already marks the active schema with 󰄬, so
  // the plain "✓" the status rows used was a second, differently drawn check
  // for the same idea - one panel should not have two checkmarks.
  function stateGlyph(ok) {
    return ok ? "󰄬" : "󰅙"
  }

  // Status is the one place the panel gets to say "this part is working", so it
  // is where the theme's accent earns its keep. A settled row wears the accent
  // role, a row that needs attention wears the urgent one; both are palette
  // roles, so a theme swap repaints them without touching this file. The glyph
  // on a result line takes the same two roles for the same reason.
  function rowColor(ok, optional) {
    if (ok) return Color.accent
    // A row reporting an optional enhancement is not a row reporting a fault,
    // and painting it urgent would send someone to fix a machine that is fine.
    // It keeps the panel's own two glyphs and gains no colour of its own: it
    // steps back to the muted role the detail lines already wear.
    return optional ? root.dim : root.urgent
  }

  // One detail line for a piece of the setup that is optional and not this
  // plugin's to write. Three answers, and only the first is "in effect": the
  // other two are told apart here rather than by the mark, which keeps the
  // panel's two glyphs and says the same thing about both - this is not on.
  function hyprDetail(state, absent) {
    if (state === "on") return "在你的 Hyprland 配置中 · 插件从不写入它"
    if (state === "off") return absent
    return "无法确定 · Hyprland 没有回答"
  }

  // What the status report says, row by row. The first three are this plugin's
  // own doing and a failure in one is a failure; the last two are the pieces of
  // a working input method that live in the compositor's configuration, which
  // is the user's territory - so they are reported, never written, and a row
  // that is merely absent wears the muted mark rather than the urgent one.
  function statusRows() {
    if (!statusLoaded) return []
    return [
      {
        id: "packages",
        label: "软件包",
        ok: missingCount === 0,
        // A settled row says nothing the mark has not already said: naming the
        // three packages back at someone who can see they are installed is not
        // news. A row that needs attention still names what is missing, because
        // that is the part they cannot see.
        detail: missingCount === 0
          ? ""
          : "缺失 " + missingCount + " 个 · " + packagesMissing.join(", ")
      },
      {
        id: "dictionary",
        label: "词典",
        // Not wanted is not missing. With annotation off - the default -
        // nothing reads the dictionary, so its absence is nothing to fix.
        ok: dictionaryReady || !dictionaryNeeded,
        detail: !dictionaryNeeded
          ? "候选词注释关闭时不需要"
          : dictionaryReady
            ? ""
            : "下次部署时下载"
      },
      {
        id: "deploy",
        label: "配置",
        ok: deployed,
        // The one settled row that keeps a sentence, because it is a warning
        // and not a status: whatever is on disk is rewritten from the settings
        // in this panel, so a hand edit to it does not survive a deploy.
        detail: deployed
          ? "部署会用上面的设置重写它"
          : "从未部署 · 磁盘上还没有任何内容"
      },
      {
        id: "hyprToggle",
        label: "输入法切换键",
        ok: hyprToggle === "on",
        optional: true,
        detail: hyprDetail(
          hyprToggle,
          "没有切换 Fcitx5 的键 · README 里有示例代码"
        )
      },
      {
        id: "hyprBlur",
        label: "候选词窗口模糊",
        ok: hyprBlur === "on",
        optional: true,
        detail: hyprDetail(
          hyprBlur,
          "候选词窗口没有模糊 · README 里有示例代码"
        )
      }
    ]
  }

  // Settings live on this widget's own entry in shell.json. The panel reads
  // them through `ime-manager settings get` and changes them through
  // `ime-manager settings set`, so the controls below are the same write path a
  // hand edit takes - validation included.
  function settingValue(key, fallback) {
    if (settingValues && settingValues[key] !== undefined && settingValues[key] !== null) {
      return settingValues[key]
    }
    return setting(key, fallback)
  }

  // QML only notices a `var` property changed when it is reassigned, so a
  // single new value means a fresh object rather than a mutation in place.
  function withSetting(key, value) {
    var next = {}
    for (var name in settingValues) next[name] = settingValues[name]
    next[key] = value
    return next
  }

  function writeSetting(key, value, label) {
    if (settingsLocked) return
    settingsWrites += 1
    settingsSetOutput = ""
    settingsSetError = ""
    settingsResult = ""
    settingsOk = true
    pendingSettingLabel = String(label || key)
    // Applied locally first so the switch throws at once; a refused write
    // reloads the real values and the control snaps back to the truth.
    settingValues = withSetting(key, value)
    settingsSetProcess.command = [
      root.managerPath, "settings", "set", "--json", String(key) + "=" + String(value)
    ]
    settingsSetProcess.running = true
  }

  // One reading of everything the bar paints, with Python nowhere in it.
  //
  // `ime-manager state --json` spends 64ms of CPU, nearly all of it Python
  // interpreter startup, to print three fields; `fcitx5-remote` is the same
  // question asked of the same daemon and answers for 1.1ms, and gdbus asks
  // Rime for the current schema for 1.7ms. Polled forever, that is the
  // difference between about 1.3% of a core and about 0.04% - to paint two
  // glyphs.
  //
  // The schema is only asked for when it is on screen: the bar shows it only
  // while composing, and the panel's ACTIVE SCHEMA section only while open. A
  // latin keystroke costs one subprocess and nothing else.
  function sampleInput() {
    if (!stateFrozen) refreshInputState()
    if (opened || inputState === "active") refreshCurrentSchema()
  }

  function refreshInputState() {
    if (inputStateProcess.running) return
    inputStateOutput = ""
    inputStateProcess.running = true
  }

  function refreshCurrentSchema() {
    if (currentSchemaProcess.running) return
    currentSchemaOutput = ""
    currentSchemaProcess.running = true
  }

  function refreshStatus() {
    if (statusProcess.running) return
    statusOutput = ""
    statusProcess.running = true
  }

  function refreshSettings() {
    if (settingsGetProcess.running) return
    settingsGetOutput = ""
    settingsGetProcess.running = true
  }

  function refreshSchemas() {
    if (schemaListProcess.running) return
    schemaListOutput = ""
    schemaListProcess.running = true
  }

  // Everything the panel refreshes at once: the refresh button, the refresh
  // key, and opening the panel all want the same set, so they ask for it here
  // rather than each keeping their own list in step.
  function refreshEverything() {
    sampleInput()
    refreshStatus()
    refreshSettings()
    refreshSchemas()
  }

  // Switching is immediate and is not a setting: nothing is written to
  // shell.json and no deploy is needed, because this is the live Rime session
  // being told to change schema.
  function selectSchema(id, label) {
    if (switchingSchema) return
    schemaResult = ""
    schemaOk = true
    schemaSetError = ""
    pendingSchema = String(label || id)
    schemaSetProcess.command = [root.managerPath, "schema", "set", String(id)]
    schemaSetProcess.running = true
  }

  function installPackages() {
    if (busy || missingCount === 0) return
    installOutput = ""
    installError = ""
    installResult = ""
    installOk = true
    installProcess.running = true
  }

  function deploy() {
    // Not while a settings write is in flight either: it is the value about to
    // land in shell.json that this deploy is supposed to be applying.
    if (busy || writingSetting) return
    deployOutput = ""
    deployError = ""
    deployResult = ""
    deployOk = true
    deployWarnings = []
    deployNotes = []
    deployPending = settingsWrites
    deployProcess.running = true
  }

  function openSettings() {
    Quickshell.execDetached(["omarchy-launch-editor", Quickshell.env("HOME") + "/.config/omarchy/shell.json"])
  }

  // Opening reads everything the panel shows and puts the advanced half away,
  // so the panel always opens at the same short height. Closing takes one
  // reading straight away rather than waiting out the timer: the freeze has
  // just lifted, and whatever the bar was showing is now up to a poll old.
  onOpenedChanged: {
    if (opened) {
      advancedShown = false
      refreshEverything()
    } else {
      sampleInput()
    }
  }

  // The bar re-injects this entry whenever shell.json changes on disk, so a
  // hand edit through the escape hatch lands in the controls without a reopen.
  onSettingsChanged: if (opened) refreshSettings()

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barLabel()
    fontSize: Style.font.caption
    horizontalMargin: 6
    dimmed: root.inputState === "closed"
    tooltipText: root.barTooltip()
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.sampleInput()
      else root.toggle()
    }
  }

  // Fcitx5's own state, asked for directly. `fcitx5-remote` prints 0, 1 or 2 -
  // closed, latin, composing - and fails outright when Fcitx5 is not running,
  // which is "closed" as well. The mapping is lib/imestate.STATES; both places
  // spell it out so neither can drift silently.
  Process {
    id: inputStateProcess
    command: ["fcitx5-remote"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.inputStateOutput = String(text || "")
    }
    onExited: function(exitCode) {
      var code = exitCode === 0 ? root.inputStateOutput.trim() : ""
      root.inputState = code === "2" ? "active" : (code === "1" ? "latin" : "closed")
    }
  }

  // Rime's current schema, asked for directly. This is the same
  // GetCurrentSchema lib/imestate.py calls, on the same object Rime's own F4
  // menu operates, so the bar and the F4 menu cannot disagree.
  Process {
    id: currentSchemaProcess
    command: [
      "gdbus", "call", "--session",
      "--dest", "org.fcitx.Fcitx5",
      "--object-path", "/rime",
      "--method", "org.fcitx.Fcitx.Rime1.GetCurrentSchema"
    ]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.currentSchemaOutput = String(text || "")
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.currentSchema = ""
        return
      }
      // gdbus prints the answer as a one-element GVariant tuple: ('rime_ice',).
      // A schema id names a file in Rime's build directory and is held to the
      // same alphabet lib/imestate._SCHEMA_ID holds it to, so the id can be
      // lifted out by matching that alphabet rather than by writing a GVariant
      // parser in QML. Anything else means the answer was not the shape that
      // was asked for, and no schema is the honest reading of that.
      var match = root.currentSchemaOutput.trim().match(/^\('([A-Za-z0-9][A-Za-z0-9_.-]*)',\)$/)
      root.currentSchema = match ? match[1] : ""
    }
  }

  Process {
    id: statusProcess
    command: [root.managerPath, "status", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.statusOutput = String(text || "")
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 || root.statusOutput === "") {
        // The rows keep whatever the last good report said rather than
        // emptying: a status that could not be read is not a status of "no".
        return
      }
      try {
        var report = JSON.parse(root.statusOutput)
        root.packagesMissing = report.packages_missing instanceof Array ? report.packages_missing : []
        root.dictionaryReady = report.dictionary === true
        // Absent from an older manager's report reads as needed, which is the
        // stricter of the two answers.
        root.dictionaryNeeded = report.dictionary_needed !== false
        root.deployed = report.deployed === true
        var hypr = report.hypr instanceof Object ? report.hypr : {}
        // Anything that is not one of the three known answers is read as
        // "unknown", which is what a report this panel cannot understand
        // honestly amounts to.
        root.hyprToggle = hypr.toggle === "on" || hypr.toggle === "off"
          ? hypr.toggle : "unknown"
        root.hyprBlur = hypr.blur === "on" || hypr.blur === "off"
          ? hypr.blur : "unknown"
        // `status --json` carries an input reading too, and this panel ignores
        // it. The report is only ever asked for while the panel is open, which
        // is exactly when a fresh reading describes the panel's own input
        // context instead of the user's window - taking it here would undo the
        // freeze through the back door.
        root.statusLoaded = true
      } catch (error) {
        // Same as above: an unreadable answer leaves the last good one standing.
      }
    }
  }

  Process {
    id: settingsGetProcess
    command: [root.managerPath, "settings", "get", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.settingsGetOutput = String(text || "")
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 || root.settingsGetOutput === "") return
      try {
        root.settingValues = JSON.parse(root.settingsGetOutput)
        root.settingsLoaded = true
      } catch (error) {
        root.settingsOk = false
        root.settingsResult = "无法读取设置"
      }
    }
  }

  // One setting per run: the command is rebuilt for each change, and the
  // resolved settings it prints back are what the controls then show.
  Process {
    id: settingsSetProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.settingsSetOutput = String(text || "")
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.settingsSetError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.settingsOk = false
        root.settingsResult = root.settingsSetError || "无法更改设置"
        root.refreshSettings()
        return
      }
      try {
        root.settingValues = JSON.parse(root.settingsSetOutput)
        root.settingsLoaded = true
      } catch (error) {
        root.refreshSettings()
      }
      root.settingsDirty = true
      root.settingsOk = true
      root.settingsResult = "已保存「" + root.pendingSettingLabel + "」· 部署后生效"
    }
  }

  Process {
    id: schemaListProcess
    command: [root.managerPath, "schema", "list", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.schemaListOutput = String(text || "")
    }
    onExited: function(exitCode) {
      // An empty list is the honest answer when Fcitx5 is not running with the
      // Rime addon, so it is kept rather than treated as a read that failed.
      if (exitCode !== 0 || root.schemaListOutput === "") {
        root.schemas = []
        root.schemasLoaded = true
        return
      }
      try {
        var listed = JSON.parse(root.schemaListOutput)
        root.schemas = listed instanceof Array ? listed : []
      } catch (error) {
        root.schemas = []
      }
      // The list also says which schema is current, and that is the one field
      // the cheap poll reads on its own. Taking it here means the bar has a
      // name and an id from the first read at startup, without waiting for the
      // first D-Bus round trip.
      for (var index = 0; index < root.schemas.length; index++) {
        if (root.schemas[index].current === true) {
          root.currentSchema = String(root.schemas[index].id)
          break
        }
      }
      root.schemasLoaded = true
    }
  }

  // One switch per run: the command is rebuilt for each click, and the list is
  // re-read afterwards so the mark follows what Rime actually did rather than
  // what was asked for.
  Process {
    id: schemaSetProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.schemaSetError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      root.schemaOk = exitCode === 0
      root.schemaResult = exitCode === 0
        ? "已切换到 " + root.pendingSchema
        : (root.schemaSetError || "无法切换输入方案")
      root.refreshSchemas()
      root.refreshCurrentSchema()
    }
  }

  Process {
    id: installProcess
    command: [root.managerPath, "packages", "install"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.installOutput = String(text || "").trim()
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.installError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      root.installOk = exitCode === 0
      // The manager's own sentence, both ways. It is the only thing that knows
      // what happened: on the way out it says a terminal has been opened to run
      // the install in - which is where sudo can ask for a password, and where
      // this panel cannot - and on the way in it carries pacman's or the
      // launcher's stderr. Both used to be captured and dropped, which left
      // "Package installation failed" with no package, no reason and no next
      // step.
      root.installResult = exitCode === 0
        ? (root.installOutput || "软件包已安装")
        : (root.installError || root.installOutput || "软件包安装失败")
      root.refreshStatus()
    }
  }

  // Deploy prints one "target/relative" line per installed file. It prints
  // other lines too - which Omarchy theme the candidate window followed, a
  // setting the selection could not reach, that the environment override waits
  // for the next login, and "rime: rebuilt" - so the file count is the lines
  // that look like an installed path and not every line there was.
  Process {
    id: deployProcess
    command: [root.managerPath, "deploy"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.deployOutput = String(text || "")
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.deployError = String(text || "").trim()
    }
    onExited: function(exitCode) {
      // Everything the deploy printed, sorted into what it wrote, what went
      // wrong, and what is worth knowing. The middle pile used to be thrown
      // away: `warning: ` lines carry the only notice a user ever gets that the
      // candidate window has stopped following their theme, or that a schema
      // was withheld because its tables could not be fetched.
      var paths = []
      var warnings = []
      var notes = []
      var printed = root.deployOutput.split("\n")
      for (var index = 0; index < printed.length; index++) {
        var line = printed[index].trim()
        if (line === "") continue
        if (line.indexOf("warning: ") === 0) {
          warnings.push(line.substring(9))
        } else if (/^[A-Za-z0-9_.-]+\/\S/.test(line)) {
          paths.push(line)
        } else if (line.indexOf("rime: ") === 0 && line !== "rime: rebuilt") {
          // A rebuild that was triggered but could not be confirmed - no
          // journal to read, or fcitx5 not running yet. Not a failure, and not
          // something to paint over with a tick either.
          notes.push(line.substring(6))
        }
      }
      root.deployWarnings = warnings
      root.deployNotes = notes

      if (exitCode !== 0) {
        root.deployOk = false
        root.deployResult = root.deployError || "部署失败"
        return
      }
      root.deployOk = true
      // The environment override is the one deployed file systemd does not
      // read until the next session starts, so a deploy that installed it has
      // done something the user cannot see yet. Saying so is what stops them
      // concluding it did not work - and it is said only when that file was
      // actually one of the paths the deploy reported.
      var login = paths.some(function(line) {
        return line.indexOf("environment/") === 0
      })
      root.deployResult = (paths.length === 0
        ? "已部署 · 没有需要写入的内容"
        : "已部署 · 安装了 " + paths.length + " 个文件")
        + (login ? " · QT_IM_MODULE 覆盖在下次登录时生效" : "")
        + (warnings.length === 0
            ? ""
            : " · " + warnings.length + " 条警告")
      // Only the changes this deploy actually read are reported as applied. A
      // write that landed after it started is still pending, and the footer
      // stays up saying so.
      if (root.settingsWrites === root.deployPending) {
        root.settingsDirty = false
        root.settingsResult = ""
        root.settingsOk = true
      }
      root.refreshStatus()
      root.sampleInput()
      // A deploy is what decides which schemas exist at all, so the id-to-name
      // map the bar reads from is stale the moment one finishes.
      root.refreshSchemas()
    }
  }

  // Focus is the event, and Hyprland is where it comes from.
  //
  // There is no subscribable event for the input state itself. Fcitx5's
  // Controller1 only announces group changes, its InputContext1 signals are
  // unicast to the client owning that context so a third party never sees
  // them, and the channel built for indicators - kimpanel - hands the
  // candidate UI to whoever registers, which would throw away the classicui
  // theme this plugin exists to generate. So the state has to be asked for.
  //
  // But the thing that changes it most often is not the state, it is which
  // window is focused: with ShareInputState=No, every window carries its own,
  // and switching windows changes what the bar should say without anything in
  // Fcitx5 changing at all. That, Hyprland does announce. Subscribing to it is
  // what lets the timer below be slow.
  Connections {
    target: Hyprland

    function onRawEvent(event) {
      if (!event || !event.name) return
      var name = String(event.name)
      // activewindow and activewindowv2 are the same focus change described
      // twice, and either one alone is enough; taking both costs a duplicate
      // reading that `sampleInput` drops, and misses nothing on a Hyprland
      // that only emits one of them. focusedmon is the same change made by
      // moving to another output.
      if (name === "activewindow" || name === "activewindowv2" || name === "focusedmon") {
        root.sampleInput()
      }
    }
  }

  // What is left for a timer, now that focus arrives as an event, is the one
  // switch that announces nothing at all: the user pressing Ctrl+Space inside
  // the window they are already in. Fcitx5 emits nothing a third party can
  // hear, so it can only be found by looking.
  //
  // Three seconds. It is a change the user made themselves, with the candidate
  // window appearing in front of them to confirm it, so the bar is not how
  // they learn about it - it only has to have caught up by the time they
  // glance up at it. At 1.1ms a reading that is 0.04% of a core; the poll it
  // replaces spent 64ms every five seconds, 1.3%, and every two seconds while
  // the panel was open, 3.2%. One second would still be cheaper than what was
  // here before, and would be buying reaction time nobody is waiting on.
  Timer {
    interval: 3000
    repeat: true
    running: true
    onTriggered: root.sampleInput()
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(root.panelWidth))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(720))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // The catcher takes keys before any descendant, so a panel with an
      // editor in it has to stand aside while that editor is typing. All of
      // them here are text: the hotkey field, the schema picker's search box,
      // and the two background image paths - without this, searching for
      // "radical" would fire the panel's own r, d and i shortcuts and deploy
      // the configuration mid-word, and typing a path would open the editor on
      // its first "e".
      blocked: hotkeyField.activeFocus || schemaPicker.popupOpen
        || root.editingBackgroundImage
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") root.refreshEverything()
        else if (text === "d" || text === "D") root.deploy()
        else if (text === "i" || text === "I") root.installPackages()
        else if (text === "e" || text === "E") root.openSettings()
        else if (text === "a" || text === "A") root.advancedShown = !root.advancedShown
      }

      // The footer below overlays this rather than sitting beside it, so the
      // scroll area keeps the whole panel and reserves the footer's height at
      // the end of its content instead. Anchoring the Flickable's bottom to a
      // footer that is animating its own height would re-lay out the entire
      // column on every frame of the reveal, and would still leave the last row
      // unreachable at the moment the footer arrives.
      Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight + footer.height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: contentColumn
          width: parent.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "输入法"
            meta: root.stateDescription()
            detail: root.stateLabel()
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Item {
                implicitWidth: Style.font.display
                implicitHeight: Style.font.display

                Text {
                  anchors.centerIn: parent
                  text: "󰌌"
                  color: root.stateColor()
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display

                  // The one live reading on this panel. A deploy, or Fcitx5
                  // coming up or going away underneath, moves this glyph
                  // between the dim, foreground and accent roles, and a state
                  // that snaps between two colours is easy to miss. Gated the
                  // way Ui/WidgetButton gates its own foreground fade: part of
                  // this colour comes from the bar, and the bar drops that flag
                  // while it recomputes its foreground, so honouring it keeps
                  // the glyph from fading through a colour that was never a
                  // state. 160ms is WidgetButton's own figure.
                  Behavior on color {
                    enabled: !root.bar || root.bar.foregroundAnimationEnabled
                    ColorAnimation { duration: 160 }
                  }
                }
              }
            }
          }

          // The panel's most important state, and the one place it can be seen.
          //
          // "Configuration ✗ Never deployed" lives in STATUS, which is inside a
          // disclosure that `onOpenedChanged` forces shut on every open - so a
          // user who has just installed the plugin met three switches and an
          // unremarkable Deploy button, with nothing anywhere saying that
          // nothing had been written yet. The disclosure itself stays: it
          // exists because this panel was too tall. What comes out from behind
          // it is the one line that changes what the user should do next.
          //
          // It is not a duplicate of the STATUS row. That row is the standing
          // fact; this is the instruction, and it disappears for good the
          // moment the first deploy lands.
          Rectangle {
            width: parent.width
            visible: root.statusLoaded && !root.deployed
            height: visible ? firstRun.implicitHeight + Style.spacing.lg * 2 : 0
            radius: Style.cornerRadius
            color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.10)

            Column {
              id: firstRun
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.spacing.lg
              anchors.rightMargin: Style.spacing.lg
              spacing: Style.spacing.xs

              Text {
                width: parent.width
                text: "󰆓  还没有部署任何内容"
                color: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Text {
                width: parent.width
                text: root.missingCount > 0
                  ? "先安装缺失的软件包，然后按「部署」写入配置。"
                  : "按「部署」写入配置。在此之前什么都不会到达 Fcitx5 或 Rime。"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }
          }

          // A Flow rather than a Row: four controls, one of which only exists
          // while packages are missing and is the widest of them when it does,
          // no longer fit on one line at this panel's width. Wrapping is the
          // honest answer - a Row would simply run off the edge.
          //
          // A Flow lays its items out against the top of the line and has no
          // vertical alignment of its own, so items of different heights come
          // out ragged: Ui/Button reserves row height plus padding plus the
          // widest border any of its states can paint (33px here), while
          // Ui/PanelActionButton is a plain square (22px), and the two bare
          // glyphs sat 5.5px above the middle of the pill beside them. The
          // shell's own answer elsewhere is `anchors.verticalCenter` on the
          // children, but that only works inside a Row, which sets x and
          // leaves y alone; a Flow sets both, and anchoring inside one fights
          // the positioner.
          //
          // So the line is made uniform instead of aligned: every item is
          // given the pill's height, and a line of equal heights has nothing
          // left to misalign. That survives the wrap - each line is uniform
          // for the same reason - and it survives the install button coming
          // and going, since that button is a Button too and is exactly as
          // tall as the deploy one. `size` is PanelActionButton's own knob for
          // this and dev-gallery already sets it; the glyph inside keeps its
          // own font size, so the buttons stay as bare as they were and only
          // the box they sit in changes.
          Flow {
            id: actionRow
            width: parent.width
            spacing: Style.space(8)

            readonly property int lineHeight: Math.max(Style.space(22), deployButton.implicitHeight)

            // Deploy is the only action that makes a changed setting real, so
            // once something is waiting it takes over the accent role: Button
            // paints its text, icon, and border from whatever fills
            // `foreground`, so handing it Color.accent moves the whole control
            // onto the accent through the same tokens it already uses.
            Button {
              id: deployButton
              text: root.busy ? "正在处理…" : (root.settingsDirty ? "部署以应用更改" : "部署")
              iconText: root.busy ? "󰑓" : "󰆓"
              iconSpinning: deployProcess.running
              bordered: true
              foreground: root.settingsDirty ? Color.accent : root.foreground
              accent: Color.accent
              tooltipText: "写入配置并重建 Rime，等同于 ime-manager deploy（D）"
              onClicked: root.deploy()
            }

            Button {
              visible: root.missingCount > 0
              text: installProcess.running
                ? "正在安装…"
                : "安装 " + root.missingCount + " 个软件包"
              iconText: installProcess.running ? "󰑓" : "󰏖"
              iconSpinning: installProcess.running
              bordered: true
              foreground: root.foreground
              // Says what will happen rather than which program does it. The
              // elevation is Omarchy's `pkg add`, not this plugin's, and the
              // only thing the user needs from this sentence is that a window
              // will open and ask them for a password.
              tooltipText: "在终端窗口中安装缺失的软件包，终端会询问你的密码，等同于 ime-manager packages install（I）"
              onClicked: root.installPackages()
            }

            PanelActionButton {
              iconText: "󰑐"
              size: actionRow.lineHeight
              tooltipText: "重新读取状态、设置和输入方案，等同于 ime-manager status（R）"
              foreground: root.foreground
              enabled: !root.busy
              onClicked: root.refreshEverything()
            }

            PanelActionButton {
              iconText: "󰒓"
              size: actionRow.lineHeight
              tooltipText: "在 shell.json 中编辑设置（E）"
              foreground: root.foreground
              onClicked: root.openSettings()
            }
          }

          // Whichever of the two actions above spoke last. Deploy wins when
          // both have run, because it is the one that had to be asked for.
          ResultLine {
            message: root.deployResult !== "" ? root.deployResult : root.installResult
            ok: root.deployResult !== "" ? root.deployOk : root.installOk
          }

          // What the last deploy warned about, in the deploy's own words. These
          // are the lines that say the candidate window is no longer following
          // the Omarchy theme, that a schema was withheld because its tables
          // could not be downloaded, or that a setting reaches only some of the
          // selected schemas - each of them a thing the user would otherwise
          // discover by typing. They sit under the result line rather than in
          // the STATUS block, because STATUS is behind the disclosure and this
          // is news about something that just happened.
          Column {
            width: parent.width
            spacing: Style.spacing.sm
            visible: root.deployWarnings.length > 0 || root.deployNotes.length > 0

            Repeater {
              model: root.deployWarnings

              Text {
                required property var modelData
                width: parent.width
                text: "󰀦  " + modelData
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            Repeater {
              model: root.deployNotes

              Text {
                required property var modelData
                width: parent.width
                text: "󰋼  " + modelData
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }
          }

          // The top-level sections are separated with a stronger rule than the
          // groups inside them, so the panel reads at two levels instead of one
          // long run of identical dividers.
          PanelSeparator { foreground: root.foreground; strength: 0.25 }

          // A section of its own rather than a group inside SETTINGS, because
          // nothing here is a setting: the rows below act on the running Rime
          // session and leave shell.json alone. The SETTINGS group named
          // "input schemas" decides which schemas a deploy puts on disk; this
          // decides which of them is composing right now.
          Column {
            width: parent.width
            spacing: Style.spacing.md

            PanelSectionHeader {
              text: "当前输入方案"
              fontSize: Style.font.bodySmall
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              // One line, because one thing is worth saying: clicking switches
              // straight away. Why it needs no deploy - that this is the live
              // Rime session rather than a setting - is an explanation of our
              // internals, and it was being delivered every time the panel
              // opened. The comment above this Column is where it belongs.
              text: "点击即可切换，立即生效，无需部署。"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              width: parent.width
              visible: root.schemas.length === 0
              text: root.schemasLoaded
                ? "没有输入方案 · Fcitx5 是否正在运行并加载了 Rime 插件？"
                : "正在读取输入方案…"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Repeater {
              model: root.schemas

              SchemaRow {
                required property var modelData
                entry: modelData
              }
            }

            ResultLine {
              message: root.schemaResult
              ok: root.schemaOk
            }
          }

          PanelSeparator { foreground: root.foreground; strength: 0.25 }

          Column {
            width: parent.width
            spacing: Style.spacing.sm

            PanelSectionHeader {
              text: "设置"
              fontSize: Style.font.bodySmall
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              // Shorter, but the deploy half stays: it is the answer to "I
              // changed it and nothing happened".
              text: "保存到 shell.json，下次部署时生效。"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              width: parent.width
              text: "正在读取设置…"
              visible: !root.settingsLoaded
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // The everyday half, always on screen. Four switches, and no header
          // of their own: SETTINGS above has already named them, and a heading
          // over four rows is a heading for its own sake.
          Column {
            width: parent.width
            spacing: Style.spacing.md

            Repeater {
              model: root.everydaySettings

              BooleanSetting {
                required property var modelData
                entry: modelData
              }
            }
          }

          // The disclosure. Everything behind it is chosen once - which schemas
          // get deployed, what the candidate window looks like, where the
          // background images live - or read once, which is what the status
          // report is. Leaving all of it on screen made a panel that had to be
          // scrolled past to reach anything, so it goes behind a control that
          // says it opens rather than that it acts.
          //
          // The mark is the kit's own. Ui/Dropdown and Ui/MultiSelect both put
          // the same chevron on the thing that opens, and this one turns a
          // quarter circle to point at what it has opened; Ui/Button carries
          // that rotation as a property of its icon, so the mark belongs to the
          // button rather than being a Text laid over it. It turns on the
          // panel's own duration, so the chevron and the section arrive
          // together.
          Button {
            id: advancedToggle
            width: parent.width
            text: "高级设置"
            iconText: "󰅀"
            iconRotation: root.advancedShown ? 0 : -90
            leftAlign: true
            bordered: true
            foreground: root.foreground
            accent: Color.accent
            tooltipText: root.advancedShown
              ? "隐藏只需选择一次的设置（A）"
              : "显示输入方案、候选词窗口和状态报告（A）"
            onClicked: root.advancedShown = !root.advancedShown

            Behavior on iconRotation {
              NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
            }
          }

          // The reveal: clip, grow the height, fade the opacity, and only drop
          // `visible` at a true zero so the column stops reserving spacing for
          // it once it is really gone. The same recipe, duration and curve as
          // the pinned footer below and the result lines above - which is what
          // plugins/panels/network collapses its band section with.
          //
          // Nothing in here is unbound while it is away. A switch flipped and
          // then put back behind the toggle is still written, still pending,
          // and still has the footer's deploy button waiting on it: the footer
          // is a layer over the whole panel rather than a row in this column,
          // so it does not leave with the control that raised it.
          Item {
            id: advanced
            width: parent.width
            clip: true
            visible: height > 0
            height: root.advancedShown ? advancedBody.implicitHeight : 0
            opacity: root.advancedShown ? 1 : 0

            Behavior on height {
              NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
            }
            Behavior on opacity {
              NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
            }

            Column {
              id: advancedBody
              width: parent.width
              spacing: Style.space(12)

              Column {
                width: parent.width
                spacing: Style.spacing.md

                PanelSectionHeader {
                  text: "注释"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Repeater {
                  model: root.annotationSettings

                  BooleanSetting {
                    required property var modelData
                    entry: modelData
                  }
                }

                SettingRow {
                  label: "注释热键"
                  description: "组字时切换释义。它在其他地方保持空闲。"
                  focused: hotkeyField.activeFocus
                  controlWidth: Style.space(140)

                  TextField {
                    id: hotkeyField
                    width: parent.width
                    placeholderText: "Control+e"
                    foreground: root.foreground
                    accent: Color.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    text: root.settingValue("annotateHotkey", "Control+e")
                    // Answers to the master switch like the gloss rows do.
                    enabled: !root.settingsLocked && root.settingValue("annotation", false) === true
                    Keys.onEscapePressed: root.close()
                    // Written when the field is left or Enter is pressed rather than
                    // on every keystroke: a half-typed hotkey is not a hotkey.
                    onEditingFinished: {
                      var value = text.trim()
                      if (value === "" || value === String(root.settingValue("annotateHotkey", "Control+e"))) {
                        text = root.settingValue("annotateHotkey", "Control+e")
                        return
                      }
                      root.writeSetting("annotateHotkey", value, "注释热键")
                    }
                  }
                }
              }

              PanelSeparator { foreground: root.foreground }

              Column {
                width: parent.width
                spacing: Style.spacing.md

                PanelSectionHeader {
                  text: "输入方案"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                // Two dozen schemas behind one collapsed control rather than in
                // front of the reader: the section is the same length whether one
                // schema is deployed or eight. Ui/MultiSelect is the platform's own
                // control for choosing several from a long list - a trigger showing
                // the selection, a popup holding a searchable checklist - and
                // manifest.json declares the same setting as `multiselect`, so the
                // panel and Omarchy's own settings surface offer the same thing.
                MultiSelect {
                  id: schemaPicker

                  width: parent.width
                  label: "部署的输入方案"
                  // A schema's readable name is the `name:` in its own schema yaml,
                  // so the list is asked for rather than written here: the manager
                  // reads those files and answers with the id, that name, and the
                  // family the schema belongs to.
                  optionsCommand: [root.managerPath, "schema", "available", "--json"]
                  noSelectionText: "未选择任何方案"
                  placeholderText: "搜索输入方案…"
                  emptyText: "没有找到输入方案"
                  foreground: root.foreground
                  accent: Color.accent
                  fontFamily: root.fontFamily
                  enabled: !root.settingsLocked
                  values: root.settingValue("schemas", ["rime_ice"])

                  // Toggling a row assigns `values` from inside the control, which
                  // breaks the binding above. So the panel puts the truth back
                  // whenever the settings are re-read - which is also what makes a
                  // refused selection snap back to what is really on disk.
                  Connections {
                    target: root
                    function onSettingValuesChanged() {
                      schemaPicker.values = root.settingValue("schemas", ["rime_ice"])
                    }
                  }

                  onChanged: function(selection) {
                    root.writeSetting("schemas", selection, "部署的输入方案")
                  }
                }

                Text {
                  width: parent.width
                  // Both halves are things the reader cannot see: that the
                  // selection is floored at one Chinese schema, and that picking a
                  // Japanese one makes the next deploy fetch a dictionary.
                  text: "至少保留一个中文输入方案。选择日语方案会在下次部署时下载其词典。"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }

              PanelSeparator { foreground: root.foreground }

              Column {
                width: parent.width
                spacing: Style.spacing.md

                PanelSectionHeader {
                  text: "词库"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                // Extra vocabulary, chosen the same way schemas are: the same
                // collapsed searchable checklist, the same write path, and the
                // options asked for rather than written here so the catalogue
                // lives in exactly one place (lib/dicts.py).
                MultiSelect {
                  id: dictionaryPicker

                  width: parent.width
                  label: "挂载的细胞词库"
                  optionsCommand: [root.managerPath, "dict", "available", "--json"]
                  noSelectionText: "不使用词库"
                  placeholderText: "搜索词库…"
                  emptyText: "没有找到词库"
                  foreground: root.foreground
                  accent: Color.accent
                  fontFamily: root.fontFamily
                  enabled: !root.settingsLocked
                  values: root.settingValue("dictionaries", [])

                  Connections {
                    target: root
                    function onSettingValuesChanged() {
                      dictionaryPicker.values = root.settingValue("dictionaries", [])
                    }
                  }

                  onChanged: function(selection) {
                    root.writeSetting("dictionaries", selection, "挂载的细胞词库")
                  }
                }

                Text {
                  width: parent.width
                  // What the reader cannot see: that the cells only reach
                  // pinyin-family schemas, and that the first deploy after a
                  // selection downloads them.
                  text: "词库对拼音类方案（全拼、双拼）生效，下次部署时下载并编译。注音、形码和日语方案不受影响。"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }

              PanelSeparator { foreground: root.foreground }

              Column {
                width: parent.width
                spacing: Style.spacing.md

                PanelSectionHeader {
                  text: "候选词窗口"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                SettingRow {
                  label: "每页候选词数"
                  description: "Rime 一次显示多少个候选词。"
                  focused: pageSizeField.field.activeFocus
                  controlWidth: Style.spacing.numberFieldWidth

                  NumberField {
                    id: pageSizeField
                    width: parent.width
                    fieldWidth: parent.width
                    from: 1
                    to: 10
                    stepSize: 1
                    value: root.settingValue("pageSize", 10)
                    foreground: root.foreground
                    accent: Color.accent
                    fontFamily: root.fontFamily
                    enabled: !root.settingsLocked
                    onModified: function(value) { root.writeSetting("pageSize", value, "每页候选词数") }
                  }
                }

                Repeater {
                  model: root.candidateSettings

                  BooleanSetting {
                    required property var modelData
                    entry: modelData
                  }
                }

                // Two fields rather than one, because the candidate window is
                // painted from the active Omarchy theme and follows it when it
                // changes. An image picked to sit under a dark theme's pale text is
                // unreadable the moment a light theme paints dark text over it, so
                // each mode gets its own and a deploy writes whichever one matches.
                //
                // Plain TextFields rather than a browse button: the shell's Ui/
                // ships no file picker, and this panel does no filesystem work of
                // its own anyway - the path goes to `ime-manager`, which is the one
                // place that opens files and the only place that judges them.
                // `writeSetting` hands the value over as its own argv element, so a
                // path with spaces in it needs no quoting here.
                Repeater {
                  // "Background image (dark themes)" did not fit the label column
                  // and arrived as "Background image (da…", which left the two rows
                  // distinguishable only by reading the sentence underneath. Half
                  // the words say the same thing and fit.
                  model: [
                    {
                      key: "backgroundImageDark",
                      label: "深色主题背景",
                      description: "深色 Omarchy 主题激活时使用。"
                    },
                    {
                      key: "backgroundImageLight",
                      label: "浅色主题背景",
                      description: "浅色 Omarchy 主题激活时使用。"
                    }
                  ]

                  SettingRow {
                    required property var modelData

                    label: modelData.label
                    description: modelData.description
                    focused: imageField.activeFocus
                    controlWidth: Style.space(150)

                    TextField {
                      id: imageField
                      width: parent.width
                      // An instruction, not an example. The placeholder used to be
                      // "~/Pictures/candidates.png" on both rows, which reads as a
                      // value: two fields apparently holding the same file, with no
                      // way to tell at a glance that both were in fact empty and no
                      // such file existed. The kit paints a placeholder in a dimmer
                      // role than a value, but that difference is a shade, and a
                      // shade is not enough to carry "there is nothing here" - the
                      // words have to say it.
                      placeholderText: "无 · 绝对路径"
                      foreground: root.foreground
                      accent: Color.accent
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      text: root.settingValue(modelData.key, "")
                      enabled: !root.settingsLocked
                      Keys.onEscapePressed: root.close()
                      // The key catcher above takes keys before this field ever
                      // sees them, so it has to be told to stand aside while a path
                      // is being typed - the "e" in "Pictures" would otherwise open
                      // the editor mid-word.
                      onActiveFocusChanged: root.backgroundImageFocus += activeFocus ? 1 : -1
                      // Written when the field is left or Enter is pressed rather
                      // than on every keystroke, and only when the path actually
                      // changed - otherwise every click through the panel would
                      // rewrite shell.json and raise the deploy prompt.
                      //
                      // An empty value is written like any other. Clearing the
                      // field is how the user asks for the theme's own background
                      // back, so unlike the hotkey field above - where an empty
                      // hotkey is not a hotkey and snaps back - nothing is
                      // restored here.
                      onEditingFinished: {
                        var value = text.trim()
                        if (value === String(root.settingValue(modelData.key, ""))) return
                        root.writeSetting(modelData.key, value, modelData.label)
                      }
                    }
                  }
                }
              }

              PanelSeparator { foreground: root.foreground; strength: 0.25 }

              Column {
                width: parent.width
                spacing: Style.spacing.md

                PanelSectionHeader {
                  text: "状态"
                  fontSize: Style.font.bodySmall
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                }

                Text {
                  width: parent.width
                  text: "正在读取插件状态…"
                  visible: !root.statusLoaded
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Repeater {
                  model: root.statusRows()

                  StatusRow {
                    required property var modelData
                    width: parent.width
                    row: modelData
                  }
                }
              }
            }
          }
        }
      }

      // The deploy affordance, pinned. It used to be the last two rows of the
      // scrolling column, which meant a confirmation that lived below the fold:
      // change a setting halfway down the panel and the line saying it was
      // saved - and the button that makes it real - were somewhere the reader
      // had already scrolled past, or had never scrolled to. A confirmation
      // nobody can see is not a confirmation.
      //
      // It is a layer over the content rather than a row in it, so it carries
      // the panel's own background and a rule along its top edge: without the
      // opaque fill the rows underneath would slide through the text, and
      // without the rule it would read as something floating loose in the
      // middle of a list. The rule is the section-strength one, because that is
      // what this panel already uses to separate a band from the content above
      // it.
      //
      // Nothing when nothing is pending: at rest its height is zero, it is not
      // visible, and the panel is exactly what it was.
      Item {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true
        visible: height > 0
        // The band's own padding, above and below its content, so the rule
        // along its top edge is a rule and not an underline.
        height: root.footerShown ? footerBody.implicitHeight + Style.spacing.xl * 2 : 0
        opacity: root.footerShown ? 1 : 0

        // The same 140ms OutCubic the result lines and the old inline button
        // used, so the footer arrives at the speed everything else in this
        // panel moves at.
        Behavior on height {
          NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
        }
        Behavior on opacity {
          NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
        }

        Rectangle {
          anchors.fill: parent
          color: Color.popups.background
        }

        PanelSeparator {
          anchors.top: parent.top
          anchors.left: parent.left
          anchors.right: parent.right
          foreground: root.foreground
          strength: 0.25
        }

        Column {
          id: footerBody
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: parent.top
          anchors.topMargin: Style.spacing.xl
          spacing: Style.spacing.md

          // A saved setting keeps its accent: it is not just "that worked", it
          // is "there is now something waiting for the button below". Its own
          // reveal is off here - the footer is what animates, and two nested
          // height animations on the same 140ms would fight over the same
          // pixels.
          ResultLine {
            animated: false
            message: root.settingsResult
            ok: root.settingsOk
            messageColor: root.settingsOk
              ? (root.settingsDirty ? Color.accent : root.dim)
              : root.urgent
          }

          // A changed setting only reaches Fcitx5 and Rime through a deploy,
          // and a deploy can download dictionaries, so the panel offers it
          // here instead of firing one off after every click.
          Button {
            id: applyButton
            visible: root.settingsDirty
            text: root.busy ? "正在处理…" : "部署以应用更改"
            iconText: root.busy ? "󰑓" : "󰆓"
            iconSpinning: deployProcess.running
            bordered: true
            foreground: Color.accent
            accent: Color.accent
            tooltipText: "写入配置并重建 Rime，等同于 ime-manager deploy（D）"
            onClicked: root.deploy()
          }
        }
      }
    }
  }

  // A line that reports how an action ended - a deploy, an install, a schema
  // switch, a saved setting. It does two things the plain Text it replaced did
  // not.
  //
  // It carries the outcome. The sentence has always said whether the run
  // worked, but only to someone who reads it; every result line was painted the
  // same muted grey whether it was a success or a failure. The glyph is the
  // shell's confirm or refuse mark in the matching palette role, so the outcome
  // is legible before the words are, and the sentence itself goes urgent when
  // it is bad news and stays quiet when it is not.
  //
  // And it arrives rather than appears. This panel is one tall scrolling
  // column, so a line that pops into being shoves everything beneath it down by
  // its own height. Growing from zero and fading in over the shell's own 140ms
  // makes the rows below slide instead of jump. The shape is Omarchy's: clip,
  // animate height and opacity together, and only drop `visible` at a true zero
  // so the Column stops reserving spacing for the row once it is really gone.
  // plugins/panels/network/Panel.qml collapses its band section exactly this
  // way.
  component ResultLine: Item {
    id: resultLine

    property string message: ""
    property bool ok: true
    property color messageColor: resultLine.ok ? root.dim : root.urgent

    // Off for a line that already sits inside something that animates - the
    // pinned footer. Two height animations of the same length nested inside one
    // another do not add up to one smooth reveal; the inner one just makes the
    // outer one's target move while it is chasing it.
    property bool animated: true

    readonly property bool shown: resultLine.message !== ""

    width: parent ? parent.width : implicitWidth
    clip: true
    visible: height > 0
    height: resultLine.shown ? resultBody.implicitHeight : 0
    opacity: resultLine.shown ? 1 : 0

    Behavior on height {
      enabled: resultLine.animated
      NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
    }
    Behavior on opacity {
      enabled: resultLine.animated
      NumberAnimation { duration: root.revealDuration; easing.type: Easing.OutCubic }
    }

    Row {
      id: resultBody
      width: parent.width
      spacing: Style.spacing.controlGap

      // Sized with the sentence rather than at icon size: it is punctuation on
      // this line, not a control of its own.
      Text {
        id: resultGlyph
        text: root.stateGlyph(resultLine.ok)
        color: root.rowColor(resultLine.ok)
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Text {
        width: parent.width - resultGlyph.width - parent.spacing
        text: resultLine.message
        color: resultLine.messageColor
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }
    }
  }

  // One on/off setting. Nothing here but the kit's own Toggle: it already
  // draws the label, the description, the switch, and every interaction state,
  // so the panel adds no surface of its own around it. Hierarchy in the
  // settings section comes from the group headers, not from extra chrome.
  component BooleanSetting: Toggle {
    required property var entry

    width: parent ? parent.width : implicitWidth
    label: entry.label
    description: entry.description
    foreground: root.foreground
    accent: Color.accent
    fontFamily: root.fontFamily
    checked: root.settingValue(entry.key, entry.fallback)
    // Locked while a write or a deploy is running, and also while the switch
    // this row answers to (`requires`) is off: the value stays visible and
    // stored, it just cannot be changed until the feature is on.
    enabled: !root.settingsLocked
      && (entry.requires === undefined || root.settingValue(entry.requires, false) === true)
    onClicked: root.writeSetting(entry.key, !checked, entry.label)
  }

  // A labelled control row for the settings that are not on/off. It borrows
  // Toggle's own recipe - same fill and border tokens, same corner radius, the
  // same row padding, and the same minimum height - so a NumberField or a
  // TextField sits in the rhythm of the switches around it instead of arriving
  // as a differently sized stranger.
  //
  // When the field inside has focus the row takes the accent role: the focus
  // fill and focus border tokens are asked for in accent rather than in
  // foreground, which is the one state in this section where the theme's accent
  // reaches a setting.
  component SettingRow: BorderSurface {
    id: settingRow

    property string label: ""
    property string description: ""
    property bool focused: false
    property real controlWidth: Style.spacing.numberFieldWidth

    default property alias controlData: controlHolder.data

    readonly property bool hot: rowHover.hovered
    readonly property color roleColor: focused ? Color.accent : root.foreground

    width: parent ? parent.width : implicitWidth
    implicitHeight: Math.max(Style.space(54), rowContent.implicitHeight + Style.spacing.huge)
    radius: Style.cornerRadius

    color: Style.controlFill(focused, hot, roleColor, Color.accent)
    borderSpec: Border.controlSpec(
      focused ? "focus" : (hot ? "hover-cursor" : "normal"), roleColor, Color.accent)

    Behavior on color { ColorAnimation { duration: 100 } }

    HoverHandler { id: rowHover }

    Row {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: settingRow.borderLeft + Style.spacing.rowPaddingX
      anchors.rightMargin: settingRow.borderRight + Style.spacing.rowPaddingX
      spacing: Style.spacing.rowPaddingX

      Column {
        width: parent.width - controlHolder.width - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.spacing.xs

        Text {
          width: parent.width
          text: settingRow.label
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          visible: settingRow.description !== ""
          text: settingRow.description
          color: Qt.darker(root.foreground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }

      Item {
        id: controlHolder
        width: settingRow.controlWidth
        implicitHeight: childrenRect.height
        height: implicitHeight
        anchors.verticalCenter: parent.verticalCenter
      }
    }
  }

  // One switchable Rime schema. It follows SettingRow's recipe line for line -
  // same fill and border tokens, same corner radius, same row padding, same
  // minimum height - so the list sits in the rhythm of the controls below it
  // instead of arriving as a differently sized stranger. It is its own
  // component rather than a SettingRow because the whole surface is the
  // control here, and SettingRow hands its children to the control slot.
  //
  // The accent role means something different here than it does there: not
  // "this has focus" but "this is the one in use". Exactly one schema is
  // active, and which one should be findable without reading any text.
  component SchemaRow: BorderSurface {
    id: schemaRow

    required property var entry

    // From the live reading rather than from the entry's own `current` flag:
    // the list is re-read on open, on a switch and after a deploy, but the
    // current schema is polled between all three, so an F4 switch made while
    // the panel is open moves the mark without the list having to be fetched
    // again.
    readonly property bool active: String(entry.id) === root.currentSchema
    readonly property bool hot: schemaHover.hovered
    readonly property color roleColor: active ? Color.accent : root.foreground

    width: parent ? parent.width : implicitWidth
    implicitHeight: Math.max(Style.space(54), schemaContent.implicitHeight + Style.spacing.huge)
    radius: Style.cornerRadius

    color: Style.controlFill(active, hot, roleColor, Color.accent)
    borderSpec: Border.controlSpec(
      active ? "focus" : (hot ? "hover-cursor" : "normal"), roleColor, Color.accent)

    Behavior on color { ColorAnimation { duration: 100 } }

    HoverHandler { id: schemaHover }

    TapHandler {
      enabled: !root.switchingSchema && !schemaRow.active
      onTapped: root.selectSchema(schemaRow.entry.id, schemaRow.entry.name)
    }

    Row {
      id: schemaContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: schemaRow.borderLeft + Style.spacing.rowPaddingX
      anchors.rightMargin: schemaRow.borderRight + Style.spacing.rowPaddingX
      spacing: Style.spacing.rowPaddingX

      Column {
        // Sized from the shared token rather than from the mark, so the title
        // column is the same width on every row whether or not that row is the
        // one wearing the mark.
        width: parent.width - root.markDiameter - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.spacing.xs

        Text {
          width: parent.width
          text: String(schemaRow.entry.name || schemaRow.entry.id)
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          // The id is what Rime calls the schema and what `schema set` takes,
          // so it stays visible whenever the readable name is not the id.
          visible: String(schemaRow.entry.name || "") !== String(schemaRow.entry.id)
          text: String(schemaRow.entry.id)
          color: Qt.darker(root.foreground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      // The same mark the status rows wear, for the same reason: this row is
      // either the active one or it is not. It was a bare glyph before, in the
      // accent role, on a surface the active state had already tinted with the
      // accent - legible in a light theme and gone in a dark one. On the row's
      // own trailing edge and centred on the row, where the kit puts a control
      // that belongs to the whole surface (Ui/Toggle's switch, SettingRow's
      // field), because here the whole surface is the control.
      StateMark {
        visible: schemaRow.active
        ok: true
        anchors.verticalCenter: parent.verticalCenter
      }
    }
  }

  // The mark this panel puts beside a thing that is either settled or not:
  // the shell's confirm or refuse glyph on a disc of the same palette role.
  //
  // One component rather than a Rectangle-and-Text pair written out at each
  // site, because the sizes have to agree. They are read from root.markDiameter
  // and Style.font.body here and nowhere else, so a mark cannot end up a
  // different size, a different inset, or a different weight in one row than in
  // the next.
  //
  // The disc is what makes the glyph survive a dark theme. On its own, in the
  // accent role, at body size, it is a thin stroke of the same hue as the
  // accent-tinted surface it is drawn on - which is why the mark on the active
  // schema row was legible in a light theme and invisible in a dark one. The
  // disc separates the glyph from whatever it sits on before the glyph has to.
  component StateMark: Rectangle {
    id: stateMark

    property bool ok: true

    // Whether a false `ok` means "not switched on" rather than "broken". Only
    // the colour moves; the glyph is the same pair either way.
    property bool optional: false

    implicitWidth: root.markDiameter
    implicitHeight: root.markDiameter
    width: implicitWidth
    height: implicitHeight
    radius: width / 2
    color: Util.alpha(root.rowColor(stateMark.ok, stateMark.optional),
                      Style.selectedFillAlpha)

    // A glyph, not a letterform: no bold, which would only ask the font for a
    // synthetic weight it has no icon for.
    Text {
      anchors.centerIn: parent
      text: root.stateGlyph(stateMark.ok)
      color: root.rowColor(stateMark.ok, stateMark.optional)
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }
  }

  // One line of the status report: a mark, a title, and a detail under them
  // both.
  //
  // The mark is centred on the title rather than on the row. Centring it on the
  // row makes its position a function of how long the detail happens to be - a
  // row whose detail wraps to two lines drops its mark visibly lower than a row
  // whose detail is one line or none - and reading down the column the marks
  // wander. The kit's answer to this is to align the leading glyph against the
  // label instead of against the box: plugins/panels/clock anchors its hero
  // glyph to `heroDate.baseline`, and plugins/panels/network centres the band
  // switch on `bandAutoLabel.verticalCenter`. Anchoring to a sibling is what
  // both do, so the mark and the title are siblings here, and the detail is
  // indented under them with the mark's own width.
  //
  // That also means Row/Column rather than RowLayout/ColumnLayout: a Layout
  // owns its children's vertical position and would refuse the anchor.
  component StatusRow: Item {
    id: statusRow

    required property var row

    width: parent ? parent.width : implicitWidth
    implicitHeight: statusContent.implicitHeight + Style.spacing.rowPaddingX

    Column {
      id: statusContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(3)

      Row {
        width: parent.width
        spacing: root.markGap

        StateMark {
          ok: statusRow.row.ok === true
          optional: statusRow.row.optional === true
          anchors.verticalCenter: statusTitle.verticalCenter
        }

        Text {
          id: statusTitle
          width: parent.width - root.markDiameter - parent.spacing
          text: String(statusRow.row.label || "")
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          elide: Text.ElideRight
        }
      }

      Text {
        width: parent.width
        // Indented to the title's own left edge, so the detail reads as
        // belonging to it rather than to the mark.
        leftPadding: root.markDiameter + root.markGap
        text: String(statusRow.row.detail || "")
        visible: text !== ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
      }
    }
  }
}
