#!/usr/bin/env bash
# install_fonts.sh — 把项目自带的中文字体安装到当前用户的字体目录。
#
# 用途：在新 Mac 上克隆本项目后执行一次，让 XeLaTeX (xeCJK / ctex fontset=windows)
#       与 Word/LibreOffice 等其他 GUI 应用都能找到 SimSun/SimHei/FangSong/KaiTi。
#
# 用法：
#   cd thesis/Materials/Fonts
#   ./install_fonts.sh                 # 拷贝缺失的字体（已存在则跳过）
#   ./install_fonts.sh --force         # 强制覆盖已有字体
#   ./install_fonts.sh --uninstall     # 从 ~/Library/Fonts 卸载本项目安装的字体
#
# 需要 macOS。Linux / Windows 请将 DST 改为对应路径。

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST_DIR="$HOME/Library/Fonts"

FONTS=(
  "Simsun.ttc"      # 宋体（含 SimSun 与 NSimSun）
  "simsunb.ttf"     # 宋体扩展 B（生僻汉字）
  "SimHei.ttf"      # 黑体
  "Fangsong.ttf"    # 仿宋
  "Kaiti.ttf"       # 楷体
)

mode="install"
force=false
case "${1:-}" in
  --force)     force=true ;;
  --uninstall) mode="uninstall" ;;
  -h|--help)
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "") ;;
  *) echo "unknown arg: $1" >&2; exit 2 ;;
esac

if [ "$mode" = "install" ]; then
  mkdir -p "$DST_DIR"
  echo "==> Installing fonts to $DST_DIR"
  installed=0; skipped=0; missing=0
  for f in "${FONTS[@]}"; do
    src="$SRC_DIR/$f"
    dst="$DST_DIR/$f"
    if [ ! -f "$src" ]; then
      echo "    [missing] $f  (not in repo)" >&2
      missing=$((missing+1))
      continue
    fi
    if [ -f "$dst" ] && [ "$force" = false ]; then
      echo "    [skip]    $f  (already installed)"
      skipped=$((skipped+1))
      continue
    fi
    cp -f "$src" "$dst"
    echo "    [ok]      $f"
    installed=$((installed+1))
  done

  echo "==> Refreshing fontconfig cache"
  if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f "$DST_DIR" >/dev/null
    echo "    fc-cache done"
  else
    echo "    fc-cache not found (skipped); macOS GUI apps will pick up fonts automatically"
  fi

  echo
  echo "Summary: installed=$installed skipped=$skipped missing=$missing"
  echo "Verify:  fc-list | grep -iE 'simsun|simhei|fangsong|kaiti'"

elif [ "$mode" = "uninstall" ]; then
  echo "==> Uninstalling fonts from $DST_DIR"
  removed=0
  for f in "${FONTS[@]}"; do
    dst="$DST_DIR/$f"
    if [ -f "$dst" ]; then
      rm -f "$dst"
      echo "    [removed] $f"
      removed=$((removed+1))
    fi
  done
  if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f "$DST_DIR" >/dev/null
  fi
  echo "Summary: removed=$removed"
fi
