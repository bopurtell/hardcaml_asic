#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
pdk=${PDK_ROOT:-"$repo/.toolchain/pdk"}
out=${1:-"$here/runs/behavioral"}
macro="$pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_256x16_c2_bm_bist.v"
model="$pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v"

mkdir -p "$out"
command=(iverilog -g2012 -Wall -DFUNCTIONAL -s tt_um_sram_probe_tb
  -o "$out/probe.vvp" "$model" "$macro"
  "$here/tt_um_sram_probe.v"
  "$here/tt_um_sram_probe_tb.sv")
printf 'cwd: %s\ncommand:' "$repo" >"$out/compile.log"
printf ' %q' "${command[@]}" >>"$out/compile.log"
printf '\n' >>"$out/compile.log"
"${command[@]}" >>"$out/compile.log" 2>&1
vvp "$out/probe.vvp" >"$out/run.log" 2>&1
rm "$out/probe.vvp"
