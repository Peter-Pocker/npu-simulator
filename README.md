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

| 算子 | 计算模型 | 说明 |
|------|----------|------|
| `conv2d` | MAC 密集型 | 标准卷积，MACs = B×H×W×weight_elements |
| `fc` | MAC 密集型 | 全连接层，复用 conv2d 计算模型 |
| `pool` | 向量型 | 池化层，ops = ofmap_volume |
| `element_wise` | 向量型 | 逐元素运算（add/relu 等） |
| `point_to_point` | 数据搬运型 | 数据重排/拷贝 |

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

## 编译与运行

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)

# 运行仿真
./npu_sim -c ../configs/default_config.json -i <path_to_ir.json>
```

## 可配置参数

通过 `configs/default_config.json` 配置：

- **Core**: MAC 单元数、向量单元数、时钟频率
- **SRAM**: 容量、读/写带宽
- **NoC**: Flit 大小、跳数延迟、路由器延迟
- **DRAM**: 通道数、配置路径
- **NI**: 最大并发请求数、注入/弹出队列大小
