(** Actual bounded probes, supervised by OCaml. Only import-dependent metadata is
     an external, version-1 Python payload. Stdout/stderr/argv and terminal outcomes
     are recorded in the caller-owned evidence directory when storage permits.
     A failed/cancelled process raises [Runner.Callback_error] with evidence-write
     failures secondary; a successful process with a failed terminal write also
     raises, never returns successful metadata. No policy, staging or Python runner call. *)
type t
val create : directory:string -> environment:string list -> timeout:float -> t
val create_cancellable : cancel:(unit -> int option) -> directory:string -> environment:string list -> timeout:float -> t
(* Cancellation signals use OCaml Sys/Unix representations, as Process_supervisor. *)
val command : t -> prog:string -> arguments:string list -> string
val observe_host : t -> python:string -> payload:string -> Identity_json.t
val observe_container : ?workers:int -> ?pids_limit:int -> t -> docker:string -> payload:string -> Runtime_contract.t -> Identity_json.t
(* Standalone metadata only: no Classic imports, staging, mounts or fixed image. *)
val observe : ?workers:int -> ?pids_limit:int -> t -> python:string -> docker:string -> payload:string
  -> pdk_root:string -> Runtime_contract.t
  -> Identity_json.t * Identity_json.t * Execution_transport.binding
val verify : t -> Execution_transport.binding -> (unit, string) result
(** Reprobe the bound engine and local image ID, never resolve a tag again. *)

val find_executable : environment:string list -> string -> string
