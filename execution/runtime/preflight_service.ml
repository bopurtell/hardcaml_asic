(* Like Process_supervisor, this synchronous boundary owns main-thread handlers. *)
[@@@alert "-unsafe_multidomain"]
module J = Identity_json
module R = Result_store
module P = Runtime_probe
let codec = function Ok x -> x | Error e -> failwith (J.error_message e)
let checked = function Ok x -> x | Error e -> failwith e
let s x = codec (J.string x)
let obj xs = codec (J.object_ xs)
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String x -> x | _ -> failwith "expected string"
let array = function J.Array xs -> xs | _ -> failwith "expected array"
let rec python_repr = function
  | J.Null -> "None" | J.Bool true -> "True" | J.Bool false -> "False"
  | J.String x ->
    let quote = if String.contains x '\'' && not (String.contains x '"') then '"' else '\'' in
    let b = Buffer.create (String.length x + 2) in
    Buffer.add_char b quote;
    String.iter (fun c -> match c with
      | '\n' -> Buffer.add_string b "\\n" | '\r' -> Buffer.add_string b "\\r"
      | '\t' -> Buffer.add_string b "\\t" | '\\' -> Buffer.add_string b "\\\\"
      | c when c = quote -> Buffer.add_char b '\\'; Buffer.add_char b c
      | c when Char.code c < 32 || Char.code c = 127 -> Buffer.add_string b (Printf.sprintf "\\x%02x" (Char.code c))
      | c -> Buffer.add_char b c) x;
    Buffer.add_char b quote; Buffer.contents b
  | J.Array xs -> "[" ^ String.concat ", " (List.map python_repr xs) ^ "]"
  | J.Object xs -> "{" ^ String.concat ", " (List.map (fun (k,v) -> python_repr (s k) ^ ": " ^ python_repr v) xs) ^ "}"
  | x -> J.canonical x
let lexical p = checked (Checkpoint.absolute_path p)
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
        loop (depth+1) (if Filename.is_relative target then Filename.concat parent target else target))
        else path
      with Unix.Unix_error ((Unix.ENOENT | Unix.ENOTDIR),_,_) -> path in
  loop 0 p
let safe_child root relative =
  if relative="" || not (Filename.is_relative relative) then failwith ("invalid relative path: " ^ python_repr (s relative));
  let root = resolve root in
  let path = resolve (Filename.concat root relative) |> lexical in
  if path <> root && not (String.starts_with ~prefix:(root ^ "/") path) then failwith ("path escapes its root: " ^ relative);
  path
let is_file p = try (Unix.stat p).Unix.st_kind = Unix.S_REG with _ -> false
let hash p = (checked (Checkpoint.file_record p)).sha256
let message = function Failure x | Invalid_argument x -> x | Runner.Callback_error e -> String.concat "; " (e.message::e.secondary) | e -> Printexc.to_string e
let interrupted = function Runner.Callback_error {kind=Runner.Cancellation _;_} -> true | _ -> false
let check_cancel cancel = match cancel () with None -> () | Some signal ->
  raise (Runner.Callback_error (Runner.cancellation ~signal:(if signal=Sys.sigint then 2 else 15) "preflight interrupted"))
let probe_with_cancel ~cancel ~bundle ~support_tools ~pdk_root ~python ~resources ~environment ~directory
    ?(native=false) ?(allow_python_mismatch=false) () =
  if Synthesis_envelope.requested environment then
    invalid_arg "strict synthesis task envelope requires segmented stages, not coarse preflight/run";
  let manifest = codec (R.read_json (bundle ^ "/manifest.json")) in
  Manifest_metadata.validate (Yojson.Safe.from_string (J.canonical manifest));
  let requested = get manifest "requested_tools" in
  let issues = ref [] and waivers = ref [] and references = ref [] and actual = ref [] in
  let add x = issues := !issues @ [s x] in
  let attempt f = check_cancel cancel; try f () with e -> if interrupted e then raise e else add (message e) in
  let policy = Runtime_contract.policy_from_requested requested in
  let host = ref (obj []) and container = ref (obj []) and comparisons = ref [] and binding = ref [] in
  let report () = obj (["ready",J.bool (!issues=[]);"issues",J.array !issues;"waivers",J.array !waivers;
    "references",J.array !references;"requested",requested;"actual",obj !actual;
    "support_tools",s (resolve support_tools);"pdk_root",s (resolve pdk_root);"python",s (lexical python);
    "runtime_contract",obj ["authority",s "bundle.requested_tools";
      "normalized_policy",(match policy with Ok p -> Runtime_contract.to_json p | Error _ -> J.null);
      "execution_mode",s (if native then "native" else "dockerized");
      "observations",obj (match policy with Error _ -> [] | Ok _ -> ["host",!host] @ if native then [] else ["container",!container]);
      "comparisons",J.array !comparisons]] @ !binding) in
  match policy with Error e -> add ("runtime policy: " ^ e); report () | Ok policy ->
  if get manifest "schema_version" <> codec (J.integer "1") || get manifest "adapter" <> s "LibreLane" then add "unsupported bundle schema or adapter";
  List.iter (fun entry -> attempt (fun () -> let relative = text (get entry "path") in
    let path = safe_child bundle relative in
    if not (is_file path) || hash path <> text (get entry "sha256") then add ("missing or changed bundle file: " ^ relative))) (array (get manifest "files"));
   let environment = List.filter (fun x -> not (String.starts_with ~prefix:"GIT_OPTIONAL_LOCKS=" x)) environment @ ["GIT_OPTIONAL_LOCKS=0"] in
   let environment = if Macro_contract.active manifest then
     List.filter (fun x -> not (List.exists (fun key -> String.starts_with ~prefix:(key ^ "=") x)
       ["OMP_NUM_THREADS";"OPENROAD_THREADS";"_OPENLANE_MAX_CORES"])) environment @
     ["OMP_NUM_THREADS=6";"OPENROAD_THREADS=6";"_OPENLANE_MAX_CORES=6"] else environment in
  let probe = P.create_cancellable ~cancel ~directory ~environment ~timeout:20. in
  let command name arguments = P.command probe ~prog:(P.find_executable ~environment name) ~arguments in
  List.iter (fun (root,key,label) -> attempt (fun () ->
    let observed = command "git" ["-C";root;"rev-parse";"HEAD"] in
    let expected = text (get requested key) in
    if observed <> expected then add (label ^ " revision " ^ observed ^ "; requested " ^ expected)))
    [support_tools,"support_tools_revision","support tools";pdk_root,"pdk_revision","PDK"];
  let target = get manifest "target" in
  attempt (fun () ->
    if Macro_contract.active manifest || is_file (safe_child bundle "src/config.json") then (
    let config = codec (R.read_json (safe_child bundle "src/config.json")) in
    let collateral = Macro_contract.validate manifest config in
    List.iter (fun (role, reference) ->
      check_cancel cancel;
      let relative = String.sub reference 9 (String.length reference - 9) in
      (* CMOS5L SRAM exposure links to shared SG13G2 collateral. Constrain the
         resolved file to the pinned checkout, not the technology subdirectory. *)
      let path = safe_child pdk_root (text (get target "technology") ^ "/" ^ relative) in
      if not (is_file path) then add ("missing macro " ^ role ^ ": " ^ path)
      else (
        let digest = hash path in
        references := !references @ [obj ["role",s ("macro:" ^ role);"reference",s reference;"path",s path;"sha256",s digest]];
        (* A modified technology-exposure symlink must not pass merely because
           its new target happens to be a clean file elsewhere in the checkout. *)
        let rec links base = function
          | [] -> []
          | component::rest ->
            let next = Filename.concat base component in
            (if (Unix.lstat next).Unix.st_kind=Unix.S_LNK then [next] else []) @ links next rest in
        let aliases = links pdk_root (String.split_on_char '/' (text (get target "technology") ^ "/" ^ relative)) in
        if command "git" (["-C";pdk_root;"status";"--porcelain";"--";path] @ aliases) <> ""
        then add ("modified macro " ^ role ^ ": " ^ path))) collateral));
   if Macro_contract.physical manifest then attempt (fun () ->
     let plan = get (get manifest "macro_contract") "physical_plan" in
     if codec (R.read_json (safe_child bundle "physical/macro-plan.json")) <> plan
     then failwith "physical plan file differs from manifest";
     List.iter (fun (relative,contents) ->
       if hash (safe_child bundle relative)<>Identity_json.sha256_bytes contents
       then failwith ("physical input differs from canonical generation: " ^ relative)) (Macro_contract.physical_files manifest);
     (* Include transitive deck includes and sibling default JSON. All resolved
        paths remain inside the selected pinned PDK checkout. *)
     let rec tree ancestors path =
       check_cancel cancel;
       let path=resolve path in
       if not (String.starts_with ~prefix:(resolve pdk_root ^ "/") path) then failwith "checking collateral escapes pinned PDK";
       if List.mem path ancestors then failwith "checking collateral symlink cycle";
       if (Unix.stat path).Unix.st_kind=Unix.S_DIR then
         Array.to_list (Sys.readdir path) |> List.sort String.compare |> List.concat_map (fun name -> tree (path::ancestors) (path ^ "/" ^ name))
       else if is_file path then [path] else failwith "unsupported checking collateral" in
     let paths=List.concat_map (fun relative -> tree [] (safe_child pdk_root relative))
       ["ihp-sg13cmos5l/libs.tech/klayout";"ihp-sg13g2/libs.tech/klayout/tech/drc";
        "ihp-sg13cmos5l/libs.tech/magic";"ihp-sg13cmos5l/libs.tech/netgen"] |> List.sort_uniq String.compare in
     let paths=List.sort_uniq String.compare (paths @ List.map (safe_child pdk_root)
       ["ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v";
        "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v"]) in
     if command "git" (["-C";pdk_root;"status";"--porcelain";"--"] @ paths) <> ""
     then failwith "modified pinned checking collateral";
     List.iter (fun path -> references := !references @ [obj ["role",s "physical:checking_collateral";"path",s path;"sha256",s (hash path)]]) paths);
   if get manifest "timing_physical_qualification"<>J.null && not (Macro_contract.physical manifest) then attempt (fun () ->
     Timing_intent_evidence.validate_qualification (Yojson.Safe.from_string (J.canonical manifest));
     let paths=List.map (safe_child pdk_root)
       ["ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v";
        "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v"] in
     if command "git" (["-C";pdk_root;"status";"--porcelain";"--"] @ paths)<>"" then
       failwith "modified pinned timing functional-model source";
     List.iter (fun path -> if not (is_file path) then failwith ("missing timing functional-model source: "^path);
       references:= !references @ [obj ["role",s "timing:functional_model_source";"path",s path;"sha256",s (hash path)]]) paths);
   List.iter (fun reference -> attempt (fun () ->
    let repo,root = if get reference "source" = s "Support_tools" then support_tools,support_tools
      else pdk_root,Filename.concat pdk_root (text (get target "technology")) in
    let path = safe_child root (text (get reference "path")) and role = text (get reference "role") in
    if not (is_file path) then add ("missing " ^ role ^ ": " ^ path) else (
      let digest = hash path in
      references := !references @ [obj ["role",s role;"path",s path;"sha256",s digest]];
      if get reference "sha256" <> J.null && get reference "sha256" <> s "" && get reference "sha256" <> s digest then add ("changed " ^ role ^ ": " ^ path);
      if command "git" ["-C";repo;"status";"--porcelain";"--";path] <> "" then add ("modified " ^ role ^ ": " ^ path))))
    (array (get target "external_references"));
  let payload = resources ^ "/stage_metadata_probe.py" in
  attempt (fun () -> host := P.observe_host probe ~python ~payload;
    actual := ["python",get !host "python_version";"librelane",get !host "librelane_version"]);
  if not native then attempt (fun () ->
     let pt7=Timing_intent_flow.expected (Yojson.Safe.from_string (J.canonical manifest)) in
     container := P.observe_container ~workers:(if Macro_contract.active manifest || pt7 then 6 else 20) probe ~docker:(P.find_executable ~environment "docker") ~payload policy;
    actual := !actual @ ["docker_server",get (get !container "engine_identity") "server_version";
      "librelane_image",get (get !container "image_identity") "image_id";
      "container_python",get !container "python_version";"container_librelane",get !container "librelane_version";
      "container_requires_python",get !container "requires_python"]);
  let evaluation = checked (Runtime_evidence.evaluate_observations policy ~host:!host ~container:!container ~native ~allow_python_mismatch ()) in
  comparisons := Runtime_evidence.comparisons evaluation;
  let row r = text (get r "role") ^ " " ^ text (get r "field") ^ " " ^ python_repr (get r "actual") ^ "; requested " ^ python_repr (get r "expected") in
  List.iter (fun r -> add (row r)) (Runtime_evidence.issues evaluation);
  waivers := List.map (fun r -> s (row r)) (Runtime_evidence.waivers evaluation);
  (match Runtime_evidence.binding_from_observation ~native (if native then !host else !container) with
   | Ok b -> binding := ["execution_binding",Execution_transport.to_json b]
   | Error e -> add ("execution binding: " ^ e));
  check_cancel cancel;
  report ()
let probe ~bundle ~support_tools ~pdk_root ~python ~resources ~environment ~directory
    ?(native=false) ?(allow_python_mismatch=false) ?(cancel=(fun () -> None)) () =
  probe_with_cancel ~cancel ~bundle ~support_tools ~pdk_root ~python ~resources
    ~environment ~directory ~native ~allow_python_mismatch ()
type outcome = {report_path:string;receipt_path:string option;report:J.t;status:R.status;exit_code:int;
  report_published:bool;receipt_published:bool;publication_errors:string list}
let now () =
  let time = Unix.gettimeofday () in
  let seconds = Float.floor time in
  let t = Unix.gmtime seconds in
  Printf.sprintf "%04d-%02d-%02dT%02d:%02d:%02d.%06d+00:00"
    (t.tm_year+1900) (t.tm_mon+1) t.tm_mday t.tm_hour t.tm_min t.tm_sec
    (int_of_float ((time -. seconds) *. 1000000.))
let run ~bundle ~support_tools ~pdk_root ~python ~resources ~environment ~results_root
    ?output ?(native=false) ?(allow_python_mismatch=false) () =
  let bundle = resolve bundle in
  let report_path,receipt = match output with
    | Some p ->
      let p = lexical p in
      let path = try R.validate_output p with
        | Unix.Unix_error (Unix.ENOENT,_,_) -> failwith ("preflight output parent does not exist: " ^ Filename.dirname p)
        | Unix.Unix_error (Unix.EISDIR,_,_) -> failwith ("preflight output is a directory: " ^ p)
        | Unix.Unix_error ((Unix.EACCES | Unix.EPERM),_,_) -> failwith ("preflight output is not writable: " ^ p) in
      path,None
    | None -> let _,path,receipt_path,receipt = R.begin_preflight ~results_root ~bundle () in path,Some (receipt_path,receipt) in
  let status = ref R.Failed and exit_code = ref 1 in
  let interrupted_report signal =
    status := R.Interrupted; exit_code := 128+signal;
    let name = if signal=2 then "SIGINT" else if signal=15 then "SIGTERM" else "SIG" ^ string_of_int signal in
    R.diagnostic ~bundle ~status:R.Interrupted ~interrupted_by:name ~issue:("interrupted by " ^ name) () in
  let signal = ref None in
  let handler n = if !signal=None then signal := Some n in
  let old_int = Sys.signal Sys.sigint (Sys.Signal_handle handler) in
  let old_term = Sys.signal Sys.sigterm (Sys.Signal_handle handler) in
  Fun.protect ~finally:(fun () -> Sys.set_signal Sys.sigint old_int; Sys.set_signal Sys.sigterm old_term) (fun () ->
    R.save_json report_path (R.pending_report ~bundle ());
    (* Explicit output owns only its report. Probe logs use a private temporary
       directory, never an operation directory or an implicit output parent. *)
    let temporary = Option.is_some output in
    let directory = if temporary then (
      let p = Filename.temp_file "hardcaml-asic-preflight-" "" in Unix.unlink p; Unix.mkdir p 0o700; p)
      else Filename.dirname report_path in
    let rec remove p = match (Unix.lstat p).Unix.st_kind with
      | Unix.S_DIR -> Array.iter (fun n -> remove (Filename.concat p n)) (Sys.readdir p); Unix.rmdir p
      | _ -> Unix.unlink p in
    let cleanup () = if temporary then remove directory in
    let report = Fun.protect ~finally:cleanup (fun () ->
      try check_cancel (fun () -> !signal);
        let report = probe_with_cancel ~cancel:(fun () -> !signal) ~bundle ~support_tools ~pdk_root ~python ~resources ~environment ~directory ~native ~allow_python_mismatch () in
        check_cancel (fun () -> !signal); report
      with
       | Runner.Callback_error {kind=Runner.Cancellation signal;secondary;_} ->
         let report = interrupted_report signal in
         (match report with J.Object xs -> obj (List.map (fun (k,v) ->
           k,(if k="issues" then match v with J.Array issues -> J.array (issues @ List.map s secondary) | _ -> v else v)) xs)
          | _ -> report)
      | e -> R.diagnostic ~bundle ~status:R.Failed ~issue:("preflight failed: " ^ message e) ()) in
    if !status <> R.Interrupted then (let st,code = checked (R.classify_report report) in status := st; exit_code := code);
    let report = ref report and errors = ref [] in
    let report_published = ref false and receipt_published = ref false in
    let refresh_cancel () = match !signal with
      | Some signal when !status <> R.Interrupted ->
        report := interrupted_report (if signal=Sys.sigint then 2 else 15);
        report_published := false
      | _ -> () in
    let with_errors report = match report with
      | J.Object xs -> obj (List.map (fun (k,v) -> k,
          (if k="ready" && !errors<>[] then J.bool false
           else if k="issues" then match v with
             | J.Array issues -> J.array (issues @ List.map s !errors) | _ -> v
           else v)) xs)
      | _ -> report in
    let publication_error path e =
      errors := !errors @ ["terminal publication failed: " ^ path ^ ": " ^ message e];
      (* Cancellation and readiness failure remain primary. Storage alone is
         an infrastructure failure, never a successful readiness result. *)
      if !status=R.Ready then (status := R.Failed; exit_code := 2) in
    let publish_report () =
      refresh_cancel ();
      report_published := false;
      try R.save_json report_path (with_errors !report); report_published := true
      with e -> publication_error report_path e; refresh_cancel () in
    publish_report ();
    refresh_cancel ();
    if not !report_published && !errors=[] then publish_report ();
    Option.iter (fun (path,receipt) ->
      try R.save_json path (R.finish_receipt receipt ~status:!status ~exit_code:!exit_code
        ~ended_at:(now ()) ?error:(if !errors=[] then None else Some (String.concat "; " !errors)) ());
        receipt_published := true
      with e -> publication_error path e; refresh_cancel ();
        (* A writable report can disclose the failed receipt. Never retry or
           replace a caller-corrupted receipt entry. *)
        if !report_published then publish_report ()) receipt;
    {report_path;receipt_path=Option.map fst receipt;report=with_errors !report;
     status= !status;exit_code= !exit_code;report_published= !report_published;
     receipt_published= !receipt_published;publication_errors= !errors})
