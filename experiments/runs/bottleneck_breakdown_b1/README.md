# 端到端阶段占比分解实验（批1）

用于第五章“端到端阶段占比分解口径”小节，表 \ref{tab:bottleneck} 数据来源。

## 数据生成

单核、批大小 1、simple 后端下运行：

```bash
# ResNet34 b1
build/npu_sim -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet34.sim_quantized_b1_c1_bw16_stschedule.json -c experiments/simple_single_core_config.json

# ResNet50 b1
build/npu_sim -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json -c experiments/simple_single_core_config.json
```

从 stdout 的 Per-Core Statistics 读取 Loading、Compute、Writeback、StallNoC，按论文公式计算 T_Compute、T_NoC_Stall、T_Mem_Stall 及占比，写入 `bottleneck_breakdown.csv`。

## 文件

- `bottleneck_breakdown.csv`：总周期、T_Compute、T_NoC_Stall、T_Mem_Stall、计算\%、NoC\%、访存\%
