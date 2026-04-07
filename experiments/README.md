# 第五章实验数据

本目录存放 ZeBu 对比实验与真实负载瓶颈实验的原始数据与汇总表，供论文第五章引用与复现。

**每次运行写入独立目录，不覆盖历史**：脚本默认将结果写入 `experiments/runs/run_<timestamp>/`，或通过 `--run-dir` 指定目录；默认使用 **BookSim2+Ramulator2 全后端**（`configs/default_config.json`），以增强说服力。

## 目录与文件说明

| 路径/文件 | 说明 |
|-----------|------|
| `runs/run_<timestamp>/` 或 `runs/<name>/` | 单次运行输出目录，内含本次所有 CSV、trace 与 run_info.txt |
| `run_info.txt` | 当次运行的 config 路径、时间戳、simulator 路径 |
| `sim_results_zebu.csv` | 各配置（model, batch）下仿真器 Total cycles |
| `zebu_validation_table.csv` | ZeBu 对比完整表：ZeBu周期、仿真器周期、相对误差（%） |
| `bottleneck_breakdown.csv` | 真实负载瓶颈分解：总周期、T_Compute、T_NoC_Stall、T_Mem_Stall 及占比 |
| `bottleneck_per_core.csv` | 每核阶段统计：idle、loading、computing、writeback、stall_noc |
| `trace/ResNet34/`, `trace/ResNet50/` | 带 `-t` 时导出的 state_trace.csv、workload_summary.csv |
| `simple_single_core_config.json` | 单核 simple 后端配置（可选，快速调试用） |

## 后处理（脚本默认调用）

运行 `scripts/run_zebu_and_bottleneck_experiments.py` 后，脚本会默认调用：

- **scripts/plot_gantt.py**：对每个含 `state_trace.csv` 的 trace 子目录生成 `core_states.png` 甘特图（需 pandas、matplotlib）。
- **scripts/csv_to_xlsx.py**：对每个 trace 目录生成 `trace_report.xlsx`，并对 `experiments/` 生成 `experiments_report.xlsx`（汇总本目录下所有 CSV，需 openpyxl）。

使用 `--no-plot` 或 `--no-xlsx` 可关闭对应后处理。

## 复现与补全

- **完整重跑**：`python3 scripts/run_zebu_and_bottleneck_experiments.py`（默认 full 后端、新建 `runs/run_<timestamp>`）。指定目录：`--run-dir experiments/runs/full_backend_run`。
- **ZeBu 对比补全**：若某配置未跑完，用同一 config 与 `--run-dir` 再跑一次（脚本会覆盖该 run_dir 下已有 CSV）；或手动将 Total cycles 追加到该 run 目录下的 `sim_results_zebu.csv`，再在脚本内逻辑中会重新生成 `zebu_validation_table.csv`（需在相同 run_dir 下执行 `--skip-run` 时需指定同一 `--run-dir`）。
- **瓶颈实验**：脚本会带 `-t` 跑 ResNet34/ResNet50 批 16，结果写入当次 run 目录下的 `bottleneck_breakdown.csv`、`bottleneck_per_core.csv` 与 `trace/ResNet34`、`trace/ResNet50`。
