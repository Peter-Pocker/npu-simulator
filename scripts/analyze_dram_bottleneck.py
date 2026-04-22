#!/usr/bin/env python3
"""Analyze DRAM bandwidth bottleneck from workload_summary.csv and hardware config.

Produces a quantitative breakdown:
  - Per-workload: DRAM read bytes, compute cycles, ideal DRAM latency, actual loading cycles
  - Aggregate: arithmetic intensity (compute/byte), effective BW utilization
  - Diagnosis: is the bottleneck in HW BW, IR data volume, or clock ratio?
"""
import argparse, csv, json, sys
from pathlib import Path
from collections import defaultdict

def parse_data_sources(detail: str):
    """Extract DRAM byte totals from data_sources like waiting_for=[t1(DRAM,317400B),...]"""
    total = 0
    import re
    for m in re.finditer(r'\(DRAM,(\d+)B\)', detail):
        total += int(m.group(1))
    return total

def main():
    p = argparse.ArgumentParser(description="Analyze DRAM read bottleneck")
    p.add_argument("--summary", required=True, help="workload_summary.csv path")
    p.add_argument("--config", default=None, help="Simulator config JSON (for HW params)")
    p.add_argument("--top", type=int, default=20, help="Show top-N worst workloads")
    args = p.parse_args()

    rows = []
    with open(args.summary) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    if not rows:
        print("No workload data found.")
        return

    hw_info = {}
    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)
        hw_info['core_freq_mhz'] = cfg.get('core', {}).get('clock_freq_mhz', 1000)
        dram = cfg.get('dram', {})
        hw_info['dram_channels'] = dram.get('num_channels', 4)
        hw_info['dram_standard'] = dram.get('standard', '?')
        hw_info['dram_timing'] = dram.get('timing', '?')
        hw_info['dram_clock_ratio'] = dram.get('clock_ratio', 1.0)
        hw_info['cache_line'] = dram.get('cache_line_size', 0) or 64
        sram = cfg.get('sram', {})
        hw_info['sram_read_bw'] = sram.get('read_bandwidth_bytes_per_cycle', 64)
        hw_info['sram_write_bw'] = sram.get('write_bandwidth_bytes_per_cycle', 64)

    print("=" * 80)
    print("DRAM Bottleneck Analysis")
    print("=" * 80)

    if hw_info:
        freq = hw_info['core_freq_mhz']
        ch = hw_info['dram_channels']
        std = hw_info['dram_standard']
        timing = hw_info['dram_timing']
        cr = hw_info['dram_clock_ratio']

        rate_match = None
        import re
        m = re.search(r'(\d+)Gbps', timing)
        if m:
            rate_gbps_per_pin = int(m.group(1))
        else:
            rate_gbps_per_pin = 2

        if 'HBM' in std:
            dq_bits = 128
        else:
            dq_bits = 8

        peak_bw_per_ch_gbps = rate_gbps_per_pin * dq_bits / 8
        peak_bw_total_gbps = peak_bw_per_ch_gbps * ch
        peak_bw_bytes_per_ns = peak_bw_total_gbps
        core_period_ns = 1000.0 / freq
        peak_bw_bytes_per_cycle = peak_bw_bytes_per_ns * core_period_ns

        print(f"\nHardware Config:")
        print(f"  Core freq:           {freq} MHz  (period = {core_period_ns:.1f} ns)")
        print(f"  DRAM:                {std} {timing}, {ch} channels")
        print(f"  DRAM clock ratio:    {cr}")
        print(f"  Peak BW per channel: {peak_bw_per_ch_gbps:.1f} GB/s")
        print(f"  Peak BW total:       {peak_bw_total_gbps:.1f} GB/s = {peak_bw_bytes_per_cycle:.1f} B/cycle")
        print(f"  SRAM read BW:        {hw_info['sram_read_bw']} B/cycle")
        print(f"  SRAM write BW:       {hw_info['sram_write_bw']} B/cycle")

        sram_bw = hw_info['sram_read_bw']
    else:
        peak_bw_bytes_per_cycle = None
        sram_bw = 64

    # Per-workload analysis
    wl_data = []
    total_loading = 0
    total_compute = 0
    total_writeback = 0
    total_dram_bytes = 0
    core_workloads = defaultdict(int)

    for r in rows:
        loading = int(r['loading_cycles'])
        compute = int(r['compute_cycles'])
        wb = int(r['writeback_cycles'])
        dram_bytes = parse_data_sources(r.get('data_sources', ''))
        core_id = int(r['core_id'])
        core_workloads[core_id] += 1

        ideal_sram_fill = dram_bytes / sram_bw if sram_bw > 0 else 0
        if peak_bw_bytes_per_cycle:
            ideal_dram_latency = dram_bytes / peak_bw_bytes_per_cycle
        else:
            ideal_dram_latency = 0

        ratio = loading / compute if compute > 0 else float('inf')

        wl_data.append({
            'core': core_id,
            'wl': int(r['workload_idx']),
            'layer': r['layer_name'],
            'dram_bytes': dram_bytes,
            'loading': loading,
            'compute': compute,
            'writeback': wb,
            'ideal_sram_fill': ideal_sram_fill,
            'ideal_dram': ideal_dram_latency,
            'load_compute_ratio': ratio,
        })

        total_loading += loading
        total_compute += compute
        total_writeback += wb
        total_dram_bytes += dram_bytes

    n_wl = len(wl_data)
    n_cores = len(core_workloads)

    print(f"\nWorkload Summary:")
    print(f"  Total workloads:     {n_wl} across {n_cores} cores")
    print(f"  Total DRAM read:     {total_dram_bytes / 1e6:.1f} MB")
    print(f"  Total loading cyc:   {total_loading:,}")
    print(f"  Total compute cyc:   {total_compute:,}")
    print(f"  Total writeback cyc: {total_writeback:,}")
    if total_compute > 0:
        print(f"  Loading/Compute:     {total_loading/total_compute:.3f}  (< 1 = compute bound, > 1 = memory bound)")
    if total_dram_bytes > 0 and total_compute > 0:
        arith_intensity = total_compute / total_dram_bytes
        print(f"  Arithmetic intensity:{arith_intensity:.2f} cycles/byte (higher = less BW pressure)")

    if peak_bw_bytes_per_cycle:
        ideal_total = total_dram_bytes / peak_bw_bytes_per_cycle
        print(f"\n  Ideal DRAM load (peak BW): {ideal_total:,.0f} cycles")
        print(f"  Actual DRAM load total:    {total_loading:,} cycles")
        if ideal_total > 0:
            bw_util = ideal_total / total_loading * 100
            print(f"  BW utilization:            {bw_util:.1f}%  (ideal/actual)")
            print(f"  Slowdown vs ideal:         {total_loading/ideal_total:.1f}x")

    # Layer-level aggregation
    layer_stats = defaultdict(lambda: {'count': 0, 'dram_bytes': 0, 'loading': 0, 'compute': 0, 'writeback': 0})
    for w in wl_data:
        s = layer_stats[w['layer']]
        s['count'] += 1
        s['dram_bytes'] += w['dram_bytes']
        s['loading'] += w['loading']
        s['compute'] += w['compute']
        s['writeback'] += w['writeback']

    print(f"\n{'='*80}")
    print("Per-Layer Breakdown (sorted by loading time)")
    print(f"{'='*80}")
    print(f"{'Layer':<20} {'Count':>5} {'DRAM(MB)':>10} {'Load(Kcyc)':>12} {'Comp(Kcyc)':>12} {'WB(Kcyc)':>10} {'Load/Comp':>10}")
    sorted_layers = sorted(layer_stats.items(), key=lambda x: x[1]['loading'], reverse=True)
    for name, s in sorted_layers:
        ratio = s['loading'] / s['compute'] if s['compute'] > 0 else float('inf')
        print(f"{name:<20} {s['count']:>5} {s['dram_bytes']/1e6:>10.2f} {s['loading']/1e3:>12.1f} {s['compute']/1e3:>12.1f} {s['writeback']/1e3:>10.1f} {ratio:>10.3f}")

    # Top-N worst workloads
    worst = sorted(wl_data, key=lambda x: x['loading'], reverse=True)[:args.top]
    print(f"\n{'='*80}")
    print(f"Top-{args.top} Worst DRAM-Loading Workloads")
    print(f"{'='*80}")
    print(f"{'Core':>4} {'WL':>4} {'Layer':<20} {'DRAM(KB)':>10} {'Load(cyc)':>12} {'Comp(cyc)':>12} {'IdealDRAM':>10} {'Slowdown':>8}")
    for w in worst:
        slowdown = w['loading'] / w['ideal_dram'] if w['ideal_dram'] > 0 else 0
        print(f"{w['core']:>4} {w['wl']:>4} {w['layer']:<20} {w['dram_bytes']/1024:>10.1f} {w['loading']:>12,} {w['compute']:>12,} {w['ideal_dram']:>10.0f} {slowdown:>8.1f}x")

    # Diagnosis
    print(f"\n{'='*80}")
    print("Diagnosis")
    print(f"{'='*80}")

    if peak_bw_bytes_per_cycle:
        ideal_total = total_dram_bytes / peak_bw_bytes_per_cycle
        slowdown = total_loading / ideal_total if ideal_total > 0 else 0

        if total_loading > total_compute * 0.5:
            print("[!] System is MEMORY-BOUND: loading time is significant relative to compute.")
        else:
            print("[OK] System is COMPUTE-BOUND: loading time is small relative to compute.")

        if slowdown > 5:
            print(f"[!] DRAM BW severely underutilized ({slowdown:.1f}x slower than ideal).")
            print("    Likely causes:")
            print("    1. Large data volumes per workload (IR/compiler issue)")
            print("    2. Ramulator2 queuing contention (4 cores sharing DRAM)")
            print("    3. Row-miss / bank-conflict overhead in DRAM controller")
        elif slowdown > 2:
            print(f"[!] DRAM BW moderately underutilized ({slowdown:.1f}x slower than ideal).")
            print("    Expected from queuing + row-miss overhead.")

        large_layers = [(n, s) for n, s in sorted_layers if s['compute'] > 0 and s['dram_bytes'] > 1e6 and s['loading'] / s['compute'] > 0.5]
        if large_layers:
            print(f"\n    Layers with excessive DRAM traffic (>1MB, load/compute > 0.5):")
            for n, s in large_layers[:5]:
                print(f"      {n}: {s['dram_bytes']/1e6:.1f} MB, load/compute = {s['loading']/s['compute']:.2f}")

    print()

if __name__ == "__main__":
    main()
