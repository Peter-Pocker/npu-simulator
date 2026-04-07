#!/usr/bin/env python3
"""
One-shot: run GEMINI to generate IR, then run the NPU simulator with that IR.

Each run creates a timestamped record folder under trace/ (or -t base):
  trace/trace_YYYYMMDD_HHMMSS/
  ├── gemini_log/          # Only with --save-gemini-log: stdout, stderr
  ├── trans_2x2_ir.json    # Generated IR
  ├── state_trace.csv      # Simulator trace (via run.sh)
  ├── workload_summary.csv
  ├── stdout.txt           # Simulator output (via run.sh)
  └── run_info.md          # Run times, GEMINI input, config path

Example:
  python3 scripts/gemini_run.py -c configs/gemini_run_example.json -n trans
  python3 scripts/gemini_run.py -c configs/full_config.json -n resnet --skip-sim
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime


# GEMINI network index → name (for IR filename). Index is nn in stdin.
GEMINI_NETWORKS = [
    ("darknet19", 0),
    ("vgg", 1),
    ("resnet", 2),
    ("goog", 3),
    ("resnet101", 4),
    ("densenet", 5),
    ("ires", 6),
    ("gnmt", 7),
    ("lstm", 8),
    ("zfnet", 9),
    ("trans", 10),
    ("trans_cell", 11),
    ("pnas", 12),
    ("resnext50", 13),
    ("resnet152", 14),
    ("bert", 15),
    ("gpt_prefill", 16),
    ("gpt_decode", 17),
]

# GEMINI supported mac_dim (from main.cpp). Map simulator mac_units to nearest.
GEMINI_MAC_DIMS = (512, 1024, 2048, 4096, 8192)

# GEMINI only accepts DDR_type in ("LPDDR5", "GDDR6X", "HBM"). Map simulator dram.standard.
def map_dram_type_to_gemini(standard: str) -> str:
    s = (standard or "").upper()
    if "HBM" in s:
        return "HBM"
    if "GDDR" in s:
        return "GDDR6X"
    if "LPDDR" in s or "DDR4" in s or "DDR5" in s:
        return "LPDDR5"
    return "HBM"  # default for accelerator-style configs


def find_script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def project_root() -> str:
    return os.path.dirname(find_script_dir())


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def snap_mac_units_to_gemini(mac_units: int) -> int:
    """Return nearest GEMINI-supported mac_dim."""
    if mac_units <= 0:
        return 512
    best = GEMINI_MAC_DIMS[0]
    for d in GEMINI_MAC_DIMS:
        if abs(d - mac_units) < abs(best - mac_units):
            best = d
    return best


def resolve_network(network_arg: str) -> tuple[int, str]:
    """Return (nn_index, net_name_for_ir)."""
    # By index
    try:
        idx = int(network_arg)
        if 0 <= idx < len(GEMINI_NETWORKS):
            name, _ = GEMINI_NETWORKS[idx]
            return idx, name
    except ValueError:
        pass
    # By name (case-insensitive)
    arg_lower = network_arg.lower().strip()
    for name, idx in GEMINI_NETWORKS:
        if name.lower() == arg_lower:
            return idx, name
    raise SystemExit(f"Unknown network: {network_arg}. Use index 0–{len(GEMINI_NETWORKS)-1} or name: " + ", ".join(n[0] for n in GEMINI_NETWORKS))


def build_gemini_params(config: dict, nn_index: int, net_name: str) -> dict:
    """Build GEMINI stdin parameters from simulator config + overrides."""
    root = project_root()
    gemini_block = config.get("gemini", {})

    # Cores: from config or gemini block
    xx = gemini_block.get("num_cores_x") or config.get("num_cores_x") or 2
    yy = gemini_block.get("num_cores_y") or config.get("num_cores_y") or 2
    if xx <= 0 or yy <= 0:
        raise SystemExit("num_cores_x and num_cores_y must be > 0 (set in config or config.gemini).")

    # Core microarch: mm 0 = PolarCore (recommended)
    mm = int(gemini_block.get("core_microarch", 0))
    # mac_dim from simulator core.mac_units, snapped to GEMINI support
    mac_units = config.get("core", {}) and config["core"].get("mac_units") or 256
    _mac_dim = snap_mac_units_to_gemini(mac_units)
    # ul3 (SRAM KB)
    _ul3 = int(gemini_block.get("sram_kb") or config.get("sram", {}).get("size_kb") or 1024)

    # Batch, stride, exploration, opt goal
    bb = int(gemini_block.get("batch_size", 1))
    ss = int(gemini_block.get("stride", 1))
    rr = int(gemini_block.get("exploration_rounds", 1))
    ff = int(gemini_block.get("opt_goal", 0))

    # Chiplet / package (SoC single die = 1x1)
    xcut = int(gemini_block.get("xcut", 1))
    ycut = int(gemini_block.get("ycut", 1))

    # Tech, package, IO, DDR (GEMINI only supports LPDDR5, GDDR6X, HBM)
    tech = str(gemini_block.get("tech", "7"))
    package_type = str(gemini_block.get("package_type", "SI"))
    IO_type = str(gemini_block.get("io_type", "UCIe"))
    raw_dram = gemini_block.get("dram_type") or (config.get("dram") or {}).get("standard") or "DDR4"
    DDR_type = map_dram_type_to_gemini(str(raw_dram))
    _NoP_bw = int(gemini_block.get("nop_bw", 128))
    _DRAM_bw = int(gemini_block.get("dram_bw", 1024000))
    _NoC_bw = int(gemini_block.get("noc_bw", 128))

    total_tops = int(gemini_block.get("total_tops") or (2 * xx * yy * _mac_dim))

    return {
        "tech": tech,
        "mm": mm,
        "nn": nn_index,
        "xx": xx,
        "yy": yy,
        "ss": ss,
        "bb": bb,
        "rr": rr,
        "ff": ff,
        "xcut": xcut,
        "ycut": ycut,
        "package_type": package_type,
        "IO_type": IO_type,
        "_NoP_bw": _NoP_bw,
        "DDR_type": DDR_type,
        "_DRAM_bw": _DRAM_bw,
        "_NoC_bw": _NoC_bw,
        "_mac_dim": _mac_dim,
        "_ul3": _ul3,
        "total_tops": total_tops,
        "net_name": net_name,
    }


def gemini_stdin_line(p: dict) -> str:
    """Single line for GEMINI stdin."""
    return (
        f"{p['tech']} {p['mm']} {p['nn']} {p['xx']} {p['yy']} {p['ss']} {p['bb']} {p['rr']} {p['ff']} "
        f"{p['xcut']} {p['ycut']} {p['package_type']} {p['IO_type']} {p['_NoP_bw']} {p['DDR_type']} "
        f"{p['_DRAM_bw']} {p['_NoC_bw']} {p['_mac_dim']} {p['_ul3']} {p['total_tops']}"
    )


def run_gemini(params: dict, gemini_dir: str, record_folder: str, save_log: bool) -> tuple[str, float]:
    """Run GEMINI; return (absolute path to IR file, elapsed seconds)."""
    stschedule = os.path.join(gemini_dir, "build", "stschedule")
    if not os.path.isfile(stschedule):
        raise SystemExit(f"GEMINI binary not found: {stschedule}. Build with: cd {gemini_dir} && make release")

    stdin_line = gemini_stdin_line(params)
    net_name = params["net_name"]
    xx, yy = params["xx"], params["yy"]
    ir_basename = f"{net_name}_{xx}x{yy}_ir.json"

    if save_log:
        cwd = os.path.join(record_folder, "gemini_log")
        os.makedirs(cwd, exist_ok=True)
    else:
        cwd = record_folder
        os.makedirs(cwd, exist_ok=True)

    cwd = os.path.abspath(cwd)
    stschedule_abs = os.path.abspath(stschedule)
    ir_path_out = os.path.join(cwd, ir_basename)

    print("[gemini_run] Running GEMINI (this may take a minute)...")
    print("[gemini_run] stdin:", stdin_line)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [stschedule_abs],
        input=stdin_line.encode("utf-8"),
        cwd=cwd,
        capture_output=True,
        timeout=600,
    )
    elapsed = time.perf_counter() - t0

    if save_log:
        with open(os.path.join(cwd, "stdout.txt"), "wb") as f:
            f.write(proc.stdout or b"")
        with open(os.path.join(cwd, "stderr.txt"), "wb") as f:
            f.write(proc.stderr or b"")

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(f"GEMINI exited with code {proc.returncode}")

    if not os.path.isfile(ir_path_out):
        raise SystemExit(f"GEMINI did not produce expected IR file: {ir_path_out}")

    if not save_log:
        for fname in ("temp_points.txt",):
            p = os.path.join(cwd, fname)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    return ir_path_out, elapsed


def run_via_run_sh(config_path: str, ir_path: str, record_folder: str, root: str) -> tuple[int, float]:
    """Invoke run.sh to run the simulator; return (exit code, elapsed seconds)."""
    run_sh = os.path.join(root, "run.sh")
    if not os.path.isfile(run_sh):
        raise SystemExit(f"run.sh not found: {run_sh}")
    cmd = [run_sh, "-c", config_path, "-i", ir_path, "-t", record_folder]
    print("[gemini_run] Running via run.sh:", " ".join(cmd))
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=root)
    elapsed = time.perf_counter() - t0
    return proc.returncode, elapsed


def write_run_info(record_folder: str, gemini_input: str, config_path: str,
                   gemini_time_sec: float, sim_time_sec: float, ir_path: str) -> None:
    """Write run_info.md to record folder."""
    path = os.path.join(record_folder, "run_info.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# gemini_run 运行记录\n\n")
        f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## 运行时间\n\n")
        f.write(f"| 阶段 | 耗时 |\n")
        f.write(f"|------|------|\n")
        f.write(f"| GEMINI | {gemini_time_sec:.2f} 秒 |\n")
        f.write(f"| 仿真器 | {sim_time_sec:.2f} 秒 |\n")
        f.write(f"| 合计 | {gemini_time_sec + sim_time_sec:.2f} 秒 |\n\n")
        # Include command.txt content in run_info (run.sh creates it when -t is used)
        cmd_path = os.path.join(record_folder, "command.txt")
        if os.path.isfile(cmd_path):
            with open(cmd_path, "r", encoding="utf-8") as cf:
                cmd_content = cf.read().rstrip()
            f.write("## 运行命令\n\n```\n")
            f.write(cmd_content)
            f.write("\n```\n\n")
            try:
                os.remove(cmd_path)
            except OSError:
                pass
        f.write("## GEMINI 输入\n\n")
        f.write("```\n")
        f.write(gemini_input)
        f.write("\n```\n\n")
        f.write("## 仿真配置\n\n")
        f.write(f"- **配置文件路径**: `{config_path}`\n")
        f.write(f"- **IR 文件路径**: `{ir_path}`\n")
        # Remove ir_path.txt (redundant, path is in run_info.md)
        ir_path_txt = os.path.join(record_folder, "ir_path.txt")
        if os.path.isfile(ir_path_txt):
            try:
                os.remove(ir_path_txt)
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run GEMINI to generate IR, then run the NPU simulator. Config from file; network from CLI.",
    )
    ap.add_argument(
        "-c", "--config",
        default="configs/full_config.json",
        help="Simulator config JSON (and optional 'gemini' block for GEMINI params)",
    )
    ap.add_argument(
        "-n", "--network",
        default=None,
        help="Network: index (0–17) or name, e.g. trans, resnet, bert (required unless --list-networks)",
    )
    ap.add_argument(
        "-t", "--trace-base",
        default="trace",
        metavar="DIR",
        help="Base dir for timestamped record folder (default: trace)",
    )
    ap.add_argument(
        "--skip-sim",
        action="store_true",
        help="Only run GEMINI and print IR path; do not run simulator",
    )
    ap.add_argument(
        "--save-gemini-log",
        action="store_true",
        help="Save GEMINI stdout/stderr to gemini_log/ (default: off)",
    )
    ap.add_argument(
        "--list-networks",
        action="store_true",
        help="Print GEMINI network index and names, then exit",
    )
    args = ap.parse_args()

    if args.list_networks:
        print("GEMINI networks (use -n <index> or -n <name>):")
        for name, idx in GEMINI_NETWORKS:
            print(f"  {idx:2d}  {name}")
        return 0

    if not args.network:
        ap.error("-n/--network is required (or use --list-networks)")

    root = project_root()
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(root, config_path)
    config_path = os.path.abspath(config_path)
    if not os.path.isfile(config_path):
        raise SystemExit(f"Config not found: {config_path}")

    config = load_config(config_path)
    nn_index, net_name = resolve_network(args.network)
    params = build_gemini_params(config, nn_index, net_name)

    gemini_dir = os.path.join(root, "third_party", "GEMINI-HPCA2024")
    if not os.path.isdir(gemini_dir):
        raise SystemExit(f"GEMINI directory not found: {gemini_dir}")

    # Create timestamped record folder: trace_base/trace_YYYYMMDD_HHMMSS
    trace_base = args.trace_base if os.path.isabs(args.trace_base) else os.path.join(root, args.trace_base)
    record_folder = os.path.join(trace_base, f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    print("[gemini_run] Record folder:", os.path.abspath(record_folder))

    save_gemini_log = args.save_gemini_log
    ir_path, gemini_time = run_gemini(params, gemini_dir, record_folder, save_gemini_log)
    if save_gemini_log:
        ir_basename = os.path.basename(ir_path)
        ir_dest = os.path.join(record_folder, ir_basename)
        shutil.move(ir_path, ir_dest)
        ir_path = ir_dest
    print("[gemini_run] IR written to:", ir_path)

    if args.skip_sim:
        with open(os.path.join(record_folder, "run_info.md"), "w", encoding="utf-8") as f:
            f.write("# gemini_run 运行记录 (仅 GEMINI)\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**GEMINI 耗时**: {gemini_time:.2f} 秒\n\n")
            f.write("## GEMINI 输入\n\n```\n")
            f.write(gemini_stdin_line(params))
            f.write("\n```\n\n")
            f.write(f"**配置文件**: `{config_path}`\n")
        print("[gemini_run] Skip simulator (--skip-sim).")
        return 0

    exit_code, sim_time = run_via_run_sh(config_path, ir_path, record_folder, root)
    write_run_info(record_folder, gemini_stdin_line(params), config_path, gemini_time, sim_time, ir_path)
    print("[gemini_run] Run info written to:", os.path.join(record_folder, "run_info.md"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)
