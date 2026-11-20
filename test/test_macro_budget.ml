open! Core
open! Hardcaml_asic

let macro = "fixture_macro_8"
let other = "fixture_macro_16"
let technology_name = "budget_fixture"

let technology =
  let mapping width name : Resource.Technology.Resource_mapping.t =
    { request = Fixture.Store.request ~width; macro = { name; collateral = [] } }
  in
  Resource.Technology.create_exn
    ~name:technology_name
    ~resource_mappings:[ mapping 8 macro; mapping 16 other ]
;;

let project ?(design = Fixture.design ()) ?macro_limits ?(rules = []) default =
  let policy = Resource_policy.create ~default ?macro_limits rules |> Or_error.ok_exn in
  Fixture.project ~design ~technology ~policy () |> Or_error.ok_exn
;;

let selections project mode =
  let elaborated = Project.elaborate project ~mode |> Or_error.ok_exn in
  Elaborated_project.resources elaborated
  |> List.map ~f:(fun (r : Resource.Record.t) -> Resource.Id.to_string r.id, r.selection)
  |> List.sort ~compare:(Comparable.lift String.compare ~f:fst)
;;

let exact name : Resource.Selection.t =
  { implementation = Macro name; reason = Exact_mapping { technology = technology_name } }
;;

let exhausted macro limit : Resource.Selection.t =
  { implementation = Flops; reason = Budget_exhausted { macro; limit } }
;;

let check project mode expected =
  [%test_eq: (string * Resource.Selection.t) list] (selections project mode) expected
;;

let%test_unit "registration order allocates the budget afresh for each elaboration" =
  let design = Fixture.design ~order:`Reverse () in
  let p = project ~design ~macro_limits:[ macro, 1 ] Exact_or_flop_fallback in
  let expected =
    [ "left/buf", exhausted macro 1
    ; "right/buf", exhausted macro 1
    ; "status", exact macro
    ]
  in
  List.iter
    [ Elaboration.Mode.Simulation; Implementation; Implementation ]
    ~f:(fun mode -> check p mode expected)
;;

let%test_unit "earlier fallback-eligible allocation is not reserved for later Exact" =
  let rules =
    [ { Resource_policy.Rule.selector = Resource_policy.Selector.instance_exn "right/buf"
      ; requirement = Exact
      }
    ]
  in
  let p = project ~macro_limits:[ macro, 1 ] ~rules Exact_or_flop_fallback in
  match Project.elaborate p ~mode:Implementation with
  | Ok _ -> failwith "Exact succeeded despite exhausted budget"
  | Error e ->
    let message = Error.to_string_hum e in
    List.iter [ "right/buf"; macro ] ~f:(fun substring ->
      if not (String.is_substring message ~substring) then Error.raise e)
;;

let%test_unit "omitting limits leaves every exact mapping available" =
  check
    (project Exact)
    Implementation
    [ "left/buf", exact macro; "right/buf", exact macro; "status", exact macro ]
;;

let%test_unit "a zero limit affects only the named macro" =
  let before context =
    ignore
      (Elaboration.Context.register_exn
         context
         ~name:"wide"
         (Fixture.Store.request ~width:16)
       : Resource.Record.t)
  in
  let p =
    project
      ~design:(Fixture.design ~before ())
      ~macro_limits:[ other, 0; macro, 2 ]
      Exact_or_flop_fallback
  in
  check
    p
    Implementation
    [ "left/buf", exact macro
    ; "right/buf", exact macro
    ; "status", exhausted macro 2
    ; "wide", exhausted other 0
    ]
;;

let%test_unit "unlisted mappings stay unlimited and missing mappings keep their reason" =
  let before context =
    ignore
      (Elaboration.Context.register_exn
         context
         ~name:"odd"
         (Fixture.Store.request ~width:7)
       : Resource.Record.t)
  in
  let p =
    project
      ~design:(Fixture.design ~before ())
      ~macro_limits:[ other, 0 ]
      Exact_or_flop_fallback
  in
  check
    p
    Implementation
    [ "left/buf", exact macro
    ; ( "odd"
      , { implementation = Flops
        ; reason = Flop_fallback { no_exact_mapping_in = technology_name }
        } )
    ; "right/buf", exact macro
    ; "status", exact macro
    ]
;;

let%test_unit "negative limits, blank names and duplicate names are rejected at creation" =
  List.iter
    [ [ macro, -1 ]; [ " ", 1 ]; [ macro, 1; macro, 2 ] ]
    ~f:(fun macro_limits ->
      assert (Result.is_error (Resource_policy.create ~default:Flops ~macro_limits [])))
;;
