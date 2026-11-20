module J = Identity_json
module S = Execution_plan.Stage
let checked = function Ok x -> x | Error e -> invalid_arg e
let codec = function Ok x -> x | Error e -> invalid_arg (J.error_message e)
let str s = codec (J.string s)
let obj xs = codec (J.object_ xs)
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String s -> s | _ -> invalid_arg "expected report string"
let fields = function J.Object xs -> xs | _ -> invalid_arg "expected metrics object"
let json p = codec (Result_store.read_json p)
let protect f = try Ok (f ()) with
  | Invalid_argument s | Failure s | Sys_error s -> Error s
  | End_of_file -> Error "report source truncated while reading"
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
type target = Coarse_run of string | Checkpoint of string
            | Stage_execution of {path:string;selected_record:string}
let select path = protect (fun () ->
  let path=Unix.realpath path in
  match (Unix.stat path).Unix.st_kind with
  | Unix.S_REG -> ignore (checked (Stage_execution_record.read path)); Stage_execution {path;selected_record=path}
  | Unix.S_DIR ->
    let names=Array.to_list (Sys.readdir path) in
    let marker n=List.mem n names in
    let coarse=marker "run.json" in
    let checkpoint=marker "manifest.json" || marker "segment.json" || marker "state.json" || marker "closure.json" in
    let executions=List.filter (fun n -> String.starts_with ~prefix:"execution-" n && String.ends_with ~suffix:".json" n
      && not (String.ends_with ~suffix:".timing-analysis.json" n)) names |> List.sort String.compare in
    let observation=marker "000000000001.json" || marker "attempt.json" || marker "events.jsonl" || marker "snapshot.json" in
    let count=List.length (List.filter Fun.id [coarse;checkpoint;executions<>[];observation]) in
    if count>1 then invalid_arg "conflicting report target markers (run/checkpoint/execution/observation)";
    if coarse then Coarse_run path
    else if checkpoint then (ignore (checked (Checkpoint.read path));Checkpoint path)
    else if executions<>[] then (match executions with
      | [name] -> let selected_record=Filename.concat path name in
        ignore (checked (Stage_execution_record.read selected_record));Stage_execution {path;selected_record}
      | _ -> invalid_arg ("multiple stage execution records: "^String.concat ", " executions^
                         "; report an explicit execution JSON file"))
    else if observation then invalid_arg "observation directory: use hardcaml-asic-flow observation inspect TARGET"
    else invalid_arg "unrecognized report target: expected run, checkpoint, stage workspace, or execution JSON"
  | _ -> invalid_arg "report target must be a directory or execution JSON file")
type t = {envelope:J.t;human:string}
let methodology t = match get t.envelope "methodology" with
  | J.Null -> Historical_methodology.not_recorded | j -> j
let known = [
  "design__instance__area",("Mapped cell area","µm²",false);
  "design__instance__count",("Mapped cells","cells",true);
  "design__instance_unmapped__count",("Unmapped cells","cells",true);
  "synthesis__check_error__count",("Synthesis check errors","count",true);
  "design__inferred_latch__count",("Inferred latches","count",true);
  "design__lint_error__count",("Lint errors","count",true);
  "design__lint_warning__count",("Lint warnings","count",true);
  "design__lint_timing_construct__count",("Lint timing constructs","count",true)]
let numeric = function J.Integer s -> float_of_string_opt s | J.Float f -> Some f | _ -> None
let metric source (key,raw) =
  let recognized=List.mem_assoc key known in
  let label,unit,count=Option.value (List.assoc_opt key known) ~default:(key,"",false) in
  let value=get raw "value" in
  let valid=match numeric value with Some f -> Float.is_finite f && f>=0. &&
    (not count || (f>=0. && floor f=f)) | None -> false in
  let available=get raw "availability"=str "available" && (not recognized || valid) in
  let reason=if available then J.null else match get raw "reason" with
    | J.String _ as r -> r
    | _ -> str (if get raw "availability"=str "available" then
        (if count then "not_a_finite_nonnegative_integer" else "nonfinite_or_nonnumeric") else "not_recorded") in
  obj ["label",str label;"raw_key",str key;"availability",(if recognized then str (if available then "available" else "unavailable") else get raw "availability");
       "value",(if not recognized || available then value else J.null);"unit",(if unit="" then J.null else str unit);
        "reason",reason;"source",obj ["path",str source;"field",str ("metrics."^key);"raw",raw]]
let decode_segment_bytes ~expected_sha256 raw = protect (fun () ->
  if String.length raw>32*1024*1024 then invalid_arg "checkpoint segment byte bound exceeded";
  if J.sha256_bytes raw<>expected_sha256 then invalid_arg "checkpoint segment bytes differ from validated inventory SHA-256";
  codec (J.parse raw))
let checkpoint ?(supplementary=true) ?(admit_metadata=(fun _ -> ())) reference compatibility =
  let path=text (get reference "path") in
  admit_metadata (Filename.concat path "manifest.json");
  let m=checked (Checkpoint.read path) in
  List.iter (fun name -> admit_metadata (Filename.concat path name)) ["segment.json";"state.json";"resolved.json";"closure.json"];
  let compatibility=Option.value compatibility ~default:m.compatibility in
  let m=checked (Checkpoint.validate ~compatibility path) in
  if get reference "identity"<>str m.identity || get reference "boundary"<>str (S.to_string m.boundary)
  then invalid_arg "selected checkpoint reference identity/boundary mismatch";
  let source=Filename.concat path "segment.json" in
  (* Read the inventoried bytes only after Checkpoint.validate has checked them;
     never decode backend-native metrics or guess a run directory. *)
  let inventory=match List.assoc_opt "segment.json" m.files with
    | Some record -> record | None -> invalid_arg "validated checkpoint has no segment inventory" in
  let ic=open_in_bin source in
  let bytes=Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let n=in_channel_length ic in
    if n>32*1024*1024 then invalid_arg "checkpoint segment byte bound exceeded";
    really_input_string ic n) in
  let segment=checked (decode_segment_bytes ~expected_sha256:inventory.sha256 bytes) in
  if get segment "metrics"<>m.metrics then invalid_arg "checkpoint manifest/segment metrics mismatch";
  if get segment "boundary"<>str (S.to_string m.boundary) then invalid_arg "checkpoint segment boundary mismatch";
  let metrics=fields m.metrics |> List.sort (fun (a,_) (b,_) -> String.compare a b) |> List.map (metric source) in
  let missing=List.filter (fun (k,_) -> not (List.mem_assoc k (fields m.metrics))) known
    |> List.map (fun (k,_) -> metric source (k,obj ["availability",str "unavailable";"reason",str "not_recorded"])) in
  let origin=obj ["scope",str "boundary_snapshot";"description",str "Accumulated metrics at this boundary; not per-step measurement provenance";
    "source",str source;"recorded",get segment "metric_origin";"recorded_target",get m.compatibility "target"] in
  let mapped_count=match List.find_opt (fun m -> get m "raw_key"=str "design__instance__count" && get m "availability"=str "available") metrics with
    | None -> None
    | Some m -> (match get m "value" with J.Integer s -> Some (Z.of_string s)
      | J.Float f -> Some (Z.of_float f) | _ -> None) in
  let supplementary,diagnostics=if not supplementary then [],[] else match get segment "run_dir" with
    | J.String run_dir when run_dir<>"" -> Yosys_stat_evidence.read ~run_dir ~mapped_count
    | _ -> [],[] in
  m,obj ["reference",reference;"validation",Checkpoint.inspection ~compatibility_source:`Recorded m;
    "metric_origin",origin;"metrics",J.array (metrics@missing);"supplementary",J.array supplementary],diagnostics
let validate_shared_files refs =
  List.iter (fun name ->
    let hashes=List.map (fun r -> (checked (Checkpoint.file_record (Filename.concat (text (get r "path")) name))).sha256) refs in
    match hashes with [] -> () | h::rest ->
      if not (List.for_all ((=) h) rest) then invalid_arg ("selected checkpoints have different "^name)) ["closure.json";"resolved.json"]
let validate_execution_snapshot = Stage_execution_record.validate_snapshot
let validate_execution path = protect (fun () ->
  let raw=json path in
  fst (checked (validate_execution_snapshot raw)))
let grouped s =
  let whole,fraction=match String.index_opt s '.' with
    | None -> s,"" | Some i -> String.sub s 0 i,String.sub s i (String.length s-i) in
  let b=Buffer.create 24 in
  String.iteri (fun i c -> if i>0 && (String.length whole-i) mod 3=0 then Buffer.add_char b ','; Buffer.add_char b c) whole;
  Buffer.contents b^fraction
let readable = function
  | J.Integer s -> grouped s
  | J.Float f -> grouped (Printf.sprintf "%.10f" f |> fun s ->
      let i=ref (String.length s-1) in while !i>=0 && s.[!i]='0' do decr i done;
      if !i>=0 && s.[!i]='.' then decr i; String.sub s 0 (!i+1))
  | v -> J.canonical v
let run ?(verbose=false) target = protect (fun () ->
  let snapshot=ref None in
  let kind,path,selected,execution,refs = match target with
    | Coarse_run _ -> invalid_arg "coarse target requires Report_service"
    | Checkpoint path ->
      let m=checked (Checkpoint.read path) in
      let r=Runner.of_checkpoint ~path m in
      "checkpoint",path,J.null,None,[Runner.reference_json r]
    | Stage_execution {path;selected_record} ->
      let document=Evidence_json.read selected_record in
      let bytes=Evidence_json.raw_bytes document in
      let raw=codec (J.parse bytes) in
      let r,manifests=checked (validate_execution_snapshot raw) in
      snapshot:=Some (selected_record,J.sha256_bytes bytes,raw,r,manifests);
      "stage_execution",path,str selected_record,Some r,r.references in
  let compatibility=ref None in
  let diagnostics=ref [] in
  let checkpoints=List.map (fun r -> let m,p,ds=checkpoint r !compatibility in
    diagnostics:= !diagnostics@ds;
    compatibility:=Some m.Checkpoint.compatibility;p) refs in
  validate_shared_files refs;
  let requested,physical=match execution with
    | None -> J.null,J.null
    | Some r ->
      let requested=str (S.to_string (Execution_plan.Stage_plan.endpoint r.plan)) in
      let completed=List.filter (fun o -> get o "status"=str "completed") r.occurrences in
      let last=match List.rev completed with [] -> J.null | o::_ ->
        let key=text (get o "action_key") in str (String.sub key 15 (String.length key-15)) in
      let physical=if last=str "signoff" then str "full" else last in
      requested,physical in
   let methodology = match execution with
     | Some {methodology=Some r;_} -> Some (Historical_methodology.recorded r)
      | _ -> None in
   let timing_intent=match !snapshot with None -> None | Some (execution,execution_sha256,raw,record,checkpoints) ->
     checked (Timing_intent_execution.inspect_snapshot ~execution ~execution_sha256 ~raw ~record ~checkpoints ()) in
   let envelope=obj (["contract",str "hardcaml-asic.report";"schema_version",codec (J.integer "1");
    "target",obj ["kind",str kind;"path",str path;"selected_record",selected];
    "execution",(match execution with None -> J.null | Some r -> r.raw);
    "completion",obj ["requested",requested;"physical",physical];
     "acceptance",str "not_evaluated";"external_postcheck",str "not_evaluated";
     "resource_realization",obj ["status",str "unavailable";"reason",str "checkpoint boundary metrics do not establish per-resource instance realization; use coarse/finalized resource evidence where retained"];
     "checkpoints",J.array checkpoints;"diagnostics",J.array !diagnostics] @
       (match methodology with None -> [] | Some m -> ["methodology",m]) @
       (match timing_intent with None -> [] | Some j -> ["timing_intent",codec (J.parse (Yojson.Safe.to_string j))]) @
      (match execution with None -> [] | Some r ->
        match Timing_analysis.load_file ~expected_record:r.raw (text selected) with None -> [] | Some analysis -> ["timing_analysis",analysis])) in
  let b=Buffer.create 2048 in
  let line fmt=Printf.ksprintf (fun s -> Buffer.add_string b (s^"\n")) fmt in
  let show = function J.Null -> "not recorded" | J.String s -> s | v -> J.canonical v in
  line "STAGE REPORT  %s" path;
  line "  requested: %s; physical completion: %s" (show requested) (show physical);
   line "  acceptance: not_evaluated; external postcheck: not_evaluated";
    Option.iter (fun m -> Buffer.add_string b (Historical_methodology.human ~verbose m)) methodology;
    Option.iter (fun j ->
      let status=match j with `Assoc xs -> (match List.assoc_opt "status" xs with Some (`String s) -> s | _ -> "unavailable") | _ -> "unavailable" in
      line "NAMED SYNTHESIS TIMING INTENT  %s" status;
      line "  Exact retained association; exhaustive coverage unavailable, timing closure not established.";
      line "  Path-bound evidence; named archive/relocation unsupported.") timing_intent;
  (match execution with None -> () | Some r ->
    line "EXECUTION  %s (%s, exit %s)" (text (get r.raw "attempt_id")) r.status (show (get r.raw "exit_code"));
    line "  %-28s %-14s %s" "Stage" "Status" "Duration (s)";
    List.iter (fun o -> let status=text (get o "status") in
      line "  %-28s %-14s %s" (text (get o "action_key")) status
        (if get o "exit_code"=J.null then "not recorded" else show (get o "duration_seconds"))) r.occurrences);
  List.iter (fun p ->
    let reference=get p "reference" in
    line "CHECKPOINT  %s  %s" (text (get reference "boundary")) (text (get reference "path"));
    line "  validation: host_only (runtime closure not validated)";
    line "  Accumulated boundary snapshot; metrics were not necessarily measured here.";
    let origin=get p "metric_origin" in
    let context=get origin "recorded_target" in
    line "  recorded target context: technology %s; corner %s (not measurement attribution)"
      (show (get context "technology")) (show (get context "corner"));
    line "  %-32s %-20s %s" "Metric" "Value" "Unit";
    (match get p "metrics" with J.Array ms -> List.iter (fun m ->
      let value=if get m "availability"=str "available" then
        (match numeric (get m "value") with
          | Some _ when get m "unit"<>J.null -> readable (get m "value")
          | _ -> show (get m "value"))
        else "unavailable: "^show (get m "reason") in
      line "  %-32s %-20s %s" (text (get m "label")) value
        (if get m "unit"=J.null then "unknown" else text (get m "unit"))) ms | _ -> ());
     (match get p "supplementary" with J.Array supplements -> List.iter (fun s ->
       let types=fields (get s "mapped_cell_types") in
       line "  YOSYS CELL TYPES  %d types; %s cells total" (List.length types) (readable (get s "mapped_cell_count"));
       let seq=get s "sequential_area" in
       if seq<>J.null then line "  Sequential cell area: %s %s" (readable (get seq "value")) (text (get seq "unit"));
       line "  evidence: %s" (text (get s "evidence_scope"));
       line "  source: %s" (text (get s "path"));
       line "  sha256: %s; tool: %s" (text (get s "sha256")) (text (get s "tool"));
       line "  %-52s %s" "Cell type" "Count";
       let ranked=List.sort (fun (a,x) (b,y) -> let c=Z.compare (Z.of_string (J.canonical y)) (Z.of_string (J.canonical x)) in
         if c=0 then String.compare a b else c) types in
       List.iteri (fun i (name,value) -> if verbose || i<10 then
         line "  %-52s %s" (if String.exists (fun c -> Char.code c<32 || c='"' || c='\\') name then J.canonical (str name) else name) (readable value)) ranked;
       if not verbose && List.length types>10 then line "  ... %d more types; use --verbose or --json for all types" (List.length types-10)) supplements | _ -> ())) checkpoints;
   List.iter (fun d -> line "DIAGNOSTIC  %s: %s" (text (get d "path")) (text (get d "message"))) !diagnostics;
   if checkpoints=[] then line "CHECKPOINTS  none published";
   (match get envelope "timing_analysis" with
    | J.Null -> ()
    | analysis ->
      line
        "POST-PNR STA ANALYSIS  %s; producer stage: %s; timing goal: %s"
        (show (get analysis "status"))
        (show (get analysis "stage"))
        (show (get analysis "timing_goal"));
      line
        "  Native STA runtime (s): %s; full segment duration is listed in EXECUTION"
        (show (get (get analysis "duration") "seconds"));
      line
        "  Exact source: %s; digest: %s"
        (show (get analysis "execution_file"))
        (show (get analysis "source_digest")));
   {envelope;human=Buffer.contents b})
