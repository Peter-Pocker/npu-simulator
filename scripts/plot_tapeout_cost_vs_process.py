#!/usr/bin/env python3
"""
制程越先进，流片成本与芯片整体成本越高 —— 基于公开数据的示意图
数据来源：行业报告、Silicon Analysts、台积电/晶圆厂公开信息综合
"""

import os
os.environ['MPLCONFIGDIR'] = os.path.join(os.path.dirname(__file__), '.mplconfig')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'Heiti SC', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 实际数据（综合多源）
# 流片/掩膜+NRE 成本（百万美元）
nodes = ['28nm\n(约2011)', '16/14nm\n(约2014)', '7nm\n(约2018)', '5nm\n(约2020)', '3nm\n(约2022)']
tapeout_musd = [2, 8, 30, 80, 200]   # 流片/NRE 量级：行业常见区间
# 单颗芯片总成本（美元，高端大芯片/SoC）
per_chip_usd = [40, 80, 150, 426, 550]  # 5nm/3nm 来自公开报道

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.25))  # 16:7
fig.patch.set_facecolor('white')
ax1.set_facecolor('white')
ax2.set_facecolor('white')

x = np.arange(len(nodes))
w = 0.5

# 左图：流片成本（百万美元）
bars1 = ax1.bar(x - w/2, tapeout_musd, w, color='#1976D2', edgecolor='#0D47A1', linewidth=1.2, label='流片/NRE 成本')
ax1.set_ylabel('流片 / NRE 成本（百万美元）', fontsize=14, fontweight='bold')
ax1.set_xlabel('制程节点', fontsize=14, fontweight='bold')
ax1.set_xticks(x)
ax1.set_xticklabels(nodes, fontsize=12)
ax1.set_title('制程越先进，流片成本越高', fontsize=15, fontweight='bold')
ax1.set_ylim(0, 220)
ax1.yaxis.grid(True, linestyle='--', alpha=0.5)
ax1.tick_params(axis='both', labelsize=12)
for i, v in enumerate(tapeout_musd):
    ax1.text(i, v + 5, f'{v}', ha='center', va='bottom', fontsize=13, fontweight='bold')
ax1.legend(loc='upper right', fontsize=12)

# 右图：单颗芯片整体成本（美元）
bars2 = ax2.bar(x - w/2, per_chip_usd, w, color='#C62828', edgecolor='#B71C1C', linewidth=1.2, label='单颗芯片总成本')
ax2.set_ylabel('单颗芯片总成本（美元）', fontsize=14, fontweight='bold')
ax2.set_xlabel('制程节点', fontsize=14, fontweight='bold')
ax2.set_xticks(x)
ax2.set_xticklabels(nodes, fontsize=12)
ax2.set_title('制程越先进，芯片整体成本越高', fontsize=15, fontweight='bold')
ax2.set_ylim(0, 600)
ax2.yaxis.grid(True, linestyle='--', alpha=0.5)
ax2.tick_params(axis='both', labelsize=12)
for i, v in enumerate(per_chip_usd):
    ax2.text(i, v + 12, f'${v}', ha='center', va='bottom', fontsize=13, fontweight='bold')
ax2.legend(loc='upper right', fontsize=12)

fig.suptitle('芯片制造工艺越先进，流片成本与芯片整体成本均显著上升', fontsize=16, fontweight='bold', y=1.02)
fig.text(0.5, -0.02, '数据来源：行业报告、Silicon Analysts、台积电/晶圆厂公开信息综合，数值为量级参考', ha='center', fontsize=11, color='#555')
plt.tight_layout(rect=[0, 0.02, 1, 1])

out = 'scripts/tapeout_cost_vs_process.png'
fig.savefig(out, dpi=200, bbox_inches='tight', facecolor='white')
print(f'Saved: {out}')
