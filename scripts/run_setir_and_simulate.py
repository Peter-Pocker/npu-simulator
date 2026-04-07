#!/usr/bin/env python3
"""
解析硬件配置文件，按传入参数选择模型，运行 SET-IR 得到预测 latency 与 IR，
再用 SET-IR 生成的 IR 和同一硬件配置运行 npu_sim，输出 SET-IR 预测与仿真器周期对比。

依赖：
  - SET-IR 子模块。若 SET-IR 不从 stdin 读参数，可用 --patch-setir 让本脚本自动修补 main.cpp（仅改参数输入方式）。
  - SET-IR 要求 mesh 的 ylen>=2（DRAM 布局），故 num_cores_x/num_cores_y 至少有一个>=2，脚本会自动保证 y>=2。
  - 已编译的 build/npu_sim 与硬件 config 的 JSON 格式与 npu_sim 一致。

用法:
  python3 scripts/run_setir_and_simulate.py -c configs/chips/two_core_resnet.json -m resnet50
  python3 scripts/run_setir_and_simulate.py -c configs/chips/two_core_resnet.json -m resnet50 -b 2 --out-dir out/setir_sim
  python3 scripts/run_setir_and_simulate.py -c configs/chips/two_core_resnet.json -m resnet50 -t   # 启用仿真 state trace，输出到 <out-dir>/trace
  python3 scripts/run_setir_and_simulate.py -c configs/chips/two_core_resnet.json -m resnet50 -t --trace-dir out/my_trace
  python3 scripts/run_setir_and_simulate.py -c my_config.json -m resnet101 --no-sim   # 只跑 SET-IR，不跑仿真器
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path


# SET-IR 网络名 -> stdin 的 net 编号（与 SET-IR README 一致）
MODEL_TO_NET = {
    "darknet19": 0,
    "vgg19": 1,
    "resnet50": 2,
    "googlenet": 3,
    "resnet101": 4,
    "densenet": 5,
    "inception_resnet_v1": 6,
    "ires": 6,
    "gnmt": 7,
    "lstm": 8,
    "zfnet": 9,
    "transformer": 10,
    "trans_cell": 11,
    "pnasnet": 12,
    "ires_block": 13,
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_hw_config(config_path: Path) -> dict:
    """加载硬件配置 JSON，返回 dict。"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_setir_params_from_config(config: dict) -> tuple[int, int, int, int]:
    """
    从 npu_sim 格式的 config 中提取 SET-IR 需要的 x, y, batch, bw。
    返回 (x, y, batch, bw)。
    """
    x = int(config.get("num_cores_x", 2))
    y = int(config.get("num_cores_y", 2))
    batch = 1
    if "gemini" in config and isinstance(config["gemini"], dict):
        batch = int(config["gemini"].get("batch_size", 1))
    bw = 16
    if "gemini" in config and isinstance(config["gemini"], dict):
        bw = int(config["gemini"].get("noc_bw", 16))
    # 若顶层有 noc 相关字段也可考虑
    if x < 1:
        x = 1
    if y < 1:
        y = 1
    # SET-IR 要求 ylen >= 2
    if y < 2 and x >= 2:
        x, y = y, x
    if y < 2:
        y = 2
    return x, y, batch, bw


def ensure_setir(root: Path, patch_stdin: bool = False) -> Path:
    """确保 SET-IR 存在并已构建；若 patch_stdin 为 True 则修补 main.cpp 从 stdin 读参数。返回 SET-IR 目录。"""
    setir = root / "SET-IR"
    if not setir.is_dir():
        print("[run_setir_sim] SET-IR 目录不存在，尝试初始化子模块...")
        subprocess.run(
            ["git", "submodule", "update", "--init", "SET-IR"],
            cwd=root,
            check=True,
        )
    if not setir.is_dir():
        raise SystemExit("SET-IR 未找到。请添加子模块: git submodule add https://github.com/EliminateSpace/SET-IR.git SET-IR")
    did_patch = False
    main_cpp = setir / "src" / "main.cpp"
    if patch_stdin and main_cpp.is_file():
        text = main_cpp.read_text(encoding="utf-8", errors="replace")
        if "// mm,yy 等由 stdin" in text:
            pass  # 已补过
        elif "std::cin>>mm>>nn>>bb>>xx>>yy>>ss>>rr>>ff>>bw" in text and "mm=0; nn=2;" in text:
            old = """\t/*
\t//std::ifstream in("params.in");
\tif(!(std::cin>>mm>>nn>>bb>>xx>>yy>>ss>>rr>>ff>>bw)){
\t\tassert(false);
\t\tstd::cout<<"Warning: No input file detected, use default settings:"<<std::endl;
\t\tmm=0;nn=2;bb=64;xx=8;yy=8;ss=4;rr=100;ff=1;bw=24;
\t\tstd::cout<<mm<<' '<<nn<<' '<<bb<<' '<<xx<<' '<<yy<<' '<<ss<<' '<<rr<<' '<<ff<<' '<<bw<<std::endl;
\t}else{
\t\t//in.close();
\t}*/"""
            new = """\t//std::ifstream in("params.in");
\tif(!(std::cin>>mm>>nn>>bb>>xx>>yy>>ss>>rr>>ff>>bw)){
\t\tstd::cout<<"Warning: No input file detected, use default settings:"<<std::endl;
\t\tmm=0;nn=2;bb=64;xx=8;yy=8;ss=4;rr=100;ff=1;bw=24;
\t\tstd::cout<<mm<<' '<<nn<<' '<<bb<<' '<<xx<<' '<<yy<<' '<<ss<<' '<<rr<<' '<<ff<<' '<<bw<<std::endl;
\t}
\tunicast_only=0;"""
            if old in text:
                text = text.replace(old, new)
                text = text.replace(
                    "\tmm=0; nn=2; bb=4; xx=4; yy=4; ss=2; rr=50; ff=1; bw=24; unicast_only=0;",
                    "\t// mm,yy 等由 stdin 或默认值设置: mm=0; nn=2; bb=4; xx=4; yy=4; ss=2; rr=50; ff=1; bw=24; unicast_only=0;",
                    1,
                )
                main_cpp.write_text(text, encoding="utf-8")
                did_patch = True
                print("[run_setir_sim] 已修补 SET-IR main.cpp 从 stdin 读取参数。")
            else:
                print("[run_setir_sim] 警告: SET-IR main.cpp 格式与预期不符，未自动修补；若 mesh 与配置不符请手动改 SET-IR 从 stdin 读参。")
    exe = setir / "build" / "stschedule"
    if not exe.is_file() or did_patch:
        if did_patch:
            print("[run_setir_sim] 重新构建 SET-IR...")
        else:
            print("[run_setir_sim] 构建 SET-IR...")
        subprocess.run(["make"], cwd=setir, check=True)
    return setir


def run_setir(
    setir_dir: Path,
    *,
    model: str,
    x: int,
    y: int,
    batch: int,
    bw: int,
    out_ir_path: Path,
    timeout: int = 300,
) -> tuple[Path, int | None]:
    """
    运行 SET-IR：stdin 传入 dataflow net batch x y stride round cost bw unicast_only；
    将生成的 IR 复制到 out_ir_path；
    从 stdout 解析最后一次出现的 T:(数字) 作为 SET-IR 预测的 latency（周期）。
    返回 (生成的 IR 路径, 解析到的 latency 或 None)。
    """
    exe = setir_dir / "build" / "stschedule"
    net = MODEL_TO_NET.get(model)
    if net is None:
        raise SystemExit(f"不支持的模型: {model}. 支持: {list(MODEL_TO_NET.keys())}")
    # dataflow=0 (Polar/Simba), stride=1, round=1, cost=0 (delay), unicast_only=0
    stdin_str = f"0 {net} {batch} {x} {y} 1 1 0 {bw} 0\n"
    out_base = f"{model}_{x}x{y}"
    proc = subprocess.run(
        [str(exe), out_base],
        input=stdin_str.encode("utf-8"),
        cwd=setir_dir,
        capture_output=True,
        timeout=timeout,
    )
    stderr = proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(stderr)
        sys.stderr.write(stdout)
        raise SystemExit(f"SET-IR 退出码 {proc.returncode}")
    # 解析 SET-IR 输出的 latency：SchCost 格式为 "E:..., T:12345, Cost:..."
    setir_latency = None
    for m in re.finditer(r"T:\s*(\d+)", stdout):
        setir_latency = int(m.group(1))
    # 取最后一次（通常是 SA-LS 结果）
    ir_result = setir_dir / "results" / "json" / f"{out_base}_SA-LS.json"
    if not ir_result.is_file():
        for suffix in ("_SA-LS.json", "_LP-SA.json", "_LS-opt-SA.json"):
            alt = setir_dir / "results" / "json" / f"{out_base}{suffix}"
            if alt.is_file():
                ir_result = alt
                break
    if not ir_result.is_file():
        raise SystemExit(f"SET-IR 未生成预期 IR: {ir_result}")
    out_ir_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ir_result, out_ir_path)
    return out_ir_path, setir_latency


def convert_setir_to_gemini_ir(
    root: Path,
    setir_ir_path: Path,
    gemini_ir_path: Path,
    buffersize: int = 8 * 1024 * 1024,
) -> None:
    """调用 scripts/setir_to_gemini_ir.py 将 SET-IR 的 IR 转为 Gemini Scheduler 格式。"""
    converter = root / "scripts" / "setir_to_gemini_ir.py"
    if not converter.is_file():
        raise SystemExit(f"转换脚本不存在: {converter}")
    subprocess.run(
        [
            sys.executable,
            str(converter),
            str(setir_ir_path),
            "-o",
            str(gemini_ir_path),
            "--buffersize",
            str(buffersize),
        ],
        cwd=root,
        check=True,
    )


def run_simulator(
    root: Path,
    config_path: Path,
    ir_path: Path,
    timeout: int = 600,
    trace_dir: Path | None = None,
    stream_output: bool = True,
) -> tuple[int | None, str]:
    """运行 npu_sim，返回 (Total cycles, stdout)。若 trace_dir 非空则传入 --trace <dir> 启用状态 trace 输出。
    stream_output=True 时边运行边把仿真输出打印到终端，便于观察是否在推进或死锁。"""
    exe = root / "build" / "npu_sim"
    if not exe.is_file():
        raise SystemExit("未找到 build/npu_sim，请先编译。")
    cmd = [str(exe), "--config", str(config_path), "--ir", str(ir_path)]
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--trace", str(trace_dir)])
    if stream_output:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        read_done = threading.Event()

        def read_stdout():
            try:
                for line in proc.stdout:
                    lines.append(line)
                    sys.stdout.write(line)
                    sys.stdout.flush()
            finally:
                read_done.set()

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
            if proc.returncode is None:
                proc.kill()
                proc.wait()
            read_done.wait(timeout=2)
            stdout = "".join(lines)
            raise SystemExit(f"npu_sim 运行超过 {timeout} 秒，已终止。可能死锁或负载过大。")
        read_done.wait(timeout=2)
        stdout = "".join(lines)
        if proc.returncode != 0:
            raise SystemExit(f"npu_sim 退出码 {proc.returncode}")
    else:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            sys.stderr.write(stderr)
            sys.stderr.write(stdout)
            raise SystemExit(f"npu_sim 退出码 {proc.returncode}")
    m = re.search(r"Total cycles:\s*(\d+)", stdout)
    total_cycles = int(m.group(1)) if m else None
    return total_cycles, stdout


def main():
    ap = argparse.ArgumentParser(
        description="解析硬件配置 → 选模型 → 运行 SET-IR → 用生成 IR + 配置运行 npu_sim，输出 SET-IR 预测与仿真周期。",
    )
    ap.add_argument("-c", "--config", type=Path, required=True, help="硬件配置文件 (npu_sim 格式 JSON)")
    ap.add_argument("-m", "--model", type=str, default="resnet50", help="模型名，如 resnet50, resnet101, vgg19 (见 SET-IR 支持列表)")
    ap.add_argument("-b", "--batch", type=int, default=None, help="batch 大小，不传则从 config 的 gemini.batch_size 或 1")
    ap.add_argument("--out-dir", type=Path, default=None, help="输出目录：SET-IR 生成的 IR 会复制到此；默认 <project>/out/setir_<model>_<x>x<y>")
    ap.add_argument("--no-sim", action="store_true", help="只运行 SET-IR，不运行仿真器")
    ap.add_argument("-t", "--trace", action="store_true", help="启用仿真器 state trace 输出（每核状态、workload 等）")
    ap.add_argument("--trace-dir", type=Path, default=None, help="trace 输出目录；未指定且启用 -t 时使用 <out-dir>/trace")
    ap.add_argument("--patch-setir", action="store_true", help="自动修补 SET-IR main.cpp 从 stdin 读取参数（本仓库子模块默认硬编码参数）")
    ap.add_argument("--setir-timeout", type=int, default=300, help="SET-IR 超时秒数")
    ap.add_argument("--sim-timeout", type=int, default=600, help="npu_sim 超时秒数")
    ap.add_argument("--quiet", action="store_true", help="仿真器输出不实时打印到终端（仅最后解析 Total cycles）")
    args = ap.parse_args()

    root = project_root()
    config_path = args.config if args.config.is_absolute() else (root / args.config)
    if not config_path.is_file():
        raise SystemExit(f"配置文件不存在: {config_path}")

    config = load_hw_config(config_path)
    x, y, batch, bw = get_setir_params_from_config(config)
    if args.batch is not None:
        batch = args.batch
    model = args.model.lower().replace("-", "_")

    if args.out_dir is None:
        args.out_dir = root / "out" / f"setir_{model}_{x}x{y}"
    out_dir = args.out_dir if args.out_dir.is_absolute() else (root / args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_ir_path = out_dir / f"{model}_{x}x{y}_setir_ir.json"

    print("[run_setir_sim] 硬件配置:", config_path)
    print("[run_setir_sim] 模型:", model, "batch:", batch, "mesh:", f"{x}x{y}", "NoC bw:", bw)
    print("[run_setir_sim] 输出目录:", out_dir)
    print()

    setir_dir = ensure_setir(root, patch_stdin=args.patch_setir)
    print("[run_setir_sim] 运行 SET-IR...")
    ir_path, setir_latency = run_setir(
        setir_dir,
        model=model,
        x=x,
        y=y,
        batch=batch,
        bw=bw,
        out_ir_path=out_ir_path,
        timeout=args.setir_timeout,
    )
    print(f"[run_setir_sim] SET-IR IR 已写入: {ir_path}")
    if setir_latency is not None:
        print(f"[run_setir_sim] SET-IR 预测 latency (周期): {setir_latency}")
    else:
        print("[run_setir_sim] 未能从 SET-IR 输出中解析 T: (latency)")
    print()

    # 自动转换为 Gemini Scheduler IR 格式，供仿真器使用
    gemini_ir_path = out_dir / f"{model}_{x}x{y}_gemini_ir.json"
    buffersize = 8 * 1024 * 1024
    if "sram_per_core_kb" in config:
        buffersize = int(config["sram_per_core_kb"]) * 1024
    elif isinstance(config.get("sram"), dict) and "size_kb" in config["sram"]:
        buffersize = int(config["sram"]["size_kb"]) * 1024
    elif "gemini" in config and isinstance(config.get("gemini"), dict) and "sram_per_core_kb" in config["gemini"]:
        buffersize = int(config["gemini"]["sram_per_core_kb"]) * 1024
    print("[run_setir_sim] 转换 SET-IR IR 为 Gemini 格式...")
    convert_setir_to_gemini_ir(root, ir_path, gemini_ir_path, buffersize=buffersize)
    print(f"[run_setir_sim] Gemini 格式 IR 已写入: {gemini_ir_path}")
    ir_path = gemini_ir_path
    print()

    if args.no_sim:
        print("[run_setir_sim] --no-sim：跳过仿真器。")
        return

    trace_dir: Path | None = None
    if args.trace:
        trace_dir = args.trace_dir if args.trace_dir is not None else (out_dir / "trace")
        if not trace_dir.is_absolute():
            trace_dir = root / trace_dir
        print(f"[run_setir_sim] 仿真 trace 输出目录: {trace_dir}")

    print("[run_setir_sim] 运行 npu_sim（输出将实时打印，可观察是否在推进或死锁）...")
    sim_cycles, sim_stdout = run_simulator(
        root,
        config_path,
        ir_path,
        timeout=args.sim_timeout,
        trace_dir=trace_dir,
        stream_output=not args.quiet,
    )
    if sim_cycles is not None:
        print(f"[run_setir_sim] 仿真器 Total cycles: {sim_cycles}")
    else:
        print("[run_setir_sim] 未能从仿真器输出中解析 Total cycles")
    if trace_dir is not None:
        print(f"[run_setir_sim] 仿真 trace 已写入: {trace_dir}")
    print()

    # 汇总
    print("======== 汇总 ========")
    print(f"  配置:     {config_path}")
    print(f"  模型:     {model}  batch={batch}  mesh={x}x{y}  noc_bw={bw}")
    print(f"  SET-IR IR: {ir_path}")
    if setir_latency is not None:
        print(f"  SET-IR 预测周期: {setir_latency}")
    if sim_cycles is not None:
        print(f"  npu_sim 周期:    {sim_cycles}")
    if trace_dir is not None:
        print(f"  trace 目录:      {trace_dir}")
    if setir_latency is not None and sim_cycles is not None and setir_latency > 0:
        diff_pct = 100.0 * (sim_cycles - setir_latency) / setir_latency
        print(f"  相对差异 (sim vs SET-IR): {diff_pct:+.2f}%")
    print("=====================")


if __name__ == "__main__":
    main()
