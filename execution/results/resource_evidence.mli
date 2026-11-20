(** Read-only resource reconciliation. New collections emit schema 2; strict schema-1
    validation and replay retain the original recorded measurement semantics.
    All references hash the bytes actually read.
    No source reconstruction, EDA, PDK discovery or publication is performed. *)
val inspect : run_dir:string -> (Evidence_json.t, string) result
val collect : ?context:Evidence_json.t -> run_dir:string -> unit -> Evidence_json.t
val problems : Evidence_json.t -> string list
val validate : Evidence_json.t -> (Evidence_json.t, string) result

(** Recheck source hashes and replay the complete reconciliation from those sources.
    [context] is required when evidence originated from an explicit in-memory
    execution context rather than the selected run.json. Genuine failed verdicts
    are valid evidence and are not rejected solely for failing retention. *)
val verify_sources : ?context:Evidence_json.t -> run_dir:string -> Evidence_json.t -> (unit, string) result
val inventory_available : run_dir:string -> (bool, string) result

(** Shared manifest inventory adapter. Caller first verifies the manifest identity.
    This is selection/RTL expectation only, never realization. *)
val inventory : Evidence_json.t -> string * Evidence_json.t list

(** Pure instance reconciliation seam; observations must be complete to establish
    absence. Unknown classification fails required retention coverage. *)
val reconcile : expected:Evidence_json.t list -> instances:(string * string) list option
  -> known_standard_cells:string list -> reason:string option -> Evidence_json.t

(** Retention of declared macro instances in a complete structural top-module
    netlist. Raises [Invalid_argument] for unsupported/untrustworthy input. *)
val checkpoint_macro_rows : top:string -> inventory:Evidence_json.t list -> netlist:string
  -> refs:Evidence_json.t list -> Evidence_json.t list
