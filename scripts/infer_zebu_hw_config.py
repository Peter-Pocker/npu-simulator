#!/usr/bin/env python3
"""
从 third_party/Gemini-Compiler-IR 的 Scheduler IR（stschedule.json）与文件名中
推断 ZeBu test chip 的硬件配置，并输出与 npu_sim 兼容的 config 片段或完整 JSON。

可推断项：
  - 核阵列拓扑：xlen × ylen（来自 IR）
  - 每核 L2/SRAM 大小：buffersize（来自 IR，字节）
  - 调度时假设的 DRAM 带宽：来自文件名 bw*（如 bw16 → 16 GB/s）
  - 批大小 / 核数：来自文件名 b*_c*

不可直接推断（需文档或反推）：
  - MAC 数量、vector units、时钟频率
  - NoC/DRAM 详细参数
  - ZeBu trace 仅含指令类型与 [start,end] 周期，无硬件参数

用法：
  python3 scripts/infer_zebu_hw_config.py --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json
  python3 scripts/infer_zebu_hw_config.py --ir-dir third_party/Gemini-Compiler-IR/scheduler_output --out configs/chips/zebu_inferred.json
"""
import argparse
import json
import re
import sys
from pathlib import Path


def parse_stschedule_filename(path: Path) -> dict:
    """
    从 stschedule 文件名解析调度时使用的约束。
    例: int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json
        -> batch=1, cores=1 (c1), dram_bw_gbps=16
    """
    name = path.name
    out = {"batch": None, "num_cores": None, "dram_bw_gbps": None}
    m_batch = re.search(r"_b(\d+)_", name)
    m_core = re.search(r"_c(\d+)_", name)
    m_bw = re.search(r"_bw(\d+)_", name)
    if m_batch:
        out["batch"] = int(m_batch.group(1))
    if m_core:
        out["num_cores"] = int(m_core.group(1))
    if m_bw:
        out["dram_bw_gbps"] = int(m_bw.group(1))
    return out


def extract_ir_config(ir_path: Path) -> dict:
    """从 stschedule JSON 提取可推断的硬件相关字段。"""
    with open(ir_path, "r", encoding="utf-8") as f:
        root = json.load(f)
    cfg = {
        "num_cores_x": root.get("xlen", 0),
        "num_cores_y": root.get("ylen", 0),
        "buffersize_bytes": root.get("buffersize"),
        "top_batch_cut": root.get("top_batch_cut"),
    }
    return cfg


def build_simulator_config(ir_config: dict, filename_config: dict) -> dict:
    """
    构造与 npu_sim SimConfig 兼容的 JSON。
    可推断的填上，其余用与 ZeBu 对比时常用的默认值（如 simple_single_core_config）。
    """
    num_x = ir_config.get("num_cores_x") or 1
    num_y = ir_config.get("num_cores_y") or 1
    sram_bytes = ir_config.get("buffersize_bytes") or 8 * 1024 * 1024
    sram_kb = sram_bytes // 1024

    # 与 experiments/simple_single_core_config.json 对齐的默认值；MAC/vector 等需文档或反推
    config = {
        "_comment": "Inferred from Gemini-Compiler-IR Scheduler IR + filename; MAC/NoC/DRAM details are defaults.",
        "num_cores_x": num_x,
        "num_cores_y": num_y,
        "element_size_bits": 8,
        "core": {
            "mac_units": 256,
            "vector_units": 64,
            "clock_freq_mhz": 1000,
            "use_analytical_time": True,
        },
        "sram": {
            "size_kb": sram_kb,
            "read_bandwidth_bytes_per_cycle": 64,
            "write_bandwidth_bytes_per_cycle": 64,
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
    }
    if filename_config.get("dram_bw_gbps") is not None:
        config["_inferred_from_filename"] = {
            "batch": filename_config.get("batch"),
            "num_cores_schedule": filename_config.get("num_cores"),
            "dram_bw_gbps": filename_config.get("dram_bw_gbps"),
        }
    return config


def main():
    ap = argparse.ArgumentParser(
        description="Infer ZeBu test chip config from Scheduler IR and filename."
    )
    ap.add_argument(
        "--ir",
        type=Path,
        help="Path to a single stschedule.json",
    )
    ap.add_argument(
        "--ir-dir",
        type=Path,
        help="Directory of stschedule JSONs; use first *_stschedule.json found",
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="Write inferred config JSON to this path",
    )
    ap.add_argument(
        "--no-defaults",
        action="store_true",
        help="Only print inferred fields (no full simulator config)",
    )
    args = ap.parse_args()

    ir_path = args.ir
    if not ir_path and args.ir_dir:
        candidates = list(args.ir_dir.glob("*_stschedule.json"))
        if not candidates:
            print("No *_stschedule.json in", args.ir_dir, file=sys.stderr)
            sys.exit(1)
        ir_path = sorted(candidates)[0]
        print("Using", ir_path, file=sys.stderr)

    if not ir_path or not ir_path.exists():
        print("Need --ir <path> or --ir-dir <dir> pointing to stschedule.json", file=sys.stderr)
        sys.exit(1)

    filename_config = parse_stschedule_filename(ir_path)
    ir_config = extract_ir_config(ir_path)

    print("From filename:", filename_config)
    print("From IR:      ", ir_config)

    if args.no_defaults:
        out_obj = {"filename": filename_config, "ir": ir_config}
    else:
        out_obj = build_simulator_config(ir_config, filename_config)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out_obj, f, indent=2, ensure_ascii=False)
        print("Wrote", args.out)
    else:
        print(json.dumps(out_obj, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
