module T = Hardcaml_asic_execution.Execution_transport
module J = Hardcaml_asic_execution.Identity_json
module S = Hardcaml_asic_execution.Process_supervisor
module P = Hardcaml_asic_execution.Process
module Execution_plan = Hardcaml_asic_execution.Execution_plan
module Synthesis_envelope = Hardcaml_asic_execution.Synthesis_envelope
[@@@alert "-unsafe_multidomain"]
let unwrap = function Ok x -> x | Error _ -> failwith "JSON construction"
let string s = unwrap (J.string s)
let obj f = unwrap (J.object_ f)
let list l = J.array (List.map string l)
let fields = function J.Object f -> f | _ -> failwith "object"
let get j k = List.assoc k (fields j)
let str = function J.String s -> s | _ -> failwith "string"
let strings = function J.Array l -> List.map str l | _ -> failwith "array"
let number = function J.Float x -> x | J.Integer n -> float_of_string n | _ -> failwith "number"
let bool = function J.Bool b -> b | _ -> failwith "bool"
let nullable_string = function J.Null -> None | v -> Some (str v)
let outcome = function
  | S.Exited n -> "exit:" ^ string_of_int n
  | S.Signaled s -> "signal:" ^ string_of_int s
  | S.Cancelled s -> "cancel:" ^ string_of_int s
  | S.Timed_out -> "timeout"
  | S.Failed s -> "failed:" ^ s
let optional = function None -> J.null | Some s -> string s
let main request =
  if str (get request "op") = "envelope" then (
    let env = strings (get request "environment") in
    let plan = match Execution_plan.of_json (get request "plan") with Ok p -> p | Error e -> invalid_arg e in
    Synthesis_envelope.check_plan env plan;
    obj ["requested",J.bool (Synthesis_envelope.requested env);
      "limit",(match Synthesis_envelope.pids_limit env with None -> J.null | Some n -> unwrap (J.integer (string_of_int n)))])
  else
  match (if str (get request "op") = "parse" then T.parse (str (get request "raw"))
         else T.of_json (get request "binding")) with
  | Error e -> obj ["error", string e]
  | Ok binding ->
    let op = str (get request "op") in
    if op = "binding" || op = "parse" then obj ["binding", T.to_json binding] else
    let env = strings (get request "environment") in
    let args = strings (get request "arguments") in
    let cwd = str (get request "cwd") in
    let project = str (get request "project") in
    let pdk_root = str (get request "pdk_root") in
    let workdir = nullable_string (get request "workdir") in
    let pdk = nullable_string (get request "pdk") in
    let entrypoint = str (get request "entrypoint") in
    if op = "command" || op = "metadata-command" then
      let pids_limit = match List.assoc_opt "pids_limit" (fields request) with
        | None | Some J.Null -> None | Some (J.Integer n) -> Some (int_of_string n)
        | _ -> invalid_arg "integer task cap required" in
      let argv = match binding with
        | T.Docker d when op = "metadata-command" ->
          let i = T.metadata_invocation ~workers:6 ?pids_limit d ~arguments:args ~environment:env
            ~stdout:(P.Descriptor Unix.stdout) ~stderr:(P.Descriptor Unix.stderr) in
          (T.process_specification i).argv
        | T.Native n -> n.interpreter :: ["-I"; "-B"] @ args
        | T.Docker d -> T.docker_command d ~project ~pdk_root ?workdir ?pdk ~entrypoint
            ?pids_limit ~name:(str (get request "name")) ~uid:(Unix.getuid ()) ~gid:(Unix.getgid ()) ()
            @ (if bool (get request "interpreter") then
                 (match d.flags with Isolated -> ["-I"; "-B"] | Bytecode_disabled -> ["-B"]) else []) @ args in
      obj ["argv", list argv; "environment", list (T.clean_environment env); "binding", T.to_json binding]
    else
      let stdout = P.File (Filename.concat cwd "stdout.log") in
      let stderr = P.File (Filename.concat cwd "stderr.log") in
      let invocation = match binding with
        | T.Native n -> T.native_invocation n ~arguments:args ~cwd ~environment:env ~stdout ~stderr
        | T.Docker d -> T.docker_invocation d ~project ~pdk_root ?workdir ?pdk ~entrypoint
            ~interpreter:(bool (get request "interpreter")) ~arguments:args ~cwd ~environment:env ~stdout ~stderr () in
      let cancel () = if op = "cancel" then Some Sys.sigterm
        else if op = "cancel-failure" then failwith "cancellation callback failed" else None in
      let rejects f = try ignore (f ()); failwith "accepted invalid duration" with Invalid_argument _ -> () in
      List.iter (fun duration ->
          rejects (fun () -> T.run ~timeout:duration invocation);
          rejects (fun () -> T.run ~term_grace:duration invocation);
          rejects (fun () -> T.run ~cleanup_timeout:duration invocation))
        [0.; -1.; Float.nan; Float.infinity];
      let restored = ref 0 in
      let handler _ = incr restored in
      Sys.set_signal Sys.sigint (Sys.Signal_handle handler);
      Sys.set_signal Sys.sigterm (Sys.Signal_handle handler);
      let result = T.run ~timeout:(number (get request "timeout")) ~term_grace:0.03
          ~cleanup_timeout:(number (get request "cleanup_timeout")) ~cancel invocation in
      assert (!restored = 0);
      Unix.kill (Unix.getpid ()) Sys.sigint;
      Unix.kill (Unix.getpid ()) Sys.sigterm;
      assert (!restored = 2);
      let reuse_rejected = try ignore (T.run invocation); false with Invalid_argument _ -> true in
      obj ["outcome", string (outcome result.process.outcome); "succeeded", J.bool (T.succeeded result);
           "handlers_restored", J.bool (!restored = 2);
           "reuse_rejected", J.bool reuse_rejected;
           "cleanup_error", optional result.process.cleanup_error;
           "argv", list (T.process_specification invocation).argv;
           "cleanup", (match result.cleanup with None -> J.null | Some c ->
             obj ["error", optional c.error; "attempts", J.array (List.map (fun (a : T.removal) ->
                 obj ["outcome", string (outcome a.result.outcome); "diagnostic", string a.diagnostic]) c.attempts)])]
let () =
  let request = unwrap (J.parse (input_line stdin)) in
  let result = try main request with Invalid_argument s -> obj ["error", string s] in
  print_endline (J.canonical result)
