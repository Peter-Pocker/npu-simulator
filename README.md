# NPU Simulator

一个面向大规模多核 NPU（Neural Processing Unit）的周期级全系统仿真框架。支持从编译器 IR 到微架构行为的端到端仿真，涵盖计算核心、片上网络（NoC）和片外访存（DRAM）三大子系统。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    Gemini/SET 编译器 IR (JSON)                │
│                         ↓                                    │
│              ┌─────────────────────┐                        │
│              │  IR Parser (前端)    │  ← 可扩展：支持多种 IR    │
│              └─────────┬───────────┘                        │
│                        ↓                                    │
│   ┌──────────┐  ┌──────────┐       ┌──────────┐            │
│   │  Core 0  │  │  Core 1  │  ...  │  Core N  │            │
│   │ ┌──────┐ │  │ ┌──────┐ │       │ ┌──────┐ │            │
│   │ │ SRAM │ │  │ │ SRAM │ │       │ │ SRAM │ │            │
│   │ └──────┘ │  │ └──────┘ │       │ └──────┘ │            │
│   └────┬─────┘  └────┬─────┘       └────┬─────┘            │
│        └──────────────┼──────────────────┘                  │
│                       ↓                                     │
│            ┌─────────────────────┐                          │
│            │  NoC (NetworkInterface)  │ ← 可替换：SimpleNoC / BookSim2 │
│            └─────────┬───────────┘                          │
│                      ↓                                      │
│            ┌─────────────────────┐                          │
│            │  DRAM (MemoryInterface)  │ ← 可替换：SimpleDRAM / Ramulator2 │
│            └─────────────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```
npu-simulator/
├── CMakeLists.txt
├── include/npu_sim/
│   ├── types.h              # 基础类型与枚举
│   ├── config.h             # 配置结构体（JSON 可序列化）
│   ├── task.h               # 内部任务表示（Workload, BufferRequirement 等）
│   ├── packet.h             # NoC 数据包定义
│   ├── sram.h               # SRAM 模型
│   ├── core.h               # NPU 核心状态机
│   ├── ir_parser.h          # 抽象 IR 解析器接口
│   ├── gemini_parser.h      # Gemini/SET IR 解析器
│   ├── network_interface.h  # 抽象 NoC 接口
│   ├── memory_interface.h   # 抽象 DRAM 接口
│   └── simulator.h          # 仿真引擎
├── src/
│   ├── sram.cpp
│   ├── core.cpp
│   ├── gemini_parser.cpp
│   ├── simulator.cpp
│   └── main.cpp
├── configs/
│   └── default_config.json
├── tests/
│   └── test_ir.json
└── third_party/
    ├── nlohmann/            # JSON 解析库
    ├── ramulator2/          # DRAM 仿真器 (submodule)
    ├── booksim2/            # NoC 仿真器 (submodule)
    └── GEMINI-HPCA2024/     # Gemini 编译器 (submodule)
```

## 支持的算子类型


| 算子               | 计算模型    | 说明                                |
| ---------------- | ------- | --------------------------------- |
| `conv2d`         | MAC 密集型 | 标准卷积，MACs = B×H×W×weight_elements |
| `fc`             | MAC 密集型 | 全连接层，复用 conv2d 计算模型               |
| `pool`           | 向量型     | 池化层，ops = ofmap_volume            |
| `element_wise`   | 向量型     | 逐元素运算（add/relu 等）                 |
| `point_to_point` | 数据搬运型   | 数据重排/拷贝                           |


## 核心模型（Core State Machine）

每个核心独立维护一个五状态有限状态机：

```
IDLE → LOADING → COMPUTING → WRITEBACK → IDLE
                                ↓ (无需写回)
                              IDLE
```

- **IDLE**: 等待新工作负载
- **LOADING**: 从其他核心的 SRAM 或 DRAM 获取输入数据
- **COMPUTING**: 执行算子计算
- **WRITEBACK**: 将结果写回 DRAM
- **DONE**: 所有工作负载完成

## 核间数据流（Inter-core Dataflow）

每个核心拥有独立的 SRAM（Scratchpad Memory）。核间通信采用 **拉取模型（Pull-based）**：

1. 生产者核心完成计算 → 输出数据保留在本地 SRAM
2. 消费者核心发起 `READ_REQUEST` → 通过 NoC 到达生产者
3. 生产者从 SRAM 读取数据 → 发送 `READ_RESPONSE` 回消费者
4. 消费者接收数据 → 存入本地 SRAM → 开始计算

## 依赖与环境

- **编译器**: 支持 C++20 的编译器（`g++-12`/`clang++-15` 及以上）
- **构建工具**: CMake >= 3.16, Make
- **操作系统**: macOS / Linux

## 编译

项目提供了一键构建脚本，会按顺序编译所有依赖和仿真器本体：

```bash
./build.sh
```

构建流程：

1. **编译 Ramulator2** — 在 `third_party/ramulator2/build/` 下生成 `libramulator.dylib`（macOS）或 `libramulator.so`（Linux）
2. **编译 BookSim2** — 在 `third_party/booksim2/src/` 下编译所有源文件并打包为 `libbooksim.a`
3. **编译 NPU Simulator** — 在 `build/` 下生成可执行文件 `npu_sim`

如需手动编译，可分步执行：

```bash
# 1. Ramulator2
cd third_party/ramulator2 && mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) ramulator
cd ../../..

# 2. BookSim2
cd third_party/booksim2/src && make -j$(nproc)
ar rcs libbooksim.a $(find . -name '*.o' ! -name 'main.o')
cd ../../..

# 3. NPU Simulator
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) npu_sim
```

## 运行

### 使用运行脚本

```bash
./run.sh -i <IR文件路径> [选项]
```

#### 命令行参数

| 参数                    | 说明                         | 默认值                             |
| --------------------- | -------------------------- | --------------------------------- |
| `-i, --ir <path>`     | 编译器 IR 文件路径（**必需**）        | —                                 |
| `-c, --config <path>` | 仿真器配置文件路径                   | `configs/default_config.json`     |
| `-t, --trace [dir]`   | 启用逐核状态追踪；可选指定输出目录，不指定则用带时间戳目录 | 关闭；开启时默认 `trace/trace_YYYYMMDD_HHMMSS/` |
| `-h, --help`          | 显示帮助信息                     | —                                 |

NoC/DRAM 后端由配置文件中的 `noc.backend`、`dram.backend` 决定（如 `booksim2`、`ramulator2`），无需单独指定运行模式。默认配置与 `configs/chips/*.json` 已使用 BookSim2 + Ramulator2。

#### 运行示例

```bash
# 使用默认配置 + 测试 IR
./run.sh -i tests/test_ir.json

# 使用默认配置 + Gemini 编译器 IR（ResNet50）
./run.sh -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json

# 指定芯片配置（如 Eyeriss、TPU）
./run.sh -c configs/chips/eyeriss.json -i tests/test_ir.json

# 开启状态追踪（输出到默认时间戳目录）
./run.sh -t -i tests/test_ir.json

# 指定配置 + 开启 trace + 指定 trace 输出目录
./run.sh -c configs/chips/simba.json -i my_ir.json -t my_trace_dir/
```

#### IR 切片（只仿真子结构）

若希望只仿真网络中的一部分（减少负载、加快仿真），可用 `scripts/slice_ir.py` 从完整 IR 中切出子图：

- **按层名**：`--layers "conv1,pool1,conv_2_0_a"`，会包含这些层及其所有依赖，无需手写 transfer。
- **按“前 N 个 workload”**：`--first N`，按「核顺序」取前 N 个 workload 并自动包含依赖，适合快速切出一块代表性子结构，不用查层名。
- **查看层名**：`--list-layers` 列出当前 IR 中所有 `layer_name`，便于写 `--layers`。

切出的 IR 可直接交给仿真器运行。

```bash
# 列出 IR 中所有层名
python3 scripts/slice_ir.py --ir full_ir.json --list-layers

# 切出前 50 个 workload（含依赖），输出到 sub_ir.json
python3 scripts/slice_ir.py --ir full_ir.json --first 50 -o sub_ir.json

# 按层名切出（多层级名逗号分隔）
python3 scripts/slice_ir.py --ir full_ir.json --layers "conv1,pool1,conv_2_0_a" -o sub_ir.json

# 用切片后的 IR 跑仿真
./run.sh -i sub_ir.json -t
```

### 直接使用可执行文件

```bash
./build/npu_sim --config <配置文件> --ir <IR文件> [--trace [目录]] [--log-dram]
```

- **--log-dram**：在控制台打印与 DRAM 的通信日志（每次请求/响应一行），用于验证仿真器与 Ramulator2 的交互是否正常。也可在配置 JSON 中设置 `"log_dram": true`。

### 测试仿真器与 Ramulator2 的 DRAM 交互

若需确认仿真器与 Ramulator2 的读写流程是否正常，可使用小规模切片 IR 并开启 DRAM 日志：

```bash
# 使用默认切片 IR（前 4 个 workload），生成 ir_ramulator2_dram_test_slice.json 并跑仿真，打印 [DRAM] / [Ramulator2DRAM] 日志
./scripts/run_ramulator2_dram_test.sh

# 指定输入 IR 与切片 workload 数量
./scripts/run_ramulator2_dram_test.sh resnet50_2x2_gemini_ir_slice.json 4
```

配置 `configs/ramulator2_dram_test.json` 已启用 `ramulator2` 后端与 `log_dram`；日志中会看到：仿真器发出的 `READ_REQ`/`WRITE_REQ`、Ramulator2 的 `accept READ/WRITE` 与 `READ complete`/`WRITE queued`、以及返回给各核的 `READ_RESP`/`WRITE_RESP`。

### 运行结果与输出文件

#### run.sh 与 npu_sim 的区别

| 方式 | 控制台输出 | 生成文件 |
|------|------------|----------|
| **run.sh**（不加 `-t`） | 仿真过程与统计信息打印到终端 | 无额外文件 |
| **run.sh -t**（开 trace） | 同上，并写入 trace 目录；**同时**在 trace 目录下生成实验元数据与后处理结果 | 见下方「trace 目录内容」 |
| **npu_sim**（不加 `--trace`） | 仅控制台输出 | 无 |
| **npu_sim --trace [dir]** | 同上 | 仅仿真器写入的 `state_trace.csv`、`workload_summary.csv`（无 run.sh 的元数据与 XLSX/甘特图） |

**结论**：若要做可复现实验并保留完整记录，建议用 **run.sh -t**（或 `-t <目录名>`），这样会得到：命令、配置、IR 副本、完整 stdout、CSV trace、XLSX 汇总和甘特图。

#### 控制台输出（run.sh 与 npu_sim 共有）

仿真过程中会打印：

1. **加载阶段**：SRAM 是否按 IR 调整、拓扑摘要、地址分配摘要、IR 的 core 数/workload 数/DRAM 读写数。
2. **运行进度**：每 10 万周期一行，如 `Cycle 100000, cores done: 0/1`。
3. **仿真统计（print_stats）**：
   - **Total cycles**：总仿真周期数（性能主指标）
   - **NoC packets**：经 NoC 的包数（多核或 DRAM 经 NoC 时才有）
   - **DRAM reads / writes**：逻辑读/写请求数
   - **DRAM sub-requests**：实际发往 Ramulator2 的 cache-line 级请求数
   - **DRAM addr allocated**：地址分配器分配的总字节数（MB）
   - **Per-Core Statistics**：每核的 Idle / Loading / Compute / Writeback / StallNoC 周期数、完成 workload 数、Peak SRAM(KB)

4. **Trace 提示**：若开启 trace，会打印 `[Trace] State transitions: ...`、`[Trace] Workload summary: ...`、`[Trace] Files written to ...`。
5. **Ramulator2 摘要**：若使用 ramulator2 后端，结束时可能打印其内部统计。

#### trace 目录内容（仅在使用 `-t` / `--trace` 时）

**用 run.sh -t 时**，trace 目录（默认 `trace/trace_YYYYMMDD_HHMMSS/` 或你指定的目录）包含：

| 文件 | 来源 | 含义 |
|------|------|------|
| **command.txt** | run.sh | 运行时间、本次 run.sh 命令、完整 npu_sim 命令行 |
| **config.json** | run.sh | 本次使用的配置文件的完整副本 |
| **ir_path.txt** | run.sh | 本次使用的 IR 文件路径（一行） |
| **ir.json** | run.sh | 本次使用的 IR 文件副本（便于复现） |
| **stdout.txt** | run.sh（tee） | 本次运行的完整标准输出（含加载、进度、统计、Trace 提示等） |
| **state_trace.csv** | npu_sim | 按时间排序的逐核状态迁移：cycle, core_id, old_state, new_state, workload_idx, layer_name, detail |
| **workload_summary.csv** | npu_sim | 每个 workload 的起止与阶段周期：core_id, workload_idx, layer_name, start/loading_done/compute_done/end_cycle, loading_cycles, **loading_dram_cycles**（等 DRAM 的周期）, **loading_core_cycles**（等其它核的周期）, compute_cycles, writeback_cycles, idle_before_cycles, data_sources |
| **trace_report.xlsx** | scripts/csv_to_xlsx.py | 将上述 CSV 合并为带格式的 Excel，便于查看与汇报 |
| **core_states.png** | scripts/plot_gantt.py | 按 core 与时间的甘特图。若同目录下存在带 `loading_dram_cycles`/`loading_core_cycles` 的 workload_summary.csv，则 LOADING 会拆成「等 DRAM」「等其它核」两段显示。 |

**直接用 npu_sim --trace &lt;dir&gt; 时**：只会生成 `state_trace.csv` 和 `workload_summary.csv`，**不会**生成 command.txt、config.json、ir_path.txt、ir.json、stdout.txt、trace_report.xlsx、core_states.png（这些由 run.sh 和后处理脚本完成）。

**增量写入**：仿真过程中每 10 万周期会刷新一次 trace 文件（覆盖写入）。因此即使因死锁超时退出或被中断（如 Ctrl+C），trace 目录中仍会保留截止到最近一次刷新的 trace，可用于画甘特图、分析卡住位置。

#### 如何看实验结果

- **总性能**：看控制台或 `stdout.txt` 里的 **Total cycles**；不同配置/IR 对比时以该值为准。
- **瓶颈大致在哪**：看 **Per-Core Statistics** 里各核的 Loading / Compute / Writeback / StallNoC 占比；Loading 高多为访存/NoC 瓶颈，Compute 高为算力主导，StallNoC 高为 NoC 拥塞。
- **逐 workload 行为**：用 **workload_summary.csv**（或 trace_report.xlsx 对应 sheet）看每段的 loading_cycles、**loading_dram_cycles**（因等 DRAM 的 loading）、**loading_core_cycles**（因等其它核的 loading）、compute_cycles、writeback_cycles、idle_before_cycles 和 data_sources。可据此区分瓶颈在访存还是核间依赖。若某 workload 无 DRAM 输出，writeback_cycles 为 0 属正常。
- **时间线可视化**：用 **core_states.png** 看各核在哪些周期处于 IDLE/Loading/COMPUTING/WRITEBACK，便于发现空闲或重叠。
- **复现实验**：用同一 trace 目录下的 **command.txt**、**config.json**、**ir.json** 和 **stdout.txt** 即可完整复现当次运行与输出。

### 使用 GEMINI 生成 IR 并运行仿真（一键脚本）

若希望**先由 GEMINI 根据配置生成 IR，再用该 IR 启动仿真器**，可使用 `scripts/gemini_run.py`。GEMINI 所需参数从仿真器配置文件中读取（含可选 `gemini` 块），**神经网络由命令行指定，不写在配置里**。

每次运行会创建带时间戳的记录文件夹 `trace/trace_YYYYMMDD_HHMMSS/`，内含：
- `gemini_log/`：GEMINI 的 stdout、stderr 及其他输出
- `*_ir.json`：生成的 IR
- `state_trace.csv`、`workload_summary.csv`：仿真 trace
- `simulator_stdout.txt`、`simulator_stderr.txt`：仿真器输出
- `run_info.md`：运行时间、GEMINI 输入、配置文件路径

```bash
# 依赖：已编译 GEMINI (cd third_party/GEMINI-HPCA2024 && make release) 与仿真器 (./build/npu_sim)
python3 scripts/gemini_run.py -c configs/gemini_run_example.json -n trans

# 仅生成 IR 不跑仿真（仍会创建记录文件夹）
python3 scripts/gemini_run.py -c configs/full_config.json -n resnet --skip-sim

# 指定记录文件夹的父目录（默认 trace）
python3 scripts/gemini_run.py -c configs/gemini_run_example.json -n trans -t my_experiments

# 查看支持的神经网络（索引与名称）
python3 scripts/gemini_run.py --list-networks
```

| 参数 | 说明 |
|------|------|
| `-c, --config` | 仿真器配置 JSON；可含顶层键 `gemini` 覆盖 GEMINI 专用参数（tech、package_type、io_type、dram_bw 等） |
| `-n, --network` | 网络：索引 0–17 或名称（如 `trans`、`resnet`、`bert`） |
| `-t, --trace-base DIR` | 记录文件夹的父目录（默认 `trace`），每次运行在其下创建 `trace_YYYYMMDD_HHMMSS/` |
| `--skip-sim` | 只跑 GEMINI 生成 IR，不启动仿真器 |

---

## 配置参数详解

仿真器通过 JSON 配置文件控制所有参数。以下是完整的参数说明。

### 全局参数


| 参数                  | 类型     | 默认值            | 说明                        |
| ------------------- | ------ | -------------- | ------------------------- |
| `num_cores_x`       | int    | `0`（自动）        | 核心阵列 X 维度。设为 0 时从 IR 自动推断 |
| `num_cores_y`       | int    | `0`（自动）        | 核心阵列 Y 维度。设为 0 时从 IR 自动推断 |
| `element_size_bits` | int    | `8`            | 数据元素位宽（INT8 = 8）          |
| `ir_path`           | string | `""`           | IR 文件路径（可被命令行 `-i` 覆盖）    |
| `output_path`       | string | `"sim_output"` | 仿真结果输出目录                  |


### Core（计算核心）

每个核心拥有独立的 MAC 阵列和向量处理单元。


| 参数                    | 类型    | 默认值      | 说明                      |
| --------------------- | ----- | -------- | ----------------------- |
| `core.mac_units`      | int   | `256`    | MAC 单元数量，决定卷积/全连接层的计算吞吐 |
| `core.vector_units`   | int   | `64`     | 向量处理单元数量，用于池化、逐元素运算等    |
| `core.clock_freq_mhz` | float | `1000.0` | 核心时钟频率（MHz）             |


**计算延迟公式**：

- MAC 密集型算子（conv2d, fc）: `cycles = total_MACs / mac_units`
- 向量型算子（pool, element_wise）: `cycles = total_ops / vector_units`

### SRAM（片上存储）

每个核心配备独立的 Scratchpad Memory。


| 参数                                     | 类型  | 默认值   | 说明            |
| -------------------------------------- | --- | ----- | ------------- |
| `sram.size_kb`                         | int | `512` | SRAM 容量（KB）   |
| `sram.read_bandwidth_bytes_per_cycle`  | int | `64`  | 每周期读带宽（Bytes） |
| `sram.write_bandwidth_bytes_per_cycle` | int | `64`  | 每周期写带宽（Bytes） |


### NI（Network Interface，网络接口）

控制核心与 NoC 之间的数据注入/弹出行为。


| 参数                        | 类型  | 默认值  | 说明               |
| ------------------------- | --- | ---- | ---------------- |
| `ni.max_outstanding_reqs` | int | `16` | 最大同时在途请求数        |
| `ni.injection_queue_size` | int | `8`  | 注入队列深度（核心 → NoC） |
| `ni.ejection_queue_size`  | int | `8`  | 弹出队列深度（NoC → 核心） |


### NoC（片上网络）

支持两种后端：内置简单模型和 BookSim2 周期精确仿真。


| 参数                          | 类型     | 默认值                           | 说明                               |
| --------------------------- | ------ | ----------------------------- | -------------------------------- |
| `noc.backend`               | string | `"simple"`                    | NoC 后端：`"simple"` 或 `"booksim2"` |
| `noc.flit_size_bytes`       | int    | `16`                          | Flit 大小（Bytes），影响数据包拆分           |
| `noc.hop_latency_cycles`    | int    | `1`                           | 每跳延迟（周期），仅 simple 模式             |
| `noc.router_latency_cycles` | int    | `1`                           | 路由器流水线延迟（周期），仅 simple 模式         |
| `noc.booksim2_config_path`  | string | `"configs/booksim2_mesh.cfg"` | BookSim2 配置文件路径                  |
| `noc.injection_queue_depth` | int    | `16`                          | NoC 注入缓冲队列深度                     |


**simple 模式延迟计算**: `latency = hop_count × (hop_latency + router_latency)`，其中 hop_count 基于 Mesh 拓扑的曼哈顿距离。

### DRAM（片外存储）

支持两种后端：内置简单模型和 Ramulator2 周期精确仿真。


| 参数                  | 类型     | 默认值                              | 说明                                  |
| ------------------- | ------ | -------------------------------- | ----------------------------------- |
| `dram.backend`      | string | `"simple"`                       | DRAM 后端：`"simple"` 或 `"ramulator2"` |
| `dram.num_channels` | int    | `4`                              | DRAM 通道数                            |
| `dram.config_path`  | string | `"configs/ramulator2_ddr4.yaml"` | Ramulator2 配置文件路径                   |


### 完整配置文件示例

```json
{
    "num_cores_x": 0,
    "num_cores_y": 0,
    "element_size_bits": 8,
    "core": {
        "mac_units": 256,
        "vector_units": 64,
        "clock_freq_mhz": 1000.0
    },
    "sram": {
        "size_kb": 512,
        "read_bandwidth_bytes_per_cycle": 64,
        "write_bandwidth_bytes_per_cycle": 64
    },
    "ni": {
        "max_outstanding_reqs": 16,
        "injection_queue_size": 8,
        "ejection_queue_size": 8
    },
    "noc": {
        "flit_size_bytes": 16,
        "hop_latency_cycles": 1,
        "router_latency_cycles": 1,
        "backend": "simple",
        "booksim2_config_path": "configs/booksim2_mesh.cfg",
        "injection_queue_depth": 16
    },
    "dram": {
        "config_path": "configs/ramulator2_ddr4.yaml",
        "num_channels": 4,
        "backend": "simple"
    },
    "ir_path": "",
    "output_path": "sim_output"
}
```

---

## BookSim2 配置（`configs/booksim2_mesh.cfg`）

当 `noc.backend` 设为 `"booksim2"` 时，通过此文件配置周期精确的 NoC 仿真。核心阵列维度（`k` 和 `n`）由仿真器根据 `num_cores_x/y` 自动注入，无需手动设置。

### 拓扑与路由


| 参数                 | 默认值         | 可选值                               | 说明     |
| ------------------ | ----------- | --------------------------------- | ------ |
| `topology`         | `mesh`      | `mesh`, `torus`, `fly`, `flatfly` | 网络拓扑结构 |
| `routing_function` | `dim_order` | `dim_order`, `romm`, `min_adapt`  | 路由算法   |


### 路由器微架构


| 参数             | 默认值     | 说明                                |
| -------------- | ------- | --------------------------------- |
| `router`       | `iq`    | 路由器类型（`iq` = Input-Queued）        |
| `num_vcs`      | `8`     | 每个输入端口的虚通道数                       |
| `vc_buf_size`  | `8`     | 每个虚通道的缓冲区大小（flit 数）               |
| `vc_allocator` | `islip` | 虚通道分配算法（`islip`, `pim`, `select`） |
| `sw_allocator` | `islip` | 交换分配算法（`islip`, `pim`, `select`）  |
| `alloc_iters`  | `1`     | 分配器迭代次数                           |


### 流水线时序


| 参数               | 默认值 | 说明          |
| ---------------- | --- | ----------- |
| `routing_delay`  | `1` | 路由计算延迟（周期）  |
| `vc_alloc_delay` | `1` | 虚通道分配延迟（周期） |
| `sw_alloc_delay` | `1` | 交换分配延迟（周期）  |
| `st_final_delay` | `1` | 交换遍历延迟（周期）  |
| `credit_delay`   | `0` | 信用返回延迟（周期）  |
| `output_delay`   | `0` | 输出延迟（周期）    |


### 其他


| 参数                      | 默认值    | 说明                         |
| ----------------------- | ------ | -------------------------- |
| `subnets`               | `1`    | 子网数量                       |
| `packet_size`           | `1`    | 包大小（flit 数），实际由仿真器按数据量动态计算 |
| `deadlock_warn_timeout` | `1024` | 死锁检测超时周期数                  |


更多参数请参考 [BookSim 2.0 文档](https://nocs.stanford.edu/booksim.html)。

---

## Ramulator2 配置（`configs/ramulator2_ddr4.yaml`）

当 `dram.backend` 设为 `"ramulator2"` 时，通过此 YAML 文件配置周期精确的 DRAM 仿真。

### Frontend（前端接口）


| 参数                     | 默认值    | 说明                      |
| ---------------------- | ------ | ----------------------- |
| `Frontend.impl`        | `GEM5` | 前端接口类型（由仿真器通过 API 调用）   |
| `Frontend.clock_ratio` | `4`    | 前端与 DRAM 的时钟比（CPU:DRAM） |


### DRAM 规格


| 参数                                | 默认值           | 可选值                                                              | 说明                  |
| --------------------------------- | ------------- | ---------------------------------------------------------------- | ------------------- |
| `MemorySystem.DRAM.impl`          | `DDR4`        | `DDR3`, `DDR4`, `DDR5`, `LPDDR5`, `GDDR6`, `HBM`, `HBM2`, `HBM3` | DRAM 标准             |
| `MemorySystem.DRAM.org.preset`    | `DDR4_8Gb_x8` | 见下表                                                              | 组织结构预设              |
| `MemorySystem.DRAM.org.channel`   | `1`           | 整数                                                               | 每个 Ramulator 实例的通道数 |
| `MemorySystem.DRAM.org.rank`      | `2`           | 整数                                                               | Rank 数量             |
| `MemorySystem.DRAM.timing.preset` | `DDR4_2400R`  | 见下表                                                              | 时序预设                |


**DDR4 组织结构预设**：`DDR4_2Gb_x4`, `DDR4_2Gb_x8`, `DDR4_2Gb_x16`, `DDR4_4Gb_x4`, `DDR4_4Gb_x8`, `DDR4_4Gb_x16`, `DDR4_8Gb_x4`, `DDR4_8Gb_x8`, `DDR4_8Gb_x16`

**DDR4 时序预设**：`DDR4_1600K`, `DDR4_1600L`, `DDR4_1866M`, `DDR4_1866N`, `DDR4_2133P`, `DDR4_2133R`, `DDR4_2400R`, `DDR4_2400U`, `DDR4_3200W`, `DDR4_3200AA`

### 内存控制器


| 参数                                            | 默认值       | 可选值                  | 说明                    |
| --------------------------------------------- | --------- | -------------------- | --------------------- |
| `MemorySystem.Controller.impl`                | `Generic` | `Generic`            | 控制器实现                 |
| `MemorySystem.Controller.Scheduler.impl`      | `FRFCFS`  | `FRFCFS`, `FCFS`     | 调度策略（FR-FCFS = 优先行命中） |
| `MemorySystem.Controller.RefreshManager.impl` | `AllBank` | `AllBank`, `PerBank` | 刷新管理策略                |


### 地址映射


| 参数                             | 默认值          | 可选值                                       | 说明     |
| ------------------------------ | ------------ | ----------------------------------------- | ------ |
| `MemorySystem.AddrMapper.impl` | `RoBaRaCoCh` | `RoBaRaCoCh`, `ChRaBaRoCo`, `MOP4CLXOR` 等 | 地址映射方案 |


地址映射方案名称由地址字段缩写组合而成：`Ro`=Row, `Ba`=Bank, `Ra`=Rank, `Co`=Column, `Ch`=Channel。

### 完整配置示例

```yaml
Frontend:
  impl: GEM5
  clock_ratio: 4

MemorySystem:
  impl: GenericDRAM
  clock_ratio: 1

  DRAM:
    impl: DDR4
    org:
      preset: DDR4_8Gb_x8
      channel: 1
      rank: 2
    timing:
      preset: DDR4_2400R

  Controller:
    impl: Generic
    Scheduler:
      impl: FRFCFS
    RefreshManager:
      impl: AllBank

  AddrMapper:
    impl: RoBaRaCoCh
```

更多参数和 DRAM 标准请参考 [Ramulator 2.0 文档](https://github.com/CMU-SAFARI/ramulator2)。