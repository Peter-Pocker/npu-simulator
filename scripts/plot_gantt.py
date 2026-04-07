import pandas as pd
import matplotlib.pyplot as plt
import argparse
import sys
import os

def main():
    parser = argparse.ArgumentParser(description="Plot NPU Simulator Core States Gantt Chart")
    parser.add_argument("--trace", default="trace",
                        help="Path to trace folder (containing state_trace.csv and workload_summary.csv), or path to state_trace.csv file")
    parser.add_argument("--workload-summary", default=None,
                        help="Path to workload_summary.csv (overrides auto path when --trace is a folder)")
    parser.add_argument("--output", default="core_states.png", help="Path to output image")
    args = parser.parse_args()

    trace_path = os.path.abspath(args.trace)
    if os.path.isdir(trace_path):
        state_trace_path = os.path.join(trace_path, "state_trace.csv")
        workload_summary_path = args.workload_summary
        if workload_summary_path is None:
            workload_summary_path = os.path.join(trace_path, "workload_summary.csv")
        else:
            workload_summary_path = os.path.abspath(workload_summary_path)
    else:
        state_trace_path = trace_path
        workload_summary_path = args.workload_summary
        if workload_summary_path is None:
            workload_summary_path = os.path.join(os.path.dirname(state_trace_path), "workload_summary.csv")
        else:
            workload_summary_path = os.path.abspath(workload_summary_path)

    if not os.path.exists(state_trace_path):
        print(f"Error: state_trace.csv not found at {state_trace_path}")
        sys.exit(1)

    df = pd.read_csv(state_trace_path)

    # Sort by core_id and cycle to ensure ordered processing
    df = df.sort_values(by=["core_id", "cycle"])
    has_loading_breakdown = False
    wl_summary = None
    if os.path.exists(workload_summary_path):
        wl_summary = pd.read_csv(workload_summary_path)
        if "loading_dram_cycles" in wl_summary.columns and "loading_core_cycles" in wl_summary.columns:
            has_loading_breakdown = True
        else:
            wl_summary = None

    # Extract max cycle to set x-axis limits correctly
    max_cycle = df["cycle"].max()

    fig, ax = plt.subplots(figsize=(20, 6))

    # States drawn in the chart (order matters for legend)
    colors = {
        "LOADING_DRAM": "#E67E22",   # dark orange – waiting for DRAM
        "LOADING_CORE": "#9B59B6",   # purple – waiting for other cores
        "LOADING_BOTH": "#D35400",   # overlap (both) – darker orange
        "LOADING": "tab:orange",      # fallback when no breakdown
        "COMPUTING": "tab:blue",
        "WRITEBACK": "tab:red"
    }

    # Segments: (core_id, start_cycle, duration, state_name, workload_idx or None)
    segments = []
    cores = df["core_id"].unique()
    max_core_id = max(cores) if len(cores) > 0 else 0

    for core in cores:
        core_events = df[df["core_id"] == core]
        current_state = "IDLE"
        start_cycle = 0
        loading_workload_idx = None  # set when we enter LOADING, used when we emit LOADING segment

        for _, row in core_events.iterrows():
            new_state = row["new_state"]
            cycle = row["cycle"]
            wl_idx = row.get("workload_idx", None)
            if pd.isna(wl_idx):
                wl_idx = None
            else:
                wl_idx = int(wl_idx)

            if current_state in colors:
                duration = cycle - start_cycle
                if duration > 0:
                    seg_wl_idx = loading_workload_idx if current_state == "LOADING" else None
                    segments.append((core, start_cycle, duration, current_state, seg_wl_idx))

            current_state = new_state
            start_cycle = cycle
            if new_state == "LOADING":
                loading_workload_idx = wl_idx
            else:
                loading_workload_idx = None

        # Last segment
        if current_state in colors:
            duration = max_cycle - start_cycle
            if duration > 0:
                seg_wl_idx = loading_workload_idx if current_state == "LOADING" else None
                segments.append((core, start_cycle, duration, current_state, seg_wl_idx))

    # Build lookup (core_id, workload_idx) -> (loading_dram_cycles, loading_core_cycles)
    def get_loading_breakdown(core_id, wl_idx):
        if wl_summary is None or wl_idx is None:
            return None, None
        rows = wl_summary[(wl_summary["core_id"] == core_id) & (wl_summary["workload_idx"] == wl_idx)]
        if rows.empty:
            return None, None
        r = rows.iloc[0]
        return r.get("loading_dram_cycles", 0), r.get("loading_core_cycles", 0)

    # Plot segments
    for core, start, duration, state, wl_idx in segments:
        if state == "LOADING" and has_loading_breakdown and wl_idx is not None:
            dram_cycles, core_cycles = get_loading_breakdown(core, wl_idx)
            if dram_cycles is not None and core_cycles is not None:
                try:
                    dram_cycles = int(dram_cycles)
                    core_cycles = int(core_cycles)
                except (TypeError, ValueError):
                    dram_cycles = core_cycles = 0
                # Overlap = time waiting for both; tail = time waiting only for the bottleneck
                total = max(dram_cycles, core_cycles)
                if total <= 0:
                    ax.barh(y=core, width=duration, left=start, height=0.8, color=colors["LOADING"])
                    continue
                overlap = min(dram_cycles, core_cycles)
                tail = total - overlap
                # Clamp to segment duration (summary may differ slightly from trace)
                if overlap + tail > duration:
                    scale = duration / (overlap + tail)
                    overlap = int(overlap * scale)
                    tail = duration - overlap
                # First part: waiting for both DRAM and other cores
                if overlap > 0:
                    ax.barh(y=core, width=overlap, left=start, height=0.8, color=colors["LOADING_BOTH"])
                # Second part: waiting only for the bottleneck (DRAM or other cores)
                if tail > 0:
                    if dram_cycles >= core_cycles:
                        ax.barh(y=core, width=tail, left=start + overlap, height=0.8, color=colors["LOADING_DRAM"])
                    else:
                        ax.barh(y=core, width=tail, left=start + overlap, height=0.8, color=colors["LOADING_CORE"])
                continue
        if state in colors:
            ax.barh(y=core, width=duration, left=start, height=0.8, color=colors[state])

    # Aesthetics
    ax.set_xlabel("Time(cycle)", fontsize=14, fontweight="bold")
    ax.set_ylabel("Core ID", fontsize=14, fontweight="bold")
    ax.set_yticks(range(max_core_id + 1))
    ax.set_ylim(-0.5, max_core_id + 0.5)
    ax.set_xlim(0, max_cycle if max_cycle > 0 else 1)

    # Legend: show loading breakdown labels when available
    if has_loading_breakdown:
        legend_order = ["LOADING_BOTH", "LOADING_DRAM", "LOADING_CORE", "COMPUTING", "WRITEBACK"]
    else:
        legend_order = ["LOADING", "COMPUTING", "WRITEBACK"]
    legend_order = [s for s in legend_order if s in colors]
    handles = [plt.Rectangle((0, 0), 1, 1, color=colors[s]) for s in legend_order]
    labels = [
        "Loading (both)",
        "Loading (DRAM)",
        "Loading (other cores)",
        "Computing",
        "Writeback"
    ]
    label_map = dict(zip(["LOADING_BOTH", "LOADING_DRAM", "LOADING_CORE", "COMPUTING", "WRITEBACK"], labels))
    labels = [label_map.get(s, s.replace("_", " ")) for s in legend_order]
    ax.legend(handles, labels, loc="upper right", bbox_to_anchor=(1.22, 1))

    plt.tight_layout()
    plt.savefig(args.output, dpi=300)
    print(f"Gantt chart saved to {args.output}")
    if has_loading_breakdown:
        print("  (LOADING split: DRAM vs other-core wait from workload_summary.csv)")

if __name__ == "__main__":
    main()
