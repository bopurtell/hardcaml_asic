module U = Unix
open! Core
open! Hardcaml
open! Hardcaml_asic
module J = Elaboration.Preparation_json
module PJ = Flow.Plan_json
module MP = Methodology_plan

let ok = Or_error.ok_exn
let assertions = ref 0
let check b message = incr assertions; if not b then failwith message
let reject r = check (Or_error.is_error r) "accepted invalid manifest/plan"
let set j key value = `Assoc (List.map (J.fields j) ~f:(fun (k,v) -> k, if String.equal k key then value else v))
let remove j key = `Assoc (List.filter (J.fields j) ~f:(fun (k,_) -> not (String.equal k key)))
let resign m = set m "identity" (`String (J.sha256 (Yojson.Safe.to_string (remove m "identity"))))
let snapshot m = J.get m "resource_snapshot"
let change_snapshot m f = set m "resource_snapshot" (f (snapshot m))
let change_first s f = set s "resources" (`List (match J.list (J.get s "resources") with
  | r :: rest -> f r :: rest | [] -> failwith "empty fixture"))
let verified m = ignore (ok (Bundle.verify_manifest_identity m) : unit); incr assertions

(* Read declared inputs from the real checkout, including when Dune runs in
   _build/default. No fixture commits or source mutations are necessary. *)
let rec source_root path =
  if Stdlib.Sys.file_exists (Filename.concat path ".git") then path
  else let parent = Filename.dirname path in
    if String.equal parent path then failwith "test requires a git checkout"
    else source_root parent

let project ?(depth=4) ?(count=1) ?(requirement=Resource_policy.Requirement.Flops)
    ?policy ?(macros=[]) ?(within=false) ?(calls=ref 0) () =
  let module D = struct
    module I = Tt_cmos5l.I
    module O = Tt_cmos5l.O
    let name = "tt_um_resource_bundle"
    let create context (i : _ I.t) =
      incr calls;
      let context = if within then Elaboration.Context.in_scope context
        (Scope.sub_scope (Elaboration.Context.scope context) "bank") else context in
      let data = List.init count ~f:(fun n ->
        Single_port_ram.create context ~name:("program" ^ Int.to_string n)
          (Single_port_ram.Config.create_exn ~width:16 ~depth ~read_latency:1)
          ~clock:i.clk ~enable:i.ena ~write_enable:i.rst_n
          ~address:(Signal.sel_bottom i.ui_in ~width:(Int.ceil_log2 depth))
          ~write_data:(Signal.concat_msb [i.ui_in; i.uio_in])
        |> Signal.sel_bottom ~width:8)
        |> List.fold ~init:i.ui_in ~f:Signal.( ^: ) in
      { O.uo_out=data; uio_out=Signal.zero 8; uio_oe=Signal.zero 8 }
  end in
  Project.create ~name:"resource_bundle_test"
    ~metadata:{title="Resource bundle";author="test";description="PT.5 library fixture"}
    ~design:(module D)
    ~target:{harness=Tiny_tapeout {tiles=T6x4};technology=Technology.ihp_sg13cmos5l}
    ~flow:(Flow.librelane ~macros Synthesis)
    ~clocks:[{port="clk";period=Time_float.Span.of_ns 20.}]
    ~pinout:(List.concat_map [Pinout.Bank.Ui; Uo; Uio] ~f:(fun bank ->
      List.init 8 ~f:(fun bit -> {Pinout.bank;bit;description="test"})))
    ~source_inputs:["lib/dune"]
    ~policy:(Option.value policy ~default:(ok (Resource_policy.create ~default:requirement []))) () |> ok

let catalog ?(endpoints=[Flow.Stage.Synthesis]) () =
  let module M = Hardcaml_asic_flow_domain in
  let methodology = M.Methodology.create ~id:"bundle" ~label:"Bundle" ~description:"test"
    ~backend_recipe:Librelane_classic_v1 ~endpoints
    ~configurable:[Synth_strategy] ~defaults:[M.Parameter.synth_strategy Area_0] |> ok in
  let profile = M.Profile.create ~id:"default" ~label:"Default" ~description:"test" ~assignments:[] |> ok in
  let entry = M.Catalog.Entry.create ~methodology ~profiles:[profile] ~default_profile:"default" |> ok in
  M.Catalog.create ~entries:[entry] ~default_flow:"bundle" ~default_profile:"default" |> ok

let plan_with_manifest wire manifest =
  let wire = set (set wire "bundle_identity" (J.get manifest "identity"))
    "bundle_manifest" (`String (Yojson.Safe.to_string manifest)) in
  set wire "plan_identity" (`String (ok (PJ.identity ~contract:"hardcaml-asic.plan-identity"
    (remove wire "plan_identity"))))

let run root store =
  let render ?(include_simulation=false) p =
    Project.render_result ~include_simulation p ~source_root:root |> ok in
  let calls = ref 0 in
  let rendered = render (project ~calls ()) in
  let b = Project.Rendered.bundle rendered in
  let m = Bundle.manifest b in
  check (!calls = 1) "bundle independently elaborated";
  check (PJ.equal (snapshot m) (Resource_snapshot.to_json
    (Resource_snapshot.of_resolved (Project.Rendered.resolution rendered)))) "snapshot not from rendering resolution";
  verified m;
  let again = Bundle.render ~source_root:root ~input_paths:["lib/dune"]
    (Project.Rendered.resolution rendered) |> ok in
  check (!calls = 1 && String.equal (Bundle.identity b) (Bundle.identity again)) "rerender changed resolution/identity";
  let capped = Resource_policy.create ~default:Exact_or_flop_fallback
    ~macro_limits:["RM_IHPSG13_1P_256x16_c2_bm_bist",1] [] |> ok in
  List.iter [project ~count:0 (); project ~count:2 (); project ~requirement:Exact_or_flop_fallback ();
    project ~depth:256 ~count:2 ~policy:capped ();
    project ~depth:256 ~requirement:Exact (); project ~depth:256 ~count:2 ~requirement:Exact ()]
    ~f:(fun p -> let r = render p in
      let m = Bundle.manifest (Project.Rendered.bundle r) in verified m;
      check (List.equal Resource.Record.equal
        (Resource_snapshot.records (ok (Resource_snapshot.of_json (snapshot m))))
        (Elaborated_project.resources (Project.Rendered.resolution r).elaborated_project)) "record projection lost inventory");
  let instance_policy = Resource_policy.create ~default:Flops
    [{selector=Resource_policy.Selector.instance_exn "program0";requirement=Exact_or_flop_fallback}] |> ok in
  let subtree_policy = Resource_policy.create
    [{selector=Resource_policy.Selector.subtree_exn "bank";requirement=Flops}] |> ok in
  verified (Bundle.manifest (Project.Rendered.bundle (render (project ~policy:instance_policy ()))));
  verified (Bundle.manifest (Project.Rendered.bundle (render (project ~policy:subtree_policy ~within:true ()))));
  let fallback = Bundle.manifest (Project.Rendered.bundle (render (project ~requirement:Exact_or_flop_fallback ()))) in
  check (not (PJ.equal (snapshot m) (snapshot fallback))) "policy change lost";
  check (not (PJ.equal (J.get m "identity") (J.get fallback "identity"))) "policy absent from identity";
  let legacy = resign (remove m "resource_snapshot") in
  verified legacy;
  check (not (PJ.equal (J.get legacy "identity") (J.get m "identity"))) "snapshot absent from identity";
  check (not (List.Assoc.mem (J.fields legacy) "resource_snapshot" ~equal:String.equal)) "legacy silently upgraded";
  reject (Bundle.verify_manifest_identity (change_snapshot m (fun s -> set s "project" (`String "tampered"))));
  List.iter
    [ (fun s -> set s "schema_version" (`Int 2))
    ; (fun s -> `Assoc (("unknown", `Null) :: J.fields s))
    ; (fun s -> `Assoc (("schema_version", `Int 1) :: J.fields s))
    ; (fun s -> set s "project" (`String "other"))
    ; (fun s -> set s "top" (`String "other"))
    ; (fun s -> set s "scope" (`String "elaborated"))
    ; (fun s -> set s "resources" (`List []))
    ; (fun s -> set s "resources" (`List (J.list (J.get s "resources") @ J.list (J.get s "resources"))))
    ; (fun s -> change_first s (fun r -> set r "requirement" (`String "exact")))
    ; (fun _ -> `Null)
    ; (fun _ -> snapshot fallback)
    ] ~f:(fun f -> reject (Bundle.verify_manifest_identity (resign (change_snapshot m f))));
  let macro_project = project ~depth:256 ~requirement:Exact ~calls () in
  let before = !calls in
  let sim_rendered = render ~include_simulation:true macro_project in
  check (!calls = before + 2) "simulation/implementation elaboration count";
  let mm = Bundle.manifest (Project.Rendered.bundle sim_rendered) in
  verified mm;
  let row = J.list (J.get (snapshot mm) "resources") |> List.hd_exn in
  J.expect (J.get row "elaborated_as") (`String "selected_implementation");
  J.expect (J.get (J.get row "actual_instances") "status") (`String "rtl_verified");
  let simulation = Project.elaborate macro_project ~mode:Simulation |> ok in
  let sim_snapshot = Resource_snapshot.of_elaborated simulation |> Resource_snapshot.to_json in
  let sim_row = J.list (J.get sim_snapshot "resources") |> List.hd_exn in
  J.expect (J.get sim_row "elaborated_as") (`String "behavioral_model");
  J.expect (J.get (J.get sim_row "actual_instances") "status") (`String "not_applicable");
  check (String.is_substring (Bundle.file (Project.Rendered.bundle sim_rendered) "simulation/tt_um_resource_bundle.v" |> Option.value_exn)
    ~substring:"module") "missing simulation RTL";
  reject (Bundle.verify_manifest_identity (resign (set mm "resource_snapshot" sim_snapshot)));
  reject (Bundle.verify_manifest_identity (resign (remove mm "macro_contract")));
  let bad_macro = change_snapshot mm (fun s -> change_first s (fun r ->
    set r "actual_instances" (J.obj ["status", `String "rtl_verified"; "instances", `List [
      J.obj ["module", `String "wrong"; "instance", `String "renamed"]]]))) in
  reject (Bundle.verify_manifest_identity (resign bad_macro));
  List.iter
    [ (fun macros -> List.map macros ~f:(fun m -> set m "instance" (`String "renamed")))
    ; (fun macros -> List.map macros ~f:(fun m -> set m "module" (`String "wrong_type")))
    ; (fun macros -> macros @ macros)
    ] ~f:(fun f ->
      let contract = J.get mm "macro_contract" in
      let contract = set contract "macros" (`List (f (J.list (J.get contract "macros")))) in
      reject (Bundle.verify_manifest_identity (resign (set mm "macro_contract" contract))));
  let change_macro f =
    let contract = J.get mm "macro_contract" in
    let macros = J.list (J.get contract "macros") in
    set mm "macro_contract" (set contract "macros" (`List (match macros with
      | first :: rest -> f first :: rest | [] -> failwith "empty macro fixture"))) |> resign in
  let change_collateral f = change_macro (fun m -> set m "collateral" (f (J.list (J.get m "collateral")))) in
  let change_first_collateral f = function
    | first :: rest -> `List (f first :: rest)
    | [] -> failwith "empty collateral fixture" in
  List.iter
    [ change_collateral (change_first_collateral (fun c -> set c "reference" (`String "wrong/reference.lib")))
    ; change_collateral (fun _ -> `List [])
    ; change_collateral (change_first_collateral (fun c -> set c "role"
        (`String (if String.equal (J.string (J.get c "role")) "Black_box" then "Physical_layout" else "Black_box"))))
    ; change_collateral (fun cs -> `List (List.map cs ~f:(fun c ->
        if String.is_substring (J.string (J.get c "role")) ~substring:"Timing_model"
        then set c "role" (`String (Sexp.to_string_hum
          (Resource.Technology.Collateral.Role.sexp_of_t (Timing_model {corner="wrong_corner"}))))
        else c)))
    ; change_collateral (fun cs -> `List (List.tl_exn cs))
    ; change_collateral (fun cs -> `List (List.hd_exn cs :: cs))
    ; change_macro (fun m -> remove m "collateral")
    ] ~f:(fun changed -> reject (Bundle.verify_manifest_identity changed));
  verified (change_collateral (fun cs -> `List (List.rev cs)));
  verified (resign (remove mm "resource_snapshot"));
  let intent = Macro_intent.tt_cmos5l_r0 ~resource:(Resource.Id.of_string_exn "program0")
    ~x_um:42. ~y_um:81. () in
  let placed = render (project ~depth:256 ~requirement:Exact ~macros:[intent] ()) in
  let placed_manifest = Bundle.manifest (Project.Rendered.bundle placed) in
  verified placed_manifest;
  let invalid_intent = change_snapshot placed_manifest (fun s -> change_first s (fun r ->
    set r "physical_intent" (set (J.get r "physical_intent") "halo_um" (`Int (-1))))) in
  reject (Bundle.verify_manifest_identity (resign invalid_intent));
  let conflicting_intent = change_snapshot placed_manifest (fun s -> change_first s (fun r ->
    set r "physical_intent" (set (J.get r "physical_intent") "location_um" (`List [`Float 43.; `Float 81.])))) in
  reject (Bundle.verify_manifest_identity (resign conflicting_intent));
  let methodology = Hardcaml_asic_flow_domain.Catalog.resolve (catalog ()) ~endpoint:Synthesis () |> ok in
  let named_macro = Project.render_result ~methodology ~include_simulation:false macro_project ~source_root:root |> ok in
  let single = MP.of_rendered named_macro |> ok in
  let wire = MP.to_yojson single in
  let macro_manifest = Bundle.manifest (Project.Rendered.bundle named_macro) in
  let modify key value = resign (set macro_manifest key value) in
  let contract = J.get macro_manifest "macro_contract" in
  let stub_path = "macros/RM_IHPSG13_1P_256x16_c2_bm_bist.blackbox.v" in
  List.iter [ resign (remove macro_manifest "resource_snapshot")
    ; modify "operation" (`String "Hardening")
    ; modify "macro_contract" (set contract "execution" (`String "fresh_full"))
    ; modify "macro_contract" (set contract "blackbox_sources" (`List [`String "inputs/lib/dune"]))
    ; modify "files" (`List (List.map (J.list (J.get macro_manifest "files")) ~f:(fun file ->
        if PJ.equal (J.get file "path") (`String stub_path)
        then set file "sha256" (`String (PJ.sha256 "different stub")) else file))) ]
    ~f:(fun tampered -> reject (MP.of_yojson (plan_with_manifest wire tampered)));
  let staged = catalog ~endpoints:[Synthesis; Floorplan] () in
  reject (MP.prepare ~catalog:staged ~endpoint:Floorplan macro_project ~source_root:root);
  reject (MP.prepare ~catalog:staged ~endpoint:Synthesis
    (project ~depth:256 ~requirement:Exact ~macros:[intent] ()) ~source_root:root);
  List.iter [project ~depth:256 ~count:2 ~requirement:Exact ();
             project ~depth:256 ~count:3 ~policy:capped ()] ~f:(fun project ->
    let p = MP.prepare ~catalog:staged ~endpoint:Synthesis project ~source_root:root |> ok in
    let plan = MP.Prepared.plan p in
    check (String.equal (MP.plan_identity plan) (MP.plan_identity (ok (MP.of_string (MP.to_string plan)))))
      "fresh schema1 multi-bank synthesis must round-trip");
  let prepared = MP.prepare ~catalog:(catalog ()) ~endpoint:Synthesis (project ()) ~source_root:root |> ok in
  let plan = MP.Prepared.plan prepared and bundle = MP.Prepared.bundle prepared in
  let wire = MP.to_yojson plan in
  let decoded = MP.of_string (MP.to_string plan) |> ok in
  check (String.equal (MP.bundle_identity decoded) (Bundle.identity bundle)) "methodology roundtrip";
  let publication = Bundle.publish bundle ~store_dir:store |> ok in
  let reused = Bundle.publish bundle ~store_dir:store |> ok in
  check (not publication.reused && reused.reused) "publication/reuse";
  let disk = In_channel.read_all (Filename.concat publication.directory "manifest.json") |> Yojson.Safe.from_string in
  check (PJ.equal disk (Bundle.manifest bundle)) "published different bundle";
  verified disk;
  let legacy_plan_manifest = resign (remove (Bundle.manifest bundle) "resource_snapshot") in
  let legacy_wire = plan_with_manifest wire legacy_plan_manifest in
  let legacy_plan = MP.of_yojson legacy_wire |> ok in
  check (PJ.equal (MP.to_yojson legacy_plan) legacy_wire) "legacy plan rewritten";
  let tampered = resign (change_snapshot (Bundle.manifest bundle) (fun s -> set s "resources" (`List []))) in
  reject (MP.of_yojson (plan_with_manifest wire tampered));
  Out_channel.write_all (Filename.concat publication.directory "manifest.json") ~data:"{}\n";
  reject (Bundle.publish bundle ~store_dir:store);
  printf "resource realization bundle: %d assertions passed (no EDA)\n" !assertions

let () =
  let root = source_root (U.getcwd ()) in
  if Poly.equal (Sys.get_argv ()) [| (Sys.get_argv ()).(0); "inspection-fixture-plan" |] then (
    let policy = Resource_policy.create ~default:Exact_or_flop_fallback
      ~macro_limits:["RM_IHPSG13_1P_256x16_c2_bm_bist",2] [] |> ok in
    let prepared = MP.prepare ~catalog:(catalog ()) ~endpoint:Synthesis
      (project ~count:3 ~depth:256 ~policy ()) ~source_root:root |> ok in
    print_endline (MP.to_string (MP.Prepared.plan prepared)); Stdlib.exit 0);
  let temp = Stdlib.Filename.temp_file "asic-resource-bundle-" "" in
  U.unlink temp; U.mkdir temp 0o700;
  Exn.protect ~f:(fun () -> run root (Filename.concat temp "store"))
    ~finally:(fun () ->
      let rec remove path = match (U.lstat path).U.st_kind with
        | U.S_DIR -> Array.iter (Stdlib.Sys.readdir path) ~f:(fun n -> remove (Filename.concat path n)); U.rmdir path
        | _ -> U.unlink path in
      remove temp)
