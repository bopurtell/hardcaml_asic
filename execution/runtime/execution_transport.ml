[@@@alert "-unsafe_multidomain"]
module J = Identity_json
type optional_string = Absent | Null | Value of string
type flags = Isolated | Bytecode_disabled
type native = { interpreter : string }
type docker =
  { executable : string; rootless : bool; server_version : string
  ; daemon_id : optional_string; name_on_server : optional_string
  ; declared_ref : string; image_id : string; repo_digests : string list
  ; os : string; architecture : string; variant : optional_string; flags : flags }
type binding = Native of native | Docker of docker
let invalid s = invalid_arg ("Execution_transport: " ^ s)
let text s =
  if String.contains s '\000' then invalid "NUL in string";
  match J.string s with Ok _ -> () | Error _ -> invalid "invalid Unicode"
let nonempty s = text s; if s = "" then invalid "empty string"
let absolute s = nonempty s; if Filename.is_relative s then invalid "relative path"
let optional = function Absent | Null -> () | Value s -> text s
let hex s = String.for_all (function '0'..'9' | 'a'..'f' -> true | _ -> false) s
let native ~interpreter = absolute interpreter; Native { interpreter }
let docker ~executable ~rootless ~server_version ?(daemon_id=Absent)
    ?(name_on_server=Absent) ~declared_ref ~image_id ~repo_digests ~os ~architecture
    ?(variant=Absent) ~flags () =
  absolute executable;
  List.iter nonempty [server_version; declared_ref; os; architecture];
  List.iter text repo_digests;
  List.iter optional [daemon_id; name_on_server; variant];
  if String.length image_id <> 71 || String.sub image_id 0 7 <> "sha256:"
     || not (hex (String.sub image_id 7 64)) then invalid "invalid local image ID";
  Docker {executable; rootless; server_version; daemon_id; name_on_server;
          declared_ref; image_id; repo_digests; os; architecture; variant; flags}
let unwrap = function Ok x -> x | Error e -> invalid (J.error_message e)
let str s = unwrap (J.string s)
let obj fields = unwrap (J.object_ fields)
let opts key = function Absent -> [] | Null -> [key, J.null] | Value s -> [key, str s]
let flag_list = function Isolated -> ["-I"; "-B"] | Bytecode_disabled -> ["-B"]
let to_json b =
  let mode, interpreter, flags, more = match b with
    | Native n -> "native", n.interpreter, Isolated, []
    | Docker d ->
      "dockerized", "python3", d.flags,
      ["image_identity", obj
         ["binding", str "local-image-id"; "declared_ref", str d.declared_ref;
          "image_id", str d.image_id;
          "repo_digests", J.array (List.map str d.repo_digests);
          "platform", obj (["os", str d.os; "architecture", str d.architecture]
                            @ opts "variant" d.variant)];
       "engine_identity", obj
         (["name", str "docker"; "executable", str d.executable;
           "rootless", J.bool d.rootless; "server_version", str d.server_version]
          @ opts "daemon_id" d.daemon_id @ opts "name_on_server" d.name_on_server)]
  in
  obj (["schema_version", unwrap (J.integer "1"); "execution_mode", str mode;
        "interpreter", str interpreter;
        "interpreter_flags", J.array (List.map str (flag_list flags))] @ more)
let of_json value =
  try
    let fields = function J.Object f -> f | _ -> invalid "expected object" in
    let get f key = match List.assoc_opt key f with Some v -> v | None -> invalid ("missing " ^ key) in
    let string = function J.String s -> s | _ -> invalid "expected string" in
    let required f k = string (get f k) in
    let closed v required optional =
      let f = fields v in
      ignore (unwrap (J.exact_object v ~fields:(required @ List.filter (fun k -> List.mem_assoc k f) optional)));
      f
    in
    let opt f k = match List.assoc_opt k f with
      | None -> Absent | Some J.Null -> Null | Some v -> Value (string v) in
    let f = fields value in
    if get f "schema_version" <> unwrap (J.integer "1") then invalid "schema version";
    let flags = match get f "interpreter_flags" with
      | J.Array [J.String "-I"; J.String "-B"] -> Isolated
      | J.Array [J.String "-B"] -> Bytecode_disabled
      | _ -> invalid "interpreter flags" in
    let base = ["schema_version"; "execution_mode"; "interpreter"; "interpreter_flags"] in
    match required f "execution_mode" with
    | "native" ->
      ignore (closed value base []);
      if flags <> Isolated then invalid "native flags";
      Ok (native ~interpreter:(required f "interpreter"))
    | "dockerized" ->
      ignore (closed value (base @ ["image_identity"; "engine_identity"]) []);
      if required f "interpreter" <> "python3" then invalid "Docker interpreter";
      let image = closed (get f "image_identity")
          ["binding"; "declared_ref"; "image_id"; "repo_digests"; "platform"] [] in
      let engine = closed (get f "engine_identity")
          ["name"; "executable"; "rootless"; "server_version"] ["daemon_id"; "name_on_server"] in
      let platform = closed (get image "platform") ["os"; "architecture"] ["variant"] in
      if required image "binding" <> "local-image-id" || required engine "name" <> "docker"
      then invalid "binding authority";
      let rootless = match get engine "rootless" with J.Bool b -> b | _ -> invalid "rootless" in
      let repo_digests = match get image "repo_digests" with
        | J.Array l -> List.map string l | _ -> invalid "repo digests" in
      Ok (docker ~executable:(required engine "executable") ~rootless
            ~server_version:(required engine "server_version")
            ~daemon_id:(opt engine "daemon_id") ~name_on_server:(opt engine "name_on_server")
            ~declared_ref:(required image "declared_ref") ~image_id:(required image "image_id")
            ~repo_digests ~os:(required platform "os") ~architecture:(required platform "architecture")
            ~variant:(opt platform "variant") ~flags ())
    | _ -> invalid "execution mode"
  with Invalid_argument s -> Error s
let parse s = match J.parse s with Ok j -> of_json j | Error e -> Error (J.error_message e)
let starts s prefix = String.starts_with ~prefix s
let clean_environment env =
  let excluded = ["PYTHONHOME"; "PYTHONPATH"; "TCL_LIBRARY"; "TK_LIBRARY"; "TCLLIBPATH";
                  "GCONV_PATH"; "GLIBC_TUNABLES"; "LIBRELANE_IMAGE_OVERRIDE";
                  "LIBRELANE_CONTAINER_ENGINE"; "OPENLANE_CONTAINER_ENGINE"; "_MOUNT_HOST_LIBRELANE"] in
  List.filter (fun entry ->
      let key = match String.index_opt entry '=' with
        | Some i -> String.sub entry 0 i | None -> invalid "environment entry" in
      not (List.mem key excluded || starts key "LD_" || starts key "DYLD_")) env
let owned_name () =
  let ic = open_in_bin "/dev/urandom" in
  let bytes = Fun.protect ~finally:(fun () -> close_in ic) (fun () -> really_input_string ic 16) in
  "hardcaml-asic-execution-" ^ String.concat "" (List.init 16 (fun i -> Printf.sprintf "%02x" (Char.code bytes.[i])))
let check_name name =
  let prefix = "hardcaml-asic-execution-" in
  let n = String.length prefix in
  if String.length name <> n + 32 || not (starts name prefix) || not (hex (String.sub name n 32))
  then invalid "owned container name"
let pids_arguments = function
  | None -> []
  | Some n when n >= 1 && n <= 20 -> ["--pids-limit=" ^ string_of_int n]
  | Some _ -> invalid "total container tasks must be in 1..20"
let docker_command d ~project ~pdk_root ~name ?workdir ?(entrypoint="python3") ?pdk ?(extra_mounts=[]) ?(workers=20) ?pids_limit ~uid ~gid () =
   if workers < 1 || workers > 20 then invalid "worker budget";
   let budget = string_of_int workers in
  check_name name;
   List.iter (fun p -> absolute p; if String.contains p ':' then invalid "colon in mount path") ([project; pdk_root] @ extra_mounts);
  let workdir = Option.value workdir ~default:project in
  absolute workdir; nonempty entrypoint; Option.iter text pdk;
  if uid < 0 || gid < 0 then invalid "negative uid/gid";
     [d.executable; "run"; "--rm"; "--pull=never"; "--name"; name; "--cpus=" ^ budget]
   @ pids_arguments pids_limit
  @ (if d.rootless then [] else ["--user"; Printf.sprintf "%d:%d" uid gid])
   @ ["-v"; project ^ ":" ^ project ^ ":rw"; "-v"; pdk_root ^ ":" ^ pdk_root ^ ":ro"; "-w"; workdir]
   @ List.concat_map (fun p -> ["-v";p ^ ":" ^ p ^ ":rw"]) extra_mounts
  @ (match pdk with None -> [] | Some p -> ["-e"; "PDK_ROOT=" ^ pdk_root; "-e"; "PDK=" ^ p])
   @ ["-e"; "_OPENLANE_MAX_CORES=" ^ budget; "-e"; "OPENROAD_THREADS=" ^ budget; "-e"; "OMP_NUM_THREADS=" ^ budget;
      "--entrypoint"; entrypoint; d.image_id]
type invocation = { process : Process.t; container : (docker * string) option; mutable launched : bool }
let process_specification i = i.process
let container i = i.container
let native_invocation n ~arguments ~cwd ~environment ~stdout ~stderr =
  {process = Process.make ~prog:n.interpreter
       ~argv:(n.interpreter :: flag_list Isolated @ arguments) ~cwd
       ~environment:(clean_environment environment) ~stdout ~stderr; container=None; launched=false}
let docker_invocation d ~project ~pdk_root ?workdir ?pdk ?extra_mounts ?workers ?pids_limit ?(entrypoint="python3")
    ~interpreter ~arguments ~cwd ~environment ~stdout ~stderr () =
  if interpreter && entrypoint <> "python3" then invalid "interpreter entrypoint";
  let name = owned_name () in
   let argv = docker_command d ~project ~pdk_root ~name ?workdir ?pdk ?extra_mounts ?workers ?pids_limit ~entrypoint
      ~uid:(Unix.getuid ()) ~gid:(Unix.getgid ()) ()
      @ (if interpreter then flag_list d.flags else []) @ arguments in
  {process = Process.make ~prog:d.executable ~argv ~cwd
       ~environment:(clean_environment environment) ~stdout ~stderr;
   container=Some (d,name); launched=false}
type removal = { result : Process_supervisor.result; diagnostic : string }
let metadata_invocation ?(workers=20) ?pids_limit d ~arguments ~environment ~stdout ~stderr =
   if workers < 1 || workers > 20 then invalid "worker budget";
  let name = owned_name () in
  let argv = [d.executable; "run"; "--rm"; "--pull=never"; "--network"; "none";
     "--read-only"; "--cpus=" ^ string_of_int workers; "--name"; name]
     @ pids_arguments pids_limit
     @ (if workers=20 then [] else ["-e";"OMP_NUM_THREADS=" ^ string_of_int workers;
         "-e";"_OPENLANE_MAX_CORES=" ^ string_of_int workers])
     @ ["--entrypoint"; "python3"; d.image_id]
    @ flag_list d.flags @ arguments in
  {process=Process.make ~prog:d.executable ~argv ~cwd:"/"
    ~environment:(clean_environment environment) ~stdout ~stderr;
   container=Some (d,name); launched=false}
type cleanup = { attempts : removal list; error : string option }
type result = { process : Process_supervisor.result; cleanup : cleanup option }
let succeeded r = Process_supervisor.succeeded r.process
                  && Option.fold ~none:true ~some:(fun c -> c.error = None) r.cleanup
external now : unit -> float = "hardcaml_asic_monotonic"
let contains s sub =
  let rec loop i = i + String.length sub <= String.length s
                  && (String.sub s i (String.length sub) = sub || loop (i+1)) in
  loop 0
let remove d name process remaining =
  let file, oc = Filename.open_temp_file "hardcaml-asic-removal-" ".log" in
  let fd = Unix.descr_of_out_channel oc in
  Fun.protect ~finally:(fun () -> close_out_noerr oc; Sys.remove file) (fun () ->
      let p = Process.make ~prog:d.executable ~argv:[d.executable; "rm"; "-f"; name]
          ~cwd:"/" ~environment:process.Process.environment
          ~stdout:(Process.Descriptor fd) ~stderr:(Process.Descriptor fd) in
      let result = Process_supervisor.run ~timeout:remaining ~term_grace:0.01 p in
      let ic = open_in_bin file in
      let diagnostic = Fun.protect ~finally:(fun () -> close_in ic) (fun () ->
          really_input_string ic (min 8192 (in_channel_length ic))) in
      {result; diagnostic})
let run ?timeout ?term_grace ?cancel ?(cleanup_timeout=5.) (invocation : invocation) =
  let duration x = if not (Float.is_finite x) || x <= 0. then invalid "duration" in
  duration cleanup_timeout; Option.iter duration timeout; Option.iter duration term_grace;
  if invocation.launched then invalid "invocation already launched";
  invocation.launched <- true;
  (* An outer scope bridges supervisor scopes, including sleeps and resource IO.
     Each supervisor restores these handlers; removal never inherits cancellation
     of the primary launch, so cancellation cannot skip owned-resource cleanup. *)
  let interrupted = ref None in
  let handler s = if !interrupted = None then interrupted := Some s in
  let old_int = Sys.signal Sys.sigint (Sys.Signal_handle handler) in
  let old_term = Sys.signal Sys.sigterm (Sys.Signal_handle handler) in
  let result = Fun.protect ~finally:(fun () -> Sys.set_signal Sys.sigint old_int; Sys.set_signal Sys.sigterm old_term)
    (fun () ->
       let main_cancel () = match !interrupted with
         | Some s -> Some s
         | None -> Option.fold ~none:None ~some:(fun f -> f ()) cancel in
        let process = Process_supervisor.run ?timeout ?term_grace ~cancel:main_cancel invocation.process in
        (* Only a clean completed client establishes that creation is finished.
           Nonzero/failed/killed clients can leave a daemon request in flight. *)
        let definitive = Process_supervisor.succeeded process in
        let cleanup = Option.map (fun (d,name) ->
           let deadline = now () +. cleanup_timeout in
           let attempts = ref [] and error = ref (Some "cleanup not attempted") in
           (try
              (* Do not launch an engine with only a few milliseconds left: its
                 exec/startup would make the last otherwise healthy observation
                 spuriously time out. No new attempt starts in the final 150ms;
                 no unbounded final attempt is made. *)
               while (not definitive || !error <> None) && now () < deadline -. 0.15 do
                let removal = remove d name invocation.process (min 1. (deadline -. now () -. 0.02)) in
                attempts := removal :: !attempts;
                (match removal.result.outcome with
                 | Process_supervisor.Cancelled s -> handler s
                 | _ -> ());
                let absent = match removal.result.outcome with
                  | Process_supervisor.Exited n when n <> 0 ->
                    contains removal.diagnostic "No such container" || contains removal.diagnostic "no container"
                  | _ -> false in
                error := if Process_supervisor.succeeded removal.result || absent then None
                  else Some ("owned container removal failed: " ^ removal.diagnostic);
                 let wait = if definitive && !error = None then 0.
                   else min 0.05 (max 0. (deadline -. now () -. 0.03)) in
                (try ignore (Unix.select [] [] [] wait) with Unix.Unix_error (Unix.EINTR,_,_) -> ())
              done
            with exn -> error := Some (Printexc.to_string exn));
           {attempts=List.rev !attempts; error= !error}) invocation.container in
       {process; cleanup}) in
  (* Snapshot after restoring handlers, including signals during resource closing
     or the end of the outer scope, just as Process_supervisor does. *)
  let process = match !interrupted, result.process.outcome with
    | Some s, Process_supervisor.Exited 0 -> {result.process with outcome=Process_supervisor.Cancelled s}
    | Some _, _ -> {result.process with cleanup_error=Some "interrupted during transport cleanup"}
    | _ -> result.process in
  {result with process}
