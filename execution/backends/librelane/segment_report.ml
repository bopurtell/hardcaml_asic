module J = Identity_json
module Stage = Execution_plan.Stage
type metric = Available of J.t | Unavailable of { reason : string; raw : string }
type t = { boundary : Stage.t; metrics : (string * metric) list; raw : J.t }
exception Invalid of string
let fail s = raise (Invalid s)
let codec = function Ok x -> x | Error e -> fail (J.error_message e)
let str s = codec (J.string s)
let text = function J.String s -> s | _ -> fail "report string required"
let exact j fields = codec (J.exact_object j ~fields)
let of_json ~(request:Backend_protocol.request) raw = try
   let macro=match raw with J.Object xs -> List.mem_assoc "macro_profile" xs | _ -> false in
   let xs = exact raw (["status"; "boundary"; "from"; "source_boundary"; "to"; "run_dir";
     "returned_steps"; "bridge_sha256"; "metrics"; "metric_origin"; "configured_gates";
     "gate_evidence"; "acceptance" ] @ if macro then ["macro_profile";"macro_evidence"] else []) in
   let get k = List.assoc k xs in
   if macro then (
     if get "macro_profile" <> str Macro_contract.segmented_profile then fail "unsupported macro segment profile";
     match Checkpoint.macro_evidence ~boundary:request.boundary (get "macro_evidence") with
     | Ok _ -> () | Error e -> fail e);
  let expect k v = if get k <> v then fail ("inconsistent segment report: " ^ k) in
  let cap = Stage.capability request.boundary in
  let from = Option.bind request.source_boundary (fun s -> (Stage.capability s).successor) in
  let optional f = function None -> J.null | Some x -> str (f x) in
  expect "status" (str "completed"); expect "boundary" (str (Stage.to_string request.boundary));
  expect "bridge_sha256" (str request.bridge_sha256);
  expect "source_boundary" (optional Stage.to_string request.source_boundary);
  expect "from" (optional Fun.id from); expect "to" (str cap.endpoint);
  expect "acceptance" (str "not_evaluated");
  expect "metric_origin" (str "cumulative_final_state; not per-step measurement provenance");
  expect "gate_evidence" (str "configured Classic gates; returned_steps are successful outer step objects, not body/skip provenance");
  let run_dir = text (get "run_dir") in
  if Filename.is_relative run_dir || String.contains run_dir '\000' then fail "invalid run directory";
  let steps = match get "returned_steps" with J.Array xs -> List.map text xs | _ -> fail "step list required" in
  if steps = [] || List.exists ((=) "") steps || List.hd (List.rev steps) <> cap.endpoint
     || (match from with None -> false | Some s -> List.hd steps <> s)
  then fail "inconsistent returned steps";
  (match get "configured_gates" with J.Object xs -> List.iter (fun (_,v) -> match v with
    J.Object _ -> () | _ -> fail "configured gate object required") xs | _ -> fail "configured gates object required");
  let metrics = match get "metrics" with J.Object xs -> List.map (fun (k,v) ->
    let availability = match v with J.Object xs -> List.assoc_opt "availability" xs | _ -> None in
    let metric = match availability with
      | Some (J.String "available") -> let fields = exact v ["availability"; "value"] in
        let value = List.assoc "value" fields in
        (match value with J.Integer _ | J.Float _ -> Available value | _ -> fail "finite numeric metric required")
      | Some (J.String "unavailable") -> let fields = exact v ["availability"; "reason"; "raw"] in
        let reason = text (List.assoc "reason" fields) and raw = text (List.assoc "raw" fields) in
        if reason <> "nonfinite_or_nonnumeric" then fail "unknown unavailable metric reason";
        Unavailable {reason; raw}
      | _ -> fail "metric availability required" in k,metric) xs
    | _ -> fail "metrics object required" in
  Ok {boundary=request.boundary; metrics; raw}
with Invalid s -> Error s
