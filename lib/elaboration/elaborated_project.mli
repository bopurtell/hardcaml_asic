(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/elaborated_project.mli" *)
(* The immutable result of one successful project elaboration.

   An elaborated project can be inspected without running any tool. It keeps design intent
   (metadata, target selection, clocks, resource inventory) separate from adapter
   settings. It contains an elaborated Hardcaml circuit, NOT a synthesis result or a
   LibreLane configuration; {!Bundle.render} renders the latter.

   Nothing can be added after finalization: the elaboration context that produced it
   is closed. A failed elaboration produces no elaborated project at all.

   The TT/LibreLane resolution result is a separate {!Resolved.t}. The
   bundle renderer consumes that validated result and emits flattened RTL and
   provenance. Hierarchical RTL emission is deferred.
*)

open! Core
open! Hardcaml

(** Prints every field except the circuit, which is summarised by its top module name. *)
type t [@@deriving sexp_of]

(** The name given to {!Chip_target.create}. *)
val project_name : t -> string

val metadata : t -> Metadata.t

(** The mode passed to {!Chip_target.elaborate}. *)
val mode : t -> Mode.t

(** The declared harness and technology selection. Use {!Resolved.resolve}
    or {!Chip_target.elaborate_for_flow} for a checked TT/LibreLane target. *)
val target : t -> Platform.Target.t

val flow : t -> Flow.Config.t

(** The declared clocks. Their ports are checked by {!Resolved.resolve}. *)
val clocks : t -> Platform.Clock.t list

val pinout : t -> Platform.Pinout.t
val timing : t -> Platform.Timing.t
val timing_stages : t -> (Platform.Timing.Stage.t * Signal.t) list
val hardware_variant : t -> Hardware_variant.t option

(** Ordered by {!Resource.Id.compare}, independent of registration order. *)
val resources : t -> Resource.Record.t list

(** The flattened circuit returned by the design constructor. *)
val circuit : t -> Circuit.t

module Private : sig
  (** Project-owned propagation only; not a semantic-use attestation. *)
  val with_hardware_variant : t -> Hardware_variant.t option -> t
  val with_timing_stages : t -> (Platform.Timing.Stage.t * Signal.t) list -> t
  (** Only {!Chip_target.elaborate} should call this, after
      {!Context.Private.finalize} succeeded. It performs no validation. *)
  val create
    :  project_name:string
    -> metadata:Metadata.t
    -> mode:Mode.t
    -> target:Platform.Target.t
    -> flow:Flow.Config.t
    -> clocks:Platform.Clock.t list
    -> pinout:Platform.Pinout.t
    -> timing:Platform.Timing.t
    -> resources:Resource.Record.t list
    -> circuit:Circuit.t
    -> t
end
