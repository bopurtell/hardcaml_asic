(** Authoritative bounded execution over caller-PREPARED paths. No preparation,
    probes, CLI policy, scheduler or backend fine-step event synthesis.
    Stable trusted parents and exclusive use of the workspace are required. *)
type t
type checkpoint = private { path : string; manifest : Checkpoint.t }
type segment = private
  { request : Backend_protocol.request
  ; process : Execution_transport.result
  ; reply : Backend_protocol.reply
   ; report : Segment_report.t
   ; transport_record : string
   ; transport_result : string
  }
val bridge_sha256 : string
val protocol_sha256 : string
val macro_runtime_sha256 : string
val verify_reply : Backend_protocol.request -> Backend_protocol.reply
(* Correlated protocol-v1 reply and actual regular artifact hashes/lengths.
    Raises [Runner.Callback_error] on invalid evidence. *)
val create : binding:Execution_transport.binding -> workspace:string -> project:string -> pdk_root:string
  -> checkpoint_store:string -> compatibility:Identity_json.t
   -> environment:string list -> timeout:float
     -> ?workers:int -> ?cancel:(unit -> int option)
     -> ?initial_checkpoint:string -> ?verify_runtime:(unit -> (unit, string) result)
     -> ?verify_inputs:(unit -> (unit, string) result)
     -> ?verify_resolved:(string -> (unit, string) result)
    -> ?failure_exit_code:(Backend_protocol.operation -> Execution_transport.result -> int option)
   -> unit -> (t, Runner.error) result

val process_error : Execution_transport.result -> Runner.error
val signal : int -> int
val process_json : Process_supervisor.result -> Identity_json.t
(** Shared outcome/cleanup mapping for supervised runtime probes and execution. *)

(** [failure_exit_code] optionally maps a failed transport's reported status at
    the caller boundary. Raw process evidence is retained; cancellation always
    preserves its signal-derived status and successful transport is never mapped.
    The default preserves process statuses. CLI4 uses 2 for timeout/publication
    validation errors, matching its historical exception convention. *)

(** Production requires Docker and the exact demonstrated image. Compatibility
    has the existing seven fields and must bind these exact paths/runtime/bridge.
    Workspace, project, PDK, store and candidates directory already exist; project
    and store are disjoint descendants of the mounted workspace. No adoption,
     copying, directory creation or runtime readiness inference. Optional runtime
      verifier runs before and after each successful backend call; preparation
      supplies bound engine/image re-probes. [verify_inputs] retains an independent
      checked input baseline; it runs at validation, before/after successful calls,
      at publication entry and immediately before promotion. [verify_resolved]
      checks each candidate/staging resolved.json against the selected contract.
      Named Stage_preparation supplies both; defaults preserve ordinary callers.
      Stable trusted parents and exclusive workspace use are still required; these
       checks do not make arbitrary adversarial filesystem races impossible. *)
(* [workers] defaults to6 and admits1..6; the qualified bridge bounds its native
   executor/tool settings to6 (STA1). [cancel] is forwarded to every supervised
   backend call, including publication validation, and polled by execute. *)

val initial : t -> checkpoint Runner.source option

val transport_records : t -> (string * string) list
(** Actual per-call immutable launch/terminal paths, in invocation order. A missing
    terminal file denotes incomplete recording, never successful execution. *)

val diagnostics_json : t -> ('run, 'checkpoint) Runner.t -> Identity_json.t
(** Runner diagnostics with additive transport-record references. Summary and
    checkpoint schemas retain compatibility with existing readers. *)

val callbacks : t -> (unit, segment, checkpoint) Runner.callbacks
val execute : ?persist:((unit, checkpoint) Runner.t -> unit)
  -> ?lifecycle:(Runner.lifecycle -> Execution_plan.Action.t -> Runner.occurrence
    -> checkpoint Runner.publication option -> unit)
  -> attempt_id:string -> Execution_plan.t -> t -> (unit, checkpoint) Runner.t

(** Source is taken only from Runner context. Each invocation revalidates it,
    resources, reply correlation and artifact bytes. Publication rechecks the
    remembered completion, then supplies actual supervised runtime validation to
    Checkpoint_publisher. Only successful atomic publication advances Runner.
    Requests/replies use TAG-request.json / TAG-reply.json in project; merged logs
    use TAG.log in workspace. All request/log writes are exclusive and retained.
    Tags are boundary-32hex or validate-boundary-32hex, matching Python.
    TAG-transport.json and TAG-transport-result.json in workspace are exclusive
    immutable launch/terminal evidence correlated to the request identity. The
    launch is recorded before execution; terminal evidence includes primary
    process and all cleanup outcomes, including successful cleanup. Binding,
    argv, owned container, host UID/GID, explicit resources and timestamps are
    retained; the ambient environment is never serialized. Recording failure
    vetoes success and is secondary to an existing process failure/cancellation. *)

module Testing : sig
  (** Explicit fixture-only native transport. Does not advertise native stages.
      Executes [interpreter -I -B fixture bridge request] through the same actual
      Execution_transport/Process_supervisor. Compatibility still requires the
      exact-image schema; it is fixture evidence, not real runtime readiness. *)
  val create_native_fixture : fixture:string -> interpreter:string
     -> workspace:string -> project:string -> pdk_root:string -> checkpoint_store:string
    -> compatibility:Identity_json.t -> environment:string list -> timeout:float
      -> ?workers:int -> ?cancel:(unit -> int option)
      -> ?initial_checkpoint:string -> ?verify_runtime:(unit -> (unit, string) result)
      -> ?verify_inputs:(unit -> (unit, string) result)
      -> ?verify_resolved:(string -> (unit, string) result)
      -> ?failure_exit_code:(Backend_protocol.operation -> Execution_transport.result -> int option)
     -> unit -> (t, Runner.error) result
end
