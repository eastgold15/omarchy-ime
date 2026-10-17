#!/usr/bin/env bash
# 开发用安装脚本：把当前仓库复制成 Omarchy 插件，并重启 shell 让改动生效。
#
# 为什么不用软链接：Omarchy 的插件目录不接受符号链接，必须是真实文件，
# 所以这里用 rsync 做真实复制。每次改完代码跑一次这个脚本即可。
#
# 下次要改什么，对照下面每段开头的注释。

set -euo pipefail

# 仓库根目录（脚本在 scripts/ 下，所以取上一级）。
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# 插件 id —— 必须和 manifest.json 里的 "id" 完全一致。
# 改 id 时两边一起改，否则 shell 会当成另一个插件。
ID="io.github.eastgold15.omarchy.ime"

# Omarchy 加载插件的位置。
DEST="$HOME/.config/omarchy/plugins/$ID"

mkdir -p "$DEST"

# 同步仓库内容到插件目录：
#   -a          保留权限、时间戳，递归复制
#   --delete    删掉目标里仓库已经没有的文件（重命名/删除文件后不会留垃圾）
#   --exclude   不复制的内容：.git 是版本历史，插件不需要；
#               预览图只给 GitHub 看，插件运行用不到
rsync -a --delete \
  --exclude .git \
  --exclude preview.png \
  --exclude preview-light.png \
  "$ROOT/" "$DEST/"

# 复制不会自己保留可执行位以外的约定，确保 CLI 和脚本可直接运行。
# 这个项目没有 bin/ 目录，CLI 就是根目录下的 ime-manager。
chmod +x "$DEST/ime-manager" "$DEST/scripts/"*.sh

# 让 shell 重新扫描插件列表（新增插件时才需要，已有插件这步很快）。
# 命令不存在时静默跳过，不影响手动安装的场景。
if command -v omarchy-shell >/dev/null 2>&1; then
  omarchy-shell shell rescanPlugins || true
fi

# rescanPlugins 只重新发现插件列表；已经加载的 QML 还缓存在运行中的
# shell 里。必须重启 shell，Panel.qml 的改动才会显示出来。
if command -v omarchy-restart-shell >/dev/null 2>&1; then
  omarchy-restart-shell || true
fi

printf 'installed %s\n' "$DEST"
