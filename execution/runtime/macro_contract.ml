(* Fail-closed execution capability for the public exact SRAM synthesis bundle.
   PDK I/O remains in preflight; this module only validates declared JSON. *)
module J = Identity_json
let fail s = invalid_arg ("SRAM bundle: " ^ s)
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> fail "expected object"
let text = function J.String x -> x | _ -> fail "expected string"
let array = function J.Array xs -> xs | _ -> fail "expected array"
let obj xs = match J.object_ xs with Ok j -> j | Error _ -> fail "invalid object"
let s x = match J.string x with Ok j -> j | Error _ -> fail "invalid string"
let strings xs = J.array (List.map s xs)
let stock = "RM_IHPSG13_1P_256x16_c2_bm_bist"
let stub = "macros/" ^ stock ^ ".blackbox.v"
let prefix = "pdk_dir::libs.ref/sg13cmos5l_sram/"
let corners =
  ["nom_fast_1p32V_m40C", "fast_1p32V_m55C";
   "nom_slow_1p08V_125C", "slow_1p08V_125C";
   "nom_typ_1p20V_25C", "typ_1p20V_25C"]
let path directory suffix = prefix ^ directory ^ "/" ^ stock ^ suffix
let active manifest = match manifest with
  | J.Object xs -> List.mem_assoc "macro_contract" xs
  | _ -> false
let check condition message = if not condition then fail message

(* The resource inventory is the independent elaboration record, not the macro
   contract's own assertion. Parse its sexps structurally; whitespace is irrelevant. *)
let sexp j = try Core.Sexp.of_string (text j) with _ -> fail "invalid resource inventory sexp"
let sexp_field key = function
  | Core.Sexp.List fields ->
    (match List.filter_map (function
       | Core.Sexp.List [Core.Sexp.Atom k;value] when k=key -> Some value
       | _ -> None) fields with
     | [value] -> value | _ -> fail ("missing or duplicate resource field: " ^ key))
  | _ -> fail "invalid resource inventory record"

let registered_macros manifest =
  let module R = Hardcaml_asic.Resource in
  let resources = array (get manifest "resources") in
  let ids = List.map (fun r -> text (get r "id")) resources in
  check (List.length ids=List.length (List.sort_uniq String.compare ids)) "duplicate registered resource";
  List.filter_map (fun r ->
    match sexp_field "implementation" (sexp (get r "selection")) with
    | Core.Sexp.Atom "Flops" -> None
    | Core.Sexp.List [Core.Sexp.Atom "Macro"; Core.Sexp.Atom name] ->
      check (name=stock) "unsupported registered macro";
      check (Core.Sexp.equal (sexp (get r "request"))
        (R.Request.sexp_of_t (R.Request.single_port_ram ~width:16 ~depth:256 ~read_latency:1)))
        "registered macro requires exact256x16 request";
      check (sexp (get r "elaborated_as") = Core.Sexp.Atom "Selected_implementation")
        "registered macro is not the elaborated implementation";
      let id = text (get r "id") in
      let canonical = try R.Id.of_string_exn id |> R.Id.rtl_instance_name
        with _ -> fail "invalid registered macro resource ID" in
      Some (id, canonical)
    | _ -> fail "invalid registered resource implementation") resources

let generated_physical manifest =
  let module P = Hardcaml_asic.Elaboration.Macro_physical_plan in
  let macros = array (get (get manifest "macro_contract") "macros") |> List.map (fun m ->
    let intent=get m "physical_intent" in
    let number = function J.Float x when Float.is_finite x -> x | _ -> fail "invalid physical coordinate" in
    let x,y=match array (get intent "location_um") with [x;y] -> number x,number y | _ -> fail "physical location" in
    let resource=Hardcaml_asic.Resource.Id.of_string_exn (text (get m "resource")) in
    P.{resource;instance=text (get m "instance");name=text (get m "module");
       intent=Hardcaml_asic.Macro_intent.tt_cmos5l_r0 ~resource ~x_um:x ~y_um:y
         ~halo_um:(number (get intent "halo_um")) ()}) in
  Core.Or_error.ok_exn (P.create macros)

let physical_files manifest =
  Hardcaml_asic.Elaboration.Macro_physical_plan.files (generated_physical manifest)

let validate manifest config =
  let macros = get config "MACROS" in
  if not (active manifest) then (
    check (macros=J.null || macros=obj []) "MACROS require a public macro contract";
    []) else (
    let contract = get manifest "macro_contract" in
    let version n = match J.integer n with Ok j -> j | Error _ -> assert false in
    let physical = get contract "schema_version"=version "2" && get contract "execution"=s "fresh_full" in
    check (physical || (get contract "schema_version"=version "1" && get contract "execution"=s "synthesis_only")) "unsupported contract schema/execution";
    check (get manifest "operation"=s (if physical then "Hardening" else "Synthesis")) "operation differs from macro capability";
    check (get manifest "flow_configuration" = config) "configuration differs from manifest";
    check (get config "PDK" = s "ihp-sg13cmos5l") "unsupported PDK";
    check (get config "STA_CORNERS" = strings (List.map fst corners)) "exact STA corner set required";
    check (get config "DEFAULT_CORNER" = s "nom_typ_1p20V_25C") "unexpected default corner";
    check (get config "EXTRA_VERILOG_MODELS" = strings ["dir::../" ^ stub]) "generated synthesis stub required";
    check (get contract "blackbox_sources" = strings [stub]) "unexpected blackbox inventory";
    let macro = get macros stock in
    check (match macros with J.Object [name,_] -> name=stock | _ -> false) "only stock exact256x16 is supported";
    check (match macro with J.Object xs -> List.map fst xs = ["gds";"instances";"lef";"lib";"spice"] | _ -> false)
      "unexpected macro configuration fields";
    List.iter (fun (key,value) -> check (get macro key=value) ("incorrect macro " ^ key))
      ["lef",strings [path "lef" ".lef"];"gds",strings [path "gds" ".gds"];
       "spice",strings [path "cdl" ".cdl"];
       "lib",obj (List.map (fun (corner,suffix) -> corner,strings [path "lib" ("_"^suffix^".lib")]) corners)];
    let entries = array (get contract "macros") in
    check (entries<>[]) "empty macro inventory";
    let registered = registered_macros manifest in
    let resources = List.map (fun m -> text (get m "resource")) entries in
    check (List.sort String.compare resources = List.sort String.compare (List.map fst registered))
      "macro resources differ from registered exact-SRAM inventory";
    let instances = List.map (fun m -> text (get m "instance")) entries in
    check (List.length (List.sort_uniq String.compare instances)=List.length entries) "duplicate instance";
    List.iter (fun m ->
      let resource = text (get m "resource") in
      check (get m "instance"=s (List.assoc resource registered)) "macro instance differs from canonical resource encoding") entries;
    let placements = List.filter_map (fun m ->
      let intent = get m "physical_intent" in
      if intent=J.null then None else (
        check (get intent "orientation"=s "R0") "unsupported macro intent orientation";
        let location = get intent "location_um" in
        check (match location with J.Array [J.Float x;J.Float y] -> Float.is_finite x && Float.is_finite y
          | _ -> false) "invalid macro intent location";
        Some (text (get m "instance"),obj ["location",location;"orientation",s "R0"]))) entries in
    check (get macro "instances"=obj placements) "configuration placements differ from declared macro intent inventory";
    let references = List.concat_map (fun m ->
      check (get m "module"=s stock) "unknown module";
      let refs = List.map (fun c -> text (get c "role"),text (get c "reference")) (array (get m "collateral")) in
      check (List.length refs=11) "unexpected collateral roles";
      let require role reference = check (List.filter (fun (r,_) -> r=role) refs = [role,reference]) ("incorrect collateral " ^ role) in
      require "Black_box" ("generated:" ^ stock ^ ".blackbox.v");
      require "Physical_abstract" (path "lef" ".lef");
      require "Physical_layout" (path "gds" ".gds");
      require "Spice_netlist" (path "cdl" ".cdl");
      List.iter (fun (corner,suffix) ->
        let tag = if String.starts_with ~prefix:"nom_fast" corner then "fast"
          else if String.starts_with ~prefix:"nom_slow" corner then "slow" else "typ" in
        require ("(Timing_model (corner " ^ tag ^ "))") (path "lib" ("_"^suffix^".lib"))) corners;
      List.iter (fun role ->
        let actual = List.filter_map (fun (r,p) -> if r=role then Some p else None) refs |> List.sort String.compare in
        let expected = [path "verilog" ".v";prefix ^ "verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v"] |> List.sort String.compare in
        check (actual=expected) ("incomplete vendor model set: " ^ role)) ["Simulation_model";"Timed_simulation_model"];
      List.filter_map (fun (role,reference) ->
        if String.starts_with ~prefix:"generated:" reference then None else (
          check (String.starts_with ~prefix reference) "unsupported collateral reference";
          Some (role,reference))) refs) entries in
    let models = [path "verilog" ".v"; prefix ^ "verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v"] |> List.sort String.compare in
    let sim = get contract "implementation_simulation" in
    check (get sim "defines"=strings ["FUNCTIONAL"] && get sim "models"=strings models)
      "functional implementation model set required";
    if physical then (
      let module P = Hardcaml_asic.Elaboration.Macro_physical_plan in
      let generated=generated_physical manifest in
      let convert value=match J.parse (Yojson.Safe.to_string value) with Ok j -> j | Error _ -> fail "generated physical JSON" in
      let plan = get contract "physical_plan" in
      check (plan=convert (P.to_json generated)) "physical plan differs from canonical generation";
      List.iter (fun (key,value) -> check (get config key=convert value) ("physical setting differs from canonical generation: " ^ key)) (P.settings generated);
      check (get plan "schema_version"=version "1") "unsupported physical plan schema";
      List.iter (fun (key,value) -> check (get plan key=s value) ("unsupported physical plan " ^ key))
        ["strategy","cmos5l_m4_edge_access_v1";"plan_file","physical/macro-plan.json";
         "pdn_tcl","physical/pdn.tcl";"checking_profile","cmos5l_real_gds_v1";
         "lvs_scope","abstract_macro_boundary_with_schematic_cdl"];
      check (List.length placements=List.length entries) "full execution requires every macro placement";
      List.iter (fun key -> check (List.exists (fun f -> get f "path"=s key) (array (get manifest "files"))) ("missing physical input " ^ key))
        ["physical/macro-plan.json";"physical/pdn.tcl"];
      List.iter (fun key -> check (get config key=J.bool true) ("physical checker must remain enabled: " ^ key))
        ["RUN_KLAYOUT_DRC";"RUN_MAGIC_DRC";"ERROR_ON_ILLEGAL_OVERLAPS"];
      List.iter (fun key -> check (get config key=J.null || get config key=J.bool true) ("physical checker cannot be disabled: " ^ key))
        ["ERROR_ON_KLAYOUT_DRC";"ERROR_ON_LVS_ERROR";"ERROR_ON_PDN_VIOLATIONS";"ERROR_ON_DISCONNECTED_PINS";"ERROR_ON_TR_DRC"];
      check (get config "PRIMARY_GDSII_STREAMOUT_TOOL"=s "klayout") "real GDS must use KLayout streamout";
      check (get config "EXTRA_SPICE_MODELS"=strings [path "cdl" ".cdl"]) "schematic CDL consumption required";
      check (get config "PDN_CFG"=s "dir::../physical/pdn.tcl") "physical PDN input required";
      check (get config "KLAYOUT_DRC_RUNSET"=s "pdk_dir::libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc") "pinned real-GDS deck required";
      let options = obj ((List.map (fun key -> key,s "false")
        ["no_feol";"no_beol";"no_offgrid";"no_angle";"no_pin";"no_forbidden";"no_recommended";"precheck_drc"])
        @ ["log",s "drc-klayout.log";"threads",version "6"]) in
      check (get config "KLAYOUT_DRC_OPTIONS"=options) "exact textual deck options and six threads required";
      let expected = List.map (fun m ->
        let intent=get m "physical_intent" in
        let number = function J.Float x when Float.is_finite x -> x | _ -> fail "invalid physical coordinate/halo" in
        let halo=number (get intent "halo_um") in
        let aligned x = abs_float (x *. 200. -. Float.round (x *. 200.)) < 1e-7 in
        check (halo>=2. && aligned halo) "invalid physical halo";
        List.iter (fun coordinate -> let x=number coordinate in check (x>=0. && aligned x) "off-grid physical placement") (array (get intent "location_um"));
        check (get intent "power"=obj ["VDD!",s "VPWR";"VDDARRAY!",s "VPWR";"VSS!",s "VGND"]) "unsupported macro supply mapping";
        obj ["resource",get m "resource";"instance",get m "instance";"module",get m "module";
          "location_um",get intent "location_um";"orientation",get intent "orientation";
          "halo_um",get intent "halo_um";"supplies",get intent "power"]) entries in
      check (get plan "macros"=J.array expected) "physical plan differs from macro inventory";
      let halos=List.map (fun m -> get (get m "physical_intent") "halo_um") entries |> List.sort_uniq compare in
      check (List.length halos=1) "common physical halo required";
      check (get config "MAGIC_EXT_ABSTRACT_CELLS"=strings [stock]) "explicit abstract macro LVS scope required";
      check (get config "MAGIC_DRC_USE_GDS"=J.bool false) "abstract Magic diagnostic scope required");
    List.sort_uniq compare references)

let physical manifest = active manifest && get (get manifest "macro_contract") "execution"=s "fresh_full"

let require_full manifest =
  if active manifest then (
    check (physical manifest) "physical execution is unsupported for synthesis-only bundles";
    ignore (validate manifest (get manifest "flow_configuration")))

let require_synthesis manifest =
  if active manifest then fail "legacy synthesis-only gate does not admit SRAM checkpoints; use schema2 Hardening segmented admission through Timing, or fresh coarse execution"

(* sram_segmented1 is deliberately narrower than coarse fresh_full. The bundle
   contract stays unchanged; execution capability is selected by the provider. *)
let segmented_profile = "stock256x16_r0_through_timing_v1"
let require_segmented manifest =
  if active manifest then (
    check (physical manifest) "segmented SRAM requires schema2 Hardening; schema1 physical continuation is unsupported";
    ignore (validate manifest (get manifest "flow_configuration"));
    check (List.length (array (get (get manifest "macro_contract") "macros"))=1)
      "segmented SRAM supports exactly one stock256x16 R0 macro";
    check (get manifest "timing_intent"=J.null && get manifest "timing_native_coverage"=J.null &&
      get manifest "timing_physical_qualification"=J.null)
      "segmented SRAM does not support PT7 linked timing intent or exceptions")

let require_segmented_plan manifest plan =
  require_segmented manifest;
  if active manifest then match plan with
    | Execution_plan.Stages p ->
      check (not (List.mem Execution_plan.Stage.Signoff (Execution_plan.Stage_plan.sequence p)))
        "segmented SRAM Signoff is unsupported; maximum endpoint is Timing";
      check (Execution_plan.Stage_plan.source_boundary p <> Some Execution_plan.Stage.Signoff)
        "segmented SRAM cannot resume Signoff"
    | _ -> fail "segmented SRAM requires a stage recipe"
