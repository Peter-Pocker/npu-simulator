#!/usr/bin/env python3
"""
AI Accelerator (NPU/GPU/TPU) Peak Performance Growth Over Time
Data sources: NVIDIA, Google, Huawei official specifications
"""

import matplotlib.pyplot as plt
import matplotlib
import numpy as np

matplotlib.rcParams['font.family'] = ['Arial', 'Helvetica', 'DejaVu Sans']

# ── Data ──────────────────────────────────────────────────────────────
# Format: (year, name, peak_TFLOPS_FP16_or_BF16)

nvidia = [
    (2016, "P100",   21.2),
    (2017, "V100",   120),
    (2020, "A100",   312),
    (2022, "H100",   990),
    (2024, "B200",   4500),
]

google_tpu = [
    (2017, "TPU v2",  46),
    (2018, "TPU v3",  123),
    (2021, "TPU v4",  275),
    (2023, "TPU v5e", 459),
    (2024, "TPU v6e", 918),
    (2025, "Ironwood", 2307),
]

huawei = [
    (2018, "Ascend 310",  8),
    (2019, "Ascend 910",  320),
    (2023, "Ascend 910B", 256),
    (2025, "Ascend 910C", 800),
]

# ── Plot ──────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 6.5))

def plot_series(data, color, marker, label):
    years  = [d[0] for d in data]
    tflops = [d[2] for d in data]
    names  = [d[1] for d in data]
    ax.plot(years, tflops, marker=marker, color=color, linewidth=2.2,
            markersize=9, label=label, zorder=3)
    for y, t, n in zip(years, tflops, names):
        offset_y = 1.35 if t > 100 else 1.5
        ax.annotate(f"{n}\n{t:,.0f}", (y, t),
                    textcoords="offset points", xytext=(0, 14),
                    ha='center', fontsize=7.5, color=color, fontweight='bold')

plot_series(nvidia,     "#76B900", "o", "NVIDIA GPU")
plot_series(google_tpu, "#4285F4", "s", "Google TPU")
plot_series(huawei,     "#E60012", "D", "Huawei Ascend")

ax.set_yscale('log')
ax.set_xlabel("Year", fontsize=13, fontweight='bold')
ax.set_ylabel("Peak Performance (TFLOPS, FP16/BF16, log scale)", fontsize=13, fontweight='bold')
ax.set_title("AI Accelerator Peak Performance Growth (2016 – 2025)",
             fontsize=15, fontweight='bold', pad=16)

ax.set_xticks(range(2016, 2026))
ax.set_xlim(2015.5, 2025.8)
ax.set_ylim(5, 10000)
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
    lambda v, _: f"{v:,.0f}"))
ax.grid(True, which='major', linestyle='--', alpha=0.4)
ax.grid(True, which='minor', linestyle=':', alpha=0.2)
ax.legend(fontsize=11, loc='upper left', framealpha=0.9)
ax.tick_params(labelsize=11)

fig.tight_layout()
out = "scripts/npu_performance_timeline.png"
fig.savefig(out, dpi=200, bbox_inches='tight')
print(f"Chart saved to {out}")

# ── Also print a markdown table ───────────────────────────────────────
print("\n## AI Accelerator Peak Performance (FP16/BF16 TFLOPS)\n")
all_data = ([(y, n, t, "NVIDIA")  for y, n, t in nvidia] +
            [(y, n, t, "Google")  for y, n, t in google_tpu] +
            [(y, n, t, "Huawei")  for y, n, t in huawei])
all_data.sort(key=lambda x: (x[0], x[1]))

print(f"| {'Year':^4} | {'Vendor':^8} | {'Chip':^14} | {'FP16/BF16 TFLOPS':^18} |")
print(f"|{'-'*6}|{'-'*10}|{'-'*16}|{'-'*20}|")
for y, n, t, v in all_data:
    print(f"| {y}   | {v:<8} | {n:<14} | {t:>14,.0f}     |")
