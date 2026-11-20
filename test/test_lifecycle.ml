(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "test_lifecycle.ml" *)
(* P0.1, P0.2, P0.4: declaration to elaborated project, registration identity and ordering,
   and closing the context. *)

open! Core
open! Hardcaml
open! Hardcaml_asic
open! Expect_test_helpers_core

let elaborate ?(mode = Elaboration.Mode.Implementation) project =
  Project.elaborate (ok_exn project) ~mode
;;

let%expect_test "declaration to finalized implementation project" =
  (* Keep the lifecycle snapshot independent of the stock technology's growing
     capability manifest; backend tests exercise the real SRAM inventory. *)
  let technology =
    Technology.create_exn ~name:"lifecycle-tech" ~resource_mappings:[]
  in
  let elaborated_project = elaborate (Fixture.project ~technology ()) |> ok_exn in
  print_s [%sexp (elaborated_project : Elaborated_project.t)];
  [%expect
    {|
    ((project_name fixture)
     (top          fixture_top)
     (metadata (
       (title  "Lifecycle fixture")
       (author hardcaml_asic)
       (description "Exercises declaration, registration and finalization")))
     (mode Implementation)
     (target (
       (harness (Tiny_tapeout (tiles T1x1)))
       (technology ((name lifecycle-tech) (resource_mappings ())))))
     (flow (
       (adapter (
         Librelane (
           (overrides ())
           (macros    ()))))
       (operation Hardening)))
     (clocks ((
       (port   clock)
       (period 20ns))))
     (pinout ())
     (timing (
       (input_delays  ())
       (output_delays ())))
     (resources (
       ((id left/buf)
        (request ((kind fixture_store) (contract ((width 8)))))
        (requirement Flops)
        (policy      Default)
        (selection (
          (implementation Flops)
          (reason         Explicit_flops)))
        (elaborated_as Selected_implementation)
        (sources (Generated_synthesis_rtl)))
       ((id right/buf)
        (request ((kind fixture_store) (contract ((width 8)))))
        (requirement Flops)
        (policy      Default)
        (selection (
          (implementation Flops)
          (reason         Explicit_flops)))
        (elaborated_as Selected_implementation)
        (sources (Generated_synthesis_rtl)))
       ((id status)
        (request ((kind fixture_store) (contract ((width 8)))))
        (requirement Flops)
        (policy      Default)
        (selection (
          (implementation Flops)
          (reason         Explicit_flops)))
        (elaborated_as Selected_implementation)
        (sources (Generated_synthesis_rtl))))))
    |}]
;;

let%expect_test "simulation mode elaborates behavioural models and simulates" =
  let elaborated_project = elaborate ~mode:Simulation (Fixture.project ()) |> ok_exn in
  List.iter (Elaborated_project.resources elaborated_project) ~f:(fun r ->
    print_s
      [%message
        ""
          ~_:(r.id : Resource.Id.t)
          ~_:(r.elaborated_as : Resource.Record.Elaborated_as.t)
          ~_:(r.sources : Resource.Record.Source.t list)]);
  [%expect
    {|
    (left/buf Behavioral_model (Generated_behavioral_model))
    (right/buf Behavioral_model (Generated_behavioral_model))
    (status Behavioral_model (Generated_behavioral_model))
    |}];
  let sim = Cyclesim.create (Elaborated_project.circuit elaborated_project) in
  Cyclesim.in_port sim "d" := Bits.of_int_trunc ~width:8 0x5a;
  Cyclesim.cycle sim;
  print_s [%message "" ~status:(!(Cyclesim.out_port sim "status") : Bits.t)];
  [%expect {| (status 01011010) |}]
;;

let%expect_test "identities and inventory order do not depend on registration order" =
  let inventory order =
    elaborate (Fixture.project ~design:(Fixture.design ~order ()) ())
    |> ok_exn
    |> Elaborated_project.resources
  in
  let forward = inventory `Forward in
  require_equal
    (module struct
      type t = Resource.Record.t list [@@deriving equal, sexp_of]
    end)
    forward
    (inventory `Reverse);
  require_equal
    (module struct
      type t = Resource.Record.t list [@@deriving equal, sexp_of]
    end)
    forward
    (inventory `Forward);
  print_s [%sexp (List.map forward ~f:(fun r -> r.id) : Resource.Id.t list)];
  [%expect {| (left/buf right/buf status) |}]
;;

let%expect_test "duplicate names in one scope are rejected" =
  let design =
    Fixture.design
      ~after:(fun context ->
        let clock = Signal.input "clock2" 1 in
        ignore
          (Fixture.Store.create
             context
             ~name:"status"
             ~clock
             (Signal.of_int_trunc ~width:8 0)
           : Signal.t))
      ()
  in
  print_s [%sexp (elaborate (Fixture.project ~design ()) : Elaborated_project.t Or_error.t)];
  [%expect
    {|
    (Error (
      ("design construction failed"
        (project fixture)
        (mode    Implementation))
      ("duplicate resource instance; give each instance in a scope a distinct name"
       (instance status)
       (request ((kind fixture_store) (contract ((width 8))))))))
    |}]
;;

let%expect_test "a retained context cannot register after finalization" =
  let retained = ref None in
  let design = Fixture.design ~before:(fun context -> retained := Some context) () in
  let elaborated_project = elaborate (Fixture.project ~design ()) |> ok_exn in
  let context = Option.value_exn !retained in
  require_does_raise (fun () ->
    Fixture.Store.create
      context
      ~name:"late"
      ~clock:(Signal.input "clock" 1)
      (Signal.input "d" 8));
  require_does_raise (fun () -> Elaboration.Context.scope context);
  print_s [%sexp (List.length (Elaborated_project.resources elaborated_project) : int)];
  [%expect
    {|
    ("elaboration context is closed; each elaboration uses a fresh context"
     (operation register)
     (state     Finalized))
    ("elaboration context is closed; each elaboration uses a fresh context"
     (operation scope)
     (state     Finalized))
    3
    |}]
;;

let%expect_test "failed construction is closed and a retry starts clean" =
  let retained = ref None in
  let fail = ref true in
  let design =
    Fixture.design
      ~before:(fun context -> retained := Some context)
      ~after:(fun _ ->
        if !fail then raise_s [%message "fixture failure after registering"])
      ()
  in
  let project = Fixture.project ~design () |> ok_exn in
  print_s
    [%sexp (Project.elaborate project ~mode:Implementation : Elaborated_project.t Or_error.t)];
  [%expect
    {|
    (Error (
      ("design construction failed"
        (project fixture)
        (mode    Implementation))
      "fixture failure after registering"))
    |}];
  let failed_context = Option.value_exn !retained in
  require_does_raise (fun () ->
    Elaboration.Context.register_exn
      failed_context
      ~name:"late"
      (Fixture.Store.request ~width:8));
  [%expect
    {|
    ("elaboration context is closed; each elaboration uses a fresh context"
     (operation register)
     (state     Failed))
    |}];
  fail := false;
  let elaborated_project = Project.elaborate project ~mode:Implementation |> ok_exn in
  require (not (phys_equal (Option.value_exn !retained) failed_context));
  Fixture.print_inventory elaborated_project;
  [%expect
    {|
    (left/buf Default ((implementation Flops) (reason Explicit_flops)))
    (right/buf Default ((implementation Flops) (reason Explicit_flops)))
    (status Default ((implementation Flops) (reason Explicit_flops)))
    |}]
;;

let%expect_test "a registration failure caught by the design still fails elaboration" =
  let swallow_duplicate context =
    try
      ignore
        (Elaboration.Context.register_exn
           context
           ~name:"status"
           (Fixture.Store.request ~width:8)
         : Resource.Record.t)
    with
    | _ -> ()
  in
  (* Swallowed after every resource registered: finalization refuses the elaboration. *)
  let design = Fixture.design ~after:swallow_duplicate () in
  print_s [%sexp (elaborate (Fixture.project ~design ()) : Elaborated_project.t Or_error.t)];
  [%expect
    {|
    (Error (
      ("elaboration validation failed"
        (project fixture)
        (mode    Implementation))
      "a resource registration failed during construction and the exception was caught inside the design"))
    |}];
  (* Swallowed before the rest of the design registers: the next registration raises. *)
  let design =
    Fixture.design
      ~before:(fun context ->
        swallow_duplicate context;
        swallow_duplicate context)
      ()
  in
  print_s [%sexp (elaborate (Fixture.project ~design ()) : Elaborated_project.t Or_error.t)];
  [%expect
    {|
    (Error (
      ("design construction failed"
        (project fixture)
        (mode    Implementation))
      ("elaboration context is closed; each elaboration uses a fresh context"
       (operation scope)
       (state     Failed))))
    |}]
;;

let%expect_test "a scope from another elaboration is rejected" =
  let design =
    Fixture.design
      ~before:(fun context ->
        ignore
          (Elaboration.Context.in_scope context (Scope.create ()) : Elaboration.Context.t))
      ()
  in
  print_s [%sexp (elaborate (Fixture.project ~design ()) : Elaborated_project.t Or_error.t)];
  [%expect
    {|
    (Error (
      ("design construction failed"
        (project fixture)
        (mode    Implementation))
      "scope does not belong to this elaboration"))
    |}]
;;

let%expect_test "invalid declarations produce diagnostics before elaboration" =
  let flow =
    Flow.librelane
      Hardening
      ~overrides:
        [ { key = "PL_TARGET_DENSITY_PCT"; value = `Int 60; reason = "" }
        ; { key = "PL_TARGET_DENSITY_PCT"; value = `Int 55; reason = "fits 1x1" }
        ]
  in
  print_s
    [%sexp (Fixture.project ~flow () |> Or_error.map ~f:Project.name : string Or_error.t)];
  [%expect
    {|
    (Error (
      ("invalid chip target declaration" (name fixture))
      ("LibreLane overrides need an uppercase key, strict JSON value, and nonempty reason"
       (invalid ((
         (key    PL_TARGET_DENSITY_PCT)
         (value  60)
         (reason "")))))
      ("LibreLane override key set more than once"
       (duplicate (
         (key    PL_TARGET_DENSITY_PCT)
         (value  60)
         (reason ""))))))
    |}]
;;
