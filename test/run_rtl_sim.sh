#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: test/run_rtl_sim.sh OUTPUT_DIR" >&2
  exit 2
fi

output_dir=$1
mkdir -p "$output_dir"
dune exec -j "${ASIC_BUILD_JOBS:-20}" examples/memory_example.exe -- --rtl "$output_dir/memory_example.v"
iverilog_bin=${IVERILOG_BIN:-iverilog}
vvp_bin=${VVP_BIN:-vvp}
if [ -n "${IVERILOG_BASE:-}" ]; then
  "$iverilog_bin" -B "$IVERILOG_BASE" -g2012 -s memory_example_tb \
    -o "$output_dir/memory_example.vvp" \
    "$output_dir/memory_example.v" test/memory_example_tb.v
else
  "$iverilog_bin" -g2012 -s memory_example_tb \
    -o "$output_dir/memory_example.vvp" \
    "$output_dir/memory_example.v" test/memory_example_tb.v
fi
"$vvp_bin" "$output_dir/memory_example.vvp"
