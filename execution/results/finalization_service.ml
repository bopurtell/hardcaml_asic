[@@@alert "-unsafe_multidomain"]
module J=Identity_json
module E=Evidence_json
module R=Result_store
module T=Execution_transport
module B=Backend_protocol
module S=Execution_plan.Stage
let checked = function Ok x->x | Error s->invalid_arg s
let codec = function Ok x->x | Error e->invalid_arg (J.error_message e)
let s x=codec (J.string x)
let int x=codec (J.integer x)
let obj x=codec (J.object_ x)
let get j k=match j with J.Object xs->Option.value (List.assoc_opt k xs) ~default:J.null | _->J.null
let text=function J.String x->x | _->invalid_arg "expected string"
let array=function J.Array xs->xs | _->invalid_arg "expected array"
let json p=codec (R.read_json p)
let hash p=(checked (Checkpoint.file_record p)).sha256
let file p=let r=checked (Checkpoint.file_record p) in obj ["sha256",s r.sha256;"bytes",codec (J.integer r.bytes)]
let now () = let t=Unix.gettimeofday () in let sec=floor t in let tm=Unix.gmtime sec in
  Printf.sprintf "%04d-%02d-%02dT%02d:%02d:%02d.%06d+00:00" (tm.tm_year+1900) (tm.tm_mon+1) tm.tm_mday tm.tm_hour tm.tm_min tm.tm_sec (int_of_float ((t-.sec)*.1e6))
type selection={value:J.t;compatibility:J.t;publications:(string*Checkpoint.t) list}
let selected ?(synthesis_only=false) execution =
   let value=json execution in
   (* Reject bounded macro executions before the fresh-full eligibility check,
      so a legitimate Timing prefix gets its actual capability reason. *)
   (match get value "artifacts" with J.Array artifacts -> List.iter (fun a ->
     match get a "path" with J.String p ->
       let m=checked (Checkpoint.read p) in
       if m.schema=Checkpoint.Schema_3 then invalid_arg "segmented SRAM finalization is unsupported; maximum endpoint is Timing"
     | _ -> ()) artifacts | _ -> ());
  (* Historical finalization accepted reduced occurrence objects. Preserve that
     contract when the optional named-flow field is absent; its presence requires
     the complete new Runner record and a verified immutable snapshot. *)
  let methodology = match value with
    | J.Object xs when List.mem_assoc "methodology" xs ->
      (checked (Stage_execution_record.of_json value)).methodology
    | _ -> ignore (codec (J.exact_object value ~fields:["attempt_id";"plan";"occurrences";"status";"exit_code";"artifacts"])); None in
  let attempt=text (get value "attempt_id") in if attempt="" then invalid_arg "malformed segmented execution record";
  let plan=checked (Execution_plan.of_json (get value "plan")) in
  let stages =
    match plan with
    | Execution_plan.Stages p ->
      let expected =
        if synthesis_only then [S.Synthesis]
        else if Execution_plan.Stage_plan.schema_version p = 4
        then S.all
        else List.filter (fun s -> s <> S.Timing) S.all
      in
      if Execution_plan.Stage_plan.sequence p <> expected
         || Execution_plan.Stage_plan.source_boundary p <> None
      then invalid_arg "finalization requires a complete fresh recipe";
      expected
    | _ -> invalid_arg "finalization requires a complete fresh stage recipe"
  in
  if get value "status"<>s "completed" || get value "exit_code"<>int "0" then invalid_arg "implementation execution did not complete";
  let occurrences=array (get value "occurrences") and artifacts=array (get value "artifacts") in
  if List.length occurrences <> List.length stages
     || List.length artifacts <> List.length stages
  then invalid_arg "missing selected stage evidence";
  let compatibility=ref None in
  let publications=List.map2 (fun boundary (occurrence,artifact) ->
    let name=S.to_string boundary in
    if get occurrence "status"<>s "completed" || get occurrence "exit_code"<>int "0"
      || get occurrence "action_key"<>s ("implementation."^name)
      || get occurrence "occurrence_id"<>s (attempt^"/implementation."^name)
      || get artifact "boundary"<>s name then invalid_arg "inconsistent stage completion/evidence";
     let path=text (get artifact "path") in
     let m=checked (Checkpoint.read path) in
     if m.schema=Checkpoint.Schema_3 then invalid_arg "segmented SRAM finalization is unsupported; maximum endpoint is Timing";
     let c=match !compatibility with Some c->c | None->compatibility:=Some m.compatibility;m.compatibility in
     (match get c "project" with J.String project ->
       if Sys.file_exists (project ^ "/manifest.json") && Macro_contract.active (json (project ^ "/manifest.json")) then
         invalid_arg "segmented SRAM finalization is unsupported; maximum endpoint is Timing"
      | _ -> ());
    ignore (checked (Checkpoint.validate ~compatibility:c path));
    Option.iter (fun r -> if get c "bundle_identity" <> s (Hardcaml_asic.Methodology_plan.bundle_identity (Methodology_execution_record.plan r))
      then invalid_arg "methodology/checkpoint bundle mismatch") methodology;
    if m.identity<>text (get artifact "identity") || m.boundary<>boundary then invalid_arg "selected publication identity/boundary mismatch";
    if m.schema<>Checkpoint.Schema_2 then invalid_arg "finalization requires checkpoint schema 2";
     path,m) stages (List.combine occurrences artifacts) in
  List.iter (fun name -> let hashes=List.map (fun (p,_)->hash (p^"/"^name)) publications in
    if not (List.for_all ((=) (List.hd hashes)) hashes) then invalid_arg "selected stages have different configuration/closure") ["closure.json";"resolved.json"];
  {value;compatibility=Option.get !compatibility;publications}
let copy src dst = let ic=open_in_bin src in Fun.protect ~finally:(fun ()->close_in_noerr ic) (fun ()->
  let oc=open_out_bin dst in Fun.protect ~finally:(fun ()->close_out_noerr oc) (fun ()->
    let b=Bytes.create 65536 in let rec loop ()=let n=input ic b 0 (Bytes.length b) in if n>0 then (output oc b 0 n;loop ()) in loop ()))
let rec copy_tree src dst = Unix.mkdir dst 0o777; Array.iter (fun name->let a=src^"/"^name and b=dst^"/"^name in
  if (Unix.stat a).Unix.st_kind=Unix.S_DIR then copy_tree a b else copy a b) (Sys.readdir src)
let rec files root = if not (Sys.file_exists root) then [] else
  if (Unix.stat root).Unix.st_kind=Unix.S_DIR then Array.to_list (Sys.readdir root) |> List.sort String.compare |> List.concat_map (fun n->files (root^"/"^n))
  else if (Unix.stat root).Unix.st_kind=Unix.S_REG then [root] else []
let evidence j=E.value (checked (E.parse (J.canonical j)))
let csv_value = function `String x->x | `Bool true->"True" | `Bool false->"False" | `Null->"" | j->
  (match j with `Float n when Float.is_nan n -> "nan"
   | `Float n when not (Float.is_finite n) -> if n<0. then "-inf" else "inf"
   | `Int _ | `Intlit _ | `Float _ -> String.trim (E.record_bytes j) | _->E.repr j)
let csv x=if String.contains x ',' || String.contains x '"' || String.contains x '\r' || String.contains x '\n' then
  "\"" ^ String.concat "\"\"" (String.split_on_char '"' x) ^ "\"" else x
type outcome={directory:string;record:Finalization_record.t}
let run ~execution ~store ~environment ?(accept=false) ?support_tools ?precheck_python ?testbench ?testbench_top ?replay_trace ?allocated () =
  let a=R.allocate store in let directory=a.directory in let op=directory^"/operation.json" in
  let opt= function None->J.null | Some x->s x in
  let execution=if Filename.is_relative execution then Filename.concat (Sys.getcwd ()) execution else execution in
  let fields=ref ["contract",s Finalization_record.contract;"schema_version",int "1";"attempt_id",s a.identity;
    "status",s "running";"phase",s "validation";"started_at",s (now ());"ended_at",J.null;
    "acceptance",s "not_evaluated";"external_postcheck",s "not_evaluated";
    "request",obj ["execution",s execution;"accept",J.bool accept;"support_tools",opt support_tools;
      "precheck_python",opt precheck_python;"testbench",opt testbench;"testbench_top",opt testbench_top;"replay_trace",opt replay_trace];
    "commands",J.array [];"results",J.null;"error",J.null] in
  let set k v=fields:=(k,v)::List.remove_assoc k !fields in
  let save ()=R.save_json op (obj !fields) in save ();
  let signal=ref None in let handler n=if !signal=None then signal:=Some n in
  let old_int=Sys.signal Sys.sigint (Sys.Signal_handle handler) and old_term=Sys.signal Sys.sigterm (Sys.Signal_handle handler) in
  let cancel () = !signal in
  let check_cancel ()=match !signal with None->() | Some n->raise (Runner.Callback_error (Runner.cancellation ~signal:(Segment_service.signal n) "finalization interrupted")) in
  Fun.protect ~finally:(fun ()->Sys.set_signal Sys.sigint old_int;Sys.set_signal Sys.sigterm old_term) (fun ()->
  let code=try
    Option.iter (fun f->f directory a.identity) allocated; check_cancel ();
     let synthesis_only=Synthesis_envelope.requested environment in
     if synthesis_only && accept then invalid_arg "strict synthesis report collection does not support --accept";
     let selected_=selected ~synthesis_only execution in let c=selected_.compatibility and pubs=selected_.publications in
     if synthesis_only then (Synthesis_envelope.check_plan environment (checked (Execution_plan.of_json (get selected_.value "plan")));
       set "execution_envelope" (Synthesis_envelope.to_json ()));
    ignore (codec (J.exact_object c ~fields:["bundle_identity";"target";"requested_tools";"binding";"bridge_sha256";"project";"pdk_root"]));
     let execution_hash=hash execution in
     Option.iter (set "timing_analysis") (Timing_analysis.load_file ~expected_record:selected_.value execution);
    set "execution" (obj ["path",s (Unix.realpath execution);"sha256",s execution_hash;"attempt_id",get selected_.value "attempt_id"]);
    let binding=checked (T.of_json (get c "binding")) in
    set "binding" (T.to_json binding);
    let selected_checkpoints=J.array (List.map (fun (p,(m:Checkpoint.t))->obj ["path",s p;"identity",s m.identity;"boundary",s (S.to_string m.boundary)]) pubs) in
    set "selected_checkpoints" selected_checkpoints;save ();
    let project=text (get c "project") and pdk=text (get c "pdk_root") in
    let bridge=project^"/bridge/librelane_bridge.py" in
    let verify_resources () =
      if get c "bridge_sha256"<>s Segment_service.bridge_sha256 || hash bridge<>Segment_service.bridge_sha256 then invalid_arg "retained bound bridge changed or unsupported";
      if hash (project^"/bridge/backend_protocol.py")<>Segment_service.protocol_sha256 then invalid_arg "retained backend protocol changed or unsupported" in
    verify_resources ();
    let d=match binding with T.Docker d when d.image_id=(S.capability S.Synthesis).tested_image_id->d | _->invalid_arg "unsupported finalization runtime" in
    let probes=directory^"/runtime-probes" in Unix.mkdir probes 0o777;
    let probe=Runtime_probe.create_cancellable ~cancel ~directory:probes ~environment ~timeout:20. in
    checked (Runtime_probe.verify probe binding);check_cancel ();
    let tag="finalize-validate-"^String.sub (T.owned_name ()) 24 32 in
     let request=checked (B.make_request ~operation:B.Validate_publication ~boundary:(if synthesis_only then S.Synthesis else S.Signoff)
      ~candidate:(fst (List.hd (List.rev pubs))) ~project ~pdk_root:pdk ~request_id:tag ~tag
      ~reply_path:(directory^"/runtime-validation-reply.json") ~bridge_sha256:Segment_service.bridge_sha256 ~checkpoint:None ~source_boundary:None) in
    let request_path=directory^"/runtime-validation-request.json" in R.save_json request_path (B.request_to_json request);
    let output=Process.File (directory^"/runtime-validation.log") in
     let invocation=T.docker_invocation d ~workers:(if synthesis_only then 6 else 20)
       ?pids_limit:(Synthesis_envelope.pids_limit environment) ~project:(Filename.dirname project) ~pdk_root:pdk ~workdir:project
      ~extra_mounts:[directory;Filename.dirname (fst (List.hd (List.rev pubs)))] ~pdk:(text (get (get c "target") "technology"))
      ~interpreter:true ~arguments:[bridge;request_path] ~cwd:project ~environment ~stdout:output ~stderr:output () in
    let argv=(T.process_specification invocation).argv in set "commands" (J.array [J.array (List.map s argv)]);save ();
     let correlation=(if synthesis_only then ["execution_envelope",Synthesis_envelope.to_json ()] else []) @ ["schema_version",int "1";"request_id",s request.request_id;"request_path",s request_path;
      "request_sha256",s (B.request_identity request)] in
    R.save_json (directory^"/runtime-validation-transport.json") (obj (correlation @ [
      "argv",J.array (List.map s argv);"binding",T.to_json binding;"cwd",s project;
      "container_name",(match T.container invocation with None->J.null | Some (_,name)->s name);
      "timeout_seconds",int "120";"cleanup_timeout_seconds",int "5"]));
    let process=T.run ~cancel ~timeout:120. invocation in
    let cleanup=Option.map (fun (cleanup:T.cleanup)->J.array (List.map (fun (r:T.removal)->obj (
      ["status",s (if Process_supervisor.succeeded r.result then "removed-or-absent" else "failed");
       "process",Segment_service.process_json r.result;"diagnostic",s r.diagnostic] @
      if Process_supervisor.succeeded r.result then [] else ["error",s r.diagnostic])) cleanup.attempts)) process.cleanup in
    Option.iter (set "container_cleanup") cleanup;
    Option.iter (fun error->set "client_termination" (s ("failed: "^error))) process.process.cleanup_error;
    let recording_error=try
      R.save_json (directory^"/runtime-validation-transport-result.json") (obj (correlation @ [
        "process",Segment_service.process_json process.process;"cleanup",Option.value cleanup ~default:J.null;
        "transport_succeeded",J.bool (T.succeeded process)]));save ();None
      with exn->Some (Printexc.to_string exn) in
    (* Preserve interruption/process/cleanup precedence if terminal recording fails. *)
    if not (T.succeeded process) then (
      let e=Segment_service.process_error process in
      let secondary=e.secondary @ Option.to_list recording_error in
      let e=match e.kind with Runner.Cancellation n->Runner.cancellation ~signal:n ~secondary e.message
        | Runner.Failure->Runner.failure ~exit_code:e.exit_code ~secondary e.message in
      raise (Runner.Callback_error e));
    Option.iter (fun error->invalid_arg ("transport evidence recording failed: "^error)) recording_error;
    ignore (Segment_service.verify_reply request);checked (Runtime_probe.verify probe binding);verify_resources ();check_cancel ();
    set "runtime_validation" (s "completed");
    let manifest=json (project^"/manifest.json") in
    Manifest_metadata.validate (Yojson.Safe.from_string (J.canonical manifest));
    if get manifest "identity"<>get c "bundle_identity" then invalid_arg "retained project manifest identity mismatch";
    let target=directory^"/project" and flow=directory^"/evidence" in Unix.mkdir target 0o777;Unix.mkdir flow 0o777;
    copy (project^"/manifest.json") (target^"/manifest.json");copy_tree (project^"/constraints") (target^"/constraints");
    let provenance=ref (List.map (fun (p,(m:Checkpoint.t))->obj ["boundary",s (S.to_string m.boundary);"path",s p;"identity",s m.identity;"manifest_sha256",s (hash (p^"/manifest.json"))]) pubs) in
    let synth=flow^"/synthesis-state.json" in copy (fst (List.hd pubs)^"/state.json") synth;
     if not synthesis_only then copy (fst (List.hd (List.rev pubs))^"/state.json") (flow^"/signoff-state.json");
     let state=E.value (E.read (if synthesis_only then synth else flow^"/signoff-state.json")) in
    let eget j k=match j with `Assoc xs->List.assoc k xs | _->invalid_arg "state object required" in
    let final=flow^"/final" in Unix.mkdir final 0o777;copy (project^"/info.yaml") (final^"/info.yaml");
    let metrics=match eget state "metrics" with `Assoc xs->List.sort (fun (a,_) (b,_)->String.compare a b) xs | _->invalid_arg "metrics object required" in
    R.save_bytes (final^"/metrics.csv") (String.concat "" (List.map (fun (k,v)->csv k^","^csv (csv_value v)^"\r\n") metrics));
    let top=text (get manifest "top_module") in
    if top="" || Filename.basename top<>top || top="." || top=".." || String.contains top '\000' then invalid_arg "invalid top module path";
    List.iter (fun (key,relative)->let source=match eget state key with `String x->x | _->invalid_arg "view path required" in
      let dest=final^"/"^relative in Unix.mkdir (Filename.dirname dest) 0o777;copy source dest;
       provenance:= !provenance @ [obj ["boundary",s (if synthesis_only then "synthesis" else "signoff");"view",s key;"source",s source;"sha256",s (hash source);"mapped",s ("evidence/final/"^relative)]])
       (if synthesis_only then ["nl","nl/"^top^".nl.v"] else ["gds","gds/"^top^".gds";"nl","nl/"^top^".nl.v";"lef","lef/"^top^".lef"]);
    let context=obj ["run_id",s a.identity;"build_identity",get manifest "identity";"manifest_sha256",s (hash (project^"/manifest.json"));"bundle",s project;"status",s "completed";
       "requested_stage",s (if synthesis_only then "synthesis" else "full");"completed_stage",s (if synthesis_only then "synthesis" else "full");"outputs",obj ["flow",s "evidence"];
      "execution_binding_required",J.bool true;"execution_binding",T.to_json binding;
      "evidence_origin",obj ["contract",s Finalization_record.contract;"schema_version",int "1";"operation",s op;"selected_checkpoints",selected_checkpoints];
      "execution_mode",s "dockerized";"environment",obj ["requested",get c "requested_tools";"execution_binding",T.to_json binding]] in
     let adapter=obj ["metrics_csv",s (if synthesis_only then "derived from synthesis-state.json metrics, cumulative synthesis state" else "derived from signoff-state.json metrics, cumulative final state");
      "synthesis",s "published synthesis-state.json; no synthetic Yosys report";
       "scope",s (if synthesis_only then "synthesis only; physical views/checks unavailable, acceptance not evaluated" else "full denotes selected physical completion, not historical run completion")] in
    let mapped=List.map (fun p->String.sub p (String.length directory+1) (String.length p-String.length directory-1),file p) (files flow @ files target) in
    set "evidence_context" context;set "selected_evidence" (J.array !provenance);set "mapped_files" (obj mapped);set "adapter" adapter;
    set "phase" (s "collection");save ();check_cancel ();
    let collateral=ref [] in
    if accept then (
      let required name=function Some x when x<>""->x | _->invalid_arg ("accept requires "^name) in
      let support=required "support tools" support_tools and python=required "precheck Python" precheck_python
      and bench=required "testbench" testbench and top=required "top" testbench_top in
      collateral:=List.map (fun p->p,file p) (files (support^"/precheck") @ files (support^"/tech"));
      set "postcheck_collateral" (obj !collateral);set "phase" (s "external_postcheck");set "external_postcheck" (s "running");save ();
      let o=Postcheck_service.run ~run_dir:directory ~support_tools:support ~pdk_root:pdk ~precheck_python:python ~testbench:bench ~testbench_top:top
        ~environment ~context:(evidence context) ?replay_trace () in
      set "external_postcheck" (s o.status);set "postcheck_record" (s o.check_record);set "postcheck_sha256" (s (hash o.check_record));save ();
      if o.status="interrupted" then raise (Runner.Callback_error (Runner.cancellation ~signal:(o.exit_code-128) "external postcheck interrupted"));
      let check=json o.check_record in
      if get check "run_id"<>s a.identity || get check "build_identity"<>get manifest "identity"
        || get check "evidence_origin"<>get context "evidence_origin" then invalid_arg "postcheck evidence origin mismatch";
      let simulator=get (get check "tools") "execution_binding" in
      if (simulator<>J.null && simulator<>T.to_json binding) || (o.status="completed" && simulator<>T.to_json binding)
        then invalid_arg "postcheck execution binding mismatch");
     check_cancel ();verify_resources ();ignore (selected ~synthesis_only execution);
    if hash execution<>execution_hash then invalid_arg "selected execution changed during finalization";
    List.iter (fun (p,expected)->if file (directory^"/"^p)<>expected then invalid_arg "mapped evidence changed during finalization") mapped;
    List.iter (fun (p,expected)->if file p<>expected then invalid_arg "postcheck collateral changed during finalization") !collateral;
    let results=Collect_service.to_json (Collect_service.collect ~context:(evidence context) ~synthesis_state:synth directory) in
    let results=match results with `Assoc xs->`Assoc (("provenance",evidence (obj ["contract",s Finalization_record.contract;"operation",s op;"selected_evidence",J.array !provenance;"adapter",adapter]))::
      List.map (fun (k,v)-> if List.mem k ["metrics";"synthesis_checks";"checks"] then k,(match v with `Assoc items->`Assoc (List.map (fun (name,v)->name,(match v with `Assoc fs->
         let boundary=if synthesis_only || List.assoc_opt "stage" fs=Some (`String "Yosys.Synthesis") then "synthesis" else "signoff" in `Assoc (("origin_boundary",`String boundary)::fs) | _->v)) items) | _->v) else k,v) xs) | _->assert false in
    let results_path=directory^"/results.json" in R.save_bytes results_path (E.record_bytes results);
    set "results" (obj ["path",s results_path;"sha256",s (hash results_path)]);
    let code=if accept then (
      set "phase" (s "acceptance");save ();check_cancel ();
      let verdict=Report_service.evaluate ~context:(evidence context) results in
      R.save_bytes (directory^"/acceptance.txt") (Report_service.render ~run_dir:directory ~context:(evidence context) ~results ~source:"segmented mapped evidence" verdict);
      set "acceptance_report" (obj ["path",s (directory^"/acceptance.txt");"sha256",s (hash (directory^"/acceptance.txt"))]);
      let code=Report_service.exit_code verdict in set "acceptance" (s (if code=0 then "pass" else "fail"));code) else 0 in
    check_cancel ();set "status" (s (if code=0 then "completed" else "failed"));code
  with exn->
    let cancelled=match !signal,exn with Some n,_->Some (Segment_service.signal n)
      | None,Runner.Callback_error {kind=Runner.Cancellation n;_}->Some n | _->None in
    let status,code=match cancelled with Some n->"interrupted",128+n | None->"failed",1 in
    set "status" (s status);
    if List.assoc "acceptance" !fields=s "pass" then set "acceptance" (s "not_evaluated");
    if List.assoc "external_postcheck" !fields=s "running" then set "external_postcheck" (s status);
    let message=match exn with Failure x | Invalid_argument x->x | Runner.Callback_error e->String.concat "; " (e.message::e.secondary) | _->Printexc.to_string exn in
    set "error" (s message);code in
  set "ended_at" (s (now ()));set "exit_code" (codec (J.integer (string_of_int code)));save ();
  {directory;record=checked (Finalization_record.of_json (obj !fields))})
