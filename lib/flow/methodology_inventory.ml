open! Core
module J = Plan_json

let safe_path path =
  if String.is_empty path
     || not (Filename.is_relative path)
     || String.exists path ~f:(fun c -> Char.equal c '\000' || Char.equal c '\\')
     || List.exists (String.split path ~on:'/') ~f:(fun p ->
       List.mem [ ""; "."; ".." ] p ~equal:String.equal)
  then J.fail ("unsafe methodology inventory path: " ^ path)
;;

let digest s =
  if String.length s <> 64
     || not (String.for_all s ~f:(fun c ->
       Char.is_digit c || (Char.compare c 'a' >= 0 && Char.compare c 'f' <= 0)))
  then J.fail "methodology inventory requires lowercase SHA-256"
;;

(* Classic 3.1.0.dev3 get_all_config_variables: all Path/Macro variables and
   their deprecated aliases, except the four exact provider links below.
   These cannot introduce additional design/PDK/script inputs through raw project
   overrides in this versioned translation. Non-path project knobs stay raw.
   Runtime-selected PDK collateral remains the existing native runtime boundary. *)
let unsupported_input_keys =
  String.split ~on:' '
    "CELL_BB_VERILOG_MODELS CELL_CDLS CELL_GDS CELL_LEFS CELL_LIBS CELL_MAGLEFS CELL_MAGS CELL_SPICE_MODELS CELL_VERILOG_MODELS DESIGN_DIR EQY_SCRIPT EXTRA_CDLS EXTRA_GDS EXTRA_LEFS EXTRA_LIBS EXTRA_SPEFS EXTRA_SPICE_MODELS EXTRA_VERILOG_MODELS FALLBACK_SDC FP_TRACKS_INFO IO_PIN_ORDER_CFG KLAYOUT_DEF_LAYER_MAP KLAYOUT_DRC_RUNSET KLAYOUT_PROPERTIES KLAYOUT_TECH LINTER_VLTS MACROS MACRO_PLACEMENT_CFG MAGICRC MAGIC_DRC_MAGLEFS MAGIC_PDK_SETUP MAGIC_TECH NETGEN_SETUP PAD_CDLS PAD_GDS PAD_LEFS PAD_LIBS PAD_SPICE_MODELS PAD_VERILOG_MODELS PDK_ROOT PDN_CFG PNR_EXCLUDED_CELL_FILE RCX_RULESETS RCX_SDC_FILE STA_EXTRA_CORNER_TCL_FILE SYNTH_ABC_STRATEGY_SCRIPT SYNTH_CSA_MAP SYNTH_EXCLUDED_CELL_FILE SYNTH_EXTRA_MAPPING_FILE SYNTH_FA_MAP SYNTH_LATCH_MAP SYNTH_RCA_MAP SYNTH_TRISTATE_MAP TECH_LEFS VERILOG_INCLUDE_DIRS VSRC_LOC_FILES LIB CELLS_LEF GDS_FILES CELLS_GDS STD_CELL_LIBRARY_CDL NO_SYNTH_CELL_LIST SYNTH_EXCLUSION_CELL_LIST DRC_EXCLUDE_CELL_LIST PNR_EXCLUSION_CELL_LIST VERILOG_FILES_BLACKBOX EXTRA_GDS_FILES FALLBACK_SDC_FILE BASE_SDC_FILE SDC_FILE LINTER_VLT TRISTATE_BUFFER_MAP CARRY_SELECT_ADDER_MAP RIPPLE_CARRY_ADDER_MAP FULL_ADDER_MAP TRACKS_INFO_FILE FP_PDN_CFG FP_PIN_ORDER_CFG MAGIC_MAGICRC MAGIC_TECH_FILE KLAYOUT_DRC_TECH_SCRIPT NETGEN_SETUP_FILE"
;;

let rec literal key = function
  | `String s when String.is_substring s ~substring:"::" ->
    J.fail ("named classic v1 requires literal settings; unsupported expression in " ^ key)
  | `List xs -> List.iter xs ~f:(literal key)
  | `Assoc xs ->
    List.iter xs ~f:(fun (k, v) ->
      if String.is_substring k ~substring:"::"
      then J.fail ("unsupported conditional configuration in " ^ key);
      literal key v)
  | _ -> ()
;;

let verify ~manifest:m ~configuration =
  J.decode (fun () ->
    J.validate m;
    let files =
      List.map (J.array (J.get m "files")) ~f:(fun f ->
        J.object_ f ~fields:[ "path"; "sha256" ];
        let path = J.string (J.get f "path") and hash = J.string (J.get f "sha256") in
        safe_path path;
        digest hash;
        path, hash)
    in
    let paths = List.map files ~f:fst in
    if Option.is_some (List.find_a_dup paths ~compare:String.compare)
    then J.fail "duplicate methodology inventory file";
    List.iter paths ~f:(fun path ->
      let rec parents = function
        | [] | [ _ ] -> ()
        | parts ->
          let parent = List.drop_last_exn parts in
          if List.mem paths (String.concat ~sep:"/" parent) ~equal:String.equal
          then J.fail ("methodology inventory file/descendant collision: " ^ path);
          parents parent
      in
      parents (String.split path ~on:'/'));
    let require path =
      safe_path path;
      match List.Assoc.find files path ~equal:String.equal with
      | Some hash -> hash
      | None -> J.fail ("named classic v1 input missing from bundle inventory: " ^ path)
    in
    if not (String.equal (require "src/config.json") (J.sha256 configuration))
    then J.fail "configuration bytes digest mismatch";
    let copies =
      List.map (J.array (J.get m "source_inputs")) ~f:(fun s ->
        J.object_ s ~fields:[ "path"; "copy"; "sha256"; "git_status" ];
        let path = J.string (J.get s "path") and copy = J.string (J.get s "copy") in
        safe_path path;
        ignore (J.string (J.get s "git_status") : string);
        let hash = J.string (J.get s "sha256") in
        digest hash;
        if not (String.equal copy ("inputs/" ^ path))
           || not (String.equal (require copy) hash)
        then J.fail ("methodology source input identity mismatch: " ^ path);
        copy)
    in
    if List.is_empty copies
       || Option.is_some (List.find_a_dup copies ~compare:String.compare)
       || not (List.equal String.equal
                 (List.sort copies ~compare:String.compare)
                 (List.filter paths ~f:(String.is_prefix ~prefix:"inputs/")
                  |> List.sort ~compare:String.compare))
    then J.fail "methodology source inventory mismatch";
    let top = J.string (J.get m "top_module") in
    if String.is_empty top || not (String.for_all top ~f:(fun c -> Char.is_alphanum c || Char.equal c '_'))
    then J.fail "named classic v1 requires a literal top module identifier";
    let rtl = "src/" ^ top ^ ".v" in
    let declared key =
      let paths = List.map (J.array (J.get m key)) ~f:J.string in
      List.iter paths ~f:(fun p -> ignore (require p : string));
      paths
    in
    if not (List.equal String.equal (declared "synthesis_sources") [ rtl ])
    then J.fail "named classic v1 synthesis_sources must match generated VERILOG_FILES";
    (match declared "simulation_sources" with
     | [ p ] when String.equal p rtl || String.equal p ("simulation/" ^ top ^ ".v") -> ()
     | _ -> J.fail "named classic v1 simulation_sources must name the provider RTL");
    ignore (require "constraints/top.sdc" : string);
     let config = Yojson.Safe.from_string configuration in
     J.validate config;
     (* Only exact provider-generated macro links are admitted. Their complete
        canonical reconstruction is checked by Methodology_plan; this inventory
        still rejects every other path-bearing override. *)
     let macro_inputs =
       match List.Assoc.find (match m with `Assoc xs -> xs | _ -> []) "macro_contract" ~equal:String.equal with
       | None -> []
       | Some c ->
         if not (J.equal (J.get c "schema_version") (`Int 2)) then J.fail "named SRAM requires schema2 Hardening";
         let stock="RM_IHPSG13_1P_256x16_c2_bm_bist" in
         let macro=match J.array (J.get c "macros") with
          | [macro] when J.equal (J.get macro "module") (`String stock) -> macro
          | _ -> J.fail "named SRAM supports exactly one stock256x16 macro" in
         if not (J.equal (J.get c "execution") (`String "fresh_full") &&
           J.equal (J.get m "operation") (`String "Hardening")) then J.fail "named SRAM requires schema2 Hardening";
         let intent=J.get macro "physical_intent" in
         if not (J.equal (J.get intent "orientation") (`String "R0")) then J.fail "named SRAM requires R0";
         let instance=Resource.Id.rtl_instance_name (Resource.Id.of_string_exn (J.string (J.get macro "resource"))) in
         if not (String.equal (J.string (J.get macro "instance")) instance) then J.fail "named SRAM requires canonical instance";
         let path dir suffix=`String ("pdk_dir::libs.ref/sg13cmos5l_sram/" ^ dir ^ "/" ^ stock ^ suffix) in
         let corners=["nom_fast_1p32V_m40C","fast_1p32V_m55C";"nom_slow_1p08V_125C","slow_1p08V_125C";"nom_typ_1p20V_25C","typ_1p20V_25C"] in
         let expected=[
           "MACROS",`Assoc [stock,`Assoc [
             "instances",`Assoc [instance,`Assoc ["location",J.get intent "location_um";"orientation",`String "R0"]];
             "lef",`List [path "lef" ".lef"];"gds",`List [path "gds" ".gds"];"spice",`List [path "cdl" ".cdl"];
             "lib",`Assoc (List.map corners ~f:(fun (corner,suffix) -> corner,`List [path "lib" ("_" ^ suffix ^ ".lib")]))]];
           "EXTRA_VERILOG_MODELS",`List [`String ("dir::../macros/" ^ stock ^ ".blackbox.v")];
           "PDN_CFG",`String "dir::../physical/pdn.tcl";
           "EXTRA_SPICE_MODELS",`List [path "cdl" ".cdl"];
           "KLAYOUT_DRC_RUNSET",`String "pdk_dir::libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc"] in
         List.iter ["macros/" ^ stock ^ ".blackbox.v";"physical/macro-plan.json";"physical/pdn.tcl"]
           ~f:(fun p -> ignore (require p : string));
         let generated=J.get m "generated_settings" in
         List.map expected ~f:(fun (key,value) ->
           if not (J.equal value (J.get generated key) && J.equal value (J.get config key)) then J.fail ("named SRAM generated link mismatch: " ^ key);
           key,value)
     in
    let links =
      [ "VERILOG_FILES", `List [ `String ("dir::" ^ top ^ ".v") ]
      ; "PNR_SDC_FILE", `String "dir::../constraints/top.sdc"
       ; "SIGNOFF_SDC_FILE", `String "dir::../constraints/top.sdc"
       ] @ macro_inputs
    in
    List.iter links ~f:(fun (k, v) ->
      if not (J.equal (J.get config k) v)
      then J.fail ("named classic v1 requires the exact provider-generated link: " ^ k));
    let floorplan = List.hd_exn (J.array (J.get (J.get m "target") "external_references")) in
    let path = J.string (J.get floorplan "path") in
    safe_path path;
    if String.exists path ~f:(fun c -> String.contains "*?[]{}$" c)
    then J.fail "named classic v1 requires a literal floorplan reference";
    if not (J.equal (J.get config "FP_DEF_TEMPLATE") (`String ("dir::../tt/" ^ path)))
    then J.fail "named classic v1 floorplan reference mismatch";
    match config with
    | `Assoc xs ->
      List.iter xs ~f:(fun (key, value) ->
         if String.equal key "meta" || (List.mem unsupported_input_keys key ~equal:String.equal &&
           not (List.Assoc.mem macro_inputs key ~equal:String.equal))
        then J.fail ("named classic v1 does not support additional input setting " ^ key
                     ^ "; use the provider-generated config/RTL/SDC and target links");
        if String.is_substring key ~substring:"::"
        then J.fail ("unsupported conditional configuration: " ^ key);
        if not (List.mem ("FP_DEF_TEMPLATE" :: List.map links ~f:fst) key ~equal:String.equal)
        then literal key value)
    | _ -> J.fail "named classic v1 requires a JSON configuration object")
;;
