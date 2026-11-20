module J = Identity_json
module S = Execution_plan.Stage
type t = { raw:J.t; plan:Execution_plan.Stage_plan.t; occurrences:J.t list;
            references:J.t list; status:string; methodology:Methodology_execution_record.t option }
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String s -> s | _ -> invalid_arg "execution: expected string"
let array = function J.Array xs -> xs | _ -> invalid_arg "execution: expected array"
let checked = function Ok x -> x | Error e -> invalid_arg e
let codec = function Ok x -> x | Error e -> invalid_arg (J.error_message e)
let code = function J.Integer n -> (try int_of_string n with _ -> invalid_arg "invalid execution exit code")
  | _ -> invalid_arg "invalid execution exit code"
let number = function J.Integer n -> float_of_string n | J.Float f -> f | _ -> invalid_arg "invalid execution duration"
let validate_methodology_checkpoints r references =
  let bundle = Hardcaml_asic.Methodology_plan.bundle_identity (Methodology_execution_record.plan r) in
  List.iter (fun reference ->
    let checkpoint = checked (Checkpoint.read (text (get reference "path"))) in
    if get checkpoint.compatibility "bundle_identity" <> codec (J.string bundle)
       || checkpoint.identity <> text (get reference "identity")
       || S.to_string checkpoint.boundary <> text (get reference "boundary")
    then invalid_arg "methodology/checkpoint bundle or publication mismatch";
    ignore (checked (Checkpoint.validate ~compatibility:checkpoint.compatibility (text (get reference "path"))));
    checked (Methodology_execution_service.check_resolved (Methodology_execution_record.plan r)
      (text (get reference "path") ^ "/resolved.json"))) references
let decode raw =
  let fields = match raw with J.Object xs -> xs | _ -> invalid_arg "expected execution object" in
  if List.exists (fun (k,_) -> not (List.mem k ["attempt_id";"plan";"occurrences";"status";"exit_code";"artifacts";"methodology";"timing_intent"])) fields
  then invalid_arg "not a segmented Runner record";
  let attempt = text (get raw "attempt_id") in
  if attempt="" || String.contains attempt '/' || String.contains attempt '\000' then invalid_arg "invalid execution attempt ID";
  let plan = checked (Execution_plan.Stage_plan.of_json (get raw "plan")) in
  let methodology = Option.map (fun r -> checked (Methodology_execution_record.of_json ~attempt_id:attempt ~plan r)) (List.assoc_opt "methodology" fields) in
  (match List.assoc_opt "timing_intent" fields with None -> () | Some expectation ->
    ignore (codec (J.exact_object expectation ~fields:["schema_version";"kind";"attempt_id";"occurrence_id";"profile";"directory";"request";"publication";"companion"]));
    let str s=codec (J.string s) in
    if get expectation "schema_version"<>codec (J.integer "1") ||
       get expectation "kind"<>str "hardcaml-asic.named-timing-expectation" ||
       get expectation "attempt_id"<>str attempt ||
       get expectation "occurrence_id"<>str (attempt^"/implementation.synthesis") ||
       get expectation "profile"<>str "opensta-2.7-native-state-v2" then
      invalid_arg "execution timing expectation contract/attempt/occurrence/profile mismatch";
    if methodology=None || Execution_plan.Stage_plan.schema_version plan<>4 ||
       Execution_plan.Stage_plan.source_boundary plan<>None ||
       Execution_plan.Stage_plan.sequence plan<>[S.Synthesis] then
      invalid_arg "execution timing expectation requires fresh named schema4 synthesis";
    let directory=text (get expectation "directory") in
    if Filename.is_relative directory || checked (Checkpoint.absolute_path directory)<>directory ||
       Filename.basename directory<>attempt || Filename.basename (Filename.dirname directory)<>"timing-intent" then
      invalid_arg "execution timing expectation directory mismatch";
    List.iter (fun (key,name) -> if get expectation key<>str (directory^"/"^name) then
      invalid_arg ("execution timing expectation path mismatch: "^key))
      ["request","request.json";"publication","publication.json";"companion","analysis.json"]);
  let status = text (get raw "status") and exit = code (get raw "exit_code") in
  if not (match status with "completed"|"running" -> exit=0 | "failed" -> exit>0
    | "interrupted" -> exit>128 && exit<=255 | _ -> false) then invalid_arg "inconsistent execution status/exit";
  let occurrences = array (get raw "occurrences") in
  let actions = Execution_plan.Stage_plan.actions plan in
  if List.length occurrences<>List.length actions then invalid_arg "execution occurrence count/order mismatch";
  let blocked = ref false in
  List.iter2 (fun (a:Execution_plan.Action.t) o ->
    ignore (codec (J.exact_object o ~fields:["occurrence_id";"action_key";"status";"exit_code";"duration_seconds";"best_effort";"error";"verdict"]));
    if text (get o "action_key")<>a.key || text (get o "occurrence_id")<>attempt^"/"^a.key
    then invalid_arg "execution occurrence identity/order mismatch";
    if get o "best_effort"<>J.bool false || get o "verdict"<>J.null then invalid_arg "invalid segmented occurrence verdict/best_effort";
    (match get o "error" with J.Null | J.String _ -> () | _ -> invalid_arg "invalid occurrence error");
    let duration=number (get o "duration_seconds") in
    if not (Float.is_finite duration) || duration<0. then invalid_arg "invalid execution duration";
    let st=text (get o "status") and ex=get o "exit_code" in
    let terminal = match st with
      | "completed" -> ex=codec (J.integer "0")
      | "failed" -> code ex>0
      | "interrupted" -> let n=code ex in n>128 && n<=255
      | "running" | "unreached" -> ex=J.null && duration=0.
      | _ -> false in
    if not terminal then invalid_arg "inconsistent occurrence status/exit/duration";
    if (st="failed" || st="interrupted") && st<>status then invalid_arg "occurrence/outer execution status mismatch";
    if List.mem st ["completed";"running";"unreached"] && get o "error"<>J.null then invalid_arg "unexpected occurrence error";
    if !blocked && st<>"unreached" then invalid_arg "execution occurrences out of order";
    if st<>"completed" then blocked:=true;
    if status="completed" && st<>"completed" then invalid_arg "completed execution has incomplete occurrence") actions occurrences;
  (* Runner persists outer failure/cancellation before finalizing the active
     occurrence after a clock or lifecycle-hook error. Completed records still
     require every occurrence to be completed above. *)
  let references=match List.assoc_opt "artifacts" fields with None -> [] | Some v -> array v in
  let next=ref 0 in
  List.iter (fun r ->
    let boundary=match S.of_string (text (get r "boundary")) with Ok b -> b | Error _ -> invalid_arg "invalid reference boundary" in
    let reference=checked (Runner.reference ~boundary ~path:(text (get r "path")) ~identity:(text (get r "identity"))) in
    if not (Runner.reference_matches ~legacy_routing:(Execution_plan.Stage_plan.schema_version plan < 4) reference r)
    then invalid_arg "invalid checkpoint reference capabilities";
    let rec locate i = if i>=List.length actions then invalid_arg "checkpoint reference order/association mismatch"
      else let a=List.nth actions i in
        if a.stage=Execution_plan.Action.Physical boundary then i else locate (i+1) in
    let i=locate !next in
    (* Publication can precede an interruption, failed persistence, or the final
       occurrence update. It cannot belong to an unreached stage. *)
    if get (List.nth occurrences i) "status"=codec (J.string "unreached") then invalid_arg "checkpoint reference belongs to unreached occurrence";
    next:=i+1) references;
  List.iter2 (fun (a:Execution_plan.Action.t) o ->
    if get o "status"=codec (J.string "completed") && not (List.exists (fun r ->
      match a.stage with Physical b -> get r "boundary"=codec (J.string (S.to_string b)) | _ -> false) references)
    then invalid_arg "completed stage missing checkpoint publication") actions occurrences;
  let advertised=match methodology with None -> false | Some r ->
    let plan=Hardcaml_asic.Methodology_plan.to_yojson (Methodology_execution_record.plan r) in
    match plan with `Assoc fields -> (match List.assoc_opt "bundle_manifest" fields with
      | Some (`String raw) ->
        let m=Yojson.Safe.from_string raw in Timing_intent_flow.expected m
      | _ -> false) | _ -> false in
  if List.mem_assoc "timing_intent" fields || advertised then
    List.iter (fun reference -> Timing_intent_inventory.admit_checkpoint (text (get reference "path"))) references;
  Option.iter (fun r -> validate_methodology_checkpoints r references) methodology;
  {raw;plan;occurrences;references;status;methodology}
let protect f = try Ok (f ()) with Invalid_argument s | Failure s -> Error s
let check_methodology_checkpoints r references = protect (fun () -> validate_methodology_checkpoints r references)
let of_json raw = protect (fun () -> decode raw)
let read path = match Result_store.read_json path with
  | Error e -> Error (J.error_message e) | Ok j -> of_json j
let validate_snapshot ?(admit_metadata=(fun _ -> ())) raw =
  try
    (match get raw "artifacts" with J.Array refs ->
      if List.length refs>7 then invalid_arg "execution checkpoint count bound exceeded";
      List.iter (fun reference -> List.iter (fun name ->
        admit_metadata (Filename.concat (text (get reference "path")) name))
        ["manifest.json";"segment.json";"state.json";"resolved.json";"closure.json"]) refs | _ -> ());
    let record=checked (of_json raw) in
    let compatibility=ref None in
    let checkpoints=List.map (fun reference ->
      let path=text (get reference "path") in
      let m=checked (Checkpoint.read path) in
      let c=Option.value !compatibility ~default:m.compatibility in
      let m=checked (Checkpoint.validate ~compatibility:c path) in
      if get reference "identity"<>codec (J.string m.identity) ||
         get reference "boundary"<>codec (J.string (S.to_string m.boundary)) then
        invalid_arg "selected checkpoint reference identity/boundary mismatch";
      let actual = Runner.reference_json (Runner.of_checkpoint ~path m) in
      if reference <> actual then (
        (* Older records projected the generic boundary defaults for restricted
           checkpoints. Keep them readable for profiles predating fresh SRAM. *)
        let historical = checked (Runner.reference ~path ~boundary:m.boundary ~identity:m.identity) in
        if get m.raw "macro_profile" = codec (J.string Macro_contract.synthesis_profile) ||
           reference <> Runner.reference_json historical
        then invalid_arg "checkpoint reference disagrees with published capabilities");
      let segment=Evidence_json.read (path^"/segment.json") in
      let bytes=Evidence_json.raw_bytes segment in
      if String.length bytes>32*1024*1024 then invalid_arg "checkpoint segment byte bound exceeded";
      let inventory=match List.assoc_opt "segment.json" m.files with Some r -> r | None -> invalid_arg "validated checkpoint has no segment inventory" in
      if J.sha256_bytes bytes<>inventory.sha256 then invalid_arg "checkpoint segment bytes differ from validated inventory SHA-256";
      let segment=codec (J.parse bytes) in
      if get segment "metrics"<>m.metrics then invalid_arg "checkpoint manifest/segment metrics mismatch";
      if get segment "boundary"<>codec (J.string (S.to_string m.boundary)) then invalid_arg "checkpoint segment boundary mismatch";
      compatibility:=Some m.compatibility;path,m) record.references in
    List.iter (fun name ->
      let hashes=List.map (fun (path,_) -> (checked (Checkpoint.file_record (path^"/"^name))).sha256) checkpoints in
      match hashes with [] -> () | h::rest -> if not (List.for_all ((=) h) rest) then
        invalid_arg ("selected checkpoints have different "^name)) ["closure.json";"resolved.json"];
    Ok (record,checkpoints)
  with Invalid_argument e | Failure e | Sys_error e -> Error e
     | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
let path ~workspace ~attempt_id =
  if attempt_id="" || String.contains attempt_id '/' || String.contains attempt_id '\000'
  then invalid_arg "invalid execution attempt ID";
  Filename.concat workspace ("execution-" ^ attempt_id ^ ".json")
let available ~workspace ~attempt_id ~methodology = protect (fun () ->
  let path = path ~workspace ~attempt_id in
  match Unix.lstat path with
  | exception Unix.Unix_error (Unix.ENOENT,_,_) -> None
  | stat ->
    if stat.Unix.st_kind <> Unix.S_REG then invalid_arg "execution reference is not a regular file";
    (* Preparation reserves an empty file; reservation is not readable evidence. *)
    if stat.Unix.st_size=0 then None else (
      let record = checked (read path) in
      if get record.raw "attempt_id" <> codec (J.string attempt_id)
         || get record.raw "methodology" <> Methodology_execution_record.to_json methodology
      then invalid_arg "execution reference/allocated methodology mismatch";
      Some (Unix.realpath path)))
