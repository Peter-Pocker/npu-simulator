#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$ROOT_DIR/build/npu_sim"

# ── Default values ──
CONFIG=""
IR=""
MODE="simple"
TRACE_ARGS=""

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  -c, --config <path>   Path to config JSON (default: auto-selected by mode)
  -i, --ir <path>       Path to IR JSON file (required)
  -m, --mode <mode>     Simulation mode (default: simple)
                          simple   - simple latency model (fast)
                          full     - BookSim2 + Ramulator2 (cycle-accurate)
  -t, --trace [dir]     Enable per-core state trace (default dir: ./trace/)
  -h, --help            Show this help

Examples:
  $0 -i tests/test_ir.json
  $0 -m full -i tests/test_ir.json
  $0 -t -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config) CONFIG="$2"; shift 2 ;;
        -i|--ir)     IR="$2";     shift 2 ;;
        -m|--mode)   MODE="$2";   shift 2 ;;
        -t|--trace)
            if [[ $# -gt 1 && "${2:0:1}" != "-" ]]; then
                TRACE_ARGS="--trace $2"; shift 2
            else
                TRACE_ARGS="--trace"; shift
            fi
            ;;
        -h|--help)   usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$IR" ]]; then
    echo "Error: IR file not specified. Use -i <path>"
    echo ""
    usage
fi

if [[ ! -f "$BIN" ]]; then
    echo "Error: npu_sim binary not found at $BIN"
    echo "       Run ./build.sh first."
    exit 1
fi

if [[ -z "$CONFIG" ]]; then
    case "$MODE" in
        simple) CONFIG="$ROOT_DIR/configs/default_config.json" ;;
        full)   CONFIG="$ROOT_DIR/configs/full_config.json" ;;
        *)      echo "Error: Unknown mode '$MODE'. Use 'simple' or 'full'."; exit 1 ;;
    esac
fi

echo "========================================"
echo " NPU Simulator"
echo "  Mode:   $MODE"
echo "  Config: $CONFIG"
echo "  IR:     $IR"
[[ -n "$TRACE_ARGS" ]] && echo "  Trace:  enabled"
echo "========================================"
echo ""

exec "$BIN" --config "$CONFIG" --ir "$IR" $TRACE_ARGS
