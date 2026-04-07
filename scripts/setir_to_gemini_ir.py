#!/usr/bin/env python3
"""
将 SET-IR 生成的 JSON IR 转换为 third_party/Gemini-Compiler-IR 的 Scheduler IR 格式，
使仿真器只需支持一种 IR 格式（Gemini Scheduler IR）。

用法:
  python3 scripts/setir_to_gemini_ir.py <setir_output.json> [--output <gemini_ir.json>] [--buffersize 8388608]
  python3 scripts/setir_to_gemini_ir.py resnet50_2x2_setir.json -o resnet50_2x2_gemini_format.json

转换内容:
  - 顶层: metadata 拆到根级，DDR -> "-1"，增加 buffersize
  - 核 ID: 将 SET-IR 的非连续核 ID 重映射为 0,1,2,... 并只保留数字核键
  - 每个 workload: ifmap 单对象 -> 数组；buffer 从 data_id 展开为完整 buffer 快照（含 source）；destination 的 id -> core_id 并补 workload_id
  - DRAM "in": 补全 layer_name、workload_id（由 producer 推断）
  - DRAM "out": destination 中 id -> core_id，补 workload_id
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def normalize_lower_upper(obj: dict) -> tuple[list, list]:
    """将 SET-IR 的 lower/upper（可能为 int、list 或 range）转为 4 元组 list。"""
    if "range" in obj and isinstance(obj["range"], dict):
        r = obj["range"]
        lower = r.get("lower", [0, 0, 0, 0])
        upper = r.get("upper", [0, 0, 0, 0])
    else:
        lower = obj.get("lower", [0, 0, 0, 0])
        upper = obj.get("upper", [0, 0, 0, 0])
    if isinstance(lower, list):
        lower = [int(x) for x in lower][:4]
    else:
        lower = [0, int(lower), 0, 0]
    if isinstance(upper, list):
        upper = [int(x) for x in upper][:4]
    else:
        upper = [0, int(upper), 0, 0]
    while len(lower) < 4:
        lower.append(0)
    while len(upper) < 4:
        upper.append(0)
    return lower, upper


def convert_source_list(sources: list, old_to_new_core: dict) -> list:
    """将 buffer_data.source 转为 Gemini 的 source：id -> core_id，并统一键名。"""
    out = []
    for s in sources:
        sid = s.get("id", -1)
        core_id = old_to_new_core.get(sid, sid if sid < 0 else 0)
        elem = {
            "type": "DRAM" if s.get("type") == "DRAM" else "core",
            "transfer_id": s.get("transfer_id", 0),
            "size": s.get("size", 0),
            "layer_name": s.get("layer_name", ""),
        }
        if elem["type"] == "core":
            elem["core_id"] = core_id
        else:
            elem["core_id"] = -1
        if "lower" in s and "upper" in s:
            lo, hi = normalize_lower_upper(s)
            elem["lower"] = lo
            elem["upper"] = hi
        out.append(elem)
    return out


def build_tid_to_producer(cores_data: dict, core_ids: list) -> dict:
    """transfer_id -> (core_id, workload_id, layer_name) 生产者。"""
    tid_to_producer = {}
    for cid in core_ids:
        wl_list = cores_data.get(str(cid), [])
        if not isinstance(wl_list, list):
            continue
        for wl in wl_list:
            wlid = wl.get("workload_id", 0)
            layer_name = wl.get("layer_name", "")
            for of in wl.get("ofmap", []) or []:
                tid = of.get("transfer_id")
                if tid is not None:
                    tid_to_producer[tid] = (cid, wlid, layer_name)
    return tid_to_producer


def build_tid_core_to_consumer_workload(cores_data: dict, core_ids: list) -> dict:
    """(transfer_id, core_id) -> workload_id：某核上消费该 transfer_id 的 workload。"""
    key_to_wlid = {}
    for cid in core_ids:
        wl_list = cores_data.get(str(cid), [])
        if not isinstance(wl_list, list):
            continue
        for wl in wl_list:
            wlid = wl.get("workload_id", 0)
            ifmap = wl.get("ifmap")
            if not ifmap:
                continue
            tids = ifmap.get("transfer_id")
            if isinstance(tids, list):
                for tid in tids:
                    key_to_wlid[(tid, cid)] = wlid
            elif tids is not None:
                key_to_wlid[(tids, cid)] = wlid
    return key_to_wlid


def convert_workload(
    wl: dict,
    buffer_data: dict,
    old_to_new_core: dict,
    consumer_map: dict,
) -> dict:
    """将 SET-IR 的单个 workload 转为 Gemini Scheduler IR 格式。"""
    out = {
        "workload_id": wl.get("workload_id", 0),
        "layer_name": wl.get("layer_name", ""),
        "layer_type": wl.get("layer_type", "pe"),
        "workload": wl.get("workload", [[0, 0, 0, 0], [0, 0, 0, 0]]),
        "ofmap_size": wl.get("ofmap_size", 0),
        "time": wl.get("time", 0),
    }

    # ifmap: 单对象 -> 数组，补 size/align/bitwidth
    ifmap_raw = wl.get("ifmap")
    if ifmap_raw:
        tids = ifmap_raw.get("transfer_id")
        if not isinstance(tids, list):
            tids = [tids] if tids is not None else []
        size = 0
        for bid in wl.get("buffer", []) or []:
            data_id = bid.get("data_id")
            if data_id is not None and isinstance(buffer_data.get(str(data_id)), dict):
                bd = buffer_data[str(data_id)]
                if bd.get("type") == "ifmap":
                    size = bd.get("size", 0)
                    break
        ifmap_entry = {
            "lower": ifmap_raw.get("lower", [0, 0, 0, 0]),
            "upper": ifmap_raw.get("upper", [0, 0, 0, 0]),
            "size": size,
            "transfer_id": tids,
            "align": 8,
            "bitwidth": 8,
        }
        out["ifmap"] = [ifmap_entry]
    else:
        out["ifmap"] = []

    # ofmap: destination 的 id -> core_id，补 workload_id
    ofmap_list = []
    for of in wl.get("ofmap", []) or []:
        go = {
            "lower": of.get("lower", [0, 0, 0, 0]),
            "upper": of.get("upper", [0, 0, 0, 0]),
            "size": of.get("size", 0),
            "transfer_id": of.get("transfer_id", 0),
        }
        dest_list = []
        for d in of.get("destination", []) or []:
            old_id = d.get("id", d.get("core_id", -1))
            new_core = old_to_new_core.get(old_id, old_id)
            tid = of.get("transfer_id")
            wlid = consumer_map.get((tid, old_id), 0)
            dest_list.append({
                "core_id": new_core,
                "type": "DRAM" if d.get("type") == "DRAM" else "core",
                "workload_id": wlid,
                "layer_name": d.get("layer_name", ""),
            })
        go["destination"] = dest_list
        ofmap_list.append(go)
    out["ofmap"] = ofmap_list

    # buffer: 从 data_id 展开为完整快照，含 source（id->core_id）
    buffer_list = []
    tensor_id = 0
    for bid in wl.get("buffer", []) or []:
        data_id = bid.get("data_id")
        if data_id is None:
            continue
        bd = buffer_data.get(str(data_id))
        if not isinstance(bd, dict):
            continue
        addr = bid.get("start_reserve", 0)
        if not isinstance(addr, int):
            addr = 0
        lower, upper = normalize_lower_upper(bd)
        src_list = bd.get("source", [])
        if not isinstance(src_list, list):
            src_list = []
        gemini_src = convert_source_list(src_list, old_to_new_core)
        buf_entry = {
            "address": addr,
            "align": 8,
            "bitwidth": 8,
            "size": bd.get("size", 0),
            "lower": lower,
            "upper": upper,
            "type": bd.get("type", "ifmap"),
            "source": gemini_src,
            "tensor_id": tensor_id,
            "tensor_order": tensor_id,
            "newly_added": bool(bid.get("start_reserve", False)),
            "cur_wl_ifmap": bd.get("type") == "ifmap",
            "layer_name": bd.get("layer_name", bd.get("layer", "")),
        }
        tids_from_src = [x.get("transfer_id") for x in gemini_src if x.get("transfer_id") is not None]
        buf_entry["transfer_id"] = tids_from_src if tids_from_src else []
        tensor_id += 1
        buffer_list.append(buf_entry)
    out["buffer"] = buffer_list

    # weight: 若该 workload 有 weight 类 buffer_data，则单独写出
    weight_tids = []
    weight_size = 0
    weight_lower = [0, 0, 0, 0]
    weight_upper = [0, 0, 0, 0]
    for bid in wl.get("buffer", []) or []:
        data_id = bid.get("data_id")
        if data_id is None:
            continue
        bd = buffer_data.get(str(data_id))
        if not isinstance(bd, dict) or bd.get("type") != "weight":
            continue
        weight_size = bd.get("size", 0)
        weight_lower, weight_upper = normalize_lower_upper(bd)
        for s in bd.get("source", []) or []:
            if s.get("type") == "DRAM" and s.get("transfer_id") is not None:
                weight_tids.append(s["transfer_id"])
        break
    if weight_tids or weight_size:
        out["weight"] = {
            "size": weight_size,
            "transfer_id": weight_tids if weight_tids else [0],
            "lower": weight_lower,
            "upper": weight_upper,
        }

    out["ring_buffer_info"] = [[0, 8 * 1024 * 1024]]
    return out


def convert_dram_in(entries: list, old_to_new_core: dict, tid_to_producer: dict) -> list:
    """转换 DRAM in：补 layer_name、workload_id；core_id 重映射。"""
    out = []
    for e in entries or []:
        tid = e.get("transfer_id")
        info = tid_to_producer.get(tid, (e.get("core_id"), 0, ""))
        old_cid = info[0]
        wlid = info[1]
        layer_name = info[2]
        new_core = old_to_new_core.get(old_cid, old_cid)
        out.append({
            "core_id": new_core,
            "layer_name": layer_name,
            "lower": e.get("lower", [0, 0, 0, 0]),
            "upper": e.get("upper", [0, 0, 0, 0]),
            "transfer_id": tid,
            "related_ofmap": e.get("related_ofmap", []),
            "workload_id": wlid,
        })
    return out


def convert_dram_out(entries: list, old_to_new_core: dict, consumer_map: dict) -> list:
    """转换 DRAM out：destination 中 id -> core_id，补 workload_id。"""
    out = []
    for e in entries or []:
        tid = e.get("transfer_id")
        dest_list = []
        for d in e.get("destination", []) or []:
            old_id = d.get("id", d.get("core_id", -1))
            new_core = old_to_new_core.get(old_id, old_id)
            wlid = consumer_map.get((tid, old_id), 0)
            dest_list.append({
                "core_id": new_core,
                "type": "DRAM" if d.get("type") == "DRAM" else "core",
                "workload_id": wlid,
                "layer_name": d.get("layer_name", ""),
            })
        out.append({
            "destination": dest_list,
            "lower": e.get("lower", [0, 0, 0, 0]),
            "upper": e.get("upper", [0, 0, 0, 0]),
            "size": e.get("size", 0),
            "transfer_id": tid,
            "type": e.get("type", "fmap"),
            "related_ifmap": e.get("related_ifmap", []),
            "layer_name": e.get("layer_name", ""),
        })
    return out


def setir_to_gemini(setir_obj: dict, buffersize: int = 8 * 1024 * 1024) -> dict:
    """将 SET-IR 整棵 IR 转为 Gemini Scheduler IR。"""
    metadata = setir_obj.get("metadata", {})
    xlen = metadata.get("xlen", 1)
    ylen = metadata.get("ylen", 1)
    top_batch_cut = metadata.get("top_batch_cut", 1)

    core_ids = []
    for k in setir_obj:
        if k in ("DDR", "buffer_data", "metadata"):
            continue
        try:
            cid = int(k)
            if cid >= 0 and isinstance(setir_obj[k], list):
                core_ids.append(cid)
        except ValueError:
            continue
    core_ids.sort()
    old_to_new_core = {cid: i for i, cid in enumerate(core_ids)}

    cores_data = {k: v for k, v in setir_obj.items() if k not in ("DDR", "buffer_data", "metadata") and isinstance(v, list)}
    buffer_data = setir_obj.get("buffer_data", {})
    tid_to_producer = build_tid_to_producer(cores_data, core_ids)
    consumer_map = build_tid_core_to_consumer_workload(cores_data, core_ids)

    gemini = {
        "xlen": xlen,
        "ylen": ylen,
        "top_batch_cut": top_batch_cut,
        "buffersize": buffersize,
        "-1": {
            "in": convert_dram_in(setir_obj.get("DDR", {}).get("in"), old_to_new_core, tid_to_producer),
            "out": convert_dram_out(setir_obj.get("DDR", {}).get("out"), old_to_new_core, consumer_map),
        },
    }

    for i, old_cid in enumerate(core_ids):
        wl_list = setir_obj.get(str(old_cid), [])
        if not isinstance(wl_list, list):
            continue
        gemini[str(i)] = [
            convert_workload(wl, buffer_data, old_to_new_core, consumer_map)
            for wl in wl_list
        ]

    return gemini


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert SET-IR JSON IR to Gemini Scheduler IR format.")
    ap.add_argument("input", type=Path, help="SET-IR output JSON path")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output Gemini-format JSON path (default: stdout)")
    ap.add_argument("--buffersize", type=int, default=8 * 1024 * 1024, help="L2 buffer size in bytes (default: 8MB)")
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        setir_obj = json.load(f)

    gemini_obj = setir_to_gemini(setir_obj, buffersize=args.buffersize)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(gemini_obj, f, indent=3, ensure_ascii=False)
        print(f"Wrote Gemini-format IR to {args.output}", file=sys.stderr)
    else:
        json.dump(gemini_obj, sys.stdout, indent=3, ensure_ascii=False)


if __name__ == "__main__":
    main()
