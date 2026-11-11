module J = Identity_json
type status = Running | Completed | Failed | Interrupted
type acceptance = Not_evaluated | Pass | Fail
type t = { attempt_id:string;status:status;acceptance:acceptance;exit_code:Z.t option;raw:J.t }
let contract = "hardcaml-asic.segment-finalization"
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String x -> x | _ -> invalid_arg "malformed finalization receipt"
let of_json raw = try
  let required=["contract";"schema_version";"attempt_id";"status";"phase";"started_at";"ended_at";
    "acceptance";"external_postcheck";"request";"commands";"results";"error"] in
  let optional=["exit_code";"execution";"binding";"selected_checkpoints";"runtime_validation";
    "selected_evidence";"mapped_files";"adapter";"evidence_context";"postcheck_collateral";
    "postcheck_record";"postcheck_sha256";"acceptance_report";"container_cleanup";"client_termination";
    "timing_analysis";"execution_envelope"] in
  let fields=match raw with J.Object xs -> xs | _ -> invalid_arg "malformed finalization receipt" in
  let malformed () = invalid_arg "malformed finalization receipt" in
  if not (List.for_all (fun k -> List.mem_assoc k fields) required)
    || List.exists (fun (k,_) -> not (List.mem k (required@optional))) fields then malformed ();
  if text (get raw "contract")<>contract || get raw "schema_version"<>(match J.integer "1" with Ok j->j | Error _->assert false) then malformed ();
  let attempt_id=text (get raw "attempt_id") in
  let request=get raw "request" in
  (match request,get request "accept",get raw "commands" with J.Object _,J.Bool _,J.Array _ -> () | _ -> malformed ());
  let status=match text (get raw "status") with "running"->Running | "completed"->Completed | "failed"->Failed | "interrupted"->Interrupted | _->malformed () in
  if not (List.mem (text (get raw "phase")) ["validation";"collection";"external_postcheck";"acceptance"]) then malformed ();
  let external_=text (get raw "external_postcheck") in
  if not (List.mem external_ ["not_evaluated";"running";"completed";"failed";"interrupted"]) then malformed ();
  let acceptance=match text (get raw "acceptance") with "not_evaluated"->Not_evaluated | "pass"->Pass | "fail"->Fail | _->malformed () in
  let terminal=status<>Running in
  if terminal<>(get raw "ended_at"<>J.null) || terminal<>List.mem_assoc "exit_code" fields then invalid_arg "inconsistent finalization lifecycle";
  let exit_code=if terminal then match get raw "exit_code" with J.Integer x ->
    let n=Z.of_string x in if (status=Completed)<>(Z.equal n Z.zero) then invalid_arg "inconsistent finalization exit status"; Some n
    | _ -> invalid_arg "inconsistent finalization exit status" else None in
  if status=Completed && get raw "results"=J.null then invalid_arg "completed finalization has no results";
  if acceptance=Pass && (get request "accept"<>J.bool true || external_<>"completed" || status<>Completed) then invalid_arg "unearned finalization acceptance";
  Ok {attempt_id;status;acceptance;exit_code;raw}
  with Invalid_argument s | Failure s -> Error s
let read directory = match Result_store.read_json (directory^"/operation.json") with
  | Ok j -> of_json j | Error e -> Error (J.error_message e)
let to_json t = t.raw
let render ~directory t =
  let b=Buffer.create 1024 in
  let value missing = function J.Null -> missing | J.Bool true -> "yes" | J.Bool false -> "no" | J.String "" -> "empty" | J.String x -> x | j -> J.canonical j in
  let field ?(missing="not recorded") label j = Buffer.add_string b (Printf.sprintf "%-22s %s\n" label (value missing j)) in
  let str x=match J.string x with Ok j->j | Error _->assert false in
  let r=t.raw and request=get t.raw "request" in
  field "finalization" (str directory); field "attempt id" (get r "attempt_id");
  field "operation receipt" (str (directory^"/operation.json"));
  field ~missing:"not recorded (nonterminal)" "status" (get r "status"); field "phase" (get r "phase");
  field ~missing:"not recorded (nonterminal)" "exit" (get r "exit_code");
  field "requested execution" (get request "execution"); field "acceptance requested" (get request "accept");
  field ~missing:"not established" "runtime validation" (get r "runtime_validation");
  Buffer.add_string b "\nSELECTED CHECKPOINTS (historical publication)\n";
  (match get r "selected_checkpoints" with J.Array (_::_ as xs) -> List.iter (fun j -> Buffer.add_string b
    (Printf.sprintf "  %s: %s\n" (value "not recorded" (get j "boundary")) (value "not recorded" (get j "path")))) xs
   | _ -> Buffer.add_string b "  none recorded\n");
  Buffer.add_string b "\nRESULTS AND ACCEPTANCE\n";
  field ~missing:"not published" "results" (get (get r "results") "path");
  field "external postcheck" (get r "external_postcheck"); field "postcheck record" (get r "postcheck_record");
  field "acceptance" (get r "acceptance"); field "acceptance report" (get (get r "acceptance_report") "path");
  if get r "error"<>J.null then field "operation error" (get r "error");
  Buffer.add_string b "\nCompletion of collection alone does not establish acceptance.\nInterrupted means cancellation/interruption, not an acceptance rejection.\n";
  Buffer.contents b
