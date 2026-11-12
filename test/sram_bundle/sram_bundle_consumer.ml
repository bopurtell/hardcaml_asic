module U = Unix
open! Core
open! Hardcaml
open! Hardcaml_asic

module Design = struct
  module I = Tt_cmos5l.I
  module O = Tt_cmos5l.O
  let name = "tt_um_public_sram"
  let create context (i : _ I.t) =
    let ram name = Single_port_ram.create context ~name
      (Single_port_ram.Config.create_exn ~width:16 ~depth:256 ~read_latency:1)
      ~clock:i.clk ~enable:i.ena ~write_enable:i.rst_n ~address:i.ui_in
      ~write_data:(Signal.concat_msb [i.ui_in; i.uio_in]) in
    let left = ram "left" and right = ram "right" in
    { O.uo_out = Signal.sel_bottom left ~width:8
    ; uio_out = Signal.sel_bottom right ~width:8
    ; uio_oe = Signal.zero 8 }
end

module Single_design = struct
  module I = Tt_cmos5l.I
  module O = Tt_cmos5l.O
  let name = "tt_um_segmented_sram_fixture"
  let create context (i : _ I.t) =
    let read_data = Single_port_ram.create context ~name:"program"
      (Single_port_ram.Config.create_exn ~width:16 ~depth:256 ~read_latency:1)
      ~clock:i.clk ~enable:i.ena ~write_enable:i.rst_n ~address:i.ui_in
      ~write_data:(Signal.concat_msb [i.ui_in; i.uio_in]) in
    { O.uo_out = Signal.sel_bottom read_data ~width:8
    ; uio_out = Signal.zero 8; uio_oe = Signal.zero 8 }
end

let first_stage = Timing.Stage.create ~id:"fixture_first" ~chain:"fixture" ~index:0
let second_stage = Timing.Stage.create ~id:"fixture_second" ~chain:"fixture" ~index:1

module Linked_single_design = struct
  include Single_design
  let create context (i : _ I.t) =
    let output = Single_design.create context i in
    let spec = Signal.Reg_spec.create ~clock:i.clk () in
    let first = Signal.reg spec i.ena in
    let second = Signal.reg spec first in
    Elaboration.Context.register_timing_stage_exn context ~stage:first_stage ~q:first;
    Elaboration.Context.register_timing_stage_exn context ~stage:second_stage ~q:second;
    { output with O.uio_out = Signal.(uresize (first |: second) ~width:8) }
end

let id = Resource.Id.of_string_exn
let intent name x = Macro_intent.tt_cmos5l_r0 ~resource:(id name) ~x_um:x ~y_um:81. ()
let project ?(single = false) ?(linked = false) ?(operation = Flow.Operation.Synthesis) ?(macros = [intent "left" 42.; intent "right" 400.]) ?(overrides = []) () =
  Project.create ~name:"sram_bundle_fixture"
    ~metadata:{ title="Public SRAM"; author="test"; description="Exact production resource bundle" }
    ~design:(if linked then (module Linked_single_design : Project.Design)
      else if single then (module Single_design : Project.Design) else (module Design : Project.Design))
    ~target:{ harness=Tiny_tapeout { tiles=T6x4 }; technology=Technology.ihp_sg13cmos5l }
    ~flow:(Flow.librelane ~macros ~overrides operation)
    ~clocks:[{port="clk";period=Time_float.Span.of_ns 20.}]
    ~timing:(if linked then Timing.create ~stages:[first_stage; second_stage]
      ~exceptions:[Timing.Exception.async_capture ~id:"fixture_capture" ~reason:"negative PT7 fixture"
        ~source:(Timing.Port.input "ena") ~first_stage] () else Timing.create ())
    ~pinout:(List.concat_map [Pinout.Bank.Ui; Uo; Uio] ~f:(fun bank ->
      List.init 8 ~f:(fun bit -> {Pinout.bank; bit; description="fixture"})))
    ~source_inputs:(if single then ["test/sram_bundle/sram_bundle_consumer.ml"] else ["generator.ml"])
    ~policy:(Resource_policy.create ~default:Exact [] |> ok_exn) ()
;;

let error_contains result substring =
  match result with
  | Ok _ -> failwith ("expected error: " ^ substring)
  | Error e -> assert (String.is_substring (Error.to_string_hum e) ~substring)
;;

let check_resolution () =
  let resolved = Project.elaborate_for_flow (project () |> ok_exn) |> ok_exn in
  assert (List.length resolved.macros = 2);
  List.iter resolved.macros ~f:(fun m -> assert (String.equal m.instance (Resource.Id.rtl_instance_name m.resource)));
  ignore (Project.elaborate_for_flow (project ~operation:Hardening () |> ok_exn) |> ok_exn);
  error_contains (Project.elaborate_for_flow (project ~macros:[intent "missing" 42.] () |> ok_exn)) "selected macro resource";
  error_contains (Project.elaborate_for_flow (project ~macros:[intent "left" (-1.)] () |> ok_exn)) "target bounds";
  error_contains (Project.elaborate_for_flow (project ~macros:[intent "left" 42.; intent "right" 43.] () |> ok_exn)) "overlap";
  error_contains (project ~macros:[intent "left" 42.; intent "left" 400.] ()) "duplicate macro";
  error_contains (Project.elaborate_for_flow (project ~overrides:[{key="MACROS";value=`Assoc [];reason="try bypass"}] () |> ok_exn)) "conflicts";
  let ep = resolved.elaborated_project in
  let empty = Circuit.create_exn ~name:Design.name
    [Signal.output "uo_out" (Signal.zero 8); Signal.output "uio_out" (Signal.zero 8); Signal.output "uio_oe" (Signal.zero 8)] in
  let malformed = Elaborated_project.Private.create
    ~project_name:(Elaborated_project.project_name ep) ~metadata:(Elaborated_project.metadata ep)
    ~mode:(Elaborated_project.mode ep) ~target:(Elaborated_project.target ep)
    ~flow:(Elaborated_project.flow ep) ~clocks:(Elaborated_project.clocks ep)
    ~pinout:(Elaborated_project.pinout ep) ~timing:(Elaborated_project.timing ep)
    ~resources:(Elaborated_project.resources ep) ~circuit:empty in
  (* Preserve wrapper ports while disconnecting the selected resources. *)
  let malformed = Elaborated_project.Private.create
    ~project_name:(Elaborated_project.project_name malformed) ~metadata:(Elaborated_project.metadata malformed)
    ~mode:(Elaborated_project.mode malformed) ~target:(Elaborated_project.target malformed)
    ~flow:(Elaborated_project.flow malformed) ~clocks:(Elaborated_project.clocks malformed)
    ~pinout:(Elaborated_project.pinout malformed) ~timing:(Elaborated_project.timing malformed)
    ~resources:(Elaborated_project.resources malformed)
    ~circuit:(Circuit.set_phantom_inputs empty Resolved_flow.expected_inputs) in
  error_contains (Resolved_flow.resolve malformed) "actual emitted RTL instance"
;;

let () =
  match Sys.get_argv () with
   | [|_; ("emit-single-physical" | "emit-single-synthesis" | "emit-single-linked" as mode); root; output|] ->
     let physical = not (String.equal mode "emit-single-synthesis") in
     let p = project ~single:true ~linked:(String.equal mode "emit-single-linked") ~macros:[intent "program" 40.]
       ~operation:(if physical then Hardening else Synthesis) () |> ok_exn in
     let inputs = { Target.Inputs.reference_cmos5l_6x4 with
       floorplan_sha256 = Flow.Plan_json.sha256 "floorplan fixture" } in
     Project.emit_bundle ~inputs p ~source_root:root ~output_dir:output |> ok_exn |> ignore
    | [|_; ("emit" | "emit-physical" as mode); root; output|] ->
    check_resolution ();
     let project = project ~operation:(if String.equal mode "emit-physical" then Hardening else Synthesis) () |> ok_exn in
    let a = Project.render_bundle project ~source_root:root |> ok_exn in
    let b = Project.render_bundle project ~source_root:root |> ok_exn in
    assert (String.equal (Bundle.identity a) (Bundle.identity b));
    Bundle.write a ~output_dir:output |> ok_exn
  | [|_; "validate"; bundle|] ->
    let module E = Hardcaml_asic_execution in
    let load p = match E.Result_store.read_json (Filename.concat bundle p) with Ok x -> x | Error _ -> failwith "JSON" in
    let m = load "manifest.json" and c = load "src/config.json" in
    ignore (E.Macro_contract.validate m c);
    (try E.Macro_contract.require_synthesis m; failwith "physical gate missing" with Invalid_argument _ -> ())
   | [|_; "preflight"; bundle; support_tools; pdk_root; python; resources; directory|] ->
    let module E = Hardcaml_asic_execution in
    let report = E.Preflight_service.probe ~bundle ~support_tools ~pdk_root ~python ~resources
      ~environment:(Array.to_list (U.environment ())) ~directory ~native:true () in
     print_endline (E.Identity_json.canonical report)
   | [|_; "postcheck-physical"; run_dir; support_tools; pdk_root; precheck_python; testbench; resources|] ->
     let module E = Hardcaml_asic_execution in
     let o=E.Postcheck_service.run ~run_dir ~support_tools ~pdk_root ~precheck_python ~testbench
       ~testbench_top:"memory_tb" ~resources ~environment:(Array.to_list (U.environment ())) () in
     print_endline (E.Identity_json.canonical (E.Postcheck_service.outcome_json o));
     Stdlib.exit o.exit_code
  | [|_; ("coarse-synthesis" | "coarse-full" as mode); bundle; support_tools; pdk_root; python; resources; runs|] ->
    let module E = Hardcaml_asic_execution in
    let plan = E.Execution_plan.Coarse.resolve (if String.equal mode "coarse-synthesis" then Synthesis else Full) in
    let result = E.Coarse_run_service.run ~bundle ~support_tools ~pdk_root ~python ~resources ~runs ~plan
      ~environment:(Array.to_list (U.environment ())) ~native:(String.equal mode "coarse-synthesis") () in
    print_endline (E.Identity_json.canonical (E.Run_record.outcome_json result));
    Stdlib.exit result.exit_code
  | _ -> failwith "emit ROOT OUTPUT | validate BUNDLE"
