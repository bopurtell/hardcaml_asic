`default_nettype none

module tt_um_sram_probe (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    wire [15:0] read_data;

    RM_IHPSG13_1P_256x16_portable_wrapper memory (
        .clock(clk),
        .enable(ena && rst_n),
        .write_enable(uio_in[7]),
        .address(ui_in),
        .write_data({uio_in, ui_in}),
        .read_data(read_data)
    );

    assign uo_out = read_data[7:0];
    assign uio_out = read_data[15:8];
    assign uio_oe = 8'hff;
endmodule

module RM_IHPSG13_1P_256x16_portable_wrapper (
    input  wire        clock,
    input  wire        enable,
    input  wire        write_enable,
    input  wire [7:0]  address,
    input  wire [15:0] write_data,
    output wire [15:0] read_data
);
    RM_IHPSG13_1P_256x16_c2_bm_bist sram (
        .A_CLK(clock),
        .A_MEN(enable),
        .A_WEN(enable && write_enable),
        .A_REN(enable && !write_enable),
        .A_ADDR(address),
        .A_DIN(write_data),
        .A_DLY(1'b1),
        .A_DOUT(read_data),
        .A_BM(16'hffff),
        .A_BIST_CLK(1'b0),
        .A_BIST_EN(1'b0),
        .A_BIST_MEN(1'b0),
        .A_BIST_WEN(1'b0),
        .A_BIST_REN(1'b0),
        .A_BIST_ADDR(8'h00),
        .A_BIST_DIN(16'h0000),
        .A_BIST_BM(16'h0000)
    );
endmodule

`default_nettype wire
