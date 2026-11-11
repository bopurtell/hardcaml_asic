module J = Identity_json
module T = Execution_transport
let codec = function Ok x -> x | Error e -> failwith (J.error_message e)
let checked = function Ok x -> x | Error s -> failwith s
let s x = codec (J.string x)
let obj xs = codec (J.object_ xs)
let get j k = match j with J.Object xs -> (match List.assoc_opt k xs with Some x -> x | None -> failwith ("missing " ^ k)) | _ -> failwith "expected object"
let read p = let ic = open_in_bin p in Fun.protect ~finally:(fun () -> close_in_noerr ic)
  (fun () -> let n = in_channel_length ic in if n > 1048576 then failwith "probe output exceeds 1 MiB";
    really_input_string ic n)
type t = { directory:string; environment:string list; timeout:float; cancel:unit -> int option; mutable serial:int }
let create_cancellable ~cancel ~directory ~environment ~timeout =
  if not (Float.is_finite timeout) || timeout <= 0. then invalid_arg "finite positive probe timeout required";
  {directory;environment=T.clean_environment environment;timeout;cancel;serial=0}
let create ~directory ~environment ~timeout =
  create_cancellable ~cancel:(fun () -> None) ~directory ~environment ~timeout
let paths t = t.serial <- t.serial + 1; let base = t.directory ^ "/probe-" ^ string_of_int t.serial in
  base,base ^ ".stdout",base ^ ".stderr"
let record_result base argv (result:T.result) =
  let outcome,code = match result.process.outcome with
    | Process_supervisor.Exited n -> "exited",codec (J.integer (string_of_int n))
    | Signaled n -> "signaled",codec (J.integer (string_of_int n))
    | Cancelled n -> "cancelled",codec (J.integer (string_of_int n))
    | Timed_out -> "timed_out",J.null | Failed _ -> "failed",J.null in
  Result_store.save_json (base ^ ".json") (obj ["argv",J.array (List.map s argv);
    "outcome",s outcome;"code_or_ocaml_signal",code;"elapsed_seconds",codec (J.float result.process.elapsed_seconds);
    "succeeded",J.bool (T.succeeded result);
    "process_error",(match result.process.outcome with Failed x -> s x | _ -> J.null);
    "process_cleanup_error",(match result.process.cleanup_error with Some x -> s x | None -> J.null);
    "cleanup_error",(match result.cleanup with Some c -> (match c.error with Some x -> s x | None -> J.null) | None -> J.null)])
let finish base argv result =
  let storage = try record_result base argv result; [] with e ->
    ["runtime probe evidence write failed: " ^ Printexc.to_string e] in
  if not (T.succeeded result) then (
    let e = Segment_service.process_error result in
    let secondary = e.secondary @ storage in
    let e = match e.kind with
      | Runner.Cancellation signal -> Runner.cancellation ~secondary ~signal e.message
      | Runner.Failure -> Runner.failure ~exit_code:e.exit_code ~secondary e.message in
    raise (Runner.Callback_error e));
  match storage with [] -> () | xs -> raise (Runner.Callback_error (Runner.failure (String.concat "; " xs)))
let command t ~prog ~arguments =
  (match t.cancel () with None -> () | Some signal ->
    raise (Runner.Callback_error (Runner.cancellation ~signal:(if signal=Sys.sigint then 2 else 15) "runtime probe interrupted")));
  let base,out,err = paths t in
  Result_store.save_json (base ^ ".json") (obj ["argv",J.array (List.map s (prog::arguments))]);
  let process = Process.make ~prog ~argv:(prog::arguments) ~cwd:"/" ~environment:t.environment
    ~stdout:(Process.File out) ~stderr:(Process.File err) in
  let result = Process_supervisor.run ~cancel:t.cancel ~timeout:t.timeout process in
  finish base process.argv {T.process=result;cleanup=None}; String.trim (read out)
let find_executable ~environment name =
  let path = match List.find_opt (String.starts_with ~prefix:"PATH=") environment with
    | Some x -> String.sub x 5 (String.length x - 5) | None -> "" in
  let candidates = if Filename.is_relative name then List.map (fun p -> Filename.concat p name) (String.split_on_char ':' path) else [name] in
  match List.find_opt (fun p -> not (Filename.is_relative p) && try Unix.access p [Unix.X_OK]; (Unix.stat p).Unix.st_kind=Unix.S_REG with _ -> false) candidates with
  | Some p -> p | None -> failwith ("executable not found: " ^ name)
let metadata ~host output =
  let j = codec (J.parse output) in
  ignore (codec (J.exact_object j ~fields:["protocol_version";"kind";"role";"metadata"]));
  if get j "protocol_version" <> codec (J.integer "1") || get j "kind" <> s "stage_runtime_metadata"
    || get j "role" <> s (if host then "host" else "container") then failwith "metadata probe protocol mismatch";
  let m = get j "metadata" in ignore (checked (Runtime_evidence.metadata ~host m)); m
let flags f = J.array (List.map s (match f with T.Isolated -> ["-I";"-B"] | T.Bytecode_disabled -> ["-B"]))
let observe_host t ~python ~payload =
  let host = metadata ~host:true (command t ~prog:python ~arguments:["-I";"-B";"-c";read payload;"host"]) in
  match host with J.Object xs -> obj (xs @ ["interpreter",s python;"interpreter_flags",flags T.Isolated]) | _ -> assert false
let observe_container ?(workers=20) ?pids_limit t ~docker ~payload policy =
  let info = codec (J.parse (command t ~prog:docker ~arguments:["info";"--format";"{{json .}}"])) in
  let inspection = codec (J.parse (command t ~prog:docker ~arguments:["image";"inspect";Runtime_contract.image_ref policy])) in
  let probe flag =
    let binding = checked (Runtime_evidence.docker_binding ~declared_ref:(Runtime_contract.image_ref policy)
      ~executable:docker ~info ~inspection ~observation:(obj ["interpreter_flags",flags flag])) in
    let d = match binding with T.Docker d -> d | _ -> assert false in
    let base,out,err = paths t in
     let invocation = T.metadata_invocation ~workers ?pids_limit d ~arguments:["-c";read payload;"metadata-container"]
      ~environment:t.environment ~stdout:(Process.File out) ~stderr:(Process.File err) in
    let spec = T.process_specification invocation in
    Result_store.save_json (base ^ ".json") (obj ["argv",J.array (List.map s spec.argv)]);
    let result = T.run ~cancel:t.cancel ~timeout:t.timeout invocation in
    finish base spec.argv result;
    metadata ~host:false (read out),binding in
  let observed,binding = probe T.Isolated in
  let observed,binding = if get observed "metadata_error" <> J.null then probe T.Bytecode_disabled else observed,binding in
  if get observed "metadata_error" <> J.null || get observed "librelane_version" = J.null
    || get observed "requires_python" = J.null then failwith "missing container distribution metadata";
  let b = T.to_json binding in
  match observed with J.Object xs -> obj (xs @ ["interpreter",s "python3";
    "interpreter_flags",get b "interpreter_flags";
    "packaging_path",s (if get b "interpreter_flags" = flags T.Isolated then "isolated" else "image-owned-environment");
    "image_identity",get b "image_identity";"engine_identity",get b "engine_identity"]) | _ -> assert false
let observe ?(workers=6) ?pids_limit t ~python ~docker ~payload ~pdk_root policy =
  let code = read payload in
  let host = metadata ~host:true (command t ~prog:python ~arguments:["-I";"-B";"-c";code;"host"]) in
  let host = match host with J.Object xs -> obj (xs @ ["interpreter",s python;"interpreter_flags",flags T.Isolated]) | _ -> assert false in
  let info = codec (J.parse (command t ~prog:docker ~arguments:["info";"--format";"{{json .}}"])) in
  let inspection = codec (J.parse (command t ~prog:docker ~arguments:["image";"inspect";Runtime_contract.image_ref policy])) in
  let probe flag =
    let binding = checked (Runtime_evidence.docker_binding ~declared_ref:(Runtime_contract.image_ref policy) ~executable:docker
      ~info ~inspection ~observation:(obj ["interpreter_flags",flags flag])) in
    let d = match binding with T.Docker d -> d | _ -> assert false in
    let base,out,err = paths t in
     let invocation = T.docker_invocation d ~workers ?pids_limit ~project:t.directory ~pdk_root ~interpreter:true
      ~arguments:["-c";code;"container"] ~cwd:"/" ~environment:t.environment ~stdout:(Process.File out) ~stderr:(Process.File err) () in
    let spec = T.process_specification invocation in
    Result_store.save_json (base ^ ".json") (obj ["argv",J.array (List.map s spec.argv)]);
    let result = T.run ~cancel:t.cancel ~timeout:t.timeout invocation in
    finish base spec.argv result;
    metadata ~host:false (read out),binding in
  let observed,binding = probe T.Isolated in
  let observed,binding = if get observed "metadata_error" <> J.null then probe T.Bytecode_disabled else observed,binding in
  let container = match observed with J.Object xs ->
    let binding_json = T.to_json binding in
    obj (xs @ ["interpreter",s "python3";"interpreter_flags",get binding_json "interpreter_flags";
      "image_identity",get binding_json "image_identity";"engine_identity",get binding_json "engine_identity"]) | _ -> assert false in
  host,container,binding
let verify t = function
  | T.Native _ -> Error "stage runtime requires Docker"
  | T.Docker d -> try
    let info = codec (J.parse (command t ~prog:d.executable ~arguments:["info";"--format";"{{json .}}"] )) in
    ignore (checked (Runtime_evidence.verify_engine d info));
    let inspection = codec (J.parse (command t ~prog:d.executable ~arguments:["image";"inspect";d.image_id])) in
    let image = match inspection with J.Array [j] -> j | _ -> failwith "invalid image inspection" in
    let id = match get image "Id" with J.String x -> x | _ -> failwith "missing local image ID" in
    Runtime_evidence.verify_image d ~actual_id:id
    with Runner.Callback_error e -> raise (Runner.Callback_error e) | exn -> Error (Printexc.to_string exn)
