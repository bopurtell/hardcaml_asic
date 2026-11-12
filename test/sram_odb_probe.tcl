# Read-only checkpoint database observation, executed only by the opt-in real harness.
# Never runs floorplanning, PDN generation, placement, routing, or design repair.
read_db $::env(HC_SRAM_ODB_INPUT)
set block [ord::get_db_block]
set inst [$block findInst hc_resource_7_program]
if {$inst eq "NULL"} { error "missing stock macro instance" }
if {[[$inst getMaster] getName] ne "RM_IHPSG13_1P_256x16_c2_bm_bist"} { error "wrong stock master" }
if {[$inst getOrient] ne "R0"} { error "wrong macro orientation" }
set units [[ord::get_db_tech] getDbUnitsPerMicron]
if {$units <= 0} { error "missing database units" }
set bbox [$inst getBBox]
foreach method {xMin yMin xMax yMax} expected {40.0 81.0 276.8 199.78} {
    if {[$bbox $method] != round($expected * $units)} { error "macro footprint moved: $method" }
}
set count 0
foreach candidate [$block getInsts] {
    if {[[$candidate getMaster] getName] eq "RM_IHPSG13_1P_256x16_c2_bm_bist"} { incr count }
}
if {$count != 1} { error "expected exactly one stock macro" }
foreach pin {VDD! VDDARRAY! VSS!} {
    set term [$inst findITerm $pin]
    if {$term eq "NULL" || [$term getNet] eq "NULL"} { error "missing macro supply $pin" }
    set expected [expr {$pin eq "VSS!" ? "VGND" : "VPWR"}]
    if {[[$term getNet] getName] ne $expected} { error "wrong supply net $pin" }
}
set counts {}
foreach name {VPWR VGND} {
    set net [$block findNet $name]
    set boxes 0
    foreach wire [$net getSWires] { incr boxes [llength [$wire getWires]] }
    if {$boxes == 0} { error "lost PDN shapes on $name" }
    lappend counts "\"$name\":$boxes"
}
set output [open $::env(HC_SRAM_ODB_OUTPUT) {WRONLY CREAT EXCL}]
try {
    puts $output "\{\"schema_version\":1,\"scope\":\"checkpoint_odb_supply_and_shape_carryover_only\",\"macro_count\":1,\"orientation\":\"R0\",\"supply_shape_counts\":\{[join $counts ,]\}\}"
} finally { close $output }
