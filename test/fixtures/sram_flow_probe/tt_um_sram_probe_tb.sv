`timescale 1ns/1ps

module tt_um_sram_probe_tb;
    reg [7:0] ui_in = 0;
    wire [7:0] uo_out;
    reg [7:0] uio_in = 0;
    wire [7:0] uio_out;
    wire [7:0] uio_oe;
    reg ena = 0;
    reg clk = 0;
    reg rst_n = 0;
    integer checks = 0;

    always #10 clk = !clk;

    tt_um_sram_probe dut (.*);

    task cycle(input [7:0] address, input [7:0] command_data);
        begin
            @(negedge clk);
            ui_in = address;
            uio_in = command_data;
            @(posedge clk);
            #0.1;
        end
    endtask

    task expect_word(input [15:0] expected);
        begin
            checks = checks + 1;
            if ({uio_out, uo_out} !== expected)
                $fatal(1, "check %0d: got %h expected %h", checks,
                       {uio_out, uo_out}, expected);
        end
    endtask

    initial begin
        repeat (2) @(posedge clk);
        rst_n = 1;
        ena = 1;

        cycle(8'h12, 8'ha5);
        cycle(8'h34, 8'hc3);
        cycle(8'h12, 8'h00);
        expect_word(16'ha512);

        ena = 0;
        cycle(8'hee, 8'hff);
        expect_word(16'ha512);

        ena = 1;
        cycle(8'h34, 8'h00);
        expect_word(16'hc334);

        if (uio_oe !== 8'hff)
            $fatal(1, "uio_oe is not enabled");
        $display("PASS SRAM flow probe behavioral fixture: %0d checks", checks + 1);
        $finish;
    end
endmodule
