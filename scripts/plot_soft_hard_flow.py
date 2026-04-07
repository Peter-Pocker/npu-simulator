#!/usr/bin/env python3
"""
软硬件协同仿真流程图：从神经网络与芯片配置到仿真结果可视化
"""

import os
os.environ['MPLCONFIGDIR'] = os.path.join(os.path.dirname(__file__), '.mplconfig')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

matplotlib.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'Heiti SC', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(figsize=(8, 10))
ax.set_xlim(0, 10)
ax.set_ylim(0, 14)
ax.set_aspect('equal')
ax.axis('off')
fig.patch.set_facecolor('white')
ax.set_facecolor('white')

# 颜色
c_input = '#E8F4FD'   # 浅蓝 - 输入
c_compile = '#FFF4E6' # 浅橙 - 编译
c_ir = '#E8F5E9'      # 浅绿 - IR
c_sim = '#F3E5F5'     # 浅紫 - 仿真
c_out = '#FFF8E1'     # 浅黄 - 输出
c_viz = '#E0F7FA'     # 浅青 - 可视化
edge = '#37474F'
arrow_c = '#455A64'

def box(ax, x, y, w, h, text, facecolor, fontsize=10):
    p = FancyBboxPatch((x - w/2, y - h/2), w, h, boxstyle="round,pad=0.03,rounding_size=0.15",
                       facecolor=facecolor, edgecolor=edge, linewidth=1.5)
    ax.add_patch(p)
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize, wrap=True)

def arrow(ax, x1, y1, x2, y2):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=arrow_c, lw=2))

# 从上到下，y 递减
y = 12.5
box(ax, 5, y, 7.2, 1.0, '神经网络描述文件\n(模型结构 / 算子)', c_input, 9.5)
box(ax, 5, y - 1.8, 7.2, 1.0, '芯片硬件配置文件\n(核数、带宽、频率等)', c_input, 9.5)

y -= 3.2
arrow(ax, 5, 11.0, 5, 10.4)
box(ax, 5, y, 7.0, 1.1, '编译器\n(调度与映射)', c_compile, 10)

y -= 2.4
arrow(ax, 5, 8.9, 5, 8.3)
box(ax, 5, y, 6.2, 1.0, '中间表示 (IR)\n(调度后的任务与数据流)', c_ir, 9.5)

y -= 2.2
arrow(ax, 5, 6.5, 5, 5.9)
box(ax, 5, y, 6.8, 1.0, '仿真器\n(从 IR 解析任务队列并执行)', c_sim, 9.5)

y -= 2.2
arrow(ax, 5, 4.2, 5, 3.6)
box(ax, 5, y, 7.0, 1.2, '仿真输出\n计算时间 · 各计算核状态记录', c_out, 9.5)

y -= 2.5
arrow(ax, 5, 2.5, 5, 1.9)
box(ax, 5, y, 5.8, 1.0, '仿真结果可视化', c_viz, 10)

# 两个输入框各画箭头到编译器
ax.annotate('', xy=(5, 9.45), xytext=(5, 12.0), arrowprops=dict(arrowstyle='->', color=arrow_c, lw=2))
ax.annotate('', xy=(5, 9.45), xytext=(5, 10.2), arrowprops=dict(arrowstyle='->', color=arrow_c, lw=2))

plt.tight_layout()
out = 'scripts/soft_hard_flow.png'
fig.savefig(out, dpi=200, bbox_inches='tight', facecolor='white')
print(f'Saved: {out}')
