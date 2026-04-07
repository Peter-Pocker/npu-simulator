#!/usr/bin/env python3
"""
批量对比 ZeBu trace 与仿真器结果，并输出可写入论文的 CSV 与 LaTeX 表格。

用法：
  # 1. 先对每个配置跑仿真器，把 Total cycles 记到 sim_results.csv（见下方格式）
  # 2. 运行本脚本生成汇总表与 LaTeX
  python3 scripts/batch_compare_zebu.py --sim-results sim_results.csv --zebu-dir third_party/Gemini-Compiler-IR/zebu_trace_output --latex

  # 若已编译仿真器，可自动跑仿真并对比（耗时较长）
  python3 scripts/batch_compare_zebu.py --zebu-dir third_party/Gemini-Compiler-IR/zebu_trace_output --ir-dir third_party/Gemini-Compiler-IR/scheduler_output --run-sim --simulator-exe ./npu_sim --latex

sim_results.csv 格式（表头）：
  model,batch,sim_cycles
  resnet34,1,12345678
  resnet34,4,...
  resnet50,1,...
"""
import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from parse_zebu_trace import load_zebu_trace, total_cycles

# 已知的 ZeBu trace 与 (model, batch) 对应关系
ZEBU_BASENAMES = [
    "int8_resnet34.sim_quantized_b1_c1_bw16_sim.txt",
    "int8_resnet34.sim_quantized_b4_c1_bw16_sim.txt",
    "int8_resnet34.sim_quantized_b16_c1_bw16_sim.txt",
    "int8_resnet50.sim_quantized_b1_c1_bw16_sim.txt",
    "int8_resnet50.sim_quantized_b4_c1_bw16_sim.txt",
    "int8_resnet50.sim_quantized_b16_c1_bw16_sim.txt",
    "int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt",
]


def parse_zebu_basename(name: str):
    """从 trace 文件名解析 model 和 batch。如 int8_resnet50...b64_c1... -> ('resnet50', 64)."""
    m = re.search(r"resnet(\d+).*_b(\d+)_c1", name)
    if m:
        return f"ResNet{m.group(1)}", int(m.group(2))
    return None, None


def get_zebu_cycles(zebu_dir: Path, basename: str):
    path = zebu_dir / basename
    if not path.exists():
        return None
    entries = load_zebu_trace(str(path))
    return total_cycles(entries) if entries else None


def run_simulator_for_ir(ir_path: str, config_path: Optional[str], exe: str) -> Optional[int]:
    _SCRIPT_DIR = Path(__file__).resolve().parent
    if str(_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPT_DIR))
    from compare_zebu_simulator import run_simulator
    try:
        cycles, _ = run_simulator(ir_path, config_path, exe, None)
        return cycles
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Batch compare ZeBu vs simulator, output table and LaTeX.")
    parser.add_argument("--zebu-dir", type=str, default="third_party/Gemini-Compiler-IR/zebu_trace_output",
                        help="Directory containing *_sim.txt")
    parser.add_argument("--ir-dir", type=str, default="third_party/Gemini-Compiler-IR/scheduler_output",
                        help="Directory containing *_stschedule.json (for --run-sim)")
    parser.add_argument("--sim-results", type=str, default=None,
                        help="CSV: model,batch,sim_cycles. If not set and not --run-sim, only Zebu stats are printed.")
    parser.add_argument("--run-sim", action="store_true", help="Run simulator for each IR to get sim_cycles")
    parser.add_argument("--simulator-exe", type=str, default="./npu_sim")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--latex", action="store_true", help="Print LaTeX table rows for thesis")
    parser.add_argument("--csv-out", type=str, default=None, help="Write full comparison table to CSV")
    args = parser.parse_args()

    zebu_dir = Path(args.zebu_dir)
    ir_dir = Path(args.ir_dir)

    # 读取或运行得到 sim_cycles: key = (model, batch)
    sim_map = {}
    if args.sim_results:
        with open(args.sim_results, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                model = row.get("model", "").strip()
                batch = row.get("batch", "").strip()
                try:
                    sim_map[(model, batch)] = int(row["sim_cycles"])
                except (KeyError, ValueError):
                    continue
    elif args.run_sim:
        sys.path.insert(0, str(_SCRIPT_DIR))
        from compare_zebu_simulator import run_simulator
        for basename in ZEBU_BASENAMES:
            model, batch = parse_zebu_basename(basename)
            if model is None:
                continue
            ir_name = basename.replace("_sim.txt", "_stschedule.json")
            ir_path = ir_dir / ir_name
            if not ir_path.exists():
                print(f"Skip (no IR): {ir_name}", file=sys.stderr)
                continue
            print(f"Running simulator for {model} batch={batch}...", file=sys.stderr)
            cycles = run_simulator_for_ir(str(ir_path), args.config, args.simulator_exe)
            if cycles is not None:
                sim_map[(model, str(batch))] = cycles
                sim_map[(model, batch)] = cycles

    rows = []
    for basename in ZEBU_BASENAMES:
        model, batch = parse_zebu_basename(basename)
        if model is None:
            continue
        zebu_cyc = get_zebu_cycles(zebu_dir, basename)
        if zebu_cyc is None:
            continue
        sim_cyc = (
            sim_map.get((model, batch))
            or sim_map.get((model, str(batch)))
            or sim_map.get((model.lower(), str(batch)))
        )
        if sim_cyc is None:
            rows.append({"model": model, "batch": batch, "zebu_cycles": zebu_cyc, "sim_cycles": "", "error_pct": ""})
        else:
            err = 100.0 * (sim_cyc - zebu_cyc) / zebu_cyc if zebu_cyc else 0
            rows.append({"model": model, "batch": batch, "zebu_cycles": zebu_cyc, "sim_cycles": sim_cyc, "error_pct": err})

    # 控制台表
    print("\n" + "=" * 72)
    print(f"{'Model':<12} {'Batch':<8} {'ZeBu (cycles)':<18} {'Sim (cycles)':<18} {'Error %':<10}")
    print("=" * 72)
    for r in rows:
        sim_s = str(r["sim_cycles"]) if r["sim_cycles"] != "" else "—"
        err_s = f"{r['error_pct']:+.2f}%" if r["error_pct"] != "" else "—"
        print(f"{r['model']:<12} {r['batch']:<8} {r['zebu_cycles']:<18} {sim_s:<18} {err_s:<10}")
    print("=" * 72)

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["model", "batch", "zebu_cycles", "sim_cycles", "error_pct"])
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {args.csv_out}")

    if args.latex:
        print("\n% ----- LaTeX table (paste into thesis) -----")
        print("\\begin{table}[htbp]")
        print("  \\centering")
        print("  \\caption{与ZeBu硬件仿真对比：总周期与相对误差}")
        print("  \\label{tab:zebu_validation}")
        print("  \\begin{tabular}{lrrrr}")
        print("    \\toprule")
        print("    模型 & 批大小 & ZeBu周期 & 仿真器周期 & 相对误差 \\\\")
        print("    \\midrule")
        for r in rows:
            sim_s = str(r["sim_cycles"]) if r["sim_cycles"] != "" else "—"
            err_s = f"{r['error_pct']:+.2f}\\%" if r["error_pct"] != "" else "—"
            print(f"    {r['model']} & {r['batch']} & {r['zebu_cycles']} & {sim_s} & {err_s} \\\\")
        print("    \\bottomrule")
        print("  \\end{tabular}")
        print("\\end{table}")


if __name__ == "__main__":
    main()
