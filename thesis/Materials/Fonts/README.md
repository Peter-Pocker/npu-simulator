# 项目自带中文字体

本目录存放论文编译所需的中文字体（来自 Microsoft Word for Mac），
配合 `\PassOptionsToPackage{fontset=windows}{ctex}` 与 cls 中 macOS 分支的
`\setCJKmainfont{SimSun}` 使用，保证全角引号、标点字宽符合 GB/T 15834-2011。

## 字体清单

| 文件 | 名称 | 用途 |
|---|---|---|
| `Simsun.ttc` | SimSun + NSimSun | `\songti` 主字体，正文 |
| `simsunb.ttf` | SimSun-ExtB | 生僻汉字（CJK Unified Ideographs Extension B） |
| `SimHei.ttf` | SimHei | `\heiti` 黑体，章节标题 |
| `Fangsong.ttf` | FangSong | `\fangsong` 仿宋，引用 |
| `Kaiti.ttf` | KaiTi | `\kaishu` 楷体 |

## 安装到系统（新机器克隆后执行一次）

```bash
cd thesis/Materials/Fonts
./install_fonts.sh
```

脚本会把字体拷贝到 `~/Library/Fonts/` 并刷新 fontconfig 缓存。
之后 `make pdf` 即可正常编译。

其他选项：
- `./install_fonts.sh --force`     强制覆盖已有字体
- `./install_fonts.sh --uninstall` 从 `~/Library/Fonts/` 卸载
- `./install_fonts.sh --help`      查看说明

## 版权说明

SimSun / SimHei / FangSong / KaiTi 是 Microsoft 与中易公司的授权字体，
随 Microsoft Office for Mac 一起分发。本目录下的字体文件来自
`/Applications/Microsoft Word.app/Contents/Resources/DFonts/`，仅供
本论文项目本地编译使用，**不要将本目录公开发布到第三方仓库**。

如需公开仓库，请改用开源替代字体（如思源宋体 Source Han Serif SC），
并修改 `XJTU-thesis.cls` 中的 `\setCJKmainfont` 配置。
