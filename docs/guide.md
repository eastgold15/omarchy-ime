# 使用指南

README 只讲功能、安装和卸载；这里的每一节讲一个主题的完整细节。

- [安装的细节](#安装的细节)
- [部署](#部署)
- [`QT_IM_MODULE` 覆盖](#qt_im_module-覆盖)
- [首次部署对已存在文件做了什么](#首次部署对已存在文件做了什么)
- [跟随 Omarchy 主题](#跟随-omarchy-主题)
- [验证它工作](#验证它工作)
- [设置](#设置)
- [候选词注释](#候选词注释)
- [词典](#词典)
- [Hyprland：插件报告但不管理的两个部分](#hyprland插件报告但不管理的两个部分)

## 安装的细节

### 把组件放到你想要的位置

`--enable` 会把组件放在插件的默认区域。把它移到属于它的地方：

```bash
omarchy bar move eastgold15.omarchy.ime --after omarchy.clock
```

**原版键盘布局指示器不会自行消失。** 这个组件旨在取代它，但没有任何东西会替你移除旧的：如果你之前使用 `omarchy.keyboard-layout`，请手动从 `~/.config/omarchy/shell.json` 的 `bar.layout` 中删除其条目，否则状态栏上会同时出现两者。没有 `omarchy bar` 子命令可以移除组件。

### 设置是在组件之后，而不是之前

只在组件确实位于状态栏上之后才更改设置。设置内联在 `shell.json` 中组件自己的条目上，因此如果没有条目，就没有地方写入它们，而 `ime-manager settings set` 会拒绝而不是凭空创建一个：

```
ime-manager: eastgold15.omarchy.ime is not on the bar in /home/you/.config/omarchy/shell.json; add it with `omarchy bar` first
```

这是当 `shell.json` 存在且未提及此插件时的消息。如果文件根本不存在，读取会在查找条目之前失败，你会得到：

```
ime-manager: cannot read /home/you/.config/omarchy/shell.json: [Errno 2] No such file or directory: '/home/you/.config/omarchy/shell.json'
```

这是刻意的，也是新用户首先会遇到的事情。读取则不同：`ime-manager settings get` 在任何时候都可用，并在组件不在状态栏上时回答默认值。

### 从现有设置迁移

**在首次部署之前设置你已经拥有的功能。** 部署会根据设置完整写出配置，因此设置中关闭的功能就是生成文件中消失的功能——无论你旧配置有什么。最容易被忽略的是 `schemas`，它默认只有 `rime_ice`：用默认值部署，其他所有 schema 都会从 `schema_list` 中掉出。

所以如果你今天使用的不只是全拼，请先选择它们：

```bash
IME=~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager
$IME schema available            # 此 Rime 安装提供的所有 schema
$IME settings set schemas=rime_ice,double_pinyin_flypy
```

其他你依赖的内容也一样——对照[设置](#设置)表检查你拥有的内容，并在首次部署之前而不是之后设置它。

## 部署

```bash
ime-manager deploy
```

（`ime-manager` 也可以写完整路径 `~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager`。）

部署会解析你的设置，如果候选词注释开启（且至少有一个释义方向）则下载注释词典，获取已启用功能所需的固定来源，将 Rime 配置写入 `~/.local/share/fcitx5/rime/`，将 Fcitx5 配置安装到 `~/.config/fcitx5/`，其主题文件安装到 `~/.local/share/fcitx5/themes/`，环境覆盖安装到 `~/.config/environment.d/`，并按目标分组打印它安装的每个路径。

它还会在 `~/.local/bin/ime-manager` 放一个指向插件里 CLI 的软链接——Arch 默认把 `~/.local/bin` 放在 PATH 上，因此部署之后终端里直接敲 `ime-manager` 就能用。这个链接和其他部署文件一样记录在安装清单中，`ime-manager uninstall` 会把它一并移除；如果那个位置已有你自己放的文件，会先备份再替换，卸载时还原。

其中一个文件在你再次登录之前不会做任何事。systemd 在会话启动时读取 `environment.d`，因此部署写入的 `QT_IM_MODULE` 覆盖在**下次登录**时生效，而不是之前——部署会说明这一点，面板也会。

你的 Fcitx5 `profile` 是**合并的，不是写入的**。它携带你的键盘布局和你设置的所有输入法，因此部署会将 Rime 添加到已有的组中并使其成为该组的默认；布局、组名和其他所有项目都保持你离开时的样子。在没有 profile 的机器上，会使用 `/etc/vconsole.conf` 中 `XKBLAYOUT` 指定的布局写入一个——与 Omarchy 为 Hyprland 的 `kb_layout` 读取的位置相同。

部署无法做到的任何事情，它都会说出来。以 `warning:` 开头的行是值得阅读的：无法读取的主题，因此候选词窗口回退到中性调色板；无法获取的来源，因此某个 schema 被从本次部署中扣留；只到达部分所选 schema 的设置。面板会在部署结果下显示它们，而不是折叠成一个计数。

然后它要求 Rime 重建，并检查它是否做到了。写入文件只是部署的一半：Rime 在构建时读取其定制文件，而不是在按键时，因此磁盘上未编译的文件就是未生效的文件——而 Rime *拒绝*编译的文件会连带整个 schema 一起失败，这就是一个格式错误的补丁键如何能让你失去输入法，而所有测试仍然通过。

重建是对 Fcitx5 的 D-Bus 控制器上 `rime` 插件的 `ReloadAddonConfig` 调用。这使 fcitx5-rime 拆除其 librime 会话并重新编译每个源已更改的 schema，而不会像重启单元那样丢弃每个打开应用的输入上下文。

判定基于两件事，因为任何一件单独都不够。

**Rime 抱怨了什么。** `omarchy-fcitx5.service` 日志从触发前取的游标开始被监视，并与 librime 实际发出的错误字符串匹配——配置编译器的四个，加上词典编译器和部署任务的，也就是缺失下载会落到的地方。所有这些都是从 `strings /usr/lib/librime.so.1` 中读出的，而不是猜测的。

**Rime 构建了什么。** 静默的日志不是任何事情的证据：在 `rime-ice-git` 缺失时，`default.custom.yaml` 开头的 `__include: rime_ice_suggestion:/` 会使部署器以非零退出并且什么都不打印，留下空的 `build/`。因此部署刚写入 `schema_list` 的 schema 会在 `~/.local/share/fcitx5/rime/build/` 中查找，每一个都要求至少与部署写入的最新文件一样新。没有改变任何东西的部署会立即通过；首次部署会等待编译 rime_ice 表所需的时间。

三种结果，它们被区分开来：

- **Rebuilt（已重建）。** 两项检查都同意，`ime-manager deploy` 退出 0。
- **Refused（被拒绝）。** 它以非零退出，打印 Rime 写入 stderr 的行，并明确说明配置在磁盘上但**未**生效。这是值得大声说出的失败——它是让人无法打字的那个。
- **Not yet（尚未）。** fcitx5 不在会话总线上，因此没有什么可问，也没有任何问题：文件在磁盘上，fcitx5 启动时会构建它们。这是首次部署机器的普通状态，它会这样说并退出 0。触发了重建但结果无法确认——没有可读日志，或 fcitx5 在 `omarchy-fcitx5.service` 之外运行——也会退出 0 并说明是哪种，而不是声称它没有看到的重建。

## `QT_IM_MODULE` 覆盖

Omarchy 自带 `/usr/lib/environment.d/10-omarchy-fcitx.conf`，它设置了四个变量，包括 `QT_IM_MODULE=fcitx`。部署会将同名文件安装到 `~/.config/environment.d/`，systemd 随后读取它，因此它替换了打包的那个——其中 `QT_IM_MODULE` 被有意留空。

原因在生成文件自己的头部，值得在此重复。Fcitx5 的自我诊断报告：

```
Using Wayland native input method protocol: 1
GTK_IM_MODULE=
QT_IM_MODULE=fcitx
```

原生协议（text-input-v3）已启动且 GTK 已经使用它，但 `QT_IM_MODULE=fcitx` 强制 Qt 走较旧的 DBus 输入上下文路径。两条路线都活跃时，像 Omarchy 面板这样的 layer-shell 表面会得到赢得焦点竞争的那一条——这就是为什么向其中一个输入中文只是有时有效。让 `QT_IM_MODULE` 留空使 Qt 与 GTK 处于同一原生协议上。

其他三个变量是从打包文件复制而非从列表写出，因此 Omarchy 上游添加的变量会被带过来，而不是被覆盖静默丢失。`XMODIFIERS` 是 XWayland 和 Qt-on-xcb 回退到的；`SDL_IM_MODULE` 和 `INPUT_METHOD` 没有 Wayland 原生等价物。

它是一个普通的部署文件：记录在安装清单中，如果已有内容则备份，并由 `ime-manager uninstall` 移除——这会恢复打包行为。两个方向都在你下次登录时生效。

## 首次部署对已存在文件做了什么

部署即将替换的每个文件首先被复制到

```
~/.local/state/omarchy-ime/backups/<timestamp>/<target>/<path>
```

每次有内容可保存的部署对应一个带时间戳的目录。在已有手工构建的 Fcitx5 和 Rime 设置的机器上，首次部署会备份它覆盖的所有内容——在真实首次运行中，那是分布在各目标中的几十个文件，包括手写的 `~/.config/environment.d/10-omarchy-fcitx.conf`。只有首次部署会大量备份：之后清单记录哪些文件是插件的，插件已拥有的文件会被重写而无需新副本。

**Rime 用户数据绝不被触碰。** 插件的清单只覆盖它生成的文件以及它安装的载荷和固定来源文件。你的 `*.userdb` 目录、`sync/`、`user.yaml`、`installation.yaml` 和 `build/` 在它之外——从不写入，因为从不替换所以从不备份，也从不被 `ime-manager uninstall` 移除。你学到的短语和同步状态在部署和卸载后都存活。

保存 `~/.config/omarchy/plugins/` 下的任何文件会自动重新加载插件代码，因此就地编辑插件无需重启 shell 即可生效。

## 跟随 Omarchy 主题

候选词窗口从 Omarchy 调色板取主题，而该主题在部署时生成——因此切换 Omarchy 主题会让候选词窗口保持旧主题绘制，直到下次部署。安装一次 theme-set 钩子，它就会自行重绘：

```bash
~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager hook install
~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager hook status
```

钩子是 `~/.config/omarchy/hooks/theme-set.d/` 中的三行 shim，以插件命名，因此不会与另一个插件的冲突。它运行 `ime-manager theme apply`，只重写内容已更改的文件并重新加载 Fcitx5 的 `classicui` 插件——不下载词典，不重建 Rime。它不会失败：`omarchy-hook` 对任何非零退出打印 "Hook failed"，而这件事无事可做的普通原因不值得在每次主题切换时都说。手动运行 `ime-manager theme apply` 查看它本来会说什么。

`ime-manager hook remove` 会移除它，`ime-manager uninstall` 也会——钩子像每个部署文件一样在安装清单中，因此不会留下指向已消失插件的钩子。

## 验证它工作

端到端检查是手动的：

1. 部署——如果 Rime 拒绝了新配置，它会以非零退出——然后在普通应用中确认中文输入有效。
2. 在候选词注释开启时，开始组字并确认注释出现在候选词旁边。
3. 按注释热键并确认它消失，然后再次按下使其回来。

步骤 2 和 3 取决于 `annotation` 设置开启以及注释词典已下载——见[词典](#词典)。`ime-manager status` 报告两者，以及缺失的软件包、是否有任何内容被部署，以及下面两个 Hyprland 部分是否生效：

```
packages missing: none
dictionary: present
dictionary path: /home/you/.cache/omarchy-ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt
deployed: yes
theme hook: installed
hyprland candidate blur: on
hyprland input method toggle: on
input: latin
```

`ime-manager status --json` 是同一报告的 JSON 形式，也就是面板读取的内容。

## 设置

设置内联在 `~/.config/omarchy/shell.json` 中组件自己的条目上，通过 `id` 为 `eastgold15.omarchy.ime` 的条目匹配。没有单独的配置文件，Omarchy 在保存时热重载该文件。每个键都是可选的：缺失、损坏或未提及此插件的 `shell.json` 会得到默认值而不是错误，类型错误的值会回退到其默认值，因此拼写错误不能阻止输入法部署。

| 键 | 默认值 | 作用 |
| ---------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemas` | `["rime_ice"]` | 哪些 Rime schema 被写入 `schema_list` 并部署。一个折叠多选，覆盖此 Rime 安装提供的所有内容——全拼、七种双拼布局、注音、形码。名称从每个 schema 自己的 yaml 读取，从不硬编码。取消选择一个会在下次部署时移除其文件。至少一个中文 schema 总是存活。 |
| `annotation` | `false` | 候选词注释的总开关。默认关闭：全新安装自带没有任何附加内容的 Rime Ice。开启会安装 Lua 过滤器、下载 CC-CEDICT，并为每个选中的中文 schema 打上过滤器、其开关和热键绑定的补丁；关闭不安装任何这些，并在下次部署时移除它，留下缓存的词典。下面四个 `annotate*` 设置在它关闭时保持其值。 |
| `annotateChinese` | `true` | 中文候选词上的英文释义。作为 `annotate/chinese` 写入 schema。 |
| `annotateEnglish` | `true` | 英文候选词上的中文释义。作为 `annotate/english` 写入 schema。两个方向都关闭时，过滤器、其开关和热键绑定完全不写入。 |
| `annotateShowPinyin` | `false` | 在释义前加上候选词拼音。默认关闭：释义才是重点，而拼音已经在你刚输入的内容中。 |
| `annotateHotkey` | `Control+e` | 切换注释开关的键。以 `when: composing` 绑定——见下文。 |
| `traditionalize` | `true` | 将 `traditionalize/opencc_config` 设为 `s2twp.json`，因此输出是台湾风格繁体。关闭则完全省略补丁。 |
| `pageSize` | `10` | 每页候选词数（`menu/page_size`）。接受范围 1–10：手动编辑超出范围会被钳制到范围内，而 `ime-manager settings set` 会直接拒绝，而不是写回你从未要求的数字。 |
| `verticalCandidates` | `true` | 候选词垂直渲染。两个键，因为两个前端拥有答案：Rime 的 `style/horizontal`，Squirrel 和 Weasel 读取的；以及 classicui 的 `conf/classicui.conf` 中的 `Vertical Candidate List`，Fcitx5 下绘制窗口的。第二个是这里起作用的，也是为什么该文件是生成的而非自带的——它曾经是一个固定在 `True` 的载荷文件，因此开关接到了一个没有读取者的键上，关闭它什么也不做。 |
| `backgroundImageLight` | `""` | 当_浅色_ Omarchy 主题活动时绘制在候选词后面的图像的绝对路径。空表示主题自己的背景色。 |
| `backgroundImageDark` | `""` | _深色_主题的相同内容。两个键而非一个，因为候选词窗口跟随切换到的主题，而针对深色主题挑选的图像在浅色下不可读。接受的格式是 `.png`、`.svg`、`.svgz`、`.jpg`、`.jpeg`、`.jpe`、`.webp`、`.bmp` 和 `.gif`；路径在写入时展开并变为绝对，扩展名和文件自己的前几个字节都被检查，因为候选词窗口从扩展名选择加载器，会静默地从不加载内容与名称不符的文件。 |
| `panelWidth` | `420` | 弹出面板多宽，以 `Style.space` 按主题间距缩放的布局单位计。从内容测量而非挑选：最宽的行是一个设置及其尾部边缘的控件，其标签必须放在旁边。接受范围 400–720；下限是没有标签省略的最窄面板。 |

只读取这些键；组件条目上的其他任何内容都被忽略——包括早期版本接受的 `englishInput`。从来没有办法让它工作：Rime Ice 将 `melt_eng` 挂载为辅助翻译器，而 librime 没有列表减法运算符，因此该设置产生的 `engine/translators/-` 补丁直接使 schema 构建失败，并连带其他每个 schema 一起失败。想要纯英文的人有 `ascii_mode`，这是 Rime 和 Fcitx5 中的标准机制。留在 `shell.json` 中的陈旧 `englishInput` 在读取时被忽略；`ime-manager settings set englishInput=...` 现在会作为未知设置拒绝它。

两种经过验证的更改方式。CLI：

```bash
ime-manager settings get                       # 解析值，填入默认值
ime-manager settings set schemas=rime_ice,double_pinyin_flypy pageSize=5
```

以及面板自己的控件，它们为显示的每个控件调用 `ime-manager settings set`——因此 CLI 会拒绝的值在那里也会被拒绝，控件会弹回真相。`settings set` 写入是原子的（临时文件，然后重命名），只重写给定的键，并保持 Omarchy 自己的两空格缩进，因为半写入的 `shell.json` 会拖垮整个状态栏而不只是这个插件。

手动编辑 `shell.json` 是第三种方式，也是唯一跳过验证的方式：那里类型错误或超出范围的值会被静默解析为可部署的内容，而不是被拒绝。这就是重点——状态栏配置中的拼写错误绝不能阻止输入法部署。

插件生成的文件——`default.custom.yaml` 加上每个需要打补丁的选中 schema 的一个 `<schema>.custom.yaml`——在每次部署时从设置**完整**写出，从不读取并就地修补。这使重复部署成为无操作而非合并，并使卸载成为删除所写内容的事情。取消选择一个 schema 会移除其文件。代价是对其中任何一个的手动编辑在下次部署时丢失；每个都带有说明这一点的头部。请改为更改设置。

## 候选词注释

默认关闭。在面板中用_候选词注释_开关或 `ime-manager settings set annotation=true` 打开，然后部署。当它关闭时，功能的任何部分都不会到达机器：Rime 目录中没有 `lua/annotate.lua`，没有 CC-CEDICT 下载，任何 `<schema>.custom.yaml` 中都没有注释过滤器、开关或热键绑定——部署后关闭它会在下一次移除所有三个。`ime-manager status` 和每次部署都会说明它何时关闭，因此没有释义绝不是谜。

一个 Rime `lua_filter` 将每个候选词的含义放入其注释。输入 `nihao`，中文候选词带有其拼音和英文含义；输入 `abal`，英文候选词 `abalone` 带有其中文词。两个方向都来自同一个 [CC-CEDICT](https://cc-cedict.org/) 词典——一个文件，一次下载，加载时双向索引。

有**一个** Rime 开关，`annotate`，而不是每个方向一个：一个键不能切换两个开关，因此同一键上的第二个绑定会是死重。（针对 librime 1.17.0 测量，较晚的绑定胜过较早的——这就是为什么即使在其预设已将 `Control+e` 绑定到行尾的 schema 上，追加热键也能工作。）热键回答“现在显示释义”；_哪个_方向释义是部署时设置（`annotateChinese` 和 `annotateEnglish`），因为没人在词中途改变主意。开关以 `reset: 1` 写入，因此它启动时开启。

过滤器每个候选词批次检查开关一次，并在关闭时原样产出候选词，因此安装了功能但切换关闭几乎不花任何代价。词典惰性加载，在第一次注释按键时而非部署时——而缺失的词典文件简单地加载为空，这意味着候选词不加注释地通过。打字从不依赖下载成功，失败的下载也从不使部署失败。

### 热键，以及为什么它有作用域

热键（默认 `Control+e`）以 `when: composing` 绑定：它只在组字进行中且候选词显示时适用。

这个限制就是重点。`Control+e` 是 readline 的行尾，而使用 Rime 通常的 `when: always` 的绑定会在中文模式活动时在每个终端中吞掉它。将其作用域限于组字使该键在其他任何地方都属于你，而你想切换注释的时刻正是你看着候选词的时刻。

## 词典

注释词典是 [CC-CEDICT](https://cc-cedict.org/)，在部署时从其发布者下载并完全按发布内容读取。插件不自带任何词典数据，也不再分发任何：文件从 MDBG 到达你的机器，逐字节如 MDBG 发布的那样，而 Lua 过滤器直接解析上游行格式。

```
Traditional Simplified [pin1 yin1] /gloss one/gloss two/
```

`lib/assets.py` 下载 `https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz`，解压它——gzip 是唯一被移除的东西——并缓存到 `~/.cache/omarchy-ime/dictionary/cedict_1_0_ts_utf-8_mdbg.txt`。部署在候选词注释开启且至少有一个释义方向时获取它，否则完全跳过网络——缓存中已有的副本留在原地，因为它是缓存而非配置。

**没有固定。** MDBG 发布一个总是提供当前版本的 URL，没有针对旧版本的 URL，因此没有可固定的。相反，缓存按计划重新检查：超过 30 天的副本会在下次部署时再次下载，因此你跟踪上游而不是我们的快照。文件自己头部的发布戳（`#! date=`）记录在缓存的完成标记中，因此你可以看到你拥有哪个上游版本。

失败的下载只损失释义，别无其他：部署仍然成功，schema 仍然构建，候选词只是显示未注释，失败的刷新保留磁盘上已有的副本。`ime-manager status` 显示词典是否已下载，面板也会说明。

## Hyprland：插件报告但不管理的两个部分

一个舒适的设置想要从合成器得到两件事，而两者都不是此插件写入的：

1. 一个切换输入法的键，这样你不必伸手去按 Ctrl+Space
2. 使候选词窗口看起来像桌面其余部分的模糊规则

**这是刻意的。** `~/.config/hypr/` 是你的——常常是某个 dotfiles 模块的——而 Omarchy 提供的唯一 drop-in 是 `~/.local/state/omarchy/toggles/hypr/*.lua`，这意味着此插件会生成可执行 Lua 到你的合成器配置中。Omarchy 自己的 `toggles.lua` 带有 `exclude` 列表正是因为那里的生成 Lua 曾经带有注入的设备名。对一个键绑定来说这是糟糕的交易。

因此插件检测两者，将它们作为状态行报告在面板和 `ime-manager status` 中，并把写入留给你。每行有三个答案——**on**、**not on** 和 **cannot tell**——没有一个失败：仅仅缺失的行是未开启的可选增强，因此它戴的是柔和标记而不是紧急标记。在没有 `hyprctl` 的机器上，或不是 Hyprland 的合成器上，两者都读作 "cannot tell"，这是与 "no" 不同的答案。

### 切换键

在 Omarchy 下，把它放在 `~/.config/hypr/input.lua`。它是一个键_处理器_而非绑定，因为 Fcitx5 通过 Wayland 输入法协议不能可靠地接收仅修饰键的释放事件：

```lua
-- Treat a standalone left Shift tap as the Ctrl+Space input-method toggle,
-- while preserving Shift as a modifier for every other shortcut.
local left_shift_keycode = 50
local left_shift_tap = false

hl.on("input.keyboard.key", function(key, _, state)
  if key == left_shift_keycode then
    if state == 1 then
      left_shift_tap = true
    elseif state == 0 and left_shift_tap then
      left_shift_tap = false
      hl.exec_cmd("fcitx5-remote -t")
    end
    return
  end

  if state == 1 then
    left_shift_tap = false
  end
end)
```

普通绑定也可以，检测不在乎你选哪个键——它寻找的是 dispatch，而不是修饰键：

```
bind = SUPER, space, exec, fcitx5-remote -t
```

### 模糊规则

在 Omarchy 下，在 `~/.config/hypr/looknfeel.lua` 中：

```lua
hl.config({
  decoration = {
    blur = {
      -- Fcitx5 candidate windows are layer surfaces; blurring them keeps the
      -- input method consistent with the rest of the desktop.
      input_methods = true,
      input_methods_ignorealpha = 0.8,
    },
  },
})
```

或在普通 `hyprland.conf` 中：

```
decoration {
    blur {
        input_methods = true
        input_methods_ignorealpha = 0.8
    }
}
```
