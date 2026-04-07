# Configuration Files

- **default_config.json**: Default configuration when `-c` is not specified. Uses **BookSim2** (NoC) and **Ramulator2** (DRAM) only; the simple backend is not used by default.
- **full_config.json**, **full_config_dram_noc.json**: Full simulation with BookSim2 + Ramulator2.
- **chips/**: Per-chip configs (Eyeriss, TPU, Simba, MAERI, etc.). All use **BookSim2** and **Ramulator2**; no chip config uses the simple backend.
- **chips/zebu_testchip.json**: ZeBu test chip config inferred from `third_party/Gemini-Compiler-IR` backend log + Scheduler IR (单核、25 MHz 核频、8MB L2、simple NoC/DRAM). 用于与 ZeBu trace 对比验证仿真器周期。生成方式见 `scripts/infer_zebu_hw_from_log.py`。

- **一键流程**：使用硬件配置 + 模型运行 SET-IR 并再用生成 IR 跑仿真器，见 `scripts/run_setir_and_simulate.py`（`-c <config> -m <model>`，可选 `--patch-setir` 使 SET-IR 从 stdin 读参数）。

To run with simple backends you would need to create a custom config with `noc.backend: "simple"` and/or `dram.backend: "simple"`.

### Ramulator2 与 LPDDR5（`dram.backend: "ramulator2"`）

- 配置中的 `standard`/`org`/`timing` 需与 **third_party/ramulator2** 源码中的 preset 一致，例如 LPDDR5 见 `third_party/ramulator2/src/dram/impl/LPDDR5.cpp`（`LPDDR5_8Gb_x16`、`LPDDR5_6400` 等）。
- npu_sim 链接的是**预编译**的 `third_party/ramulator2/libramulator.dylib`，该 dylib 若非从本仓库内 Ramulator2 源码构建，可能未包含 LPDDR5 或 preset 名称不一致，运行时会报 **`unordered_map::at: key not found`**。
- **处理方式**：在 `third_party/ramulator2` 下从源码重新编译，生成新的 `libramulator.dylib`（Ramulator2 的 CMake 会将其输出到 `third_party/ramulator2/`），再重新编译并运行 npu_sim：
  ```bash
  cd third_party/ramulator2 && mkdir -p build && cd build && cmake .. && make
  # 库会生成到 third_party/ramulator2/libramulator.dylib
  cd ../../.. && cmake --build build   # 重新编译 npu_sim
  ```
  完成后即可使用带 LPDDR5 的配置（如 `chips/zebu_testchip_4cores.json`）。
