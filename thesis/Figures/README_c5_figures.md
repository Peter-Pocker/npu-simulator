# 第五章插图说明

## 图5-1（MB-1 状态转移时间线）

- **数据来源**：`experiments/runs/mb1_full/state_trace.csv`（MB-1 全后端运行 trace）
- **矢量图**：`c5_mb1_trace.svg` 已根据上述数据绘制（Core 0/1 的 LOADING / COMPUTING / WRITEBACK 甘特图）

### 在论文中直接使用 SVG（推荐）

本项目已配置 `svg` 宏包，可在正文中直接引用 SVG，编译时会自动转为 PDF：

- 在 `.tex` 中写：`\includesvg[width=\linewidth]{Figures/c5_mb1_trace}`（不要加 `.svg` 后缀）
- 需已安装 Inkscape，且使用 `latexmk` 编译（已通过 `.latexmkrc` 开启 `-shell-escape`）

### 或手动生成 PDF 供 LaTeX 使用

若不想依赖 Inkscape 编译，可先手动从 SVG 导出 PDF：

```bash
# 方式一：rsvg-convert（需安装 librsvg）
rsvg-convert -f pdf -o thesis/Figures/c5_mb1_trace.pdf thesis/Figures/c5_mb1_trace.svg

# 方式二：Inkscape
inkscape thesis/Figures/c5_mb1_trace.svg --export-type=pdf --export-filename=thesis/Figures/c5_mb1_trace.pdf
```

### 使用 Python 重新绘制（需 pandas + matplotlib）

```bash
pip install pandas matplotlib
python scripts/plot_gantt.py --trace experiments/runs/mb1_full/state_trace.csv --output thesis/Figures/c5_mb1_trace.png
```

随后在 `c5.tex` 中将 `c5_mb1_trace.pdf` 改为 `c5_mb1_trace.png` 即可。
