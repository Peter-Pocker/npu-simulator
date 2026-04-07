#!/usr/bin/env python3
"""
Compare ZeBu trace total cycles with NPU simulator total cycles.

Usage:
  # ZeBu trace + simulator cycles from file (e.g. simulator stdout or log)
  python compare_zebu_simulator.py --zebu-trace path/to/zebu_sim.txt --sim-output path/to/sim_stdout.txt

  # ZeBu trace + explicit simulator cycles
  python compare_zebu_simulator.py --zebu-trace path/to/zebu_sim.txt --sim-cycles 12345678

  # ZeBu trace + run simulator (need --ir and optionally --config, --simulator-exe)
  python compare_zebu_simulator.py --zebu-trace path/to/zebu_sim.txt --ir path/to/stschedule.json --config config.json

  # Optional: per-workload comparison (simulator must be run with -t to produce workload_summary.csv)
  python compare_zebu_simulator.py --zebu-trace path/to/zebu_sim.txt --sim-trace path/to/trace/workload_summary.csv
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Allow importing parse_zebu_trace when run as script
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from parse_zebu_trace import load_zebu_trace, total_cycles


def parse_simulator_stdout(text: str):
    """Extract 'Total cycles:       N' from simulator stdout."""
    m = re.search(r"Total cycles:\s*(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"Simulation complete at cycle (\d+)", text)
    if m:
        return int(m.group(1))
    return None


def get_simulator_cycles_from_workload_summary(csv_path: str):
    """Read workload_summary.csv and return max(end_cycle) as total cycles."""
    total = 0
    with open(csv_path, "r", encoding="utf-8") as f:
        header = f.readline()
        if "end_cycle" not in header:
            return None
        idx = header.strip().split(",").index("end_cycle")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) > idx:
                try:
                    total = max(total, int(parts[idx]))
                except ValueError:
                    pass
    return total if total > 0 else None


def run_simulator(ir_path: str, config_path: Optional[str], simulator_exe: str, trace_dir: Optional[str]):
    """Run simulator and return (total_cycles, stdout_text)."""
    cmd = [simulator_exe, "-i", ir_path]
    if config_path:
        cmd.extend(["-c", config_path])
    if trace_dir:
        cmd.extend(["-t", trace_dir])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
        cwd=Path.cwd(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Simulator failed: {result.stderr or result.stdout}")
    cycles = parse_simulator_stdout(result.stdout)
    return cycles, result.stdout


def main():
    parser = argparse.ArgumentParser(
        description="Compare ZeBu trace total cycles with NPU simulator."
    )
    parser.add_argument(
        "--zebu-trace",
        type=str,
        required=True,
        help="Path to ZeBu trace file (e.g. *_sim.txt)",
    )
    parser.add_argument(
        "--sim-cycles",
        type=int,
        default=None,
        help="Simulator total cycles (direct value)",
    )
    parser.add_argument(
        "--sim-output",
        type=str,
        default=None,
        help="Path to simulator stdout/log containing 'Total cycles: N'",
    )
    parser.add_argument(
        "--sim-trace",
        type=str,
        default=None,
        help="Path to simulator workload_summary.csv (use max end_cycle as total)",
    )
    parser.add_argument(
        "--ir",
        type=str,
        default=None,
        help="Path to Scheduler IR (stschedule.json) to run simulator",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to simulator config JSON (optional)",
    )
    parser.add_argument(
        "--simulator-exe",
        type=str,
        default="./npu_sim",
        help="Path to simulator executable (default: ./npu_sim)",
    )
    parser.add_argument(
        "--trace-dir",
        type=str,
        default=None,
        help="If set, run simulator with -t to export trace (for --sim-trace comparison)",
    )
    args = parser.parse_args()

    zebu_path = Path(args.zebu_trace)
    if not zebu_path.exists():
        print(f"Error: ZeBu trace not found: {zebu_path}", file=sys.stderr)
        sys.exit(1)

    entries = load_zebu_trace(str(zebu_path))
    zebu_total = total_cycles(entries)
    if zebu_total == 0 and entries:
        zebu_total = max(e["end"] for e in entries)

    print(f"ZeBu trace: {zebu_path.name}")
    print(f"ZeBu total cycles: {zebu_total} (instructions: {len(entries)})")

    sim_cycles = args.sim_cycles
    if sim_cycles is None and args.sim_output:
        with open(args.sim_output, "r", encoding="utf-8") as f:
            sim_cycles = parse_simulator_stdout(f.read())
        if sim_cycles is None:
            print(f"Error: could not find 'Total cycles' in {args.sim_output}", file=sys.stderr)
            sys.exit(1)
        print(f"Simulator cycles (from {args.sim_output}): {sim_cycles}")
    if sim_cycles is None and args.sim_trace:
        sim_cycles = get_simulator_cycles_from_workload_summary(args.sim_trace)
        if sim_cycles is not None:
            print(f"Simulator cycles (from workload_summary max end_cycle): {sim_cycles}")
    if sim_cycles is None and args.ir:
        exe = Path(args.simulator_exe)
        if not exe.is_absolute():
            # Try project root / build
            for base in [Path(__file__).resolve().parent.parent, Path.cwd()]:
                for name in ["npu_sim", "build/npu_sim", "out/npu_sim"]:
                    cand = base / name
                    if cand.exists():
                        exe = cand
                        break
        if not Path(args.simulator_exe).exists() and not exe.exists():
            print("Error: simulator executable not found. Use --simulator-exe or --sim-cycles.", file=sys.stderr)
            sys.exit(1)
        try:
            sim_cycles, _ = run_simulator(
                args.ir,
                args.config,
                str(exe) if exe.exists() else args.simulator_exe,
                args.trace_dir,
            )
            print(f"Simulator cycles (from run): {sim_cycles}")
        except Exception as e:
            print(f"Error running simulator: {e}", file=sys.stderr)
            sys.exit(1)

    if sim_cycles is None:
        print("Error: simulator cycles not provided. Use --sim-cycles, --sim-output, --sim-trace, or --ir.", file=sys.stderr)
        sys.exit(1)

    diff = sim_cycles - zebu_total
    if zebu_total > 0:
        pct = 100.0 * diff / zebu_total
        print(f"\nComparison:")
        print(f"  Simulator - ZeBu = {diff} cycles ({pct:+.2f}%)")
        if abs(pct) < 5.0:
            print("  => Within 5% of ZeBu (good agreement).")
        elif abs(pct) < 15.0:
            print("  => Within 15% of ZeBu (reasonable).")
        else:
            print("  => Difference > 15% (check timing model or config).")
    else:
        print(f"\nSimulator total cycles: {sim_cycles}")


if __name__ == "__main__":
    main()
