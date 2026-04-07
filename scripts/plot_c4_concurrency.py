#!/usr/bin/env python3
"""
绘制图 4-2：多核并发下请求汇聚与 NoC/DRAM 争用示意。
用于论文第四章「多核并发与资源争用」子节。
输出：thesis/figures/c4_concurrency.pdf 与 c4_concurrency.png
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import argparse
import os

# 尝试中文字体，避免方框（macOS / Linux / Windows）
def setup_chinese_font():
    for font in ['PingFang SC', 'Heiti SC', 'SimHei', 'Microsoft YaHei', 'WenQuanYi Micro Hei', 'Noto Sans CJK SC']:
        try:
            plt.rcParams['font.sans-serif'] = [font] + plt.rcParams['font.sans-serif']
            plt.rcParams['axes.unicode_minus'] = False
            return
        except Exception:
            continue


def draw_concurrency_diagram(output_dir='thesis/figures'):
    setup_chinese_font()
    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.set_aspect('equal')
    ax.axis('off')

    # 颜色
    c_load = '#ffe6cc'
    c_wb = '#f8cecc'
    c_wait = '#fff2cc'
    c_collect = '#dae8fc'
    c_noc_dram = '#e1d5e7'
    c_deliver = '#d5e8d4'
    stroke = '#333333'

    def box(ax, xy, w, h, text, fc, ec=stroke, fontsize=10, bold=False):
        style = 'round,pad=0.02' if w > 1.2 else 'round,pad=0.01'
        b = FancyBboxPatch(xy, w, h, boxstyle=style, facecolor=fc, edgecolor=ec, linewidth=1.2)
        ax.add_patch(b)
        weight = 'bold' if bold else 'normal'
        ax.text(xy[0] + w/2, xy[1] + h/2, text, ha='center', va='center', fontsize=fontsize, weight=weight, wrap=True)

    def arrow(ax, start, end, label='', color=stroke):
        ax.annotate('', xy=end, xytext=start, arrowprops=dict(arrowstyle='->', color=color, lw=1.2))
        if label:
            mid = ((start[0]+end[0])/2, (start[1]+end[1])/2)
            ax.text(mid[0], mid[1], label, fontsize=8, ha='center', va='bottom', color=color)

    # 标题
    ax.text(5, 5.75, '同一全局周期：多核并行推进 → 请求汇聚 → 共享资源争用 → 响应投递', ha='center', fontsize=11, weight='bold')

    # 左侧：多核
    ax.text(0.6, 4.6, '多核\n(每周期并行 tick)', ha='center', fontsize=9, color='#666')
    cores = [
        (0.4, 3.8, 'Core 0\nLOADING', c_load),
        (0.4, 3.0, 'Core 1\nWRITEBACK', c_wb),
        (0.4, 2.2, 'Core 2\nLOADING', c_load),
        (0.4, 1.4, 'Core 3\nLOADING（等待更久）', c_wait),
    ]
    for x, y, t, fc in cores:
        box(ax, (x, y), 1.0, 0.65, t, fc, fontsize=8)

    # 引擎统一收集
    box(ax, (2.2, 2.8), 2.0, 1.0, '引擎统一收集\n（按核遍历发送队列）\n注入 NoC / DRAM', c_collect, fontsize=9, bold=True)

    # 请求箭头：各核 → 收集
    for i, (_, y, _, _) in enumerate(cores):
        cy = y + 0.325
        arrow(ax, (1.4, cy), (2.2, 3.3), '请求' if i == 0 else '')

    # NoC
    box(ax, (5.0, 3.6), 2.0, 0.9, 'NoC（片上网络）\n共享链路/路由器 · 仲裁·排队 → 尾延迟', c_noc_dram, fontsize=9)
    arrow(ax, (4.2, 3.4), (5.0, 4.0), '读/写请求(核间→NoC)')

    # DRAM
    box(ax, (5.0, 2.0), 2.0, 0.9, 'DRAM（片外存储）\n通道/Bank 共享 · 排队·行冲突·调度延迟', c_noc_dram, fontsize=9)
    arrow(ax, (4.2, 2.8), (5.0, 2.45), '读/写请求(→DRAM)')

    # 响应投递
    box(ax, (2.2, 0.6), 2.0, 0.65, '响应投递（按节点/目的核）', c_deliver, fontsize=9, bold=True)
    arrow(ax, (6.0, 3.6), (4.2, 1.5), '')
    arrow(ax, (6.0, 2.45), (4.2, 1.5), '')
    ax.text(5.0, 2.5, '读/写响应', fontsize=8, ha='center', color='#82b366')

    # 投递 → 各核（简化：一条弧线回到“各核”）
    ax.annotate('', xy=(1.2, 3.3), xytext=(3.2, 0.95), arrowprops=dict(arrowstyle='->', color='#82b366', lw=1.2, connectionstyle='arc3,rad=0.3'))
    ax.text(2.0, 1.8, '响应回到各核\n→ 部分核因排队/背压\n停留更久', fontsize=8, color='#82b366', ha='center')

    # 底部说明
    ax.text(5, 0.25, '端到端周期与“因 NoC 背压停滞”等统计反映争用效应；同一配置与 IR 下结果可复现。', ha='center', fontsize=9, color='#666')

    plt.tight_layout(pad=0.5)

    os.makedirs(output_dir, exist_ok=True)
    pdf_path = os.path.join(output_dir, 'c4_concurrency.pdf')
    png_path = os.path.join(output_dir, 'c4_concurrency.png')
    fig.savefig(pdf_path, bbox_inches='tight', dpi=150)
    fig.savefig(png_path, bbox_inches='tight', dpi=150)
    plt.close()
    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")


def main():
    parser = argparse.ArgumentParser(description="Plot Fig 4-2: Multi-core concurrency and NoC/DRAM contention")
    parser.add_argument("--output-dir", default="thesis/figures", help="Output directory for PDF/PNG")
    args = parser.parse_args()
    draw_concurrency_diagram(args.output_dir)


if __name__ == '__main__':
    main()
