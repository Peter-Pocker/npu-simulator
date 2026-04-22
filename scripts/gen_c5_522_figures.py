#!/usr/bin/env python3
"""
Generate figures for thesis §5.2.2:
  - MB-1 hotspot heatmap from workload_summary.csv

单核 ResNet 堆叠条与 5.2.1 节图共用，由 scripts/plot_c5_breakdown.py 生成 c5_breakdown.png。

Output directory: thesis/Figures/（全部为 PNG，经 thesis_fig_png 存为 RGB）
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from thesis_fig_png import save_figure_png_rgb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 与 XJTU-thesis.cls 中 \\graphicspath{{./Figures/}} 一致，图文件放在 thesis/Figures/
FIG_DIR = PROJECT_ROOT / "thesis" / "Figures"
MB1_TRACE = PROJECT_ROOT / "experiments" / "runs" / "mb1_simple"


def _save_fig_png(fig, path: Path) -> None:
    """经 PIL 转为 RGB PNG，去掉 alpha，避免 XeLaTeX 下图示空白。"""
    save_figure_png_rgb(fig, path, dpi=150)


def plot_hotspot_mb1() -> Path:
    """Heatmap: cores x (Loading, Compute, Writeback, StallNoC) as % of per-core phase sum."""
    ws = MB1_TRACE / "workload_summary.csv"
    rows = []
    with open(ws, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    # Per workload row: loading_cycles, compute_cycles, writeback_cycles
    mat = []
    ylabels = []
    for row in rows:
        cid = int(row["core_id"])
        lc = float(row["loading_cycles"])
        cc = float(row["compute_cycles"])
        wc = float(row["writeback_cycles"])
        stall = 0.0  # MB-1 simple has no StallNoC in summary
        s = lc + cc + wc + stall
        if s <= 0:
            s = 1.0
        mat.append([100.0 * lc / s, 100.0 * cc / s, 100.0 * wc / s, 100.0 * stall / s])
        ylabels.append(f"Core {cid}")
    data = np.array(mat)

    fig, ax = plt.subplots(figsize=(8, 3.5))
    im = ax.imshow(data, aspect="auto", cmap="YlOrRd", vmin=0, vmax=100)
    ax.set_xticks(range(4))
    ax.set_xticklabels(["Loading", "Compute", "Writeback", "StallNoC"])
    ax.set_yticks(range(len(ylabels)))
    ax.set_yticklabels(ylabels)
    ax.set_title("MB-1: per-core phase fraction of active time (%)")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("%")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            ax.text(j, i, f"{data[i, j]:.1f}", ha="center", va="center", color="black", fontsize=9)
    plt.tight_layout()
    out = FIG_DIR / "c5_522_hotspot_mb1.png"
    _save_fig_png(fig, out)
    plt.close(fig)
    return out


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    assert (MB1_TRACE / "state_trace.csv").exists(), f"Missing {MB1_TRACE}/state_trace.csv"

    p1 = plot_hotspot_mb1()
    print("Wrote:", p1, sep="\n")


if __name__ == "__main__":
    main()
