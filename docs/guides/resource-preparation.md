# Prepare and inspect resources

Resource preparation runs implementation elaboration and target resolution,
without emitting a bundle or running EDA. A project-owned executable selects and
constructs its OCaml project; the ASIC execution library retains the operation.
The generic CLI reads that historical result without loading project code.

## Limit the number of selected macro instances

Pass a budget to the project's resource policy when only some eligible memories
should use a particular technology macro. For example, to use the stock SRAM for
the first matching memory and allow flops for the rest:

```ocaml
let policy =
  Hardcaml_asic.Resource_policy.create
    ~default:Exact_or_flop_fallback
    ~macro_limits:[ "RM_IHPSG13_1P_256x16_c2_bm_bist", 1 ]
    []
  |> Core.Or_error.ok_exn
```

The key is the **technology macro name**, not a resource instance path. Each
selection of that macro consumes one slot, in resource **registration order**.
With the policy above, the second matching memory selects flops with reason
`Budget_exhausted { macro; limit = 1 }`. A shape with no matching macro instead
gets the ordinary `Flop_fallback` reason. An explicit `Flops` rule consumes no
slot. An `Exact` rule fails elaboration if its macro has no slot left: the policy
does not reserve capacity for later instances. If a particular memory must get
the macro, register it first or use rules to select flops for its competitors.

Unlisted macros have no limit. A zero limit permits no selections of that
macro; negative limits, blank names and duplicate keys are errors at policy construction.
`Chip_target.create` rejects any limit whose name does not exactly match a macro in
the target technology. A known macro may have a limit even if the design never uses it.
Every elaboration starts with fresh counts, including simulation, where policy
selection is still recorded although the memory uses its behavioral model.
The limit controls selection; it is not evidence that a selected macro fits or
survives synthesis. See [resource realization](resource-realization.md) for that
distinction and [segmented SRAM](segmented-sram.md) for qualified configurations.

## Consumer entry point

Link the executable against `hardcaml_asic.execution` (and its ordinary project
dependencies). Given the consumer's `make_chip_target`, `source_root`, selected
`operation_store`, and `json` option:

```ocaml
module E = Hardcaml_asic_execution
module J = E.Identity_json

let checked = function
  | Ok x -> x
  | Error e -> invalid_arg (J.error_message e)

let prepare ~operation_store ~source_root ~json =
  let request = checked (J.object_ [
    "selection", checked (J.string "explicit-flops")
  ]) in
  try
    let outcome = E.Preparation_service.run_cli
      ~store:operation_store ~request ~json
      ~prepare:(fun ~on_phase ->
        (* Allocation has already been persisted and delivered here. *)
        let chip_target = make_chip_target () in
        Hardcaml_asic.Chip_target.prepare_resources ~on_phase chip_target ~source_root)
      () in
    Stdlib.exit (Option.value outcome.record.exit_code ~default:1)
  with exn ->
    Printf.eprintf "prepare: %s\n" (Printexc.to_string exn);
    Stdlib.exit 2
```

Construct the project **inside** the closure, so constructor errors get durable
attempt records. Pass `on_phase` to `Chip_target.prepare_resources`. Caller-specific
request fields are JSON metadata, not command or shell instructions.

`run_cli` flushes an allocation event followed by a terminal event when `json` is
true. Human mode prints allocation and outcome summaries. It does not exit for
the consumer. Use `Preparation_service.run` instead for custom presentation;
its optional `allocated` callback receives labelled `directory` and `attempt_id`.

## Historical inspection

```sh
hardcaml-asic-flow preparation-inspect "$ATTEMPT"
hardcaml-asic-flow preparation-inspect "$ATTEMPT" --json
hardcaml-asic-flow preparation-inspect "$ATTEMPT" \
  --current-identity "$CURRENT_PREPARATION_ID" --json
```

`ATTEMPT` is the directory from the allocation receipt. JSON inspection contract
`hardcaml-asic.preparation-inspection`, schema 1, returns the durable `operation`,
the complete successful `preparation` payload (or null), and `comparison`.
Human output shows the project, top, mode, identities and resource-by-resource
request, policy, selection reason, actual role and physical linkage.

Without a current identity, comparison is `not_requested`. Explicit comparison
uses the **preparation identity**, not the source/configuration/circuit identity,
bundle identity or attempt ID. It reports `matches`, `differs`, or `unavailable`
when the attempt has no committed successful snapshot. The reader does not
calculate a current identity or reconstruct current project state.

Inspection validates exact file digest/length and the library's strict snapshot
schema/content identity. It reads no source files and probes no tools or PDK.
Reader exit 0 means successful inspection, including valid failed/interrupted or
still-running attempts; invalid queries or corrupt retained evidence exit 2.
The capability is advertised as `preparation1` by `--version --json`.

## Storage, status and result boundary

Each invocation allocates a fresh attempt beneath the explicitly supplied store:

```text
<store>/<attempt>/operation.json
<store>/<attempt>/resources.json
```

The versioned operation descriptor identifies kind `elaborate_resolve_resources`,
category `preparation`, phases and prerequisites. It is independent of physical
stage enums. Its lifecycle is `running → completed | failed | interrupted`.
The workflow descriptor now writes schema 2, with actual phase order
`circuit → snapshot → source_after`. Readers also accept the **exact original
schema-1 descriptor**, retaining its historically reversed advertised order
without rewriting it. Other versions or descriptor variants are rejected.
The outer operation record and inspection envelope remain schema 1; A's payload
and preparation identities are unchanged by this descriptor correction.
`operation.json` is published before invoking project code. On success,
`resources.json` is written and checked before the completed operation record
references its exact bytes and preparation identity. Terminal attempts are not
resumed or rewritten; retries allocate distinct attempts.

A failed terminal publication raises instead of returning completed. A snapshot
file left beside a `running` record is not committed success. Failed/interrupted
records have diagnostics and no success reference; they are never represented as
a successful empty inventory. A genuinely resource-free successful design has an
explicit successful snapshot containing an empty inventory.
Before returning an outcome the runner rereads the terminal record, requires
byte-for-byte agreement with the intended record, and strictly decodes it.
Verification failure propagates; it does not rewrite or repair terminal evidence.
Malformed callback failure records are validated inside the protected operation
and retained as `failed` / `preparation_exception`, with no successful snapshot.

Both evidence leaf entries must be **regular files, not symlinks**. Reads use
`lstat`, nonblocking open, `fstat` before reading, and final identity/type checks,
so replacement by a FIFO cannot hang the reader. Operation files are limited to
4 MiB and resource snapshots to 64 MiB, including growing-file detection.
Parents are caller-owned stable local directories; this is not a hostile-owner
filesystem sandbox. An explicitly selected attempt directory can itself be
reached through a caller-owned directory link; evidence leaf links are rejected.

SIGINT/SIGTERM handlers are scoped, main-thread-only and non-reentrant, and are
restored after the operation. Cancellation is cooperative at phase boundaries
and snapshot publication; an arbitrary constructor is not safely forcibly
preemptible. Terminal commit has an explicit cutoff: the runner blocks
SIGINT/SIGTERM and makes a final check of cancellation already observed by its
handler. Observed cancellation produces an interrupted terminal record.
After that check, the terminal save and exact readback are **noncancellable**.
Signals pending during commit are delivered under the previous handlers after
restoring those handlers and the caller's original signal mask, including when
commit verification fails. POSIX coalescing applies; a previously blocked signal
remains blocked according to the caller's original mask. An already-terminal
record is never rewritten because of a later signal.

A previous default signal handler may terminate the process after a valid
terminal record, before `run_cli` can deliver terminal JSONL. Thus successful
recording and successful delivery/process exit remain separate facts; no signal
is silently consumed as successful execution.
Interrupted launches exit 130/143. SIGKILL or a crash can leave `running`; readers
preserve that fact without inventing an outcome or inferring process liveness.
There is no cancel-by-persisted-PID command.

Storage uses local atomic visibility, with no fsync/power-loss guarantee. Source
observation identities do not prove the compiled constructor came from those
source bytes. Preparation has no bundle identity and does not verify collateral
files, tool readiness, synthesis retention or physical acceptance.

## Focused verification

From the ASIC checkout:

```sh
flock /tmp/opencode/asic-preparation-build.lock \
  opam exec --switch=5.2.0+ox -- dune build -j6 @test/preparation
```

This exercises the actual typed consumer and public CLI helper, durable failures,
identity comparison, byte/content mutation, publication failures/cancellation,
signal restoration, crash retention and historical source independence. It
performs read-only source Git observation, with no Git mutations, EDA or PDK.
