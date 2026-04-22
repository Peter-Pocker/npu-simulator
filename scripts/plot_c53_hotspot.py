#!/usr/bin/env python3
"""Generate per-core phase-fraction heatmaps for Chapter 5.3 / 5.4 experiments."""

import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "thesis", "Figures")

PHASES = ["Loading", "Compute", "Writeback", "StallNoC"]

EXPERIMENTS = {
    "c5_hotspot_ddr4_baseline": {
        "title": "DDR4 baseline: per-core phase fraction of active time (%)",
        "data": {
            "Core 0": [140_680, 22_352, 64_537, 0],
            "Core 1": [153_077, 22_352, 58_929, 0],
            "Core 2": [144_515, 22_352, 66_362, 0],
            "Core 3": [153_799, 22_352, 62_381, 0],
        },
    },
    "c5_hotspot_hbm_optimized": {
        "title": "HBM optimized: per-core phase fraction of active time (%)",
        "data": {
            "Core 0": [84_488, 22_352, 18_092, 0],
            "Core 1": [102_664, 22_352, 18_713, 0],
            "Core 2": [85_616, 22_352, 18_093, 0],
            "Core 3": [97_415, 22_352, 18_533, 0],
        },
    },
    "c5_hotspot_hbm_sram1024": {
        "title": "HBM + SRAM 1024 B/cyc: per-core phase fraction of active time (%)",
        "data": {
            "Core 0": [28_374, 22_352, 8_589, 0],
            "Core 1": [31_058, 22_352, 8_668, 0],
            "Core 2": [28_853, 22_352, 10_588, 0],
            "Core 3": [30_766, 22_352, 9_429, 0],
        },
    },
}


CSV_EXPERIMENTS = {
    "c5_hotspot_4x4_b1": {
        "title": "4x4 single-die batch=1: per-core phase fraction (%)",
        "csv": os.path.join(os.path.dirname(__file__), "..",
                            "out/c5_dse/cores4x4_b1/workload_summary.csv"),
    },
}


def aggregate_csv(csv_path: str) -> dict:
    """Read workload_summary.csv → {core_label: [loading, compute, writeback, 0]}."""
    per_core = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = int(row["core_id"])
            loading = int(row["loading_cycles"])
            compute = int(row["compute_cycles"])
            wb = int(row["writeback_cycles"])
            if cid not in per_core:
                per_core[cid] = [0, 0, 0, 0]
            per_core[cid][0] += loading
            per_core[cid][1] += compute
            per_core[cid][2] += wb
    return {f"Core {k}": v for k, v in sorted(per_core.items())}


def make_heatmap(name: str, cfg: dict) -> str:
    cores = list(cfg["data"].keys())
    raw = np.array([cfg["data"][c] for c in cores], dtype=float)
    totals = raw.sum(axis=1, keepdims=True)
    totals[totals == 0] = 1
    pct = raw / totals * 100.0

    n_cores = len(cores)
    fig_h = max(3.2, 0.55 * n_cores + 1.0)
    fig, ax = plt.subplots(figsize=(10, fig_h))
    im = ax.imshow(pct, cmap="YlOrRd", vmin=0, vmax=100, aspect="auto")

    ax.set_xticks(range(len(PHASES)))
    ax.set_xticklabels(PHASES, fontsize=12)
    ax.set_yticks(range(len(cores)))
    ax.set_yticklabels(cores, fontsize=12)
    ax.xaxis.set_ticks_position("bottom")

    for i in range(len(cores)):
        for j in range(len(PHASES)):
            val = pct[i, j]
            color = "white" if val > 55 else "black"
            ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                    fontsize=13, fontweight="bold", color=color)

    cbar = fig.colorbar(im, ax=ax, pad=0.02)
    cbar.set_label("%", fontsize=11)

    ax.set_title(cfg["title"], fontsize=13, pad=10)
    plt.tight_layout()

    out_path = os.path.join(OUTPUT_DIR, f"{name}.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for name, cfg in EXPERIMENTS.items():
        path = make_heatmap(name, cfg)
        print(f"Saved: {path}")
    for name, cfg in CSV_EXPERIMENTS.items():
        data = aggregate_csv(cfg["csv"])
        path = make_heatmap(name, {"title": cfg["title"], "data": data})
        print(f"Saved: {path}")
