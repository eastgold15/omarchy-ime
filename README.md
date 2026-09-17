# Omarchy IME

一个 Omarchy 状态栏插件，掌管整个输入法：它安装 Fcitx5 和 Rime，根据少量开关生成它们的配置，并在状态栏上用实时输入状态指示器取代 `omarchy.keyboard-layout`——因为决定一次按键是否变成中文的是 Fcitx5 状态，而不是键盘布局。

插件 id：`eastgold15.omarchy.ime`。

## 功能

- **输入状态指示器**——状态栏显示实时 Fcitx5 状态而非键盘布局：Fcitx5 未运行时显示 `󰌌`，英文模式显示 `EN`，组字时显示当前 schema 的名称
- **双向候选词注释**（可选，默认关闭）——中文候选词上标注英文释义，英文候选词上标注中文释义，组字时用一个键切换
- **生成的配置**——Rime 和 Fcitx5 配置在每次部署时从少量开关完整写出，因此重复部署是无操作
- **繁体输出**——通过 `s2twp` OpenCC 配置输出台湾风格繁体，默认开启且可关闭
- **候选词窗口跟随 Omarchy 主题**——classicui 主题根据当前启用的 Omarchy 调色板生成
- **经验证的重建**——部署会要求 Rime 重新编译，然后检查它实际构建了什么，因此 Rime 拒绝的配置是失败的部署，而不是静默损坏的输入法
- **可逆**——每个写入的文件都被记录；uninstall 删除插件创建的文件、恢复它覆盖的文件

## 要求

- Arch Linux
- 带 Quickshell shell 的 Omarchy（`omarchy plugin` 和 `omarchy bar` 可用）
- Python 3（仅标准库）

你**不需要**自己安装输入法，插件会安装自己的软件包（见下文）。

## 安装

```bash
omarchy plugin add https://github.com/eastgold15/omarchy-ime --enable --yes
```
omarchy bar move eastgold15.omarchy.ime --after omarchy.clockz

然后安装软件包并部署：

```bash
~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager packages install
~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager deploy
```

两点安装后要做的事：

- 如果你之前使用 `omarchy.keyboard-layout`，请手动从 `~/.config/omarchy/shell.json` 的 `bar.layout` 中删除它，否则状态栏上会同时出现两个指示器
- 想用全拼以外的输入方案（双拼、注音等），在**首次部署之前**设置：`ime-manager settings set schemas=rime_ice,double_pinyin_flypy`

细节（移动组件位置、从现有配置迁移、部署原理、备份）见[使用指南](docs/guide.md)。

## 卸载

顺序很重要：先还原部署，再移除插件。

```bash
~/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager uninstall
omarchy plugin remove eastgold15.omarchy.ime
```

`uninstall` 删除插件创建的文件、从备份恢复它覆盖的文件，并移除主题钩子和环境覆盖。你的 Rime 用户数据（学到的短语、同步状态）绝不被触碰。软件包（`fcitx5`、`rime-ice-git` 等）被有意留下，想要它们消失请自行移除。

## 文档

- [使用指南](docs/guide.md)——设置表、候选词注释、词典、Hyprland 切换键和模糊规则、部署与卸载的完整细节
- [设计思路](design.md)——这个插件为什么这样设计
