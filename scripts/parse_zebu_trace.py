#!/usr/bin/env python3
"""
Parse ZeBu trace output (*_sim.txt) and compute total cycles / per-workload summary.

ZeBu trace format: one line per instruction, each line is a list like
  [inst_type, {'slyr': 0, 'wkl': 0, 'tile': -1, ...}, {'inst_idx': N, 'time': [start, end]}, ...]

Usage:
  python parse_zebu_trace.py <zebu_trace.txt>
  python parse_zebu_trace.py <zebu_trace.txt> --summary    # per-inst-type and per-(slyr,wkl) stats
  python parse_zebu_trace.py <zebu_trace.txt> --json      # output machine-readable summary
"""

import re
import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict


def parse_line(line: str):
    """Extract inst_type, inst_idx, time [start, end], slyr, wkl, tile from one trace line."""
    line = line.strip()
    if not line:
        return None

    # inst_type: first token in list, e.g. [lda_cfg, ... or [pe_conv, ...
    m_type = re.search(r"^\[(\w+)\s*,\s*\{", line)
    inst_type = m_type.group(1) if m_type else ""

    # 'inst_idx': N
    m_idx = re.search(r"'inst_idx':\s*(\d+)", line)
    inst_idx = int(m_idx.group(1)) if m_idx else -1

    # 'time': [start, end]
    m_time = re.search(r"'time':\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]", line)
    start, end = (int(m_time.group(1)), int(m_time.group(2))) if m_time else (0, 0)

    # metadata for grouping: slyr, wkl, tile
    m_slyr = re.search(r"'slyr':\s*(\d+)", line)
    m_wkl = re.search(r"'wkl':\s*(\d+)", line)
    m_tile = re.search(r"'tile':\s*(-?\d+)", line)
    slyr = int(m_slyr.group(1)) if m_slyr else 0
    wkl = int(m_wkl.group(1)) if m_wkl else 0
    tile = int(m_tile.group(1)) if m_tile else -1

    return {
        "inst_type": inst_type,
        "inst_idx": inst_idx,
        "start": start,
        "end": end,
        "slyr": slyr,
        "wkl": wkl,
        "tile": tile,
    }


def load_zebu_trace(path: str):
    """Load trace file and return list of parsed instruction entries."""
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            entry = parse_line(line)
            if entry is not None:
                entries.append(entry)
    return entries


def total_cycles(entries):
    """Total execution cycles = max(end) over all instructions."""
    if not entries:
        return 0
    return max(e["end"] for e in entries)


def main():
    parser = argparse.ArgumentParser(
        description="Parse ZeBu trace and report total cycles / optional summary."
    )
    parser.add_argument(
        "zebu_trace",
        type=str,
        help="Path to ZeBu trace file (e.g. *_sim.txt)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print per-instruction-type and per-(slyr,wkl) aggregated stats",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON (total_cycles + optional summary)",
    )
    args = parser.parse_args()

    path = Path(args.zebu_trace)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    entries = load_zebu_trace(str(path))
    total = total_cycles(entries)

    result = {"total_cycles": total, "num_instructions": len(entries)}

    if args.json:
        if args.summary:
            by_type = defaultdict(lambda: {"count": 0, "total_duration": 0})
            by_wkl = defaultdict(lambda: {"start_min": None, "end_max": 0})
            for e in entries:
                by_type[e["inst_type"]]["count"] += 1
                by_type[e["inst_type"]]["total_duration"] += e["end"] - e["start"]
                key = (e["slyr"], e["wkl"])
                if by_wkl[key]["start_min"] is None:
                    by_wkl[key]["start_min"] = e["start"]
                else:
                    by_wkl[key]["start_min"] = min(by_wkl[key]["start_min"], e["start"])
                by_wkl[key]["end_max"] = max(by_wkl[key]["end_max"], e["end"])
            result["by_inst_type"] = dict(by_type)
            result["by_slyr_wkl"] = {
                f"{k[0]}_{k[1]}": v for k, v in by_wkl.items()
            }
        print(json.dumps(result, indent=2))
        return

    print(f"ZeBu trace: {path.name}")
    print(f"Instructions: {len(entries)}")
    print(f"Total cycles: {total}")

    if args.summary:
        by_type = defaultdict(lambda: {"count": 0, "total_duration": 0})
        by_wkl = defaultdict(lambda: {"start_min": None, "end_max": 0})
        for e in entries:
            by_type[e["inst_type"]]["count"] += 1
            by_type[e["inst_type"]]["total_duration"] += e["end"] - e["start"]
            key = (e["slyr"], e["wkl"])
            if by_wkl[key]["start_min"] is None:
                by_wkl[key]["start_min"] = e["start"]
            else:
                by_wkl[key]["start_min"] = min(by_wkl[key]["start_min"], e["start"])
            by_wkl[key]["end_max"] = max(by_wkl[key]["end_max"], e["end"])

        print("\n--- By instruction type ---")
        for itype in sorted(by_type.keys()):
            v = by_type[itype]
            print(f"  {itype}: count={v['count']}, total_duration={v['total_duration']}")

        print("\n--- By (slyr, wkl) span [start_min, end_max] ---")
        for (slyr, wkl) in sorted(by_wkl.keys()):
            v = by_wkl[(slyr, wkl)]
            span = v["end_max"] - (v["start_min"] or 0)
            print(f"  slyr={slyr} wkl={wkl}: [{v['start_min']}, {v['end_max']}] span={span}")


if __name__ == "__main__":
    main()
