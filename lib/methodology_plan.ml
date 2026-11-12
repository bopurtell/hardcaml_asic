open! Core
module J = Flow.Plan_json
module P = Hardcaml_asic_flow_domain.Parameter
module E = Elaboration.Resolved.Effective_parameter

let str s = `String s
let obj xs = `Assoc xs
let arr f xs = `List (List.map xs ~f)

let translation legacy =
  obj
    [ "contract", str "hardcaml-asic.librelane-classic-translation"
    ; "schema_version", `Int 1
    ; "backend_recipe", str "librelane.classic.v1"
    ; "input", str "fresh"
    ; "recipe_schema_version", `Int (if legacy then 3 else 4)
    ; "assignment_encoding", str "backend-key/enum-string/boolean"
    ; "unspecified", str "runtime-backend-default-not-observed"
    ]
;;

let owner (o : Elaboration.Resolved.Setting.Owner.t) =
  match o with
  | Override reason -> obj [ "kind", str "project"; "reason", str reason ]
  | Methodology origin ->
    obj
      [ ( "kind"
        , str
            (match origin with
             | P.Methodology -> "methodology"
             | Profile -> "profile"
             | Request -> "request") )
      ]
  | _ -> J.fail "unsupported typed parameter owner"
;;

let contribution (c : E.contribution) =
  obj
    [ "assignment", Flow.Spec.assignment_to_yojson c.assignment; "owner", owner c.owner ]
;;

let effective ps =
  arr
    (fun (p : E.t) ->
      obj
        [ "winning", contribution p.winning; "overridden", arr contribution p.overridden ])
    ps
;;

let projects ps =
  arr
    (fun (a, reason) ->
      obj [ "assignment", Flow.Spec.assignment_to_yojson a; "reason", str reason ])
    ps
;;

let project_read j =
  J.object_ j ~fields:[ "assignment"; "reason" ];
  ( J.checked (Flow.Spec.assignment_of_yojson (J.get j "assignment"))
  , J.string (J.get j "reason") )
;;

type t =
  { legacy : bool
  ; methodology : Hardcaml_asic_flow_domain.Catalog.resolved
  ; effective_parameters : E.t list
  ; project_assignments : (P.assignment * string) list
  ; project_name : string
  ; bundle_identity : string
  ; recipe : Flow.Recipe.t
  ; manifest : string
  ; configuration : string
  ; methodology_hash : string
  ; plan_identity : string
  }

let methodology t = t.methodology
let effective_parameters t = t.effective_parameters
let project_name t = t.project_name
let bundle_identity t = t.bundle_identity
let recipe t = t.recipe
let methodology_hash t = t.methodology_hash
let plan_identity t = t.plan_identity
let hardware_variant t =
  match Yojson.Safe.from_string t.manifest with
  | `Assoc fields ->
    Option.map (List.Assoc.find fields "hardware_variant" ~equal:String.equal)
      ~f:(fun j -> J.checked (Hardware_variant.of_yojson j))
  | _ -> assert false

let historical_stages = function
  | `Assoc xs ->
    `Assoc
      (List.map xs ~f:(function
        | "stages", `List stages ->
          ( "stages"
          , `List (List.filter stages ~f:(fun s -> not (J.equal s (`String "timing")))) )
        | field -> field))
  | j -> j
;;

let methodology_payload legacy methodology effective_parameters project_assignments recipe
  =
  let snapshot = Flow.Spec.resolved_to_yojson methodology in
  let recipe_json = Flow.Recipe.to_yojson recipe in
  let recipe_json =
    if legacy
    then (
      match recipe_json with
      | `Assoc xs ->
        `Assoc
          (("schema_version", `Int 3)
           :: List.Assoc.remove xs ~equal:String.equal "schema_version")
      | _ -> assert false)
    else recipe_json
  in
  obj
    [ ("snapshot", if legacy then historical_stages snapshot else snapshot)
    ; "project_assignments", projects project_assignments
    ; "effective_parameters", effective effective_parameters
    ; "translation", translation legacy
    ; "recipe", recipe_json
    ]
;;

let payload t =
  obj
    [ "contract", str "hardcaml-asic.methodology-plan"
    ; "schema_version", `Int 1
    ; ( "methodology"
      , methodology_payload
          t.legacy
          t.methodology
          t.effective_parameters
          t.project_assignments
          t.recipe )
    ; "methodology_hash", str t.methodology_hash
    ; "project_name", str t.project_name
    ; "bundle_identity", str t.bundle_identity
      (* String deliberately retains Bundle v1 object order, whose hash is NOT our
         sorted-object identity algorithm. These are compact, exact legacy JSON. *)
    ; "bundle_manifest", str t.manifest
    ; "configuration_bytes", str t.configuration
    ]
;;

let to_yojson t =
  match payload t with
  | `Assoc xs -> obj (xs @ [ "plan_identity", str t.plan_identity ])
  | _ -> assert false
;;

let to_string t = J.checked (J.canonical (to_yojson t))

let verify_associations
  ~project_name
  ~bundle_identity
  ~manifest
  ~configuration
  effective_parameters
  =
  let m = Yojson.Safe.from_string manifest in
  let snapshot_fields =
    match m with
    | `Assoc xs ->
      List.filter [ "resource_snapshot"; "timing_intent"; "timing_native_coverage"; "hardware_variant"; "macro_contract" ]
        ~f:(fun field -> List.Assoc.mem xs field ~equal:String.equal)
    | _ -> []
  in
  J.object_
    m
    ~fields:
      ([ "identity"
      ; "schema_version"
      ; "project"
      ; "top_module"
      ; "source_revision"
      ; "source_inputs"
      ; "files"
      ; "synthesis_sources"
      ; "simulation_sources"
      ; "resources"
      ; "simulation_resources"
      ; "target"
      ; "adapter"
      ; "operation"
      ; "resolved_settings"
      ; "flow_configuration"
      ; "generated_settings"
      ; "requested_tools"
     ]
       @ snapshot_fields);
  if not (String.equal manifest (Yojson.Safe.to_string m))
  then J.fail "noncompact bundle manifest";
  J.checked (Elaboration.Bundle.verify_manifest_identity m);
  if (not (String.equal (J.string (J.get m "identity")) bundle_identity))
     || (not (String.equal (J.string (J.get m "project")) project_name))
     || (not (J.equal (J.get m "schema_version") (`Int 1)))
     || not (J.equal (J.get m "adapter") (str "LibreLane"))
  then J.fail "bundle/project association mismatch";
  let config = Yojson.Safe.from_string configuration in
  J.validate config;
  if not (J.equal config (J.get m "flow_configuration"))
  then J.fail "bundle configuration mismatch";
  J.checked (Flow.Methodology_inventory.verify ~manifest:m ~configuration);
  let settings = J.array (J.get m "resolved_settings") in
  let keys =
    List.map settings ~f:(fun s ->
      J.object_ s ~fields:[ "key"; "value"; "owner" ];
      J.string (J.get s "key"))
  in
  if Option.is_some (List.find_a_dup keys ~compare:String.compare)
  then J.fail "duplicate resolved setting";
  let floorplan =
    List.hd_exn (J.array (J.get (J.get m "target") "external_references"))
  in
  let floorplan_path = J.string (J.get floorplan "path") in
  List.iter settings ~f:(fun s ->
    let key = J.string (J.get s "key") in
    let value =
      if String.equal key "FP_DEF_TEMPLATE"
      then (
        if not (J.equal (J.get s "value") (str floorplan_path))
        then J.fail "floorplan setting/target mismatch";
        str ("dir::../tt/" ^ floorplan_path))
      else J.get s "value"
    in
    if not (J.equal value (J.get config key))
    then J.fail "resolved setting/configuration mismatch");
   let generated =
     obj
       ([ "VERILOG_FILES", `List [ str ("dir::" ^ J.string (J.get m "top_module") ^ ".v") ]
      ; "PNR_SDC_FILE", str "dir::../constraints/top.sdc"
      ; "SIGNOFF_SDC_FILE", str "dir::../constraints/top.sdc"
       ] @ Elaboration.Macro_bundle_inputs.generated m)
  in
  if not (J.equal generated (J.get m "generated_settings"))
  then J.fail "generated configuration mismatch";
   let generated_keys=match generated with `Assoc xs -> List.map xs ~f:fst | _ -> assert false in
   J.object_ config ~fields:(keys @ generated_keys);
   List.iter generated_keys ~f:(fun k ->
    if not (J.equal (J.get config k) (J.get generated k))
    then J.fail "generated setting/config mismatch");
  List.iter effective_parameters ~f:(fun (p : E.t) ->
    let c = p.winning in
    let a = Flow.Spec.assignment_to_yojson c.assignment in
    let key = J.string (J.get a "key") in
    let s =
      List.find_exn settings ~f:(fun s -> String.equal (J.string (J.get s "key")) key)
    in
    let expected_owner =
      Sexp.to_string_hum (Elaboration.Resolved.Setting.Owner.sexp_of_t c.owner)
    in
    if (not (J.equal (J.get a "value") (J.get s "value")))
       || not (String.equal expected_owner (J.string (J.get s "owner")))
    then J.fail "effective parameter/bundle provenance mismatch");
  (* Configurable absent keys must also be absent from the emitted settings; checked below
     against the selected definition. *)
  m
;;

let make
  ~legacy
  ~methodology
  ~project_assignments
  ~project_name
  ~bundle_identity
  ~manifest
  ~configuration
  =
  let project_assignments =
    List.sort project_assignments ~compare:(fun (a, _) (b, _) ->
      String.compare
        (P.Key.to_string (P.assignment_key a))
        (P.Key.to_string (P.assignment_key b)))
  in
  let effective_parameters =
    J.checked
      (Elaboration.Resolved.resolve_effective_parameters
         ~methodology
         ~project_assignments)
  in
  let stages = Hardcaml_asic_flow_domain.Catalog.stages methodology in
  let recipe =
    J.checked
      (if legacy
       then
         Flow.Recipe.decode_legacy
           (List.filter stages ~f:(fun s -> not (Flow.Stage.equal s Flow.Stage.Timing)))
       else Flow.Recipe.create stages)
  in
   let m =
    verify_associations
      ~project_name
      ~bundle_identity
      ~manifest
      ~configuration
       effective_parameters
   in
   if List.Assoc.mem (match m with `Assoc xs -> xs | _ -> []) "macro_contract" ~equal:String.equal then (
     if legacy || List.mem stages Flow.Stage.Signoff ~equal:Flow.Stage.equal then
       J.fail "named segmented SRAM Signoff is unsupported; maximum endpoint is Timing";
     if List.exists ["timing_intent";"timing_native_coverage";"timing_physical_qualification"]
       ~f:(fun key -> List.Assoc.mem (match m with `Assoc xs -> xs | _ -> []) key ~equal:String.equal)
     then J.fail "named segmented SRAM does not support PT7 linked timing intent or exceptions");
  let configurable =
    Hardcaml_asic_flow_domain.Methodology.configurable
      (Hardcaml_asic_flow_domain.Catalog.Entry.methodology
         (Hardcaml_asic_flow_domain.Catalog.entry methodology))
  in
  List.iter configurable ~f:(fun key ->
    let assigned =
      List.exists effective_parameters ~f:(fun p ->
        P.Key.equal key (P.assignment_key p.E.winning.assignment))
    in
    let emitted =
      List.exists
        (J.array (J.get m "resolved_settings"))
        ~f:(fun s -> String.equal (P.Key.to_string key) (J.string (J.get s "key")))
    in
    if not (Bool.equal assigned emitted) then J.fail "unspecified/effective mismatch");
  let methodology_hash =
    J.checked
      (J.identity
         ~contract:"hardcaml-asic.methodology-hash"
         (methodology_payload
            legacy
            methodology
            effective_parameters
            project_assignments
            recipe))
  in
  let t =
    { legacy
    ; methodology
    ; effective_parameters
    ; project_assignments
    ; project_name
    ; bundle_identity
    ; recipe
    ; manifest
    ; configuration
    ; methodology_hash
    ; plan_identity = ""
    }
  in
  { t with
    plan_identity =
      J.checked (J.identity ~contract:"hardcaml-asic.plan-identity" (payload t))
  }
;;

let of_yojson j =
  J.decode (fun () ->
    J.envelope
      j
      ~contract:"hardcaml-asic.methodology-plan"
      ~fields:
        [ "methodology"
        ; "methodology_hash"
        ; "project_name"
        ; "bundle_identity"
        ; "bundle_manifest"
        ; "configuration_bytes"
        ; "plan_identity"
        ];
    let md = J.get j "methodology" in
    J.object_
      md
      ~fields:
        [ "snapshot"
        ; "project_assignments"
        ; "effective_parameters"
        ; "translation"
        ; "recipe"
        ];
    let t =
      make
        ~legacy:(J.equal (J.get (J.get md "recipe") "schema_version") (`Int 3))
        ~methodology:(J.checked (Flow.Spec.resolved_of_yojson (J.get md "snapshot")))
        ~project_assignments:
          (List.map (J.array (J.get md "project_assignments")) ~f:project_read)
        ~project_name:(J.string (J.get j "project_name"))
        ~bundle_identity:(J.string (J.get j "bundle_identity"))
        ~manifest:(J.string (J.get j "bundle_manifest"))
        ~configuration:(J.string (J.get j "configuration_bytes"))
    in
    if not (J.equal j (to_yojson t))
    then J.fail "plan identities/evidence differ from checked reconstruction";
    t)
;;

let of_string s = J.decode (fun () -> J.checked (of_yojson (Yojson.Safe.from_string s)))

let of_rendered rendered =
  J.decode (fun () ->
    let r = Project.Rendered.resolution rendered in
    let b = Project.Rendered.bundle rendered in
    let methodology =
      match r.methodology with
      | Some m -> m
      | None -> J.fail "plan requires checked methodology"
    in
    let configurable =
      Hardcaml_asic_flow_domain.Methodology.configurable
        (Hardcaml_asic_flow_domain.Catalog.Entry.methodology
           (Hardcaml_asic_flow_domain.Catalog.entry methodology))
    in
    let project_assignments =
      match (Elaboration.Elaborated_project.flow r.elaborated_project).adapter with
      | Flow.Config.Adapter.Librelane settings ->
        List.filter_map settings.overrides ~f:(fun o ->
          match P.Key.of_string o.key with
          | Ok k when List.mem configurable k ~equal:P.Key.equal ->
            Some
              ( J.checked
                  (Flow.Spec.assignment_of_yojson
                     (obj [ "key", str o.key; "value", o.value ]))
              , o.reason )
          | _ -> None)
    in
    let configuration = Option.value_exn (Elaboration.Bundle.file b "src/config.json") in
    let t =
      make
        ~legacy:false
        ~methodology
        ~project_assignments
        ~project_name:(Elaboration.Elaborated_project.project_name r.elaborated_project)
        ~bundle_identity:(Elaboration.Bundle.identity b)
        ~manifest:(Yojson.Safe.to_string (Elaboration.Bundle.manifest b))
        ~configuration
    in
    if not (J.equal (effective r.effective_parameters) (effective t.effective_parameters))
    then J.fail "rendered resolution/effective replay mismatch";
    t)
;;

type t_plan = t

module Prepared = struct
  type t =
    { plan : t_plan
    ; bundle : Elaboration.Bundle.t
    }

  let plan t = t.plan
  let bundle t = t.bundle
end

let prepare
  ?inputs
  ?selection
  ?request
  ?include_simulation
  ?catalog_inputs
  ?expected_plan_identity
  ~catalog
  ~endpoint
  project
  ~source_root
  =
  let open Or_error.Let_syntax in
  let%bind methodology =
    Hardcaml_asic_flow_domain.Catalog.resolve ?selection ?request catalog ~endpoint ()
  in
  let%bind rendered =
    Project.render_result
      ?inputs
      ~methodology
      ?include_simulation
      ?catalog_inputs
      project
      ~source_root
  in
  let%bind plan = of_rendered rendered in
  let%map () =
    match expected_plan_identity with
    | None -> Ok ()
    | Some expected when String.equal expected plan.plan_identity -> Ok ()
    | Some expected ->
      Or_error.error_s
        [%message
          "stale methodology plan identity"
            (expected : string)
            ~actual:(plan.plan_identity : string)]
  in
  { Prepared.plan; bundle = Project.Rendered.bundle rendered }
;;
