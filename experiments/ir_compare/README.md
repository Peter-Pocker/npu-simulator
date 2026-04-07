# ResNet50 SET-IR vs Gemini IR 对比实验

在**相同硬件配置**下分别用 SET-IR 和 GEMINI 生成 ResNet50 的调度 IR，并对比两种 IR 的差异。

## 硬件配置

- 配置文件：`configs/chips/two_core_resnet.json`
- 拓扑：2×2 mesh（4 核），batch=1
- 单核：256 MAC、512KB SRAM
- NoC 带宽：16（与 SET-IR 的 `bw` 参数一致）

说明：严格两核（2×1 或 1×2）在 SET-IR 中受限于 `ylen>=2` 与 DRAM 端口布局会报错，故采用 2×2 以保证两种工具都能跑通。

## 一键运行

```bash
# 在仓库根目录执行
python3 scripts/compare_setir_gemini_resnet50_two_core.py
```

脚本会依次：

1. **SET-IR**：若存在 `SET-IR/` 且已 `make`，则运行  
   `echo "0 2 1 2 2 1 1 0 16 0" | ./SET-IR/build/stschedule resnet50_2x2`  
   并将生成的 `SET-IR/results/json/resnet50_2x2_SA-LS.json` 复制到  
   `experiments/ir_compare/resnet50_2x2_setir.json`。

2. **Gemini**：调用 `scripts/gemini_run.py`，使用上述配置与 `-n resnet`、`--skip-sim`，在  
   `experiments/ir_compare/gemini_trace/trace_YYYYMMDD_HHMMSS/` 下生成  
   `resnet_2x2_ir.json`。

3. **对比**：对两份 IR 做结构对比（拓扑、核数、每核 workload 数、workload 字段、layer 名称等），结果写入  
   `experiments/ir_compare/setir_vs_gemini_report.txt` 并打印到终端。

## 仅生成其一

- 只生成 Gemini IR（不跑 SET-IR）：  
  `python3 scripts/compare_setir_gemini_resnet50_two_core.py --no-setir`  
  此时需已有 SET-IR 的 IR 文件，或仅查看 Gemini 输出、不做对比。

- 只生成 SET-IR IR（不跑 Gemini）：  
  `python3 scripts/compare_setir_gemini_resnet50_two_core.py --no-gemini`

- 使用已有 IR 文件做对比：  
  `python3 scripts/compare_setir_gemini_resnet50_two_core.py --setir-ir path/to/setir.json --gemini-ir path/to/gemini.json`

## 依赖

- **SET-IR**：仓库内 `SET-IR/` 子模块（或克隆 [EliminateSpace/SET-IR](https://github.com/EliminateSpace/SET-IR)），在 `SET-IR/` 下执行 `make`。  
  当前 SET-IR 在 2×2、batch=1 下导出 IR 时可能触发 `transfer.cpp` 的 assert，若遇此情况可先在其它配置下生成 SET-IR IR，再通过 `--setir-ir` 传入做对比。
- **Gemini**：需要已构建 `third_party/GEMINI-HPCA2024`（`make release`），即存在  
   `third_party/GEMINI-HPCA2024/build/stschedule`。

## 两种 IR 的典型差异（对比报告会包含）

- **拓扑**：`xlen`/`ylen` 是否一致。
- **核与 workload**：核 ID 集合、每核 workload 数量、总 workload 数。
- **Workload 结构**：各层字段差异（如仅 SET-IR 或仅 Gemini 有的字段）。
- **Layer 命名**：同一层在不同 IR 中的 `layer_name` 等。
- **DRAM 段**：`-1` 段是否存在及内容差异。

输出示例见同目录下的 `setir_vs_gemini_report.txt`（运行一次后生成）。
