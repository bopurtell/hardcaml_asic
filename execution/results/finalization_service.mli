(** Separate early allocation, one complete fresh six-stage execution, exact-image
    closure validation, native collection and optional external postcheck/acceptance.
    No synthetic run.json. Source paths retain checkpoint's path-bound contract. *)
type selection
(* [synthesis_only] selects exactly one fresh synthesis checkpoint. [run] enables
    this only through Synthesis_envelope and forbids acceptance in that mode. *)
val selected : ?synthesis_only:bool -> string -> selection
type outcome = private { directory : string; record : Finalization_record.t }
val run : execution:string -> store:string -> environment:string list -> ?accept:bool
  -> ?support_tools:string -> ?precheck_python:string -> ?testbench:string
  -> ?testbench_top:string -> ?replay_trace:string
  -> ?allocated:(string -> string -> unit) -> unit -> outcome
