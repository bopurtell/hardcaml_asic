(* One foreground CLI process, no domains; mutation precedes exec. *)
[@@@alert "-unsafe_multidomain"]
open Flow_cli_args
let require a keys = List.iter (fun k -> ignore (value a k)) keys
let exclusive a keys required =
  let selected=List.filter (fun k -> get a k<>None || flag a k) keys in
  if List.length selected>1 || (required && selected=[]) then
    error ((if required then "exactly one of " else "at most one of ") ^ String.concat ", " (List.map ((^) "--") keys)^" is required")
let choice a k choices = Option.iter (fun v -> if not (List.mem v choices) then error ("invalid --"^k^": "^v)) (get a k)
let validate a =
  let c=a.command in
  if c="workload-inspect" && String.trim (value a "result")="" then error "expected a nonempty result path";
  if c="compare" then (
    require a ["left-kind";"left";"right-kind";"right";"axis"];
    List.iter (fun key -> choice a key ["bundle";"coarse_run";"execution"]) ["left-kind";"right-kind"];
    choice a "axis" ["hardware";"resource_policy";"flow_configuration";"descriptive"];
    List.iter (fun k -> if String.trim (value a k)="" then error "comparison requires nonempty paths") ["left";"right"]);
  if c="timing-intent-inspect" then choice a "kind" ["standalone";"coarse_run";"execution"];
  if c="preparation-inspect" then (
    if String.trim (value a "attempt")="" then error "expected a nonempty attempt path";
    Option.iter (fun id -> if String.length id<>64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) id)
      then error "current identity must be a lowercase SHA-256 preparation identity") (get a "current-identity"));
  let endpoints=["synthesis";"floorplan";"placement";"cts";"routing";"timing";"signoff"] in
  if c="inspect" then (require a ["domain";"stage"];
    choice a "domain" ["timing";"clocks";"physical";"routing"]; choice a "stage" endpoints;
    if Option.is_some (get a "row-id") && Option.is_none (get a "expected-digest") then
      error "--row-id requires --expected-digest");
  if List.mem c ["run";"execute"] then (require a ["stage"];choice a "stage" ["synthesis";"full"]);
  if c="postcheck" then require a ["testbench";"testbench-top"];
  if c="finalize" then require a ["operation-store"];
  if c="stages" then (require a ["support-tools";"pdk-root";"python";"workspace";"checkpoint-store"];
    exclusive a ["endpoint";"plan";"methodology-plan"] true;exclusive a ["observation-store";"no-observation"] false;
    (match get a "methodology-plan",get a "expected-plan-identity" with
     | Some _,Some hash ->
       if String.length hash<>64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) hash) then error "expected plan identity must be a lowercase SHA-256";
       if get a "checkpoint"<>None then error "methodology launch is fresh-only; --checkpoint is forbidden"
     | Some _,None -> error "--expected-plan-identity is required for --methodology-plan"
     | None,Some _ -> error "--expected-plan-identity requires --methodology-plan"
     | None,None -> ());
    choice a "endpoint" endpoints);
  choice a "restart-to" endpoints;
  if c="archive" then exclusive a ["output";"list"] true;
  if c="collect" then exclusive a ["output";"stdout"] false;
  if c="observation" && a.query="read-updates" then require a ["stream-id";"after"];
  List.iter (fun k -> Option.iter (fun raw ->
    let s=String.trim raw in
    let n=String.length s in
    let start=if n>0 && (s.[0]='-' || s.[0]='+') then 1 else 0 in
    let rec digits i previous_digit =
      if i=n then previous_digit else match s.[i] with
      | '0'..'9' -> digits (i+1) true
      | '_' when previous_digit -> digits (i+1) false
      | _ -> false in
    if start=n || not (digits start false) then error ("--"^k^" requires an integer");
    let unsigned=String.sub s start (n-start) |> String.split_on_char '_' |> String.concat "" in
    let rec leading i=if i<String.length unsigned-1 && unsigned.[i]='0' then leading (i+1) else i in
    let i=leading 0 in
    let unsigned=String.sub unsigned i (String.length unsigned-i) in
    set a k ((if start=1 && s.[0]='-' && unsigned<>"0" then "-" else "")^unsigned)) (get a k))
    (if c="stages" then ["timeout"] else ["after"]);
  List.iter (fun k -> Option.iter (fun s ->
    let f=try float_of_string s with Failure _ -> error ("--"^k^" requires a number") in
    if k="interval" && (not (Float.is_finite f) || f<=0.) then error "interval must be positive and finite") (get a k)) ["interval";(if c="postcheck" then "timeout" else "interval")];
  let nonempty= ["bundle";"results-root";"runs"] @ (if c="preflight" then ["output"] else []) @
    (if c="provision" then ["python"] else []) @ (if List.mem c ["stages";"finalize";"finalization-inspect";"checkpoint-inspect";"observation"] then
      ["attempt";"checkpoint";"execution";"operation-store";"support-tools";"pdk-root";"python";"workspace";"checkpoint-store";"plan";"methodology-plan";"observation-store";"compatibility";"precheck-python";"testbench";"replay-trace"] else []) in
  List.iter (fun k -> Option.iter (fun s -> if String.trim s="" then error "expected a nonempty path") (get a k)) nonempty
let resolve a =
  let c=a.command in
  let paths=["left";"right";"target";"bundle";"run-dir";"runs";"results-root";"output";"archive";"testbench";"replay-trace";"dependency-lock";"toolchain";"support-tools";"pdk-root";
    "workspace";"checkpoint-store";"checkpoint";"plan";"methodology-plan";"observation-store";"execution";"operation-store";"attempt";"compatibility"] in
   List.iter (fun k -> Option.iter (fun v -> set a k (Flow_cli_paths.absolute (if v="" then "." else v))) (get a k)) ("result"::paths);
  List.iter (fun k -> Option.iter (fun v -> set a k (Flow_cli_paths.lexical (if v="" then "." else v))) (get a k)) ["python";"precheck-python"];
  let root=match get a "toolchain" with Some p -> Some p | None ->
    (match Sys.getenv_opt "TOOLCHAIN" with Some s when s<>"" || c="provision" -> Some (Flow_cli_paths.absolute s) | _ -> None) in
  if c="provision" then set a "toolchain" (Option.value root ~default:(Flow_cli_paths.absolute ".toolchain"));
  if List.mem c ["preflight";"run";"execute"] then (
    if get a "results-root"=None then set a "results-root" (Flow_cli_paths.absolute "_flow_results");
    if c<>"preflight" && get a "runs"=None then set a "runs" (value a "results-root"^"/runs"));
  let required=match c with "preflight"|"run"|"execute" -> ["support-tools","tt";"pdk-root","pdk";"python",".venv/bin/python"]
    | "postcheck" -> ["support-tools","tt";"pdk-root","pdk";"precheck-python",".venv-precheck/bin/python"] | _ -> [] in
  let default k suffix = if get a k=None then Option.iter (fun r -> set a k ((if k="python" || k="precheck-python" then Flow_cli_paths.lexical else Flow_cli_paths.absolute) (r^"/"^suffix))) root in
  List.iter (fun (k,s) -> default k s; if get a k=None then error ("--"^k^" or --toolchain/TOOLCHAIN is required")) required;
  if c="execute" && get a "stage"=Some "full" then (
    default "precheck-python" ".venv-precheck/bin/python";
    if get a "precheck-python"=None then error "--precheck-python or --toolchain/TOOLCHAIN is required for full execution");
  if c="collect" && get a "output"=None then set a "output" (value a "run-dir"^"/results.json")
let contract a =
  List.iter (fun w -> match String.index_opt w '=' with
    | Some i when List.mem (String.sub w 0 i) ["python-version";"tool-source";"dependency-revision";"generator-tool-mismatch";"legacy-generator-identity"]
      && String.trim (String.sub w (i+1) (String.length w-i-1))<>"" -> ()
    | _ -> error "--waiver must be a recognized CODE=REASON with a nonempty reason") a.waivers;
  if a.command="provision" && (flag a "json" || flag a "verbose") then error "provision uses bootstrap human output; --json/--verbose are unsupported";
  if a.command="archive" && flag a "list" && flag a "json" then error "--json with archive --list is unsupported; use human --list";
  if flag a "force" && not (a.command="archive" && get a "output"<>None || a.command="execute" && get a "archive"<>None) then error "--force requires an explicit archive destination";
  if List.mem a.command ["run";"execute"] && flag a "native" && get a "stage"=Some "full" then error "native execution supports synthesis only";
  if a.command="execute" then (
    let inputs=List.map (fun k -> get a k<>None) ["testbench";"testbench-top";"precheck-python"] in
    if get a "stage"=Some "full" && not (List.for_all Fun.id inputs) then error "full execution requires --testbench, --testbench-top, and precheck Python";
    if get a "stage"=Some "synthesis" && List.exists Fun.id inputs then error "synthesis execution rejects postcheck-only inputs");
  if a.waivers<>[] || get a "dependency-lock"<>None then failwith "dependency identity and waiver production handling belongs to P6.5"
let exec path args = Unix.execv path (Array.of_list (path::args))
let request a =
  let options,switches,pos=grammar a.command a.query in
  let field k = String.map (fun c -> if c='-' then '_' else c) k in
  let scalar k = match get a k with
    | None when k="timeout" && a.command="stages" -> `Int 600
    | None -> `Null
    | Some s when k="timeout" -> `Int (int_of_string s)
    | Some s -> `String s in
  Yojson.Basic.to_string (`Assoc (["caller_cwd",`String (Sys.getcwd ());"command",`String a.command;
    "version",`Bool (flag a "version");"json",`Bool (flag a "json");"verbose",`Bool (flag a "verbose")] @
    (if pos="" then [] else [field pos,scalar pos]) @
    List.map (fun k -> field k,scalar k) options @
    List.map (fun k -> field k,`Bool (flag a k)) switches))
let route a request =
  let c=a.command in
  let fields keys=List.concat_map (fun k -> match get a k with None -> [] | Some v -> ["--"^k;v]) keys in
  let switches keys=List.filter_map (fun k -> if flag a k then Some ("--"^k) else None) keys in
  let presentation=switches ["json";"verbose"] in
  let resources=["--resources";Flow_cli_paths.resources ()] in
  let helper,args=match c with
    | "preflight" -> c,resources @ fields ["bundle";"support-tools";"pdk-root";"python";"results-root";"output"] @ switches ["native"] @ presentation
    | "run"|"execute" -> c,resources @ fields (["bundle";"support-tools";"pdk-root";"python";"runs";"stage"] @
      if c="execute" then ["precheck-python";"testbench";"testbench-top";"archive"] else []) @
      (if c="run" then ["--public-cli4"] else []) @ switches ["native";"force"] @ presentation
     | "postcheck" -> c,["--public-cli4"] @ resources @ fields ["run-dir";"support-tools";"pdk-root";"precheck-python";"testbench";"testbench-top";"replay-trace";"timeout"] @ presentation
    | "collect" -> c,fields ["run-dir"] @ (if flag a "stdout" then ["--stdout"] else fields ["output"]) @ presentation
      | "report" -> c,fields ["target"] @ presentation
      | "workload-inspect" -> c,[value a "result"] @ presentation
     | "compare" -> c,fields ["left-kind";"left";"right-kind";"right";"axis";"left-workload";"right-workload"] @ presentation
       | "resources-inspect" -> c,fields ["run-dir"] @ presentation
       | "timing-intent-inspect" -> c,[value a "target"] @ fields ["kind"] @ presentation
      | "resource-inspect" -> c,fields ["target";"kind";"query";"resource-id";"section";"expected-digest";"artifact-id";"offset";"limit"] @ presentation
     | "inspect" -> c,fields ["execution";"domain";"stage";"row-id";"expected-digest"] @ switches ["refresh-timing"] @ presentation
    | "archive" -> c,fields ["run-dir";"output"] @ switches ["list";"force"] @ presentation
    | "checkpoint-inspect" -> c,(if flag a "json" then ["--json"] else []) @ [value a "checkpoint"] @ fields ["compatibility";"restart-to"]
    | "observation" -> "observation-read",[a.query] @ (if a.query="describe" then [] else [value a "attempt"]) @
      fields ["stream-id";"after";"interval"] @ (if a.query="watch" then (if flag a "json" then ["--json"] else [])
       else ["--public-cli4"] @ switches ["json";"verbose"])
    | "finalization-inspect" -> "finalization",["--public-cli4";"--inspect";"--directory";value a "attempt"] @ presentation
    | "preparation-inspect" -> c,[value a "attempt"] @ fields ["current-identity"] @ presentation
    | "finalize" -> "finalization",["--public-cli4";"--execution";value a "execution";"--store";value a "operation-store"] @
      fields ["support-tools";"precheck-python";"testbench";"replay-trace";"testbench-top"] @ switches ["accept"] @ presentation
    | "stages" ->
      let workspace=value a "workspace" in
      let evidence=Filename.dirname workspace ^ "/stage-preflight" in
      let evidence=if evidence=workspace then evidence^"-evidence" else evidence in
      c,["--public-cli4"] @ resources @ fields ["bundle";"workspace";"checkpoint-store";"support-tools";"pdk-root";"python";"plan";"methodology-plan";"expected-plan-identity";"checkpoint";"observation-store"] @
        ["--evidence-root";evidence;"--timeout";Option.value (get a "timeout") ~default:"600";"--request";request] @
        (match get a "endpoint" with None -> [] | Some s -> ["--through";s]) @ switches ["json";"no-observation"]
    | _ -> assert false in
  exec (Flow_cli_paths.helper helper) args
let provision a =
  let data=Flow_cli_paths.resources ()^"/data" in
  List.iter (fun name -> if not (Sys.file_exists (data^"/"^name)) then failwith ("missing bootstrap runtime data: "^data^"/"^name^". Reinstall hardcaml_asic.")) ["toolchain.sh";"toolchain.lock"];
  Unix.putenv "TOOLCHAIN" (value a "toolchain");
  let bootstrap_executable name=List.find_map (fun dir -> let p=Flow_cli_paths.lexical (Filename.concat dir name) in
    try Unix.access p [Unix.X_OK];Some p with Unix.Unix_error _ -> None)
    (String.split_on_char ':' (Option.value (Sys.getenv_opt "PATH") ~default:"/usr/bin:/bin")) in
  let required name=match bootstrap_executable name with Some p -> p | None -> failwith ("provision bootstrap requires external "^name^" on PATH") in
  let host_python=required "python3" in
  exec (required "bash") ([data^"/toolchain.sh";"--lock-file";data^"/toolchain.lock";"--host-python";host_python] @
    (match get a "python" with None -> [] | Some p -> ["--python";p]) @
    List.filter_map (fun k -> if flag a k then Some ("--"^k) else None) ["check";"offline";"no-container"])
let () =
  Sys.set_signal Sys.sigpipe Sys.Signal_ignore;
  let silence_stdout () = try
    let fd=Unix.openfile "/dev/null" [Unix.O_WRONLY] 0 in
    Fun.protect ~finally:(fun () -> Unix.close fd) (fun () -> Unix.dup2 fd Unix.stdout)
    with Unix.Unix_error _ -> () in
  try
    let a=parse (List.tl (Array.to_list Sys.argv)) in
    if flag a "version" then Flow_cli_identity.report (flag a "json")
    else if a.command="" then help "" ""
    else (
      validate a;
      let request=if a.command="stages" then request a else "" in
      resolve a;contract a;
      if a.command="provision" then provision a else route a request);
    flush stdout
  with
  | Usage s -> prerr_endline ("hardcaml-asic-flow: error: "^s); exit 2
  | Failure s | Invalid_argument s -> prerr_endline ("hardcaml-asic-flow: "^s); exit 2
  | Sys_error s -> silence_stdout (); prerr_endline ("hardcaml-asic-flow: "^s); exit 2
  | Unix.Unix_error (e,f,p) -> Printf.eprintf "hardcaml-asic-flow: %s %s: %s\n" f p (Unix.error_message e); exit 2
