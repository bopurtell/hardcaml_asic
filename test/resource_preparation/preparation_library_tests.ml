module U = Unix
open! Core
open! Hardcaml
open! Hardcaml_asic
module P = Resource_preparation
module J = Elaboration.Preparation_json

let check b message = if not b then failwith message
let ok = Or_error.ok_exn
let success = function
  | Ok x -> x
  | Error f -> failwith (Yojson.Safe.to_string (P.Failure.to_json f))
let failure phase = function
  | Ok _ -> failwith "expected preparation failure"
  | Error (f : P.Failure.t) ->
    check (P.Phase.equal f.phase phase) "wrong failure phase";
    ignore (P.Failure.of_json (P.Failure.to_json f) |> ok : P.Failure.t);
    check (not (List.Assoc.mem (J.fields (P.Failure.to_json f)) "resources" ~equal:String.equal)) "fabricated inventory";
    f
;;
let pinout = List.concat_map [Pinout.Bank.Ui; Uo; Uio] ~f:(fun bank ->
  List.init 8 ~f:(fun bit -> {Pinout.bank; bit; description="test"}))

let project ?(depth = 4) ?(resource = true) ?(invert = false)
    ?(before = ignore) ?(after = ignore) ?(requirement = Resource_policy.Requirement.Flops)
    ?policy ?(tiles = Platform.Harness.Tiny_tapeout.Tiles.T6x4)
    ?(period = 20.) ?(macros = []) ?(source_inputs = ["input.ml"]) () =
  let module D = struct
    module I = Tt_cmos5l.I
    module O = Tt_cmos5l.O
    let name = "tt_um_preparation"
    let create context (i : _ I.t) =
      before context;
      let data = if resource then
        Single_port_ram.create context ~name:"program"
          (Single_port_ram.Config.create_exn ~width:16 ~depth ~read_latency:1)
          ~clock:i.clk ~enable:i.ena ~write_enable:i.rst_n
          ~address:(Signal.sel_bottom i.ui_in ~width:(Int.ceil_log2 depth))
          ~write_data:(Signal.concat_msb [i.ui_in; i.uio_in])
        |> Signal.sel_bottom ~width:8
        else i.ui_in in
      after context;
      { O.uo_out = (if invert then Signal.(~:) data else data)
      ; uio_out = Signal.zero 8; uio_oe = Signal.zero 8 }
  end in
  Project.create ~name:"preparation_test"
    ~metadata:{title="Preparation";author="test";description="Library tests"}
    ~design:(module D)
    ~target:{harness=Tiny_tapeout {tiles};technology=Technology.ihp_sg13cmos5l}
    ~flow:(Flow.librelane ~macros Synthesis) ~clocks:[{port="clk";period=Time_float.Span.of_ns period}]
    ~pinout ~source_inputs
    ~policy:(Option.value policy ~default:(Resource_policy.create ~default:requirement [] |> ok)) () |> ok
;;

let replace j key value = `Assoc (List.map (J.fields j) ~f:(fun (k,v) -> k, if String.equal k key then value else v))
let first_resource snapshot = J.get (Resource_snapshot.to_json snapshot) "resources" |> J.list |> List.hd_exn
let reason r = J.get (J.get r "selection") "reason" |> fun j -> J.get j "kind" |> J.string
let rejected result = check (Or_error.is_error result) "accepted malformed artifact"

let run root =
  let prepare ?on_phase p = Project.prepare_resources ?on_phase p ~source_root:root in
  let phases = ref [] and calls = ref 0 in
  let p = project ~before:(fun _ -> incr calls) () in
  let a = prepare ~on_phase:(fun p -> phases := !phases @ [p]) p |> success in
  check (!calls = 1) "must elaborate exactly once";
  check (List.equal P.Phase.equal !phases
    [Source_before;Construction;Finalization;Resolution;Circuit;Snapshot;Source_after]) "phase order";
  let b = prepare p |> success in
  check (String.equal (P.to_string a) (P.to_string b)) "nondeterministic preparation";
  let explicit_defaults = Project.prepare_resources p ~source_root:root
    ~inputs:Platform.Target.Inputs.reference_cmos5l_6x4 |> success in
  check (String.equal (P.identity a) (P.identity explicit_defaults)) "effective defaults differ";
  let decoded = P.of_string (P.to_string a) |> ok in
  check (String.equal (P.identity decoded) (P.identity a)) "roundtrip identity";
  let r = first_resource (P.resources a) in
  check (String.equal (reason r) "explicit_flops") "explicit flops reason";
  J.expect (J.get r "actual_instances") (J.obj ["status", J.str "not_applicable"]);
  let changed_config = prepare (project ~period:21. ()) |> success in
  check (not (String.equal (P.configuration_identity a) (P.configuration_identity changed_config))) "configuration change not covered";
  let changed_circuit = prepare (project ~invert:true ()) |> success in
  check (String.equal (P.configuration_identity a) (P.configuration_identity changed_circuit)) "fixture declaration should match";
  check (not (String.equal (P.circuit_identity a) (P.circuit_identity changed_circuit))) "actual circuit change not covered";
  check (not (String.equal (P.identity a) (P.identity changed_circuit))) "circuit absent from preparation identity";
  let fallback = prepare (project ~requirement:Exact_or_flop_fallback ()) |> success in
  check (String.equal (reason (first_resource (P.resources fallback))) "flop_fallback") "fallback explanation";
  let macro_name = "RM_IHPSG13_1P_256x16_c2_bm_bist" in
  let policy = Resource_policy.create ~default:Exact_or_flop_fallback ~macro_limits:[macro_name,0] [] |> ok in
  let limited = prepare (project ~depth:256 ~policy ()) |> success in
  let snapshot = P.of_string (P.to_string limited) |> ok |> P.resources in
  let row = first_resource snapshot in
  let selection = J.get row "selection" in
  let why = J.get selection "reason" in
  check (Resource.Selection.Reason.equal
    (Resource_snapshot.records snapshot |> List.hd_exn).selection.reason
    (Budget_exhausted {macro=macro_name;limit=0})) "budget reason roundtrip";
  let reject_row row = rejected (Resource_snapshot.of_json
    (replace (Resource_snapshot.to_json snapshot) "resources" (`List [row]))) in
  reject_row (replace row "requirement" (J.str "exact"));
  List.iter ["macro", J.str " "; "limit", `Int (-1); "limit", `Int 1; "limit", J.str "0"] ~f:(fun (k,v) ->
    reject_row (replace row "selection" (replace selection "reason" (replace why k v))));
  let wrong_macro = replace row "selection" (replace selection "reason" (replace why "macro" (J.str "other"))) in
  let changed = replace (P.to_json limited) "resources"
    (replace (Resource_snapshot.to_json snapshot) "resources" (`List [wrong_macro])) in
  let payload = J.obj (List.filter (J.fields changed) ~f:(fun (k,_) -> not (String.equal k "identity"))) in
  rejected (P.of_json (replace changed "identity" (J.str (J.hash "preparation" payload))));
  let macro = prepare (project ~depth:256 ~requirement:Exact ()) |> success in
  let m = first_resource (P.resources macro) in
  J.expect (J.get (J.get m "actual_instances") "status") (J.str "rtl_verified");
  check (List.length (J.list (J.get m "collateral")) = 11) "macro collateral";
  let intent = Macro_intent.tt_cmos5l_r0 ~resource:(Resource.Id.of_string_exn "program")
    ~x_um:42. ~y_um:81. () in
  let placed = prepare (project ~depth:256 ~requirement:Exact ~macros:[intent] ()) |> success in
  J.expect (J.get (J.get (first_resource (P.resources placed)) "physical_intent") "status")
    (J.str "resolved_intent");
  check (not (String.equal (P.configuration_identity macro) (P.configuration_identity placed))) "intent missing from configuration";
  let module M = Hardcaml_asic_flow_domain in
  let methodology = M.Methodology.create ~id:"prepare" ~label:"Prepare" ~description:"test"
    ~backend_recipe:Librelane_classic_v1 ~endpoints:[Synthesis]
    ~configurable:[Synth_strategy] ~defaults:[M.Parameter.synth_strategy Area_0] |> ok in
  let profile = M.Profile.create ~id:"default" ~label:"Default" ~description:"test"
    ~assignments:[M.Parameter.synth_strategy Delay_0] |> ok in
  let entry = M.Catalog.Entry.create ~methodology ~profiles:[profile] ~default_profile:"default" |> ok in
  let catalog = M.Catalog.create ~entries:[entry] ~default_flow:"prepare" ~default_profile:"default" |> ok in
  let resolved_methodology = M.Catalog.resolve catalog ~endpoint:Synthesis () |> ok in
  let with_methodology = Project.prepare_resources ~methodology:resolved_methodology p ~source_root:root |> success in
  ignore (P.of_string (P.to_string with_methodology) |> ok : P.t);
  check (not (String.equal (P.configuration_identity a) (P.configuration_identity with_methodology))) "methodology not covered";
  Out_channel.write_all (Filename.concat root "catalog.ml") ~data:"catalog one\n";
  let with_catalog () = Project.prepare_resources ~catalog_inputs:["catalog.ml"] p ~source_root:root |> success in
  let first_catalog = with_catalog () in
  Out_channel.write_all (Filename.concat root "catalog.ml") ~data:"catalog two\n";
  let second_catalog = with_catalog () in
  check (not (String.equal (P.source_identity first_catalog) (P.source_identity second_catalog))) "catalog bytes not covered";
  let empty = prepare (project ~resource:false ()) |> success in
  J.expect (J.get (Resource_snapshot.to_json (P.resources empty)) "resources") (`List []);
  ignore (prepare (project ~depth:128 ~requirement:Exact ()) |> failure Construction : P.Failure.t);
  ignore (prepare (project ~tiles:T1x1 ()) |> failure Resolution : P.Failure.t);
  let dead = Resource_policy.create ~default:Flops
    [{ selector=Resource_policy.Selector.instance_exn "missing";requirement=Flops }] |> ok in
  ignore (prepare (project ~policy:dead ()) |> failure Finalization : P.Failure.t);
  let never = ref 0 in
  ignore (prepare (project ~source_inputs:[] ~before:(fun _ -> incr never) ()) |> failure Source_before : P.Failure.t);
  check (!never = 0) "missing source inventory constructed design";
  ignore (prepare (project ~before:(fun _ -> failwith "user construction failure") ()) |> failure Construction : P.Failure.t);
  let mutation () = Out_channel.write_all (Filename.concat root "input.ml") ~data:"changed\n" in
  let f = prepare (project ~after:(fun _ -> mutation ()) ()) |> failure Source_after in
  check (String.equal (List.hd_exn f.diagnostics).code "source_changed") "mutation diagnostic";
  let changed_source = prepare p |> success in
  check (not (String.equal (P.source_identity a) (P.source_identity changed_source))) "source bytes not covered";
  let captured = ref None in
  let callback_project = project ~before:(fun c -> captured := Some c) () in
  let callback_escaped = try
    ignore (prepare ~on_phase:(fun p -> if P.Phase.equal p Finalization then failwith "callback") callback_project);
    false with Failure s -> String.equal s "callback" in
  check callback_escaped "callback exception swallowed";
  rejected (Or_error.try_with (fun () ->
    Elaboration.Context.register_exn (Option.value_exn !captured) ~name:"after_cancellation"
      (Resource.Request.create_exn ~kind:"generic" ~contract:(Sexp.Atom "test"))));
  ignore (prepare ~on_phase:(fun phase -> if P.Phase.equal phase Snapshot then
    Out_channel.write_all (Filename.concat root "input.ml") ~data:"snapshot callback mutation\n") p
    |> failure Source_after : P.Failure.t);
  (* Pure simulation inspection retains the selected macro but has no macro RTL. *)
  let sim = Project.elaborate (project ~depth:256 ~requirement:Exact ()) ~mode:Simulation |> ok
    |> Resource_snapshot.of_elaborated in
  ignore (Resource_snapshot.of_json (Resource_snapshot.to_json sim) |> ok : Resource_snapshot.t);
  let sim_r = first_resource sim in
  check (String.equal (reason sim_r) "exact_mapping") "simulation lost selection";
  J.expect (J.get sim_r "elaborated_as") (J.str "behavioral_model");
  J.expect (J.get sim_r "actual_instances") (J.obj ["status", J.str "not_applicable"]);
  (* Generic kind/contract survives without backend assumptions. *)
  let contract = Sexp.List [Atom "ordered"; List [Atom "z"; Atom "a"]; Atom ""] in
  let generic = project ~resource:false ~before:(fun c ->
    ignore (Elaboration.Context.register_exn c ~name:"generic"
      (Resource.Request.create_exn ~kind:"arbitrary_resource" ~contract) : Resource.Record.t)) () in
  let generic = Project.elaborate generic ~mode:Implementation |> ok |> Resource_snapshot.of_elaborated in
  let generic = Resource_snapshot.of_json (Resource_snapshot.to_json generic) |> ok in
  let encoded = J.get (J.get (first_resource generic) "request") "contract" in
  check (Sexp.equal contract (J.read_sexp encoded)) "contract structure/order lost";
  (* Reader integrity is independent of output key order, but rejects corruption,
     duplicate keys, unsupported fields/versions and semantic inconsistencies. *)
  let json = P.to_json a in
  ignore (P.of_json (`Assoc (List.rev (J.fields json))) |> ok : P.t);
  rejected (P.of_json (replace json "identity" (J.str (String.make 64 '0'))));
  rejected (P.of_json (replace json "schema_version" (`Int 2)));
  rejected (P.of_json (`Assoc (("unexpected", `Null) :: J.fields json)));
  rejected (P.of_string ("{\"identity\":\"duplicate\"," ^ String.drop_prefix (P.to_string a) 1));
  rejected (P.of_string (P.to_string a ^ " {}"));
  rejected (P.of_string ("/* not JSON */" ^ P.to_string a));
  rejected (P.of_string ("{\"bad\":\"\\ud800\",\"payload\":" ^ P.to_string a ^ "}"));
  rejected (P.of_json (replace json "kind" (J.str "unsupported")));
  let bad_resources = Resource_snapshot.to_json (P.resources a)
    |> fun j -> replace j "resources" (`List [replace r "actual_instances"
      (J.obj ["status", J.str "rtl_verified";"instances", `List []])]) in
  rejected (Resource_snapshot.of_json bad_resources);
  let source = J.get json "source" in
  rejected (Source_snapshot.of_json (replace source "identity" (J.str (String.make 64 '0'))));
  rejected (Source_snapshot.observe ~source_root:root ~input_paths:["../input.ml"]);
  let outside = Stdlib.Filename.temp_file "preparation-outside" ".ml" in
  Fun.protect ~finally:(fun () -> U.unlink outside) (fun () ->
    U.symlink outside (Filename.concat root "escape.ml");
    rejected (Source_snapshot.observe ~source_root:root ~input_paths:["escape.ml"]));
  print_endline "resource preparation library tests passed"
;;

let () =
  (* Borrow only committed Git objects; never stage/commit or alter the user's
     checkout. All source mutation fixtures live in this temporary clone. *)
  let cwd = U.getcwd () in
  let channel = U.open_process_args_in "git" [|"git";"-C";cwd;"rev-parse";"--show-toplevel"|] in
  let repo = Stdlib.input_line channel in
  check (Poly.equal (U.close_process_in channel) (U.WEXITED 0)) "locate test checkout";
  let root = Stdlib.Filename.temp_file "preparation-library" "" in
  U.unlink root;
  let pid = U.create_process "git" [|"git";"clone";"--quiet";"--shared";"--no-checkout";repo;root|]
    U.stdin U.stdout U.stderr in
  check (Poly.equal (snd (U.waitpid [] pid)) (U.WEXITED 0)) "create observation fixture";
  let rec remove path =
    match (U.lstat path).st_kind with
    | U.S_DIR -> Stdlib.Sys.readdir path |> Array.iter ~f:(fun n -> remove (Filename.concat path n)); U.rmdir path
    | _ -> U.unlink path in
  Fun.protect ~finally:(fun () -> remove root) (fun () ->
    Out_channel.write_all (Filename.concat root "input.ml") ~data:"initial\n";
    run root)
