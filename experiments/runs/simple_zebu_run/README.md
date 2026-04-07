# Simple 后端 ZeBu 对比结果

配置：`experiments/simple_single_core_config.json`（NoC/DRAM 均为 simple）

## 已完成的对比（4/7）

| 模型     | 批大小 | ZeBu 周期 | 仿真器周期 | 相对误差 |
|----------|--------|-----------|------------|----------|
| ResNet34 | 1      | 1,922,067 | 1,890,695  | **-1.63%** |
| ResNet34 | 4      | 4,280,215 | 4,501,725  | **+5.18%** |
| ResNet50 | 1      | 2,545,655 | 2,432,592  | **-4.44%** |
| ResNet50 | 4      | 5,470,662 | 5,653,354  | **+3.34%** |

## 结论（simple 后端）

- 4 个配置的相对误差均在 **±5.2%** 以内，与 ZeBu 吻合较好。
- ResNet34 b1、ResNet50 b1 略低于 ZeBu（-1.63%、-4.44%）；ResNet34/50 b4 略高（+5.18%、+3.34%）。

## 未完成（需较长运行时间）

- ResNet34 b16、ResNet50 b16、ResNet50 b64 可再次执行脚本补跑：
  ```bash
  python3 scripts/run_zebu_and_bottleneck_experiments.py --config experiments/simple_single_core_config.json --run-dir experiments/runs/simple_zebu_run --timeout 1200 --no-plot --no-xlsx
  ```
  或单独对某 IR 运行 `build/npu_sim -i <ir_path> -c experiments/simple_single_core_config.json` 后将 Total cycles 追加到 `sim_results_zebu.csv`，再运行 `scripts/batch_compare_zebu.py` 生成最新 `zebu_validation_table.csv`。
