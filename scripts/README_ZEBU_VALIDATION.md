# ZeBu 与仿真器对比验证

使用 `third_party/Gemini-Compiler-IR` 中的 ZeBu 单核 trace 结果与本仿真器的周期数进行对比，用于验证仿真器准确度。

## 1. 解析 ZeBu trace：`parse_zebu_trace.py`

解析 ZeBu 输出的 `*_sim.txt`，得到总周期数（所有指令的 `max(end)`）及可选的按指令类型、按 (slyr, wkl) 的统计。

```bash
# 仅输出总周期与指令数
python3 scripts/parse_zebu_trace.py third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt

# 附带按指令类型、按 (slyr,wkl) 的汇总
python3 scripts/parse_zebu_trace.py third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt --summary

# 输出 JSON（便于脚本消费）
python3 scripts/parse_zebu_trace.py third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt --json --summary
```

## 2. 对比 ZeBu 与仿真器：`compare_zebu_simulator.py`

比较 ZeBu trace 的总周期与仿真器的总周期，并给出相对误差。

**方式 A：手动给出仿真器周期**

```bash
python3 scripts/compare_zebu_simulator.py \
  --zebu-trace third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt \
  --sim-cycles 80000000
```

**方式 B：从仿真器 stdout 中解析周期**

先运行仿真器并保存输出，再传入日志文件：

```bash
./npu_sim -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json -c config.json 2>&1 | tee sim_out.txt
python3 scripts/compare_zebu_simulator.py \
  --zebu-trace third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt \
  --sim-output sim_out.txt
```

**方式 C：指定 IR，由脚本自动运行仿真器**

```bash
python3 scripts/compare_zebu_simulator.py \
  --zebu-trace third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt \
  --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json \
  --config config.json \
  --simulator-exe ./npu_sim
```

**方式 D：从仿真器 workload 汇总中取总周期**

若已用 `-t trace` 跑过仿真器，可用 `trace/workload_summary.csv` 中的最大 `end_cycle` 作为仿真器总周期：

```bash
python3 scripts/compare_zebu_simulator.py \
  --zebu-trace third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt \
  --sim-trace trace/workload_summary.csv
```

## 输入对应关系

- ZeBu trace 与 Scheduler IR 需来自同一配置。命名对应关系示例：
  - ZeBu trace: `zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt`
  - Scheduler IR: `scheduler_output/int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json`
- 当前仅支持 ZeBu **单核**（c1）结果与仿真器单核/同配置对比。

## 单核与 NoC / 如何更贴近 ZeBu

- **单核时访存是否走 NoC？** 默认配置（`configs/default_config.json`）中 `topology.dram_controllers` 为空，表示 DRAM 直连核、**不经过 NoC**（legacy 模式）。因此单核下所有 DRAM 请求都直接进入 `dram->send_request()`，不会注入 NoC，单核没有 NoC 延迟。
- **为何全后端（BookSim2+Ramulator2）周期仍高于 ZeBu？** 与 ZeBu 的偏差主要来自 **Ramulator2** 的 DDR 时序、bank 冲突、请求排队等建模比 ZeBu 侧内存更保守，以及 workload 级与指令级抽象差异，而非 NoC。
- **如何让单核 ZeBu 对比更准？** 若希望周期更接近 ZeBu（例如误差在 ±5% 内），单核校验时建议使用 **simple 后端** 配置，例如：
  ```bash
  python3 scripts/run_zebu_and_bottleneck_experiments.py --config experiments/simple_single_core_config.json --run-dir experiments/runs/zebu_validation
  ```
  或对单条对比使用 `--config experiments/simple_single_core_config.json`。多核与设计空间探索仍建议使用 `configs/default_config.json`（BookSim2+Ramulator2）以保留 NoC/DRAM 效应。
