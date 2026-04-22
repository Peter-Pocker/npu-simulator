#!/usr/bin/env python3
"""
Chapter 5.4 Design-Space Exploration: throughput scaling across
  - Dimension 1: mac_units (single-core compute power)
  - Dimension 2: core count (2x2, 4x4, 8x8) with optional chiplet
  - Dimension 3: batch size (1, 4)

Usage:
  python3 scripts/run_c5_dse.py                  # run all experiments
  python3 scripts/run_c5_dse.py --dry-run        # print what would run
  python3 scripts/run_c5_dse.py --summary-only    # just print results table
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_BASE = os.path.join(ROOT, "out", "c5_dse")
GEMINI_BIN = os.path.join(ROOT, "third_party", "GEMINI-HPCA2024", "build", "stschedule")
NPU_SIM = os.path.join(ROOT, "build", "npu_sim")

BASE_CONFIG = {
    "element_size_bits": 8,
    "core": {
        "mac_units": 256,
        "vector_units": 64,
        "clock_freq_mhz": 25,
        "use_analytical_time": False,
    },
    "sram": {
        "size_kb": 16384,
        "read_bandwidth_bytes_per_cycle": 1024,
        "write_bandwidth_bytes_per_cycle": 1024,
    },
    "ni": {"max_outstanding_reqs": 16, "injection_queue_size": 8, "ejection_queue_size": 8},
    "noc": {
        "backend": "booksim2",
        "flit_size_bytes": 16,
        "hop_latency_cycles": 1,
        "router_latency_cycles": 1,
        "injection_queue_depth": 16,
        "num_vcs": 8,
        "vc_buf_size": 8,
    },
    "dram": {
        "num_channels": 4,
        "clock_ratio": 0.025,
        "backend": "ramulator2",
        "standard": "HBM",
        "org": "HBM_4Gb",
        "timing": "HBM_2Gbps",
        "num_ranks": 1,
        "scheduler": "FRFCFS",
        "addr_mapper": "RoBaRaCoCh",
        "frontend_clock_ratio": 4,
    },
    "topology": {"dram_controllers": [], "dram_routing_policy": "nearest"},
}

GEMINI_BASE = {
    "tech": "7", "package_type": "SI", "io_type": "UCIe", "dram_type": "HBM",
    "stride": 1, "exploration_rounds": 1, "opt_goal": 0,
    "nop_bw": 128, "dram_bw": 1024000, "noc_bw": 128,
}

GEMINI_MAC_DIMS = (512, 1024, 2048, 4096, 8192)


def snap_mac(v: int) -> int:
    return min(GEMINI_MAC_DIMS, key=lambda d: abs(d - v))


def build_experiments() -> list[dict]:
    """Return list of experiment descriptors."""
    exps = []

    for batch in (1, 4):
        for mac in (256, 512, 1024):
            exps.append({
                "name": f"mac{mac}_2x2_b{batch}",
                "dim": "mac_sweep",
                "xx": 2, "yy": 2, "xcut": 1, "ycut": 1,
                "mac_units": mac, "batch": batch,
            })

    for batch in (1, 4):
        for xx, yy, xcut, ycut, label in [
            (2, 2, 1, 1, "2x2"),
            (4, 4, 1, 1, "4x4"),
            (4, 4, 2, 2, "4x4chip"),
            (8, 8, 2, 2, "8x8chip"),
        ]:
            exps.append({
                "name": f"cores{label}_b{batch}",
                "dim": "core_sweep",
                "xx": xx, "yy": yy, "xcut": xcut, "ycut": ycut,
                "mac_units": 512, "batch": batch,
            })

    seen = set()
    unique = []
    for e in exps:
        key = e["name"]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def ir_key(exp: dict) -> str:
    return f"resnet_{exp['xx']}x{exp['yy']}_b{exp['batch']}_xcut{exp['xcut']}_ycut{exp['ycut']}"


def make_config(exp: dict) -> dict:
    cfg = copy.deepcopy(BASE_CONFIG)
    cfg["num_cores_x"] = exp["xx"]
    cfg["num_cores_y"] = exp["yy"]
    cfg["core"]["mac_units"] = exp["mac_units"]

    g = copy.deepcopy(GEMINI_BASE)
    g["batch_size"] = exp["batch"]
    g["xcut"] = exp["xcut"]
    g["ycut"] = exp["ycut"]
    cfg["gemini"] = g
    return cfg


def gemini_stdin(cfg: dict, nn: int = 2) -> str:
    g = cfg["gemini"]
    xx = cfg["num_cores_x"]
    yy = cfg["num_cores_y"]
    mac_dim = snap_mac(cfg["core"]["mac_units"])
    ul3 = cfg["sram"]["size_kb"]
    total_tops = 2 * xx * yy * mac_dim
    return (
        f"{g['tech']} 0 {nn} {xx} {yy} {g['stride']} {g['batch_size']} "
        f"{g['exploration_rounds']} {g['opt_goal']} {g['xcut']} {g['ycut']} "
        f"{g['package_type']} {g['io_type']} {g['nop_bw']} HBM "
        f"{g['dram_bw']} {g['noc_bw']} {mac_dim} {ul3} {total_tops}"
    )


def generate_ir(exp: dict, dry_run: bool = False) -> str:
    key = ir_key(exp)
    ir_dir = os.path.join(OUT_BASE, "ir", key)
    ir_path = os.path.join(ir_dir, f"resnet_{exp['xx']}x{exp['yy']}_ir.json")

    if os.path.isfile(ir_path):
        print(f"  [IR] cached: {ir_path}")
        return ir_path

    cfg = make_config(exp)
    stdin_line = gemini_stdin(cfg)
    print(f"  [IR] GEMINI: {key}")
    print(f"       stdin: {stdin_line}")

    if dry_run:
        return f"<dry-run:{ir_path}>"

    os.makedirs(ir_dir, exist_ok=True)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [GEMINI_BIN], input=stdin_line.encode(), cwd=ir_dir,
        capture_output=True, timeout=600,
    )
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise RuntimeError(f"GEMINI failed for {key} (exit {proc.returncode})")

    if not os.path.isfile(ir_path):
        candidates = [f for f in os.listdir(ir_dir) if f.endswith("_ir.json")]
        if candidates:
            actual = os.path.join(ir_dir, candidates[0])
            os.rename(actual, ir_path)
        else:
            raise RuntimeError(f"GEMINI did not produce IR in {ir_dir}")

    print(f"       done in {elapsed:.1f}s -> {ir_path}")
    return ir_path


def run_simulation(exp: dict, ir_path: str, dry_run: bool = False) -> str:
    out_dir = os.path.join(OUT_BASE, exp["name"])
    cfg = make_config(exp)

    cfg_path = os.path.join(out_dir, "config.json")
    os.makedirs(out_dir, exist_ok=True)

    stdout_path = os.path.join(out_dir, "stdout.txt")
    if os.path.isfile(stdout_path):
        print(f"  [SIM] cached: {exp['name']}")
        return out_dir

    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)

    print(f"  [SIM] {exp['name']}  mac={exp['mac_units']} {exp['xx']}x{exp['yy']} b={exp['batch']}")

    if dry_run:
        return out_dir

    t0 = time.perf_counter()
    proc = subprocess.run(
        [NPU_SIM, "--config", cfg_path, "--ir", ir_path, "--trace", out_dir],
        capture_output=True, text=True, timeout=600,
    )
    elapsed = time.perf_counter() - t0

    with open(stdout_path, "w") as f:
        f.write(proc.stdout)
    if proc.stderr:
        with open(os.path.join(out_dir, "stderr.txt"), "w") as f:
            f.write(proc.stderr)

    print(f"       done in {elapsed:.1f}s (exit {proc.returncode})")

    gantt_script = os.path.join(ROOT, "scripts", "plot_gantt.py")
    if os.path.isfile(gantt_script):
        subprocess.run(
            [sys.executable, gantt_script, "--trace", out_dir,
             "--output", os.path.join(out_dir, "core_states.png")],
            capture_output=True, timeout=60,
        )

    return out_dir


def parse_results(out_dir: str) -> dict | None:
    stdout_path = os.path.join(out_dir, "stdout.txt")
    if not os.path.isfile(stdout_path):
        return None

    with open(stdout_path) as f:
        text = f.read()

    result = {}
    m = re.search(r"Total cycles:\s+(\d+)", text)
    if m:
        result["total_cycles"] = int(m.group(1))

    cores = []
    for m in re.finditer(
        r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+",
        text, re.MULTILINE,
    ):
        cores.append({
            "id": int(m.group(1)),
            "idle": int(m.group(2)),
            "loading": int(m.group(3)),
            "computing": int(m.group(4)),
            "writeback": int(m.group(5)),
            "stall_noc": int(m.group(6)),
            "workloads": int(m.group(7)),
        })
    if cores:
        result["cores"] = cores
        result["total_loading"] = sum(c["loading"] for c in cores)
        result["total_computing"] = sum(c["computing"] for c in cores)
        result["total_writeback"] = sum(c["writeback"] for c in cores)
        result["num_cores"] = len(cores)
        result["total_workloads"] = sum(c["workloads"] for c in cores)

    return result if result else None


def print_summary(exps: list[dict]):
    print("\n" + "=" * 120)
    print(f"{'Name':<24} {'Cores':>5} {'MAC':>5} {'Batch':>5} {'Cycles':>10} "
          f"{'Load':>10} {'Comp':>10} {'WB':>10} {'L/C':>6} {'Comp%':>6} {'Thru':>10}")
    print("=" * 120)

    for exp in exps:
        out_dir = os.path.join(OUT_BASE, exp["name"])
        r = parse_results(out_dir)
        if not r or "total_cycles" not in r:
            print(f"{exp['name']:<24} {'---':>5}")
            continue

        nc = r.get("num_cores", 0)
        tc = r["total_cycles"]
        tl = r.get("total_loading", 0)
        tcomp = r.get("total_computing", 0)
        tw = r.get("total_writeback", 0)
        lc = tl / tcomp if tcomp > 0 else 999
        max_active = max(c["loading"] + c["computing"] + c["writeback"] for c in r.get("cores", [{"loading": 0, "computing": 0, "writeback": 0}]))
        max_comp = max(c["computing"] for c in r.get("cores", [{"computing": 0}]))
        comp_pct = 100 * max_comp / max_active if max_active > 0 else 0
        throughput = exp["batch"] / tc * 1e6 if tc > 0 else 0

        print(f"{exp['name']:<24} {nc:>5} {exp['mac_units']:>5} {exp['batch']:>5} {tc:>10,} "
              f"{tl:>10,} {tcomp:>10,} {tw:>10,} {lc:>6.2f} {comp_pct:>5.1f}% {throughput:>10.4f}")

    print("=" * 120)
    print("Thru = throughput in inferences/cycle (x1e6 = inferences/sec at 1MHz)")


def main():
    ap = argparse.ArgumentParser(description="Chapter 5.4 DSE experiments")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    exps = build_experiments()

    if args.summary_only:
        print_summary(exps)
        return

    print(f"Total experiments: {len(exps)}")
    os.makedirs(OUT_BASE, exist_ok=True)

    ir_cache: dict[str, str] = {}

    for i, exp in enumerate(exps, 1):
        print(f"\n[{i}/{len(exps)}] {exp['name']} ({exp['dim']})")

        key = ir_key(exp)
        if key not in ir_cache:
            ir_cache[key] = generate_ir(exp, dry_run=args.dry_run)
        ir_path = ir_cache[key]

        run_simulation(exp, ir_path, dry_run=args.dry_run)

    print_summary(exps)


if __name__ == "__main__":
    main()
