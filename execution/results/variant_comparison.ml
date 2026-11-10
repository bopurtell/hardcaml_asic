module E = Evidence_json
module J = Identity_json
type target = Bundle of string | Coarse_run of string | Execution of string
type axis = Hardware | Resource_policy | Flow_configuration | Descriptive
let s x = `String x
let obj xs = `Assoc xs
let arr xs = `List xs
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let fields = function `Assoc xs -> xs | `Null -> [] | _ -> invalid_arg "comparison object required"
let list = function `List xs -> xs | `Null -> [] | _ -> invalid_arg "comparison array required"
let text = function `String x -> x | _ -> invalid_arg "comparison string required"
let checked = function Ok x -> x | Error e -> invalid_arg e
let wire x = Yojson.Safe.from_string (J.canonical x)
(* Evidence permits nonfinite observations; equality/digests retain their tokens,
   but the public comparison response is strict JSON with unavailable quantities. *)
let rec ordered = function
  | `Assoc xs -> obj (List.sort (fun (a,_) (b,_) -> String.compare a b) (List.map (fun (k,v) -> k,ordered v) xs))
  | `List xs -> arr (List.map ordered xs)
  | x -> x
let bytes x = E.record_bytes (ordered x)
let equal a b = bytes a=bytes b
let digest x = J.sha256_bytes (bytes x)
let rec json_safe = function
  | `Float f when not (Float.is_finite f) -> `Null
  | `Assoc xs -> obj (List.map (fun (k,v) -> k,json_safe v) xs)
  | `List xs -> arr (List.map json_safe xs)
  | x -> x
let axis_name = function Hardware -> "hardware" | Resource_policy -> "resource_policy"
  | Flow_configuration -> "flow_configuration" | Descriptive -> "descriptive"
let axis = function "hardware" -> Ok Hardware | "resource_policy" -> Ok Resource_policy
  | "flow_configuration" -> Ok Flow_configuration | "descriptive" -> Ok Descriptive
  | _ -> Error "invalid comparison axis"
let target ~kind ~path =
  if String.trim path="" then Error "comparison requires nonempty target paths" else
  match kind with "bundle" -> Ok (Bundle path) | "coarse_run" -> Ok (Coarse_run path)
    | "execution" -> Ok (Execution path) | _ -> Error "invalid comparison target kind"
let path = function Bundle p | Coarse_run p | Execution p -> p
let kind = function Bundle _ -> "bundle" | Coarse_run _ -> "coarse_run" | Execution _ -> "execution"
let resource_target = function Bundle p -> Resource_inspection_service.Bundle p
  | Coarse_run p -> Resource_inspection_service.Coarse_run p | Execution p -> Resource_inspection_service.Execution p
let read p =
  let raw=Retained_file.read p in
  (match J.parse raw with Ok _ -> () | Error e -> invalid_arg (J.error_message e)); raw
let read_json p = Yojson.Safe.from_string (read p)
let select keys j = obj (List.map (fun k -> k,get j k) keys)
let nonempty = function `Null | `List [] | `Assoc [] | `String "" -> false | _ -> true
let hashes refs = arr (List.map (fun r -> get r "sha256") refs)
let integer = function
  | `Int n -> Some (Z.of_int n)
  | `Intlit n -> (try Some (Z.of_string n) with Invalid_argument _ -> None)
  | _ -> None
let number ~count value = match integer value with
  | Some n when not count || Z.sign n>=0 -> Some (`Integer n)
  | Some _ -> None
  | None -> match value with
    | `Float f when Float.is_finite f && (not count || f>=0. && f<9007199254740992. && floor f=f) ->
      Some (if count then `Integer (Z.of_float f) else `Real f)
    | _ -> None
let delta ~count a b = match number ~count a,number ~count b with
  | Some (`Integer a),Some (`Integer b) -> Some (`Intlit (Z.to_string (Z.sub b a)))
  | Some (`Real a),Some (`Real b) -> let d=b-.a in if Float.is_finite d then Some (`Float d) else None
  (* Never round arbitrary-precision counts through float conversion. *)
  | _ -> None
let rational_delta left right =
  match integer (get left "numerator"),integer (get left "denominator"),
        integer (get right "numerator"),integer (get right "denominator") with
  | Some a,Some b,Some c,Some d when Z.sign b>0 && Z.sign d>0 ->
    let n=Z.sub (Z.mul c b) (Z.mul a d) and den=Z.mul b d in
    let gcd=Z.gcd n den in
    Some (obj ["numerator",`Intlit (Z.to_string (Z.div n gcd));
      "denominator",`Intlit (Z.to_string (Z.div den gcd))])
  | _ -> None
let required_controls axis =
  ["source";"target";"libraries";"environment";"timing_environment";"rc"] @
  (if axis=Hardware then [] else ["hardware"]) @
  (if axis=Resource_policy then [] else ["resource_policy"]) @
  (if axis=Flow_configuration then [] else ["flow_configuration"]) @
  (if axis=Hardware then [] else ["resource_contracts"])
let public_metric m = if m=`Null then `Null else
  obj (("controls_digest",s (digest (get m "controls")))::List.remove_assoc "controls" (fields (json_safe m)))
let compare_metric ~axis ~left ~right =
  let workload = List.exists (fun m -> match get m "metric_id" with
    | `String id -> String.starts_with ~prefix:"workload/" id | _ -> false) [left;right] in
  let l=get left "value" and r=get right "value" in
  let count=get left "quantity"=s "integer" in
  let classification=get left "classification" in
  let eligible=List.mem classification [s "observed";s "derived"] && get right "classification"=classification in
  let missing=ref [] and conflicts=ref [] in
  let unknown key = missing:= !missing@[s (key^": unknown or unavailable")] in
  let different key = conflicts:= !conflicts@[s (key^": differs")] in
  List.iter (fun key ->
    let a=get left key and b=get right key in
    if not (nonempty a && nonempty b) then unknown key else if not (equal a b) then different key)
    ["definition";"unit";"stage";"scope";"classification";"quantity"];
  List.iter (fun key ->
    let a=get (get left "controls") key and b=get (get right "controls") key in
    if not (nonempty a && nonempty b) then unknown key else if not (equal a b) then different key)
     (if workload then ["workload";"adapter";"clock";"model";"measurement";"rtl_mode";"tool"]
      else required_controls axis);
  if not eligible then unknown "measured observed/derived classification";
  let difference=delta ~count l r in
  if difference=None then unknown "finite compatible numeric values";
  let throughput=workload && (get left "metric_id"=s "workload/throughput" || get right "metric_id"=s "workload/throughput") in
  let ratio=if throughput then rational_delta left right else None in
  if throughput && ratio=None then unknown "exact operation/cycle ratio";
  let state=if !missing<>[] then "unavailable" else if !conflicts<>[] then "confounded" else "comparable" in
  obj ["metric_id",(if left=`Null then get right "metric_id" else get left "metric_id");
    "left",public_metric left;"right",public_metric right;"comparison",s state;
     "reasons",arr (!missing @ !conflicts);"right_minus_left",(if state="comparable" then Option.get difference else `Null)]
    |> fun row -> if throughput then obj (fields row @ ["right_minus_left_ratio",
      (if state="comparable" then Option.get ratio else `Null)]) else row

let rtl_identity manifest =
  let names=list (get manifest "synthesis_sources") in
  let refs=List.filter (fun r -> List.mem (get r "path") names) (list (get manifest "files")) in
  if names=[] || List.length names<>List.length refs then `Null else
  arr (List.map (select ["path";"sha256"]) refs)
let policies inventory = arr (List.map (fun r -> obj ["resource_id",get r "resource_id";
  "policy",get r "policy";"selection",get r "selection";"selected_implementation",get r "selected_implementation"]) inventory)
let contracts inventory = arr (List.map (fun r -> select ["resource_id";"requested_contract"] r) inventory)
let resource_registry = [
  "total_mapped_cells";"total_physical_netlist_cells";"macro_count";"standard_cell_count";
  "synthesis_standard_cell_area";"whole_design_sequential_area";"macro_library_area";"combined_cell_area_estimate";
  "final_standard_cell_area";"standard_cell_utilization";
  "native_all_component_area";"native_nonspacer_area";"native_spacer_area";"native_macro_area";
  "macro_footprint_area";"macro_component_area";"macro_component_count";
  "nonspacer_component_area";"nonspacer_component_count";"spacer_component_area";"spacer_component_count";
  "nonspacer_plus_macro_area";"all_component_area";"gross_core_area";"gross_die_area"]
let count_name name = List.mem name ["total_mapped_cells";"total_physical_netlist_cells";"macro_count";
  "standard_cell_count";"macro_component_count";"nonspacer_component_count";"spacer_component_count";
  "setup_violations";"hold_violations";"max_slew_violations";"max_cap_violations"]
let metric ~id ~name ~value ~classification ~unit_ ~stage ~definition ~scope ~controls ~sources =
  obj ["metric_id",s id;"name",s name;"value",value;"classification",classification;
    "quantity",s (if count_name name then "integer" else "real");"unit",unit_;"stage",stage;
    "definition",definition;"scope",scope;"controls",controls;"sources",sources]
let boundary_projection controls report = List.concat_map (fun checkpoint ->
  let boundary=get (get checkpoint "reference") "boundary" in
  List.filter_map (fun m ->
    let key=get m "raw_key" in
    if not (List.mem key [s "design__instance__count";s "design__instance__area"]) then None else
    let count=key=s "design__instance__count" in
    let name=if count then "total_mapped_cells" else "mapped_cell_area_estimate" in
    Some (metric ~id:("boundary/"^text boundary^"/"^name) ~name
      ~value:(if get m "availability"=s "available" then get m "value" else `Null)
      ~classification:(s (if count then "derived" else "estimate")) ~unit_:(get m "unit")
      ~stage:boundary ~definition:(s ("accumulated_boundary_snapshot."^text key))
      ~scope:(obj ["origin",s "boundary_snapshot_not_per_step_measurement";"boundary",boundary])
      ~controls ~sources:(get m "source"))) (list (get checkpoint "metrics"))) (list (get report "checkpoints"))
let control_update xs controls = obj (xs @ List.filter (fun (k,_) -> not (List.mem_assoc k xs)) (fields controls))
let corner_rows timing = list (get timing "corners")
let timing_projection controls timing = List.concat_map (fun row ->
  (* Only companion2's checked whole-design native metrics are authoritative.
     Historical/named native witnesses intentionally supply no scalar here. *)
  let native=get row "timing" in
  let sources=get native "metric_sources" in
  let corner=get row "corner" in
  let env=get (get row "inputs") "environment" in
  let libs=list (get sources "libraries") in
  let rc=if get timing "stage"=s "post_pnr" then get (get row "spef_selection") "retained"
    else if get timing "stage"=s "synthesized_netlist" then s "no_extracted_rc_pre_pnr" else `Null in
  let controls=control_update ["libraries",(if libs=[] then `Null else hashes libs);
    "environment",(if not (nonempty env && nonempty (get controls "environment")) then `Null
      else obj ["native",env;"execution",get controls "environment"]);
    "rc",rc] controls in
  List.filter_map (fun m ->
    let name=text (get m "key") in
    if not (List.mem name ["setup_slack";"hold_slack";"setup_violations";"hold_violations";"max_slew_violations";"max_cap_violations"]) then None else
    Some (metric ~id:("native/"^text corner^"/"^name) ~name ~value:(get m "value")
      ~classification:(s "observed") ~unit_:(get m "unit") ~stage:(get timing "stage")
      ~definition:(get m "raw_key") ~scope:(obj ["coverage",s "native_whole_design_metric";"corner",corner])
      ~controls ~sources)) (list (get native "metrics"))) (corner_rows timing)

(* Internal raw audit inventory, never inferred from arbitrary provenance labels.
   Readers' admitted reference tables supply expected hashes; checkpoint inventories
   and explicitly consulted auxiliary metadata complete the target closure. *)
type raw_source = { name:string; resolved:string; sha256:string option; bound:int }
type discovery = { directory:string; resolution:string option; members:(string * bool) list option }
let discover_postchecks directory =
  let resolution=if Retained_file.exists directory then Some (Unix.realpath directory) else None in
  let handle=try Some (Unix.opendir directory) with
    | Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> None in
  let members=Option.map (fun handle ->
    Fun.protect ~finally:(fun () -> Unix.closedir handle) (fun () ->
      let rec loop count acc = match Unix.readdir handle with
        | "." | ".." -> loop count acc
        | name ->
          if count>=8192 then invalid_arg "comparison check directory count exceeds 8192";
          let present=Sys.file_exists (directory^"/"^name^"/postcheck.json") in
          loop (count+1) ((name,present)::acc)
        | exception End_of_file -> List.sort compare acc in
      loop 0 [])) handle in
  {directory;resolution;members}
let audit_discoveries discoveries = List.iter (fun previous ->
  let current=discover_postchecks previous.directory in
  if current.resolution<>previous.resolution || current.members<>previous.members then
    invalid_arg "comparison final postcheck discovery generation changed") discoveries
let mib = 1024*1024
let source_inventory () =
  let entries=Hashtbl.create 64 and used=ref 0 in
  let add ?(aliases=false) ?expected ?(bound=128*mib) ?(optional=false) name =
    let resolved=if aliases && Retained_file.exists name then Unix.realpath name else name in
    match Hashtbl.find_opt entries name with
    | Some previous ->
      Option.iter (fun hash -> if previous.sha256<>Some hash then
        invalid_arg "comparison raw source has conflicting captured hashes") expected
    | None ->
      if Hashtbl.length entries>=8192 then invalid_arg "comparison raw source count exceeds 8192 per side";
      let sha256,bound=if optional && not (Retained_file.exists name) then None,0 else (
        let raw=Retained_file.read ~max_bytes:(min bound (1024*mib - !used)) resolved in
        used:= !used+String.length raw;
        let actual=J.sha256_bytes raw in
        Option.iter (fun hash -> if actual<>hash then
          invalid_arg "comparison raw source changed during inventory capture") expected;
        Some actual,String.length raw) in
      Hashtbl.add entries name {name;resolved;sha256;bound} in
  add,(fun () -> Hashtbl.fold (fun _ source acc -> source::acc) entries []
    |> List.sort (fun a b -> String.compare a.name b.name))
let audit_raw_sources sources =
  (* This is the last source-reading phase. No parsing or semantic/deep replay may
     follow it. Sequential audit is not an atomic cross-file/ABA snapshot. *)
  let unique=Hashtbl.create 64 in
  List.iter (fun source -> match Hashtbl.find_opt unique source.name with
    | None ->
      if Hashtbl.length unique>=16384 then invalid_arg "comparison final raw source count exceeds 16384";
      Hashtbl.add unique source.name source
    | Some previous ->
      if previous.sha256<>source.sha256 || previous.resolved<>source.resolved then
        invalid_arg "comparison final raw source generations conflict";
      Hashtbl.replace unique source.name {source with bound=min source.bound previous.bound}) sources;
  let sources=Hashtbl.fold (fun _ source acc -> source::acc) unique []
    |> List.sort (fun a b -> String.compare a.name b.name) in
  let used=ref 0 in
  List.iter (fun source -> match source.sha256 with
    | None -> if Retained_file.exists source.name then
        invalid_arg "comparison final raw source generation changed (previously absent)"
    | Some expected ->
      if source.resolved<>source.name && Unix.realpath source.name<>source.resolved then
        invalid_arg "comparison final raw source alias changed";
      let raw=Retained_file.read ~max_bytes:(min source.bound (2048*mib - !used)) source.resolved in
      used:= !used+String.length raw;
      if J.sha256_bytes raw<>expected then invalid_arg "comparison final raw source generation changed";
      if source.resolved<>source.name && Unix.realpath source.name<>source.resolved then
        invalid_arg "comparison final raw source alias changed") sources
type captured = { projection:E.t; metrics:E.t list; generation:string; raw_sources:raw_source list;
  workload_bundles:(raw_source * E.t list) list; roots:(string * string) list; discoveries:discovery list }
let capture target =
  let selected=path target in
  let p=Unix.realpath (path target) in
  let target=match target with Bundle _ -> Bundle p | Coarse_run _ -> Coarse_run p | Execution _ -> Execution p in
  let resource=checked (Resource_inspection_service.comparison_snapshot (resource_target target) ()) in
  let index=get resource "index" and manifest=get resource "manifest" in
  if manifest<>`Null then Manifest_metadata.validate manifest;
  let inventory=list (get resource "inventory") in
  let discoveries=ref [] in
  let record,methodology,timing,report,outcomes,report_metadata = match target with
    | Bundle _ -> `Null,`Null,`Null,`Null,obj ["execution",s "not_evaluated";"acceptance",s "not_evaluated"],[]
    | Coarse_run _ ->
     let record=read_json (p^"/run.json") in
     if not (Retained_file.exists (p^"/results.json")) || Timing_physical_flow.enabled manifest ||
        get record "timing_physical_expected"=`Bool true then
       discoveries:=[discover_postchecks (p^"/checks")];
      (* The service admits and consumes each actual descriptor once. No separate
         precheck followed by an unchecked reopen; comparison needs no human
         supplementary report reads. Stored resource assertions remain strict. *)
      let report=Report_service.run ~render_human:false ~strict_sources:true p in
      let stored=get report.results "resource_evidence" in
      if stored<>`Null then ignore (checked (Resource_evidence.verify_sources ~run_dir:p stored));
      let timing=if Timing_intent_flow.expected manifest || get record "timing_intent_expected"=`Bool true ||
        get record "timing_physical_expected"=`Bool true then checked (Timing_intent_flow.inspect ~run_dir:p ())
        else obj ["status",s "unavailable";"reason",s "historical run has no required linked timing evidence"] in
      record,`Null,timing,report.results,obj ["execution",get record "status";
        "acceptance",s (if Report_service.accepted report.verdict then "pass" else "fail");
        "acceptance_scope",s (match report.verdict.scope with Synthesis -> "synthesis" | Full -> "full" | Invalid -> "invalid");
        "resource_retention",get index "verdict";"postchecks",get report.results "postchecks"],report.metadata_sources
    | Execution _ ->
      let captured=read_json p in
      let report=checked (Stage_report_service.run (Stage_report_service.Stage_execution {path=p;selected_record=p})) in
      let report=wire report.envelope in
      let record=get report "execution" in
      if not (equal captured record) then invalid_arg "execution generation changed during comparison capture";
      record,get report "methodology",get report "timing_intent",report,
      obj ["execution",get record "status";"acceptance",get report "acceptance";
        "resource_retention",get report "resource_realization";"external_postcheck",get report "external_postcheck"],[] in
  let sources=list (get resource "sources") in
  let bundle_sources=list (get resource "bundle_sources") in
  let library_refs=List.filter (fun r -> get r "format"=s "liberty") sources in
  let source=get manifest "source_inputs" in
  let source=if source=`List [] then `Null else source in
  let hardware=match get manifest "hardware_variant" with
    | `Null -> rtl_identity manifest
    | declaration -> obj ["basis",s "checked_declared_configuration_not_semantic_use_proof";"declaration",declaration] in
  let sdc=List.filter (fun r -> get r "path"=s "constraints/top.sdc") (list (get manifest "files")) in
  let policy=if get index "inventory_status"=s "available" then
      (if inventory=[] then s "verified_empty_inventory" else policies inventory) else `Null in
  let controls=obj ["source",source;"target",get manifest "target";
    "hardware",hardware;"hardware_declaration",get manifest "hardware_variant";
    "resource_policy",policy;"resource_contracts",(if get index "inventory_status"<>s "available" then `Null
      else if inventory=[] then s "verified_empty_inventory" else contracts inventory);
    "flow_configuration",get manifest "flow_configuration";
    "timing_environment",(match sdc with [r] -> obj ["sdc_sha256",get r "sha256";"intent",get manifest "timing_intent"] | _ -> `Null);
    "libraries",(if library_refs=[] then `Null else hashes library_refs);
    "environment",get record "environment";"rc",`Null;"workload",`Null] in
  let resource_metrics=List.filter_map (fun m ->
    let name=text (get m "name") in
    if not (List.mem name resource_registry) then None else
    Some (metric ~id:("resource/"^text (get m "stage")^"/"^name) ~name ~value:(get m "value")
      ~classification:(get m "classification") ~unit_:(get m "unit") ~stage:(get m "stage")
      ~definition:(get m "definition") ~scope:(get m "scope") ~controls ~sources:(obj ["resource_source_digest",get index "source_digest"]))) (list (get resource "measurements")) in
  let metrics=resource_metrics @ boundary_projection controls report @ timing_projection controls timing in
  if List.length metrics>256 then invalid_arg "comparison metric count exceeds 256 per side";
  let ids=List.map (fun m -> text (get m "metric_id")) metrics in
  if List.length ids<>List.length (List.sort_uniq String.compare ids) then invalid_arg "duplicate comparison metric identity";
  let corner_outcomes=List.map (fun row ->
    let native=get row "timing" in
    obj (("corner",get row "corner")::fields (select ["setup_goal";"hold_goal";"electrical_goal";"annotation_goal"] native))) (corner_rows timing) in
  let timing_outcomes=obj (("corners",arr corner_outcomes)::fields (select ["status";"stage";"verdict";"exhaustive_coverage";"all_corner_timing_goal";
    "qualification_corner_coverage";"configured_corners";"executed_corners"] timing)) in
  let binding=obj ["resource",resource;"record",record;"report",report;"timing",timing;
    "consulted_metadata",arr report_metadata] in
  let generation=digest binding in
  let identities=obj ["bundle_identity",get manifest "identity";"run_id",get record "run_id";
    "attempt_id",get record "attempt_id";"methodology",get record "methodology";
    "occurrences",get record "occurrences";"publications",get record "artifacts";
    "record_sha256",(if record=`Null then `Null else
      let file=match target with Execution _ -> p | _ -> p^"/run.json" in
      let raw=read file in
      if not (equal (Yojson.Safe.from_string raw) record) then invalid_arg "record changed during comparison capture";
      s (J.sha256_bytes raw));
    "timing_provenance",select ["schema";"run_id";"attempt_id";"build_identity";"analysis_id";"stage";"status";
      "occurrence_id";"profile";"runtime_binding";"execution_provider"] (get timing "provenance");
    "timing_provenance_digest",(if timing=`Null then `Null else s (digest (get timing "provenance")))] in
  let producers=List.map (fun row ->
    let evidence=if get row "evidence"=`Null then row else get row "evidence" in
    let provenance=get evidence "provenance" in
    select ["producer";"corner";"stage";"schema";"audit_id";"build_identity"] provenance) (corner_rows timing) in
  let contexts=List.map (fun m -> let c=get m "controls" in digest c,c) metrics
    |> List.sort_uniq (fun (a,_) (b,_) -> String.compare a b) in
  let mapped_netlist=if get timing "synthesis"<>`Null then get (get timing "synthesis") "netlist"
    else get (get timing "originating_synthesis") "netlist" in
  let outputs=obj ["mapped_netlist",mapped_netlist;"implementation_netlist",get (get timing "implementation") "netlist"] in
  let add,raw_sources=source_inventory () in
  let root=match target with Execution _ -> Filename.dirname p | _ -> p in
  let references ?(aliases=false) root refs = List.iter (fun r ->
    let name=text (get r "path") in
    let name=if Filename.is_relative name then Filename.concat root name else name in
    add ~aliases ~expected:(text (get r "sha256")) name) refs in
  references ~aliases:true root (sources @ bundle_sources);
  references root report_metadata;
  references ~aliases:true root (list (get timing "sources"));
  (match target with
   | Bundle _ -> ()
   | Coarse_run _ ->
     add ~expected:(text (get identities "record_sha256")) (p^"/run.json");
     add ~optional:true (p^"/results.json");
     (* Stored resource assertions are verified before fresh reconciliation replaces
        their projection. Their consulted source table remains part of the fence. *)
     if Retained_file.exists (p^"/results.json") then (
       let stored=read_json (p^"/results.json") in
       references ~aliases:true root (list (get (get stored "resource_evidence") "evidence_refs")));
     if !discoveries<>[] then (
       let flow=text (get (get record "outputs") "flow") in
       let flow=if Filename.is_relative flow then Filename.concat p flow else flow in
       List.iter (fun name -> add ~aliases:true ~optional:true (flow^"/"^name))
         ["final/metrics.csv";"06-yosys-synthesis/reports/stat.json";"06-yosys-synthesis/state_out.json"];
       List.iter (fun discovery ->
         let checks=discovery.directory in
         List.iter (fun (name,present) ->
           let directory=checks^"/"^name in
           let path=directory^"/postcheck.json" in
           add ~aliases:true ~optional:true path;
           if present then (
             let check=read_json (Unix.realpath path) in
             if Timing_physical_flow.enabled manifest || get record "physical_checks_sha256"<>`Null then (
               List.iter (fun name -> add ~aliases:true ~optional:true (directory^"/"^name))
                 ["testbench.v";"replay-models.json"];
               List.iter (fun (name,_) -> add ~aliases:true ~optional:true (directory^"/"^name)) (fields (get check "artifacts"));
               List.iter (fun (suffix,extension) -> add ~aliases:true ~optional:true
                 (flow^"/final/"^suffix^"/"^text (get manifest "top_module")^extension)) ["gds",".gds";"nl",".nl.v"])))
           (Option.value discovery.members ~default:[])) !discoveries)
   | Execution _ ->
     add ~expected:(text (get identities "record_sha256")) p;
     let methodology=get record "methodology" in
     (match get methodology "snapshot_path" with
      | `String name -> add ~expected:(text (get methodology "snapshot_sha256")) name | _ -> ());
     List.iter (fun row ->
       let path=text (get (get row "reference") "path") in
       let checkpoint=checked (Checkpoint.read path) in
       add (path^"/manifest.json");
       List.iter (fun (name,(record:Checkpoint.file_record)) ->
         add ~expected:record.sha256 (Filename.concat path name)) checkpoint.files;
       List.iter (fun (name,(record:Checkpoint.file_record)) ->
         if not (String.starts_with ~prefix:"/nix/store/" name) then add ~aliases:true ~expected:record.sha256 name) checkpoint.external_files;
       List.iter (fun r -> match get r "sha256" with
         | `String _ -> references ~aliases:true root [r] | _ -> ()) (list (get row "supplementary")))
       (list (get report "checkpoints"));
     List.iter (fun r -> match get r "sha256" with
       | `String _ -> references ~aliases:true root [r] | _ -> ()) (list (get report "diagnostics"));
     add ~optional:true (Timing_analysis.path p);
     references root (list (get (get report "timing_analysis") "inventory")));
  let projection=obj ["kind",s (kind target);"target",s p;"source_digest",s generation;
    "identities",identities;"hardware_variant",get manifest "hardware_variant";
    "hardware_metadata_status",s (if get manifest "hardware_variant"=`Null then "unavailable" else "recorded");
    "inventory_status",get index "inventory_status";"resources",arr inventory;
    "methodology",methodology;"controls",controls;"outcomes",obj (("timing",timing_outcomes)::fields outcomes);
    "metric_contexts",obj contexts;"resource_sources",arr sources;"outputs",outputs;
    "verified_bundle_sources",arr bundle_sources;"consulted_metadata",arr report_metadata;
    "timing_producers",arr producers;
    "source_count",`Int (List.length sources + List.length bundle_sources + List.length report_metadata + List.length (list (get timing "sources")));
    "source_count_scope",s "sum_of_reader_reference_lists_not_deduplicated";
    "timing_source_count",`Int (List.length (list (get timing "sources")));
    "source_authentication",s "hash_checked_retained_assertions_not_execution_attestation"] in
  audit_discoveries !discoveries;
  {projection;metrics;generation;raw_sources=raw_sources ();workload_bundles=[];roots=[selected,p];discoveries= !discoveries}

let differences left right =
  let control_keys=["source";"target";"hardware";"hardware_declaration";"resource_policy";"resource_contracts";
    "flow_configuration";"libraries";"environment";"timing_environment";"rc";"workload"] in
  List.map (fun key ->
    let a=get (get left "controls") key and b=get (get right "controls") key in
    obj ["domain",s key;"key",s key;"left",a;"right",b;
      "state",s (if not (nonempty a && nonempty b) then "unavailable" else if equal a b then "equal" else "different")]) control_keys @
  [obj ["domain",s "resource_inventory";"key",s "resources";"left",get left "resources";"right",get right "resources";
    "state",s (if get left "inventory_status"<>s "available" || get right "inventory_status"<>s "available" then "unavailable"
      else if equal (get left "resources") (get right "resources") then "equal" else "different")];
   obj ["domain",s "methodology";"key",s "frozen_methodology";"left",get left "methodology";"right",get right "methodology";
    "state",s (if get left "methodology"=`Null || get right "methodology"=`Null then "unavailable"
      else if equal (get left "methodology") (get right "methodology") then "equal" else "different")]] @
  List.map (fun key -> let a=get (get left "outputs") key and b=get (get right "outputs") key in
    obj ["domain",s "output_bytes";"key",s key;"left",a;"right",b;
      "state",s (if get a "sha256"=`Null || get b "sha256"=`Null then "unavailable"
        else if get a "sha256"=get b "sha256" then "equal" else "different")]) ["mapped_netlist";"implementation_netlist"]
let attach_workload captured = function
  | None -> captured
  | Some path ->
    let workload=checked (Workload_result.snapshot path) in
    let binding=get workload "binding" in
    let variant=get captured.projection "hardware_variant" in
    if get binding "bundle_identity"<>get (get captured.projection "identities") "bundle_identity" ||
       variant=`Null || get binding "hardware_variant_identity"<>get variant "identity" then
      invalid_arg "workload result does not match target verified bundle/hardware identities";
    let controls=control_update ["workload",get binding "workload_identity"] (get captured.projection "controls") in
    let workload_controls=get workload "controls" in
    let contexts=control_update [digest workload_controls,workload_controls] (get captured.projection "metric_contexts") in
    let projection=obj (List.filter (fun (k,_) -> not (List.mem k ["source_digest";"controls";"metric_contexts"])) (fields captured.projection) @
      ["source_digest",s (digest (obj ["target",s captured.generation;"workload",get workload "source_digest"]));
      "controls",controls;
      "metric_contexts",contexts;
      "workload",workload;
      "workload_source_count",`Int (List.length (list (get (get workload "provenance") "sources")));
      "workload_association",s "verified_bundle_and_hardware_only_not_execution_attempt"]) in
    if List.length captured.metrics + List.length (list (get workload "metrics"))>256 then
      invalid_arg "comparison metric count exceeds 256 per side";
    (* Snapshot already supplies descriptor-admitted raw hashes/byte counts. Do not
       reopen workload records here: preserve the deep readers' capture boundary. *)
    let sources=List.map (fun r -> let name=text (get r "path") in
      let bound=match get r "bytes" with `Int n when n>=0 && n<=8*mib -> n
        | _ -> invalid_arg "invalid captured workload source bytes" in
      {name;resolved=name;sha256=Some (text (get r "sha256"));bound})
      (list (get (get workload "provenance") "sources")) in
    let root=Unix.realpath (Filename.dirname path) in
    let result_path=Filename.concat root (Filename.basename path) in
    let result_source=match List.find_opt (fun source -> source.name=result_path) sources with
      | Some source -> source | None -> invalid_arg "captured workload result source missing" in
    {captured with projection;metrics=captured.metrics @ list (get workload "metrics");
      raw_sources=captured.raw_sources @ sources;
      workload_bundles=[result_source,list (get (get workload "provenance") "bundle_sources")];
      roots=captured.roots @ [Filename.dirname path,root]}

let final_raw_fence captures =
  (* Resolve the workload's independently selected bundle from its captured raw
     result, not from the comparison target (which may be a different retained
     copy). This is bounded inventory preparation, not another semantic replay. *)
  let bundle_sources=List.concat_map (fun captured -> List.concat_map (fun (source,refs) ->
    let raw=Retained_file.read ~max_bytes:source.bound source.name in
    if Some (J.sha256_bytes raw)<>source.sha256 then
      invalid_arg "comparison final raw source generation changed";
    let root=text (get (Yojson.Safe.from_string raw) "bundle") in
    let root=Unix.realpath root in
    List.map (fun r ->
      let name=Filename.concat root (text (get r "path")) in
      let resolved=Unix.realpath name in
      if not (String.starts_with ~prefix:(if root="/" then "/" else root^"/") resolved) then
        invalid_arg "comparison final workload bundle source escapes root";
      {name;resolved;sha256=Some (text (get r "sha256"));bound=32*mib}) refs)
    captured.workload_bundles) captures in
  let roots=List.concat_map (fun captured -> captured.roots) captures in
  let check_roots () = List.iter (fun (name,resolved) ->
    if Unix.realpath name<>resolved then invalid_arg "comparison selected root generation changed") roots in
  check_roots ();
  let discoveries=List.concat_map (fun captured -> captured.discoveries) captures in
  audit_discoveries discoveries;
  audit_raw_sources (List.concat_map (fun captured -> captured.raw_sources) captures @ bundle_sources);
  audit_discoveries discoveries;
  check_roots ()

let inspect ~left ~right ~axis ?left_workload ?right_workload () =
  try
    let a=attach_workload (capture left) left_workload
    and b=attach_workload (capture right) right_workload in
    let keys=List.map (fun m -> text (get m "metric_id")) (a.metrics@b.metrics) |> List.sort_uniq String.compare in
    let find id xs=Option.value (List.find_opt (fun m -> get m "metric_id"=s id) xs) ~default:`Null in
    let metrics=List.map (fun id -> compare_metric ~axis ~left:(find id a.metrics) ~right:(find id b.metrics)) keys in
    let result=obj ["contract",s "hardcaml-asic.variant-comparison";"schema_version",`Int 1;
      "policy",s "bounded-pair-v1";"axis",s (axis_name axis);"left",a.projection;"right",b.projection;
      "differences",arr (differences a.projection b.projection);"metrics",arr metrics;
       "workload",(if left_workload=None && right_workload=None then
         obj ["status",s "unavailable";"reason",s "no verified generic workload-result contract";
           "metrics",arr (List.map s ["firmware_size";"fetch_count";"latency";"throughput"])]
         else obj ["status",s "explicit_results";"policy",s "memory-1rw-workload-pair-v1";
           "association",s "bundle_and_hardware_not_execution_attempt";
           "unsupported_metrics",arr (List.map s ["firmware_size";"fetch_count";"physical_speed"]) ]);
      "truncated",`Bool false] |> json_safe in
    let result=obj (("comparison_digest",s (digest result))::fields result) in
    if String.length (E.record_bytes result)>1024*1024 then invalid_arg "comparison whole JSON exceeds 1MiB";
    (* Replay each complete source closure, not just an outer record hash. This also
       rejects a running generation changing while the opposite side is read. *)
    if (capture left).generation<>a.generation || (capture right).generation<>b.generation then
      invalid_arg "comparison source generation changed; retry explicit targets";
    List.iter (fun (path,captured) -> match path with None -> () | Some path ->
      ignore (checked (Workload_result.recheck path
        ~expected_digest:(text (get (get captured.projection "workload") "source_digest")))))
       [left_workload,a;right_workload,b];
    final_raw_fence [a;b];
    Ok result
  with Invalid_argument e | Failure e | Sys_error e -> Error e
    | End_of_file -> Error "truncated comparison evidence"
    | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
