(* Pure named-plan admission for the same bounded physical macro profile. This
   reconstructs provider-owned settings, rather than trusting generated_settings
   as an escape hatch for arbitrary paths or project overrides. No PDK reads. *)
open! Core
module J = Flow.Plan_json
let generated manifest =
  match List.Assoc.find (match manifest with `Assoc xs -> xs | _ -> []) "macro_contract" ~equal:String.equal with
  | None -> []
  | Some contract ->
    let require condition message = if not condition then J.fail ("named SRAM: " ^ message) in
    require (J.equal (J.get contract "schema_version") (`Int 2) &&
      J.equal (J.get contract "execution") (`String "fresh_full") &&
      J.equal (J.get manifest "operation") (`String "Hardening")) "requires schema2 Hardening";
    require (J.equal (J.get (J.get manifest "target") "technology") (`String "ihp-sg13cmos5l") &&
      J.equal (J.get (J.get manifest "flow_configuration") "PDK") (`String "ihp-sg13cmos5l")) "requires CMOS5L";
    let stock = "RM_IHPSG13_1P_256x16_c2_bm_bist" in
    let macro = match J.array (J.get contract "macros") with [m] -> m | _ -> J.fail "named SRAM supports exactly one macro" in
    require (String.equal (J.string (J.get macro "module")) stock) "requires stock256x16";
    let resource=Resource.Id.of_string_exn (J.string (J.get macro "resource")) in
    let instance=Resource.Id.rtl_instance_name resource in
    require (String.equal (J.string (J.get macro "instance")) instance) "canonical instance required";
    let registered = List.filter (J.array (J.get manifest "resources")) ~f:(fun r ->
      String.equal (J.string (J.get r "id")) (Resource.Id.to_string resource)) in
    (match registered with
     | [r] ->
       require (Sexp.equal (Sexp.of_string (J.string (J.get r "request")))
         (Resource.Request.sexp_of_t (Resource.Request.single_port_ram ~width:16 ~depth:256 ~read_latency:1))) "exact request required";
       require (Sexp.equal (Sexp.of_string (J.string (J.get r "elaborated_as"))) (Sexp.Atom "Selected_implementation")) "implementation RTL required";
       let selection=Sexp.of_string (J.string (J.get r "selection")) in
       require (match selection with Sexp.List fields -> List.exists fields ~f:(Sexp.equal
         (Sexp.List [Sexp.Atom "implementation";Sexp.List [Sexp.Atom "Macro";Sexp.Atom stock]])) | _ -> false) "selected stock macro required"
     | _ -> J.fail "named SRAM resource inventory mismatch");
    let intent=J.get macro "physical_intent" in
    require (J.equal (J.get intent "orientation") (`String "R0")) "R0 required";
    let number = function `Float x when Float.is_finite x -> x | _ -> J.fail "named SRAM finite coordinates required" in
    let x_um,y_um=match J.array (J.get intent "location_um") with [x;y] -> number x,number y | _ -> J.fail "named SRAM location required" in
    let physical=J.checked (Macro_physical_plan.create
      [{Macro_physical_plan.resource;instance;name=stock;
        intent=Flow.Macro_intent.tt_cmos5l_r0 ~resource ~x_um ~y_um ~halo_um:(number (J.get intent "halo_um")) ()}]) in
    require (J.equal (J.get contract "physical_plan") (Macro_physical_plan.to_json physical)) "canonical physical plan required";
    List.iter (Macro_physical_plan.files physical) ~f:(fun (path,bytes) ->
      let matching=List.filter (J.array (J.get manifest "files")) ~f:(fun f ->
        String.equal (J.string (J.get f "path")) path) in
      require (match matching with [f] -> String.equal (J.string (J.get f "sha256")) (J.sha256 bytes) | _ -> false)
        ("canonical physical input required: " ^ path));
    let supplies=`Assoc ["VDD!",`String "VPWR";"VDDARRAY!",`String "VPWR";"VSS!",`String "VGND"] in
    require (J.equal (J.get intent "power") supplies) "supported supply mapping required";
    let path dir suffix=`String ("pdk_dir::libs.ref/sg13cmos5l_sram/" ^ dir ^ "/" ^ stock ^ suffix) in
    let corners=["nom_fast_1p32V_m40C","fast_1p32V_m55C";"nom_slow_1p08V_125C","slow_1p08V_125C";"nom_typ_1p20V_25C","typ_1p20V_25C"] in
    ["MACROS",`Assoc [stock,`Assoc [
      "instances",`Assoc [instance,`Assoc ["location",J.get intent "location_um";"orientation",`String "R0"]];
      "lef",`List [path "lef" ".lef"];"gds",`List [path "gds" ".gds"];"spice",`List [path "cdl" ".cdl"];
      "lib",`Assoc (List.map corners ~f:(fun (corner,suffix) -> corner,`List [path "lib" ("_" ^ suffix ^ ".lib")]))]];
     "EXTRA_VERILOG_MODELS",`List [`String ("dir::../macros/" ^ stock ^ ".blackbox.v")];
     "STA_CORNERS",`List (List.map corners ~f:(fun (c,_) -> `String c));
     "DEFAULT_CORNER",`String "nom_typ_1p20V_25C"] @ Macro_physical_plan.settings physical
