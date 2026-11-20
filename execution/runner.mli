(** Synchronous bounded checked-plan runner; no scheduler, signal handlers,
    probes, or fine-step observations. Callback owners must bound their own IO. *)
module Stage = Execution_plan.Stage
module Action = Execution_plan.Action
type status = Unreached | Skipped | Running | Completed | Failed | Interrupted | Accepted | Rejected
val status_name : status -> string
type error_kind = Failure | Cancellation of int
type error = private { kind : error_kind; exit_code : int; message : string; secondary : string list }
val failure : ?exit_code:int -> ?secondary:string list -> string -> error

(** Positive POSIX signal number, not OCaml's negative [Sys.sigint]. *)
val cancellation : ?secondary:string list -> signal:int -> string -> error
exception Callback_error of error

(** Use [error] for actual failure/cleanup evidence even when returning an
    allocated run via [Run_result]. It must agree with status and exit code;
    successful completions have no error. Without it, nonzero completion is still
    a failure fact but has no invented diagnostic. Report rejection is a verdict,
    not an execution exception. *)
type completion = { status : status; exit_code : int; error : error option }
type occurrence = private
  { occurrence_id : string; action_key : string; status : status
  ; exit_code : int option; duration_seconds : float; best_effort : bool
  ; error : string option; verdict : status option }
type step = private
  { operation : Action.operation; status : status; exit_code : int
  ; duration_seconds : float; best_effort : bool; error : string option }

(** Publication attestation in the existing Python reference format. [reference]
    checks syntax, not files; only a publisher owner may attest to promotion.
    Prefer [of_checkpoint_publication] for the actual writer. *)
type reference = private { boundary : Stage.t; path : string; identity : string; restart_targets : Stage.t list }
val reference : boundary:Stage.t -> path:string -> identity:string -> (reference, string) result
val of_checkpoint_publication : Checkpoint_publisher.publication -> reference
val of_checkpoint : path:string -> Checkpoint.t -> reference
val reference_json : reference -> Identity_json.t
val reference_matches : ?equal:(Identity_json.t -> Identity_json.t -> bool) ->
  legacy_routing:bool -> reference -> Identity_json.t -> bool
type 'checkpoint publication = { evidence : reference; checkpoint : 'checkpoint }
type 'checkpoint source = { boundary : Stage.t; checkpoint : 'checkpoint }
type ('run, 'segment) outcome =
  | Run_result of 'run * completion
  | Segment_result of 'segment * completion
  | Service_result of completion
  | Archive_result of string * completion
type ('run, 'checkpoint) context =
  { occurrence_id : string; best_effort : bool; run : 'run option
  ; source : 'checkpoint source option }
type lifecycle = Started | Published | Finished
type point = Validate | Cancel | Clock | Invoke | Publish | Hook of lifecycle | Observe | Persist
type diagnostic = private { action_key : string option; point : point; error : error }
type ('run, 'checkpoint) t = private
   { attempt_id : string; plan : Execution_plan.t; occurrences : occurrence list
   ; timing_intent : Identity_json.t option
  ; status : status; exit_code : int; run : 'run option; steps : step list
  ; archive : string option; artifacts : 'checkpoint publication list
  ; primary : diagnostic option; secondary : diagnostic list }
type ('run, 'segment, 'checkpoint) callbacks =
  { validate : Execution_plan.t -> (unit, error) result
  ; invoke : Action.t -> ('run, 'checkpoint) context -> (('run, 'segment) outcome, error) result
  ; publish : Action.t -> ('run, 'checkpoint) context -> 'segment -> ('checkpoint publication, error) result
  }

(** Checks initial handoff presence/boundary for schema 2/3; [validate] checks real
    compatibility. IDs are nonempty Unicode, NUL/slash-free. Invalid ID raises
    before callbacks. Ordinary callback exceptions become failure/2;
    [Callback_error] retains explicit interruption. All callback errors are facts.
    No preallocation exception escapes as it does in Python's run adapter.

    [persist] sees an immutable early running snapshot before invocation, each
    lifecycle transition, and terminal status. Initial save failure prevents work.
    First non-best-effort failure wins; later failures remain secondary. Failed
    coarse run/postcheck still permits best-effort collection when a run exists.
    Interrupted services with a run also permit best-effort collection, as in
    Python. Cancellation reported by [cancel] blocks all later actions. [cancel]
    is polled before actions and after invoke/publication; in-flight callbacks own
    supervised cancellation.
    Finished hooks run even if started/invoke/published fails. Promoted artifacts
    survive later hook failures; no rollback/retry. [observe] sees returned
    postcheck outcomes only. Finite nondecreasing clock, six-decimal duration.
     Hooks are coarse service facts, never simulated backend step observations. *)
(* [timing_intent] is the provider's versioned named expectation. It is serialized
   from the initial snapshot onward, saved before callbacks.validate or cancellation
   polling. Failed initial storage prevents validation/work. For these executions only, promotion is
   persisted before Published; failed persistence vetoes the required hook while
   retaining the promoted artifact. Ordinary records keep historical ordering. *)
val execute : ?initial:'checkpoint source -> ?timing_intent:Identity_json.t -> ?clock:(unit -> float)
  -> ?cancel:(unit -> error option)
  -> ?lifecycle:(lifecycle -> Action.t -> occurrence -> 'checkpoint publication option -> unit)
  -> ?observe:(Action.t -> ('run, 'segment) outcome -> unit)
  -> ?persist:(('run, 'checkpoint) t -> unit)
  -> attempt_id:string -> Execution_plan.t -> ('run, 'segment, 'checkpoint) callbacks
  -> ('run, 'checkpoint) t

(** Python Execution.as_dict finite terminal shape. Early status is [running],
     rather than Python's premature [completed]. No steps/diagnostic schema drift.
     Caller supplies actual run serialization; serializer errors propagate.
     Optional named-flow evidence is verified against this attempt and stage plan
     before serialization; without it, historical serialization is unchanged. *)
val to_json : ?methodology:Methodology_execution_record.t -> run_json:('run -> Identity_json.t) -> ('run, 'checkpoint) t -> Identity_json.t
val occurrence_json : occurrence -> Identity_json.t

(** Separate explicit version-1 envelope: primary and secondary errors, lifecycle
    points, codes, signals and secondary cleanup diagnostics. Persist separately
    when required; the legacy summary cannot encode hook/storage failures fully. *)
val diagnostics_json : ('run, 'checkpoint) t -> Identity_json.t
val steps_json : ('run, 'checkpoint) t -> Identity_json.t
