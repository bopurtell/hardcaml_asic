module J = Identity_json
module Stage = Execution_plan.Stage
type schema = Schema_1 | Schema_2
type status = Unreached | Running | Completed | Failed | Interrupted
type phase = Preparing | Executing | Finished
type error_code = Invalid_record | Reset_required
type error = { code : error_code; detail : string }
let error_code_name = function Invalid_record -> "INVALID_RECORD" | Reset_required -> "RESET_REQUIRED"
exception Invalid of error
let fail ?(code=Invalid_record) detail = raise (Invalid {code; detail})
let require b s = if not b then fail s
let protect f = try Ok (f ()) with
  | Invalid e -> Error e
  | Sys_error detail -> Error {code=Invalid_record; detail}
  | Unix.Unix_error (e, op, path) -> Error {code=Invalid_record; detail=op ^ ": " ^ path ^ ": " ^ Unix.error_message e}
let codec = function Ok v -> v | Error e -> fail (J.error_message e)
let checked = function Ok v -> v | Error e -> raise (Invalid e)
let str s = codec (J.string s)
let obj xs = codec (J.object_ xs)
let int n = codec (J.integer (string_of_int n))
let integer s = codec (J.integer s)
let optional f = function None -> J.null | Some x -> f x
let fields = function J.Object xs -> xs | _ -> fail "expected object"
let get j k = match List.assoc_opt k (fields j) with Some v -> v | None -> fail ("missing " ^ k)
let exact j names = ignore (codec (J.exact_object j ~fields:names))
let string = function J.String s -> s | _ -> fail "expected string"
let nullable_string = function J.Null -> None | J.String s -> Some s | _ -> fail "expected nullable string"
let integer_string = function J.Integer s -> s | _ -> fail "expected integer"
let path j = let s = string j in require (not (Filename.is_relative s)) "expected absolute path"; s
let nullable_path = function J.Null -> None | v -> Some (path v)
let hex n s = String.length s = n && String.for_all (function '0'..'9' | 'a'..'f' -> true | _ -> false) s
let nonnegative j =
  let f = match j with J.Integer s -> Z.to_float (Z.of_string s) | J.Float f -> f | _ -> fail "expected number" in
  require (Float.is_finite f && f >= 0.) "expected finite nonnegative number"; j
(* Python equality for the few lifecycle comparisons that permit numeric values.
   Identity hashes always use canonical bytes and never this equivalence. *)
let rec equal a b =
  match a, b with
  | J.Bool x, (J.Integer _ | J.Float _) -> equal (int (if x then 1 else 0)) b
  | (J.Integer _ | J.Float _), J.Bool y -> equal a (int (if y then 1 else 0))
  | J.Integer x, J.Float y | J.Float y, J.Integer x -> Float.floor y = y && Z.equal (Z.of_string x) (Z.of_float y)
  | J.Object xs, J.Object ys -> List.length xs = List.length ys && List.for_all (fun (k,v) -> match List.assoc_opt k ys with Some w -> equal v w | None -> false) xs
  | J.Array xs, J.Array ys -> List.length xs = List.length ys && List.for_all2 equal xs ys
  | _ -> a = b
let numeric_compare a b = match a,b with
  | J.Integer x, J.Integer y -> Z.compare (Z.of_string x) (Z.of_string y)
  | J.Float x, J.Float y -> Float.compare x y
  | J.Integer x, J.Float y -> Q.compare (Q.of_bigint (Z.of_string x)) (Q.of_float y)
  | J.Float x, J.Integer y -> Q.compare (Q.of_float x) (Q.of_bigint (Z.of_string y))
  | _ -> fail "expected number"
let status_name = function Unreached -> "unreached" | Running -> "running" | Completed -> "completed" | Failed -> "failed" | Interrupted -> "interrupted"
let status = function "unreached" -> Unreached | "running" -> Running | "completed" -> Completed | "failed" -> Failed | "interrupted" -> Interrupted | _ -> fail "invalid stage status"
let terminal = function Completed | Failed | Interrupted -> true | _ -> false
let contract = "hardcaml-asic.stage-observation"
let version = function Schema_1 -> 1 | Schema_2 -> 2
let error_json e = obj ["contract",str contract; "schema_version",int 1; "error",obj ["code",str (error_code_name e.code); "detail",str e.detail]]
type timing = { recorded_at : string; elapsed_seconds : J.t }
type occurrence = { occurrence_id : string; action_key : string; status : status; exit_code : string option; duration_seconds : J.t; error : string option; raw : J.t }
type reference = { boundary : Stage.t; path : string; identity : string; raw : J.t }
type outcome = { status : status; exit_code : string; error : string option }
type allocation = { request : J.t; workspace : string; checkpoint_store : string; lock_path : string; preflight_record : string }
type event = Allocated of allocation | Planned of Execution_plan.Stage_plan.t | Prepared of string option | Started of occurrence | Published of occurrence * reference | Finished_occurrence of occurrence * reference option | Outcome of outcome
type state = { attempt_id : string; allocation : allocation; phase : phase; plan : Execution_plan.Stage_plan.t option; occurrences : occurrence list; artifacts : reference list; runtime_record : string option; outcome : outcome option }
type record = { schema : schema; stream_id : string; sequence : int; previous_sha256 : string option; sha256 : string; timing : timing option; event : event; raw : J.t }
let occurrence raw =
  exact raw ["occurrence_id";"action_key";"status";"exit_code";"duration_seconds";"best_effort";"error";"verdict"];
  require (get raw "best_effort" = J.bool false && get raw "verdict" = J.null) "invalid stage identity/verdict";
  { occurrence_id=string (get raw "occurrence_id"); action_key=string (get raw "action_key"); status=status (string (get raw "status"));
    exit_code=(match get raw "exit_code" with J.Null -> None | v -> Some (integer_string v));
    duration_seconds=nonnegative (get raw "duration_seconds"); error=nullable_string (get raw "error"); raw }
let reference raw =
  exact raw ["identity";"path";"boundary";"restart_supported";"restart_targets";"closure_scope";"tested_image_id";"acceptance"];
  let name = string (get raw "boundary") in
  let boundary = match List.find_opt (fun s -> Stage.to_string s = name) Stage.all with Some s -> s | None -> fail "invalid boundary" in
  let identity=string (get raw "identity") in
  require (hex 64 identity) "invalid artifact identity";
  let path = path (get raw "path") in
  let reference = match Runner.reference ~boundary ~path ~identity with Ok r -> r | Error e -> fail e in
  require (Runner.reference_matches ~equal ~legacy_routing:false reference raw
    || (boundary=Stage.Routing && Runner.reference_matches ~equal ~legacy_routing:true reference raw))
    "invalid checkpoint capability";
  {boundary; identity; path; raw}
let decode_event ~name data = match name with
  | "allocated" ->
    exact data ["request";"workspace";"checkpoint_store";"lock_path";"preflight_record"];
    ignore (fields (get data "request"));
    Allocated {request=get data "request";workspace=path (get data "workspace");checkpoint_store=path (get data "checkpoint_store");lock_path=path (get data "lock_path");preflight_record=path (get data "preflight_record")}
  | "planned" -> exact data ["plan"];
    let p = match Execution_plan.Stage_plan.of_json (get data "plan") with Ok p -> p | Error e -> fail e in Planned p
  | "prepared" -> exact data ["runtime_record"]; Prepared (nullable_path (get data "runtime_record"))
  | "started" | "published" | "finished" ->
    exact data ["occurrence";"artifact"];
    let o = occurrence (get data "occurrence") in
    let a = match get data "artifact" with J.Null -> None | j -> Some (reference j) in
    (match name,a with
     | "started", None -> Started o
     | "published", Some a ->
       require (match get a.raw "restart_supported" with J.Bool _ -> true | _ -> false) "invalid artifact";
       Published (o,a)
     | "finished", a -> Finished_occurrence (o,a)
     | _ -> fail "invalid lifecycle artifact")
  | "outcome" -> exact data ["status";"exit_code";"error"];
    let status=status (string (get data "status")) in require (terminal status) "invalid outcome";
    Outcome {status;exit_code=integer_string (get data "exit_code");error=nullable_string (get data "error")}
  | _ -> fail "unknown event"
let event_of_json ~name data = protect (fun () -> decode_event ~name data)
let allocation_json a = ["request",a.request;"workspace",str a.workspace;"checkpoint_store",str a.checkpoint_store;"lock_path",str a.lock_path;"preflight_record",str a.preflight_record]
let outcome_json (o:outcome) = obj ["status",str (status_name o.status);"exit_code",integer o.exit_code;"error",optional str o.error]
let event_to_json = function
  | Allocated a -> "allocated",obj (allocation_json a)
  | Planned p -> "planned",obj ["plan",Execution_plan.Stage_plan.to_json p]
  | Prepared p -> "prepared",obj ["runtime_record",optional str p]
  | Started o -> "started",obj ["occurrence",o.raw;"artifact",J.null]
  | Published (o,a) -> "published",obj ["occurrence",o.raw;"artifact",a.raw]
  | Finished_occurrence (o,a) -> "finished",obj ["occurrence",o.raw;"artifact",optional (fun (r:reference)->r.raw) a]
  | Outcome o -> "outcome",outcome_json o
let initial_occurrence stream (a:Execution_plan.Action.t) =
  occurrence (obj ["occurrence_id",str (stream ^ "/" ^ a.key);"action_key",str a.key;"status",str "unreached";"exit_code",J.null;"duration_seconds",int 0;"best_effort",J.bool false;"error",J.null;"verdict",J.null])
let reduce old ~stream_id event =
  require (hex 32 stream_id) "invalid stream identity";
  match old,event with
  | None, Allocated allocation -> {attempt_id=stream_id;allocation;phase=Preparing;plan=None;occurrences=[];artifacts=[];runtime_record=None;outcome=None}
  | None, _ -> fail "missing allocation"
  | Some s, _ ->
    require (s.attempt_id=stream_id) "stream mismatch";
    require (s.outcome=None) "event after final outcome";
    match event with
    | Allocated _ -> fail "duplicate allocation"
    | Planned p -> require (s.plan=None) "duplicate plan";
      {s with plan=Some p;occurrences=List.map (initial_occurrence stream_id) (Execution_plan.Stage_plan.actions p)}
    | Prepared p -> require (s.plan<>None && s.phase=Preparing) "invalid preparation"; {s with phase=Executing;runtime_record=p}
    | Started o | Published (o,_) | Finished_occurrence (o,_) ->
      require (s.phase=Executing) "invalid lifecycle phase";
      let rec find previous = function
        | [] -> fail "unknown occurrence"
        | (old:occurrence)::rest when old.occurrence_id=o.occurrence_id -> List.rev previous,old,rest
        | old::rest -> find (old::previous) rest in
      let before,old,after=find [] s.occurrences in
      require (old.action_key=o.action_key) "invalid stage identity";
      let artifacts = match event with
        | Started _ -> require (old.status=Unreached && o.status=Running && o.exit_code=None && List.for_all (fun (x:occurrence)->x.status=Completed) before) "invalid start order"; s.artifacts
        | Published (_,a) ->
          require (old.status=Running && equal o.raw old.raw) "invalid publication";
          require (o.action_key="implementation." ^ Stage.to_string a.boundary && not (List.exists (fun (x:reference)->x.boundary=a.boundary) s.artifacts)) "invalid artifact";
          s.artifacts @ [a]
        | Finished_occurrence (_,a) ->
          require (old.status=Running && terminal o.status && o.exit_code<>None) "invalid finish";
          let published=List.find_opt (fun (x:reference)->o.action_key="implementation." ^ Stage.to_string x.boundary) s.artifacts in
          require ((o.status=Completed && o.exit_code=Some "0" && (match a,published with Some a,Some p -> equal a.raw p.raw | _ -> false)) ||
                   ((o.status=Failed || o.status=Interrupted) && o.exit_code<>Some "0" && a=None)) "unearned completion"; s.artifacts
        | _ -> assert false in
      {s with occurrences=before @ [o] @ after;artifacts}
    | Outcome o ->
      if o.status=Completed then require (s.phase=Executing && o.exit_code="0" && s.occurrences<>[] && List.for_all (fun (x:occurrence)->x.status=Completed) s.occurrences) "unearned outer success"
      else require (o.exit_code<>"0") "failure with zero exit";
      {s with phase=Finished;outcome=Some o}
let reduce_event s ~stream_id e = protect (fun () -> reduce s ~stream_id e)
let state_json s = obj (allocation_json s.allocation @
  ["attempt_id",str s.attempt_id;"phase",str (match s.phase with Preparing->"preparing"|Executing->"executing"|Finished->"finished");
   "plan",optional Execution_plan.Stage_plan.to_json s.plan;"occurrences",J.array (List.map (fun (o:occurrence)->o.raw) s.occurrences);
   "artifacts",J.array (List.map (fun (a:reference)->a.raw) s.artifacts);"runtime_record",optional str s.runtime_record;"outcome",optional outcome_json s.outcome])
let validate_stamp stamp =
  require (String.length stamp=27) "recorded_at must be canonical UTC with six fractional digits and Z";
  String.iteri (fun i c -> require (match i with 4|7 -> c='-' | 10->c='T' |13|16->c=':'|19->c='.'|26->c='Z'|_->c>='0' && c<='9') "invalid recorded_at format") stamp;
  let n i l = int_of_string (String.sub stamp i l) in
  let year=n 0 4 and month=n 5 2 and day=n 8 2 in
  let leap=year mod 4=0 && (year mod 100<>0 || year mod 400=0) in
  let days=match month with 2->if leap then 29 else 28|4|6|9|11->30|1|3|5|7|8|10|12->31|_->0 in
  require (year>=1 && day>=1 && day<=days && n 11 2<24 && n 14 2<60 && n 17 2<60) "invalid recorded_at calendar time"
type prefix = { reversed : record list; count : int; reduced : state option }
let empty = {reversed=[];count=0;reduced=None}
let records p = List.rev p.reversed
let state p = p.reduced
let cursor p = p.count
let append_record p raw = protect (fun () ->
  let schema=match get raw "schema_version" with J.Integer "1"->Schema_1|J.Integer "2"->Schema_2|_->fail "unsupported record schema" in
  let last=match p.reversed with []->None|r::_->Some r in
  require (match last with None->true|Some r->r.schema=schema) "mixed record schema";
  exact raw (["contract";"schema_version";"stream_id";"sequence";"previous_sha256";"event";"data";"sha256"] @ if schema=Schema_2 then ["recorded_at";"elapsed_seconds"] else []);
  require (get raw "contract"=str contract && get raw "sequence"=int (p.count+1)) "unsupported record sequence/contract";
  let stream_id=string (get raw "stream_id") in require (hex 32 stream_id) "invalid stream identity";
  let previous_sha256=nullable_string (get raw "previous_sha256") and sha256=string (get raw "sha256") in
  require ((match last with None->previous_sha256=None|Some r->stream_id=r.stream_id && previous_sha256=Some r.sha256) && sha256=J.identity (codec (J.unsigned raw ~field:"sha256"))) "record chain mismatch";
  let timing=if schema=Schema_1 then None else (
    let recorded_at=string (get raw "recorded_at") and elapsed_seconds=nonnegative (get raw "elapsed_seconds") in validate_stamp recorded_at;
    require (match last with Some {timing=Some t;_}->numeric_compare elapsed_seconds t.elapsed_seconds>=0|_->true) "elapsed_seconds regressed";
    Some {recorded_at;elapsed_seconds}) in
  let event=decode_event ~name:(string (get raw "event")) (get raw "data") in
  let reduced=reduce p.reduced ~stream_id event in
  {reversed={schema;stream_id;sequence=p.count+1;previous_sha256;sha256;timing;event;raw}::p.reversed;count=p.count+1;reduced=Some reduced})
let nonempty p = match p.reduced,p.reversed with Some s,r::_->s,r.schema|_->fail "missing allocation record"
let final_results s =
  let physical=List.exists (fun (a:reference)->a.boundary=Stage.Signoff) s.artifacts && List.exists (fun (o:occurrence)->o.action_key="implementation.signoff" && o.status=Completed) s.occurrences in
  obj ["physical_completion",str (if physical then "completed" else "not_recorded");"checkpoint_refs",J.array (List.map (fun (a:reference)->a.raw) s.artifacts);"acceptance",str "not_evaluated";"external_postcheck",str "not_evaluated"]
let snapshot p ~writer_active = protect (fun () ->
  let s,schema=nonempty p in obj ["contract",str contract;"schema_version",int (version schema);"stream_id",str s.attempt_id;"cursor",int p.count;"state",state_json s;"writer_active",optional J.bool writer_active;
    "observation_status",str (if s.outcome<>None then "final" else if writer_active=Some true then "live" else "writer_lost_or_unknown");"final_results",final_results s])
let updates p ~stream_id ~after = protect (fun () ->
  let s,schema=nonempty p in
  if stream_id<>s.attempt_id || after<0 || after>p.count then fail ~code:Reset_required "wrong stream or cursor outside retained prefix";
  obj ["contract",str contract;"schema_version",int (version schema);"stream_id",str stream_id;"after",int after;"cursor",int p.count;"events",J.array (List.filter_map (fun r->if r.sequence>after then Some r.raw else None) (records p))])
external active_probe : string -> int = "hardcaml_asic_observation_active"
external metadata : string -> string = "hardcaml_asic_observation_metadata"
let writer_active path = if String.contains path '\000' then None else match active_probe path with 0->Some false|1->Some true|_->None
module Reader = struct
  type t = {directory:string; mutable validated:prefix; mutable metadata:string list; mutable after:int; mutable failed:error option}
  let prefix r = r.validated
  let names directory = Sys.readdir directory |> Array.to_list |> List.filter (fun n -> String.length n>0 && n.[0]>='0' && n.[0]<='9' && Filename.check_suffix n ".json") |> List.sort String.compare
  let refresh r =
    (match r.failed with None->()|Some e->raise (Invalid e));
    let names=names r.directory in
    require (names<>[]) "missing allocation record";
    require (List.length names>=r.validated.count) "retained prefix deleted";
    let cached=Array.of_list r.metadata in
    let new_metadata=List.mapi (fun i name ->
      require (name=Printf.sprintf "%012d.json" (i+1)) "journal gap or malformed record name";
      let path=Filename.concat r.directory name in
      let before=metadata path in
      if i<Array.length cached then require (before=cached.(i)) "retained prefix changed or replaced"
      else (
        let raw=codec (Result_store.read_json path) in
        let next=checked (append_record r.validated raw) in
        require (metadata path=before) "record changed during read";
        r.validated<-next);
      before) names in
    r.metadata<-new_metadata
  let create ?stream_id ?after directory = protect (fun () ->
    if (stream_id=None)<>(after=None) then fail ~code:Reset_required "stream-id and after are required together";
    let directory=Unix.realpath directory in
    let r={directory;validated=empty;metadata=[];after=0;failed=None} in refresh r;
    let s,_=nonempty r.validated in
    let after=Option.value after ~default:0 in
    ignore (checked (updates r.validated ~stream_id:(Option.value stream_id ~default:s.attempt_id) ~after));
    r.after<-after;r)
  let poll r =
    let result=protect (fun () ->
      refresh r;
      let s,_=nonempty r.validated in
      let active=writer_active s.allocation.lock_path in
      if active<>Some true then refresh r;
      let s,_=nonempty r.validated in
      let u=checked (updates r.validated ~stream_id:s.attempt_id ~after:r.after) in
      let snap=checked (snapshot r.validated ~writer_active:active) in
      let status=obj (List.map (fun k->k,get snap k) ["contract";"schema_version";"stream_id";"cursor";"writer_active";"observation_status";"final_results"] @ ["outcome",optional outcome_json s.outcome]) in
      r.after<-r.validated.count;u,status) in
    (match result with Error e->r.failed<-Some e|Ok _->());result
end
let load directory = protect (fun () -> (checked (Reader.create directory)).validated)
let inspect_with_preflight_read_hook ~after_preflight_read directory = protect (fun () ->
  let p=checked (load directory) in
  let s,_=nonempty p in
  let snap=checked (snapshot p ~writer_active:(writer_active s.allocation.lock_path)) in
  let launch = match s.allocation.request with J.Object xs -> List.assoc_opt "methodology_launch" xs | _ -> None in
   let delivery_fields,snap = match launch with
     | None -> [],snap
    | Some launch ->
      let rec evidence depth report =
        require (depth<16) "nested preflight evidence limit";
        match report with
        | J.Object xs -> (match List.assoc_opt "methodology" xs with
          | Some r -> Some (r,Option.value (List.assoc_opt "request" xs) ~default:s.allocation.request)
          | None ->
            require (match List.assoc_opt "request" xs with
              | Some (J.Object request) -> not (List.mem_assoc "methodology" request)
              | _ -> true) "missing required named preflight reference";
            (match List.assoc_opt "previous_report" xs with
             | Some old -> evidence (depth+1) old
             | None ->
               require (List.assoc_opt "ready" xs=Some (J.bool false)
                 && (match List.assoc_opt "diagnostic" xs with
                   | Some (J.Object diagnostic) -> List.assoc_opt "status" diagnostic=Some (str "pending")
                   | _ -> false)) "missing required named preflight reference";
               None))
        | _ -> fail "invalid named preflight evidence" in
       let read_report () = if Sys.file_exists s.allocation.preflight_record then
           evidence 0 (codec (Result_store.read_json s.allocation.preflight_record)) else None in
       let report = read_report () in
       after_preflight_read ();
       (* The reference-bearing report commits metadata, not the earlier sidecar
          rename. Drain again across writer release and re-read the marker: an old
          pending read must not hide a newly committed missing/corrupt snapshot. *)
       let active = writer_active s.allocation.lock_path in
       let p = if report=None then checked (load directory) else p in
       let s,_ = nonempty p in
       let report = if report=None then read_report () else report in
      let methodology,request,execution_file = match report with
       | None ->
          require (s.artifacts=[] && s.runtime_record=None && s.phase<>Executing
            && (match s.outcome with Some o -> o.status<>Completed | None -> true)
            && (Sys.file_exists s.allocation.preflight_record
                || not (Sys.file_exists (string (get launch "snapshot_path")))))
           "missing required named preflight reference";
          Historical_methodology.pending launch,s.allocation.request,J.null
       | Some (raw,request) ->
         let reference = match (match s.plan with
           | None -> Methodology_execution_record.of_reference_json ~attempt_id:s.attempt_id raw
           | Some plan -> Methodology_execution_record.of_json ~attempt_id:s.attempt_id ~plan raw) with
           | Ok r -> r | Error e -> fail e in
         require (get raw "snapshot_path"=get launch "snapshot_path"
           && get raw "expected_plan_identity"=get launch "expected_plan_identity") "named allocation/reference mismatch";
         (match Stage_execution_record.check_methodology_checkpoints reference (List.map (fun (r:reference) -> r.raw) s.artifacts) with
          | Ok () -> () | Error e -> fail e);
         let execution_file = match Stage_execution_record.available ~workspace:s.allocation.workspace
           ~attempt_id:s.attempt_id ~methodology:reference with
           | Ok p -> optional str p | Error e -> fail e in
         (match s.outcome with Some o when o.status=Completed ->
           require (execution_file<>J.null) "completed named execution record missing"
          | _ -> ());
          Historical_methodology.recorded reference,request,execution_file in
       ["methodology",methodology;"execution_request",request;"execution_file",execution_file],
       checked (snapshot p ~writer_active:active) in
   match snap with J.Object xs -> obj (xs @ delivery_fields) | _ -> assert false)
let inspect directory = inspect_with_preflight_read_hook ~after_preflight_read:(fun () -> ()) directory
module For_testing = struct
  let inspect = inspect_with_preflight_read_hook
end
let read_updates directory ~stream_id ~after = protect (fun () -> checked (updates (checked (load directory)) ~stream_id ~after))
let describe () = codec (J.parse {|{
 "contract":"hardcaml-asic.stage-observation","schema_version":2,"supported_schema_versions":[1,2],
 "response_schema_policy":"inspect_read_updates_watch_match_stream; CLI_allocation_terminal_error_envelopes_remain_1",
 "timing":{"recorded_at":"UTC_YYYY-MM-DDTHH:MM:SS.ffffffZ_before_atomic_promotion","elapsed_seconds":"finite_nonnegative_monotonic_seconds_since_attempt_creation","ordering":"sequence_authoritative; wall_clock_may_regress","legacy":"schema_1_has_no_timing; never_synthesized","stream":"single_schema_version; timing_inside_record_hash"},
 "scope":"one_path_bound_stage_attempt","cursor":"stream_id_and_sequence",
 "events":["allocated","planned","prepared","started","published","finished","outcome"],
 "capabilities":{"inspect":true,"read_updates":true,"watch":true,"incremental_reader":true,"reader_mutations":false,"producer_event_timestamps":true,"monotonic_elapsed_seconds":true,"stage_lifecycle":true,"backend_step_lifecycle":false,"runtime_observation":false,"execution_resume":false,"request_idempotency":false,"store_wide_journal":false,"fsync_durability":false},
 "writer_policy":"nonblocking_advisory_workspace_flock; conflict=BUSY; no_takeover",
 "retention":"caller_retains_store_workspace_checkpoints_and_external_roots",
  "lost_writer":"last_recorded_facts_preserved; outer_outcome_unknown",
  "named_execution_delivery":{"execution_file":"named_inspect_and_terminal_only; absolute_validated_readable_record_or_null; empty_reservation_is_null", "validation":"strict_stage_execution_record; exact_attempt_recipe_snapshot_plan_bundle; no_runtime_or_catalog", "stored_record":"unchanged; delivery_field_is_not_persisted"},
 "watch":{"mode":"foreground_in_process_read_only","interval_seconds":0.5,"interval_rule":"positive_finite","reconnect":"stream_id_and_after_required_together","initial_replay":"validate_complete_prefix; deliver_events_after_cursor","idle":"metadata_checks_and_liveness; no_record_reparse; suppress_duplicate_status","release":"drain_committed_tail_after_inactive_or_unknown_lock","prefix_integrity":"dev_inode_size_mtime_ns_ctime_ns; trusted_local_immutable_convention","exit_codes":{"recorded_terminal":0,"writer_lost_or_unknown":3,"query_error":2,"reader_sigint":130},"delivery":"flushed_JSONL; updates_only_when_nonempty; changed_status_and_final_status"},
 "schemas":{
 "legacy_record":{"contract":"hardcaml-asic.stage-observation","schema_version":1,"stream_id":"32_lowercase_hex","sequence":"positive_integer","previous_sha256":"sha256_or_null_at_1","event":"declared_event_enum","data":"event_specific_object","sha256":"canonical_unsigned_record_sha256"},
 "record":{"contract":"hardcaml-asic.stage-observation","schema_version":2,"stream_id":"32_lowercase_hex","sequence":"positive_integer","previous_sha256":"sha256_or_null_at_1","recorded_at":"canonical_UTC_with_six_fractional_digits_and_Z","elapsed_seconds":"finite_nonnegative_number_nondecreasing","event":"declared_event_enum","data":"event_specific_object","sha256":"canonical_unsigned_record_sha256"},
 "event_data":{"allocated":{"request":"object","workspace":"absolute_path","checkpoint_store":"absolute_path","lock_path":"absolute_path","preflight_record":"absolute_path"},"planned":{"plan":"StagePlan_schema_2_or_3"},"prepared":{"runtime_record":"absolute_path_or_null"},"started|published|finished":{"occurrence":"occurrence","artifact":"checkpoint_reference_or_null"},"outcome":{"status":"completed|failed|interrupted","exit_code":"integer","error":"string_or_null"}},
 "occurrence":{"occurrence_id":"stream_id/action_key","action_key":"implementation.boundary","status":"unreached|running|completed|failed|interrupted","exit_code":"integer_or_null","duration_seconds":"nonnegative_number","best_effort":false,"error":"string_or_null","verdict":null},
 "checkpoint_reference":{"path":"absolute_path","identity":"manifest_sha256","boundary":"stage_name","restart_supported":"boolean","restart_targets":"stage_name_array","closure_scope":"path_bound","tested_image_id":"image_id","acceptance":"not_evaluated"},
   "inspect":{"contract":"hardcaml-asic.stage-observation","schema_version":2,"stream_id":"32_lowercase_hex","cursor":"prefix_length","state":"reduced_state","writer_active":"boolean_or_null","observation_status":"final|live|writer_lost_or_unknown","final_results":"final_results","methodology":"named_only; hardcaml-asic.historical-methodology_v1_recorded|pending; absent_means_not_recorded","execution_request":"named_only; retained_native_request_or_original_allocation_request","execution_file":"named_only; absolute_validated_readable_record_or_null"},
 "reduced_state":{"attempt_id":"stream_id","request":"object","workspace":"absolute_path","checkpoint_store":"absolute_path","lock_path":"absolute_path","preflight_record":"absolute_path","phase":"preparing|executing|finished","plan":"StagePlan_or_null","occurrences":"occurrence_array","artifacts":"checkpoint_reference_array","runtime_record":"absolute_path_or_null","outcome":"outcome_data_or_null"},
 "final_results":{"physical_completion":"completed|not_recorded","checkpoint_refs":"checkpoint_reference_array","acceptance":"not_evaluated","external_postcheck":"not_evaluated"},
 "read_updates":{"contract":"hardcaml-asic.stage-observation","schema_version":2,"stream_id":"32_lowercase_hex","after":"requested_cursor","cursor":"prefix_length","events":"record_array"},
 "watch_updates":{"event":"updates","contract":"hardcaml-asic.stage-observation","schema_version":2,"stream_id":"32_lowercase_hex","after":"requested_or_last_delivered_cursor","cursor":"validated_prefix_length","events":"complete_record_array_after_cursor"},
 "watch_status":{"event":"status","contract":"hardcaml-asic.stage-observation","schema_version":2,"stream_id":"32_lowercase_hex","cursor":"validated_prefix_length","writer_active":"boolean_or_null","observation_status":"final|live|writer_lost_or_unknown","outcome":"outcome_data_or_null","final_results":"final_results"},
 "error":{"contract":"hardcaml-asic.stage-observation","schema_version":1,"error":{"code":"INVALID_RECORD|RESET_REQUIRED|BUSY|EXECUTION_FAILED","detail":"string"}}
 }}|})
