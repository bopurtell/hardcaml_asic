(** Immutable post-PNR STA observation; never a checkpoint. Historical schema 1 is
    Signoff-scoped; schema 2 is Timing-scoped and may bind an immutable running execution
    prefix. [load] resolves that prefix only after checking the current execution's
    attempt, plan and exact Timing occurrence/publication. *)
val path : string -> string

val publish : execution:string -> transport_records:(string * string) list -> unit

(** Recompute from retained sources without publishing or replacing an observation.
    Uses the same strict execution, transport, producer and source associations as
    [publish]. Historical observations remain immutable. *)
val capture : execution:string -> transport_records:(string * string) list -> Identity_json.t option
val load : execution:string -> execution_raw:string -> Identity_json.t option
val validate_sources : Identity_json.t -> unit
val load_file : ?expected_record:Identity_json.t -> string -> Identity_json.t option

(** Read-only reinspection anchored to a validated historical observation and its
    exact inventoried transport. Returns original and recomputed observations;
    neither is published, and the original outcome is never rewritten. *)
val reinspect : execution:string -> expected_record:Identity_json.t -> Identity_json.t * Identity_json.t

(** Shared bounded LibreLane Step completion-duration grammar; no filesystem or
    execution association is inferred by parsing a runtime string. *)
val native_runtime_seconds : string -> float
