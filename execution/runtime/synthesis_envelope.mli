(** Public opt-in environment interface for one fresh segmented synthesis and its
    publication/report collection. Absence preserves default/physical behavior.
    [HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=6] enforces a total container task ceiling,
    including controller/executor threads. Six usable executor slots are NOT
    promised. Invalid/duplicate values or non-synthesis plans fail closed. *)
val variable : string
val requested : string list -> bool
val pids_limit : string list -> int option
val check_plan : string list -> Execution_plan.t -> unit
val check_boundary : string list -> Execution_plan.Stage.t -> unit
val to_json : unit -> Identity_json.t
