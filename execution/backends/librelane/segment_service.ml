module J = Identity_json
module Stage = Execution_plan.Stage
module B = Backend_protocol
module T = Execution_transport
type checkpoint = { path : string; manifest : Checkpoint.t }
type segment = { request : B.request; process : T.result; reply : B.reply; report : Segment_report.t;
  transport_record : string; transport_result : string }
type runtime = Docker of T.docker | Fixture of T.native * string
type t = { workspace : string; project : string; pdk_root : string; store : string;
   workers : int; cancel : unit -> int option;
  compatibility : J.t; runtime : runtime; environment : string list; timeout : float;
   initial : checkpoint Runner.source option; completed : (string, segment) Hashtbl.t;
   mutable transport_records : (string * string) list;
    verify_runtime : unit -> (unit, string) result;
    verify_inputs : unit -> (unit, string) result;
    verify_resolved : string -> (unit, string) result;
   failure_exit_code : B.operation -> T.result -> int option }
let bridge_sha256 = "37a848289286479715b7d6ac1d9aa6b3c43a8f3e205a85d6f510794daecf3295"
let protocol_sha256 = "eb6490db856f551ffe2ab35ea872bdc85c86801339e67a9d5bdf427238b25ae1"
let macro_runtime_sha256 = "20b4288ce4ecb89577454edcaa489583fd8eeaed14f663888b59c9eba7fa9048"
exception Service_error of Runner.error
let fail s = raise (Service_error (Runner.failure s))
let checked = function Ok x -> x | Error s -> fail s
let codec = function Ok x -> x | Error e -> fail (J.error_message e)
let str s = codec (J.string s)
let get j k = match j with J.Object xs -> (match List.assoc_opt k xs with Some v -> v | None -> fail ("missing " ^ k)) | _ -> fail "object required"
let text = function J.String s -> s | _ -> fail "string required"
let rec exception_error = function
  | Service_error e | Runner.Callback_error e -> e
  | Checkpoint_publisher.Cleanup_error (exn,s) -> let e = exception_error exn in
    (match e.kind with Runner.Failure -> Runner.failure ~exit_code:e.exit_code ~secondary:(e.secondary @ [s]) e.message
     | Runner.Cancellation n -> Runner.cancellation ~signal:n ~secondary:(e.secondary @ [s]) e.message)
  | exn -> Runner.failure (Printexc.to_string exn)
let protect f = try Ok (f ()) with exn -> Error (exception_error exn)
let read_json p = codec (Result_store.read_json p)
let directory p =
  let normalized = checked (Checkpoint.absolute_path p) in
  if Filename.is_relative p || normalized <> p || Unix.realpath p <> p
     || (Unix.lstat p).Unix.st_kind <> Unix.S_DIR then fail ("prepared canonical directory required: " ^ p);
  p
let within parent child = String.starts_with ~prefix:(parent ^ "/") child
let regular p = if (Unix.lstat p).Unix.st_kind <> Unix.S_REG then fail ("regular artifact required: " ^ p)
let verify_resources t =
  List.iter (fun (name,hash) -> let path = Filename.concat (t.project ^ "/bridge") name in
    regular path;
    if (checked (Checkpoint.file_record path)).sha256 <> hash then fail ("transported resource changed: " ^ name))
     ["librelane_bridge.py",bridge_sha256; "backend_protocol.py",protocol_sha256];
   let manifest=read_json (t.project ^ "/manifest.json") in
   if Macro_contract.active manifest then (
     Macro_contract.require_segmented manifest;
     if (checked (Checkpoint.file_record (t.project ^ "/bridge/physical_runtime.py"))).sha256 <> macro_runtime_sha256 then
       fail "transported macro runtime changed")
let handoff t ~boundary path =
  ignore (directory path);
  if not (within t.store path) then fail "checkpoint must belong to selected mounted store";
   let manifest = checked (Checkpoint.validate ~restart:true ~restart_to:boundary ~compatibility:t.compatibility path) in
   if Macro_contract.active (read_json (t.project ^ "/manifest.json")) && manifest.schema <> Checkpoint.Schema_3 then
     fail "macro continuation requires checkpoint schema3 profile";
  {path; manifest}
let create_internal ~runtime ~workspace ~project ~pdk_root ~checkpoint_store ~compatibility ~environment ~timeout ?(workers=6) ?(cancel=fun () -> None) ?initial_checkpoint ?(verify_runtime=(fun () -> Ok ())) ?(verify_inputs=(fun () -> Ok ())) ?(verify_resolved=(fun _ -> Ok ())) ?(failure_exit_code=(fun _ _ -> None)) () = protect (fun () ->
   if workers < 1 || workers > 6 then fail "segment workers must be in 1..6";
  if not (Float.is_finite timeout) || timeout <= 0. then fail "finite positive timeout required";
  let workspace = directory workspace and project = directory project and pdk_root = directory pdk_root
  and store = directory checkpoint_store in
  if not (within workspace project && within workspace store) || project = store || within project store || within store project
     || workspace = pdk_root || within workspace pdk_root || within pdk_root workspace then fail "invalid prepared path ownership";
  ignore (directory (project ^ "/bridge")); ignore (directory (project ^ "/candidates"));
  ignore (codec (J.exact_object compatibility ~fields:["bundle_identity";"target";"requested_tools";"binding";"bridge_sha256";"project";"pdk_root"]));
  if get compatibility "project" <> str project || get compatibility "pdk_root" <> str pdk_root
     || get compatibility "bridge_sha256" <> str bridge_sha256 then fail "prepared compatibility paths/bridge mismatch";
  let binding = checked (T.of_json (get compatibility "binding")) in
  (match binding with T.Native _ -> fail "stage compatibility requires exact Docker image"
   | T.Docker d -> if d.image_id <> (Stage.capability Stage.Synthesis).tested_image_id then fail "unsupported stage image";
     match runtime with Docker selected -> if T.to_json (T.docker ~executable:selected.executable ~rootless:selected.rootless
       ~server_version:selected.server_version ~daemon_id:selected.daemon_id ~name_on_server:selected.name_on_server
       ~declared_ref:selected.declared_ref ~image_id:selected.image_id ~repo_digests:selected.repo_digests
       ~os:selected.os ~architecture:selected.architecture ~variant:selected.variant ~flags:selected.flags ()) <> T.to_json binding
       then fail "selected runtime differs from compatibility" | Fixture _ -> ());
      let t = {workspace;project;pdk_root;store;compatibility;runtime;environment;timeout;workers;cancel;initial=None;completed=Hashtbl.create 8;transport_records=[];verify_runtime;verify_inputs;verify_resolved;failure_exit_code} in
  let prog = match runtime with Docker d ->
    ignore (T.docker_command d ~project:workspace ~pdk_root ~name:(T.owned_name ()) ~workdir:project
       ~workers ~pdk:(text (get (get compatibility "target") "technology")) ~uid:(Unix.getuid ()) ~gid:(Unix.getgid ()) ());
    d.executable | Fixture (n,fixture) ->
      if Filename.is_relative fixture || String.contains fixture '\000' then fail "absolute testing fixture required";
      regular fixture; n.interpreter in
  ignore (Process.make ~prog ~argv:[prog] ~cwd:project ~environment:(T.clean_environment environment)
    ~stdout:(Process.Descriptor Unix.stdout) ~stderr:(Process.Descriptor Unix.stderr));
  verify_resources t;
  let initial = Option.map (fun path -> ignore (directory path);
    if not (within store path) then fail "initial checkpoint outside mounted store";
    let manifest = checked (Checkpoint.validate ~restart:true ~compatibility path) in
    {Runner.boundary=manifest.boundary; checkpoint={path;manifest}}) initial_checkpoint in
  {t with initial})
let create ~binding = match binding with
  | T.Docker d -> create_internal ~runtime:(Docker d)
     | T.Native _ -> fun ~workspace:_ ~project:_ ~pdk_root:_ ~checkpoint_store:_ ~compatibility:_ ~environment:_ ~timeout:_ ?workers:_ ?cancel:_ ?initial_checkpoint:_ ?verify_runtime:_ ?verify_inputs:_ ?verify_resolved:_ ?failure_exit_code:_ () ->
    Error (Runner.failure "native physical stage support is not demonstrated")
let initial t = t.initial
(* POSIX signal values at Runner's boundary, rather than OCaml's negative
   portable signal constants. This service targets the same Linux/POSIX platform
   as the no-replace publisher. Unknown positive native signals retain their value. *)
let signal n = match List.assoc_opt n
  [Sys.sighup,1;Sys.sigint,2;Sys.sigquit,3;Sys.sigill,4;Sys.sigtrap,5;Sys.sigabrt,6;Sys.sigbus,7;
   Sys.sigfpe,8;Sys.sigkill,9;Sys.sigusr1,10;Sys.sigsegv,11;Sys.sigusr2,12;
   Sys.sigpipe,13;Sys.sigalrm,14;Sys.sigterm,15;Sys.sigchld,17;Sys.sigcont,18;
   Sys.sigstop,19;Sys.sigtstp,20;Sys.sigttin,21;Sys.sigttou,22;Sys.sigurg,23;
   Sys.sigxcpu,24;Sys.sigxfsz,25;Sys.sigvtalrm,26;Sys.sigprof,27;
   Sys.sigpoll,29;Sys.sigsys,31] with Some s -> s | None -> if n > 0 then n else 1
let process_error (r:T.result) =
  let secondary = (match r.process.cleanup_error with None -> [] | Some s -> [s]) @
    (match r.cleanup with None -> [] | Some c -> match c.error with None -> [] | Some s -> [s]) in
  match r.process.outcome with
  | Process_supervisor.Cancelled n -> Runner.cancellation ~secondary ~signal:(signal n) "backend execution interrupted"
  | Process_supervisor.Exited n when n <> 0 -> Runner.failure ~exit_code:n ~secondary "backend process exited nonzero"
  | Process_supervisor.Signaled n -> Runner.failure ~exit_code:(128 + signal n) ~secondary "backend process terminated by signal"
  | Process_supervisor.Timed_out -> Runner.failure ~exit_code:124 ~secondary "backend execution timed out"
  | Process_supervisor.Failed s -> Runner.failure ~secondary s
  | Process_supervisor.Exited _ -> Runner.failure ~secondary "backend transport cleanup failed"
let verify_reply_internal request =
  regular request.B.reply_path;
  let reply = checked (B.reply_of_json ~request (read_json request.reply_path)) in
  (match reply.outcome with B.Failed s -> fail ("backend reply failed: " ^ s) | B.Completed | B.Validated -> ());
  List.iter (fun (_, (a:B.artifact)) -> regular a.path;
    let actual = checked (Checkpoint.file_record a.path) in
    if actual.sha256 <> a.sha256 || actual.bytes <> a.bytes then fail ("backend artifact mismatch: " ^ a.path)) reply.artifacts;
   reply
let verify_reply request = try verify_reply_internal request with
  | Service_error e -> raise (Runner.Callback_error e)
let write_fresh p j =
  let fd = Unix.openfile p [Unix.O_WRONLY;Unix.O_CREAT;Unix.O_EXCL] 0o600 in
  let oc = Unix.out_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_out_noerr oc) (fun () -> output_string oc (J.canonical j ^ "\n"); close_out oc)
let random () = let ic = open_in_bin "/dev/urandom" in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let bytes = Bytes.of_string (really_input_string ic 16) in
    Bytes.set bytes 6 (Char.chr ((Char.code (Bytes.get bytes 6) land 15) lor 64));
    Bytes.set bytes 8 (Char.chr ((Char.code (Bytes.get bytes 8) land 63) lor 128));
     Cryptokit.transform_string (Cryptokit.Hexa.encode ()) (Bytes.to_string bytes))
let obj xs = codec (J.object_ xs)
let int n = codec (J.integer (string_of_int n))
let float n = codec (J.float n)
let opt f = function None -> J.null | Some x -> f x
let process_json (p:Process_supervisor.result) =
  let outcome,code,error = match p.outcome with
    | Exited n -> "exited",Some n,None | Signaled n -> "signaled",Some (signal n),None
    | Cancelled n -> "cancelled",Some (signal n),None | Timed_out -> "timed_out",None,None
    | Failed s -> "failed",None,Some s in
  let child_status = match p.child_status with None -> J.null
    | Some (Unix.WEXITED n) -> obj ["outcome",str "exited";"code",int n]
    | Some (Unix.WSIGNALED n) -> obj ["outcome",str "signaled";"code",int (signal n)]
    | Some (Unix.WSTOPPED n) -> obj ["outcome",str "stopped";"code",int (signal n)] in
  obj ["outcome",str outcome;"code",opt int code;"error",opt str error;"pid",opt int p.pid;
    "child_status",child_status;"elapsed_seconds",float p.elapsed_seconds;
    "stdout_path",opt str p.stdout_path;"stderr_path",opt str p.stderr_path;
    "cleanup_error",opt str p.cleanup_error]
let transport_records t = t.transport_records
let diagnostics_json t execution =
  let fields = match Runner.diagnostics_json execution with J.Object xs -> xs | _ -> assert false in
  obj (fields @ ["transport_records",J.array (List.map (fun (launch,terminal) ->
    obj ["launch",str launch;"terminal",str terminal]) t.transport_records)])
let call t ~operation ~boundary ~candidate ~source tag =
  Synthesis_envelope.check_boundary t.environment boundary;
  if Synthesis_envelope.requested t.environment && source <> None then
    invalid_arg "strict synthesis envelope is fresh-only";
  checked (t.verify_runtime ());
  verify_resources t;
  checked (t.verify_inputs ());
  let request = checked (B.make_request ~operation ~boundary ~candidate ~project:t.project ~pdk_root:t.pdk_root
    ~request_id:tag ~tag ~reply_path:(t.project ^ "/" ^ tag ^ "-reply.json") ~bridge_sha256
    ~checkpoint:(Option.map (fun (c:checkpoint) -> c.path) source)
    ~source_boundary:(Option.map (fun (c:checkpoint) -> c.manifest.boundary) source)) in
  let path = t.project ^ "/" ^ tag ^ "-request.json" in
  write_fresh path (B.request_to_json request);
  let bridge = t.project ^ "/bridge/librelane_bridge.py" in
  let output = Process.File (t.workspace ^ "/" ^ tag ^ ".log") in
  let invocation = match t.runtime with
     | Docker d -> T.docker_invocation d ~workers:t.workers ?pids_limit:(Synthesis_envelope.pids_limit t.environment) ~project:t.workspace ~pdk_root:t.pdk_root ~workdir:t.project
      ~pdk:(text (get (get t.compatibility "target") "technology")) ~interpreter:true ~arguments:[bridge;path]
      ~cwd:t.project ~environment:t.environment ~stdout:output ~stderr:output ()
    | Fixture (n,fixture) -> T.native_invocation n ~arguments:[fixture;bridge;path]
      ~cwd:t.project ~environment:t.environment ~stdout:output ~stderr:output in
   let launch_path = t.workspace ^ "/" ^ tag ^ "-transport.json" in
   let terminal_path = t.workspace ^ "/" ^ tag ^ "-transport-result.json" in
   let specification = T.process_specification invocation in
   let binding = match t.runtime with Docker _ -> checked (T.of_json (get t.compatibility "binding"))
     | Fixture (n,_) -> T.native ~interpreter:n.interpreter in
   let correlation = (if Synthesis_envelope.requested t.environment then ["execution_envelope",Synthesis_envelope.to_json ()] else []) @ ["schema_version",int 1;"request_id",str request.request_id;
     "request_path",str path;"request_sha256",str (B.request_identity request);
     "launch_record",str launch_path;"terminal_record",str terminal_path] in
   let run_dir=t.project ^ "/src/runs/" ^ tag in
   let fresh_run_dir =
     if operation=B.Execute_segment && (try ignore (Unix.lstat run_dir);false with Unix.Unix_error (Unix.ENOENT,_,_) -> true)
     then str run_dir else J.null in
   (* Immutable per-call launch and terminal records. Never serialize the ambient
      environment: only the explicit argv/resources and checked binding. *)
   write_fresh launch_path (obj (correlation @ ["kind",str "backend_transport_launch";"fresh_run_dir",fresh_run_dir;
     "recorded_at_unix",float (Unix.gettimeofday ());"argv",J.array (List.map str specification.argv);
     "executable",str specification.prog;"cwd",str specification.cwd;"binding",T.to_json binding;
     "container_name",opt (fun (_,name) -> str name) (T.container invocation);
     "host_uid",int (Unix.getuid ());"host_gid",int (Unix.getgid ());
     "timeout_seconds",float t.timeout;
     "resources",(match t.runtime with Fixture _ -> J.null | Docker d -> obj [
        "cpus",int t.workers;"openlane_max_cores",int t.workers;"openroad_threads",int t.workers;"omp_num_threads",int t.workers;
       "container_user",(if d.rootless then J.null else str (Printf.sprintf "%d:%d" (Unix.getuid ()) (Unix.getgid ())))]) ]));
   t.transport_records <- t.transport_records @ [launch_path,terminal_path];
   let started = Unix.gettimeofday () in
    let process = T.run ~cancel:t.cancel ~timeout:t.timeout invocation in
   let recording_error = try
     write_fresh terminal_path (obj (correlation @ ["kind",str "backend_transport_result";
       "started_at_unix",float started;"ended_at_unix",float (Unix.gettimeofday ());
       "transport_succeeded",J.bool (T.succeeded process);"process",process_json process.process;
       "cleanup",opt (fun (c:T.cleanup) -> obj ["error",opt str c.error;
         "attempts",J.array (List.map (fun (r:T.removal) -> obj ["process",process_json r.result;
           "diagnostic",str r.diagnostic]) c.attempts)]) process.cleanup])); None
     with exn -> Some ("transport evidence recording failed: " ^ Printexc.to_string exn) in
   if not (T.succeeded process) then (
     let cause = process_error process in
     let cause = match cause.kind with Runner.Cancellation _ -> cause | Runner.Failure ->
       match t.failure_exit_code operation process with None -> cause | Some exit_code ->
         Runner.failure ~exit_code ~secondary:cause.secondary cause.message in
      let secondary = cause.secondary @ Option.to_list recording_error in
      let cause = match cause.kind with
        | Runner.Failure -> Runner.failure ~exit_code:cause.exit_code ~secondary cause.message
        | Runner.Cancellation signal -> Runner.cancellation ~signal ~secondary cause.message in
      raise (Service_error cause));
   Option.iter fail recording_error;
   checked (t.verify_runtime ());
   verify_resources t;
   checked (t.verify_inputs ());
   let reply = verify_reply request in
   checked (t.verify_resolved (candidate ^ "/resolved.json"));
   request,process,reply,launch_path,terminal_path
let validate t plan = protect (fun () ->
   verify_resources t;
   Macro_contract.require_segmented_plan (read_json (t.project ^ "/manifest.json")) plan;
  checked (t.verify_inputs ());
  match plan with Execution_plan.Coarse _ -> fail "segment service requires a stage plan"
  | Execution_plan.Stages p ->
    if Execution_plan.Stage_plan.schema_version p <> 4 then
      fail "historical six-stage recipes are read-only; prepare a schema-4 recipe for this bridge";
    checked (Hardcaml_asic_flow_domain.Backends.Librelane.Mapping.validate_sequence
      ~initial_boundary:(Execution_plan.Stage_plan.source_boundary p)
      (Execution_plan.Stage_plan.sequence p));
    match Execution_plan.Stage_plan.source_boundary p,t.initial with
    | None,None -> ()
    | Some boundary,Some source -> let c = handoff t ~boundary:(List.hd (Execution_plan.Stage_plan.sequence p)) source.checkpoint.path in
      if c.manifest.boundary <> boundary || c.manifest.identity <> source.checkpoint.manifest.identity
      then fail "initial source boundary/identity mismatch"
    | _ -> fail "stage plan does not match prepared handoff")
let stage (a:Execution_plan.Action.t) = match a.operation,a.stage with
  | Segment,Physical s -> s | _ -> fail "segment action required"
let invoke t action (context:(unit,checkpoint) Runner.context) = protect (fun () ->
  let boundary = stage action in
  let source = Option.map (fun (s:checkpoint Runner.source) -> let c = handoff t ~boundary s.checkpoint.path in
    if c.manifest.boundary <> s.boundary || c.manifest.identity <> s.checkpoint.manifest.identity then fail "source checkpoint changed"; c) context.source in
  let tag = Stage.to_string boundary ^ "-" ^ random () in
  let candidate = t.project ^ "/candidates/" ^ tag in
   let request,process,reply,transport_record,transport_result = call t ~operation:B.Execute_segment ~boundary ~candidate ~source tag in
   let report = checked (Segment_report.of_json ~request (read_json (candidate ^ "/segment.json"))) in
   let macro=Macro_contract.active (read_json (t.project ^ "/manifest.json")) in
   let reported=match report.raw with J.Object xs -> List.mem_assoc "macro_profile" xs | _ -> false in
   if macro<>reported then fail "macro segment profile missing or unexpected";
   let segment = {request;process;reply;report;transport_record;transport_result} in
  Hashtbl.add t.completed candidate segment;
  Runner.Segment_result (segment,{Runner.status=Completed;exit_code=0;error=None}))
let rec publisher_error = function
  | Checkpoint_publisher.Runtime_interrupted n -> Runner.cancellation ~signal:n "publication runtime validation interrupted"
  | Checkpoint_publisher.Cleanup_failed (e,s) -> let e = publisher_error e in
    (match e.kind with Runner.Failure -> Runner.failure ~exit_code:e.exit_code ~secondary:(e.secondary @ [s]) e.message
     | Runner.Cancellation n -> Runner.cancellation ~signal:n ~secondary:(e.secondary @ [s]) e.message)
  | e -> Runner.failure (Checkpoint_publisher.error_message e)
let publish t action (_:(unit,checkpoint) Runner.context) segment = protect (fun () ->
  let boundary = stage action in
  if segment.request.boundary <> boundary || Hashtbl.find_opt t.completed segment.request.candidate <> Some segment
     || not (T.succeeded segment.process) then fail "publication requires this service's completed call";
   verify_resources t;
   checked (t.verify_inputs ());
   checked (t.verify_resolved (segment.request.candidate ^ "/resolved.json"));
  let reply = verify_reply segment.request in
  if B.reply_to_json reply <> B.reply_to_json segment.reply then fail "completed reply evidence changed";
  ignore (checked (Segment_report.of_json ~request:segment.request (read_json (segment.request.candidate ^ "/segment.json"))));
  let validate_runtime (r:Checkpoint_publisher.runtime_request) =
    (* Throw explicit Runner errors to preserve actual process code/secondary cleanup
       through publisher-owned exception cleanup, rather than flattening to a string. *)
    let tag = "validate-" ^ Stage.to_string boundary ^ "-" ^ random () in
    ignore (call t ~operation:B.Validate_publication ~boundary:r.boundary ~candidate:r.staging ~source:None tag);
    Checkpoint_publisher.Validated in
   let p = match Checkpoint_publisher.publish ~candidate:segment.request.candidate ~store:t.store ~compatibility:t.compatibility
     ~boundary ~validate_runtime ~before_promote:(fun staging ->
       checked (t.verify_inputs ());
       checked (t.verify_resolved (staging ^ "/resolved.json"))) () with Ok p -> p | Error e -> raise (Service_error (publisher_error e)) in
  Hashtbl.remove t.completed segment.request.candidate;
  {Runner.evidence=Runner.of_checkpoint_publication p; checkpoint={path=p.path;manifest=p.checkpoint}})
let callbacks t = {Runner.validate=validate t;invoke=invoke t;publish=publish t}
let execute ?persist ?lifecycle ~attempt_id plan t =
  let cancel () = Option.map (fun n -> Runner.cancellation ~signal:(signal n) "stage execution interrupted") (t.cancel ()) in
  Runner.execute ?initial:t.initial ~cancel ?persist ?lifecycle ~attempt_id plan (callbacks t)
module Testing = struct
  let create_native_fixture ~fixture ~interpreter =
    create_internal ~runtime:(Fixture ((match T.native ~interpreter with T.Native n -> n | _ -> assert false),fixture))
end
