(** Bounded, host-only comparison of exactly two explicit retained targets. No
    execution, publication, rendering or current catalog/PDK discovery. Unknown
    required controls withhold deltas; the intended axis never establishes cause.
    Whole response <=1MiB, without pagination or silent truncation. *)
type target = Bundle of string | Coarse_run of string | Execution of string
type axis = Hardware | Resource_policy | Flow_configuration | Descriptive
(** Explicit workload results bind to each target's verified bundle and hardware,
    never to an execution attempt inferred from that bundle. Functional simulation
    metrics use workload-family controls. Every source generation is replayed;
    inconsistent associations or stale evidence return [Error]. After all deep
    replays, a descriptor-safe final raw-byte audit covers the combined consulted
    closure (at most 16384 distinct paths / 2GiB; existing reader limits still
    apply), plus checks-directory existence, resolution and postcheck discovery
    membership (at most 8192 child entries per directory). Existing admitted target
    aliases are pinned/rechecked; workload-relative references remain no-follow.
    No further deep source reads follow that audit. This sequential fence
    is not an atomic cross-file snapshot or adversarial ABA-history detector. *)
val inspect : left:target -> right:target -> axis:axis ->
  ?left_workload:string -> ?right_workload:string -> unit -> (Evidence_json.t, string) result

(** CLI codecs, rejecting unknown spellings and empty paths. *)
val target : kind:string -> path:string -> (target, string) result
val axis : string -> (axis, string) result

(** Pure registry-policy seam. Inputs are already verified measurement projections;
    this function does not grant source authority. Used by focused scope/arithmetic
    tests. Production [inspect] supplies projections only from retained readers. *)
val compare_metric : axis:axis -> left:Evidence_json.t -> right:Evidence_json.t -> Evidence_json.t
