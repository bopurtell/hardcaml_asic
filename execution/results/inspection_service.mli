(** Host-only, read-only inspection of supplementary retained producer evidence.
    Checkpoint validation precedes legacy producer discovery. A terminal timing
    observation can authorize exact post-PNR evidence without a signoff checkpoint.
    With no row_id, expected_digest pins the timing analysis digest. With row_id,
    it pins the inspection index digest as before. No runtime or EDA probes. *)
val run : execution:string -> domain:string -> stage:string -> ?row_id:string
   -> ?expected_digest:string -> ?refresh_timing:bool -> unit -> (Identity_json.t, string) result

(** Pure parsers shared with focused fixture tests. Line spans are one-based. *)
val timing_records : ?max_records:int -> string -> ((int * int * (string * string) list) list * string list)

(** Native integers retain [Evidence_json]'s arbitrary-precision [`Intlit]
    representation, even for small values. Nonfinite tokens remain [`Float]
    here; [run] diagnoses them and emits null/unavailable numeric quantities.
    Duplicate scalar entries retain their separate one-based line spans. *)
val native_entries : ?max_entries:int -> string -> ((int * string * Yojson.Safe.t) list * string list)

(** Quoted Liberty [time_unit] attributes outside comments/quoted text.
    Returns (one-based line, exact unit string, original declaration line).
    This is lexical evidence extraction, not full Liberty validation. Missing
    declarations yield an empty list; malformed declarations yield diagnostics. *)
val liberty_time_units : string -> ((int * string * string) list * string list)

(** Bounded clock report records with explicit per-clock header relationships.
    Both [Clock:] and [Clock NAME] reset transition context. *)
val clock_records : ?max_records:int -> string -> ((int * (string * string) list) list * string list)

(** Class identity for validated occurrence-specific returned IDs. *)
val registered_class : string -> string

(** Query-wide input admission, before allocation: 32MiB/source, 64MiB total,
    512 reads. Final consistency checks stream hashes without retaining bytes. *)
val admit_input : used_bytes:int -> source_count:int -> bytes:int -> (int * int, string) result
