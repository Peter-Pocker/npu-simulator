#!/usr/bin/env python3
"""
Generate figures for thesis §5.2.2:
  - MB-1 hotspot heatmap from workload_summary.csv
  - ResNet34/50 single-core phase stacked bars (matches bottleneck_breakdown_b1.csv)

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
BREAKDOWN_B1 = PROJECT_ROOT / "experiments" / "runs" / "bottleneck_breakdown_b1" / "bottleneck_breakdown.csv"


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


def plot_resnet_phase_bars() -> Path:
    """Stacked horizontal bars from bottleneck_breakdown_b1 (batch 1)."""
    models = []
    pct_c = []
    pct_m = []
    with open(BREAKDOWN_B1, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["batch"] != "1":
                continue
            models.append(row["model"].replace("ResNet", "RN"))
            pct_c.append(float(row["pct_Compute"]))
            pct_m.append(float(row["pct_Mem_Stall"]))
    y = np.arange(len(models))
    fig, ax = plt.subplots(figsize=(8, 2.8))
    w1 = [c / 100.0 for c in pct_c]
    w2 = [m / 100.0 for m in pct_m]
    ax.barh(y, w1, color="tab:blue", label=r"$T_{\mathrm{Compute}}$")
    ax.barh(y, w2, left=w1, color="tab:orange", label=r"$T_{\mathrm{Mem}}$ (approx.)")
    ax.set_yticks(y)
    ax.set_yticklabels(models)
    ax.set_xlabel("Fraction of end-to-end time")
    ax.set_xlim(0, 1)
    ax.legend(loc="lower right")
    ax.set_title("")  # 图题在论文正文中以中文给出
    plt.tight_layout()
    out = FIG_DIR / "c5_522_resnet_phase_bars.png"
    _save_fig_png(fig, out)
    plt.close(fig)
    return out


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    assert (MB1_TRACE / "state_trace.csv").exists(), f"Missing {MB1_TRACE}/state_trace.csv"
    assert BREAKDOWN_B1.exists(), f"Missing {BREAKDOWN_B1}"

    p1 = plot_hotspot_mb1()
    p2 = plot_resnet_phase_bars()
    print("Wrote:", p1, p2, sep="\n")


if __name__ == "__main__":
    main()
