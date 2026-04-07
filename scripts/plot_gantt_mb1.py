#!/usr/bin/env python3
"""Plot MB-1 state trace to RGB PNG（独立工具；正文已不再引用甘特图，可按需手动生成）。"""
import argparse
import csv
import os
import sys
from pathlib import Path

# 与 thesis_fig_png 同目录
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from thesis_fig_png import save_figure_png_rgb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, help="state_trace.csv path")
    parser.add_argument("--output", required=True, help="output PNG path")
    args = parser.parse_args()
    if not os.path.exists(args.trace):
        raise SystemExit(f"Trace not found: {args.trace}")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except ImportError:
        raise SystemExit("matplotlib required: pip install matplotlib")

    rows = []
    with open(args.trace, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append((int(row["cycle"]), int(row["core_id"]), row["new_state"]))
    rows.sort(key=lambda x: (x[1], x[0]))

    colors = {"LOADING": "#ff7f0e", "COMPUTING": "#1f77b4", "WRITEBACK": "#d62728"}
    segments = []
    max_cycle = max(r[0] for r in rows) if rows else 0
    cores = sorted(set(r[1] for r in rows))

    for core in cores:
        core_events = [(c, s) for cy, c, s in rows if c == core]
        current_state = "IDLE"
        start_cycle = 0
        for cycle, new_state in core_events:
            if current_state in colors:
                duration = cycle - start_cycle
                if duration > 0:
                    segments.append((core, start_cycle, duration, current_state))
            current_state = new_state
            start_cycle = cycle
        if current_state in colors and start_cycle < max_cycle:
            segments.append((core, start_cycle, max_cycle - start_cycle, current_state))

    fig, ax = plt.subplots(figsize=(10, 4))
    # 使用 broken_barh 绘制时间条，比 barh 在部分环境下更稳定
    y_half = 0.35
    for core, start, duration, state in segments:
        ax.broken_barh(
            [(start, duration)],
            (core - y_half, 2 * y_half),
            facecolors=colors[state],
            linewidth=0,
        )
    ax.set_xlabel("Time (cycle)", fontsize=12)
    ax.set_ylabel("Core ID", fontsize=12)
    ax.set_yticks(cores)
    ax.set_ylim(-0.5, max(cores) + 0.5)
    ax.set_xlim(0, max(max_cycle, 1))
    legend_elements = [Patch(facecolor=colors[k], edgecolor="none", label=k) for k in colors]
    ax.legend(handles=legend_elements, loc="upper right")
    plt.tight_layout()
    save_figure_png_rgb(fig, args.output, dpi=150)
    plt.close()
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
