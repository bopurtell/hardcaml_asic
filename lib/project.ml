(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "project.ml" *)
(* Behaviour of the actual project system; promises derived from project.mli
*)

open! Core
open! Hardcaml

[@@@ocamlformat "disable"]
(* Design items of the signature from the MLI *)
module type Design = sig
  module I : Interface.S
  module O : Interface.S

  val name : string
  val create :
    Elaboration.Context.t ->
    Signal.t I.t ->
    Signal.t O.t
end

type t =
  { name      : string
  ; metadata  : Elaboration.Metadata.t
  ; design    : (module Design)
  ; target    : Platform.Target.t
  ; flow      : Flow.Config.t
  ; clocks    : Platform.Clock.t list
  ; pinout    : Platform.Pinout.t
  ; timing    : Platform.Timing.t
  ; policy    : Resource.Policy.t
  ; source_inputs : string list
  ; hardware_variant : Hardware_variant.t option
  }

let validate_macro_limits policy (technology : Resource.Technology.t) =
  match Resource.Policy.limited_macro_names policy with
  | [] -> Ok ()
  | names ->
    let known =
      String.Set.of_list (List.map technology.resource_mappings ~f:(fun m -> m.macro.name))
    in
    match List.filter names ~f:(fun name -> not (Set.mem known name)) with
    | [] -> Ok ()
    | unknown_macros ->
      Or_error.error_s
        [%message "macro limits name macros not mapped by the project technology"
            ~technology:(technology.name : string) (unknown_macros : string list)]
;;

let create
    ~name
    ~metadata
    ~design
    ~target
    ~flow
    ?(clocks = [])
    ?(pinout = [])
    ?(timing = Platform.Timing.empty)
    ?(source_inputs = [])
    ~policy ()
  =
  (* Glorified validation function on all of the inputs;

    Checks each input is valid in it's construction and use,
      If valid -> returns the record out to the caller
      If not valid -> returns all errors of whatever items are incorrect

    This is a fun ppx sugar for Or_error.map

    Or_error.t is the Jane result type; declare "Ok x or Error.t" in layman's terms. More to it but not that we care about right here.

  *)
  let%map.Or_error () =
    (* Or_err -> checks for error returns on all members; this doesn't short circuit like a more naive item might; *)
    Or_error.combine_errors_unit

      [ (if Resource.Id.is_valid_component name (* project name must be literal identifier *)
         then Ok ()
         else
           Or_error.error_s
             [%message "project name must be an identifier" (name : string)])

      ; Elaboration.Metadata. validate      metadata  (* nonblank title *)
      ; Platform.Clock.       validate_list clocks    (* ports nonempty, periods > 0, no dups *)
      ; Flow.Config.          validate      flow      (* librelane overrides formed correctly *)
      ; validate_macro_limits policy target.Platform.Target.technology
      ]

    (* if there was an error, tag it with where the error belongs in the project scheme *)
    |> Or_error.tag_s ~tag:[%message "invalid project declaration" (name : string)]
  in

  (* Nice return record if all OK; otherwise an error group gets formed; *)
  { name
  ; metadata
  ; design
  ; target
  ; flow
  ; clocks
  ; pinout
  ; timing
  ; policy
  ; source_inputs
  ; hardware_variant = None
  }
;;

let name t = t.name
let source_inputs t = t.source_inputs
let hardware_variant t = t.hardware_variant

let of_variant ~variant ~create =
  let open Or_error.Let_syntax in
  let%bind project = Or_error.try_with (fun () -> create variant) |> Or_error.join in
  if Option.is_some project.hardware_variant then
    Or_error.error_string "project already has a hardware variant (repeated/conflicting attachment)"
  else Ok { project with hardware_variant = Some variant }
;;

(* Local declaration inspection only, not a Workbench observation protocol:
   unpacking does not run D.create, resolve a target, or read files/Git/PDKs. *)
let describe t =
  let (module D) = t.design in
  let declaration = [%sexp
    { project_name = (t.name : string)
    ; top = (D.name : string)
    ; metadata = (t.metadata : Elaboration.Metadata.t)
    ; source_inputs = (t.source_inputs : string list)
    ; target = (t.target : Platform.Target.t)
    ; flow = (t.flow : Flow.Config.t)
    ; clocks = (t.clocks : Platform.Clock.t list)
    ; pinout = (t.pinout : Platform.Pinout.t)
    ; timing = (t.timing : Platform.Timing.t)
    ; policy = (t.policy : Resource.Policy.t)
    }] in
  match t.hardware_variant, declaration with
  | None, _ -> declaration
  | Some variant, Sexp.List fields ->
    Sexp.List (fields @ [Sexp.List [Sexp.Atom "hardware_variant"; Hardware_variant.sexp_of_t variant]])
  | Some _, Sexp.Atom _ -> assert false
;;

(* Runs the user's design once, and returns the Elaborated_project.t object *)
let elaborate_with_phases t ~mode ~on_phase =
  (* Unpack our first class design module *)
  let (module D) = t.design in

  (* Functor on I and O to make the Circuit.t out of create_exn calls *)
  let module C = Circuit.With_interface (D.I) (D.O) in

  (* Represent error handling with the expanded context under an error happening; *)
  let tag message =
    Or_error.tag_s ~tag:[%message message ~project:t.name (mode : Elaboration.Mode.t)]
  in

  (* Overall Circuit.t scope *)
  on_phase Resource_preparation.Phase.Construction;
  let scope = Scope.create ~flatten_design:true () in

  (* Context creation; This is only lived as long as a given elaboration run, and that is the
  intention behind a context; think of it perhaps as a temporary database for things with an elaboration run to exist within and communicate via;

     Contexts hold their mode, technology theyre implementing on, a resource policy, and a scope.
  *)
  let context =
    Elaboration.Context.Private.create
      ~mode
      ~technology:t.target.technology
      ~policy:t.policy
      ~scope
  in

  (* Runs the user's design and catches any of the errors that are emitted out; *)
  match Or_error.try_with ( (* Any exception is turned into an error instead, and registered. *)
    (* Passes a partial context in order to create the Signal.t I -> Signal.t O that create_exn wants to make the Circuit.t *)
    fun () -> C.create_exn ~name:D.name (D.create context)
  ) with

  (* Context error plug *)
  | Error _ as error ->
    Elaboration.Context.Private.fail context;
    tag "design construction failed" error
    |> Result.map_error ~f:(fun e -> Resource_preparation.Phase.Construction, e)

  (* Ok circuit generation; validates on the resouces first; *)
  | Ok circuit ->
    (try on_phase Resource_preparation.Phase.Finalization with exn ->
       let backtrace = Backtrace.Exn.most_recent () in
       Elaboration.Context.Private.fail context;
       Exn.raise_with_original_backtrace exn backtrace);
    let%map.Result resources =
      (* Good! -> Finalize; This closes the context and returns the resource records. *)
      Elaboration.Context.Private.finalize context |> tag "elaboration validation failed"
      |> Result.map_error ~f:(fun e -> Resource_preparation.Phase.Finalization, e)
    in

    (* Only runs if the above resources validate runs based on the context;
       Elaborated_project.Private.create is called only after successful finalization. *)
    Elaboration.Elaborated_project.Private.create
      ~project_name:t.name
      ~metadata:t.metadata
      ~mode
      ~target:t.target
      ~flow:t.flow
      ~clocks:t.clocks
      ~pinout:t.pinout
      ~timing:t.timing
      ~resources
      ~circuit
    |> fun project -> Elaboration.Elaborated_project.Private.with_timing_stages project
          (Elaboration.Context.Private.timing_stages context)
    |> fun project -> Elaboration.Elaborated_project.Private.with_hardware_variant project
          t.hardware_variant

  (* Elaborated_project.Private.create and Elaboration.Context.Private are exposed
     conventional internal APIs intended for Project.elaborate. This naming convention
     is distinct from the compiler-enforced private records in Elaboration.Resolved. *)
;;

let elaborate t ~mode =
  elaborate_with_phases t ~mode ~on_phase:ignore
  |> Result.map_error ~f:snd
;;

let prepare_resources ?(inputs = Platform.Target.Inputs.reference_cmos5l_6x4)
    ?methodology ?(catalog_inputs = []) ?(on_phase = ignore) t ~source_root =
  let module P = Resource_preparation in
  let step phase code message f =
    on_phase phase;
    Or_error.try_with f |> Or_error.join
    |> Result.map_error ~f:(P.Failure.create ~phase ~code ~message)
  in
  let open Result.Let_syntax in
  let input_paths = t.source_inputs @ catalog_inputs in
  let%bind before = step Source_before "source_observation_failed"
    "Could not observe declared source inputs" (fun () ->
      Elaboration.Source_snapshot.observe ~source_root ~input_paths) in
  (* Callback exceptions must escape to the durable operation owner. Construction
     itself already catches design errors; callback invocation is outside it. *)
  let%bind elaborated = elaborate_with_phases t ~mode:Implementation ~on_phase
    |> Result.map_error ~f:(fun (phase, error) ->
      P.Failure.create ~phase
        ~code:(match phase with Construction -> "design_construction_failed" | _ -> "elaboration_finalization_failed")
        ~message:"Implementation elaboration failed" error) in
  let%bind resolution = step Resolution "flow_resolution_failed"
    "Implementation flow resolution failed" (fun () ->
      Elaboration.Resolved.resolve ~inputs ?methodology elaborated) in
  let%bind rtl = step Circuit "circuit_render_failed"
    "Could not encode the actual implementation circuit" (fun () ->
      Ok (Rtl.create Verilog [Elaboration.Elaborated_project.circuit elaborated]
          |> Rtl.full_hierarchy |> Rope.to_string)) in
  let%bind prepared = step Snapshot "snapshot_failed" "Could not encode resource preparation" (fun () ->
    let declaration = [%sexp { project = (describe t : Sexp.t);
      catalog_inputs = (List.dedup_and_sort catalog_inputs ~compare:String.compare : string list) }] in
    P.Private.create ~source:before ~declaration ~inputs ~resolution ~rtl) in
  let%bind after = step Source_after "source_observation_failed"
    "Could not re-observe declared source inputs" (fun () ->
      Elaboration.Source_snapshot.observe ~source_root ~input_paths) in
  let%bind () =
    if String.equal (Elaboration.Source_snapshot.identity before)
        (Elaboration.Source_snapshot.identity after) then Ok ()
    else Error (P.Failure.create ~phase:Source_after ~code:"source_changed"
      ~message:"Declared sources changed during resource preparation"
      (Error.create_s [%message "before and after source identities differ"
        ~before_identity:(Elaboration.Source_snapshot.identity before : string)
        ~after_identity:(Elaboration.Source_snapshot.identity after : string)])) in
  Ok prepared
;;

let elaborate_for_flow ?inputs ?methodology t =
  let%bind.Or_error elaborated_project = elaborate t ~mode:Elaboration.Mode.Implementation in
  Elaboration.Resolved.resolve ?inputs ?methodology elaborated_project
  |> Or_error.tag_s ~tag:[%message "flow resolution failed" ~project:t.name]
;;

(* Shared rendering sequence; reject empty inventory before construction,
   and leave source canonicalization and synthesis-source fallback to Bundle; *)
module Rendered = struct
  type t = { resolution : Elaboration.Resolved.t; bundle : Elaboration.Bundle.t }
  let resolution t = t.resolution
  let bundle t = t.bundle
end

let render_result ?inputs ?methodology ?(include_simulation = true)
    ?(catalog_inputs = []) t ~source_root =
  let open Or_error.Let_syntax in
  let%bind () =
    if List.is_empty t.source_inputs
    then Or_error.error_string "bundle emission requires nonempty source_inputs"
    else Ok ()
  in
  let%bind resolved = elaborate_for_flow ?inputs ?methodology t in
  let%bind simulation_project =
    if include_simulation
    then
      let%map project = elaborate t ~mode:Simulation in
      Some project
    else Ok None
  in
  let%map bundle = Elaboration.Bundle.render ?simulation_project ~source_root
    ~input_paths:(t.source_inputs @ catalog_inputs) resolved in
  { Rendered.resolution = resolved; bundle }
;;

let render_bundle ?inputs ?methodology ?include_simulation t ~source_root =
  render_result ?inputs ?methodology ?include_simulation t ~source_root
  |> Or_error.map ~f:Rendered.bundle
;;

(* Explicit empty-directory emission keeps Bundle.write's original semantics; *)
let emit_bundle ?inputs ?methodology ?include_simulation t ~source_root ~output_dir =
  let open Or_error.Let_syntax in
  let%bind bundle = render_bundle ?inputs ?methodology ?include_simulation t ~source_root in
  let%map () = Elaboration.Bundle.write bundle ~output_dir in
  bundle
;;

(* Project owns elaboration once; publication uses that exact rendered bundle; *)
let emit_bundle_to_store ?inputs ?methodology ?include_simulation t ~source_root ~store_dir =
  let open Or_error.Let_syntax in
  let%bind bundle = render_bundle ?inputs ?methodology ?include_simulation t ~source_root in
  let%map publication = Elaboration.Bundle.publish bundle ~store_dir in
  bundle, publication
;;
[@@@ocamlformat "enable"]
