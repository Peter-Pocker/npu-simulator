#!/usr/bin/env python3
"""
Slice an NPU simulator IR (Gemini/SET style JSON) to a subgraph.

Modes:
  1. By layer names: --layers "conv1,pool1,conv_2_0_a"  (include these layers + all dependencies)
  2. By representative sub-structure: --first N  (first N workloads in core order + dependencies)

Output: new IR JSON that the simulator can run (smaller, faster).
"""

import argparse
import json
import sys
from pathlib import Path


def get_core_ids(root):
    """Return sorted list of core id keys that are numeric (exclude -1, xlen, etc.)."""
    cores = []
    for k in root:
        if k in ("xlen", "ylen", "top_batch_cut", "buffersize", "-1"):
            continue
        try:
            c = int(k)
            if c >= 0 and isinstance(root[k], list):
                cores.append(c)
        except (ValueError, TypeError):
            continue
    return sorted(cores)


def collect_producers(root, core_ids):
    """transfer_id -> (core_id, workload_idx)."""
    tid_to_producer = {}
    for c in core_ids:
        for w_idx, wl in enumerate(root[str(c)]):
            for ofmap in wl.get("ofmap", []) or []:
                tid = ofmap.get("transfer_id")
                if tid is not None:
                    tid_to_producer[int(tid)] = (c, w_idx)
    return tid_to_producer


def collect_consumed_transfers(wl):
    """From a workload's buffer sources, return list of (transfer_id, is_dram)."""
    consumed = []
    for buf in wl.get("buffer", []) or []:
        for src in buf.get("source", []) or []:
            tid = src.get("transfer_id")
            if tid is None:
                continue
            typ = (src.get("type") or "").upper()
            consumed.append((int(tid), typ == "DRAM"))
    # weight can have transfer_id at top level
    if "weight" in wl and isinstance(wl["weight"], dict):
        for tid in wl["weight"].get("transfer_id", []) or []:
            consumed.append((int(tid), True))  # weight from DRAM
    return consumed


def close_under_predecessors(seed_set, root, core_ids, tid_to_producer):
    """Add all predecessors (producers of consumed transfer_ids) to the set."""
    selected = set(seed_set)
    changed = True
    while changed:
        changed = False
        for (c, w_idx) in list(selected):
            wl = root[str(c)][w_idx]
            for tid, is_dram in collect_consumed_transfers(wl):
                if is_dram:
                    continue
                prod = tid_to_producer.get(tid)
                if prod is not None and prod not in selected:
                    selected.add(prod)
                    changed = True
    return selected


def build_order(core_ids, root):
    """(core_id, workload_idx) in lexicographic order (core 0 all wl, then core 1 ...)."""
    order = []
    for c in core_ids:
        for w_idx in range(len(root[str(c)])):
            order.append((c, w_idx))
    return order


def main():
    ap = argparse.ArgumentParser(
        description="Slice NPU simulator IR to a subgraph by layer names or first N workloads."
    )
    ap.add_argument("--ir", required=True, help="Path to input IR JSON")
    ap.add_argument("--output", "-o", default=None, help="Path to output sliced IR JSON (required unless --list-layers)")
    ap.add_argument(
        "--layers",
        type=str,
        default=None,
        help='Comma-separated layer names to include (e.g. "conv1,pool1,conv_2_0_a"). Includes dependencies.',
    )
    ap.add_argument(
        "--first",
        type=int,
        default=None,
        help="Include the first N workloads (in core order) plus all their dependencies (no need to list layer names).",
    )
    ap.add_argument(
        "--list-layers",
        action="store_true",
        help="Only list all unique layer names in the IR and exit.",
    )
    args = ap.parse_args()

    if not args.layers and args.first is None and not args.list_layers:
        ap.error("Specify one of: --layers, --first N, or --list-layers")
    if not args.list_layers and not args.output:
        ap.error("Specify --output for slice output (not needed for --list-layers)")

    path = Path(args.ir)
    if not path.exists():
        print(f"Error: IR file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        root = json.load(f)

    core_ids = get_core_ids(root)
    if not core_ids:
        print("Error: No core arrays found in IR.", file=sys.stderr)
        sys.exit(1)

    # List layers and exit
    if args.list_layers:
        names = set()
        for c in core_ids:
            for wl in root[str(c)]:
                n = wl.get("layer_name") or ""
                if n:
                    names.add(n)
        for n in sorted(names):
            print(n)
        return

    tid_to_producer = collect_producers(root, core_ids)

    if args.layers:
        want_names = {s.strip() for s in args.layers.split(",") if s.strip()}
        seed_set = set()
        for c in core_ids:
            for w_idx, wl in enumerate(root[str(c)]):
                if (wl.get("layer_name") or "").strip() in want_names:
                    seed_set.add((c, w_idx))
        if not seed_set:
            print("Warning: No workloads matched the given layer names.", file=sys.stderr)
    else:
        order = build_order(core_ids, root)
        n = max(0, min(args.first, len(order)))
        seed_set = set(order[:n])

    selected = close_under_predecessors(seed_set, root, core_ids, tid_to_producer)

    # Build new IR: compact core ids, renumber workload_id, filter dram
    old_core_to_new = {}
    new_cores = []
    for c in core_ids:
        indices = [w_idx for (oc, w_idx) in selected if oc == c]
        if not indices:
            continue
        indices.sort()
        old_core_to_new[c] = len(new_cores)
        new_cores.append((c, indices))

    # Selected set in terms of (new_core, new_wl_idx) for convenience
    old_to_new_wl = {}  # (old_c, old_w_idx) -> (new_core_idx, new_wl_idx)
    for new_cidx, (old_c, w_indices) in enumerate(new_cores):
        for new_widx, old_widx in enumerate(w_indices):
            old_to_new_wl[(old_c, old_widx)] = (new_cidx, new_widx)

    out = {
        "xlen": root.get("xlen", len(new_cores)),
        "ylen": root.get("ylen", 1),
        "top_batch_cut": root.get("top_batch_cut", 1),
    }
    if "buffersize" in root:
        out["buffersize"] = root["buffersize"]

    def remap_core_id_in_wl(wl, old_to_new_map):
        """Rewrite core ids in buffer sources and ofmap destinations."""
        for buf in wl.get("buffer", []) or []:
            for src in buf.get("source", []) or []:
                if (src.get("type") or "").lower() == "core":
                    old_id = src.get("id") if "id" in src else src.get("core_id")
                    if old_id is not None and int(old_id) in old_to_new_map:
                        src["id"] = old_to_new_map[int(old_id)]
                        if "core_id" in src:
                            src["core_id"] = old_to_new_map[int(old_id)]
        for ofmap in wl.get("ofmap", []) or []:
            for dest in ofmap.get("destination", []) or []:
                if (dest.get("type") or "").lower() == "core":
                    old_id = dest.get("id") if "id" in dest else dest.get("core_id")
                    if old_id is not None and int(old_id) in old_to_new_map:
                        dest["id"] = old_to_new_map[int(old_id)]
                        if "core_id" in dest:
                            dest["core_id"] = old_to_new_map[int(old_id)]

    for new_cidx, (old_c, w_indices) in enumerate(new_cores):
        arr = []
        for new_wl_idx, old_wl_idx in enumerate(w_indices):
            wl = json.loads(json.dumps(root[str(old_c)][old_wl_idx]))  # deep copy
            wl["workload_id"] = new_wl_idx
            remap_core_id_in_wl(wl, old_core_to_new)
            arr.append(wl)
        out[str(new_cidx)] = arr

    # DRAM section: keep only entries used by selected workloads
    dram = root.get("-1")
    if isinstance(dram, dict):
        out_dram = {"out": [], "in": []}
        # "out" = dram reads: keep if destination (core_id, workload_id) is in selected
        for entry in dram.get("out", []) or []:
            dest_list = entry.get("destination", []) or []
            if not dest_list:
                continue
            d = dest_list[0]
            core_key = d.get("core_id", d.get("id"))
            if core_key is None:
                continue
            old_c = int(core_key)
            old_w = int(d.get("workload_id", 0))
            if (old_c, old_w) not in old_to_new_wl:
                continue
            new_cidx, new_widx = old_to_new_wl[(old_c, old_w)]
            ent = json.loads(json.dumps(entry))
            ent["destination"] = [
                {"type": "core", "id": new_cidx, "core_id": new_cidx, "workload_id": new_widx}
            ]
            if "layer_name" in entry:
                ent["layer_name"] = entry["layer_name"]
            out_dram["out"].append(ent)
        # "in" = dram writes: keep if (core_id, workload_id) is in selected
        for entry in dram.get("in", []) or []:
            old_c = int(entry.get("core_id", -1))
            old_w = int(entry.get("workload_id", 0))
            if (old_c, old_w) not in old_to_new_wl:
                continue
            new_cidx, new_widx = old_to_new_wl[(old_c, old_w)]
            ent = json.loads(json.dumps(entry))
            ent["core_id"] = new_cidx
            ent["workload_id"] = new_widx
            out_dram["in"].append(ent)
        out["-1"] = out_dram

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=4, ensure_ascii=False)

    total_before = sum(len(root[str(c)]) for c in core_ids)
    total_after = len(selected)
    print(f"Sliced IR: {total_after} workloads (from {total_before}) -> {out_path}")
    print(f"Cores: {len(new_cores)} (from {len(core_ids)})")


if __name__ == "__main__":
    main()
