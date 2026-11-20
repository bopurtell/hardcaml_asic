module Stage = Execution_plan.Stage
module Action = Execution_plan.Action
module J = Identity_json
type status = Unreached | Skipped | Running | Completed | Failed | Interrupted | Accepted | Rejected
let status_name = function Unreached -> "unreached" | Skipped -> "skipped" | Running -> "running"
  | Completed -> "completed" | Failed -> "failed" | Interrupted -> "interrupted"
  | Accepted -> "accepted" | Rejected -> "rejected"
type error_kind = Failure | Cancellation of int
type error = { kind : error_kind; exit_code : int; message : string; secondary : string list }
let failure ?(exit_code=2) ?(secondary=[]) message =
  if exit_code <= 0 then invalid_arg "failure requires positive exit code";
  {kind=Failure; exit_code; message; secondary}
let cancellation ?(secondary=[]) ~signal message =
  if signal <= 0 || signal > 127 then invalid_arg "expected positive POSIX signal";
  {kind=Cancellation signal; exit_code=128+signal; message; secondary}
exception Callback_error of error
type completion = { status : status; exit_code : int; error : error option }
type occurrence = { occurrence_id : string; action_key : string; status : status;
  exit_code : int option; duration_seconds : float; best_effort : bool; error : string option; verdict : status option }
type step = { operation : Action.operation; status : status; exit_code : int;
  duration_seconds : float; best_effort : bool; error : string option }
type reference = { boundary : Stage.t; path : string; identity : string; restart_targets : Stage.t list }
let reference ~boundary ~path ~identity =
  match Checkpoint.absolute_path path with
  | Error e -> Error e
  | Ok path ->
    if String.length identity <> 64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) identity)
    then Error "invalid publication identity"
    else match J.string path with Error e -> Error (J.error_message e) | Ok _ ->
      Ok {boundary;path;identity;restart_targets=(Stage.capability boundary).restart_targets}
let of_checkpoint ~path (c : Checkpoint.t) =
  {boundary=c.boundary;path;identity=c.identity;restart_targets=c.restart_targets}
let of_checkpoint_publication (p : Checkpoint_publisher.publication) =
  of_checkpoint ~path:p.path p.checkpoint
let get = function Ok x -> x | Error e -> invalid_arg (J.error_message e)
let str x = get (J.string x)
let obj x = get (J.object_ x)
let int x = get (J.integer (string_of_int x))
let float x = get (J.float x)
let opt f = function None -> J.null | Some x -> f x
let reference_json (p : reference) =
  let cap = Stage.capability p.boundary in
  obj ["path",str p.path; "identity",str p.identity; "boundary",str (Stage.to_string p.boundary);
       "restart_supported",J.bool (p.restart_targets <> []);
       "restart_targets",J.array (List.map (fun s -> str (Stage.to_string s)) p.restart_targets);
       "acceptance",str "not_evaluated"; "closure_scope",str "path_bound";
       "tested_image_id",str cap.tested_image_id]
(* A reference carries no profile field; whitelist published profile-specific
   restrictions as pairs, never as two independently optional fields. *)
let reference_matches ?(equal=(=)) ~legacy_routing (p:reference) raw =
  let choices = match p.boundary with
    | Stage.Routing when legacy_routing -> [[Stage.Signoff]]
    | Stage.Synthesis -> [p.restart_targets; [Stage.Floorplan]; []]
    | Stage.Timing -> [p.restart_targets; []]
    | _ -> [p.restart_targets] in
  List.exists (fun restart_targets -> equal raw (reference_json {p with restart_targets})) choices
type 'checkpoint publication = { evidence : reference; checkpoint : 'checkpoint }
type 'checkpoint source = { boundary : Stage.t; checkpoint : 'checkpoint }
type ('run, 'segment) outcome = Run_result of 'run * completion | Segment_result of 'segment * completion
  | Service_result of completion | Archive_result of string * completion
type ('run, 'checkpoint) context = { occurrence_id : string; best_effort : bool;
  run : 'run option; source : 'checkpoint source option }
type lifecycle = Started | Published | Finished
type point = Validate | Cancel | Clock | Invoke | Publish | Hook of lifecycle | Observe | Persist
type diagnostic = { action_key : string option; point : point; error : error }
type ('run, 'checkpoint) t = { attempt_id : string; plan : Execution_plan.t; occurrences : occurrence list;
  timing_intent : J.t option;
  status : status; exit_code : int; run : 'run option; steps : step list; archive : string option;
  artifacts : 'checkpoint publication list; primary : diagnostic option; secondary : diagnostic list }
type ('run, 'segment, 'checkpoint) callbacks = {
  validate : Execution_plan.t -> (unit,error) result;
  invoke : Action.t -> ('run,'checkpoint) context -> (('run,'segment) outcome,error) result;
  publish : Action.t -> ('run,'checkpoint) context -> 'segment -> ('checkpoint publication,error) result }
let occurrence_json (o : occurrence) = obj ["occurrence_id",str o.occurrence_id; "action_key",str o.action_key;
  "status",str (status_name o.status); "exit_code",opt int o.exit_code;
  "duration_seconds",(if o.exit_code=None then int 0 else float o.duration_seconds);
  "best_effort",J.bool o.best_effort; "error",opt str o.error; "verdict",opt (fun s -> str (status_name s)) o.verdict]
let to_json ?methodology ~run_json t = obj ((match methodology with None -> [] | Some r ->
  let raw = Methodology_execution_record.to_json r in
  (match t.plan with Coarse _ -> invalid_arg "methodology reference requires stage execution"
   | Stages plan -> (match Methodology_execution_record.of_json ~attempt_id:t.attempt_id ~plan raw with
     | Ok _ -> () | Error e -> invalid_arg e));
   ["methodology",raw]) @ (match t.timing_intent with None -> [] | Some x -> ["timing_intent",x]) @ ["attempt_id",str t.attempt_id; "plan",Execution_plan.to_json t.plan;
  "occurrences",J.array (List.map occurrence_json t.occurrences); "status",str (status_name t.status); "exit_code",int t.exit_code]
  @ (match t.run with None -> [] | Some r -> ["run",run_json r])
  @ (match t.archive with None -> [] | Some p -> ["archive",str p])
  @ (if t.artifacts=[] then [] else ["artifacts",J.array (List.map (fun p -> reference_json p.evidence) t.artifacts)]))
let operation_name = function Action.Run -> "run" | Postcheck -> "postcheck" | Collect -> "collect"
  | Report -> "report" | Archive -> "archive" | Segment -> "segment"
let steps_json t = J.array (List.map (fun (s : step) -> obj (["operation",str (operation_name s.operation);
  "status",str (status_name s.status); "exit_code",int s.exit_code; "duration_seconds",float s.duration_seconds]
  @ (if s.best_effort then ["best_effort",J.bool true] else []) @ (match s.error with None -> [] | Some e -> ["error",str e]))) t.steps)
let point_name = function Validate -> "validate" | Cancel -> "cancel" | Clock -> "clock" | Invoke -> "invoke"
  | Publish -> "publish" | Hook Started -> "started" | Hook Published -> "published" | Hook Finished -> "finished"
  | Observe -> "observe" | Persist -> "persist"
let diagnostics_json t =
  let diagnostic d = obj ["action_key",opt str d.action_key; "point",str (point_name d.point);
    "kind",str (match d.error.kind with Failure -> "failed" | Cancellation _ -> "interrupted");
    "signal",(match d.error.kind with Failure -> J.null | Cancellation s -> int s);
    "exit_code",int d.error.exit_code; "error",str d.error.message;
    "secondary",J.array (List.map str d.error.secondary)] in
  obj ["schema_version",int 1; "kind",str "runner_diagnostics"; "attempt_id",str t.attempt_id;
       "primary",opt diagnostic t.primary; "secondary",J.array (List.map diagnostic t.secondary)]
let protect f = try f () with Callback_error e -> Error e | exn -> Error (failure (Printexc.to_string exn))
let error_status e = match e.kind with Failure -> Failed | Cancellation _ -> Interrupted
(* Python round(binary64, 6): round the exact binary rational to a decimal
   microsecond, ties to even, then convert that decimal back to binary64. *)
let round_seconds seconds =
  if seconds=0. then seconds else
  let bits = Int64.bits_of_float seconds in
  let exponent = Int64.(to_int (logand (shift_right_logical bits 52) 0x7ffL)) in
  let mantissa = Z.of_int64 Int64.(logand bits 0x000fffffffffffffL) in
  let mantissa, shift = if exponent=0 then mantissa,-1074
    else Z.add mantissa (Z.shift_left Z.one 52),exponent-1075 in
  let scaled = Z.mul mantissa (Z.of_int 1000000) in
  let rounded = if shift>=0 then Z.shift_left scaled shift else
    let denominator=Z.shift_left Z.one (-shift) in
    let q,r=Z.div_rem scaled denominator in
    let comparison=Z.compare (Z.mul r (Z.of_int 2)) denominator in
    if comparison>0 || (comparison=0 && Z.testbit q 0) then Z.succ q else q in
  float_of_string (Z.to_string rounded ^ "e-6")
external monotonic_now : unit -> float = "hardcaml_asic_monotonic"
let execute ?initial ?timing_intent ?(clock=monotonic_now) ?(cancel=fun () -> None)
    ?(lifecycle=fun _ _ _ _ -> ()) ?(observe=fun _ _ -> ()) ?(persist=fun _ -> ()) ~attempt_id plan callbacks =
  if attempt_id="" || String.contains attempt_id '/' || String.contains attempt_id '\000' then invalid_arg "invalid attempt ID";
  ignore (str attempt_id);
  let actions = Execution_plan.actions plan in
  let occurrences = Array.of_list (List.map (fun (a : Action.t) -> {occurrence_id=attempt_id^"/"^a.key;
    action_key=a.key;status=(if a.enabled then Unreached else Skipped);exit_code=None;
    duration_seconds=0.;best_effort=false;error=None;verdict=None}) actions) in
   let state = ref {attempt_id;plan;timing_intent;occurrences=Array.to_list occurrences;status=Running;exit_code=0;
    run=None;steps=[];archive=None;artifacts=[];primary=None;secondary=[]} in
  let source = ref initial and cancelled = ref false in
  let last_clock = ref None in
  let read_clock () =
    let t=clock () in
    if not (Float.is_finite t) || (match !last_clock with Some previous -> t<previous | None -> false)
    then Error (failure "invalid nondecreasing finite clock")
    else (last_clock:=Some t; Ok t) in
  let snapshot () = {!state with occurrences=Array.to_list occurrences} in
  let record key best_effort point error =
    let d = {action_key=key;point;error} in
    (* An interrupted service with an allocated run still permits Python's
       diagnostic collection. An explicit cancellation poll vetoes all work. *)
    (match error.kind,point with Cancellation _,Cancel -> cancelled := true | _ -> ());
    if (!state).primary=None && not best_effort then
      state := {!state with primary=Some d;status=error_status error;exit_code=error.exit_code}
    else state := {!state with secondary=(!state).secondary@[d]} in
  let save key best_effort = match protect (fun () -> persist (snapshot ()); Ok ()) with
    | Ok () -> None | Error e -> record key best_effort Persist e; Some e in
  let check_cancel () = protect (fun () -> match cancel () with None -> Ok () | Some e -> Error e) in
  let initial_saved=match timing_intent with None -> true | Some _ -> save None false=None in
  let validation = if not initial_saved then Ok () else protect (fun () ->
    let expected = match plan with Execution_plan.Coarse _ -> None
      | Stages p -> Execution_plan.Stage_plan.source_boundary p in
    if expected <> Option.map (fun (s : _ source) -> s.boundary) initial then Error (failure "plan initial boundary does not match supplied handoff")
    else callbacks.validate plan) in
  (match validation with Error e -> record None false Validate e | Ok () -> ());
  (match timing_intent with None -> ignore (save None false) | Some _ ->
    if initial_saved && (!state).primary<>None then ignore (save None false));
  List.iteri (fun i (action : Action.t) ->
    let best_effort = (!state).primary<>None && action.gate=Action.Run_available && (!state).run<>None in
    if action.enabled && not !cancelled && ((!state).primary=None || best_effort) then begin
      let local = ref None and local_status = ref Failed and local_message = ref None in
      let set_local e = if !local=None then begin
        local:=Some e; local_status:=error_status e; local_message:=Some e.message
      end in
      let fail point e = set_local e; record (Some action.key) best_effort point e in
      let call point f = match protect f with Ok x -> Some x | Error e -> fail point e; None in
      let poll () = ignore (call Cancel check_cancel) in
      poll ();
      if !local=None then begin
        let started = call Clock read_clock in
        occurrences.(i) <- {occurrences.(i) with status=Running;best_effort};
        let context = {occurrence_id=occurrences.(i).occurrence_id;best_effort;run=(!state).run;source= !source} in
        let hook event artifact = ignore (call (Hook event) (fun () -> lifecycle event action occurrences.(i) artifact; Ok ())) in
        hook Started None;
        (match save (Some action.key) best_effort with None -> () | Some e -> set_local e);
        let outcome = if !local=None then call Invoke (fun () -> callbacks.invoke action context) else None in
        let completion = ref {status=Completed;exit_code=0;error=None} and publication = ref None in
        (match outcome with None -> () | Some result ->
          let valid, c = match action.operation,result with
            | Action.Run,Run_result (r,c) -> state := {!state with run=Some r}; true,c
            | Segment,Segment_result (_,c) | Postcheck,Service_result c | Collect,Service_result c | Report,Service_result c -> true,c
            | Archive,Archive_result (_,c) -> true,c
            | _,(Run_result (_,c)|Segment_result (_,c)|Service_result c|Archive_result (_,c)) -> false,c in
          completion:=c;
          let terminal = match action.operation,c.status,c.exit_code with
            | Action.Report,Accepted,0 -> true | Report,Rejected,n when n>0 -> true
            | (Run|Postcheck|Segment|Collect|Archive),Completed,0 -> true
            | (Run|Postcheck|Segment|Collect|Archive),Failed,n when n>0 -> true
            | (Run|Postcheck|Segment|Collect|Archive),Interrupted,n when n>128 && n<=255 -> true
            | _ -> false in
          let valid_error = match c.error with None -> true | Some e ->
            e.exit_code=c.exit_code && e.exit_code>0 &&
            (match c.status,e.kind with Interrupted,Cancellation s -> s>0 && s<=127 && e.exit_code=128+s
             | (Failed|Rejected),Failure -> true | _ -> false) in
          if not valid || not terminal || not valid_error then fail Invoke (failure "outcome does not match action/completion contract")
          else if c.exit_code<>0 then begin
            let first = (!state).primary=None && not best_effort in
            let first_local = !local=None in
            let error = match c.error with Some e -> e | None ->
              {kind=(if c.status=Interrupted then Cancellation (c.exit_code-128) else Failure);
               exit_code=c.exit_code;message="";secondary=[]} in
            fail Invoke error;
            if first_local then (local_status:=c.status;local_message:=Option.map (fun e -> e.message) c.error);
            if first then state := {!state with status=c.status}
          end;
          poll ();
          (match result with Archive_result (p,_) when !local=None -> state := {!state with archive=Some p} | _ -> ());
          (match result with Segment_result (segment,_) when !local=None ->
            (match call Publish (fun () -> callbacks.publish action context segment) with
             | None -> ()
             | Some p ->
               let boundary = match action.stage with Action.Physical s -> s | _ -> assert false in
               if p.evidence.boundary<>boundary then fail Publish (failure "publication boundary does not match action")
               else begin
                 publication:=Some p;
                  state := {!state with artifacts=(!state).artifacts@[p]};
                  source:=Some {boundary;checkpoint=p.checkpoint};
                  (* Required named analysis may block/fail in Published. Retain
                     its promoted artifact durably before entering that hook.
                     Historical hook/persistence ordering remains unchanged. *)
                  (match timing_intent with None -> () | Some _ ->
                    match save (Some action.key) best_effort with None -> () | Some e -> set_local e);
                  if !local=None then hook Published (Some p);
                 (match save (Some action.key) best_effort with None -> () | Some e -> set_local e);
                 poll ()
               end)
           | _ -> ()));
        let duration = match started with None -> 0. | Some start ->
          (match call Clock (fun () -> match read_clock () with Error e -> Error e | Ok stop ->
             let elapsed=stop-.start in
             if not (Float.is_finite elapsed) then Error (failure "nonfinite elapsed clock")
             else Ok (round_seconds elapsed)) with None -> 0. | Some d -> d) in
        let c = !completion in
        let status,code,error = match !local with None -> c.status,c.exit_code,None
          | Some e -> !local_status,e.exit_code,!local_message in
        let verdict = if action.operation=Action.Report && (c.status=Accepted || c.status=Rejected) then Some c.status else None in
        occurrences.(i) <- {occurrences.(i) with status=(if status=Accepted || status=Rejected then Completed else status);
          exit_code=Some code;duration_seconds=duration;error;verdict};
        hook Finished !publication;
        (match outcome with Some o when action.operation=Action.Postcheck -> ignore (call Observe (fun () -> observe action o; Ok ())) | _ -> ());
        (match !local with Some e -> occurrences.(i) <- {occurrences.(i) with
          status=(if !local_status=Rejected || !local_status=Accepted then Completed else !local_status);
          exit_code=Some e.exit_code;error= !local_message} | _ -> ());
        let o=occurrences.(i) in
        state := {!state with steps=(!state).steps@[{operation=action.operation;status=(if o.status=Completed then status else o.status);
          exit_code=Option.get o.exit_code;duration_seconds=duration;best_effort;error=o.error}]};
        (match save (Some action.key) best_effort with None -> () | Some e when !local=None ->
          occurrences.(i) <- {occurrences.(i) with status=error_status e;exit_code=Some e.exit_code;error=Some e.message};
          let updated = {operation=action.operation;status=error_status e;exit_code=e.exit_code;
            duration_seconds=duration;best_effort;error=Some e.message} in
          state := {!state with steps=List.rev (updated :: List.tl (List.rev (!state).steps))}
         | Some _ -> ())
      end
    end) actions;
  if (!state).primary=None then state := {!state with status=Completed;exit_code=0};
  ignore (save None false);
  snapshot ()
