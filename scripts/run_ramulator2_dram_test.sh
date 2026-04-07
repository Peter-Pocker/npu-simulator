#!/usr/bin/env bash
# 测试仿真器与 Ramulator2 的 DRAM 交互：使用小规模切片 IR，并打印 DRAM 请求/响应日志。
# 用法:
#   ./scripts/run_ramulator2_dram_test.sh [输入IR路径] [切片workload数]
# 示例:
#   ./scripts/run_ramulator2_dram_test.sh
#   ./scripts/run_ramulator2_dram_test.sh resnet50_2x2_gemini_ir_slice.json 4

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

INPUT_IR="${1:-$PROJECT_ROOT/resnet50_2x2_gemini_ir_slice.json}"
FIRST_N="${2:-4}"
TEST_IR="$PROJECT_ROOT/ir_ramulator2_dram_test_slice.json"
CONFIG="$PROJECT_ROOT/configs/ramulator2_dram_test.json"
SIM="${SIM:-$PROJECT_ROOT/build/npu_sim}"

if [[ ! -f "$INPUT_IR" ]]; then
  echo "Error: Input IR not found: $INPUT_IR"
  echo "Usage: $0 [input_ir.json] [first_n_workloads]"
  exit 1
fi

echo "[Ramulator2 DRAM test] Slicing IR: first $FIRST_N workloads from $INPUT_IR -> $TEST_IR"
python3 "$SCRIPT_DIR/slice_ir.py" --ir "$INPUT_IR" --first "$FIRST_N" -o "$TEST_IR"

if [[ ! -f "$SIM" ]]; then
  echo "Error: Simulator not found at $SIM. Build with: mkdir -p build && cd build && cmake .. && make"
  exit 1
fi

echo "[Ramulator2 DRAM test] Running simulator (DRAM logs enabled in config)..."
"$SIM" -c "$CONFIG" -i "$TEST_IR" --log-dram

echo "[Ramulator2 DRAM test] Done. Check output above for [DRAM] and [Ramulator2DRAM] lines."
