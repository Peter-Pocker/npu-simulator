# 用 ZeBu 数据佐证仿真器准确性：操作流程与论文呈现

## 一、怎样用 ZeBu 数据佐证仿真器准确性

### 1. 数据来源与对应关系

- **ZeBu trace**：来自 `third_party/Gemini-Compiler-IR/zebu_trace_output/`，为 Synopsys ZeBu 硬件仿真平台在**单核**配置下执行汇编指令后的 trace，每条指令带有 `inst_idx` 与 `time: [start, end]`。
- **仿真器输入**：与 ZeBu 使用**同一套**调度结果：`scheduler_output/*_stschedule.json`。同一份 IR 先经 Assembler 生成汇编在 ZeBu 上跑得到 trace，再直接作为本仿真器的输入。
- **可比指标**：在单核、同一 IR 下，**总执行周期**（ZeBu：所有指令的 `max(end)`；仿真器：`Total cycles`）应接近，用于佐证仿真器时序模型的准确性。

### 2. 操作步骤（推荐流程）

**步骤 1：获取 ZeBu 总周期（可选，仅看 ZeBu 时用）**

```bash
# 单条 trace
python3 scripts/parse_zebu_trace.py third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt
# 输出：Instructions: 10739, Total cycles: 82907529
```

**步骤 2：用同一份 IR 跑仿真器，记录 Total cycles**

对每个要对比的配置跑一次仿真器（需先编译 `npu_sim`），在 stdout 中记录 “Total cycles: N”：

```bash
# 示例：ResNet50 batch=64 单核
./npu_sim -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json -c config.json 2>&1 | tee sim_b64.log
# 在 sim_b64.log 中查找 "Total cycles: XXXXX"
```

或使用对比脚本自动跑仿真并对比（需可执行 `npu_sim`）：

```bash
python3 scripts/compare_zebu_simulator.py \
  --zebu-trace third_party/Gemini-Compiler-IR/zebu_trace_output/int8_resnet50.sim_quantized_b64_c1_bw16_sim.txt \
  --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b64_c1_bw16_stschedule.json \
  --config config.json
```

**步骤 3：批量生成论文用表格**

将各配置的仿真器周期填入 CSV（表头：`model,batch,sim_cycles`），例如 `sim_results.csv`：

```csv
model,batch,sim_cycles
resnet34,1,1900000
resnet34,4,4200000
resnet34,16,16000000
resnet50,1,2500000
resnet50,4,5400000
resnet50,16,20800000
resnet50,64,80000000
```

然后运行：

```bash
python3 scripts/batch_compare_zebu.py \
  --zebu-dir third_party/Gemini-Compiler-IR/zebu_trace_output \
  --sim-results sim_results.csv \
  --latex \
  --csv-out zebu_validation_table.csv
```

会得到：控制台汇总表、`zebu_validation_table.csv`、以及可粘贴到论文中的 **LaTeX 表格**（含 ZeBu 周期、仿真器周期、相对误差）。

**步骤 4：解读结论**

- 若多数配置下「仿真器 − ZeBu」相对误差在 **±5%** 内，可在论文中表述为“与 ZeBu 硬件仿真在总周期上吻合良好，误差在 5% 以内”。
- 若在 **±15%** 内，可表述为“与 ZeBu 趋势一致，存在一定偏差，可能来自核内/NoC/DRAM 建模粒度差异”。
- 若某配置偏差显著，可单独分析（如该配置下 DRAM/NoC 占比高，需检查带宽或延迟参数）。

### 3. 注意事项

- **单核**：当前 ZeBu 数据均为单核（c1），对比时仿真器也应用单核拓扑（与 stschedule 中 xlen×ylen=1×1 一致）。
- **时间单位**：ZeBu trace 的 `time` 与仿真器均为**周期**，无需换算；若 ZeBu 文档标明为 ns，需按时钟频率换算后再比。
- **配置一致**：对比时仿真器 config 中的 core/NoC/DRAM 参数应尽量与 ZeBu 所用硬件假设一致（如 MAC 数、带宽等），否则差异会包含配置差异。

---

## 二、在论文中如何呈现（文字、数据、图）

### 1. 放在哪一节

建议在**第五章「仿真器验证与设计空间探索」**中，在微基准（MB-1/MB-2）之后、真实负载瓶颈分析之前，增加一小节：**「与 ZeBu 硬件仿真的对比」**（或「基于硬件仿真平台的精度验证」）。这样结构为：先微基准正确性 → 再硬件仿真佐证周期精度 → 再真实负载瓶颈与 DSE。

### 2. 文字表述建议（可直接改写进论文）

- **动机**：为验证仿真器在真实调度 IR 下的周期预测能力，本文使用 Synopsys ZeBu 硬件仿真平台在单核配置下执行与仿真器相同的调度 IR 所生成的汇编，采集每条指令的执行时间区间；以 ZeBu 全程序总周期（所有指令结束时间的最大值）为基准，与仿真器输出的总周期进行对比。
- **方法**：对比在相同模型、相同批大小、相同单核拓扑下进行；ZeBu trace 来自 Gemini-Compiler-IR 仓库提供的 ResNet34/ResNet50、batch=1/4/16/64 的预跑结果；仿真器以对应 stschedule.json 为输入，配置与 ZeBu 硬件假设一致。
- **结论**：在 X 个配置下，仿真器与 ZeBu 的总周期相对误差均小于 Y%，表明本仿真器在单核、调度一致的条件下能够较好复现硬件仿真得到的执行周期，可用于后续瓶颈分析与设计空间探索。

### 3. 数据呈现：表格（推荐）

在新增小节中给出**一张表**，列出：模型、批大小、ZeBu 总周期、仿真器总周期、相对误差（%）。

- 表题示例：**「表 X-X 与 ZeBu 硬件仿真的总周期对比」**
- 表注可写：ZeBu 为单核；相对误差 = (仿真器周期 − ZeBu 周期) / ZeBu 周期 × 100%。

使用 `scripts/batch_compare_zebu.py --sim-results sim_results.csv --latex` 生成的 LaTeX 可直接粘贴进论文；若使用 `booktabs`，需在导言区加 `\usepackage{booktabs}`。

### 4. 图形呈现（可选）

- **柱状图**：横轴为配置（如 ResNet34-B1, ResNet34-B4, …, ResNet50-B64），纵轴为周期；每组两根柱（ZeBu / 仿真器），可直观看出是否接近。  
- **散点图**：横轴 ZeBu 周期、纵轴仿真器周期；每个点一个配置，理想情况落在 y=x 附近；可在图中标出 ±5% 或 ±10% 误差带，增强“准确性”表述。

图题示例：**「与 ZeBu 硬件仿真的总周期对比」** 或 **「仿真器周期与 ZeBu 周期一致性」**。

### 5. 小结句（放在该小节末尾）

- 例如：“上述结果表明，在单核、相同调度 IR 的前提下，本仿真器的总周期预测与 ZeBu 硬件仿真结果一致，可作为后续多核扩展与设计空间探索的可信时序基础。”

---

## 三、脚本与文件速查

| 用途           | 脚本/文件 |
|----------------|-----------|
| 解析单条 ZeBu trace，得到总周期 | `scripts/parse_zebu_trace.py <*_sim.txt>` |
| 单配置对比（ZeBu vs 仿真器）   | `scripts/compare_zebu_simulator.py --zebu-trace ... [--sim-cycles N \| --sim-output log \| --ir stschedule.json]` |
| 批量对比并生成表格与 LaTeX     | `scripts/batch_compare_zebu.py --zebu-dir ... [--sim-results sim_results.csv] --latex [--csv-out out.csv]` |
| 详细用法说明                   | `scripts/README_ZEBU_VALIDATION.md` |
