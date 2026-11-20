(* A PDK-free example of one design constructor in both elaboration modes. *)

open! Core
open! Hardcaml
open! Hardcaml_asic

module I = struct
  type 'a t =
    { clock : 'a
    ; enable : 'a
    ; write_enable : 'a
    ; address : 'a [@bits 2]
    ; write_data : 'a [@bits 8]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t = { read_data : 'a [@bits 8] } [@@deriving hardcaml]
end

module Design = struct
  module I = I
  module O = O

  let name = "memory_example"
  let config = Single_port_ram.Config.create_exn ~width:8 ~depth:4 ~read_latency:1

  let create context (i : _ I.t) =
    { O.read_data =
        Single_port_ram.create
          context
          ~name:"program"
          config
          ~clock:i.clock
          ~enable:i.enable
          ~write_enable:i.write_enable
          ~address:i.address
          ~write_data:i.write_data
    }
  ;;
end

let make_project requirement =
  let policy = Resource_policy.create ~default:requirement [] |> Or_error.ok_exn in
  Chip_target.create
    ~name:"memory_example"
    ~metadata:
      { title = "Registered memory example"
      ; author = "hardcaml_asic"
      ; description = "Whole-word load and readback"
      }
    ~design:(module Design)
    ~target:{ harness = Tiny_tapeout { tiles = T1x1 }; technology = Resource.Technology.ihp_sg13cmos5l }
    ~flow:(Flow.librelane Hardening)
    ~clocks:[ { port = "clock"; period = Time_float.Span.of_ns 20. } ]
    ~policy
    ()
  |> Or_error.ok_exn
;;

let simulate elaborated_project =
  let sim = Cyclesim.create (Elaborated_project.circuit elaborated_project) in
  let written = Array.create ~len:4 false in
  let set port width value =
    Cyclesim.in_port sim port := Bits.of_int_trunc ~width value
  in
  let cycle ~enable ~write ~address ~data =
    set "enable" 1 enable;
    set "write_enable" 1 write;
    set "address" 2 address;
    set "write_data" 8 data;
    Cyclesim.cycle sim;
    !(Cyclesim.out_port sim "read_data")
  in
  List.iter [ 0, 0x36; 1, 0xc2 ] ~f:(fun (address, data) ->
    ignore (cycle ~enable:1 ~write:1 ~address ~data : Bits.t);
    written.(address) <- true);
  List.iter [ 0, 0x36; 1, 0xc2 ] ~f:(fun (address, expected) ->
    if not written.(address) then failwith "consumer attempted to read an unwritten word";
    let actual = cycle ~enable:1 ~write:0 ~address ~data:0 in
    if not (Bits.equal actual (Bits.of_int_trunc ~width:8 expected))
    then failwithf "readback at %d failed" address ();
    let held = cycle ~enable:0 ~write:0 ~address:0 ~data:0 in
    if not (Bits.equal actual held) then failwith "disabled output did not hold");
  print_endline "simulation: whole-word readback and disabled hold passed"
;;

let run () =
  let project = make_project Flops in
  List.iter [ Elaboration.Mode.Simulation; Implementation ] ~f:(fun mode ->
    let elaborated_project = Chip_target.elaborate project ~mode |> Or_error.ok_exn in
    let resource = List.hd_exn (Elaborated_project.resources elaborated_project) in
    printf
      "%s: %s, %s\n"
      (Sexp.to_string (Elaboration.Mode.sexp_of_t mode))
      (Resource.Id.to_string resource.id)
      (Sexp.to_string (Resource.Selection.sexp_of_t resource.selection));
    simulate elaborated_project);
  (match Chip_target.elaborate (make_project Exact) ~mode:Implementation with
   | Ok _ -> failwith "an unsupported macro-required request was accepted"
   | Error _ -> print_endline "macro-required request: rejected as expected");
  let fallback =
    Chip_target.elaborate (make_project Exact_or_flop_fallback) ~mode:Implementation
    |> Or_error.ok_exn
  in
  let resource = List.hd_exn (Elaborated_project.resources fallback) in
  printf "fallback: %s\n" (Sexp.to_string (Resource.Selection.sexp_of_t resource.selection))
;;

let () =
  let argv = Stdlib.Sys.argv in
  if Array.length argv = 3 && String.equal argv.(1) "--rtl"
  then (
    let elaborated_project =
      Chip_target.elaborate (make_project Flops) ~mode:Implementation |> Or_error.ok_exn
    in
    let rtl =
      Rtl.create Verilog [ Elaborated_project.circuit elaborated_project ]
      |> Rtl.full_hierarchy
      |> Rope.to_string
    in
    Out_channel.write_all argv.(2) ~data:rtl)
  else if Array.length argv = 1
  then run ()
  else failwith "usage: memory_example [--rtl OUTPUT.v]"
;;
