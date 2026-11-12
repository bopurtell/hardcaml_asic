module J = Identity_json
module T = Execution_transport
let codec = function Ok x -> x | Error e -> failwith (J.error_message e)
let checked = function Ok x -> x | Error s -> failwith s
let s x = codec (J.string x)
let obj xs = codec (J.object_ xs)
let get j k = match j with J.Object xs -> (match List.assoc_opt k xs with Some x -> x | None -> failwith ("missing " ^ k)) | _ -> failwith "expected object"
let text = function J.String x when x <> "" && not (String.contains x '\000') -> x | _ -> failwith "nonempty string required"
let array = function J.Array xs -> xs | _ -> failwith "array required"
let read p = codec (Result_store.read_json p)
let within parent child = String.starts_with ~prefix:(parent ^ "/") child
(* Allocation authority, not circuit/runtime readiness: inspect existing ancestors
   and link targets without creating anything. Match result-store path resolution
   for not-yet-existing descendants so aliases cannot hide an overlapping root. *)
let prospective_directory path =
  if path="" || String.contains path '\000' then failwith "invalid metadata/workspace path";
  let absolute=if Filename.is_relative path then Filename.concat (Sys.getcwd ()) path else path in
  let rec walk current links = function
    | [] -> current
    | ""::rest | "."::rest -> walk current links rest
    | ".."::rest -> walk (Filename.dirname current) links rest
    | name::rest ->
      let candidate=Filename.concat current name in
      match Unix.lstat candidate with
      | stat when stat.Unix.st_kind=Unix.S_LNK ->
        if links=0 then failwith "metadata/workspace symlink resolution limit";
        let target=Unix.readlink candidate in
        walk (if Filename.is_relative target then current else "/") (links-1)
          (String.split_on_char '/' target @ rest)
      | stat ->
        if stat.Unix.st_kind<>Unix.S_DIR then failwith ("metadata/workspace directory required: " ^ candidate);
        walk candidate links rest
      | exception Unix.Unix_error (Unix.ENOENT,_,_) -> walk candidate links rest in
  walk "/" 40 (String.split_on_char '/' absolute)
let admit_metadata_roots ~workspace ~evidence_root =
  let workspace=prospective_directory workspace and evidence_root=prospective_directory evidence_root in
  if workspace="/" || evidence_root="/" || evidence_root=workspace ||
     within workspace evidence_root || within evidence_root workspace then
    failwith "evidence root must be separate from workspace"
let directory p = let p = checked (Checkpoint.absolute_path p) in
  if Unix.realpath p <> p || (Unix.lstat p).Unix.st_kind <> Unix.S_DIR then failwith ("canonical directory required: " ^ p); p
let output p = let normalized = checked (Checkpoint.absolute_path p) in
  if p <> normalized || Filename.is_relative p then failwith "canonical absolute output required";
  ignore (directory (Filename.dirname p)); p
let regular p = if (Unix.lstat p).Unix.st_kind <> Unix.S_REG then failwith ("regular file required: " ^ p)
let hash p = regular p; (checked (Checkpoint.file_record p)).sha256
let safe_child root relative =
  if relative = "" || Filename.is_relative relative = false
    || List.exists (fun x -> x="" || x="." || x="..") (String.split_on_char '/' relative)
    || String.contains relative '\000' then failwith ("invalid relative path: " ^ relative);
  let p = Filename.concat root relative in
  if not (within root (Unix.realpath p)) then failwith ("path escapes root: " ^ relative); regular p; p
let rec mkdir p = if Sys.file_exists p then ignore (directory p) else (mkdir (Filename.dirname p); Unix.mkdir p 0o700)
let copy source dest =
  regular source;
  let before = hash source in
  let ic = open_in_bin source in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let fd = Unix.openfile dest [Unix.O_WRONLY;Unix.O_CREAT;Unix.O_EXCL] 0o600 in
    let oc = Unix.out_channel_of_descr fd in
    Fun.protect ~finally:(fun () -> close_out_noerr oc) (fun () ->
      let buffer = Bytes.create 65536 in
      let rec loop () = let n = input ic buffer 0 (Bytes.length buffer) in if n > 0 then (Stdlib.output oc buffer 0 n; loop ()) in
      loop (); close_out oc));
  if hash source <> before || hash dest <> before then failwith ("file changed while copying: " ^ source)
let rec copy_tree source dest =
  ignore (directory source); Unix.mkdir dest 0o700;
  Array.iter (fun name -> let src = source ^ "/" ^ name and dst = dest ^ "/" ^ name in
    match (Unix.lstat src).Unix.st_kind with Unix.S_DIR -> copy_tree src dst | Unix.S_REG -> copy src dst
    | _ -> failwith ("bundle contains link or special file: " ^ src)) (Sys.readdir source)
let reserve p = let fd = Unix.openfile p [Unix.O_WRONLY;Unix.O_CREAT;Unix.O_EXCL] 0o600 in Unix.close fd; p
let now () = let t = Unix.gmtime (Unix.gettimeofday ()) in
  Printf.sprintf "%04d-%02d-%02dT%02d:%02d:%02dZ" (t.Unix.tm_year + 1900) (t.tm_mon + 1) t.tm_mday t.tm_hour t.tm_min t.tm_sec
type t = {service:Segment_service.t;compatibility:J.t;binding:T.binding;preflight_path:string;
  evidence_directory:string;summary_path:string;diagnostics_path:string;attempt_id:string;
   methodology:Methodology_execution_record.t option;
   timing_intent:J.t option; cancel:unit -> int option;
   capture_timing:(Runner.occurrence -> Segment_service.segment -> Segment_service.checkpoint Runner.publication -> unit) option}
type error = {cause:Runner.error;preflight_path:string option;methodology:Methodology_execution_record.t option}
let installed_resources ~executable = Filename.dirname (Filename.dirname (Unix.realpath executable)) ^ "/lib/hardcaml_asic/flow/hardcaml_asic_flow"
let prepare ~bundle ~support_tools ~pdk_root ~python ~docker ~git ~resources ~workspace ~checkpoint_store
      ~evidence_root ~environment ?(timeout=600.) ?(probe_timeout=20.) ?(cancel=fun () -> None) ?initial_checkpoint ?observation ?failure_exit_code ?methodology ?(after_methodology_snapshot=(fun () -> ())) ~plan () =
  let preflight = ref None in
  let published_methodology = ref None in
  let operation = ref None in
  let operation_report = ref None in
  let early_timing = ref None and early_snapshot = ref None in
  let timing_fields () = match !early_timing,!early_snapshot with
    | Some expectation,Some snapshot -> ["timing_intent",expectation;"timing_intent_snapshot",snapshot]
    | _ -> [] in
  let with_timing value = match value with J.Object fields ->
    obj (timing_fields () @ List.filter (fun (k,_) -> not (List.mem_assoc k (timing_fields ()))) fields)
    | _ -> value in
  let named_request r = obj (["methodology",Methodology_execution_record.to_json r;
    "plan",Execution_plan.to_json plan;"bundle",s bundle;"workspace",s workspace;
    "checkpoint_store",s checkpoint_store;"support_tools",s support_tools;"pdk_root",s pdk_root;
    "python",s python;"docker",s docker;"git",s git;"resources",s resources;
    "timeout_seconds",codec (J.float timeout);"probe_timeout_seconds",codec (J.float probe_timeout)] @ timing_fields ()) in
  let save_report path report =
    let report=with_timing report in
    (* Receipt references are operation-relative; retain the exact same report
       there as in the journal, including pending and terminal diagnostics. *)
    Option.iter (fun p -> Result_store.save_json p report) !operation_report;
    if Some path <> !operation_report then Result_store.save_json path report in
  try
    Synthesis_envelope.check_plan environment plan;
    if Synthesis_envelope.requested environment && (match methodology with Some (_, p) -> Timing_intent_named_service.plan_expected p | None -> false)
    then invalid_arg "strict synthesis envelope excludes native timing-intent producers";
    admit_metadata_roots ~workspace ~evidence_root;
    (* Inspect only the already-frozen typed plan here. Eligibility, bundle bytes,
       paths, cancellation and probes are deliberately checked AFTER the requested
       analysis has its own durable metadata snapshot. *)
    let early_native=match methodology with None -> false | Some (_,p) -> Timing_intent_named_service.plan_expected p in
    let _,evidence_report,receipt_path,receipt =
      if not early_native then Result_store.begin_preflight ~results_root:evidence_root ~bundle ()
      else (
        let allocation=Result_store.allocate (evidence_root ^ "/operations") in
        let evidence_directory=allocation.directory in
        let report=evidence_directory ^ "/preflight.json" and receipt_path=evidence_directory ^ "/operation.json" in
        operation:=Some receipt_path;operation_report:=Some report;
        preflight:=Some (match observation with None -> report | Some w -> Observation_writer.preflight_path w);
        let attempt_id=match observation with None -> Filename.basename evidence_directory | Some w -> Observation_writer.stream_id w in
        let expectation=Timing_intent_named_service.expectation ~workspace ~attempt_id in
        let path=evidence_directory ^ "/timing-intent-expectation.json" in
        Result_store.save_json path expectation;
        Unix.chmod path 0o400;
        early_timing:=Some expectation;
        early_snapshot:=Some (obj ["path",s path;"sha256",s (hash path)]);
       let receipt=with_timing (Result_store.operation_receipt allocation ~bundle) in
        Result_store.save_json receipt_path receipt;
        allocation,report,receipt_path,receipt) in
    operation := Some receipt_path;
    operation_report := Some evidence_report;
    let report_path = match observation with None -> evidence_report | Some w -> Observation_writer.preflight_path w in
    preflight := Some report_path;
    save_report report_path (Result_store.pending_report ~bundle ());
    let evidence_directory = Filename.dirname evidence_report in
    let attempt_id = match observation with None -> Filename.basename evidence_directory | Some w -> Observation_writer.stream_id w in
    let methodology = Option.map (fun (expected,p) ->
      if Option.is_some initial_checkpoint then failwith "methodology launch is fresh-only";
      let derived = checked (Methodology_execution_service.check ~bundle ~expected p) in
      if J.canonical (Execution_plan.to_json derived) <> J.canonical (Execution_plan.to_json plan)
      then failwith "methodology launch plan mismatch";
      let directory = match observation with None -> evidence_directory | Some w -> Observation_writer.directory w in
      Methodology_execution_record.publish ~directory ~attempt_id ~expected p) methodology in
    published_methodology := methodology;
    Option.iter (fun _ -> after_methodology_snapshot ()) methodology;
    let reference_fields = match methodology with None -> [] | Some r -> ["methodology",Methodology_execution_record.to_json r] in
    let request_fields = match methodology with None -> [] | Some r ->
      ["request",named_request r] in
    let receipt = match receipt with J.Object xs -> obj (xs @ reference_fields @ request_fields) | _ -> assert false in
    if methodology<>None then (
      Result_store.save_json receipt_path receipt;
       save_report report_path (obj (["ready",J.bool false;"issues",J.array [];"status",s "pending"] @ reference_fields @ request_fields)));
     let probe = Runtime_probe.create_cancellable ~cancel ~directory:evidence_directory ~environment ~timeout:probe_timeout in
    let docker = Runtime_probe.find_executable ~environment docker and git = Runtime_probe.find_executable ~environment git in
    let bundle = Unix.realpath bundle |> directory in
    let support_tools = Unix.realpath support_tools |> directory and pdk_root = Unix.realpath pdk_root |> directory in
    let workspace = output workspace in
    let checkpoint_store = checked (Checkpoint.absolute_path checkpoint_store) in
    let project = workspace ^ "/project" in
    if not (Float.is_finite timeout) || timeout <= 0. then failwith "finite positive segment timeout required";
    if not (within workspace checkpoint_store) || checkpoint_store=project || within project checkpoint_store
      || within checkpoint_store project || pdk_root=workspace || within workspace pdk_root || within pdk_root workspace
      || bundle=workspace || within workspace bundle || within bundle workspace
      || support_tools=workspace || within workspace support_tools || within support_tools workspace then failwith "invalid stage path ownership";
    (match plan with Execution_plan.Coarse _ -> failwith "stage plan required" | Execution_plan.Stages p ->
      if Option.is_some initial_checkpoint <> Option.is_some (Execution_plan.Stage_plan.source_boundary p) then failwith "plan/checkpoint handoff mismatch");
    (match initial_checkpoint with None -> (try ignore (Unix.lstat workspace); failwith "fresh workspace already exists" with Unix.Unix_error (Unix.ENOENT,_,_) -> ())
      | Some _ -> ignore (directory workspace));
    let manifest_path = bundle ^ "/manifest.json" in
     let manifest_hash = hash manifest_path and manifest = read manifest_path in
     Manifest_metadata.validate (Yojson.Safe.from_string (J.canonical manifest));
      Macro_contract.require_segmented_plan manifest plan;
      Timing_intent_named_service.validate_plan ~methodology ~initial_checkpoint ~plan manifest;
      let macro = Macro_contract.active manifest in
      if macro then (match Hardcaml_asic.Bundle.verify_manifest_identity (Yojson.Safe.from_file manifest_path) with
        | Ok () -> () | Error e -> failwith ("invalid macro bundle identity: " ^ Core.Error.to_string_hum e));
    if get manifest "schema_version" <> codec (J.integer "1") || get manifest "adapter" <> s "LibreLane" then failwith "unsupported bundle schema or adapter";
    let identity = text (get manifest "identity") in
    if String.length identity <> 64 || not (String.for_all (fun c -> c>='0' && c<='9' || c>='a' && c<='f') identity) then failwith "invalid bundle identity";
    let requested = get manifest "requested_tools" and target = get manifest "target" in
    let policy = checked (Runtime_contract.policy_from_requested requested) in
    let files = array (get manifest "files") in
    let paths = List.map (fun entry -> text (get entry "path")) files in
    if List.length (List.sort_uniq String.compare paths) <> List.length paths
      || not (List.mem "src/config.json" paths) then failwith "invalid bundle inventory";
    let technology = text (get target "technology") in
    if technology="." || technology=".." || String.contains technology '/' then failwith "invalid target technology";
    let validate_files root = List.iter (fun entry -> let relative = text (get entry "path") in
      if hash (safe_child root relative) <> text (get entry "sha256") then failwith ("changed bundle file: " ^ relative)) files in
    validate_files bundle;
    let git_env = List.filter (fun x -> not (String.starts_with ~prefix:"GIT_OPTIONAL_LOCKS=" x)) environment @ ["GIT_OPTIONAL_LOCKS=0"] in
     let git_probe = Runtime_probe.create_cancellable ~cancel ~directory:(Result_store.allocate (evidence_directory ^ "/collateral")).directory ~environment:git_env ~timeout:probe_timeout in
    List.iter (fun (root,key) -> if Runtime_probe.command git_probe ~prog:git ~arguments:["-C";root;"rev-parse";"HEAD"] <> text (get requested key)
      then failwith ("collateral revision mismatch: " ^ key)) [support_tools,"support_tools_revision";pdk_root,"pdk_revision"];
    let refs = array (get target "external_references") in
    let references = List.map (fun ref ->
      let source = text (get ref "source") in
      let repo,root = match source with "Support_tools" -> support_tools,support_tools
        | "Pdk" | "PDK" -> pdk_root,pdk_root ^ "/" ^ text (get target "technology")
        | _ -> failwith "unsupported external reference source" in
      let path = safe_child root (text (get ref "path")) in
      let actual = hash path in
      (match ref with J.Object xs -> (match List.assoc_opt "sha256" xs with None | Some J.Null -> () | Some j -> if text j <> actual then failwith ("changed external reference: " ^ path)) | _ -> assert false);
      if Runtime_probe.command git_probe ~prog:git ~arguments:["-C";repo;"status";"--porcelain";"--";path] <> "" then failwith ("modified external reference: " ^ path);
      obj ["role",get ref "role";"path",s path;"sha256",s actual]) refs in
     let macro_references = if not macro then [] else
       Macro_inputs.collect ~manifest ~bundle ~pdk_root
         ~command:(fun name arguments -> Runtime_probe.command git_probe
           ~prog:(Runtime_probe.find_executable ~environment:git_env (if name="git" then git else name)) ~arguments)
         ~check_cancel:(fun () -> match cancel () with None -> () | Some n ->
           raise (Runner.Callback_error (Runner.cancellation ~signal:(Segment_service.signal n) "macro preflight interrupted"))) in
     let resources = Unix.realpath resources |> directory in
     if macro && hash (resources ^ "/physical_runtime.py") <> Segment_service.macro_runtime_sha256 then
       failwith "installed macro runtime resource changed";
    List.iter (fun (name,digest) -> if hash (resources ^ "/" ^ name) <> digest then failwith ("installed runtime resource changed: " ^ name))
      ["librelane_bridge.py",Segment_service.bridge_sha256;"backend_protocol.py",Segment_service.protocol_sha256];
    let payload_hash = hash (resources ^ "/stage_metadata_probe.py") in
     let host,container,binding = Runtime_probe.observe ~workers:6 ?pids_limit:(Synthesis_envelope.pids_limit environment) probe ~python ~docker ~payload:(resources ^ "/stage_metadata_probe.py") ~pdk_root policy in
    if hash (resources ^ "/stage_metadata_probe.py") <> payload_hash then failwith "metadata payload changed during probes";
    let evaluation = checked (Runtime_evidence.evaluate_observations policy ~host ~container ()) in
    let issues = Runtime_evidence.issues evaluation in
    let ready = Runtime_evidence.policy_satisfied evaluation in
    let report = obj (["ready",J.bool ready;"issues",J.array issues;"waivers",J.array (Runtime_evidence.waivers evaluation);
      "metadata_probe",obj ["protocol_version",codec (J.integer "1");"kind",s "stage_runtime_metadata";
        "sha256",s payload_hash];
       "references",J.array (references @ macro_references);"requested",requested;"support_tools",s support_tools;"pdk_root",s pdk_root;"python",s python;
      "execution_binding",T.to_json binding;"runtime_contract",obj ["authority",s "bundle.requested_tools";
      "normalized_policy",Runtime_contract.to_json policy;"observations",obj ["host",host;"container",container];
        "comparisons",J.array (Runtime_evidence.comparisons evaluation)]] @ reference_fields @ request_fields) in
    save_report report_path report;
    if not ready then failwith "stage runtime policy/readiness mismatch";
    checked (Classic_contract.validate_inventory (get container "classic_inventory"));
    checked (Classic_contract.validate_settings (get container "classic_settings"));
    (match plan with Execution_plan.Stages p ->
      checked (Hardcaml_asic_flow_domain.Backends.Librelane.Mapping.validate_sequence
        ~initial_boundary:(Execution_plan.Stage_plan.source_boundary p)
        (Execution_plan.Stage_plan.sequence p)) | Coarse _ -> assert false);
    checked (Runtime_probe.verify probe binding);
    (match binding with T.Docker d when d.image_id=(Execution_plan.Stage.capability Execution_plan.Stage.Synthesis).tested_image_id -> () | _ -> failwith "unsupported physical stage image");
    let compatibility = obj ["bundle_identity",s identity;"target",target;"requested_tools",requested;"binding",T.to_json binding;
      "bridge_sha256",s Segment_service.bridge_sha256;"project",s project;"pdk_root",s pdk_root] in
    (match initial_checkpoint with
    | None -> Unix.mkdir workspace 0o700; copy_tree bundle project;
      List.iter (fun ref -> if get ref "source" = s "Support_tools" then (
        let relative = text (get ref "path") in
        let destination = project ^ "/tt/" ^ relative in mkdir (Filename.dirname destination);
        copy (safe_child support_tools relative) destination)) refs;
      Unix.mkdir (project ^ "/bridge") 0o700;
       List.iter (fun name -> copy (resources ^ "/" ^ name) (project ^ "/bridge/" ^ name)) ["librelane_bridge.py";"backend_protocol.py"];
       if macro then copy (resources ^ "/physical_runtime.py") (project ^ "/bridge/physical_runtime.py");
      Unix.mkdir (project ^ "/candidates") 0o700; mkdir checkpoint_store
    | Some checkpoint -> ignore (checked (Checkpoint.validate ~restart:true ~compatibility checkpoint)));
    validate_files bundle; validate_files project;
    List.iter2 (fun ref evidence ->
      let expected = text (get evidence "sha256") in
      if hash (text (get evidence "path")) <> expected then failwith "external reference changed during preparation";
      if get ref "source" = s "Support_tools" && hash (safe_child (project ^ "/tt") (text (get ref "path"))) <> expected
      then failwith "staged support reference differs from preflight") refs references;
    if hash manifest_path <> manifest_hash || hash (project ^ "/manifest.json") <> manifest_hash then failwith "staged/original manifest changed";
     (* Retain the checked frozen plan, not a new baseline read from mutable config.
        Only inventoried inputs and declared collateral are covered: tool-generated
        requests, reports, candidates and supplementary files remain mutable. *)
      let verify_inputs () = try
        if macro then (
          if hash manifest_path <> manifest_hash || hash (project ^ "/manifest.json") <> manifest_hash then
            failwith "macro manifest changed";
          validate_files bundle; validate_files project;
          Macro_inputs.verify ~manifest ~pdk_root (references @ macro_references);
          List.iter2 (fun ref evidence ->
            if get ref "source"=s "Support_tools" &&
               hash (safe_child (project ^ "/tt") (text (get ref "path"))) <> text (get evidence "sha256")
            then failwith "macro staged support input changed") refs references);
       Option.iter (fun r ->
         let p = Methodology_execution_record.plan r in
         ignore (checked (Methodology_execution_service.check ~bundle:project
           ~expected:(Hardcaml_asic.Methodology_plan.plan_identity p) p));
         List.iter (fun evidence ->
           if hash (text (get evidence "path")) <> text (get evidence "sha256")
           then failwith "methodology external input changed") references;
         List.iter2 (fun ref evidence ->
           if get ref "source" = s "Support_tools"
              && hash (safe_child (project ^ "/tt") (text (get ref "path"))) <> text (get evidence "sha256")
           then failwith "methodology staged support input changed") refs references) methodology;
       Ok () with exn -> Error (Printexc.to_string exn) in
     let verify_resolved path = match methodology with None -> Ok () | Some r ->
       Methodology_execution_service.check_resolved (Methodology_execution_record.plan r) path in
     let service = match Segment_service.create ~workers:6 ~cancel ~binding ~workspace ~project ~pdk_root ~checkpoint_store ~compatibility ~environment ~timeout
        ~verify_inputs ~verify_resolved
       ?initial_checkpoint ?failure_exit_code ~verify_runtime:(fun () -> Runtime_probe.verify probe binding) () with Ok t -> t | Error e -> raise (Runner.Callback_error e) in
    (match (Segment_service.callbacks service).validate plan with Ok () -> () | Error e -> raise (Runner.Callback_error e));
    let summary_path = reserve (Stage_execution_record.path ~workspace ~attempt_id) in
    let diagnostics_path = reserve (workspace ^ "/diagnostics-" ^ attempt_id ^ ".json") in
     Result_store.save_json (evidence_directory ^ "/prepared.json") (with_timing (obj (["compatibility",compatibility;"workspace",s workspace;"summary_path",s summary_path;"diagnostics_path",s diagnostics_path] @ reference_fields @ request_fields)));
    Result_store.save_json receipt_path (Result_store.finish_receipt receipt ~status:Ready ~exit_code:0 ~ended_at:(now ()) ());
      let timing_intent = !early_timing in
      if Timing_intent_named_service.expected manifest && timing_intent=None then
        failwith "named timing expectation was not retained before preparation";
     let capture_timing = match methodology,timing_intent with Some methodology,Some expectation ->
       Some (fun occurrence segment publication -> Timing_intent_named_service.capture ~timeout ~workspace ~resources
         ~methodology ~plan ~expectation ~occurrence ~segment ~publication ~binding ~pdk_root ~environment ~cancel
         ~verify_runtime:(fun () -> Runtime_probe.verify probe binding) ~verify_inputs ())
       | _ -> None in
     Ok {service;compatibility;binding;preflight_path=report_path;evidence_directory;summary_path;diagnostics_path;attempt_id;methodology;
       timing_intent;capture_timing;cancel}
  with exn ->
    let cause = match exn with Runner.Callback_error e -> e | _ -> Runner.failure (Printexc.to_string exn) in
    (match !preflight with None -> () | Some p ->
      (try let old = read p in
         let detail = obj (["ready",J.bool false;"issues",J.array [s cause.message];"previous_report",old;"bundle",s bundle]
           @ (match !early_timing with None -> [] | Some _ ->
             ["status",s (match cause.kind with Runner.Cancellation _ -> "interrupted" | _ -> "failed");
              "exit_code",codec (J.integer (string_of_int cause.exit_code));
              "signal",(match cause.kind with Runner.Cancellation n -> codec (J.integer (string_of_int n)) | _ -> J.null)])
           @ (match !published_methodology with None -> [] | Some r ->
             ["methodology",Methodology_execution_record.to_json r;"request",named_request r])) in
        save_report p detail;
        let receipt_path = Option.get !operation in
        if Sys.file_exists receipt_path then Result_store.save_json receipt_path
          (Result_store.finish_receipt (read receipt_path) ~status:(match cause.kind with Runner.Cancellation _ -> Interrupted | _ -> Failed)
            ~exit_code:cause.exit_code ~ended_at:(now ()) ~error:cause.message ())
       with _ -> ()));
    Error {cause;preflight_path= !preflight;methodology= !published_methodology}
let execute ?observation t plan =
  let persist execution =
    Result_store.save_json
      t.summary_path
      (Runner.to_json ?methodology:t.methodology ~run_json:(fun () -> J.null) execution);
    Result_store.save_json
      t.diagnostics_path
      (Segment_service.diagnostics_json t.service execution);
    let raw =
      Runner.to_json ?methodology:t.methodology ~run_json:(fun () -> J.null) execution
    in
    let get j k =
      match j with
      | J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null
      | _ -> J.null
    in
    let snapshot = t.summary_path ^ ".timing-source" in
    let finished =
      match get raw "occurrences" with
      | J.Array xs ->
        List.exists
          (fun o ->
            get o "action_key" = s "implementation.timing"
            && get o "status" = s "completed")
          xs
      | _ -> false
    in
    if finished && not (Sys.file_exists snapshot)
    then (
      (* A separate immutable execution prefix anchors the analysis while the mutable
         summary advances through signoff. Never retarget its hash. *)
      Unix.link t.summary_path snapshot;
      try
        Timing_analysis.publish
          ~execution:snapshot
          ~transport_records:(Segment_service.transport_records t.service)
      with
      | exn ->
        prerr_endline ("timing analysis capture unavailable: " ^ Printexc.to_string exn))
  in
   let callbacks = Segment_service.callbacks t.service in
   let association = ref None in
   let publish action context segment =
     match callbacks.publish action context segment with
     | Error e -> Error e
     | Ok publication -> association := Some (context.Runner.occurrence_id,segment,publication); Ok publication in
   let lifecycle event action occurrence publication =
     (* Runner has retained the promotion before this callback. Observation of
        Published is independent of the required analysis completing. *)
     Option.iter (fun w -> Observation_writer.lifecycle w event action occurrence publication) observation;
     match event,publication,t.capture_timing with
     | Runner.Published,Some publication,Some capture when action.Execution_plan.Action.key="implementation.synthesis" ->
       (match !association with
        | Some (id,segment,p) when id=occurrence.Runner.occurrence_id && p=publication -> capture occurrence segment publication
        | _ -> raise (Runner.Callback_error (Runner.failure "named timing: missing originating publication association")))
     | _ -> () in
   let cancel () = Option.map (fun n -> Runner.cancellation ~signal:(Segment_service.signal n) "stage execution interrupted") (t.cancel ()) in
   let result =
     Runner.execute ?initial:(Segment_service.initial t.service) ?timing_intent:t.timing_intent ~cancel
       ~persist
       ~lifecycle
       ~attempt_id:t.attempt_id
       plan
       {callbacks with publish}
  in
  (* Supplementary capture cannot replace the primary implementation outcome. *)
  (try
     let snapshot = t.summary_path ^ ".timing-source" in
     let execution = if Sys.file_exists snapshot then snapshot else t.summary_path in
     if not (Sys.file_exists (Timing_analysis.path execution))
     then
       Timing_analysis.publish
         ~execution
         ~transport_records:(Segment_service.transport_records t.service)
   with
   | exn ->
     (try
        prerr_endline
          ("timing analysis observation unavailable: " ^ Printexc.to_string exn)
      with
      | _ -> ()));
  result
;;
