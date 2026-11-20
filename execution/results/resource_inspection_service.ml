module E = Evidence_json
type target = Bundle of string | Coarse_run of string | Execution of string | Finalization of string
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let fields = function `Assoc xs -> xs | _ -> invalid_arg "expected object"
let list = function `List xs -> xs | `Null -> [] | _ -> invalid_arg "expected list"
let text = function `String s -> s | _ -> invalid_arg "expected string"
let s x = `String x
let obj x = `Assoc x
let arr x = `List x
let checked = function Ok x -> x | Error e -> invalid_arg e
let strict raw =
  (match Identity_json.parse raw with Ok _ -> () | Error e -> invalid_arg (Identity_json.error_message e));
  Yojson.Safe.from_string raw
let canonical value =
  match Identity_json.parse (Yojson.Safe.to_string value) with
  | Ok j -> Identity_json.canonical j
  | Error e -> invalid_arg (Identity_json.error_message e)
let digest x = Identity_json.sha256_bytes (E.record_bytes x)
let show = function `String x -> x | `Null -> "unavailable" | x -> E.record_bytes x
let within root p = String.starts_with ~prefix:(root ^ "/") p
let safe root path =
  if not (Filename.is_relative path) || List.exists (fun x -> x=".." || x="" || x=".") (String.split_on_char '/' path)
  then invalid_arg "unsafe source member";
  let p=Unix.realpath (Filename.concat root path) in
  if not (within root p) then invalid_arg "source escapes target"; p
let read path =
  let fd=Unix.openfile path [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    if before.st_kind<>Unix.S_REG || before.st_size>32*1024*1024 then invalid_arg "source type/32MiB bound";
    let raw=really_input_string ic before.st_size in
    let after=Unix.fstat fd in
    if before.st_dev<>after.st_dev || before.st_ino<>after.st_ino || before.st_size<>after.st_size
       || before.st_mtime<>after.st_mtime || before.st_ctime<>after.st_ctime
    then invalid_arg "source changed during read"; raw)
let ref_ path raw format = obj ["path",s path;"sha256",s (Identity_json.sha256_bytes raw);"format",s format]
let verify_ref root r =
  let p=safe root (text (get r "path")) in
  let f=checked (Checkpoint.file_record p) in
  if get r "sha256"<>s f.sha256 then invalid_arg "resource source hash changed"; p
let inventory_manifest manifest =
  (match Hardcaml_asic.Elaboration.Bundle.verify_manifest_identity manifest with
   | Ok () -> () | Error _ -> invalid_arg "invalid bundle manifest identity");
  if not (List.mem_assoc "resources" (fields manifest) || List.mem_assoc "resource_snapshot" (fields manifest))
  then None else Some (snd (Resource_evidence.inventory manifest))
(* Bundle/source metadata is not authority until it agrees with the inventoried
   copies whose actual bytes [bundle] verifies. This is structured inventory
   validation, shared by bundle and coarse comparison; no tool-report parser. *)
let validate_bundle_inventory manifest =
  let files=list (get manifest "files") in
  let names=List.map (fun f -> text (get f "path")) files in
  let unique label xs=if List.length xs<>List.length (List.sort_uniq String.compare xs) then invalid_arg ("duplicate "^label) in
  unique "bundle member" names;
  List.iter (fun key ->
    let declared=List.map text (list (get manifest key)) in
    unique key declared;
    List.iter (fun name -> if not (List.mem name names) then invalid_arg (key^" is outside bundle inventory")) declared)
    ["synthesis_sources";"simulation_sources"];
  match List.assoc_opt "source_inputs" (fields manifest) with
  | None -> () (* Historical absence is not a reconstructed source inventory. *)
  | Some (`List inputs) ->
    if List.length inputs>512 then invalid_arg "source input count exceeds 512";
    let copies=List.map (fun input ->
      let source=text (get input "path") and copy=text (get input "copy") in
      if not (Filename.is_relative source) || List.exists (fun p -> List.mem p ["";".";".."]) (String.split_on_char '/' source)
        || String.contains source '\\' || String.contains source '\000' then invalid_arg "unsafe declared source path";
      if copy<>"inputs/"^source then invalid_arg "source input copy/path mismatch";
      let member=match List.find_opt (fun f -> get f "path"=s copy) files with
        | Some f -> f | None -> invalid_arg "declared source copy absent from bundle inventory" in
      if get member "sha256"<>get input "sha256" then invalid_arg "source input/copy hash mismatch";
      copy) inputs in
    unique "source input copy" copies;
    let actual=List.filter (String.starts_with ~prefix:"inputs/") names in
    if List.sort String.compare copies<>List.sort String.compare actual then invalid_arg "source input copy inventory mismatch"
  | Some _ -> invalid_arg "source_inputs must be an array"
type source = { native_rows:E.t list; root:string; inventory:E.t list; inventory_status:string; inventory_reason:E.t;
  evidence:E.t; provenance:E.t; refs:E.t list; binding:E.t }
let bundle root =
  let raw=read (safe root "manifest.json") in
  let manifest=strict raw in
  let inventory=inventory_manifest manifest in
  let inventory_status,inventory_reason=match inventory with Some _ -> "available",`Null
    | None -> "unavailable",s "historical bundle has no recorded resource inventory" in
  let inventory=Option.value inventory ~default:[] in
  let files=list (get manifest "files") in
  if List.length files>512 then invalid_arg "bundle member count exceeds 512";
  validate_bundle_inventory manifest;
  let total=ref 0 in
  let refs=List.map (fun f ->
    let path=text (get f "path") in
    let raw=read (safe root path) in
    total:= !total+String.length raw;
    if !total>64*1024*1024 then invalid_arg "bundle members exceed 64MiB";
    let r=ref_ path raw "bundle_member" in
    if get r "sha256"<>get f "sha256" then invalid_arg "bundle member hash mismatch";r) files in
  let names=List.map (fun r -> text (get r "path")) refs in
  if List.length names<>List.length (List.sort_uniq String.compare names) then invalid_arg "duplicate bundle member";
  {native_rows=[];root;inventory;inventory_status;inventory_reason;evidence=`Null;refs=ref_ "manifest.json" raw "json"::refs;binding=manifest;
   provenance=obj ["build_identity",get manifest "identity";"project",get manifest "project";
     "hardware_assignments",get (get manifest "hardware_variant") "assignments";
     "inventory_status",s inventory_status;"inventory_reason",inventory_reason;"scope",s "verified bundle members; selection and RTL inventory only"]}
(* Only inspect the native state's exact scalar NL view of this validated
   publication. No searching a run tree or following unrelated view paths. *)
let checkpoint_synthesis ~root ~manifest ~inventory (path, (cp : Checkpoint.t)) =
  let unavailable why = [], [], Some why in
  if cp.schema <> Schema_3 || cp.boundary <> Synthesis || cp.restart_targets <> [] ||
     get (strict (Identity_json.canonical cp.raw)) "macro_profile" <> s Macro_contract.synthesis_profile
  then [], [], None
  else if not (within root path) || path <> Unix.realpath path then unavailable "checkpoint is outside the execution workspace"
  else
    let relative p = String.sub p (String.length root + 1) (String.length p - String.length root - 1) in
    let member name format =
      let expected=List.assoc name cp.files in
      let p=safe path name in
      if p <> Filename.concat path name then invalid_arg "checkpoint member is not an owned file";
      let bytes=read p in
      let r=ref_ (relative p) bytes format in
      if get r "sha256" <> s expected.sha256 then invalid_arg "checkpoint member hash changed";
      bytes,r in
    let state,state_ref=member "state.json" "native_state" in
    (* Checkpoint.validate already projected and checked the entire native state,
       including nonfinite metrics; only consume the bound scalar NL here. *)
    match get (Yojson.Safe.from_string state) "nl" with
    | `String nl when within (path ^ "/views") nl && nl=Unix.realpath nl ->
      let name=String.sub nl (String.length path+1) (String.length nl-String.length path-1) in
      let expected=List.assoc name cp.files in
      if Int64.of_string expected.bytes > 33554432L then unavailable "checkpoint netlist exceeds inspection limit"
      else let netlist,nl_ref=member name "verilog_gate_netlist" in
        let refs=[state_ref;nl_ref] in
        (try Resource_evidence.checkpoint_macro_rows ~top:(text (get manifest "top_module"))
               ~inventory ~netlist ~refs, refs, None
         with Invalid_argument why -> [],refs,Some ("unsupported checkpoint netlist: " ^ why))
    | _ -> unavailable "checkpoint has no single owned native netlist view"

let source kind target =
  let root=if kind="execution" then Filename.dirname target else target in
  match kind with
  | "bundle" -> bundle root
  | "coarse_run" ->
    let evidence=checked (Resource_evidence.inspect ~run_dir:root) in
    {native_rows=[];root; inventory=list (get evidence "resource_inventory");inventory_status="available";inventory_reason=`Null;evidence;
     provenance=get evidence "provenance";refs=list (get evidence "evidence_refs");binding=evidence}
  | "execution" ->
    let raw=read target in
    let captured=match Identity_json.parse raw with Ok j -> j | Error e -> invalid_arg (Identity_json.error_message e) in
    let record,checkpoints=checked (Stage_report_service.validate_execution_snapshot captured) in
    let inventory,manifest,inventory_reason=match record.methodology with
      | None -> None,`Null,s "historical execution has no bound methodology/bundle inventory"
      | Some m ->
        let plan=Hardcaml_asic.Methodology_plan.to_yojson (Methodology_execution_record.plan m) in
        let manifest=strict (text (get plan "bundle_manifest")) in
        inventory_manifest manifest,manifest,s "recorded methodology bundle has no resource inventory" in
    let inventory_status,inventory_reason=match inventory with Some _ -> "available",`Null
      | None -> "unavailable",inventory_reason in
    let inventory=Option.value inventory ~default:[] in
    let reference=ref_ (Filename.basename target) raw "json" in
    let methodology=match record.methodology with None -> `Null | Some m -> strict (Identity_json.canonical (Methodology_execution_record.to_json m)) in
    let refs=match get methodology "snapshot_path" with
      | `String p when within root (Unix.realpath p) ->
        let p=Unix.realpath p in
        let relative=String.sub p (String.length root+1) (String.length p-String.length root-1) in
        let r=ref_ relative (read p) "json" in
        if get r "sha256"<>get methodology "snapshot_sha256" then invalid_arg "methodology snapshot changed";
        [reference;r]
      | _ -> [reference] in
    let record_json=strict raw in
    let native_rows,native_refs,diagnostic = match checkpoints with
      | [cp] when record.status="completed" && Option.is_some record.methodology &&
          get (get record_json "plan") "schema_version"=`Int 4 &&
          get (get manifest "macro_contract") "schema_version"=`Int 1 &&
          get (get record_json "plan") "stages"=arr [s "synthesis"] &&
          get (get record_json "plan") "initial_boundary"=`Null ->
        checkpoint_synthesis ~root ~manifest ~inventory cp
      | _ -> [],[],None in
    let native = if native_rows=[] then [] else ["checkpoint_synthesis",arr native_rows] in
    let reason = match diagnostic with None -> [] | Some why -> ["synthesis_unavailable_reason",s why] in
    let build_identity=if manifest<>`Null then get manifest "identity" else
      match record.references with [] -> `Null | reference::_ ->
        let reference=strict (Identity_json.canonical reference) in
        let checkpoint=checked (Checkpoint.read (text (get reference "path"))) in
        get (strict (Identity_json.canonical checkpoint.compatibility)) "bundle_identity" in
    {native_rows;root;inventory;inventory_status;inventory_reason;evidence=`Null;refs=refs@native_refs;binding=obj (["execution",record_json;"manifest",manifest]@native@reason);
     provenance=obj ["attempt_id",get record_json "attempt_id";"execution",reference;
       "requested_stage",s (Execution_plan.Stage.to_string (Execution_plan.Stage_plan.endpoint record.plan));
       "execution_status",s record.status;"checkpoints",get record_json "artifacts";
       "methodology",methodology;
       "build_identity",build_identity;
       "inventory_status",s inventory_status;"inventory_reason",inventory_reason;
       "scope",s (if native_rows=[] then "validated named execution and methodology inventory; per-resource realization unavailable" else "stock SRAM retention in verified native synthesis netlist only")]}
  | "finalization" ->
    let operation=Finalization_record.to_json (checked (Finalization_record.read root)) in
    let op=strict (Identity_json.canonical operation) in
    let context=get op "evidence_context" in
    if get context "run_id"<>get op "attempt_id" then invalid_arg "finalization context attempt mismatch";
    let result_ref=get op "results" in
    let result_path=Unix.realpath (text (get result_ref "path")) in
    if not (within root result_path) then invalid_arg "finalization results escape target";
    let results_raw=read result_path in
    if s (Identity_json.sha256_bytes results_raw)<>get result_ref "sha256" then invalid_arg "finalization results digest mismatch";
    let results=strict results_raw in
    let execution=get op "execution" in
    let execution_path=text (get execution "path") in
    let execution_raw=read execution_path in
    if s (Identity_json.sha256_bytes execution_raw)<>get execution "sha256" then invalid_arg "finalization execution changed";
    let selected=checked (Stage_report_service.validate_execution execution_path) in
    let execution_json=strict execution_raw in
    if get execution_json "attempt_id"<>get execution "attempt_id" then invalid_arg "finalization execution attempt mismatch";
    let references=List.map (fun r -> strict (Identity_json.canonical r)) selected.references in
    let selected_checkpoints=arr (List.map (fun r -> obj ["path",get r "path";"identity",get r "identity";"boundary",get r "boundary"]) references) in
    if canonical selected_checkpoints<>canonical (get op "selected_checkpoints")
       || canonical selected_checkpoints<>canonical (get (get context "evidence_origin") "selected_checkpoints")
       || get (get context "evidence_origin") "operation"<>s (root^"/operation.json")
       || get (get context "evidence_origin") "contract"<>s Finalization_record.contract
       || get (get context "evidence_origin") "schema_version"<>`Int 1
    then invalid_arg "finalization selected checkpoint/context association mismatch";
    let bundle_ids=match selected.methodology with
      | Some m -> [s (Hardcaml_asic.Methodology_plan.bundle_identity (Methodology_execution_record.plan m))]
      | None -> List.map (fun r -> let checkpoint=checked (Checkpoint.read (text (get r "path"))) in
          let compatibility=strict (Identity_json.canonical checkpoint.compatibility) in get compatibility "bundle_identity") references in
    if bundle_ids=[] || List.exists (fun build -> build<>get context "build_identity") bundle_ids
    then invalid_arg "finalization has no matching verified execution/build association";
    let evidence=get results "resource_evidence" in
    if evidence=`Null then invalid_arg "finalization resource evidence unavailable";
    checked (Resource_evidence.verify_sources ~context ~run_dir:root evidence);
    if get (get evidence "provenance") "run_id"<>get op "attempt_id"
       || canonical (get (get evidence "provenance") "evidence_origin")<>canonical (get context "evidence_origin")
    then invalid_arg "finalization resource context provenance mismatch";
    {native_rows=[];root;inventory=list (get evidence "resource_inventory");inventory_status="available";inventory_reason=`Null;evidence;
     provenance=obj ["finalization",op;"resource_provenance",get evidence "provenance"];
     binding=obj ["operation",op;"results_sha256",get result_ref "sha256";"evidence",evidence];
     refs=ref_ "operation.json" (read (safe root "operation.json")) "json"::
       ref_ (String.sub result_path (String.length root+1) (String.length result_path-String.length root-1)) results_raw "json"::list (get evidence "evidence_refs")}
  | _ -> invalid_arg "invalid resource target kind"
let observation source stage = List.find_opt (fun o -> get o "stage"=s stage) (list (get source.evidence "observations"))
let observed source id stage =
  let rows = match observation source stage with Some o -> list (get o "resources")
    | None when stage="synthesis" -> source.native_rows
    | None -> [] in
  Option.value (List.find_opt (fun r -> get r "resource_id"=s id) rows) ~default:`Null
let sections=["inventory";"synthesis";"final";"geometry";"timing";"electrical";"provenance"]
let section_values source row section =
  let id=text (get row "resource_id") in
  let final=observed source id "final_physical" in
  match section with
  | "inventory" -> [row]
  | "synthesis" -> [observed source id "synthesis"]
  | "final" -> [if final=`Null then `Null else obj (List.filter (fun (k,_) -> not (List.mem k ["timing_links";"electrical_links"])) (fields final))]
  | "geometry" ->
    let physical=match observation source "final_physical" with Some o -> get o "physical" | None -> `Null in
    [get final "geometry"] @
    (match get physical "floorplan" with `Assoc xs -> List.map (fun (k,v) -> obj [k,v]) xs | _ -> []) @
    (match get physical "measurements" with `Assoc xs -> List.map (fun (k,v) -> obj [k,v]) xs | _ -> []) @
    list (get physical "ratios")
  | "timing" -> let links=get final "timing_links" in
    if get links "status"=s "observed" then list (get links "paths") else [links]
  | "electrical" -> let links=get final "electrical_links" in
    if get links "status"=s "observed" then list (get links "points") else [links]
  | "provenance" -> List.map (fun (k,v) -> obj [k,v]) (fields source.provenance)
  | _ -> invalid_arg "invalid resource section"
type classification = Observed | Derived | Estimate | Unknown
let classification_name = function Observed -> "observed" | Derived -> "derived" | Estimate -> "estimate" | Unknown -> "unknown"
let measurement classification name m = obj ["name",s name;"value",get m "value";"unit",get m "unit";
  "stage",get m "stage";"definition",get m "definition";"scope",get m "scope";"reason",get m "unavailable_reason";
  "classification",s (classification_name classification)]
(* Explicit semantics of Resource_evidence's supported measurement producers.
   Unknown/new quantities are not promoted from their stage, units or free text. *)
let observation_classification = function
  | "total_mapped_cells" | "total_physical_netlist_cells" | "macro_count" | "standard_cell_count" -> Derived
  | "synthesis_standard_cell_area" | "whole_design_sequential_area" | "macro_library_area" | "combined_cell_area_estimate" -> Estimate
  | "final_standard_cell_area" | "standard_cell_utilization" -> Observed
  | _ -> Unknown
let physical_classification = function
  | "native_all_component_area" | "native_nonspacer_area" | "native_spacer_area" | "native_macro_area" -> Observed
  | "macro_footprint_area" | "macro_component_area" | "macro_component_count"
  | "nonspacer_component_area" | "nonspacer_component_count" | "spacer_component_area" | "spacer_component_count"
  | "nonspacer_plus_macro_area" | "all_component_area" -> Derived
  | _ -> Unknown
let measurements source = List.concat_map (fun o ->
  let native=match get o "measurements" with `Assoc xs -> List.map (fun (k,v) -> measurement (observation_classification k) k v) xs | _ -> [] in
  let physical=get o "physical" in
  let extra=match get physical "measurements" with `Assoc xs -> List.map (fun (k,v) -> measurement (physical_classification k) k v) xs | _ -> [] in
  let floors=List.filter_map (fun name -> let m=get (get (get physical "floorplan") name) "area" in
    if m=`Null then None else Some (measurement Derived ("gross_"^name^"_area") m)) ["core";"die"] in
  let ratios=list (get physical "ratios") |> List.map (fun r ->
    let numerator=get r "numerator" and denominator=get r "denominator" in
    obj ["name",get r "name";"value",get r "value";"unit",get r "unit";
      "stage",get numerator "stage";"definition",s (show (get numerator "definition")^" / "^show (get denominator "definition"));
      "scope",get r "scope";"reason",`Null;"classification",s "derived"]) in
  native@extra@floors@ratios) (list (get source.evidence "observations"))
let sexp_field name = function
  | Core.Sexp.List xs -> List.find_map (function Core.Sexp.List [Core.Sexp.Atom k;v] when k=name -> Some v | _ -> None) xs
  | _ -> None
let contract_display request =
  try
    let kind,contract=match request with
      | `Assoc _ -> text (get request "kind"),Core.Sexp.of_string (text (get request "contract"))
      | `String raw -> let sexp=Core.Sexp.of_string raw in
        let kind=match sexp_field "kind" sexp with Some (Core.Sexp.Atom k) -> k | _ -> "resource" in
        kind,Option.value (sexp_field "contract" sexp) ~default:sexp
      | _ -> "resource",Core.Sexp.Atom (show request) in
    kind^" "^Core.Sexp.to_string_mach contract
  with _ -> show request
let reason_display selection =
  let budget macro limit = Printf.sprintf "budget_exhausted (macro=%s, limit=%s)" macro limit in
  match selection with
  | `Assoc _ -> let r=get selection "reason" in
    let kind=show (get r "kind") in
    if kind="budget_exhausted" then budget (text (get r "macro"))
      (match get r "limit" with `Int n -> string_of_int n | `Intlit n -> n | _ -> invalid_arg "invalid budget limit")
    else if get r "technology"=`Null then kind else kind^" ("^show (get r "technology")^")"
  | `String raw ->
    (try match sexp_field "reason" (Core.Sexp.of_string raw) with
      | Some (Core.Sexp.Atom "Explicit_flops") -> "explicit_flops"
      | Some (Core.Sexp.List (Core.Sexp.Atom "Budget_exhausted"::details)) ->
        let field k=match sexp_field k (Core.Sexp.List details) with
          | Some (Core.Sexp.Atom value) -> value | _ -> invalid_arg "missing budget field" in
        budget (field "macro") (field "limit")
      | Some (Core.Sexp.List [Core.Sexp.Atom kind;details]) ->
        let technology=match sexp_field (if kind="Exact_mapping" then "technology" else "no_exact_mapping_in") details with
          | Some (Core.Sexp.Atom t) -> t | _ -> Core.Sexp.to_string_mach details in
        String.lowercase_ascii kind^" ("^technology^")"
      | _ -> raw
    with _ -> raw)
  | _ -> "unavailable"
let policy_display = function
  | `Assoc _ as p -> (match get p "kind" with
    | `String "default" -> "default"
    | `String "instance" -> "instance:"^show (get p "resource")
    | `String "subtree" -> "subtree:"^String.concat "/" (List.map text (list (get p "path")))
    | _ -> show p)
  | `String raw -> (try match Core.Sexp.of_string raw with
    | Core.Sexp.Atom "Default" -> "default"
    | Core.Sexp.List [Core.Sexp.Atom "Rule";Core.Sexp.List [Core.Sexp.Atom "Instance";Core.Sexp.Atom id]] -> "instance:"^id
    | Core.Sexp.List [Core.Sexp.Atom "Rule";Core.Sexp.List [Core.Sexp.Atom "Subtree";Core.Sexp.List path]] ->
      "subtree:"^String.concat "/" (List.map (function Core.Sexp.Atom s -> s | _ -> invalid_arg "policy path") path)
    | _ -> raw with _ -> raw)
  | x -> show x
let row source r =
  let id=text (get r "resource_id") in
  let status stage=match observed source id stage with `Null -> "unavailable" | r -> show (get r "status") in
  let reason=reason_display (get r "selection") in
  let impl=get r "selected_implementation" in
  let implementation=if get impl "kind"=s "macro" then show (get impl "name") else show (get impl "kind") in
  let record=get r "inventory_record" in
  let area=List.filter_map (fun stage -> let m=get (observed source id stage) "area" in
    let classification=if stage="synthesis" && get impl "kind"=s "macro" then Estimate
      else if get (get (observed source id stage) "geometry") "status"=s "available" then Derived else Unknown in
    if m=`Null then None else Some (measurement classification "resource_area" m)) ["synthesis";"final_physical"] in
  let role=match get record "elaborated_as" with `String "Selected_implementation" -> "selected_implementation" | `String "Behavioral_model" -> "behavioral_model" | x -> show x in
  obj ["id",s id;"contract",s (contract_display (get r "requested_contract"));"requirement",s (String.lowercase_ascii (show (get r "requirement")));
    "policy",s (policy_display (get r "policy"));"implementation",s implementation;"reason",s reason;
    "role",s role;"instances",arr (List.map (fun i -> s (show (get i "instance"))) (list (get r "instances")));
    "synthesis",s (status "synthesis");"final",s (status "final_physical");"measurements",arr area;
    "sections",arr (List.map (fun section -> let values=section_values source r section in
      let final=observed source id "final_physical" in
      let available=match section with
        | "timing" -> get (get final "timing_links") "status"=s "observed"
        | "electrical" -> get (get final "electrical_links") "status"=s "observed"
        | "geometry" -> get (get final "geometry") "status"=s "available"
        | _ -> values<>[] && values<>[`Null] in
      obj ["id",s section;"count",`Int (if available then List.length values else 0);"status",s (if available then "available" else "unavailable")]) sections)]
let take_page values offset limit =
  let rec drop n xs=if n=0 then xs else match xs with [] -> [] | _::xs -> drop (n-1) xs in
  let rec take n xs=if n=0 then [] else match xs with [] -> [] | x::xs -> x::take (n-1) xs in
  take limit (drop offset values)
let run ~kind ~target ?(query="index") ?resource_id ?section ?expected_digest ?artifact_id ?(offset=0) ?(limit=16) () =
  try
    if offset<0 || offset>128*1024*1024 || limit<1 || limit>(if query="bytes" then 16384 else 64) then invalid_arg "resource page bounds";
    if not (List.mem query ["index";"detail";"artifacts";"bytes"]) then invalid_arg "invalid resource query";
    if query="index" && (resource_id<>None || section<>None || artifact_id<>None) then invalid_arg "unexpected index fields";
    if query="detail" && (resource_id=None || section=None || artifact_id<>None) then invalid_arg "detail requires resource-id/section only";
    if query="artifacts" && (resource_id<>None || section<>None || artifact_id<>None) then invalid_arg "unexpected artifact catalog fields";
    if query="bytes" && (resource_id<>None || section<>None || artifact_id=None) then invalid_arg "bytes requires artifact-id only";
    if query<>"index" && expected_digest=None then invalid_arg "page requires expected-digest";
    let target=Unix.realpath target in
    let source=source kind target in
    if List.length source.refs>1024 then invalid_arg "resource source catalog exceeds 1024 entries";
    let source_digest=digest (obj ["kind",s kind;"target",s target;"binding",source.binding;"refs",arr source.refs]) in
    Option.iter (fun expected -> if expected<>source_digest then invalid_arg "stale resource source digest") expected_digest;
    let verdict=if source.evidence=`Null then "not_evaluated" else if Resource_evidence.problems source.evidence=[] then "pass" else "fail" in
    let common=["contract",s "hardcaml-asic.resource-inspection";"schema_version",`Int 1;"kind",s kind;"target",s target;"query",s query;
      "source_digest",s source_digest;"provenance",(if query="index" then source.provenance else `Null);"verdict",s verdict] in
    let paging total count=
      if offset>total || offset+count>total then invalid_arg "resource page offset past end";
      ["offset",`Int offset;"next_offset",`Int (offset+count);"total",`Int total] in
    let result=match query with
    | "index" ->
      if List.length source.inventory>4096 then invalid_arg "resource inventory exceeds 4096 rows";
      let diagnostics=if kind="execution" && get source.binding "checkpoint_synthesis"<>`Null then
        ["Synthesis retention only; optimized flops and physical placement are not attributed"]
        else if kind="execution" && get source.binding "synthesis_unavailable_reason"<>`Null then
          [show (get source.binding "synthesis_unavailable_reason")]
        else if source.evidence=`Null then
        ["realization unavailable: inventory is not implementation retention"] @
        (if source.inventory_status="unavailable" then ["inventory unavailable: "^show source.inventory_reason] else [])
        else Resource_evidence.problems source.evidence @
          List.concat_map (fun o -> List.map show (list (get o "timing_diagnostics"))) (list (get source.evidence "observations")) @
          (if List.exists (fun o -> List.exists (fun r -> get (get r "timing_links") "status"=s "observed") (list (get o "resources")))
            (list (get source.evidence "observations")) then
            ["Resource timing coverage: sampled_reported_paths_not_exhaustive; electrical compliance: not_assessed"] else []) in
      obj (common@["rows",arr (List.map (row source) source.inventory);"measurements",arr (measurements source);
        "inventory_status",s source.inventory_status;"inventory_reason",source.inventory_reason;
        "artifact_count",`Int (List.length source.refs);"diagnostics",arr (List.map s (take_page diagnostics 0 256));"truncated",`Bool (List.length diagnostics>256)])
    | "detail" ->
      let id=Option.get resource_id and section=Option.get section in
      let r=match List.find_opt (fun r -> get r "resource_id"=s id) source.inventory with Some r -> r | None -> invalid_arg "unknown resource ID" in
      let values=section_values source r section in
      let values=if values=[`Null] then [obj ["status",s "unavailable";"reason",s "retained evidence unavailable"]] else values in
      let qualifiers=if section="timing" || section="electrical" then (
        let final=observed source id "final_physical" in
        let link=get final (if section="timing" then "timing_links" else "electrical_links") in
        if get link "status"=s "observed" then
          ["coverage",get link "coverage"] @ (if section="electrical" then ["electrical_compliance",get link "compliance"] else [])
        else []) else [] in
      let items=take_page values offset limit |> List.mapi (fun i value ->
        obj ["id",s (digest (obj ["resource",s id;"section",s section;"ordinal",`Int (offset+i);"value",value]));
          "title",s (section^" "^string_of_int (offset+i+1));"fields",arr (List.map (fun (k,v) -> arr [s k;s (show v)])
            ((match value with `Assoc xs -> xs | _ -> ["value",value]) @ qualifiers))]) in
      (* Accumulate a bounded prefix; callers follow next_offset, never lose rows. *)
      let rec fit acc bytes = function [] -> List.rev acc | x::xs -> let n=String.length (E.record_bytes x) in
        if n>48000 then invalid_arg "single resource detail item exceeds page budget";
        if bytes+n>48000 then List.rev acc else fit (x::acc) (bytes+n) xs in
      let items=fit [] 0 items in
      obj (common@paging (List.length values) (List.length items)@["resource_id",s id;"section",s section;"items",arr items])
    | "artifacts" ->
      let entries=take_page source.refs offset limit |> List.map (fun r -> obj ["id",s (digest r);"label",get r "path";"sha256",get r "sha256";"format",get r "format"]) in
      obj (common@paging (List.length source.refs) (List.length entries)@["entries",arr entries])
    | _ ->
      let id=Option.get artifact_id in
      let r=match List.find_opt (fun r -> digest r=id) source.refs with Some r -> r | None -> invalid_arg "unknown resource artifact ID" in
      let path=verify_ref source.root r in
      let fd=Unix.openfile path [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
      let raw,total=Fun.protect ~finally:(fun () -> Unix.close fd) (fun () ->
        let st=Unix.fstat fd in
        if st.st_kind<>Unix.S_REG || offset>st.st_size then invalid_arg "artifact offset/type";
        if st.st_size>128*1024*1024 then invalid_arg "artifact hash-read budget";
        (* Hash the very descriptor whose bytes are returned, not a separate path open. *)
        let h=Cryptokit.Hash.sha256 () and chunk=Bytes.create 65536 in
        let consumed=ref 0 in
        let rec hash_bytes () = let n=Unix.read fd chunk 0 (Bytes.length chunk) in
          if n>0 then (consumed:= !consumed+n;
            if !consumed>128*1024*1024 then invalid_arg "artifact grew beyond hash budget";
            h#add_substring chunk 0 n;hash_bytes ()) in
        hash_bytes ();
        let actual=Cryptokit.transform_string (Cryptokit.Hexa.encode ()) h#result in
        if get r "sha256"<>s actual then invalid_arg "opened artifact digest mismatch";
        ignore (Unix.lseek fd offset Unix.SEEK_SET);
        let b=Bytes.create (min limit (st.st_size-offset)) in
        let rec loop pos=if pos<Bytes.length b then let n=Unix.read fd b pos (Bytes.length b-pos) in
          if n=0 then invalid_arg "artifact truncated" else loop (pos+n) in
        loop 0; let after=Unix.fstat fd in
        if after.st_dev<>st.st_dev || after.st_ino<>st.st_ino || after.st_size<>st.st_size
           || after.st_mtime<>st.st_mtime || after.st_ctime<>st.st_ctime then invalid_arg "artifact changed during paging";
        Bytes.to_string b,st.st_size) in
      ignore (verify_ref source.root r);
      let hex=String.concat "" (List.init (String.length raw) (fun i -> Printf.sprintf "%02x" (Char.code raw.[i]))) in
      obj (common@paging total (String.length raw)@["artifact_id",s id;"sha256",get r "sha256";"bytes_hex",s hex]) in
    List.iter (fun r -> ignore (verify_ref source.root r)) source.refs;
    (if kind="execution" then ignore (checked (Stage_report_service.validate_execution target)));
    (if kind="finalization" then (
      let op=get source.binding "operation" in
      let execution=get op "execution" in
      if s (Identity_json.sha256_bytes (read (text (get execution "path"))))<>get execution "sha256"
      then invalid_arg "finalization execution changed after inspection"));
    if String.length (E.record_bytes result)>(if query="index" then 2*1024*1024 else 65536) then invalid_arg "resource response byte budget";
    Ok result
  with Invalid_argument e | Failure e | Sys_error e -> Error e
    | Not_found -> Error "missing resource query field"
    | End_of_file -> Error "truncated resource source"
    | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)

let inspect target ?query ?resource_id ?section ?expected_digest ?artifact_id ?offset ?limit () =
  let kind,target=match target with Bundle p -> "bundle",p | Coarse_run p -> "coarse_run",p
    | Execution p -> "execution",p | Finalization p -> "finalization",p in
  run ~kind ~target ?query ?resource_id ?section ?expected_digest ?artifact_id ?offset ?limit ()

let comparison_snapshot target () =
  try
    let kind,path=match target with Bundle p -> "bundle",p | Coarse_run p -> "coarse_run",p
      | Execution p -> "execution",p | Finalization _ -> invalid_arg "comparison does not support finalization" in
    let path=Unix.realpath path in
    let before=checked (run ~kind ~target:path ()) in
    let source=source kind path in
    let middle_digest=digest (obj ["kind",s kind;"target",s path;"binding",source.binding;"refs",arr source.refs]) in
    if get before "source_digest"<>s middle_digest then invalid_arg "resource generation differs from snapshot index";
    let manifest,bundle_sources=match kind with
      | "bundle" -> source.binding,source.refs
      | "execution" -> get source.binding "manifest",[]
      | _ ->
        (match List.find_opt (fun r -> List.mem (get r "path")
          [s "project/manifest.json";s "manifest.json";s "bundle/manifest.json"]) source.refs with
         | None -> `Null,[]
         | Some r ->
           let manifest_path=verify_ref source.root r in
           let frozen=bundle (Filename.dirname manifest_path) in
           let frozen_manifest=List.hd frozen.refs in
           if get frozen_manifest "sha256"<>get r "sha256" then invalid_arg "coarse frozen bundle generation changed";
           let prefix=Filename.dirname (text (get r "path")) in
           frozen.binding,List.map (fun ref_ -> obj (("path",s (if prefix="." then text (get ref_ "path")
             else prefix^"/"^text (get ref_ "path")))::List.remove_assoc "path" (fields ref_))) frozen.refs) in
    if manifest<>`Null then ignore (inventory_manifest manifest);
    if List.length source.inventory>4096 then invalid_arg "comparison inventory bound";
    List.iter (fun r -> ignore (verify_ref source.root r)) source.refs;
    List.iter (fun r -> ignore (verify_ref source.root r)) bundle_sources;
    let after=checked (run ~kind ~target:path ()) in
    if canonical before<>canonical after then invalid_arg "resource generation changed during snapshot";
    Ok (obj ["index",before;"manifest",manifest;"inventory",arr source.inventory;
      "measurements",arr (measurements source);"evidence",source.evidence;
      "sources",arr source.refs;"bundle_sources",arr bundle_sources])
  with Invalid_argument e | Failure e | Sys_error e -> Error e
    | End_of_file -> Error "truncated comparison resource source"
    | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
