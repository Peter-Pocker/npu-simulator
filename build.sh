#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
NPROC="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)"

echo "========================================"
echo " NPU Simulator Build Script"
echo "========================================"

# ── Step 1: Build Ramulator2 ──
echo ""
echo "[1/3] Building Ramulator2 ..."
RAMULATOR2_DIR="$ROOT_DIR/third_party/ramulator2"
mkdir -p "$RAMULATOR2_DIR/build"
cmake -S "$RAMULATOR2_DIR" -B "$RAMULATOR2_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    > /dev/null 2>&1 || \
cmake -S "$RAMULATOR2_DIR" -B "$RAMULATOR2_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
make -C "$RAMULATOR2_DIR/build" -j"$NPROC" ramulator
echo "[1/3] Ramulator2 done."

# ── Step 2: Build BookSim2 ──
echo ""
echo "[2/3] Building BookSim2 ..."
BOOKSIM2_SRC="$ROOT_DIR/third_party/booksim2/src"
make -C "$BOOKSIM2_SRC" -j"$NPROC"

echo "       Creating static library ..."
cd "$BOOKSIM2_SRC"
OBJ_FILES=$(find . -name '*.o' ! -name 'main.o' | sort)
ar rcs libbooksim.a $OBJ_FILES
cd "$ROOT_DIR"
echo "[2/3] BookSim2 done."

# ── Step 3: Build NPU Simulator ──
echo ""
echo "[3/3] Building NPU Simulator ..."
mkdir -p "$ROOT_DIR/build"
cmake -S "$ROOT_DIR" -B "$ROOT_DIR/build" -DCMAKE_BUILD_TYPE=Release
make -C "$ROOT_DIR/build" -j"$NPROC" npu_sim
echo "[3/3] NPU Simulator done."

echo ""
echo "========================================"
echo " Build complete: build/npu_sim"
echo "========================================"
