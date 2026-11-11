(** Explicit observed-runtime transport, not runtime policy or a flow runner.
    Native commands are supported; this does not advertise native stage support.
    Callers own readiness probes, backend support resources and wire validation. *)
type optional_string = Absent | Null | Value of string
type flags = Isolated | Bytecode_disabled
type native = private { interpreter : string }
type docker = private
  { executable : string
  ; rootless : bool
  ; server_version : string
  ; daemon_id : optional_string
  ; name_on_server : optional_string
  ; declared_ref : string
  ; image_id : string
  ; repo_digests : string list
  ; os : string
  ; architecture : string
  ; variant : optional_string
  ; flags : flags
  }
type binding = private Native of native | Docker of docker
val native : interpreter:string -> binding
val docker
  :  executable:string -> rootless:bool -> server_version:string
  -> ?daemon_id:optional_string -> ?name_on_server:optional_string
  -> declared_ref:string -> image_id:string -> repo_digests:string list
  -> os:string -> architecture:string -> ?variant:optional_string
  -> flags:flags -> unit -> binding
(** Constructors raise [Invalid_argument]. Paths are lexical, never realpath'd. *)

val of_json : Identity_json.t -> (binding, string) result
val parse : string -> (binding, string) result
val to_json : binding -> Identity_json.t
(** Closed schema-1 parsing, including optional observed engine/platform fields. *)

val clean_environment : string list -> string list
(** Sanitize a complete supplied environment, without consulting or editing ambient
    state. Removes Python/Tcl/loader and runtime-selection overrides. *)

val owned_name : unit -> string
val docker_command
  :  docker -> project:string -> pdk_root:string -> name:string
  -> ?workdir:string -> ?entrypoint:string -> ?pdk:string
     -> ?extra_mounts:string list -> ?workers:int -> ?pids_limit:int -> uid:int -> gid:int -> unit -> string list
(** Prefix only; image ID is authoritative, declared_ref is evidence only.
     Mount syntax rejects colon paths rather than silently mounting another path.
      [workers] bounds container quota and thread environment (1..20, default 20).
      [pids_limit] independently caps all Linux tasks (1..20); absent by default.
      It counts controller threads too and is never inferred from CPU quota. *)

type invocation
val process_specification : invocation -> Process.t
val container : invocation -> (docker * string) option
val native_invocation
  :  native -> arguments:string list -> cwd:string -> environment:string list
  -> stdout:Process.output -> stderr:Process.output -> invocation
val docker_invocation
  :  docker -> project:string -> pdk_root:string -> ?workdir:string -> ?pdk:string
     -> ?extra_mounts:string list -> ?workers:int -> ?pids_limit:int -> ?entrypoint:string -> interpreter:bool -> arguments:string list
  -> cwd:string -> environment:string list -> stdout:Process.output
  -> stderr:Process.output -> unit -> invocation
(** Fresh owned name per invocation. [interpreter=true] prepends the bound flags
     and requires the python3 entrypoint; false runs an explicit tool argv.
     [extra_mounts] are explicit same-path writable directories, validated with
     the same absolute/colon restrictions as the main workspace mount.
    Invocations are single-use, including after a failed launch. *)

type removal = { result : Process_supervisor.result; diagnostic : string }
val metadata_invocation : ?workers:int -> ?pids_limit:int -> docker -> arguments:string list -> environment:string list
  -> stdout:Process.output -> stderr:Process.output -> invocation
(* Mount-free, read-only, network-disabled metadata probe, with owned cleanup. *)
type cleanup = { attempts : removal list; error : string option }
type result = { process : Process_supervisor.result; cleanup : cleanup option }
val succeeded : result -> bool
val run
  :  ?timeout:float -> ?term_grace:float -> ?cancel:(unit -> int option)
  -> ?cleanup_timeout:float -> invocation -> result
(** Main-thread synchronous scope. Uses Process_supervisor for launch and every
    removal. After clean client completion, one successful removal or definitive
    absence ends cleanup. Failed removal retries within a separate deadline
    (default 5s). Uncertain launch/nonzero/signal/timeout/cancellation retries
    throughout that deadline, even after absence, to cover in-flight daemon
    creation. A daemon committing after that deadline cannot be
    ruled out: cleanup is bounded, not an atomic Docker cancellation protocol.
    Retries are spaced by up to 50ms; no attempt starts in the final 150ms reserved
    for engine startup. Success records the last removal/absence observation,
    not a transactional absence guarantee. Budgets <=150ms cannot prove cleanup.
    Removal uses cwd / and the launch's cleared environment, so a missing/deleted
    project directory does not prevent cleanup.
    Removal diagnostics are capped at 8192 bytes. Cleanup errors veto success but
    preserve primary exit/signal/timeout/failure; new cleanup signals veto success.
    Invalid durations are rejected before launch. No shell, pull, tag resolution,
    resource copying, image/engine probing or publication occurs. *)
