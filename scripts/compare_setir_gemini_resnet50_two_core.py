#!/usr/bin/env python3
"""
用相同硬件配置（2×2 mesh）分别生成 SET-IR 和 Gemini 的 ResNet50 调度 IR，并对比两者差异。
（注：SET-IR 对 2 核 1×2/2×1 存在 DRAM 端口布局限制，故用 2×2 保证两者可跑通）

硬件约定（与 configs/chips/two_core_resnet.json 一致）：
  - 2×2 核，batch=1
  - 单核 256 MAC、512KB SRAM
  - NoC 带宽 16（SET-IR 的 bw 参数）

用法:
  python3 scripts/compare_setir_gemini_resnet50_two_core.py
  python3 scripts/compare_setir_gemini_resnet50_two_core.py --no-setir   # 仅 Gemini，用于 SET-IR 未就绪时
  python3 scripts/compare_setir_gemini_resnet50_two_core.py --no-gemini # 仅 SET-IR
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_setir(root: Path) -> Path:
    """确保 SET-IR 子模块存在并已构建。返回 SET-IR 目录。"""
    setir = root / "SET-IR"
    if not setir.is_dir():
        print("[compare] SET-IR 目录不存在，尝试初始化子模块...")
        subprocess.run(
            ["git", "submodule", "update", "--init", "SET-IR"],
            cwd=root,
            check=True,
        )
    if not setir.is_dir():
        raise SystemExit("SET-IR 未找到。请先添加子模块: git submodule add https://github.com/EliminateSpace/SET-IR.git SET-IR")
    build_exe = setir / "build" / "stschedule"
    if not build_exe.is_file():
        print("[compare] 构建 SET-IR...")
        subprocess.run(["make"], cwd=setir, check=True)
    return setir


def run_setir(setir_dir: Path, out_ir_path: Path) -> None:
    """
    SET-IR 输入格式: dataflow net batch x y stride round cost bw [unicast_only]
    - dataflow: 0=Polar/Simba, 1=Eyeriss
    - net: 2 = resnet50
    - batch=1, x=2, y=2 (2×2 mesh，SET-IR 要求 x,y>=2)
    - stride=1, round=1, cost=0 (delay), bw=16
    SET-IR 将 IR 写入 results/json/<base>_SA-LS.json，复制到 out_ir_path。
    """
    exe = setir_dir / "build" / "stschedule"
    # 2×2 mesh, batch=1
    stdin = "0 2 1 2 2 1 1 0 16 0\n"
    out_base = "resnet50_2x2"
    proc = subprocess.run(
        [str(exe), out_base],
        input=stdin.encode("utf-8"),
        cwd=setir_dir,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(f"SET-IR 退出码 {proc.returncode}")
    # SET-IR 输出到 results/json/<base>_SA-LS.json（最终 SA 结果）
    setir_result = setir_dir / "results" / "json" / f"{out_base}_SA-LS.json"
    if not setir_result.is_file():
        # 备选：LP-SA 或 LS-opt-SA
        for suffix in ("_SA-LS.json", "_LP-SA.json", "_LS-opt-SA.json"):
            alt = setir_dir / "results" / "json" / f"{out_base}{suffix}"
            if alt.is_file():
                setir_result = alt
                break
    if not setir_result.is_file():
        raise SystemExit(f"SET-IR 未生成预期 IR: {setir_result}")
    out_ir_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(setir_result, out_ir_path)


def run_gemini(root: Path, config_path: Path, out_dir: Path) -> Path:
    """调用 gemini_run.py 生成 Gemini IR，返回生成的 IR 文件路径。"""
    gemini_run = root / "scripts" / "gemini_run.py"
    cmd = [
        sys.executable,
        str(gemini_run),
        "-c", str(config_path),
        "-n", "resnet",
        "--skip-sim",
        "-t", str(out_dir),
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        raise SystemExit(f"gemini_run 退出码 {proc.returncode}")
    # gemini_run 会创建 out_dir/trace_YYYYMMDD_HHMMSS/resnet_2x1_ir.json
    trace_dirs = sorted([d for d in out_dir.iterdir() if d.is_dir() and d.name.startswith("trace_")], key=lambda d: d.name, reverse=True)
    if not trace_dirs:
        raise SystemExit("gemini_run 未在 -t 目录下创建 trace_* 目录")
    ir_path = trace_dirs[0] / "resnet_2x2_ir.json"
    if not ir_path.is_file():
        raise SystemExit(f"未找到 Gemini IR: {ir_path}")
    return ir_path


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def convert_setir_to_gemini_format(root: Path, setir_path: Path, out_path: Path, buffersize: int = 8 * 1024 * 1024) -> None:
    """将 SET-IR 的 IR 转为 Gemini Scheduler IR 格式，便于仿真器统一用 gemini 解析。"""
    converter = root / "scripts" / "setir_to_gemini_ir.py"
    if not converter.is_file():
        raise SystemExit(f"转换脚本不存在: {converter}")
    subprocess.run(
        [sys.executable, str(converter), str(setir_path), "-o", str(out_path), "--buffersize", str(buffersize)],
        cwd=root,
        check=True,
    )
    print(f"[compare] SET-IR → Gemini 格式已写入: {out_path}")


def compare_irs(setir_data: dict, gemini_data: dict) -> list[str]:
    """对比两种 IR 的差异，返回报告行列表。"""
    lines = []
    lines.append("=" * 60)
    lines.append("SET-IR vs Gemini ResNet50 2×2 核 IR 对比")
    lines.append("=" * 60)

    # 顶层维度
    def get_dim(d: dict, xkey: str, ykey: str) -> tuple[int, int]:
        x = d.get(xkey, d.get("xlen", 0))
        y = d.get(ykey, d.get("ylen", 0))
        return int(x), int(y)

    setir_x, setir_y = get_dim(setir_data, "xlen", "ylen")
    gemini_x, gemini_y = get_dim(gemini_data, "xlen", "ylen")
    lines.append(f"\n【拓扑】")
    lines.append(f"  SET-IR:  xlen={setir_x}, ylen={setir_y}")
    lines.append(f"  Gemini:  xlen={gemini_x}, ylen={gemini_y}")
    if (setir_x, setir_y) != (gemini_x, gemini_y):
        lines.append("  -> 维度不一致")

    # 其他顶层字段
    for key in ("top_batch_cut", "buffersize"):
        if key in setir_data or key in gemini_data:
            s_val = setir_data.get(key, "N/A")
            g_val = gemini_data.get(key, "N/A")
            lines.append(f"  {key}: SET-IR={s_val}, Gemini={g_val}")

    # 核 ID 与 workload 数量
    def core_keys(d: dict) -> list[str]:
        skip = {"xlen", "ylen", "top_batch_cut", "buffersize", "-1"}
        return [k for k in d if k not in skip and (k.isdigit() or (k.lstrip("-").isdigit() and int(k) >= 0))]

    setir_cores = sorted(core_keys(setir_data), key=int)
    gemini_cores = sorted(core_keys(gemini_data), key=int)
    lines.append(f"\n【核与 workload】")
    lines.append(f"  SET-IR 核 ID: {setir_cores[:20]}{'...' if len(setir_cores) > 20 else ''} (共 {len(setir_cores)} 核)")
    lines.append(f"  Gemini 核 ID: {gemini_cores[:20]}{'...' if len(gemini_cores) > 20 else ''} (共 {len(gemini_cores)} 核)")

    setir_total_wl = sum(len(setir_data.get(c, [])) for c in setir_cores)
    gemini_total_wl = sum(len(gemini_data.get(c, [])) for c in gemini_cores)
    lines.append(f"  SET-IR 总 workload 数: {setir_total_wl}")
    lines.append(f"  Gemini 总 workload 数: {gemini_total_wl}")

    # 每核 workload 数分布
    setir_per_core = [len(setir_data.get(c, [])) for c in setir_cores]
    gemini_per_core = [len(gemini_data.get(c, [])) for c in gemini_cores]
    lines.append(f"  SET-IR 每核 workload 数: min={min(setir_per_core) if setir_per_core else 0}, max={max(setir_per_core) if setir_per_core else 0}")
    lines.append(f"  Gemini 每核 workload 数: min={min(gemini_per_core) if gemini_per_core else 0}, max={max(gemini_per_core) if gemini_per_core else 0}")

    # 抽样对比：第一核的 workload 字段结构
    lines.append(f"\n【IR 结构差异】")
    if setir_cores and gemini_cores:
        s0 = setir_data.get(setir_cores[0], [])
        g0 = gemini_data.get(gemini_cores[0], [])
        if s0 and g0:
            sw = s0[0]
            gw = g0[0]
            s_keys = set(sw.keys()) if isinstance(sw, dict) else set()
            g_keys = set(gw.keys()) if isinstance(gw, dict) else set()
            only_setir = s_keys - g_keys
            only_gemini = g_keys - s_keys
            if only_setir:
                lines.append(f"  仅 SET-IR 有的 workload 字段: {sorted(only_setir)}")
            if only_gemini:
                lines.append(f"  仅 Gemini 有的 workload 字段: {sorted(only_gemini)}")
            if not only_setir and not only_gemini:
                lines.append("  首核首 workload 顶层键一致")
        # layer 名称抽样
        def layer_names(data: dict, core_list: list) -> list[str]:
            names = []
            for c in core_list[:3]:
                for w in data.get(c, [])[:5]:
                    if isinstance(w, dict) and "layer_name" in w:
                        names.append(w["layer_name"])
            return names
        lines.append(f"  SET-IR 前若干 layer_name: {layer_names(setir_data, setir_cores)[:10]}")
        lines.append(f"  Gemini 前若干 layer_name: {layer_names(gemini_data, gemini_cores)[:10]}")

    # DRAM 段（-1）
    if "-1" in setir_data or "-1" in gemini_data:
        lines.append(f"\n【DRAM 段 (-1)】")
        lines.append(f"  SET-IR 有 -1: {'是' if '-1' in setir_data else '否'}")
        lines.append(f"  Gemini 有 -1: {'是' if '-1' in gemini_data else '否'}")

    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="SET-IR 与 Gemini 生成 ResNet50 双核 IR 并对比")
    ap.add_argument("--no-setir", action="store_true", help="不运行 SET-IR，仅用已有 SET-IR IR 文件（需与 --setir-ir 配合或跳过对比）")
    ap.add_argument("--no-gemini", action="store_true", help="不运行 Gemini，仅用已有 Gemini IR 文件")
    ap.add_argument("--setir-ir", type=str, default="", help="已有 SET-IR IR 文件路径（不填则自动生成）")
    ap.add_argument("--gemini-ir", type=str, default="", help="已有 Gemini IR 文件路径（不填则自动生成）")
    ap.add_argument("-o", "--output-dir", type=str, default="experiments/ir_compare", help="输出与中间文件目录")
    ap.add_argument("--convert-setir-to-gemini", action="store_true",
                    help="将 SET-IR 输出转为 Gemini Scheduler IR 格式并写出，便于仿真器用 --ir-format gemini 直接加载")
    args = ap.parse_args()

    root = project_root()
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "configs" / "chips" / "two_core_resnet.json"
    if not config_path.is_file():
        raise SystemExit(f"配置文件不存在: {config_path}")

    setir_ir_path = Path(args.setir_ir) if args.setir_ir else out_dir / "resnet50_2x2_setir.json"
    gemini_ir_path = Path(args.gemini_ir) if args.gemini_ir else None

    if not args.no_setir and not args.setir_ir:
        setir_dir = ensure_setir(root)
        print("[compare] 运行 SET-IR (ResNet50, 2×1, batch=1)...")
        run_setir(setir_dir, setir_ir_path)
        print(f"[compare] SET-IR IR 已写入: {setir_ir_path}")
    elif not setir_ir_path.is_file():
        raise SystemExit("未提供 --setir-ir 且未生成 SET-IR IR，无法对比。请去掉 --no-setir 或指定 --setir-ir。")

    if args.convert_setir_to_gemini and setir_ir_path.is_file():
        gemini_format_path = out_dir / "resnet50_2x2_setir_gemini_format.json"
        try:
            convert_setir_to_gemini_format(root, setir_ir_path, gemini_format_path)
        except (subprocess.CalledProcessError, SystemExit) as e:
            print(f"[compare] 转换 SET-IR→Gemini 格式失败: {e}", file=sys.stderr)

    if not args.no_gemini:
        gemini_out = out_dir / "gemini_trace"
        gemini_out.mkdir(parents=True, exist_ok=True)
        print("[compare] 运行 Gemini (resnet, 2×1)...")
        gemini_ir_path = run_gemini(root, config_path, gemini_out)
        print(f"[compare] Gemini IR: {gemini_ir_path}")

    if gemini_ir_path is None or not gemini_ir_path.is_file():
        print("[compare] 未生成或未指定 Gemini IR，跳过对比。")
        return 0

    setir_data = load_json(setir_ir_path)
    gemini_data = load_json(gemini_ir_path)
    report = compare_irs(setir_data, gemini_data)
    report_path = out_dir / "setir_vs_gemini_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\n" + "\n".join(report))
    print(f"报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
