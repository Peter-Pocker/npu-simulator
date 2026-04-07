# 从 Gemini-Compiler-IR 的 ZeBu 输出与 Scheduler IR 推断 test chip 硬件配置

## 结论概览

**可以推断出的配置**（来自 Scheduler IR + 文件名）：

| 配置项 | 来源 | 说明 |
|--------|------|------|
| 核阵列拓扑 `num_cores_x` × `num_cores_y` | IR 顶层 `xlen`, `ylen` | ZeBu 数据均为单核，即 1×1 |
| 每核 L2/SRAM 大小 | IR 顶层 `buffersize`（字节） | 如 8388608 = 8MB |
| 调度时假设的 DRAM 带宽 | 文件名 `*_bw{N}_*` | 如 `bw16` 表示 16 GB/s |
| 批大小 / 核数（调度约束） | 文件名 `*_b{N}_*`, `*_c{N}_*` | 如 b1_c1 = batch=1, 1 core |

**可从 backend log 进一步推断的配置**（`infer_zebu_hw_from_log.py`）：

| 配置项 | 来源 | 说明 |
|--------|------|------|
| 核时钟 25 MHz | Memory Delays 中 bram 40 ns | 1/40ns = 25 MHz |
| DRAM 16.95 MHz、2GB、1024-bit | dram3 / dpram_zebu_2GBx1024 | 用于备注或 Ramulator 配置 |
| **SRAM 读写带宽** | ZMEM / Memory Delays 中 L2 所用 spram 的 **Data width** | L2 使用 `spram_zebu_1024x64`，**64 bit**、1 port、40 ns/cycle → 每周期每端口 **8 bytes**；脚本解析到 `W: 64` 后得到 `read/write_bandwidth_bytes_per_cycle = 8`。若 RTL 对外暴露更宽或多端口，需按实际填。 |

**无法直接从 IR 或 ZeBu trace 得到的配置**（需文档或反推）：

- **MAC 数量、vector units、时钟频率**：Scheduler IR 的 `tile_info` 中有 `single_tile_time_pred` 和 tiling 维度，但没有暴露 PE 规模；ZeBu trace 只有指令类型和 `[start,end]` 周期。
- **NoC 参数**：flit 大小、跳数延迟、拓扑等未在 IR 中。
- **DRAM 控制器细节**：channel 数、Ramulator 标准等未在 IR 中；文件名仅给出带宽约束。

因此，**只能部分还原 test chip 的硬件配置**；若要与 ZeBu 周期对齐，需依赖仓库中已有的“与 ZeBu 对比用”的默认配置（如 `experiments/simple_single_core_config.json`），或从 Gemini/ZeBu 文档/backend log 中查实际 PE 与内存参数。

---

## 1. Scheduler IR 中与硬件相关的字段

- **`xlen`, `ylen`**：核网格尺寸，对应仿真器 `num_cores_x`, `num_cores_y`。
- **`buffersize`**：每核 L2 buffer 大小（字节），对应仿真器 `sram.size_kb`（需除以 1024）。
- **`top_batch_cut`**：根节点 batch 分组，与调度约束有关，非直接硬件参数。
- **每个 workload 的 `tile_info`**：包含 `single_tile_time_pred`、`ofmap_upper/lower`、`ifmap_upper/lower` 等，用于计算量和时序估计，但**不直接给出 MAC 数或频率**。
- **`ring_buffer_info`**：L2 环 buffer 区间，与 `buffersize` 一致（如 `[[0, 8388608]]`）。

---

## 2. 文件名中的调度约束（Scheduler IR 命名）

据 `scheduler_output/Scheduler_IR_Format.md`：

- 例：`int8_resnet50.sim_quantized_b4_c1_bw16_stschedule.json`
  - **b4**：batch size = 4  
  - **c1**：1 个 core  
  - **bw16**：调度时假设 DRAM 带宽 16 GB/s  

这些是**调度时的约束**，可视为 test chip 的带宽与规模假设，但不能反推 NoC/DRAM 控制器内部参数。

---

## 3. ZeBu trace 中与硬件配置的关系

ZeBu trace（`zebu_trace_output/*_sim.txt`）每行格式类似：

```text
[inst_type, {slyr, wkl, tile, ...}, {'inst_idx': N, 'time': [start, end]}, ...]
```

- 仅有：**指令类型**（如 `pe_conv`, `lda_mov`）、**inst_idx**、**time [start, end]**、以及 slyr/wkl/tile 等映射信息。
- **没有**：MAC 数、SRAM 带宽、NoC 延迟、DRAM 通道数等硬件参数。

因此，从 ZeBu trace **无法直接读出** test chip 的硬件配置；只能通过“同一 IR + 相同假设配置”下对比 ZeBu 周期与仿真器周期来**校验**仿真器时序模型。

---

## 4. 使用方法：脚本自动推断

项目提供脚本 `scripts/infer_zebu_hw_config.py`，从 Scheduler IR 和文件名提取可推断项，并生成与 npu_sim 兼容的 config 片段或完整 JSON（不可推断的项用默认值）：

```bash
# 单文件：打印推断结果 + 完整 config（含默认值）
python3 scripts/infer_zebu_hw_config.py --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json

# 指定目录：用该目录下第一个 *_stschedule.json
python3 scripts/infer_zebu_hw_config.py --ir-dir third_party/Gemini-Compiler-IR/scheduler_output --out configs/chips/zebu_inferred.json

# 仅打印可推断字段（不填默认值）
python3 scripts/infer_zebu_hw_config.py --ir <path> --no-defaults
```

生成的 JSON 中，**从 IR/文件名推断的**是核数、SRAM 大小、以及 `_inferred_from_filename` 中的 batch/核数/dram_bw_gbps；**MAC、vector_units、NoC、DRAM 细节**仍为占位默认值，需根据 ZeBu 或 Gemini 文档另行填写。

---

## 5. 从 backend log 完整推断并生成 test chip 配置

**backend_default_globalLog.log** 中包含可解析的硬件信息：

- **Design size**：BRAM 2251、DSP 1110（整芯片资源，MAC 数需结合 RTL 或沿用 256 的抽象）。
- **Memory Delays**：BRAM 延迟 40 ns → 核时钟 **25 MHz**；DRAM 接口 **16.95 MHz**，2GB、1024-bit。
- **L2**：逻辑上以 Scheduler IR 的 `buffersize`（8MB）为准；log 中 L2 物理实现为多块 spram（约 2MB 量级），配置中仍用 8MB 以与调度假设一致。
- **SRAM 读写带宽**：log 的 ZMEM 表与 Memory Delays 中，L2 使用的 `spram_zebu_1024x64` 为 **Data width = 64 bit**、单端口、40 ns 周期，即每周期每端口 8 字节。脚本从 “W: 64” 解析出 `sram_data_width_bits=64`，生成配置时设 `read_bandwidth_bytes_per_cycle` 与 `write_bandwidth_bytes_per_cycle` 各为 **8**。若实际 RTL 对 core 暴露更宽总线或多口并行，需在生成后的 JSON 中手工改大。

使用脚本 **`scripts/infer_zebu_hw_from_log.py`** 可从 log + 任意一份 `*_stschedule.json` 生成与 npu_sim 兼容的完整配置并写入 **`configs/chips/zebu_testchip.json`**：

```bash
python3 scripts/infer_zebu_hw_from_log.py \
  --log third_party/Gemini-Compiler-IR/ZeBu_files/backend_default_globalLog.log \
  --ir third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json \
  -o configs/chips/zebu_testchip.json
```

生成后的配置可直接用于跑仿真、与 ZeBu trace 对比，例如：

```bash
./build/npu_sim -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json -c configs/chips/zebu_testchip.json
```

## 6. ZeBu 实际硬件与 backend log

Gemini-Compiler-IR 的 README 提到：

- ZeBu 为**单核**缩小版加速器（单板已超 50% LUT）。
- 更详细的硬件综合信息见：`third_party/Gemini-Compiler-IR/ZeBu_files/backend_default_globalLog.log`。

该 log 经上述脚本解析后，可得到核频、DRAM 频率、BRAM/DSP 规模及与 Scheduler IR 一致的核数、L2 大小，并生成 **zebu_testchip.json**。MAC 数、vector units 等仍采用与现有 ZeBu 对比实验一致的默认值（256/64），以保持周期可比性；若需与 RTL 完全一致，可再根据文档手工微调。
