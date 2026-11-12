(** Read-only historical checkpoint validation. No backend imports, launches,
    publication or acceptance inference. Parents/files must remain stable during
    validation (as with Result_store); this is not a hostile-writer sandbox. *)
module Stage = Hardcaml_asic.Flow.Stage
type schema = Schema_1 | Schema_2 | Schema_3
type file_record = private { sha256 : string; bytes : string }

(** Streaming SHA-256 and decimal byte count for a regular file. Follows host
    symlinks like the provider's file_record; [validate] separately rejects all
    symlinks within the checkpoint inventory. No native state decoding. *)
val file_record : string -> (file_record, string) result

type t = private
  { schema : schema
  ; identity : string
  ; location : string
  ; boundary : Stage.t
  ; restart_targets : Stage.t list
  ; compatibility : Identity_json.t
  ; files : (string * file_record) list
  ; external_files : (string * file_record) list
  ; metrics : Identity_json.t
  ; raw : Identity_json.t
  }
val of_json : Identity_json.t -> (t, string) result
val parse : string -> (t, string) result

(** Reads and checks manifest syntax, fields, identity and declared capabilities;
    [validate] additionally checks its actual filesystem and caller compatibility. *)
val read : string -> (t, string) result
val to_json : t -> Identity_json.t
val absolute_path : string -> (string, string) result

(** Schema3/sram_segmented1 evidence entries. Placement and later checkpoints
    carry exactly one transitive macro_power_geometry artifact; earlier stages
    carry none. Timing has no restart target. Paths are publication-local. *)
val macro_evidence : boundary:Stage.t -> Identity_json.t -> (Identity_json.t list, string) result

(** Actual recorded-versus-expected compatibility, exact bound location, complete
    inventory, opaque file hashes, native view structure, public metrics and host
    closure. /nix/store/ evidence is declared but never host-validated.
    Schema 2 requires the exact tested image even without a restart request.
    [location] is solely the existing private-candidate/final-location check used
    by a future publisher; it does not grant relocation of retained checkpoints.
    Internal file and directory symlinks are rejected, including dangling links.
    External host files retain the Python validator's symlink-following behavior.
    Native state bytes are hashed verbatim. A private read-only syntax projection
    checks only view leaves and the metrics-object container; native nonfinite
    tokens, including exponent overflow, are permitted inside metrics. No native
    metric values are exposed or serialized. The projection uses the codec's
    Unicode/depth/duplicate-key restrictions; it is not a native state writer. *)
val validate : ?restart:bool -> ?restart_to:Stage.t -> ?check_external:bool
  -> ?location:string -> compatibility:Identity_json.t -> string -> (t, string) result
val inspection : compatibility_source:[ `Expected | `Recorded ] -> t -> Identity_json.t
