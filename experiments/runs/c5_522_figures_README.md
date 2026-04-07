# 第五章 §5.2.2 图件生成说明

图件由 `scripts/gen_c5_522_figures.py` 生成，输出至 `thesis/Figures/`（与模板 `\\graphicspath{{./Figures/}}` 一致）：

| 文件 | 含义 |
|------|------|
| `c5_522_hotspot_mb1.png` | MB-1 各核 Loading / Compute / Writeback / StallNoC 占该核活跃时间比例的热力图，数据来自同目录 `workload_summary.csv` |
| `c5_522_resnet_phase_bars.png` | 单核 ResNet34/50（批大小 1）计算与访存相关等待占比堆叠条，与 `experiments/runs/bottleneck_breakdown_b1/bottleneck_breakdown.csv` 一致 |

复现命令（项目根目录）：

```bash
python3 scripts/gen_c5_522_figures.py
```

说明：脚本经 `scripts/thesis_fig_png.py` 将图保存为 **RGB PNG（无 alpha 通道）**。Matplotlib 默认 PNG 常为 RGBA，XeLaTeX 嵌入 PDF 时可能出现整图不显示、仅见图注的现象。
