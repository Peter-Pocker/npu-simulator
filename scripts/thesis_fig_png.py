#!/usr/bin/env python3
"""
将 matplotlib 图形存为「无透明通道」的 RGB PNG。
Matplotlib 默认 PNG 常为 RGBA，XeLaTeX 嵌入 PDF 时可能出现整图空白（仅见图注）。
"""
from __future__ import annotations

import io
from pathlib import Path

from matplotlib.figure import Figure


def save_figure_png_rgb(fig: Figure, path: str | Path, dpi: int = 150) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        fig.savefig(
            path,
            format="png",
            dpi=dpi,
            bbox_inches="tight",
            facecolor="white",
            edgecolor="none",
            transparent=False,
        )
        return

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=dpi,
        bbox_inches="tight",
        facecolor="white",
        edgecolor="none",
        transparent=False,
    )
    buf.seek(0)
    Image.open(buf).convert("RGB").save(path, "PNG")
