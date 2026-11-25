# Experimental Metal4-only TT grid. The two positive macro rails remain
# separately named in PDN_MACRO_CONNECTIONS, then both connect to VPWR.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET)

define_pdn_grid \
    -name stdcell_grid \
    -starts_with POWER \
    -voltage_domain CORE \
    -pins Metal4

add_pdn_stripe \
    -grid stdcell_grid \
    -layer Metal4 \
    -width $::env(PDN_VWIDTH) \
    -pitch $::env(PDN_VPITCH) \
    -offset $::env(PDN_VOFFSET) \
    -spacing $::env(PDN_VSPACING) \
    -starts_with POWER

if { $::env(PDN_ENABLE_RAILS) == 1 } {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins
    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) Metal4"
}

rename pdngen pdngen_without_sram_bridges

proc replace_power_pin {name layer_name x_min y_min x_max y_max} {
    set block [ord::get_db_block]
    set bterm [$block findBTerm $name]
    foreach bpin [$bterm getBPins] {
        odb::dbBPin_destroy $bpin
    }

    set layer [[ord::get_db_tech] findLayer $layer_name]
    set bpin [odb::dbBPin_create $bterm]
    $bpin setPlacementStatus FIRM
    odb::dbBox_create $bpin $layer $x_min $y_min $x_max $y_max
}

proc pdngen {args} {
    pdngen_without_sram_bridges {*}$args

    set block [ord::get_db_block]
    set metal4 [[ord::get_db_tech] findLayer Metal4]

    # Placement is (42,81) um. These bridges abut the lower VDD/VSS pin
    # edges and the upper VDDARRAY pin edge without crossing signal layers.
    # VPWR keeps 0.28 um clearance to the VGND grid at x=115.93 um;
    # x=115.80 left only 0.13 um (power-01 M4.b/M4.e). Pin contact is 1.95 um.
    set vpwr_swire [odb::dbSWire_create [$block findNet VPWR] ROUTED]
    odb::dbSBox_create $vpwr_swire $metal4 113700 79520 115650 81000 STRIPE
    odb::dbSBox_create $vpwr_swire $metal4 113700 197660 115650 201310 STRIPE

    set vgnd_swire [odb::dbSWire_create [$block findNet VGND] ROUTED]
    odb::dbSBox_create $vgnd_swire $metal4 64400 79520 68030 81000 STRIPE

    # TT precheck requires each power port to reach both horizontal edges.
    # Keep one uninterrupted grid stripe per net as the public pin geometry.
    replace_power_pin VPWR Metal4 311830 3560 313930 707080
    replace_power_pin VGND Metal4 315930 3560 318030 707080
}
