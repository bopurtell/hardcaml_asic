# Provisional provider observation fixture contract v1

**Everything in this directory is PROVISIONAL, SYNTHETIC and FIXTURE-ONLY.**
Capabilities are **MODELED**, not claims about the installed CLI. No EDA was run.
This is a data/spec corpus for the independent pure standard-library validator and
replayer in `test/provider_observation_contract.py`, exercised by
`test/test_provider_observation.py`. It installs no provider API or live store. The
expectations are hand-authored independently of the reducer. Passing JSON
and reference checks alone is not executable contract acceptance or runtime publication proof.

The governing boundaries are [architecture](../../../../docs/architecture.md),
and the existing
[execution/postcheck policy](../../../../docs/guides/flow.md). Current library
names are `Elaborated_project` and `Resolved_flow`. `build-B` still denotes an
immutable **emitted build identity**, not either in-memory library value.

## Files and common encoding

| File | Contents |
| --- | --- |
| `describe.json` | Modeled version, scope, stream, writer, time and retention policy |
| `scenario.json` | Immutable definitions, request intents, provenance, results, evidence inventory |
| `updates.json` | 34 append-only facts, the sole authority for visibility and current state |
| `expected_snapshots.json` | Complete hand-authored state at six exact cursors |
| `queries.json` | Read-only correlation/list/inspect/attach/reconnect expectations |
| `legacy.json` | Separate minimal old schema1 input and conservative observation |
| `replay_cases.json` | Independent positive/negative transformations with response and recovery |
| `evidence/*.txt` | Five small actual synthetic byte files, SHA-256-pinned in scenario |

Every JSON document has `contract = hardcaml-asic.provider-observation-fixture`,
integer `version = 1`, and `designation`. This is **not** `run.json.schema_version`.
Only the nested legacy input uses `schema_version: 1`. No schema migration is
implied. Contract/version mismatch is `UNSUPPORTED_CONTRACT` before replay.

UTF-8 JSON only; reject duplicate object keys, unknown fields/kinds, nonfinite
numbers, booleans in integer positions, absent required fields and incorrect row
lengths. All fields shown for a record kind are required; only case-kind-specific
patches are optional. Rows are arrays with exact positional column definitions in
their containing file. Objects are unordered; arrays are ordered. IDs and reason
codes are nonempty ASCII case-sensitive strings. IDs are opaque, never parsed for
identity or chronology. Integers are nonnegative and at most 2^53-1; sequence,
revision, generation and attempt are positive (cursor/time/readiness may be zero).
`null` is explicit absence/unknown, never zero, pass or an omitted value.

Every nested record inherits the document's synthetic designation. All IDs are
scoped by `(project_id, provider_id, store_id)` from describe; other files inherit
that exact scope unless explicitly repeated. Never merge stores by display name,
build name or local path. Cursor identity is `(scope, stream_id, sequence)`;
the JSON `cursor`/`after_cursor` integer is shorthand under its document's stream.
Scope mismatch is `SCOPE_MISMATCH`; a noncurrent/retired stream is `RESET_REQUIRED`.
Future cursor beyond head is `CURSOR_AHEAD`, not a partial successful response.

Slice2 clarification: otherwise-unspecified validation failures use
`INVALID_RECORD` for encoding/shape/types/unknown fields or kinds,
`DUPLICATE_ID` for duplicate definition identities or request keys,
`INVALID_REFERENCE` for absent or inconsistent cross-table/ownership links,
`INVALID_PROVENANCE` for producer/metric/time/first-publication disagreements,
`INVALID_EVIDENCE` for unsafe, missing, nonregular or hash-mismatched evidence,
`INVALID_REVISION` for per-run revision discontinuity, and `INVALID_CURSOR` for
inconsistent snapshot/prefix cursors or backwards query ranges. Existing named
transition, intent, readiness and replay codes below retain their meanings. This
fills a missing error-code vocabulary; no fixture facts or oracle values change.

## Definitions, identities and request intent

Scenario objects are a fixture lookup catalog, **not visible live state**. Reading
the whole file must not make future runs, operations, errors or results observable.
The bundle/tool/constraints/configurations are preexisting immutable definitions.
Operations appear only at allocation; execute steps appear only with their run;
standalone steps appear with their operation. The legacy run is never inserted
into the main stream or two-run list.

Both main executions use the **same immutable build-B and config-B**, with
`enable_optional_timing: false`. The bundle explicitly binds `configuration_id`;
every request/run must match it, including standalone collect/postcheck and the
preallocation rejected execute. The latter has an absent **execution prerequisite**,
not a different hardware configuration or an invalid canonical intent. The three
execute intents are identical; their request keys/operation identities differ.

The steps table is a **bounded synthetic occurrence plan**, not a full literal
LibreLane plan or an expansion of build configuration into all flow steps. Only
`b-gated` models the optional gate; the ordinary timing measurements in A and
`b-active` are not controlled by that optional gate. A does not claim an exhaustive
inventory of gated steps. The modeled parent relationship is **operation ->
occurrence** via each step's `operation_id`; deeper nesting/hierarchy is unmodeled.
Different selected occurrences do not imply different build-derived settings.

- `operations`: ID, explicit outer `attempt`, scoped `request_key`, canonical
  `intent`. `op-b` is an intentional second implementation attempt, new key and
  run, not a resumed `op-a`. Attempt numbers are descriptive within the intended
  action's attempt lineage, not an ordering key or globally unique ID. They are
  authored metadata in this corpus; no lineage inference or allocation algorithm
  is required. Operation IDs distinguish attempts even when their numbers match.
- `runs`: ID, immutable build/configuration, owning implementation operation,
  requested/completed stage. Both request `full`; `completed_stage` stays null.
  Completing synthesis does not set completed full stage.
- `steps`: occurrence ID, run, operation, positive attempt, type and display name.
  `(operation_id, step_id, attempt)` identifies the producer. The occurrence ID
  is unique in this scope. A new attempt needs a new occurrence ID in this v1;
  never overwrite a terminal occurrence. Same `timing` type/name is not identity.
- Measurement/result/artifact IDs are immutable and unique within their tables;
  references have a specific target table. Equal measurement values do not dedupe
  IDs. Build and run IDs are separate; collect/postcheck never allocate runs.

Intent has exactly seven required fields:

| Field | Canonical form |
| --- | --- |
| `action` | `execute`, `collect`, `postcheck` |
| `build_identity` | Exact existing bundle ID |
| `target_run_id` | null for execute; explicit existing run for collect/postcheck |
| `stage` | `full` (only modeled stage in this corpus) |
| `configuration_id` | Exact configuration bound by the bundle; target run must also match |
| `checks` | `full` for execute/postcheck; `none` for collect |
| `archive` | Boolean false (only modeled value) |

These JSON values **are already normalized**: no defaults, path expansion,
whitespace trimming, case folding, implicit newest run, free-form flags or numeric
coercion. Reject noncanonical forms as `INVALID_INTENT` before allocation. Intent
equality compares the seven typed values, ignoring object key order only. Compare
under the full scope plus request key; request key and display labels are not intent.
An existing key with the same intent returns its original operation and current
associated run (possibly null), even after rejection/failure. A different intent
under that key is `REQUEST_CONFLICT`, never a replacement. An intentional retry
uses a new key and operation attempt; implementation retry allocates a new run.
There are no generic resume semantics.

Canonical validation precedes key binding/conflict handling: validate field types,
action/checks/target relationships, existing target run at the request cursor, and
bundle/run configuration agreement. Execution prerequisites are checked separately
after allocation. The conflict example submits the individually valid `op-collect`
intent at cursor 34 under `req-a`, already bound to execute. It is therefore
`REQUEST_CONFLICT`, not `INVALID_INTENT`; protected configuration is not overridden.

One coordinated mutator owns allocations and journal commits. It briefly serializes
the shared-store sequence/ID/key allocation, not entire tools across different runs.
A run is busy from owning operation allocation/binding until its terminal outcome,
including gaps between implementation failure and outer failure. New same-run
mutations return `BUSY`, no queue, no event, no operation/key reservation. Idempotent
key lookup/conflict checks precede busy checking. Accepted standalone allocation
binds its explicit existing run immediately. New execute allocates its operation
and request binding first; run allocation is a separate later fact. A prerequisite
rejection can therefore have an operation but **no run**, as sequences 1–2 show.

Generation 7 owns this modeled stream. Every append must match the coordinator's
current generation; stale generation is `FENCED` before writing, with no sequence
consumption. A generation is not a process-kill mechanism. Takeover/reconciliation
is outside this corpus; no timeout implies completion or interruption. Readers
never send execution acknowledgments, mutate state or hold up tools. Reader
disconnect/reconnect is solely observation.

## Journal and exact state transitions

Each event row has exactly the seven columns in `updates.json`. Event identity is
`(scope, stream_id, sequence)`. Sequences are contiguous 1–34 across the **store**,
not per run. `run_id` and `run_revision` are either both null or both nonnull.
Run allocation is revision 1; **every** accepted event naming that run increments
its revision once, including standalone operation allocation/outcome, error facts
and result publication. Pre-run operation facts consume store sequences only.
Revisions do not count duplicates. Sum of final revisions is 30; four pre-run facts
(1, 2, 3, 20) account for the other four store sequences.

Time is deliberately synthetic: integer seconds since the descriptor epoch. An
event's `recorded_at` equals its sequence by definition of this corpus, not by a
general clock ordering promise. Measurement and publication times use this clock.
State ordering always uses sequence, never time or a filename.

Initial cursor 0 has empty operations/runs/steps/errors/published_results. State
has exactly `cursor` plus those five arrays, with the row layouts in expectations.
Arrays preserve allocation/first-publication order, never lexical ID order.
Static metadata is joined by ID from scenario; no hidden reducer state is needed
except accepted event identities for duplicate validation (retained journal).

| `kind` | Exact `data` fields and transition |
| --- | --- |
| `operation_allocated` | `{}`. Add `[id, run_id, running, null]`, reserve request binding. Null run only for execute. Existing-run allocation adds that operation's declared steps as `not_started`. |
| `run_allocated` | `{}`. Only once for a running execute with null run. Bind operation; add run revision 1, implementation `not_started`, null implementation reason/result, check `not_evaluated`, acceptance `pending`. Seed all declared steps of that operation as `not_started`. |
| `implementation` | `status, reason`. Only owning execute may change implementation: `not_started -> running -> completed|failed|interrupted`. Terminal states immutable. `completed`/`running` require null reason; failure/interruption require reason. Completion is never inferred from steps. This corpus uses failure/interruption only. |
| `step` | `step_id, status, reason`. Matching allocated run/operation/attempt: `not_started -> running -> completed|failed|interrupted`, or `not_started -> skipped`. All terminals immutable. Skip requires explicit matching gate, condition, observed value and reason. Failure/interruption require reason; running/completed require null. |
| `deferred_error` | `error_id`. Append the immutable error while its producer step is running. Set check and acceptance `failed`, **do not** change step/implementation/operation state. Never clear the error. |
| `result_published` | `result_id`. Validate full ready reference closure and publisher; append ID to history, replace only that run's latest result pointer. No step/implementation/check/outcome changes. |
| `operation_outcome` | `status, reason`. `running -> succeeded|failed|interrupted|rejected`, exactly once. Success requires null reason; others require a reason. Does not change implementation or step states. Release the run's busy ownership. |

All event operation/run/step/result/error references must agree, operations must be
running before their facts, and prerequisite entities must already be allocated.
Postcheck rejection has zero steps/tool executions. Rejected execute must have no
run or started steps. Failed/interrupted execute outcomes require an allocated,
explicitly terminal implementation (`completed`, `failed` or `interrupted`), even
when no step is running. Only rejection can terminate execute before run allocation
in this bounded model; aborting an allocated but not-started implementation is not
modeled. Implementation completion can still be followed by outer failure or
interruption; the two outcomes and their reasons need not match. No outcome
synthesizes missing implementation/step transitions or releases BUSY early.
Successful collect requires its collection step completed **and a new result
advertised by that same operation**; an older result or step completion alone is
insufficient. Result publication alone does not complete the operation either;
successful execute would require completed implementation and passing acceptance,
which this corpus does not model. Step start for execute requires implementation
running; standalone collect may start on failed implementation. Implementation may
become terminal only after its running steps are terminal; unreached steps remain
`not_started`, never implicitly skipped. All transition violations are
`INVALID_TRANSITION` with no application of the offending event.

Check status is `not_evaluated` until the error at 13 makes A `failed`. No passing
check exists here. Acceptance is initially `pending`, becomes `failed` on any
deferred check error or failed/interrupted implementation, and stays failed.
Implementation `completed` alone cannot establish acceptance; no event here can
set acceptance to passed. Rejection of later postcheck neither erases A's check
verdict nor fabricates a new executed check. Collect success is publication success,
not successful execute or signoff. Legacy `completed` has a separate unknown
aggregate/check/history observation, not this stream's initialized pending state.

## Results, provenance and modeled readiness

Result rows have the nine columns declared in scenario. `slots` has exactly six
keys: resources, timing_reference, timing_latest, final_timing, power, cts. Each
slot is `[availability, measurement_id, reason]`. Available requires an existing
measurement and null reason. `pending`, `unavailable`, `not_requested`, and
`unsupported` require null measurement and a nonnull defined reason. Absence is
never numeric zero. Resources references mapped area; all timing slots reference
setup slack. Result absence before any publication is independently observable:
inspect returns `[pending, publication_pending]` while implementation is nonterminal;
after failed/interrupted implementation it returns `[unavailable, implementation_reason]`.
If any snapshot exists it returns `[available, null]`, regardless of individual slots.

Measurements require all shown provenance fields: run, producer operation/occurrence/
attempt, synthetic tool ID, metric/value/unit, stage/corner/mode, constraint ID,
evidence IDs, `measured_at`, `published_at`. Numeric value must be finite; null
corner means not applicable for synthesis, never a guessed corner. Times satisfy
producer start <= measured_at <= producer completion <= first published_at.
`published_at` is the **first** result advertisement containing the measurement,
not every inheritance. New result publisher has its own operation/step/attempt and
time. Publisher step must be completed, not necessarily the measurement producer.
Result time equals its publication event's sequence.

Result-a2 inherits m-area. Result-a3 keeps `timing_reference = m-timing-1` while
`timing_latest = m-timing-2`: equal -0.25 values, different IDs, producer occurrence
and measured/published times. Result-a4 inherits all those origins unchanged under
op-collect and adds diagnostic evidence. Old snapshots remain immutable: result-a3
still says final timing pending even after later routing failure. Only result-a4
says unavailable. An observer must combine historical result context with exact
current run state, not rewrite old evidence.

Timing evidence is split into two immutable reports. `artifact-timing-1` contains
only m-timing-1 and is complete at boundary 10, advertised at 11. It contains no
second measurement or future error record. `artifact-timing-2` contains m-timing-2,
measured at 12, complete at boundary 14 and advertised at 15. No completed second
report is claimed before that boundary.

At 13, `error-timing` records a deferred check of the **existing negative m-timing-1**
from the already-ready first report, discovered while a-timing-2 runs. Its
`measurement_id` identifies the checked measurement; `artifact_id` must belong to
that measurement's evidence. Its operation/step/attempt identify the **discoverer**,
not a replacement measurement producer. The checked measurement must already have
been published, in the same run. This error neither depends on the second report
nor claims that report completed. The eventual equal-valued m-timing-2 remains a
new measurement with its own producer, times and distinct evidence.

`ready_before: N` is a **model** assertion that an immutable object was complete
at logical boundary N. Advertising it at sequence S requires **N < S**, strictly.
Check this transitively: result -> measurements -> tool, constraints and artifacts;
constraints -> artifact; result -> diagnostic artifacts; run/operation -> build
and config; build -> config and input artifact. Deferred error -> checked
measurement and its evidence must likewise be ready before its event. All IDs in
these chains must exist and agree on run and producer where applicable; error
discoverer and checked measurement producer may differ as specified above.
Readiness does not itself expose an object: only a
publication/error/run fact does. Static tables can contain future-ready definitions.

Artifact paths are relative to this directory, contained regular files, no symlinks,
absolute paths or `..`; SHA-256 is lowercase hex over exact checked-in bytes,
including LF endings. Every artifact is reachable, with no missing external file
dependencies. Build/tool/config/constraints are inline immutable definitions; no
real manifest or installed tool version is claimed. Each timing report contains
only its own synthetic measurement; publication of the first report cannot expose
the later measurement through a shared evidence file.

To test ordering, vary the **model**, not file existence: the readiness cases set
m-area's boundary to 8, artifact-timing-1's boundary to 11, or artifact-timing-2's
boundary to 15 while attempting publication at that same sequence. All three
must reject the whole event atomically,
keep the last good cursor/state, and expose no result. Check every transitive
object, not only the root result. Checked-in bytes and matching hashes do **not**
prove atomic runtime publication, durable rename/fsync behavior or crash safety.

## Queries, replay and retention

All query responses carry the same scoped stream and requested exact cursor.
Request lookup joins only allocated operations at that cursor to their request
keys; unavailable keys return `NOT_FOUND`. List returns the complete run rows in
allocation order. Inspect returns exactly the matching run row plus its step rows,
operation IDs, error IDs, result history IDs and derived result availability.
Missing runs return `NOT_FOUND`. List and inspect must agree at the **same** cursor;
an inspect at 15 must not leak the later failure at 18 or collection at 31.

Snapshot-and-suffix starts with the complete expectation at snapshot_cursor and
applies exactly `(snapshot_cursor, through_cursor]`; reconnect similarly starts
after its durable cursor, with no execution acknowledgments. Compare against the
hand-authored destination checkpoint, and separately replay from cursor 0 to that
same checkpoint. This is observation reconstruction, not execution resumption.
Recovery validates the full retained prefix against the catalog in a separate
reader, then compares its projection to the supplied snapshot with exact typed
equality. The restored reader copies the supplied snapshot and applies only the
suffix to it; no destination oracle constructs its actual state. Snapshot shape,
integer types, histories and references must validate. A prefix is not trusted
merely because its last facts match. This is deliberately full-prefix validation,
not a fast or durable checkpoint store; a snapshot file alone is not a resumable
checkpoint. Authenticity and persistence of the catalog/journal remain external.

Each replay case starts independently with the unmodified scenario and the prefix
through `start_cursor` (or request `cursor`). `deliver`/`complete_lines` arrays
select journal rows by sequence. Patches use JSON Pointer on an event row or intent;
object patches select a scenario table record by ID. Apply a patch only to that
case's candidate input, never the retained accepted journal. `intent_from` copies
the indicated operation's canonical intent; the supplied request key replaces no
intent field. `expected` is an exact response projection: every listed field must
match; `appended`/`applied` count accepted facts, not attempted deliveries.

Duplicate means structurally equal decoded event (object order immaterial, arrays
ordered, primitive types exact). Compare the entire row, including generation.
Ignore identical already accepted sequence, without revision changes. A differing
row at an accepted sequence is `CONFLICTING_DUPLICATE`. A future sequence beyond
next is `GAP`; do not advance, silently skip or apply it out of order. Validate
duplicate/gap before candidate semantic checks; otherwise the conflicting result
reference would misleadingly report a readiness error. Stale writer checks happen
at append admission before journal acceptance. Unknown kinds are never ignored.

Framing case only: encode complete rows as UTF-8 JSON, one LF-terminated row per
line, followed by the literal `tail_utf8` without LF. A trailing incomplete line
is `INCOMPLETE_TAIL`, not a fact and not a sequence reservation. Malformed complete
lines are `INVALID_RECORD`; stop at last verified cursor. This does not specify a
live transport or filesystem engine. Recovery actions in cases are normative for
the model; readers never repair or truncate the producer's journal themselves.

Minimum guarantee: throughout an active stream's lifetime retain every accepted
fact, historical snapshot and its complete evidence closure, including failed and
interrupted runs. Cursor 0 remains replayable. Request key bindings and original
operation/run outcomes survive for the store lifetime, including stream retirement,
so a reset cannot turn a repeated key into a fresh execution. Retirement is explicit;
the expired-cursor case returns `RESET_REQUIRED` with no suffix/snapshot. Obtain a
new descriptor/snapshot, never splice old and replacement stream numbers. The case
does not invent replacement history or imply that retirement is automatic GC.

## Hand-authored checkpoint summary and limits

| Cursor | Exact expected observation |
| --- | --- |
| 3 | Rejected op has no run; op-a correlated to req-a, also no run; list empty |
| 7 | A revision 4, synthesis completed, no published result yet |
| 13 | A revision 10, second timing occurrence still running; deferred failed verdict checks m-timing-1/first report only |
| 15 | A revision 12, three result snapshots; second immutable timing report now advertised, equal value with distinct origin |
| 27 | A revision 16 failed; B revision 7 interrupted; skipped/interrupt/unreached distinguished |
| 34 | A revision 23, fourth result via standalone collect; postcheck REJECTED without execution; B unchanged |

The separate legacy companion has completed implementation but unknown execute,
checks, signoff and step history (`steps: null`, not a fabricated empty history).

Local slice1 sanity check, repeated after parent review fixes (2026-10-12): Python standard-library JSON parsing with
duplicate-key/nonfinite rejection passed for all seven documents; row shapes,
definition/event/checkpoint ID references, contiguous 34 sequence IDs and event
counts (A 23, B 7, unbound 4) passed. All five artifact SHA-256 references matched
the actual files; evidence inventory closure and LF/trailing-whitespace checks
passed. Shared immutable configuration/intent agreement, valid collect conflict
input, split timing evidence references and readiness metadata were also checked.
Checkpoint row values and query responses still use the same cursors/revisions;
their static provenance joins now resolve to the corrected shared configuration
and separate reports. This was an ephemeral read-only check, not a checked-in validator or a
reducer. State transitions, query execution and negative-case replay have **not**
received executable acceptance in slice1.

## Executable slice2 checks

From the repository root:

```sh
python3 test/test_provider_observation.py
dune build @test/provider-observation
```

The direct Python suite needs only the standard library. The focused Dune alias
runs that same suite with explicit helper and fixture-tree dependencies; it does
not invoke nested Dune or EDA. The model computes state only from definitions and
accepted events. All six complete snapshots remain independent test oracles.
Recovery initializes supplied snapshot state and independently replays the retained
prefix for validation, retaining its identities for duplicate checks and original
producer times. Candidate-only readiness fault tests change the candidate catalog
after restoring a valid history; they do not legitimize rewritten immutable history.
Admission returns logical proposals without reserving
keys or executing tools. These checks do not prove runtime locks, durability,
atomic publication or producer liveness; deeper live hierarchy is unmodeled.

This v1 deliberately fixes its small modeled vocabulary; it is not an extensible
scheduler. Unresolved production decisions are transport/CLI shape, actual
LibreLane lifecycle instrumentation, durable write/lock/fencing implementation,
crashed-writer reconciliation, archive/retirement mechanisms, and version evolution.
No passing-signoff event vocabulary or generic retry/resume machinery is defined.
These limits do not leave the supplied fixture transitions or expected outcomes
unspecified. Executable validator/replay acceptance is fixture-only; this directory
does not claim central phase completion.

## Independent acceptance (2026-10-12)

The fixture/model slice passes **50 Python tests** (43 initial plus seven review
regressions), covering the unchanged 34 events, six hand-authored snapshots,
13 queries, 15 replay cases and 21 checkpoint recovery pairs. Review fixed early
execute termination/BUSY release, collect success without its own publication,
and recovery accepting malformed earlier history behind matching last facts.
Additional negatives cover typed snapshot fields, publisher closure and transitive
error readiness. All seven JSON documents and five evidence files remain unchanged.

- `python3 test/test_provider_observation.py`: `Ran 50 tests`, `OK`.
- `dune build @test/provider-observation`: `Ran 50 tests`, `OK`.
- Full `dune runtest --force`: exit 0; **17 + 16 + 20 + 50 + 28 + 15 = 146**
  Python tests, all `OK`, plus OCaml inline tests and bundle integration.
  Output: `bundle determinism, provenance, staging, hashes, RTL and toolchain.lock passed`.
  Icarus `/usr/bin/iverilog` and `/usr/bin/vvp` were available: all four generated-RTL
  simulations ran, none skipped. The synthetic Git fixture used author and committer
  `Bundle test <bundle@test.invalid>`, isolated config/disabled hooks and no remotes;
  the fixture was isolated from the developer’s Git configuration.

This accepts the provisional logical fixtures only. Runtime locking, fsync/data
durability, live catalog/correlation/step/result publication and LibreLane lifecycle
instrumentation remain unproven. The next bounded task is the LibreLane observation
feasibility probe, not durable-store implementation or ASIC P6 completion.
