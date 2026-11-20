(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "test_selection.ml" *)
(* P0.3: requested contract, technology capability and project policy.

   The technology below is a test input describing a hypothetical exact mapping. It is not
   evidence of any real macro backend. *)

open! Core
open! Hardcaml
open! Hardcaml_asic
open! Expect_test_helpers_core

let fixture_macro : Resource.Technology.Macro.t =
  { name = "fixture_macro_8"
  ; collateral =
      [ { role = Simulation_model; reference = "fixture/macro_8.v" }
      ; { role = Black_box; reference = "fixture/macro_8_bb.v" }
      ; { role = Timing_model { corner = "typ" }; reference = "fixture/macro_8.lib" }
      ; { role = Physical_abstract; reference = "fixture/macro_8.lef" }
      ]
  }
;;

let technology_with_width_8_mapping =
  Resource.Technology.create_exn
    ~name:"fixture-tech"
    ~resource_mappings:
      [ { request = Fixture.Store.request ~width:8; macro = fixture_macro } ]
;;

let elaborate ?(mode = Elaboration.Mode.Implementation) ?design ~technology policy =
  let%bind.Or_error policy in
  let%bind.Or_error project = Fixture.project ?design ~technology ~policy () in
  Project.elaborate project ~mode
;;

let show ?mode ?design ~technology policy =
  match elaborate ?mode ?design ~technology policy with
  | Ok elaborated_project -> Fixture.print_inventory elaborated_project
  | Error error -> print_s [%sexp (error : Error.t)]
;;

let rule selector requirement = { Resource_policy.Rule.selector; requirement }
let instance = Resource_policy.Selector.instance_exn
let subtree = Resource_policy.Selector.subtree_exn

let%test_unit "project declaration checks budget names against technology, not design" =
  let stock = "RM_IHPSG13_1P_256x16_c2_bm_bist" in
  let declare name =
    let policy = Resource_policy.create ~default:Flops ~macro_limits:[ name, 0 ] [] |> ok_exn in
    Fixture.project ~technology:Technology.ihp_sg13cmos5l ~policy ()
  in
  (* The fixture uses no stock SRAM. *)
  let project = declare stock |> ok_exn in
  ignore (Project.elaborate project ~mode:Implementation |> ok_exn : Elaborated_project.t);
  let typo = stock ^ "_typo" in
  match declare typo with
  | Ok _ -> failwith "project declaration accepted an unknown macro"
  | Error err ->
    let message = Error.to_string_hum err in
    List.iter [ typo; Technology.ihp_sg13cmos5l.name ] ~f:(fun substring ->
      assert (String.is_substring message ~substring))
;;

let%expect_test "macro slots go to eligible registrations in construction order" =
  let policy =
    Resource_policy.create
      ~macro_limits:[ fixture_macro.name, 1 ]
      ~default:Exact_or_flop_fallback
      [ rule (instance "status") Flops ]
    |> ok_exn
  in
  let project =
    Fixture.project
      ~policy
      ~technology:technology_with_width_8_mapping
      ~design:(Fixture.design ~order:`Reverse ())
      ()
    |> ok_exn
  in
  List.iter [ Elaboration.Mode.Simulation; Implementation ] ~f:(fun mode ->
    let project = Project.elaborate project ~mode |> ok_exn in
    List.iter (Elaborated_project.resources project) ~f:(fun r ->
      print_s [%sexp (r.id : Resource.Id.t), (r.selection.reason : Resource.Selection.Reason.t)]));
  [%expect {|
    (left/buf (
      Budget_exhausted
      (macro fixture_macro_8)
      (limit 1)))
    (right/buf (Exact_mapping (technology fixture-tech)))
    (status Explicit_flops)
    (left/buf (
      Budget_exhausted
      (macro fixture_macro_8)
      (limit 1)))
    (right/buf (Exact_mapping (technology fixture-tech)))
    (status Explicit_flops)
    |}]
;;

let%expect_test "exact registration fails even with a mapping when its limit is zero" =
  show
    ~technology:technology_with_width_8_mapping
    (Resource_policy.create ~macro_limits:[ fixture_macro.name, 0 ] ~default:Exact []);
  [%expect {|
    (("design construction failed"
       (project fixture)
       (mode    Implementation))
     (("resource selection failed"
        (instance left/buf)
        (request ((kind fixture_store) (contract ((width 8))))))
      ("macro instance budget exhausted, and flop fallback is not permitted"
       (macro fixture_macro_8)
       (limit 0))))
    |}]
;;

let%expect_test "exact mapping is selected and carries its collateral" =
  let elaborated_project =
    elaborate
      ~technology:technology_with_width_8_mapping
      (Resource_policy.create ~default:Exact [])
    |> ok_exn
  in
  let status = List.last_exn (Elaborated_project.resources elaborated_project) in
  print_s [%sexp (status : Resource.Record.t)];
  [%expect
    {|
    ((id status)
     (request ((kind fixture_store) (contract ((width 8)))))
     (requirement Exact)
     (policy      Default)
     (selection (
       (implementation (Macro fixture_macro_8))
       (reason (Exact_mapping (technology fixture-tech)))))
     (elaborated_as Selected_implementation)
     (sources (
       (Collateral (
         (role      Simulation_model)
         (reference fixture/macro_8.v)))
       (Collateral (
         (role      Black_box)
         (reference fixture/macro_8_bb.v)))
       (Collateral (
         (role (Timing_model (corner typ))) (reference fixture/macro_8.lib)))
       (Collateral (
         (role      Physical_abstract)
         (reference fixture/macro_8.lef))))))
    |}]
;;

let%expect_test "missing exact mapping fails with the instance and requested contract" =
  show ~technology:Resource.Technology.ihp_sg13cmos5l (Resource_policy.create ~default:Exact []);
  [%expect
    {|
    (("design construction failed"
       (project fixture)
       (mode    Implementation))
     (("resource selection failed"
        (instance left/buf)
        (request ((kind fixture_store) (contract ((width 8))))))
      ("technology has no exact implementation for the requested contract, and flop fallback is not permitted"
       (technology ihp-sg13cmos5l))))
    |}]
;;

let%expect_test "a different contract is not matched to a nearby mapping" =
  let technology =
    Resource.Technology.create_exn
      ~name:"fixture-tech"
      ~resource_mappings:
        [ { request = Fixture.Store.request ~width:16; macro = fixture_macro } ]
  in
  show ~technology (Resource_policy.create ~default:Exact_or_flop_fallback []);
  [%expect
    {|
    (left/buf Default
     ((implementation Flops)
      (reason (Flop_fallback (no_exact_mapping_in fixture-tech)))))
    (right/buf Default
     ((implementation Flops)
      (reason (Flop_fallback (no_exact_mapping_in fixture-tech)))))
    (status Default
     ((implementation Flops)
      (reason (Flop_fallback (no_exact_mapping_in fixture-tech)))))
    |}]
;;

let%expect_test "explicit flops are distinct from fallback, even with a mapping available"
  =
  show
    ~technology:technology_with_width_8_mapping
    (Resource_policy.create
       ~default:Exact_or_flop_fallback
       [ rule (instance "status") Flops ]);
  [%expect
    {|
    (left/buf Default
     ((implementation (Macro fixture_macro_8))
      (reason (Exact_mapping (technology fixture-tech)))))
    (right/buf Default
     ((implementation (Macro fixture_macro_8))
      (reason (Exact_mapping (technology fixture-tech)))))
    (status (Rule (Instance status))
     ((implementation Flops) (reason Explicit_flops)))
    |}]
;;

let%expect_test "precedence: instance over deeper subtree over shallower subtree" =
  let design = Fixture.design ~within:"core" () in
  (* Listed in the reverse of their precedence: rule order is irrelevant. The default
     cannot be satisfied, so any instance falling through to it would fail. *)
  show
    ~design
    ~technology:technology_with_width_8_mapping
    (Resource_policy.create
       ~default:Exact
       [ rule (subtree "core") Exact_or_flop_fallback
       ; rule (subtree "core/left") Flops
       ; rule (instance "core/right/buf") Flops
       ]);
  [%expect
    {|
    (core/left/buf (Rule (Subtree (core left)))
     ((implementation Flops) (reason Explicit_flops)))
    (core/right/buf (Rule (Instance core/right/buf))
     ((implementation Flops) (reason Explicit_flops)))
    (core/status (Rule (Subtree (core)))
     ((implementation (Macro fixture_macro_8))
      (reason (Exact_mapping (technology fixture-tech)))))
    |}]
;;

let%expect_test "repeated selectors are ambiguous" =
  print_s
    [%sexp
      (Resource_policy.create [ rule (subtree "core") Flops; rule (subtree "core") Exact ]
       : Resource_policy.t Or_error.t)];
  [%expect
    {|
    (Error (
      "ambiguous implementation policy: selector appears in more than one rule"
      (selector (Subtree (core)))))
    |}]
;;

let%expect_test "an instance with no applicable policy fails" =
  show
    ~technology:Resource.Technology.ihp_sg13cmos5l
    (Resource_policy.create [ rule (subtree "left") Flops ]);
  [%expect
    {|
    (("design construction failed"
       (project fixture)
       (mode    Implementation))
     (("resource selection failed"
        (instance right/buf)
        (request ((kind fixture_store) (contract ((width 8))))))
      ("no implementation policy applies; add a default or a rule selecting this instance"
       (instance right/buf))))
    |}]
;;

let%expect_test "a rule matching no registered resource fails finalization" =
  show
    ~technology:Resource.Technology.ihp_sg13cmos5l
    (Resource_policy.create ~default:Flops [ rule (instance "left/bfu") Exact ]);
  [%expect
    {|
    (("elaboration validation failed"
       (project fixture)
       (mode    Implementation))
     ("implementation policy rules never applied to a registered resource"
      (unmatched_rules (((selector (Instance left/bfu)) (requirement Exact))))
      (registered (left/buf right/buf status))))
    |}]
;;

let%expect_test "a rule outranked on every resource it matches fails finalization" =
  (* [subtree "left"] matches only left/buf, which the instance rule decides. Its [Exact]
     cannot be satisfied by this technology, so it would fail registration if it applied. *)
  show
    ~technology:Resource.Technology.ihp_sg13cmos5l
    (Resource_policy.create
       ~default:Flops
       [ rule (subtree "left") Exact; rule (instance "left/buf") Flops ]);
  [%expect {|
    (("elaboration validation failed"
       (project fixture)
       (mode    Implementation))
     ("implementation policy rules never applied to a registered resource"
      (shadowed_rules ((
        (rule ((selector (Subtree (left))) (requirement Exact)))
        (overridden_by ((Instance left/buf))))))
      (registered (left/buf right/buf status))))
    |}]
;;

let%expect_test "a rule outranked on only some of its resources is live" =
  let design = Fixture.design ~within:"core" () in
  show
    ~design
    ~technology:Resource.Technology.ihp_sg13cmos5l
    (Resource_policy.create
       [ rule (subtree "core") Flops; rule (instance "core/left/buf") Flops ]);
  [%expect {|
    (core/left/buf (Rule (Instance core/left/buf))
     ((implementation Flops) (reason Explicit_flops)))
    (core/right/buf (Rule (Subtree (core)))
     ((implementation Flops) (reason Explicit_flops)))
    (core/status (Rule (Subtree (core)))
     ((implementation Flops) (reason Explicit_flops)))
    |}]
;;

let%expect_test "simulation elaboration still applies policy" =
  show
    ~mode:Simulation
    ~technology:Resource.Technology.ihp_sg13cmos5l
    (Resource_policy.create ~default:Exact []);
  [%expect
    {|
    (("design construction failed"
       (project fixture)
       (mode    Simulation))
     (("resource selection failed"
        (instance left/buf)
        (request ((kind fixture_store) (contract ((width 8))))))
      ("technology has no exact implementation for the requested contract, and flop fallback is not permitted"
       (technology ihp-sg13cmos5l))))
    |}];
  let elaborated_project =
    elaborate
      ~mode:Simulation
      ~technology:technology_with_width_8_mapping
      (Resource_policy.create ~default:Exact [])
    |> ok_exn
  in
  let status = List.last_exn (Elaborated_project.resources elaborated_project) in
  print_s
    [%message
      ""
        ~selection:(status.selection : Resource.Selection.t)
        ~elaborated_as:(status.elaborated_as : Resource.Record.Elaborated_as.t)
        ~sources:(status.sources : Resource.Record.Source.t list)];
  [%expect
    {|
    ((selection (
       (implementation (Macro fixture_macro_8))
       (reason (Exact_mapping (technology fixture-tech)))))
     (elaborated_as Behavioral_model)
     (sources (Generated_behavioral_model)))
    |}]
;;

let%expect_test "malformed technology capability and selectors are rejected" =
  require_does_raise (fun () ->
    Resource.Technology.create_exn
      ~name:"fixture-tech"
      ~resource_mappings:
        [ { request = Fixture.Store.request ~width:8; macro = fixture_macro }
        ; { request = Fixture.Store.request ~width:8
          ; macro = { fixture_macro with name = "other" }
          }
        ]);
  require_does_raise (fun () -> subtree "core//left");
  require_does_raise (fun () -> instance "core/left/");
  [%expect
    {|
    ("ambiguous technology capability: more than one mapping for a request; keep one mapping per request"
     (technology fixture-tech)
     (request ((kind fixture_store) (contract ((width 8))))))
    ("subtree selector must be a nonempty '/'-separated scope path"
     (selector core//left))
    ("resource identity components must be identifiers; use only [A-Za-z0-9_], not starting with a digit"
     (component "")
     (path (core left))
     (name ""))
    |}]
;;
