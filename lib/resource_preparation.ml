open! Core
module J = Elaboration.Preparation_json

module Phase = struct
  type t = Source_before | Construction | Finalization | Resolution
         | Circuit | Source_after | Snapshot
  [@@deriving compare, equal, sexp_of]
  let to_string = function
    | Source_before -> "source_before" | Construction -> "construction"
    | Finalization -> "finalization" | Resolution -> "resolution"
    | Circuit -> "circuit" | Source_after -> "source_after" | Snapshot -> "snapshot"
  let of_string s =
    match List.find [Source_before; Construction; Finalization; Resolution; Circuit; Source_after; Snapshot]
      ~f:(fun p -> String.equal s (to_string p)) with
    | Some p -> Ok p
    | None -> Or_error.error_string "unsupported resource preparation phase"
end

module Failure = struct
  type diagnostic = { code : string; message : string; detail : Sexp.t }
  type t = { phase : Phase.t; diagnostics : diagnostic list }
  let create ~phase ~code ~message error =
    { phase; diagnostics = [{ code; message; detail = Error.sexp_of_t error }] }
  let to_json t = J.obj ["schema_version", `Int 1; "phase", J.str (Phase.to_string t.phase);
    "diagnostics", J.arr (fun d -> J.obj ["code", J.str d.code;
      "message", J.str d.message; "detail", J.sexp d.detail]) t.diagnostics]
  let of_json = J.checked (fun j ->
    J.exact j ["schema_version"; "phase"; "diagnostics"];
    J.expect (J.get j "schema_version") (`Int 1);
    let phase = Phase.of_string (J.string (J.get j "phase")) |> Or_error.ok_exn in
    let diagnostics = J.list (J.get j "diagnostics") |> List.map ~f:(fun d ->
      J.exact d ["code"; "message"; "detail"];
      let code = J.string (J.get d "code") and message = J.string (J.get d "message") in
      if String.is_empty code || String.is_empty message then J.fail "empty diagnostic";
      {code; message; detail = J.read_sexp (J.get d "detail")}) in
    if List.is_empty diagnostics then J.fail "failure requires diagnostics";
    {phase; diagnostics})
end

type t = { json : Yojson.Safe.t; resources : Elaboration.Resource_snapshot.t }
let to_json t = t.json
let field t key = J.string (J.get t.json key)
let identity t = field t "identity"
let source_identity t = field t "source_identity"
let configuration_identity t = field t "configuration_identity"
let circuit_identity t = field t "circuit_identity"
let resources t = t.resources
let to_string t = J.pretty t.json
let coverage = J.obj ["declared_sources", `Bool true; "compiled_source_proven", `Bool false;
  "p6_source_stamp", `Bool false; "collateral_files_verified", `Bool false;
  "measured_evidence", `Bool false]
let without_identity j = J.obj (List.filter (J.fields j) ~f:(fun (k, _) -> not (String.equal k "identity")))
let verify_hash j key domain payload =
  if not (String.equal (J.digest (J.get j key)) (J.hash domain payload))
  then J.fail (key ^ " mismatch")
;;
(* A snapshot can verify exhaustion against selected macros. Preparation also
   carries the declared caps, so a rehashed explanation must agree with them. *)
let check_macro_limits declaration resources =
  let field key = function
    | Sexp.List xs ->
      (match List.filter_map xs ~f:(function
         | Sexp.List [Atom k; value] when String.equal k key -> Some value | _ -> None) with
       | [] -> None | [v] -> Some v | _ -> J.fail ("duplicate declaration field: " ^ key))
    | _ -> None in
  let limits = field "project" declaration
    |> Option.bind ~f:(field "policy")
    |> Option.bind ~f:(field "macro_limits")
    |> Option.value_map ~default:[] ~f:[%of_sexp: (string * int) list]
    |> Map.of_alist_exn (module String) in
  Map.iteri limits ~f:(fun ~key ~data ->
    if String.is_empty (String.strip key) || data < 0 then J.fail "invalid declaration macro limit");
  let counts = String.Table.create () in
  List.iter (Elaboration.Resource_snapshot.records resources) ~f:(fun r ->
    match r.selection.implementation, r.selection.reason with
    | Macro name, _ ->
      Hashtbl.incr counts name;
      Option.iter (Map.find limits name) ~f:(fun limit ->
        if Hashtbl.find_exn counts name > limit then J.fail "selected macro exceeds declared limit")
    | Flops, Budget_exhausted {macro;limit} ->
      if not (Option.equal Int.equal (Some limit) (Map.find limits macro))
      then J.fail "exhausted limit differs from declaration"
    | Flops, _ -> ())
;;
let of_json = J.checked (fun j ->
  J.exact j ["schema_version"; "kind"; "identity"; "source_identity";
    "configuration_identity"; "circuit_identity"; "source"; "configuration";
    "circuit"; "resources"; "coverage"];
  J.expect (J.get j "schema_version") (`Int 1);
  J.expect (J.get j "kind") (J.str "hardcaml_asic.resource_preparation");
  if not (String.equal (J.bytes (J.get j "coverage")) (J.bytes coverage)) then J.fail "unsupported coverage";
  let source = Elaboration.Source_snapshot.of_json (J.get j "source") |> Or_error.ok_exn in
  J.expect (J.get j "source_identity") (J.str (Elaboration.Source_snapshot.identity source));
  let config = J.get j "configuration" in
  J.exact config ["schema_version"; "operation"; "declaration"; "inputs"; "methodology"; "resolved_settings"];
  J.expect (J.get config "schema_version") (`Int 1);
  J.expect (J.get config "operation") (J.str "elaborate_resolve_resources");
  let declaration = J.read_sexp (J.get config "declaration") in
  ignore (J.read_sexp (J.get config "inputs") : Sexp.t);
  (match J.get config "methodology" with
   | `Null -> ()
   | m -> ignore (Flow.Spec.resolved_of_yojson m |> Or_error.ok_exn : Hardcaml_asic_flow_domain.Catalog.resolved));
  List.iter (J.list (J.get config "resolved_settings")) ~f:(fun s ->
    J.exact s ["key"; "value"; "owner"];
    ignore (J.string (J.get s "key") : string);
    ignore (J.read_sexp (J.get s "owner") : Sexp.t));
  verify_hash j "configuration_identity" "configuration" config;
  let circuit = J.get j "circuit" in
  J.exact circuit ["format"; "rtl"];
  J.expect (J.get circuit "format") (J.str "verilog");
  if String.is_empty (J.string (J.get circuit "rtl")) then J.fail "empty circuit RTL";
  verify_hash j "circuit_identity" "circuit" circuit;
  let resources = Elaboration.Resource_snapshot.of_json (J.get j "resources") |> Or_error.ok_exn in
  check_macro_limits declaration resources;
  J.expect (J.get (Elaboration.Resource_snapshot.to_json resources) "mode") (J.str "implementation");
  J.expect (J.get (Elaboration.Resource_snapshot.to_json resources) "scope") (J.str "resolved");
  verify_hash j "identity" "preparation" (without_identity j);
  {json = j; resources})
;;
let of_string s = J.parse of_json s
module Success = struct
  type nonrec t = t
  let identity = identity
  let source_identity = source_identity
  let configuration_identity = configuration_identity
  let circuit_identity = circuit_identity
  let resources = resources
  let to_json = to_json
  let of_json = of_json
  let to_string = to_string
  let of_string = of_string
end

module Private = struct
  let create ~source ~declaration ~inputs ~(resolution : Elaboration.Resolved.t) ~rtl =
    let configuration = J.obj ["schema_version", `Int 1;
      "operation", J.str "elaborate_resolve_resources";
      "declaration", J.sexp declaration;
      "inputs", J.sexp (Platform.Target.Inputs.sexp_of_t inputs);
      "methodology", Option.value_map resolution.methodology ~default:`Null ~f:Flow.Spec.resolved_to_yojson;
      "resolved_settings", J.arr (fun (s : Elaboration.Resolved.Setting.t) ->
        J.obj ["key", J.str s.key; "value", s.value;
          "owner", J.sexp (Elaboration.Resolved.Setting.Owner.sexp_of_t s.owner)]) resolution.settings] in
    let circuit = J.obj ["format", J.str "verilog"; "rtl", J.str rtl] in
    let resources = Elaboration.Resource_snapshot.of_resolved resolution in
    let payload = J.obj ["schema_version", `Int 1;
      "kind", J.str "hardcaml_asic.resource_preparation";
      "source_identity", J.str (Elaboration.Source_snapshot.identity source);
      "configuration_identity", J.str (J.hash "configuration" configuration);
      "circuit_identity", J.str (J.hash "circuit" circuit);
      "source", Elaboration.Source_snapshot.to_json source;
      "configuration", configuration; "circuit", circuit;
      "resources", Elaboration.Resource_snapshot.to_json resources;
      "coverage", coverage] in
    of_json (J.obj (("identity", J.str (J.hash "preparation" payload)) :: J.fields payload))
end
