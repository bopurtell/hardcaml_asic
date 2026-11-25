create_clock -name clk -period 20 [get_ports {clk}]
set_clock_uncertainty 0.25 [get_clocks {clk}]
set_clock_transition 0.15 [get_clocks {clk}]
set_timing_derate -early 0.95
set_timing_derate -late 1.05
set_max_fanout 10 [current_design]
set_driving_cell -lib_cell sg13cmos5l_buf_4 -pin X [get_ports {ena rst_n ui_in uio_in}]
set_driving_cell -lib_cell sg13cmos5l_buf_4 -pin X [get_ports {clk}]
set_load 0.006 [get_ports {uo_out uio_oe uio_out}]
set_input_delay -max 2 -clock clk [get_ports {ena rst_n ui_in uio_in}]
set_input_delay -min 0 -clock clk [get_ports {ena rst_n ui_in uio_in}]
set_output_delay -max 2 -clock clk [get_ports {uo_out uio_oe uio_out}]
set_output_delay -min 0 -clock clk [get_ports {uo_out uio_oe uio_out}]
