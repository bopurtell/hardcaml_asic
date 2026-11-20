(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/context.mli" *)
(* The temporary, mutable state of exactly one project elaboration.

    {!Chip_target.elaborate} creates a fresh context and passes it to the design constructor.
   Resource constructors register through it and receive their implementation selection.

   The context closes when elaboration finalizes OR fails; every operation on a closed
   context raises, so a retained context cannot add to a finalized project or leak into a
   retry. Future logging capabilities for context debugging will come, but not now.

   The context carries the current Hardcaml [Scope.t], which determines the hierarchical
   part of each resource identity. When a constructor enters a sub-scope (for example via
   [Hierarchy.In_scope]), obtain a context for it with {!in_scope}.
*)

open! Core
open! Hardcaml

type t

val mode : t -> Mode.t

(** The current scope. Pass it to Hardcaml hierarchy and naming functions. *)
val scope : t -> Scope.t

(** A view of the same elaboration positioned at [scope]. Raises if [scope] does not
    belong to this elaboration. *)
val in_scope : t -> Scope.t -> t

(** Register one resource instance named [name] in the current scope and select its
    implementation.

    Raises, with the instance identity and requested contract, if the name duplicates an
    instance already registered in this scope, if no policy applies, or if the policy
    cannot be satisfied by the technology.

    Any failure closes the context as failed before raising, so an exception caught inside
    the design still stops the elaboration: later registrations raise and
    {!Private.finalize} returns an error. *)
val register_exn : t -> name:string -> Resource.Request.t -> Resource.Record.t

(** Register an existing scalar stage; attach only a reserved Q name and Yosys
    keep=1. Duplicate IDs/Qs fail the context. No hardware or semantics are added. *)
val register_timing_stage_exn
  : t -> stage:Platform.Timing.Stage.t -> q:Signal.t -> unit

module Private : sig
  val timing_stages : t -> (Platform.Timing.Stage.t * Signal.t) list
  val create
    :  mode:Mode.t
    -> technology:Resource.Technology.t
    -> policy:Resource.Policy.t
    -> scope:Scope.t
    -> t

  (** Validate collected state and close the context. Returns the inventory ordered by
      identity. The context is closed whether or not validation succeeds. Returns an error
      if a registration already failed and closed the context. *)
  val finalize : t -> Resource.Record.t list Or_error.t

  (** Close the context after a failed construction. *)
  val fail : t -> unit
end
