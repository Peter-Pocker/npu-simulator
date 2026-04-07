#!/usr/bin/env python3
"""
运行 ZeBu 对比实验与真实负载瓶颈实验。每次运行写入独立目录，不覆盖历史结果。

用法（在项目根目录执行）：
  python3 scripts/run_zebu_and_bottleneck_experiments.py --simulator build/npu_sim
  # 默认使用 configs/default_config.json（BookSim2 + Ramulator2 全后端），输出到 experiments/runs/run_<timestamp>/

  python3 scripts/run_zebu_and_bottleneck_experiments.py --config configs/default_config.json --run-dir experiments/runs/my_run
  # 指定配置与输出目录

每次运行会在 --run-dir 下生成：
  sim_results_zebu.csv, zebu_validation_table.csv, bottleneck_breakdown.csv, bottleneck_per_core.csv
  trace_ResNet34/, trace_ResNet50/（带 -t 时）, run_info.txt

实验结束后默认调用 plot_gantt.py 与 csv_to_xlsx.py。
"""
import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
RUNS_DIR = EXPERIMENTS_DIR / "runs"
ZEBU_DIR = PROJECT_ROOT / "third_party/Gemini-Compiler-IR/zebu_trace_output"
SCHED_DIR = PROJECT_ROOT / "third_party/Gemini-Compiler-IR/scheduler_output"

ZEBU_CONFIGS = [
    ("resnet34", 1, "int8_resnet34.sim_quantized_b1_c1_bw16_stschedule.json"),
    ("resnet34", 4, "int8_resnet34.sim_quantized_b4_c1_bw16_stschedule.json"),
    ("resnet34", 16, "int8_resnet34.sim_quantized_b16_c1_bw16_stschedule.json"),
    ("resnet50", 1, "int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json"),
    ("resnet50", 4, "int8_resnet50.sim_quantized_b4_c1_bw16_stschedule.json"),
    ("resnet50", 16, "int8_resnet50.sim_quantized_b16_c1_bw16_stschedule.json"),
    ("resnet50", 64, "int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json"),
]

BOTTLENECK_CONFIGS = [
    ("ResNet34", 16, "int8_resnet34.sim_quantized_b16_c1_bw16_stschedule.json"),
    ("ResNet50", 16, "int8_resnet50.sim_quantized_b16_c1_bw16_stschedule.json"),
]


def run_simulator(ir_path: Path, config_path: Path, simulator_exe: str, trace_dir: str = None, timeout: int = 600) -> tuple:
    """返回 (total_cycles, stdout_text, per_core_rows). per_core_rows 为 [(core_id, idle, load, comp, wb, stall_noc), ...]"""
    cmd = [str(simulator_exe), "-i", str(ir_path), "-c", str(config_path)]
    if trace_dir:
        cmd.extend(["-t", trace_dir])
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if result.returncode != 0:
            return None, stdout + "\n" + stderr, []
    except subprocess.TimeoutExpired:
        return None, "", []
    except FileNotFoundError:
        return None, "npu_sim not found", []

    # Parse Total cycles
    m = re.search(r"Total cycles:\s*(\d+)", stdout)
    total_cycles = int(m.group(1)) if m else None

    # Parse per-core: Core  Idle  Loading  Compute  Writeback  StallNoC  Workloads  PeakSRAM
    per_core = []
    in_table = False
    for line in stdout.splitlines():
        if "Per-Core Statistics" in line:
            in_table = True
            continue
        if in_table and line.strip().startswith("="):
            break
        if in_table and line.strip():
            # Fixed-width: Core Idle Loading Compute Writeback StallNoC Workloads PeakSRAM
            parts = line.split()
            if len(parts) >= 6 and parts[0].isdigit():
                try:
                    core_id = int(parts[0])
                    idle = int(parts[1])
                    load = int(parts[2])
                    comp = int(parts[3])
                    wb = int(parts[4])
                    stall_noc = int(parts[5])
                    per_core.append((core_id, idle, load, comp, wb, stall_noc))
                except (ValueError, IndexError):
                    pass
    return total_cycles, stdout, per_core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulator", type=str, default="build/npu_sim", help="npu_sim 可执行文件路径")
    parser.add_argument("--config", type=str, default=None, help="配置文件，默认 configs/default_config.json（BookSim2+Ramulator2）")
    parser.add_argument("--run-dir", type=str, default=None, help="本次运行输出目录，不指定则创建 experiments/runs/run_<timestamp>，不覆盖历史")
    parser.add_argument("--skip-run", action="store_true", help="不跑仿真，仅用已有 sim_results_zebu.csv 做 Zebu 对比与瓶颈数据检查（需在 --run-dir 下已有数据）")
    parser.add_argument("--timeout", type=int, default=900, help="单次仿真超时秒数")
    parser.add_argument("--no-plot", action="store_true", help="不调用 plot_gantt.py 生成甘特图")
    parser.add_argument("--no-xlsx", action="store_true", help="不调用 csv_to_xlsx.py 生成 XLSX")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else PROJECT_ROOT / "configs/default_config.json"
    simulator_exe = Path(args.simulator)
    if not simulator_exe.is_absolute():
        simulator_exe = PROJECT_ROOT / simulator_exe

    # 本次运行输出目录：指定则用，否则新建 run_<timestamp>，避免覆盖
    if args.run_dir:
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = RUNS_DIR / f"run_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run output directory: {run_dir}", file=sys.stderr)

    # 写入本 run 的配置信息，便于复现
    with open(run_dir / "run_info.txt", "w", encoding="utf-8") as f:
        f.write(f"config={config_path}\n")
        f.write(f"timestamp={datetime.now().isoformat()}\n")
        f.write(f"simulator={simulator_exe}\n")

    # ---------- ZeBu 对比：跑 7 个配置，收集 Total cycles（每完成一次即追加写入，避免覆盖） ----------
    sim_results_path = run_dir / "sim_results_zebu.csv"
    if not args.skip_run and simulator_exe.exists():
        rows = []
        for model, batch, ir_name in ZEBU_CONFIGS:
            ir_path = SCHED_DIR / ir_name
            if not ir_path.exists():
                print(f"Skip (no IR): {ir_name}", file=sys.stderr)
                continue
            print(f"Running {model} batch={batch} ...", file=sys.stderr)
            total, _, _ = run_simulator(ir_path, config_path, simulator_exe, timeout=args.timeout)
            if total is not None:
                rows.append({"model": model, "batch": str(batch), "sim_cycles": total})
                print(f"  -> {total} cycles", file=sys.stderr)
            else:
                print(f"  -> failed or timeout", file=sys.stderr)
            # 每轮结束即写入当前结果，避免中途中断丢失
            with open(sim_results_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["model", "batch", "sim_cycles"])
                w.writeheader()
                w.writerows(rows)
        print(f"Wrote {sim_results_path}", file=sys.stderr)
    elif sim_results_path.exists():
        print(f"Using existing {sim_results_path} (use without --skip-run to re-run)", file=sys.stderr)
    else:
        print("No simulator run (missing exe or --skip-run). Create sim_results_zebu.csv manually or run without --skip-run.", file=sys.stderr)

    # ---------- 调用 batch_compare_zebu 生成 ZeBu 对比表 ----------
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    if sim_results_path.exists():
        from batch_compare_zebu import ZEBU_BASENAMES, parse_zebu_basename
        from parse_zebu_trace import load_zebu_trace, total_cycles

        def get_zebu_cycles(zebu_dir, basename):
            path = Path(zebu_dir) / basename
            if not path.exists():
                return None
            entries = load_zebu_trace(str(path))
            return total_cycles(entries) if entries else None

        zebu_dir = ZEBU_DIR
        zebu_table_path = run_dir / "zebu_validation_table.csv"
        sim_map = {}
        with open(sim_results_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    sim_map[(row["model"].strip().lower(), row["batch"].strip())] = int(row["sim_cycles"])
                except (KeyError, ValueError):
                    pass
        out_rows = []
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
            if sim_cyc is not None:
                err = 100.0 * (sim_cyc - zebu_cyc) / zebu_cyc
                out_rows.append({"model": model, "batch": batch, "zebu_cycles": zebu_cyc, "sim_cycles": sim_cyc, "error_pct": round(err, 2)})
            else:
                out_rows.append({"model": model, "batch": batch, "zebu_cycles": zebu_cyc, "sim_cycles": "", "error_pct": ""})
        with open(zebu_table_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["model", "batch", "zebu_cycles", "sim_cycles", "error_pct"])
            w.writeheader()
            w.writerows(out_rows)
        print(f"Wrote {zebu_table_path}", file=sys.stderr)

    # ---------- 瓶颈实验：跑 ResNet34/50 b16 带 trace，解析 per-core 并算占比 ----------
    bottleneck_breakdown_path = run_dir / "bottleneck_breakdown.csv"
    bottleneck_per_core_path = run_dir / "bottleneck_per_core.csv"
    if not args.skip_run and simulator_exe.exists():
        trace_base = run_dir / "trace"
        per_core_all = []
        breakdown_rows = []
        for model_name, batch, ir_name in BOTTLENECK_CONFIGS:
            ir_path = SCHED_DIR / ir_name
            if not ir_path.exists():
                continue
            trace_dir = str(trace_base / model_name.replace(" ", ""))
            print(f"Running bottleneck {model_name} b{batch} with trace ...", file=sys.stderr)
            total, _, per_core = run_simulator(ir_path, config_path, simulator_exe, trace_dir=trace_dir, timeout=args.timeout)
            if total is None or not per_core:
                continue
            sum_load = sum(p[2] for p in per_core)
            sum_comp = sum(p[3] for p in per_core)
            sum_wb = sum(p[4] for p in per_core)
            sum_stall = sum(p[5] for p in per_core)
            # T_Compute = sum C^comp; T_NoC_Stall = sum C^nocstall; T_Mem_Stall ≈ sum wb + sum load - sum nocstall
            t_compute = sum_comp
            t_noc_stall = sum_stall
            t_mem_stall = sum_wb + sum_load - sum_stall
            total_phase = t_compute + t_noc_stall + t_mem_stall
            if total_phase <= 0:
                total_phase = total
            breakdown_rows.append({
                "model": model_name,
                "batch": batch,
                "total_cycles": total,
                "T_Compute": t_compute,
                "T_NoC_Stall": t_noc_stall,
                "T_Mem_Stall": t_mem_stall,
                "pct_Compute": round(100.0 * t_compute / total_phase, 2),
                "pct_NoC_Stall": round(100.0 * t_noc_stall / total_phase, 2),
                "pct_Mem_Stall": round(100.0 * t_mem_stall / total_phase, 2),
            })
            for core_id, idle, load, comp, wb, stall_noc in per_core:
                per_core_all.append({
                    "model": model_name,
                    "batch": batch,
                    "core_id": core_id,
                    "idle": idle,
                    "loading": load,
                    "computing": comp,
                    "writeback": wb,
                    "stall_noc": stall_noc,
                })
        with open(bottleneck_breakdown_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["model", "batch", "total_cycles", "T_Compute", "T_NoC_Stall", "T_Mem_Stall", "pct_Compute", "pct_NoC_Stall", "pct_Mem_Stall"])
            w.writeheader()
            w.writerows(breakdown_rows)
        with open(bottleneck_per_core_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["model", "batch", "core_id", "idle", "loading", "computing", "writeback", "stall_noc"])
            w.writeheader()
            w.writerows(per_core_all)
        print(f"Wrote {bottleneck_breakdown_path}, {bottleneck_per_core_path}", file=sys.stderr)
    elif bottleneck_breakdown_path.exists():
        print(f"Using existing bottleneck data in {run_dir}", file=sys.stderr)

    # ---------- 默认调用 plot_gantt.py：对含 state_trace.csv 的目录生成 core_states.png ----------
    scripts_dir = PROJECT_ROOT / "scripts"
    if not args.no_plot:
        for trace_dir in run_dir.rglob("state_trace.csv"):
            trace_dir = trace_dir.parent
            out_png = trace_dir / "core_states.png"
            cmd = [
                sys.executable,
                str(scripts_dir / "plot_gantt.py"),
                "--trace", str(trace_dir),
                "--output", str(out_png),
            ]
            try:
                subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True, timeout=60)
                print(f"[plot_gantt] {out_png}", file=sys.stderr)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[plot_gantt] skip {trace_dir}: {e}", file=sys.stderr)

    # ---------- 默认调用 csv_to_xlsx.py：对含 CSV 的 trace 目录及 run_dir 生成 xlsx ----------
    if not args.no_xlsx:
        trace_dirs = set()
        for f in run_dir.rglob("state_trace.csv"):
            trace_dirs.add(f.parent)
        for td in sorted(trace_dirs):
            if not (td / "state_trace.csv").exists():
                continue
            out_xlsx = td / "trace_report.xlsx"
            cmd = [sys.executable, str(scripts_dir / "csv_to_xlsx.py"), str(td), "-o", str(out_xlsx)]
            try:
                subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True, timeout=30)
                print(f"[csv_to_xlsx] {out_xlsx}", file=sys.stderr)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[csv_to_xlsx] skip {td}: {e}", file=sys.stderr)
        if list(run_dir.glob("*.csv")):
            out_main = run_dir / "experiments_report.xlsx"
            cmd = [sys.executable, str(scripts_dir / "csv_to_xlsx.py"), str(run_dir), "-o", str(out_main)]
            try:
                subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True, timeout=30)
                print(f"[csv_to_xlsx] {out_main}", file=sys.stderr)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[csv_to_xlsx] skip run_dir: {e}", file=sys.stderr)

    print(f"All outputs written to: {run_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
