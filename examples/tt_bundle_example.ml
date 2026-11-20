open! Core
open! Hardcaml
open! Hardcaml_asic

module Observable = struct
  module I = Tt_cmos5l.I
  module O = Tt_cmos5l.O

  let name = "tt_um_asic_observable"

  let create _ (i : _ I.t) =
    let observed =
      Signal.reg
        (Signal.Reg_spec.create ~clock:i.clk ~clear:(Signal.(~:) i.rst_n) ())
        i.ui_in
    in
    { O.uo_out = Signal.mux2 i.ena observed (Signal.zero 8)
    ; uio_out = Signal.zero 8
    ; uio_oe = Signal.zero 8
    }
  ;;
end

module Memory = struct
  module I = Tt_cmos5l.I
  module O = Tt_cmos5l.O

  let name = "tt_um_asic_memory"
  let config = Single_port_ram.Config.create_exn ~width:8 ~depth:4 ~read_latency:1

  let create context (i : _ I.t) =
    let read_data =
      Single_port_ram.create
        context
        ~name:"program"
        config
        ~clock:i.clk
        ~enable:i.ena
        ~write_enable:(Signal.bit i.ui_in ~pos:2)
        ~address:(Signal.select i.ui_in ~high:1 ~low:0)
        ~write_data:i.uio_in
    in
    { O.uo_out = read_data
    ; uio_out = Signal.zero 8
    ; uio_oe = Signal.zero 8
    }
  ;;
end

let pinout : Pinout.t =
  List.concat_map [ Pinout.Bank.Ui; Uo; Uio ] ~f:(fun bank ->
    List.init 8 ~f:(fun bit ->
      let description =
        match bank with
        | Ui ->
          if bit < 2 then "Memory address / observable input"
          else if bit = 2 then "Memory write enable / observable input"
          else "Observable input"
        | Uo -> "Registered observable or memory read data"
        | Uio -> "Memory write data input; output disabled"
      in
      { Pinout.bank; bit; description }))
;;

let project kind =
  let design, title, description =
    match kind with
    | "observable" ->
      ( (module Observable : Chip_target.Design)
      , "ASIC observable example"
      , "Registered TT input observation" )
    | "memory" ->
      ( (module Memory : Chip_target.Design)
      , "ASIC memory example"
      , "Four-word registered memory with whole-word load and readback" )
    | _ -> failwith "kind must be observable or memory"
  in
  Chip_target.create
    ~name:("asic_" ^ kind)
    ~metadata:{ title; author = "hardcaml_asic"; description }
    ~design
    ~target:
      { harness = Tiny_tapeout { tiles = T6x4 }
      ; technology = Technology.ihp_sg13cmos5l
      }
    ~flow:(Flow.librelane ~overrides:(Tt_cmos5l.overrides ()) Hardening)
    ~clocks:[ { Clock.port = "clk"; period = Time_float.Span.of_sec (1. /. 48e6) } ]
    (* Minimums of 0 are the hold-pessimistic choice: TT inputs are driven asynchronously
       to clk through the mux, so a change right at the clock edge is possible, and the
       template's PL/GRT hold slack margins then make the resizer repair hold with that
       assumption rather than find no hold paths at all. rst_n (synchronous clear) and
       ena (output gate) are delayed like ui_in so their paths are not unconstrained. *)
    ~timing:
      (if String.equal kind "observable"
       then
          { Timing.empty with input_delays =
             List.map [ "ui_in"; "rst_n"; "ena" ] ~f:(fun port ->
               { Timing.Delay.port
               ; minimum = Time_float.Span.of_ns 0.
               ; maximum = Time_float.Span.of_ns 1.
               })
         ; output_delays =
             [ { port = "uo_out"
               ; minimum = Time_float.Span.of_ns 0.
               ; maximum = Time_float.Span.of_ns 2.
               }
             ]
         }
       else Timing.empty)
    ~pinout
    ~policy:(Resource_policy.create ~default:Flops [] |> Or_error.ok_exn)
    ()
  |> Or_error.ok_exn
;;

let source_inputs root =
  let rec library_files dir =
    Stdlib.Sys.readdir (Filename.concat root dir)
    |> Array.to_list
    |> List.concat_map ~f:(fun name ->
      let path = dir ^ "/" ^ name in
      if Stdlib.Sys.is_directory (Filename.concat root path)
      then library_files path
      else if String.is_suffix name ~suffix:".ml" || String.is_suffix name ~suffix:".mli"
      then [ path ]
      else [])
  in
  [ "dune-project"; "lib/dune"; "examples/dune"; "examples/tt_bundle_example.ml" ]
  @ library_files "lib"
;;

let () =
  let argv = Stdlib.Sys.argv in
  if Array.length argv <> 3 && Array.length argv <> 4
  then failwith "usage: tt_bundle_example (observable|memory) OUTPUT_DIR [SOURCE_ROOT]";
  let kind = argv.(1) in
  let output_dir = argv.(2) in
  let source_root = if Array.length argv = 4 then argv.(3) else "." in
  let project = project kind in
  let resolved = Chip_target.elaborate_for_flow project |> Or_error.ok_exn in
  let simulation_project =
    Chip_target.elaborate project ~mode:Simulation |> Or_error.ok_exn
  in
  let bundle =
    Bundle.render
      ~simulation_project
      ~source_root
      ~input_paths:(source_inputs source_root)
      resolved
    |> Or_error.ok_exn
  in
  Bundle.write bundle ~output_dir |> Or_error.ok_exn;
  printf "%s %s\n" output_dir (Bundle.identity bundle)
;;
