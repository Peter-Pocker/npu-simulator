#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$ROOT_DIR/build/npu_sim"

CONFIG=""
IR=""
TRACE_ARGS=""
TRACE_DIR=""

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  -c, --config <path>   Path to config JSON (default: configs/default_config.json)
  -i, --ir <path>       Path to IR JSON file (required)
  -t, --trace [dir]     Enable per-core state trace (default: ./trace/trace_YYYYMMDD_HHMMSS/)
  -h, --help            Show this help

Examples:
  $0 -c configs/chips/tpu_v1.json -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json
  $0 -c configs/chips/simba.json -t -i third_party/Gemini-Compiler-IR/scheduler_output/int8_resnet50.sim_quantized_b1_c1_bw16_stschedule.json
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config) CONFIG="$2"; shift 2 ;;
        -i|--ir)     IR="$2";     shift 2 ;;
        -t|--trace)
            if [[ $# -gt 1 && "${2:0:1}" != "-" ]]; then
                TRACE_DIR="$2"
                TRACE_ARGS="--trace $2"; shift 2
            else
                # Default: unique dir per run to avoid overwriting previous results
                TRACE_DIR="trace/trace_$(date +%Y%m%d_%H%M%S)"
                TRACE_ARGS="--trace $TRACE_DIR"; shift
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
    CONFIG="$ROOT_DIR/configs/default_config.json"
fi

# When trace enabled: create trace dir and record command, config, IR path, then capture stdout
if [[ -n "$TRACE_ARGS" ]]; then
    mkdir -p "$TRACE_DIR"
    {
        echo "Run at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "Command: $0 -c $CONFIG -i $IR -t $TRACE_DIR"
        echo "Full invocation: $BIN --config $CONFIG --ir $IR --trace $TRACE_DIR"
    } > "$TRACE_DIR/command.txt"
    cp "$CONFIG" "$TRACE_DIR/config.json"
    echo "$IR" > "$TRACE_DIR/ir_path.txt"
    if [[ -f "$IR" ]]; then
        IR_DIR="$(cd "$(dirname "$IR")" && pwd)"
        TRACE_ABS="$(cd "$TRACE_DIR" && pwd)"
        if [[ "$IR_DIR" != "$TRACE_ABS" ]]; then
            # IR from elsewhere: copy so trace folder is self-contained
            cp "$IR" "$TRACE_DIR/ir.json"
        fi
    fi
fi

echo "========================================"
echo " NPU Simulator"
echo "  Config: $CONFIG"
echo "  IR:     $IR"
[[ -n "$TRACE_ARGS" ]] && echo "  Trace:  $TRACE_DIR"
echo "========================================"
echo ""

if [[ -n "$TRACE_ARGS" ]]; then
    "$BIN" --config "$CONFIG" --ir "$IR" --trace "$TRACE_DIR" 2>&1 | tee "$TRACE_DIR/stdout.txt" || true
    SIM_EXIT=${PIPESTATUS[0]}
else
    "$BIN" --config "$CONFIG" --ir "$IR" $TRACE_ARGS
    SIM_EXIT=$?
fi

if [[ -n "$TRACE_ARGS" && -d "$TRACE_DIR" ]]; then
    echo ""
    echo "[post] Converting CSV to XLSX ..."
    python3 "$ROOT_DIR/scripts/csv_to_xlsx.py" "$TRACE_DIR" || true

    echo "[post] Generating Gantt chart ..."
    python3 "$ROOT_DIR/scripts/plot_gantt.py" --trace "$TRACE_DIR" --output "$TRACE_DIR/core_states.png" || true
fi

if [[ $SIM_EXIT -ne 0 ]]; then
    exit "$SIM_EXIT"
fi
