#!/usr/bin/env python3
"""
绘制图 5-4：端到端瓶颈占比（Compute / Mem_Stall 堆叠柱状图，单核下 NoC%=0）。
数据来源：表 5-4 / experiments/runs/bottleneck_breakdown_b1/bottleneck_breakdown.csv
输出：thesis/Figures/c5_breakdown.png（仅 PNG）

用法（需先安装依赖）：
  pip install pandas matplotlib
  python scripts/plot_c5_breakdown.py
若 CSV 不存在，将使用与表 5-4 一致的默认数据绘制。
"""
import argparse
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from thesis_fig_png import save_figure_png_rgb

import matplotlib.pyplot as plt
import pandas as pd

# 默认数据（与表 5-4 一致，当 CSV 不存在时使用）
DEFAULT_DATA = [
    {"model": "ResNet34", "pct_Compute": 81.0, "pct_NoC_Stall": 0.0, "pct_Mem_Stall": 19.0},
    {"model": "ResNet50", "pct_Compute": 76.6, "pct_NoC_Stall": 0.0, "pct_Mem_Stall": 23.4},
]


def load_data(csv_path: str):
    """从 CSV 加载或使用默认数据。"""
    if os.path.isfile(csv_path):
        df = pd.read_csv(csv_path)
        # 取 batch=1，若有多种 batch 可指定
        if "batch" in df.columns:
            df = df[df["batch"] == 1]
        df = df.rename(columns={
            "pct_Compute": "pct_Compute",
            "pct_Mem_Stall": "pct_Mem_Stall",
            "pct_NoC_Stall": "pct_NoC_Stall",
        })
        # 百分比取一位小数以便与表一致
        for c in ["pct_Compute", "pct_NoC_Stall", "pct_Mem_Stall"]:
            if c in df.columns:
                df[c] = df[c].round(1)
        return df
    return pd.DataFrame(DEFAULT_DATA)


def main():
    parser = argparse.ArgumentParser(description="Plot Fig 5-4: End-to-end bottleneck breakdown (stacked bar)")
    parser.add_argument(
        "--csv",
        default="experiments/runs/bottleneck_breakdown_b1/bottleneck_breakdown.csv",
        help="Path to bottleneck_breakdown.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="thesis/Figures",
        help="Directory for output PNG",
    )
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(repo_root, args.csv)
    out_dir = os.path.join(repo_root, args.output_dir)

    df = load_data(csv_path)
    if df.empty:
        print("No data to plot.", file=sys.stderr)
        sys.exit(1)

    models = df["model"].tolist()
    compute = df["pct_Compute"].tolist()
    noc = df["pct_NoC_Stall"].tolist()
    mem = df["pct_Mem_Stall"].tolist()

    fig, ax = plt.subplots(figsize=(5, 4))
    x = range(len(models))
    width = 0.5

    bars1 = ax.bar(x, compute, width, label="Compute", color="tab:blue")
    bars2 = ax.bar(x, mem, width, bottom=compute, label="Mem_Stall", color="tab:orange")
    # NoC 单核下为 0，若需显示可取消下一行
    # bars3 = ax.bar(x, noc, width, bottom=[c + m for c, m in zip(compute, mem)], label="NoC\_Stall", color="tab:green")

    ax.set_ylabel("Percentage (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(models)
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right")
    ax.set_title("End-to-end bottleneck breakdown (single-core, batch=1; NoC%=0)")
    fig.tight_layout()

    os.makedirs(out_dir, exist_ok=True)
    out_png = os.path.join(out_dir, "c5_breakdown.png")
    save_figure_png_rgb(fig, out_png, dpi=150)
    print("Wrote", out_png)
    plt.close()


if __name__ == "__main__":
    main()
