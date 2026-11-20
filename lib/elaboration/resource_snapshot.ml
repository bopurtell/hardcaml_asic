open! Core
open! Hardcaml
module J = Preparation_json
type t = Yojson.Safe.t
let to_json t = t
let requirement : Resource.Policy.Requirement.t -> string = function
  | Flops -> "flops" | Exact -> "exact" | Exact_or_flop_fallback -> "exact_or_flop_fallback"
;;
let policy : Resource.Policy.Applied.t -> Yojson.Safe.t = function
  | Default -> J.obj ["kind", J.str "default"]
  | Rule (Instance id) -> J.obj ["kind", J.str "instance"; "resource", J.str (Resource.Id.to_string id)]
  | Rule (Subtree path) -> J.obj ["kind", J.str "subtree"; "path", J.arr J.str path]
;;
let selection (s : Resource.Selection.t) =
  let implementation = match s.implementation with
    | Flops -> J.obj ["kind", J.str "flops"]
    | Macro name -> J.obj ["kind", J.str "macro"; "name", J.str name] in
  let reason = match s.reason with
    | Explicit_flops -> J.obj ["kind", J.str "explicit_flops"]
    | Exact_mapping { technology } -> J.obj ["kind", J.str "exact_mapping"; "technology", J.str technology]
    | Flop_fallback { no_exact_mapping_in } -> J.obj ["kind", J.str "flop_fallback"; "technology", J.str no_exact_mapping_in]
    | Budget_exhausted { macro; limit } ->
      J.obj ["kind", J.str "budget_exhausted"; "macro", J.str macro; "limit", `Int limit] in
  J.obj ["implementation", implementation; "reason", reason]
;;
let collateral (c : Resource.Technology.Collateral.t) =
  let role, corner = match c.role with
    | Simulation_model -> "simulation_model", `Null
    | Timed_simulation_model -> "timed_simulation_model", `Null
    | Black_box -> "black_box", `Null
    | Timing_model { corner } -> "timing_model", J.str corner
    | Physical_abstract -> "physical_abstract", `Null
    | Physical_layout -> "physical_layout", `Null
    | Spice_netlist -> "spice_netlist", `Null in
  J.obj ["role", J.str role; "corner", corner; "reference", J.str c.reference;
         "verification", J.str "unresolved_reference"]
;;
let source : Resource.Record.Source.t -> Yojson.Safe.t = function
  | Generated_behavioral_model -> J.obj ["kind", J.str "generated_behavioral_model"]
  | Generated_synthesis_rtl -> J.obj ["kind", J.str "generated_synthesis_rtl"]
  | Collateral c -> J.obj ["kind", J.str "collateral"; "collateral", collateral c]
;;
let unavailable status = J.obj ["status", J.str status]
let intent_json ~resolved (i : Flow.Macro_intent.t) =
  J.obj ["status", J.str (if resolved then "resolved_intent" else "declared_intent");
    "location_um", `List [`Float i.x_um; `Float i.y_um]; "halo_um", `Float i.halo_um;
    "orientation", J.str "R0";
    "supplies", J.obj ["VDD!", J.str "VPWR"; "VDDARRAY!", J.str "VPWR"; "VSS!", J.str "VGND"]]
;;
let make ?resolution p =
  let resources = Elaborated_project.resources p in
  let intents = match (Elaborated_project.flow p).adapter with Librelane s -> s.macros in
  let entries = List.map resources ~f:(fun (r : Resource.Record.t) ->
    let checked_macro = Option.bind resolution ~f:(fun (resolved : Resolved.t) ->
      List.find resolved.macros ~f:(fun m -> Resource.Id.equal m.resource r.id)) in
    let instances = match checked_macro with
      | Some m -> J.obj ["status", J.str "rtl_verified"; "instances", `List [
          J.obj ["module", J.str m.name; "instance", J.str m.instance]]]
      | None -> unavailable (match r.selection.implementation, r.elaborated_as with
          | Flops, _ | _, Behavioral_model -> "not_applicable"
          | Macro _, Selected_implementation -> "unavailable") in
    let intent = match checked_macro with
      | Some { intent = Some i; _ } -> intent_json ~resolved:true i
      | _ -> (match List.find intents ~f:(fun i -> Resource.Id.equal i.resource r.id) with
          | Some i -> intent_json ~resolved:false i
          | None -> unavailable "unavailable") in
    let collateral_entries = List.filter_map r.sources ~f:(function
      | Collateral c -> Some c | _ -> None) in
    J.obj ["id", J.obj ["path", J.arr J.str r.id.path; "name", J.str r.id.name;
                         "display", J.str (Resource.Id.to_string r.id)];
      "request", J.obj ["kind", J.str r.request.kind; "contract", J.sexp r.request.contract];
      "requirement", J.str (requirement r.requirement); "policy", policy r.policy;
      "selection", selection r.selection;
      "elaborated_as", J.str (match r.elaborated_as with Behavioral_model -> "behavioral_model" | Selected_implementation -> "selected_implementation");
      "sources", J.arr source r.sources; "actual_instances", instances;
      "collateral", J.arr collateral collateral_entries; "physical_intent", intent]) in
  J.obj ["schema_version", `Int 1; "project", J.str (Elaborated_project.project_name p);
    "top", J.str (Circuit.name (Elaborated_project.circuit p));
    "mode", J.str (match Elaborated_project.mode p with Simulation -> "simulation" | Implementation -> "implementation");
    "scope", J.str (if Option.is_some resolution then "resolved" else "elaborated");
    "resources", `List entries]
;;
let of_elaborated p = make p
let of_resolved r = make ~resolution:r r.elaborated_project

let identifier j = let s = J.string j in
  if not (Resource.Id.is_valid_component s) then J.fail "invalid identifier";
  s
;;
let validate_collateral j =
  J.exact j ["role"; "corner"; "reference"; "verification"];
  let role = J.enum (J.get j "role") ["simulation_model"; "timed_simulation_model";
    "black_box"; "timing_model"; "physical_abstract"; "physical_layout"; "spice_netlist"] in
  (if String.equal role "timing_model" then ignore (J.string (J.get j "corner") : string)
   else J.expect (J.get j "corner") `Null);
  ignore (J.string (J.get j "reference") : string);
  J.expect (J.get j "verification") (J.str "unresolved_reference")
;;
let number = function
  | `Float f when Float.is_finite f -> f
  | `Int i -> Float.of_int i
  | _ -> J.fail "expected finite number"
;;
let budget reason =
  J.exact reason ["kind"; "macro"; "limit"];
  let macro = J.string (J.get reason "macro") in
  if String.is_empty (String.strip macro) then J.fail "blank budget macro";
  match J.get reason "limit" with
  | `Int limit when limit >= 0 -> macro, limit
  | _ -> J.fail "invalid macro instance limit"
;;
let of_json = J.checked (fun j ->
  J.exact j ["schema_version"; "project"; "top"; "mode"; "scope"; "resources"];
  J.expect (J.get j "schema_version") (`Int 1);
  ignore (identifier (J.get j "project") : string);
  ignore (J.string (J.get j "top") : string);
  let mode = J.enum (J.get j "mode") ["simulation"; "implementation"] in
  let scope = J.enum (J.get j "scope") ["elaborated"; "resolved"] in
  if String.equal scope "resolved" && not (String.equal mode "implementation") then J.fail "resolved simulation";
  let counts = String.Table.create () and exhausted = ref [] in
  let ids = J.list (J.get j "resources") |> List.map ~f:(fun r ->
    J.exact r ["id"; "request"; "requirement"; "policy"; "selection"; "elaborated_as";
               "sources"; "actual_instances"; "collateral"; "physical_intent"];
    let id = J.get r "id" in
    J.exact id ["path"; "name"; "display"];
    let resource_id = Resource.Id.create_exn
      ~path:(List.map (J.list (J.get id "path")) ~f:identifier)
      ~name:(identifier (J.get id "name")) in
    J.expect (J.get id "display") (J.str (Resource.Id.to_string resource_id));
    let request = J.get r "request" in
    J.exact request ["kind"; "contract"];
    ignore (identifier (J.get request "kind") : string);
    ignore (J.read_sexp (J.get request "contract") : Sexp.t);
    let req = J.enum (J.get r "requirement") ["flops"; "exact"; "exact_or_flop_fallback"] in
    let policy = J.get r "policy" in
    (match J.enum (J.get policy "kind") ["default"; "instance"; "subtree"] with
     | "default" -> J.exact policy ["kind"]
     | "instance" -> J.exact policy ["kind"; "resource"];
       let selected = Resource.Id.of_string_exn (J.string (J.get policy "resource")) in
       if not (Resource.Id.equal selected resource_id) then J.fail "policy instance mismatch"
     | _ -> J.exact policy ["kind"; "path"];
       let path = J.list (J.get policy "path") |> List.map ~f:identifier in
       if List.is_empty path then J.fail "empty policy subtree";
       if not (List.is_prefix resource_id.path ~prefix:path ~equal:String.equal) then J.fail "policy subtree mismatch");
    let sel = J.get r "selection" in J.exact sel ["implementation"; "reason"];
    let impl = J.get sel "implementation" in
    let impl_kind = J.enum (J.get impl "kind") ["flops"; "macro"] in
    (if String.equal impl_kind "macro" then (J.exact impl ["kind"; "name"];
        Hashtbl.incr counts (J.string (J.get impl "name"))) else J.exact impl ["kind"]);
    let reason = J.get sel "reason" in
     let why = J.enum (J.get reason "kind") ["explicit_flops"; "exact_mapping"; "flop_fallback"; "budget_exhausted"] in
     (match why with
      | "explicit_flops" -> J.exact reason ["kind"]
      | "budget_exhausted" -> exhausted := budget reason :: !exhausted
      | _ -> J.exact reason ["kind"; "technology"]; ignore (J.string (J.get reason "technology") : string));
    let valid_selection = match req, impl_kind, why with
      | "flops", "flops", "explicit_flops"
      | ("exact" | "exact_or_flop_fallback"), "macro", "exact_mapping"
       | "exact_or_flop_fallback", "flops", ("flop_fallback" | "budget_exhausted") -> true | _ -> false in
    if not valid_selection then J.fail "inconsistent selection";
    let role = J.enum (J.get r "elaborated_as") ["behavioral_model"; "selected_implementation"] in
    if not (Bool.equal (String.equal mode "simulation") (String.equal role "behavioral_model")) then J.fail "inconsistent elaboration role";
    let sources = J.get r "sources" |> J.list in
    List.iter sources ~f:(fun s -> match J.enum (J.get s "kind")
        ["generated_behavioral_model"; "generated_synthesis_rtl"; "collateral"] with
      | "collateral" -> J.exact s ["kind"; "collateral"]; validate_collateral (J.get s "collateral")
      | _ -> J.exact s ["kind"]);
    List.iter (J.list (J.get r "collateral")) ~f:validate_collateral;
    let expected_collateral = List.filter_map sources ~f:(fun s ->
      if String.equal (J.string (J.get s "kind")) "collateral" then Some (J.get s "collateral") else None) in
    if not (String.equal (J.bytes (`List expected_collateral)) (J.bytes (J.get r "collateral"))) then J.fail "collateral/source mismatch";
    let expected_source = if String.equal mode "simulation" then Some "generated_behavioral_model"
      else if String.equal impl_kind "flops" then Some "generated_synthesis_rtl" else None in
    (match expected_source with
     | Some kind -> J.expect (J.get r "sources") (`List [J.obj ["kind", J.str kind]])
     | None -> List.iter sources ~f:(fun s -> J.expect (J.get s "kind") (J.str "collateral")));
    let actual = J.get r "actual_instances" in
    let status = J.enum (J.get actual "status") ["not_applicable"; "unavailable"; "rtl_verified"] in
    let applicable = String.equal impl_kind "macro" && String.equal mode "implementation" in
    if not (Bool.equal applicable (not (String.equal status "not_applicable"))) then J.fail "instance applicability mismatch";
    (if String.equal status "rtl_verified" then (
       if not (String.equal scope "resolved") then J.fail "unchecked RTL linkage";
       J.exact actual ["status"; "instances"];
       match J.list (J.get actual "instances") with
       | [i] -> J.exact i ["module"; "instance"];
         J.expect (J.get i "module") (J.get impl "name");
         J.expect (J.get i "instance") (J.str (Resource.Id.rtl_instance_name resource_id))
       | _ -> J.fail "expected one checked macro instance")
     else J.exact actual ["status"]);
    if String.equal scope "resolved" && applicable && not (String.equal status "rtl_verified") then J.fail "missing resolved linkage";
    let intent = J.get r "physical_intent" in
    (match J.enum (J.get intent "status") ["unavailable"; "declared_intent"; "resolved_intent"] with
     | "unavailable" -> J.exact intent ["status"]
     | status ->
       if String.equal status "resolved_intent" && not (String.equal scope "resolved" && applicable) then J.fail "unchecked intent";
       J.exact intent ["status"; "location_um"; "halo_um"; "orientation"; "supplies"];
       (match J.list (J.get intent "location_um") with
        | [x;y] -> ignore (number x : float); ignore (number y : float)
        | _ -> J.fail "invalid location");
       if Float.(number (J.get intent "halo_um") < 0.) then J.fail "negative halo";
       J.expect (J.get intent "orientation") (J.str "R0");
       let supplies = J.get intent "supplies" in
       J.exact supplies ["VDD!"; "VDDARRAY!"; "VSS!"];
       List.iter ["VDD!", "VPWR"; "VDDARRAY!", "VPWR"; "VSS!", "VGND"] ~f:(fun (k,v) -> J.expect (J.get supplies k) (J.str v)));
    resource_id) in
   List.iter !exhausted ~f:(fun (macro, limit) ->
     if Option.value (Hashtbl.find counts macro) ~default:0 <> limit then J.fail "macro count disagrees with exhausted budget");
   if not (List.equal Resource.Id.equal ids (List.dedup_and_sort ids ~compare:Resource.Id.compare)) then J.fail "resource IDs must be unique and sorted";
  j)

let records t =
  let collateral j : Resource.Technology.Collateral.t =
    let role : Resource.Technology.Collateral.Role.t =
      match J.string (J.get j "role") with
      | "simulation_model" -> Simulation_model
      | "timed_simulation_model" -> Timed_simulation_model
      | "black_box" -> Black_box
      | "timing_model" -> Timing_model { corner = J.string (J.get j "corner") }
      | "physical_abstract" -> Physical_abstract
      | "physical_layout" -> Physical_layout
      | "spice_netlist" -> Spice_netlist
      | _ -> assert false
    in
    { role; reference = J.string (J.get j "reference") }
  in
  J.list (J.get t "resources") |> List.map ~f:(fun r ->
    let id = J.get r "id" in
    let id = Resource.Id.create_exn
      ~path:(J.list (J.get id "path") |> List.map ~f:J.string)
      ~name:(J.string (J.get id "name")) in
    let request = J.get r "request" in
    let request = Resource.Request.create_exn ~kind:(J.string (J.get request "kind"))
      ~contract:(J.read_sexp (J.get request "contract")) in
    let requirement : Resource.Policy.Requirement.t = match J.string (J.get r "requirement") with
      | "flops" -> Flops | "exact" -> Exact | "exact_or_flop_fallback" -> Exact_or_flop_fallback
      | _ -> assert false in
    let p = J.get r "policy" in
    let policy : Resource.Policy.Applied.t = match J.string (J.get p "kind") with
      | "default" -> Default
      | "instance" -> Rule (Resource.Policy.Selector.instance_exn (J.string (J.get p "resource")))
      | "subtree" -> Rule (Resource.Policy.Selector.subtree_exn
          (J.list (J.get p "path") |> List.map ~f:J.string |> String.concat ~sep:"/"))
      | _ -> assert false in
    let s = J.get r "selection" in
    let i = J.get s "implementation" in
    let implementation : Resource.Selection.Implementation.t = match J.string (J.get i "kind") with
      | "flops" -> Flops | "macro" -> Macro (J.string (J.get i "name")) | _ -> assert false in
    let why = J.get s "reason" in
    let reason : Resource.Selection.Reason.t = match J.string (J.get why "kind") with
      | "explicit_flops" -> Explicit_flops
      | "exact_mapping" -> Exact_mapping { technology = J.string (J.get why "technology") }
       | "flop_fallback" -> Flop_fallback { no_exact_mapping_in = J.string (J.get why "technology") }
       | "budget_exhausted" -> let macro, limit = budget why in Budget_exhausted { macro; limit }
      | _ -> assert false in
    let elaborated_as : Resource.Record.Elaborated_as.t = match J.string (J.get r "elaborated_as") with
      | "behavioral_model" -> Behavioral_model | "selected_implementation" -> Selected_implementation
      | _ -> assert false in
    let sources = J.list (J.get r "sources") |> List.map ~f:(fun s ->
      match J.string (J.get s "kind") with
      | "generated_behavioral_model" -> Resource.Record.Source.Generated_behavioral_model
      | "generated_synthesis_rtl" -> Generated_synthesis_rtl
      | "collateral" -> Collateral (collateral (J.get s "collateral"))
      | _ -> assert false) in
    { Resource.Record.id; request; requirement; policy;
      selection = { implementation; reason }; elaborated_as; sources })
;;
