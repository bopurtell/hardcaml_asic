open! Core

(** Validated schema-4 recipes for the opt-in provider [stages --plan] command. Pure
    declarations: no I/O, process invocation, checkpoint validation or tool probing.
    Independent of [Chip_target.create], bundle identity and run identity. *)
type t [@@deriving sexp_of]

(** Fresh sequences start at synthesis. Resumed sequences start immediately after
    [initial_boundary]. Empty, skipped, repeated and reordered stages fail here. The
    provider must also validate the supplied checkpoint's actual source and compatibility
    before execution. *)
val create : ?initial_boundary:Stage.t -> Stage.t list -> t Or_error.t

(** Historical six-stage decoding only; this does not authorize bridge execution. *)
val decode_legacy : ?initial_boundary:Stage.t -> Stage.t list -> t Or_error.t

(** Expand all stages through the endpoint, optionally from an explicit source. A terminal
    source or an endpoint at/before the source fails. *)
val through : ?initial_boundary:Stage.t -> Stage.t -> t Or_error.t

(** Join validated recipes when the second recipe's source is exactly the first recipe's
    endpoint. This composes declarations, not executions or historical checkpoint sets;
    finalization of resumed attempts is a separate contract. *)
val compose : t -> t -> t Or_error.t

val stages : t -> Stage.t list
val initial_boundary : t -> Stage.t option
val endpoint : t -> Stage.t

module Input : sig
  type t =
    | Bundle
    | Checkpoint of Stage.t
  [@@deriving equal, sexp_of]
end

type declaration =
  { stage : Stage.t
  ; input : Input.t
  ; output_checkpoint : Stage.t
  }
[@@deriving sexp_of]

(** First external input and ordered per-stage handoffs. Checkpoint inputs are boundary
    requirements, not proof that any path is a ready checkpoint. All stages also require
    the runtime/closure conditions in [Stage.capability]. *)
val required_input : t -> Input.t

val declarations : t -> declaration list

(** Exactly [schema_version], [stages], [initial_boundary]; no backend settings, runtime
    bindings, paths, or run IDs. [to_string] renders strict JSON suitable for writing to
    the file passed as [--plan]. *)
val to_yojson : t -> Yojson.Safe.t

val to_string : t -> string
