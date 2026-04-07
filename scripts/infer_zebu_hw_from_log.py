#!/usr/bin/env python3
"""
从 ZeBu backend log (backend_default_globalLog.log) 与 Scheduler IR 推断 test chip 完整配置，
并生成 npu_sim 可用的 config JSON。

Backend log 可推断：
  - 核/BRAM 时钟：Memory Delays 中 bram 40 ns → 25 MHz
  - DRAM：dram3 16.95 MHz, 2GB, 1024-bit
  - 设计规模：DSP 1110, BRAM 2251（用于备注，不直接映射为 MAC 数）
  - L2 物理：spram_4096x64 x32 + spram_1024x64 x127 ≈ 2MB（调度仍用 IR 的 buffersize 8MB）
  - SRAM 读写带宽：L2 使用 spram_1024x64，Data width=64bit、1 port、40ns/cycle → 每周期每端口 8 bytes；
    若 log 中 l2buffer 对应 "W: N" 则 sram_bandwidth_bytes_per_cycle = N/8（保守估计，假定 1 读口+1 写口各 N bit）

Scheduler IR 提供：xlen, ylen, buffersize（字节）

用法：
  python3 scripts/infer_zebu_hw_from_log.py --log third_party/Gemini-Compiler-IR/ZeBu_files/backend_default_globalLog.log --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json -o configs/chips/zebu_testchip.json
"""
import argparse
import json
import re
import sys
from pathlib import Path


def parse_backend_log(log_path: Path) -> dict:
    """从 backend_default_globalLog.log 解析硬件相关字段。"""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    out = {
        "core_clock_mhz": None,
        "dram_clock_mhz": None,
        "dram_size_gb": None,
        "dram_data_width_bits": None,
        "design_dsp_count": None,
        "design_bram_count": None,
        "bram_delay_ns": None,
        "sram_data_width_bits": None,  # L2/spram 数据位宽，用于推断 read/write bytes per cycle
    }
    # Design size: ... |2251|1110|
    m = re.search(r"\|Design size\|[^\n]+\|\s*(\d+)\s*\|\s*(\d+)\s*\|", text)
    if m:
        out["design_bram_count"] = int(m.group(1))
        out["design_dsp_count"] = int(m.group(2))
    # BRAM 40 ns -> 25 MHz
    m = re.search(r"\|\s*dpram_zebu_32x288.*?\|\s*(\d+)\s*ns\s*\|\s*bram\s*\|\s*([\d.]+)\s*MHz", text)
    if m:
        out["bram_delay_ns"] = int(m.group(1))
        out["core_clock_mhz"] = round(float(m.group(2)))
    if out["core_clock_mhz"] is None:
        m = re.search(r"\|\s*(\d+)\s*ns\s*\|.*\|\s*bram\s*\|\s*([\d.]+)\s*MHz", text)
        if m:
            out["bram_delay_ns"] = int(m.group(1))
            out["core_clock_mhz"] = round(float(m.group(2)))
    # DRAM: 2GBx1024; dram3 line has 16.95 MHz (avoid matching 7.14 MHz from asyn_fifo)
    m = re.search(r"dpram_zebu_2GBx1024", text)
    if m:
        out["dram_size_gb"] = 2
        out["dram_data_width_bits"] = 1024
    m = re.search(r"dram3\s*\|\s*([\d.]+)\s*MHz", text)
    if m:
        out["dram_clock_mhz"] = round(float(m.group(1)), 2)
    # L2 SRAM 带宽：Memory Delays 中 l2buffer 对应 spram 的 "P: N W: W" → W 为数据位宽(bits)（W 在路径前）
    m = re.search(r"W:\s*(\d+)\s*[^|\n]*\|[^|\n]*l2buffer", text)
    if m:
        out["sram_data_width_bits"] = int(m.group(1))
    if out["sram_data_width_bits"] is None:
        # 备选：ZMEM 表里 spram_zebu_1024x64 的 Data width 为 64
        m = re.search(r"spram_zebu_1024x64_ZMEM_mem\s*\|[^|]+\|\s*(\d+)\s*\|", text)
        if m:
            out["sram_data_width_bits"] = int(m.group(1))
    return out


def parse_stschedule(ir_path: Path) -> dict:
    """从 stschedule.json 解析 xlen, ylen, buffersize。"""
    with open(ir_path, "r", encoding="utf-8") as f:
        root = json.load(f)
    return {
        "num_cores_x": root.get("xlen", 1),
        "num_cores_y": root.get("ylen", 1),
        "buffersize_bytes": root.get("buffersize", 8 * 1024 * 1024),
    }


def build_config(log_params: dict, ir_params: dict, dram_bw_gbps: int = 16) -> dict:
    """组装 npu_sim SimConfig 兼容的 JSON。"""
    num_x = ir_params.get("num_cores_x") or 1
    num_y = ir_params.get("num_cores_y") or 1
    sram_bytes = ir_params.get("buffersize_bytes") or (8 * 1024 * 1024)
    sram_kb = sram_bytes // 1024
    core_mhz = log_params.get("core_clock_mhz") or 25
    # SRAM 带宽：ZeBu log 中 L2 为 64-bit 宽、40ns/周期 → 8 bytes/cycle/端口；若无则用默认 64
    sram_bw = 64
    w_bits = log_params.get("sram_data_width_bits")
    if w_bits is not None:
        sram_bw = max(1, w_bits // 8)
    return {
        "_comment": "ZeBu test chip config inferred from backend_default_globalLog.log + Scheduler IR. Use with scheduler_output/*_stschedule.json for ZeBu validation.",
        "num_cores_x": num_x,
        "num_cores_y": num_y,
        "element_size_bits": 8,
        "core": {
            "mac_units": 256,
            "vector_units": 64,
            "clock_freq_mhz": core_mhz,
            "use_analytical_time": True,
        },
        "sram": {
            "size_kb": sram_kb,
            "read_bandwidth_bytes_per_cycle": sram_bw,
            "write_bandwidth_bytes_per_cycle": sram_bw,
        },
        "ni": {
            "max_outstanding_reqs": 16,
            "injection_queue_size": 8,
            "ejection_queue_size": 8,
        },
        "noc": {
            "backend": "simple",
            "flit_size_bytes": 16,
            "hop_latency_cycles": 1,
            "router_latency_cycles": 1,
        },
        "dram": {
            "backend": "simple",
            "num_channels": 4,
        },
        "topology": {
            "dram_controllers": [],
            "dram_routing_policy": "nearest",
        },
        "_inferred": {
            "from_log": {
                "core_clock_mhz": log_params.get("core_clock_mhz"),
                "dram_clock_mhz": log_params.get("dram_clock_mhz"),
                "design_dsp": log_params.get("design_dsp_count"),
                "design_bram": log_params.get("design_bram_count"),
                "sram_data_width_bits": log_params.get("sram_data_width_bits"),
                "sram_bandwidth_bytes_per_cycle": sram_bw,
            },
            "from_ir": {
                "buffersize_bytes": ir_params.get("buffersize_bytes"),
                "sram_kb": sram_kb,
            },
            "dram_bw_gbps_schedule": dram_bw_gbps,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Infer ZeBu test chip config from backend log + Scheduler IR.")
    ap.add_argument("--log", type=Path, required=True, help="Path to backend_default_globalLog.log")
    ap.add_argument("--ir", type=Path, required=True, help="Path to one *_stschedule.json")
    ap.add_argument("-o", "--out", type=Path, required=True, help="Output config JSON path")
    ap.add_argument("--dram-bw-gbps", type=int, default=16, help="DRAM bandwidth from filename (bwN) default 16")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"Error: log not found: {args.log}", file=sys.stderr)
        sys.exit(1)
    if not args.ir.exists():
        print(f"Error: IR not found: {args.ir}", file=sys.stderr)
        sys.exit(1)

    log_params = parse_backend_log(args.log)
    ir_params = parse_stschedule(args.ir)
    config = build_config(log_params, ir_params, args.dram_bw_gbps)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("Parsed from log:", log_params)
    print("Parsed from IR:", ir_params)
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
