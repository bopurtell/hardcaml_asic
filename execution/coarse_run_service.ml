[@@@alert "-unsafe_multidomain"]
module J = Identity_json
module R = Result_store
module T = Execution_transport
module C = Run_record
let codec = function Ok x -> x | Error e -> failwith (J.error_message e)
let checked = function Ok x -> x | Error e -> failwith e
let s x = codec (J.string x)
let obj x = codec (J.object_ x)
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String x -> x | _ -> failwith "expected string"
let array = function J.Array xs -> xs | _ -> failwith "expected array"
let resolve p =
  let rec loop depth p =
    if depth > 40 then failwith ("symlink loop: " ^ p);
    let p = if Filename.is_relative p then Filename.concat (Sys.getcwd ()) p else p in
    let parent = Filename.dirname p in
    if parent=p then p else
    let parent = loop depth parent in
    match Filename.basename p with
    | "." -> parent | ".." -> Filename.dirname parent
    | name -> let path = Filename.concat parent name in
      try if (Unix.lstat path).Unix.st_kind=Unix.S_LNK then (
        let target = Unix.readlink path in
        loop (depth+1) (if Filename.is_relative target then Filename.concat parent target else target)) else path
      with Unix.Unix_error ((Unix.ENOENT | Unix.ENOTDIR),_,_) -> path in loop 0 p
let safe_child root relative =
  if relative="" || not (Filename.is_relative relative) then failwith ("invalid relative path: " ^ relative);
  let root = resolve root in let p = resolve (Filename.concat root relative) in
  if not (String.starts_with ~prefix:(root ^ "/") p) then failwith ("path escapes its root: " ^ relative); p
let hash p = (checked (Checkpoint.file_record p)).sha256
let rec mkdir p = if not (Sys.file_exists p) then (mkdir (Filename.dirname p); Unix.mkdir p 0o777)
let copy src dst =
  let stream = open_in_bin src in
  Fun.protect ~finally:(fun () -> close_in_noerr stream) (fun () ->
    let output = open_out_bin dst in
    Fun.protect ~finally:(fun () -> close_out_noerr output) (fun () ->
      let b = Bytes.create 65536 in let rec loop () = let n = input stream b 0 (Bytes.length b) in
        if n > 0 then (output_bytes output (Bytes.sub b 0 n); loop ()) in loop ()));
  let st = Unix.stat src in Unix.chmod dst st.Unix.st_perm; Unix.utimes dst st.Unix.st_atime st.Unix.st_mtime
let rec copy_tree src dst =
  Unix.mkdir dst 0o777;
  Array.iter (fun name -> let a = Filename.concat src name and b = Filename.concat dst name in
    match (Unix.stat a).Unix.st_kind with Unix.S_DIR -> copy_tree a b | Unix.S_REG -> copy a b
    | _ -> failwith ("unsupported bundle entry: " ^ a)) (Sys.readdir src)
let now () = let time = Unix.gettimeofday () in let sec = floor time in let t = Unix.gmtime sec in
  Printf.sprintf "%04d-%02d-%02dT%02d:%02d:%02d.%06d+00:00" (t.tm_year+1900) (t.tm_mon+1) t.tm_mday t.tm_hour t.tm_min t.tm_sec (int_of_float ((time-.sec)*.1000000.))
let message = function Failure x | Invalid_argument x -> x | Runner.Callback_error e -> String.concat "; " (e.message::e.secondary) | e -> Printexc.to_string e
exception Retained_error of Run_record.outcome * Runner.error
let run ~bundle ~support_tools ~pdk_root ~python ~resources ~environment ~runs ~plan
    ?(native=false) ?(allow_python_mismatch=false) () =
  let stage = Execution_plan.Coarse.endpoint plan in
  if native && stage=Execution_plan.Coarse.Full then invalid_arg "native execution supports synthesis only";
  let signal = ref None in
  let handler n = if !signal=None then signal := Some n in
  let old_int = Sys.signal Sys.sigint (Sys.Signal_handle handler) in
  let old_term = Sys.signal Sys.sigterm (Sys.Signal_handle handler) in
  let cancel () = !signal in
  let check_cancel () = match !signal with None -> () | Some n ->
    raise (Runner.Callback_error (Runner.cancellation ~signal:(if n=Sys.sigint then 2 else 15) "run interrupted")) in
  Fun.protect ~finally:(fun () -> Sys.set_signal Sys.sigint old_int; Sys.set_signal Sys.sigterm old_term) (fun () ->
  let bundle = resolve bundle and support_tools = resolve support_tools and pdk_root = resolve pdk_root in
  let python = checked (Checkpoint.absolute_path python) in
  let manifest_path = bundle ^ "/manifest.json" in
  let manifest = codec (R.read_json manifest_path) in
  Manifest_metadata.validate (Yojson.Safe.from_string (J.canonical manifest));
   if stage=Execution_plan.Coarse.Full then Macro_contract.require_full manifest;
   let macro = Macro_contract.active manifest in
    let pt7 = Timing_intent_flow.expected (Yojson.Safe.from_string (J.canonical manifest)) in
    let qualification=get manifest "timing_physical_qualification"<>J.null in
    if qualification && not pt7 then invalid_arg "physical timing qualification requires the native-v2 producer";
    if qualification then Timing_intent_evidence.validate_qualification (Yojson.Safe.from_string (J.canonical manifest));
    if qualification && get (get manifest "target") "technology"<>s "ihp-sg13cmos5l" then invalid_arg "physical timing qualification requires CMOS5L";
    if qualification && stage=Execution_plan.Coarse.Full && get manifest "operation"<>s "Hardening" then
      invalid_arg "physical timing full execution requires a freshly emitted Hardening bundle";
   if pt7 then (
     let native=get manifest "timing_native_coverage" in
     if get native "schema"<>codec (J.integer "2") || get native "profile"<>s "opensta-2.7-native-state-v2"
     then invalid_arg "unsupported native producer profile; emit/capture native profile 2 before synthesis");
    if pt7 && not qualification && stage<>Execution_plan.Coarse.Synthesis then invalid_arg "PT7 native producer ordinary integration supports coarse synthesis only";
   if pt7 && native then invalid_arg "PT7 native producer requires the qualified immutable Docker runtime";
   if pt7 && macro then invalid_arg "PT7 linked analysis currently supports macro-free coarse synthesis only";
   let bounded = macro || pt7 in
  let identity = get manifest "identity" in ignore (text identity);
  let manifest_sha256 = hash manifest_path in
  check_cancel ();
  let a = R.allocate runs in
    let record = C.create a ~bundle ~build_identity:identity ~manifest_sha256 ~stage in
    if pt7 then C.set record "timing_intent_expected" (J.bool true);
    if qualification then C.set record "timing_physical_expected" (J.bool true);
    if bounded then C.set record "resource_limits" (obj ["tool_workers",codec (J.integer "6");
     "container_cpus",codec (J.integer "6");"sta_workers",codec (J.integer "1")]);
  let path = a.directory ^ "/run.json" and preflight_path = a.directory ^ "/preflight.json" in
  let save () = R.save_json path (C.to_json record) in
  save ();
    let status,exit_code,error,interrupted_by = try
      R.save_json preflight_path (R.pending_report ~bundle ());
      let report = try check_cancel (); Preflight_service.probe ~bundle ~support_tools ~pdk_root ~python
        ~resources ~environment ~directory:a.directory ~native ~allow_python_mismatch ~cancel ()
        with e -> let st,by = match e with
          | Runner.Callback_error {kind=Runner.Cancellation n;_} ->
            signal := Some (if n=2 then Sys.sigint else Sys.sigterm);
            R.Interrupted,Some (if n=2 then "SIGINT" else "SIGTERM")
          | _ -> R.Failed,None in
          R.diagnostic ~bundle ~status:st ?interrupted_by:by ~issue:(message e) () in
      R.save_json preflight_path report; C.set record "environment" report;
      check_cancel ();
      if get report "ready" <> J.bool true then failwith (String.concat "; " (List.map text (array (get report "issues"))));
       let binding = checked (T.of_json (get report "execution_binding")) in
       if pt7 then (match binding with
         | T.Docker d when d.image_id=Hardcaml_asic_flow_domain.Backends.Librelane.Mapping.tested_image_id -> ()
         | _ -> failwith "PT7 native producer requires the qualified immutable image");
      C.set record "execution_binding" (T.to_json binding);
       let project = a.directory ^ "/project" in copy_tree bundle project;
       if qualification then List.iter (fun name -> copy (resources^"/"^name) (project^"/"^name))
         ["timing_physical_runtime.py";"timing_intent_runtime.py"];
       if qualification then C.set record "timing_runtime_helpers" (obj (List.map (fun name -> name,s (hash (project^"/"^name)))
         ["timing_physical_runtime.py";"timing_intent_runtime.py"]));
      List.iter (fun ref -> if get ref "source"=s "Support_tools" then (
        let relative = text (get ref "path") in let destination = safe_child (project ^ "/tt") relative in
        mkdir (Filename.dirname destination); copy (safe_child support_tools relative) destination))
        (array (get (get manifest "target") "external_references"));
      mkdir (project ^ "/runs/asic");
      if hash (project ^ "/manifest.json") <> manifest_sha256 then failwith "staged manifest differs from original bundle";
       let technology = text (get (get manifest "target") "technology") in
       let physical_full=Macro_contract.physical manifest && stage=Execution_plan.Coarse.Full in
       if physical_full then (
         copy (resources ^ "/physical_runtime.py") (project ^ "/physical-runtime.py");
         copy (resources ^ "/macro_flow.py") (project ^ "/macro-flow.py");
         C.set record "physical_checker_sha256" (s (hash (project ^ "/physical-runtime.py")));
         C.set record "macro_flow_sha256" (s (hash (project ^ "/macro-flow.py"))));
      let env = T.clean_environment environment in
      let find key = Option.value (List.find_opt (String.starts_with ~prefix:(key ^ "=")) env) ~default:(key ^ "=") in
      let path_value = find "PATH" in
      let env = List.filter (fun x -> not (List.exists (fun key -> String.starts_with ~prefix:(key ^ "=") x)
        ["PATH";"PDK";"PDK_ROOT";"_OPENLANE_MAX_CORES";"OPENROAD_THREADS";"OMP_NUM_THREADS"])) env @
         ["PATH=" ^ Filename.dirname python ^ ":" ^ String.sub path_value 5 (String.length path_value-5);"PDK_ROOT=" ^ pdk_root;"PDK=" ^ technology;
           "_OPENLANE_MAX_CORES=" ^ (if bounded then "6" else "20");
           "OPENROAD_THREADS=" ^ (if bounded then "6" else "20");
           "OMP_NUM_THREADS=" ^ (if bounded then "6" else "20")] in
      C.status record C.Running; save (); check_cancel ();
       let arguments = (if physical_full then [project ^ "/macro-flow.py"] else if qualification then [project^"/timing_physical_runtime.py";"implementation"] else ["-m";"librelane"]) @
         ["--pdk-root";pdk_root;"--pdk";technology;"--manual-pdk";
        "--run-tag";"asic";"--force-run-dir";"runs/asic"] @
        (if stage=Execution_plan.Coarse.Synthesis then ["--to";"Yosys.Synthesis"] else []) @
          (if bounded then ["--jobs";"6";"-c";"OPENROAD_THREADS=6";"-c";"STA_THREADS=1";
           "-c";"KLAYOUT_DRC_THREADS=6";"-c";"KLAYOUT_XOR_THREADS=6"] else []) @
         (if Macro_contract.physical manifest then ["-c";"MAX_SLEW_VIOLATION_CORNERS=*";
           "-c";"MAX_CAP_VIOLATION_CORNERS=*";"-c";"ERROR_ON_MAGIC_DRC=false"] else []) @ ["src/config.json"] in
      let log = open_out_gen [Open_wronly;Open_creat;Open_append;Open_binary] 0o600 (a.directory ^ "/execution.log") in
      let result = Fun.protect ~finally:(fun () -> close_out_noerr log) (fun () ->
        let output = Process.Descriptor (Unix.descr_of_out_channel log) in
        let invocation = match binding with
          | T.Native n -> T.native_invocation n ~arguments ~cwd:project ~environment:env ~stdout:output ~stderr:output
          | T.Docker d ->
            let directory = a.directory ^ "/runtime-verification" in Unix.mkdir directory 0o700;
            let probe = Runtime_probe.create_cancellable ~cancel ~directory ~environment ~timeout:20. in
            checked (Runtime_probe.verify probe binding);
            C.set record "execution_engine" (get (T.to_json binding) "engine_identity");
             T.docker_invocation d ~project ~pdk_root ~pdk:technology ~workers:(if bounded then 6 else 20) ~interpreter:true ~arguments ~cwd:project ~environment:env ~stdout:output ~stderr:output () in
        Option.iter (fun (_,name) -> C.set record "container_name" (s name)) (T.container invocation);
        C.set record "execution_mode" (s (if native then "native" else "dockerized"));
        let spec = T.process_specification invocation in C.command record spec.argv; save ();
        output_string log ("\n$ " ^ String.concat " " (List.map Filename.quote spec.argv) ^ "\n"); flush log;
         T.run ~cancel invocation) in
       if pt7 then (
         let outcome,code=match result.process.outcome with
           | Process_supervisor.Exited n -> "exited",codec (J.integer (string_of_int n))
           | Process_supervisor.Cancelled _ -> "cancelled",J.null
           | Process_supervisor.Signaled _ -> "signaled",J.null
           | Process_supervisor.Timed_out -> "timed_out",J.null
           | Process_supervisor.Failed _ -> "failed",J.null in
         C.set record (if qualification then "implementation_process" else "synthesis_process") (obj ["outcome",s outcome;"code",code;
           "argv",(match get (C.to_json record) "commands" with J.Array [command] -> command | _ -> failwith "PT7 synthesis command association missing");
           "succeeded",J.bool (T.succeeded result)]); save ());
      Option.iter (fun (cleanup : T.cleanup) -> C.set record "container_cleanup" (J.array (List.map (fun (attempt : T.removal) ->
        obj (["status",s (if Process_supervisor.succeeded attempt.result then "removed-or-absent" else "failed")] @
        if Process_supervisor.succeeded attempt.result then [] else ["error",s attempt.diagnostic])) cleanup.attempts))) result.cleanup;
      (match result.process.outcome with
        | Process_supervisor.Cancelled n -> signal := Some n
        | _ -> ()); check_cancel ();
      Option.iter (fun (cleanup : T.cleanup) -> List.iter (fun (attempt : T.removal) ->
        match attempt.result.outcome with Process_supervisor.Cancelled n -> if !signal=None then signal := Some n | _ -> ()) cleanup.attempts) result.cleanup;
      check_cancel ();
       if not (T.succeeded result) then (
        match result.process.outcome with
        | Process_supervisor.Failed error -> failwith error
        | _ -> (match result.cleanup with Some {error=Some e;_} -> failwith ("owned container cleanup failed: " ^ e)
           | _ -> failwith "LibreLane failed; see execution.log and project/runs/asic"));
       if Macro_contract.physical manifest && stage=Execution_plan.Coarse.Full then (
          let helper=project ^ "/physical-runtime.py" in
          if get (C.to_json record) "physical_checker_sha256"<>s (hash helper) ||
             get (C.to_json record) "macro_flow_sha256"<>s (hash (project ^ "/macro-flow.py"))
          then failwith "physical runtime adapter changed during execution";
         let output=Process.File (a.directory ^ "/physical-checks.log") in
         let arguments=[helper;"check";project;pdk_root] in
         let invocation=match binding with
           | T.Docker d -> T.docker_invocation d ~project ~pdk_root ~workers:6 ~interpreter:true
               ~arguments ~cwd:project ~environment:env ~stdout:output ~stderr:output ()
           | T.Native _ -> failwith "native full execution unsupported" in
         C.command record (T.process_specification invocation).argv; save ();
         if not (T.succeeded (T.run ~cancel invocation)) then failwith "physical evidence validation failed; see physical-checks.log";
         copy (project ^ "/runs/asic/physical-checks.json") (a.directory ^ "/physical-checks.json");
         C.set record "physical_checks_sha256" (s (hash (a.directory ^ "/physical-checks.json"))));
       let verify_inputs () =
        if qualification then List.iter (fun name ->
          if get (get (C.to_json record) "timing_runtime_helpers") name<>s (hash (project^"/"^name)) ||
             hash (resources^"/"^name)<>hash (project^"/"^name) then failwith "timing runtime helper changed during implementation")
          ["timing_physical_runtime.py";"timing_intent_runtime.py"];
       if hash (project ^ "/manifest.json") <> manifest_sha256 || hash manifest_path <> manifest_sha256 then failwith "build manifest changed during execution";
       if bounded then (
        List.iter (fun entry -> check_cancel ();
          let relative = text (get entry "path") and expected = text (get entry "sha256") in
          if hash (safe_child bundle relative) <> expected || hash (safe_child project relative) <> expected
          then failwith ("macro bundle input changed during execution: " ^ relative)) (array (get manifest "files"));
         check_cancel ();
         Macro_inputs.verify ~phase:"during execution" ~manifest ~pdk_root (array (get report "references"))) in
       verify_inputs ();
       if pt7 then (
         let probe_directory=a.directory^"/timing-runtime-verification" in Unix.mkdir probe_directory 0o700;
         let probe=Runtime_probe.create_cancellable ~cancel ~directory:probe_directory ~environment ~timeout:20. in
         Timing_intent_service.capture ~run_dir:a.directory ~context:(C.to_json record) ~binding ~pdk_root
           ~resources ~environment:env ~cancel ~verify_runtime:(fun () -> Runtime_probe.verify probe binding) ();
         check_cancel (); verify_inputs ());
       C.set record "completed_stage" (s (if stage=Execution_plan.Coarse.Synthesis then "synthesis" else "full"));
      C.Completed,0,None,None
    with e ->
      let interruption name = String.concat "; " (("interrupted by " ^ name)::
        (match e with Runner.Callback_error e -> e.secondary | _ -> [])) in
      match !signal,e with
      | Some n,_ -> let code,name = if n=Sys.sigint then 130,"SIGINT" else 143,"SIGTERM" in
        C.Interrupted,code,Some (interruption name),Some name
      | None,Runner.Callback_error {kind=Runner.Cancellation n;_} ->
        let name = if n=2 then "SIGINT" else "SIGTERM" in C.Interrupted,128+n,Some (interruption name),Some name
      | _ -> C.Failed,1,Some (message e),None in
    C.finish record status ~ended_at:(now ()) ?error ?interrupted_by ();
    (try save () with e ->
      let publication=message e in
      let code=if exit_code=0 then 2 else exit_code in
      let diagnostic=Option.value error ~default:publication in
      let secondary=if error=None then [] else [publication] in
      let failure=if status=C.Interrupted then Runner.cancellation ~signal:(code-128) ~secondary diagnostic
        else Runner.failure ~exit_code:code ~secondary diagnostic in
      raise (Retained_error (C.outcome a ~status:(if status=C.Interrupted then C.Interrupted else C.Failed) ~exit_code:code,failure)));
    C.outcome a ~status ~exit_code)
