open Hardcaml_asic_execution
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let str = function `String s -> s | _ -> failwith "string expected"
let xs = function `List xs -> xs | _ -> failwith "list expected"
let hash = Identity_json.sha256_bytes
let canonical j = match Identity_json.parse (Yojson.Safe.to_string j) with Ok j -> Identity_json.canonical j | Error e -> failwith (Identity_json.error_message e)
let checks=ref 0
let check label b = incr checks; if not b then failwith label
let ok = function Ok j -> j | Error e -> failwith e
let bad label = function Error _ -> check label true | Ok _ -> check label false
let save p s = let c=open_out_bin p in Fun.protect ~finally:(fun () -> close_out_noerr c) (fun () -> output_string c s)
let root () = let p=Filename.temp_file "resource-inspection-" "" in Sys.remove p; Unix.mkdir p 0o700;p
let manifest ?snapshot root resources files =
  let payload=`Assoc ["project",`String "fixture";"top_module",`String "top";
    "resources",`List resources;"files",`List files] in
  let payload=match payload,snapshot with
    | `Assoc fields,Some j -> `Assoc (("resource_snapshot",j)::fields) | _ -> payload in
  let id=hash (Yojson.Safe.to_string payload) in
  let manifest=match payload with `Assoc xs -> `Assoc (xs@["identity",`String id]) | _ -> assert false in
  let raw=Yojson.Safe.to_string manifest in save (root^"/manifest.json") raw;id,raw
let legacy id requirement selection = `Assoc ["id",`String id;"request",`String "((kind ram) (contract ((width 16) (depth 256))))";
  "requirement",`String requirement;"policy",`String "Default";"selection",`String selection;
  "elaborated_as",`String "Selected_implementation"]
let () =
  let r=root () in
  let flops=legacy "explicit" "Flops" "((implementation Flops) (reason Explicit_flops))" in
  let fallback=legacy "fallback" "Exact_or_flop_fallback" "((implementation Flops) (reason (Flop_fallback (no_exact_mapping_in target))))" in
  let files=List.init 80 (fun i -> let path=Printf.sprintf "member-%03d" i in
    let raw="member "^string_of_int i in save (r^"/"^path) raw;
    `Assoc ["path",`String path;"sha256",`String (hash raw)]) in
  let exhausted=legacy "limited" "Exact_or_flop_fallback"
    "((implementation Flops) (reason (Budget_exhausted (macro MAC) (limit 0))))" in
  let _,_=manifest r [flops;fallback;exhausted] files in
  let run ?query ?resource_id ?section ?expected_digest ?artifact_id ?offset ?limit () =
    Resource_inspection_service.run ~kind:"bundle" ~target:r ?query ?resource_id ?section ?expected_digest ?artifact_id ?offset ?limit () in
  let index=ok (run ()) in
  check "bundle inventory" (List.length (xs (get index "rows"))=3);
  check "budget reason includes macro and limit" (get (List.nth (xs (get index "rows")) 2) "reason"
    = `String "budget_exhausted (macro=MAC, limit=0)");
  let structured=root () in
  let atom s=`Assoc ["atom",`String s] and seq xs=`Assoc ["list",`List xs] in
  let kind name=`Assoc ["kind",`String name] and status name=`Assoc ["status",`String name] in
  let record=`Assoc ["id",`Assoc ["display",`String "limited";"path",`List [];"name",`String "limited"];
    "request",`Assoc ["kind",`String "ram";"contract",seq [seq [atom "width";atom "16"];seq [atom "depth";atom "256"]]];
    "requirement",`String "exact_or_flop_fallback";"policy",kind "default";
    "selection",`Assoc ["implementation",kind "flops";"reason",`Assoc ["kind",`String "budget_exhausted";"macro",`String "MAC";"limit",`Int 0]];
    "elaborated_as",`String "selected_implementation";"sources",`List [kind "generated_synthesis_rtl"];
    "actual_instances",status "not_applicable";"collateral",`List [];"physical_intent",status "unavailable"] in
  let snapshot=`Assoc ["schema_version",`Int 1;"mode",`String "implementation";"scope",`String "resolved";
    "project",`String "fixture";"top",`String "top";"resources",`List [record]] in
  let legacy_record=match exhausted with `Assoc fields ->
    `Assoc (("source_roles",`List [`String "Generated_synthesis_rtl"])::fields) | _ -> assert false in
  let _,_=manifest ~snapshot structured [legacy_record] [] in
  let structured_index=ok (Resource_inspection_service.inspect (Bundle structured) ()) in
  check "structured and legacy inspection explain the same budget" (get (List.hd (xs (get structured_index "rows"))) "reason"
    = get (List.nth (xs (get index "rows")) 2) "reason");
  List.iter (fun (req,details) ->
    let legacy_record=legacy "limited" req ("((implementation Flops) (reason (Budget_exhausted "^details^")))") in
    let _,_=manifest structured [legacy_record] [] in
    bad "inconsistent legacy budget" (Resource_inspection_service.inspect (Bundle structured) ()))
    ["Exact","(macro MAC) (limit 0)"; "Exact_or_flop_fallback","(macro MAC) (limit 1)";
     "Exact_or_flop_fallback","(macro MAC) (limit -1)"; "Exact_or_flop_fallback","(limit 0) (limit 0)"];
  check "bundle availability explicit" (get index "inventory_status"=`String "available" && get index "inventory_reason"=`Null);
  check "explicit versus fallback reason" (get (List.hd (xs (get index "rows"))) "reason"<>get (List.nth (xs (get index "rows")) 1) "reason");
  check "inventory never realized" (get (List.hd (xs (get index "rows"))) "final"=`String "unavailable");
  let digest=str (get index "source_digest") in
  let cat offset=ok (run ~query:"artifacts" ~expected_digest:digest ~offset ~limit:32 ()) in
  check "all 81 artifacts paginated" (List.length (xs (get (cat 0) "entries"))=32 && List.length (xs (get (cat 64) "entries"))=17);
  let entry=List.hd (xs (get (cat 64) "entries")) in
  let b=ok (run ~query:"bytes" ~expected_digest:digest ~artifact_id:(str (get entry "id")) ~limit:3 ()) in
  check "bounded raw bytes" (String.length (str (get b "bytes_hex"))=6);
  bad "unknown artifact rejected" (run ~query:"bytes" ~expected_digest:digest ~artifact_id:"../../etc/passwd" ());
  bad "stale digest" (run ~query:"artifacts" ~expected_digest:(String.make 64 '0') ());
  bad "missing fence" (run ~query:"detail" ~resource_id:"explicit" ~section:"inventory" ());
  bad "unknown section" (run ~query:"detail" ~expected_digest:digest ~resource_id:"explicit" ~section:"invented" ());
  bad "unknown resource" (run ~query:"detail" ~expected_digest:digest ~resource_id:"other" ~section:"inventory" ());
  bad "oversized count" (run ~query:"artifacts" ~expected_digest:digest ~limit:65 ());
  bad "offset past catalog end" (run ~query:"artifacts" ~expected_digest:digest ~offset:82 ());
  bad "negative offset" (run ~query:"artifacts" ~expected_digest:digest ~offset:(-1) ());
  bad "unexpected index option" (run ~section:"inventory" ());
  let detail=ok (run ~query:"detail" ~resource_id:"explicit" ~section:"inventory" ~expected_digest:digest ()) in
  check "bounded inventory detail" (List.length (xs (get detail "items"))=1 && String.length (Yojson.Safe.to_string detail)<65536);
  save (r^"/member-000") "tampered";
  bad "member bytes checked not just manifest" (run ());
  let empty=root () in
  let _,_=manifest empty [] [] in
  let index=ok (Resource_inspection_service.run ~kind:"bundle" ~target:empty ()) in
  check "successful empty inventory" (get index "rows"=`List [] && get index "verdict"=`String "not_evaluated");
  check "available empty is not unknown" (get index "inventory_status"=`String "available" && get index "inventory_reason"=`Null);
  let missing=root () in
  let payload=`Assoc ["project",`String "historical";"top_module",`String "top";"files",`List []] in
  let id=hash (Yojson.Safe.to_string payload) in
  let raw=match payload with `Assoc fields -> Yojson.Safe.to_string (`Assoc (fields@["identity",`String id])) | _ -> assert false in
  save (missing^"/manifest.json") raw;
  let missing_index=ok (Resource_inspection_service.inspect (Bundle missing) ()) in
  check "missing historical bundle inventory unavailable not zero" (get missing_index "inventory_status"=`String "unavailable"
    && get missing_index "inventory_reason"<>`Null && get missing_index "rows"=`List []);
  let malformed=`Assoc ["project",`String "historical";"top_module",`String "top";"files",`List [];"resources",`Null] in
  let id=hash (Yojson.Safe.to_string malformed) in
  let raw=match malformed with `Assoc fields -> Yojson.Safe.to_string (`Assoc (fields@["identity",`String id])) | _ -> assert false in
  save (missing^"/manifest.json") raw;
  bad "malformed inventory not silently unavailable" (Resource_inspection_service.inspect (Bundle missing) ());
  let outside=Filename.temp_file "resource-inspection-outside" ".txt" in save outside "outside";
  Unix.symlink outside (empty^"/escape");
  let _,_=manifest empty [] [`Assoc ["path",`String "escape";"sha256",`String (hash "outside")]] in
  bad "escaped member rejected" (Resource_inspection_service.run ~kind:"bundle" ~target:empty ());
  let large=root () in
  let fd=Unix.openfile (large^"/large") [Unix.O_WRONLY;Unix.O_CREAT] 0o600 in
  Unix.ftruncate fd (33*1024*1024);Unix.close fd;
  let _,_=manifest large [] [`Assoc ["path",`String "large";"sha256",`String (String.make 64 '0')]] in
  bad "oversized member rejected before read" (Resource_inspection_service.inspect (Bundle large) ());
  bad "execution cannot be guessed from bundle dir" (Resource_inspection_service.run ~kind:"execution" ~target:r ());
  bad "unsupported kind" (Resource_inspection_service.run ~kind:"latest" ~target:r ());
  (* Required macro observation failure is valid, inspectable evidence. *)
  let coarse=root () in
  let macro=legacy "program" "Exact" "((implementation (Macro SRAM)) (reason (Exact_mapping (technology target))))" in
  let macro_contract=`Assoc ["schema_version",`Int 1;"macros",`List [`Assoc ["resource",`String "program";"module",`String "SRAM";"instance",`String "hc_resource_7_program"]]] in
  let payload=`Assoc ["project",`String "fixture";"top_module",`String "top";"resources",`List [macro];"files",`List [];"macro_contract",macro_contract] in
  let id=hash (Yojson.Safe.to_string payload) in
  let raw=match payload with `Assoc xs -> Yojson.Safe.to_string (`Assoc (xs@["identity",`String id])) | _ -> assert false in
  save (coarse^"/manifest.json") raw;
  save (coarse^"/run.json") (Yojson.Safe.to_string (`Assoc ["run_id",`String "real-coarse-run";"build_identity",`String id;
    "manifest_sha256",`String (hash raw);"requested_stage",`String "synthesis";"completed_stage",`String "synthesis";
    "outputs",`Assoc ["flow",`String "flow"]]));
  let failed=ok (Resource_inspection_service.run ~kind:"coarse_run" ~target:coarse ()) in
  check "failed verdict still has available inventory" (get failed "inventory_status"=`String "available");
  check "failed required resource remains evidence" (get failed "verdict"=`String "fail" && List.length (xs (get failed "rows"))=1);
  check "coarse identity preserved" (get (get failed "provenance") "run_id"=`String "real-coarse-run");
  let execution_root=root () in
  let execution_path=execution_root^"/execution-attempt.json" in
  let execution=`Assoc ["attempt_id",`String "named-attempt";
    "plan",`Assoc ["schema_version",`Int 3;"stages",`List [`String "synthesis"];"initial_boundary",`Null];
    "occurrences",`List [`Assoc ["occurrence_id",`String "named-attempt/implementation.synthesis";
      "action_key",`String "implementation.synthesis";"status",`String "failed";"exit_code",`Int 2;
      "duration_seconds",`Float 1.;"best_effort",`Bool false;"error",`Null;"verdict",`Null]];
    "artifacts",`List [];"status",`String "failed";"exit_code",`Int 2] in
  let execution_raw=Yojson.Safe.to_string execution in save execution_path execution_raw;
  let named=ok (Resource_inspection_service.inspect (Execution execution_path) ()) in
  check "historical named inventory honestly unavailable" (get named "rows"=`List [] &&
    get (get named "provenance") "build_identity"=`Null && get named "verdict"=`String "not_evaluated");
  check "historical named availability explicit" (get named "inventory_status"=`String "unavailable" && get named "inventory_reason"<>`Null);
  let final=root () in save (final^"/manifest.json") raw;
  let checkpoint=root () in Unix.mkdir (checkpoint^"/views") 0o700;
  List.iter (fun name -> save (checkpoint^"/views/"^name) name) ["nl";"json_h";"odb";"def"];
  let state=`Assoc (["metrics",`Assoc []] @ List.map (fun name -> name,`String (checkpoint^"/views/"^name)) ["nl";"json_h";"odb";"def"]) in
  save (checkpoint^"/state.json") (canonical state);
  let host=execution_root^"/host" in save host "host collateral";
  let host_record=ok (Checkpoint.file_record host) in
  let external_=`Assoc [host,`Assoc ["sha256",`String host_record.sha256;"bytes",`Int (int_of_string host_record.bytes)]] in
  save (checkpoint^"/closure.json") (canonical external_);save (checkpoint^"/resolved.json") "{}";
  save (checkpoint^"/segment.json") (canonical (`Assoc ["boundary",`String "synthesis";"metrics",`Assoc [];
    "metric_origin",`String "cumulative_final_state; not per-step measurement provenance";"run_dir",`String checkpoint]));
  let names=["state.json";"closure.json";"resolved.json";"segment.json";"views/nl";"views/json_h";"views/odb";"views/def"] in
  let files=List.map (fun name -> let r=ok (Checkpoint.file_record (checkpoint^"/"^name)) in
    name,`Assoc ["sha256",`String r.sha256;"bytes",`Int (int_of_string r.bytes)]) names in
  let image="sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a" in
  let compatibility=`Assoc ["bundle_identity",`String id;"binding",`Assoc ["image_identity",`Assoc ["image_id",`String image]]] in
  let payload=`Assoc ["schema_version",`Int 2;"location",`String checkpoint;"boundary",`String "synthesis";
    "restart_supported",`Bool true;"restart_targets",`List [`String "floorplan";`String "placement"];
    "tested_image_id",`String image;"closure_scope",`String "path_bound";"compatibility",compatibility;
    "files",`Assoc files;"external",external_;"metrics",`Assoc [];"acceptance",`String "not_evaluated"] in
  let checkpoint_id=hash (canonical payload) in
  let checkpoint_manifest=match payload with `Assoc xs -> `Assoc (("identity",`String checkpoint_id)::xs) | _ -> assert false in
  save (checkpoint^"/manifest.json") (canonical checkpoint_manifest);
  let publication=`Assoc ["path",`String checkpoint;"identity",`String checkpoint_id;"boundary",`String "synthesis";
    "restart_supported",`Bool true;"restart_targets",`List [`String "floorplan";`String "placement"];
    "tested_image_id",`String image;"closure_scope",`String "path_bound";"acceptance",`String "not_evaluated"] in
  let complete=match execution with `Assoc xs -> `Assoc (List.map (fun (k,v) -> k,(match k with
    | "status" -> `String "completed" | "exit_code" -> `Int 0 | "artifacts" -> `List [publication]
    | "occurrences" -> `List (List.map (function `Assoc xs -> `Assoc (List.map (fun (k,v) -> k,(if k="status" then `String "completed" else if k="exit_code" then `Int 0 else v)) xs) | x -> x) (match v with `List items -> items | _ -> assert false))
    | _ -> v)) xs) | _ -> assert false in
  let execution_raw=canonical complete in save execution_path execution_raw;
  let selected_checkpoints=`List [`Assoc ["path",`String checkpoint;"identity",`String checkpoint_id;"boundary",`String "synthesis"]] in
  let origin=`Assoc ["contract",`String "hardcaml-asic.segment-finalization";"schema_version",`Int 1;
    "operation",`String (final^"/operation.json");"selected_checkpoints",selected_checkpoints] in
  let context=`Assoc ["run_id",`String "final-attempt";"build_identity",`String id;"manifest_sha256",`String (hash raw);
    "requested_stage",`String "synthesis";"completed_stage",`String "synthesis";
    "outputs",`Assoc ["flow",`String "evidence"];"evidence_origin",origin] in
  let evidence=Resource_evidence.collect ~context ~run_dir:final () in
  let results=Yojson.Safe.to_string (`Assoc ["resource_evidence",evidence]) in
  save (final^"/results.json") results;
  let operation=`Assoc ["contract",`String "hardcaml-asic.segment-finalization";"schema_version",`Int 1;
    "attempt_id",`String "final-attempt";"status",`String "failed";"phase",`String "collection";
    "started_at",`String "2026-10-28T00:00:00Z";"ended_at",`String "2026-10-28T00:01:00Z";
    "acceptance",`String "not_evaluated";"external_postcheck",`String "not_evaluated";
    "request",`Assoc ["accept",`Bool false];"commands",`List [];"exit_code",`Int 1;"error",`Null;
    "selected_checkpoints",selected_checkpoints;
    "execution",`Assoc ["path",`String execution_path;"attempt_id",`String "named-attempt";"sha256",`String (hash execution_raw)];
    "evidence_context",context;"results",`Assoc ["path",`String (final^"/results.json");"sha256",`String (hash results)]] in
  save (final^"/operation.json") (Yojson.Safe.to_string operation);
  let finalized=ok (Resource_inspection_service.inspect (Finalization final) ()) in
  check "finalization without fake run.json" (not (Sys.file_exists (final^"/run.json")) && get finalized "verdict"=`String "fail");
  check "finalization keeps original named identity" (get (get (get (get finalized "provenance") "finalization") "execution") "attempt_id"=`String "named-attempt");
  let mutate_operation key value =
    let changed=match operation with `Assoc fields -> `Assoc ((key,value)::List.remove_assoc key fields) | _ -> assert false in
    save (final^"/operation.json") (canonical changed);
    bad ("finalization rejects mismatched "^key) (Resource_inspection_service.inspect (Finalization final) ());
    save (final^"/operation.json") (canonical operation) in
  mutate_operation "selected_checkpoints" (`List []);
  mutate_operation "execution" (`Assoc ["path",`String execution_path;"attempt_id",`String "other-run";"sha256",`String (hash execution_raw)]);
  mutate_operation "results" (`Assoc ["path",`String (final^"/results.json");"sha256",`String (String.make 64 '0')]);
  mutate_operation "results" (`Assoc ["path",`String execution_path;"sha256",`String (hash execution_raw)]);
  (if Array.length Sys.argv>1 then (
    let helper=Sys.argv.(1) in
    let invoke kind target query exit verdict args =
      let output=Filename.temp_file "resource-inspection-cli" ".json" in
      let fd=Unix.openfile output [Unix.O_WRONLY;Unix.O_TRUNC] 0o600 in
      let argv=Array.of_list ([helper;"--kind";kind;"--target";target;"--query";query;"--json"]@args) in
      let pid=Unix.create_process helper argv Unix.stdin fd Unix.stderr in
      Unix.close fd;
      let _,status=Unix.waitpid [] pid in
      check ("native "^query^" exit matches "^verdict) (status=Unix.WEXITED exit);
      let channel=open_in_bin output in
      let raw=Fun.protect ~finally:(fun () -> close_in_noerr channel) (fun () -> really_input_string channel (in_channel_length channel)) in
      let j=Yojson.Safe.from_string raw in
      check ("native "^query^" bounded verdict envelope") (get j "verdict"=`String verdict
        && String.length raw<(if query="index" then 2*1024*1024 else 65536));j in
    let check_source kind target exit verdict resource =
      let index=invoke kind target "index" exit verdict [] in
      let fence=["--expected-digest";str (get index "source_digest")] in
      (match resource with None -> () | Some id ->
        ignore (invoke kind target "detail" exit verdict (fence@["--resource-id";id;"--section";"inventory"])));
      let catalog=invoke kind target "artifacts" exit verdict fence in
      let artifact=List.hd (xs (get catalog "entries")) in
      ignore (invoke kind target "bytes" exit verdict (fence@["--artifact-id";str (get artifact "id");"--limit";"16"])) in
    check_source "finalization" final 1 "fail" (Some "program");
    let good_bundle=root () in let _,_=manifest good_bundle [] [] in
    check_source "bundle" good_bundle 0 "not_evaluated" None;
    (match Sys.getenv_opt "PT6_RETAINED_ROOT" with None -> () | Some root ->
      check_source "coarse_run" root 0 "pass" (Some "program"))));
  save execution_path (execution_raw^" ");
  bad "finalization execution hash fence" (Resource_inspection_service.inspect (Finalization final) ());
  (match Sys.getenv_opt "PT6_RETAINED_ROOT" with None -> () | Some root ->
    let inspect ?query ?resource_id ?section ?expected_digest ?offset ?limit () =
      Resource_inspection_service.run ~kind:"coarse_run" ~target:root ?query ?resource_id ?section ?expected_digest ?offset ?limit () |> ok in
    let index=inspect () in
    let row=List.hd (xs (get index "rows")) in
    check "actual inventory available" (get index "inventory_status"=`String "available" && get index "inventory_reason"=`Null);
    let measures=xs (get index "measurements") in
    let named name=List.find (fun m -> get m "name"=`String name) measures in
    check "native area is observed" (get (named "final_standard_cell_area") "classification"=`String "observed");
    check "exact component area is derived" (get (named "nonspacer_component_area") "classification"=`String "derived");
    check "gross rectangle area is derived" (get (named "gross_core_area") "classification"=`String "derived");
    check "ratio classification explicitly derived" (List.exists (fun m -> get m "unit"=`String "ratio" && get m "classification"=`String "derived") measures);
    check "unknown unestablished sequential count not measured" (get (named "sequential_cell_count") "classification"=`String "unknown"
      && get (named "sequential_cell_count") "value"=`Null);
    check "resource synthesis cost stays estimate" (List.exists (fun m -> get m "stage"=`String "synthesis" && get m "classification"=`String "estimate") (xs (get row "measurements")));
    check "verified resource footprint is derived" (List.exists (fun m -> get m "stage"=`String "final_physical" && get m "classification"=`String "derived") (xs (get row "measurements")));
    check "actual 76 references" (get index "artifact_count"=`Int 76);
    check "actual exact instance" (get row "instances"=`List [`String "hc_resource_7_program"]);
    let digest=str (get index "source_digest") in
    let page=inspect ~query:"detail" ~resource_id:"program" ~section:"timing" ~expected_digest:digest ~limit:64 () in
    check "actual 186 paths paged" (get page "total"=`Int 186 && String.length (Yojson.Safe.to_string page)<=65536);
    let page=inspect ~query:"detail" ~resource_id:"program" ~section:"electrical" ~expected_digest:digest () in
    check "actual 372 electrical points paged" (get page "total"=`Int 372);
    check "electrical compliance qualification retained" (List.exists (fun item ->
      List.mem (`List [`String "electrical_compliance";`String "not_assessed"]) (xs (get item "fields"))) (xs (get page "items")));
    let page=inspect ~query:"artifacts" ~expected_digest:digest ~offset:64 ~limit:32 () in
    check "actual last source page" (List.length (xs (get page "entries"))=12));
  (match Sys.getenv_opt "PT6_SYNTHESIS_ROOT" with None -> () | Some root ->
    let index=ok (Resource_inspection_service.inspect (Coarse_run root) ()) in
    let measures=xs (get index "measurements") in
    List.iter (fun name ->
      let m=List.find (fun m -> get m "name"=`String name) measures in
      check ("synthesis "^name^" remains estimate") (get m "classification"=`String "estimate" && get m "value"<>`Null))
      ["synthesis_standard_cell_area";"macro_library_area";"combined_cell_area_estimate"];
    let count=List.find (fun m -> get m "name"=`String "total_mapped_cells") measures in
    check "synthesis count derived not library area" (get count "classification"=`String "derived" && get count "value"=`Int 176));
  Printf.printf "resource inspection: %d checks passed\n" !checks
