# hardcaml_asic architecture

This document owns system boundaries: who owns what, which way dependencies run,
and which behavior is implemented versus proposed. The [phase plan](phase_plan.md)
owns status and sequencing. The full pre-condensed text, with dated delivery notes
and feasibility findings, is in the
[2026-10-15 snapshot](archive/architecture-2026-10-15.md). The original rationale
is in the [September architecture](archive/architecture-2026-09.md). The
[memory contract](reference/program-memory-contract.md) is normative for
`Single_port_ram`.

## 1. Purpose and implemented scope

Help independent Hardcaml projects declare ASIC intent, emit reproducible inputs,
execute an implementation flow and inspect traceable results. The supported path
is Tiny Tapeout 6x4 / IHP CMOS5L / LibreLane, with behavioral and explicitly
selected flop memory, plus the exact 256×16 SRAM backend through fresh synthesis
and full implementation with the checked stock R0 physical profile. Macro
checkpoints and timed-model conformance remain open; see the
[physical SRAM boundary](development/sram-example-physical.md). There is no general
multi-target or multi-backend implementation. A successful process is not a
signoff verdict.

**Implemented:**

- Declaration and inspection, elaboration, target/constraint resolution, and
  immutable identity-addressed bundles.
- The installed native OCaml CLI (contract 4) with default result stores,
  standalone preflight receipts, and human or explicit-`--json` output.
- Coarse Classic runs with separate execution IDs, `run.json`/`results.json`,
  postcheck attempts, operation-aware acceptance and integrity-checked archives.
- Explicit six-stage execution with path-bound checkpoints, default per-attempt
  journals, foreground watch and separate finalization.
- Named, project-owned flow catalogs and typed parameter profiles with
  preview/plan-identity enforcement.

**Not implemented:** a store-wide catalog with idempotency, backend-step
observation, execution recovery, configuration-changing checkpoint reuse,
Workbench application integration, and source-artifact stamping (ASIC P6.5a).

## 2. Responsibilities and dependency direction

| Layer | Responsibility |
| --- | --- |
| Hardware library `hardcaml_asic` (`lib/`) | Resource semantics, project declarations, validated design intent and input emission. Qualified groups `Resource`, `Flow`, `Platform` and `Elaboration`; `lib/hardcaml_asic.ml` is the curated public surface (see the [namespace reorganization record](development/lib-namespace-reorganization.md)) |
| Flow domain `hardcaml_asic.flow_domain` (`flow_domain/`) | Stage/operation contracts, standard compositions, checked LibreLane mapping, catalogs, methodologies, profiles and parameters |
| Execution library `hardcaml_asic.execution` (`execution/`) and native commands (`bin/`) | Public CLI parsing, defaults, identity, routing and rendering; all computation services, supervision, publication, records, observation and watch |
| Installed Python runtime (`flow/hardcaml_asic_flow/`) | Thirteen modules (`__init__`, `backend_protocol`, `librelane_bridge`, `stage_metadata_probe`, `probe`, `runtime_observation`, `runtime_contract`, `managed_python`, `paths`, `macro_flow`, `physical_runtime`, `timing_intent_runtime`, `timing_physical_runtime`) plus four data files. Calls the official LibreLane API, probes runtimes, provisions, and implements checked physical-macro and linked pre/post-PNR timing adaptations |
| Retired Python sources | Sixteen former service modules kept in the checkout only as references and test oracles. Their installed imports are a deliberate Python API break |
| Hardware project (e.g. the example) | Design and wrapper semantics, testbenches, declaration, flow catalog, dependency pin and chosen evidence stores |
| Workbench ASIC adapter | Consumes the project's public provider interface and translates it into feature data |
| Workbench application | Job supervision, derived catalog/cache, workspace/window/tile state and presentation |

Dependencies run one way: hardware project → ASIC library/CLI, and Workbench
adapter → the project/provider entry point. ASIC never imports Workbench types
and needs no Workbench daemon. The hardware library does not depend on the
execution library. Ordinary Dune builds and project regressions need not route
through ASIC. Versioned structured data lets Workbench decode typed values
independently of the provider's implementation language.

ASIC owns canonical bundles, runs, snapshots, raw evidence, check attempts and
archives. Workbench owns a rebuildable index, bounded caches, saved selections and
layouts; its daemon owns its own process handles. Readers of one run are
independent: selection, tile lifetime and client detach never control a flow.

The active [Workbench architecture](../../silver/docs/hardcaml_workbench_architecture.md)
and [construction plan](../../silver/docs/construction_phase_plan.md)
own feature, window and tile behavior. These workspace links are for navigation,
not runtime path requirements.

### Current native execution source map

`execution/dune` defines one public `hardcaml_asic.execution` library with
`(include_subdirs unqualified)`. The directories organize source only; they are
not namespaces, and every `Hardcaml_asic_execution.*` module name is unchanged.

| Source directory under `execution/` | Modules / responsibility |
| --- | --- |
| Root | `Runner`, `Execution_plan`, `Coarse_execution_service`, `Coarse_run_service`, `Methodology_execution_service`, `Methodology_execution_record`: planning and orchestration |
| `storage/` | `Checkpoint`, `Checkpoint_publisher`, `Result_store`: validation, publication and allocation |
| `observation/` | `Observation`, `Observation_writer`: persisted reader and writer |
| `results/` | `Collect_service`, `Evidence_json`, `Report_service`, `Stage_report_service`, `Postcheck_service`, `Finalization_record`, `Finalization_service`, `Run_record`, `Stage_execution_record`, `Historical_methodology`, `Yosys_stat_evidence`: collection, evidence decoding, acceptance, reporting and records |
| `archive/` | `Archive_service`, `Archive_encoding`, `Evidence_tar`: archive selection and encoding |
| `runtime/` | `Identity_json`, `Runtime_contract`, `Runtime_evidence`, `Runtime_probe`, `Execution_transport`, `Process`, `Process_supervisor`, `Preflight_service`: identity, readiness, launch and supervision |
| `backends/librelane/` | `Backend_protocol`, `Backend_state`, `Classic_contract`, `Segment_report`, `Segment_service`, `Stage_preparation`: narrow native-backend integration |

The three C stubs sit at the library root beside the foreign-stub stanza. Stage
domains live in `flow_domain/stages/`. Installed resource paths and runtime output
layouts do not depend on this source layout.

### Tool environment ownership

The installed provider works without its source checkout. Storage has separate
owners:

- the provider installation prefix (library, CLI, runtime modules and pins);
- a configurable **collateral root**, selected by `provision --toolchain PATH` or
  `TOOLCHAIN`. It holds the managed `python`, `tt`, `pdk`, `.venv` and
  `.venv-precheck` children. The example defaults to absolute `_flow_tools/`;
  provider development adapters fall back to `.toolchain/`;
- host prerequisites and the Docker/Podman/Nix stores;
- the consumer's bundle, run and archive stores.

A thin consumer bootstrap delegates to the installed provider, with no copied
pins, no source fallback and no automatic provider refresh. Refreshing the
provider and provisioning tools are separate operations. Provisioning verifies
the installed archive pin's digest before extraction, then probes the result and
keeps final generations without replacing them. `provision --python BASE`
overrides the managed base; execution `--python` keeps its lexical path, so venv
symlinks are honored. `--check` is bounded and read-only.

Initial managed-runtime support is Linux glibc x86_64 ≥ 2.17 with `renameat2`.
That is not a universal platform guarantee or a P6.5 attestation. Native EDA
capability checks and generalizing the runner to Podman remain open. See
[tool-directory ownership](guides/bootstrap.md#tool-directory-ownership).

### Runtime policy and launch binding

Manifests record `requested_tools.runtime_policy`:

- Host flow Python is exact-minor 3.11, and native execution uses it.
- Dockerized implementation uses the official LibreLane 3.1.0.dev3 image, whose
  Python must satisfy `Requires-Python >=3.10`.
- An absent policy keeps the legacy strict 3.11 for both roles, and a malformed
  one fails closed.

Installed provisioning defaults never override what the bundle requests.

Docker transport binds the observed engine, interpreter flags and immutable local
image ID (recorded separately from RepoDigests), mounts the staged project
read-write and the PDK read-only, and owns bounded cleanup. Direct transport is
needed because LibreLane's `image_exists` rejects IDs and digests. The LibreLane
flow engine itself is unchanged. This binding is per attempt; it is not a
cross-machine digest pin or runtime attestation. See
[policy and limits](guides/flow.md#runtime-policy-and-launch-binding).

## 3. Target and flow selection

Keep harness, technology and flow separate. The harness specifies the interface,
geometry and submission constraints; the technology supplies cells and collateral;
the flow adapter validates compatibility and renders and invokes the tools.

Resolution and rendering are deliberately TT/LibreLane-specific. Keep that honest
scope: a general adapter framework should be extracted only when a second real
target or backend demands it. The [target reference](reference/target-reference.md)
lists the exact supported facts.

## 4. Project lifecycle

```text
Project declaration
  → fresh Elaboration.Context for one elaboration
  → construct/register resources and validate policy
  → finalize abstract Elaborated_project (circuit + metadata/intent/resources)
  → resolve concrete TT/LibreLane facts into Resolved_flow
  → Bundle.render / Bundle.write / Bundle.publish
  → separate execution(s), observations and results
```

Elaboration constructs a Hardcaml circuit, not a synthesis result.
`Chip_target.elaborate` returns `Elaborated_project.t`. `Chip_target.elaborate_for_flow`
adds validation and returns `Resolved_flow.t`, which keeps that project in its
`elaborated_project` field. Rendering emits inputs; execution invokes tools
separately.

The consumer owns each chip target's constructor, declaration, wrapper and source inventory.
`Chip_target.create ?source_inputs` defaults to `[]`. `Chip_target.describe` returns
declaration facts as an S-expression with no design construction, resolution or
I/O. It is not a Workbench protocol. Root and output paths are execution
arguments, not declaration fields.

`Chip_target.emit_bundle ?inputs ?include_simulation t ~source_root ~output_dir`
owns the shared orchestration:

1. Reject an empty inventory before constructing the design.
2. Elaborate and resolve for implementation.
3. Optionally elaborate for simulation. This is on by default; turning it off
   reuses the synthesis sources.
4. Render, then write.

Nothing is written until rendering succeeds. `Bundle.write` needs a new or empty
directory and is not atomic. `Chip_target.emit_bundle_to_store` uses
`Bundle.publish` instead: complete private `.objects` data is verified first,
then exposed through an exclusive identity symlink. Reuse validates the exact tree
and bytes.

Store defaults: the library requires an explicit store. The project CLI uses
`ROOT/_flow_results/{bundles,runs,operations}`, and the generic installed CLI uses
the caller's `_flow_results` for runs and operations. `_flow_tools` is separate.
Collect and report always select an explicit existing run, never the newest one.
See the [command/storage matrix](guides/flow.md#default-result-store-and-cli-contract-4)
and the [entry-point guide](guides/bundle-emission.md#project-centered-entry-point).

Invariants:

- Dependencies are acyclic. Project composes Bundle and Resolved_flow, which use
  Elaborated_project rather than calling back into Chip_target.
- Contexts close on success or failure, so registration cannot leak into another
  elaboration. Resource identities use explicit names and scope paths, and
  inventories are sorted.
- Simulation and implementation elaboration stay separate; bundle source sets
  must never mix behavioral models with implementation definitions.
- `Resolved_flow.t`, `Resolved_flow.Setting.t` and `Resolved_flow.Delay_ns.t` are
  private records that only `resolve` constructs. Field reads and pattern matching
  work; construction and record updates are compiler errors. There is no
  unchecked constructor or deserializer.

The private records enforce the resolution boundary only. They do not make
Hardcaml circuit internals deeply immutable, and external target files remain
references, not verified physical evidence.

## 5. Resources and policies

Resource preparation is a first-class **overall-workflow preparation operation**,
not a member of the LibreLane physical-stage sequence. A project-owned executable
constructs its chip target and invokes `Chip_target.prepare_resources`; the execution
service records its lifecycle and immutable successful snapshot. The generic CLI
inspects retained attempts without project code, current source files or tools.
See the [preparation contract](development/resource-preparation.md) and
[CLI guide](guides/resource-preparation.md).

Preparation covers source/configuration/actual-circuit/resource identities and
explicit selection explanations. Collateral and physical intent remain declared
references, not readiness or measured implementation evidence. A source observation
is not a compiled-source stamp. Preparation creates no bundle identity; comparison
with an explicit current preparation identity leaves old results unchanged.

Retain the behavioral/flop RAM contract and its independent conformance tests.
Selection is explicit: exact mapping, flops, or an explicitly permitted fallback.
Never silently change shape, latency or behavior to fit hardware.

User hardware owns architectural registers, memory latency and arbitration.
Implementation tooling may perform recorded, behavior-preserving combinational
buffering, sizing, placement and routing; it must not add pipeline stages or
retime across the SRAM contract to repair violations. Failed electrical closure
is reported rather than repaired by weakening limits or changing architecture.

The independent `hardcaml_asic_example/memory/` consumer is the ongoing SRAM
integration/acceptance design. Historical Verilog probes remain bounded diagnostic
fixtures, not a parallel production path. Resource IDs map to verified emitted
macro names; arbitrary source nets may still be optimized, merged or renamed.

`Resource.Request.contract` currently uses structural S-expression equality, so
field order affects matching. It merits a typed/canonical representation before
broader backend authoring ([backlog](backlog/README.md)). No SRAM or public macro
framework is a blanket prerequisite for other work.

## 6. Configuration ownership and constraints

The project declaration owns target, clock, resources and supported timing intent.
Optional checked `Hardware_variant` assignments pass through `Chip_target.of_variant`
to the factory and into existing bundle/plan identity. They are distinct from resource
policy and backend flow profiles. Identity establishes declared association, not
semantic parameter use or compiled-source attestation. The headless
`Variant_comparison` service reuses verified bundle/run/execution evidence, reports
domain differences and per-metric eligibility, and never launches experiments or
selects a winner. Workload/performance association remains unimplemented in this
first slice; see the [variant/comparison guide](guides/variant-comparison.md).
Derived source lists, target geometry, clock facts, SDC and resource collateral
are protected from conflicting overrides. LibreLane escape-hatch values carry
nonempty reasons. `Tt_cmos5l` supplies overridable template defaults with
provenance; the consumer still owns reset, enable, pin arbitration and testbench
behavior.

Timing supports one primary `clk` and optional minimum and maximum I/O delays.
PT.7 extends that same declaration with registered scalar Q anchors and reasoned
first-stage capture/enumerated asynchronous-reset exceptions. Exact native mapped
resolution fails closed before application; no hardware is inserted. The first
qualified CMOS5L subset and standalone synthesized-netlist audit CLI are documented
in the [timing-intent guide](guides/timing-intent.md). Requested intent,
producer-reported resolution and command-completed application stay separate.
PT.7c adds native stored-exception comparison, category-bound diagnostic enumeration
and endpoint inventory, with ordinary macro-free coarse-synthesis linked STAPrePNR
production and collect/report/archive source replay. The analysis is separate from
the physical-stage recipe and uses only its explicitly selected default corner.
Fresh macro-free named synthesis now binds required linked analysis to the exact
frozen methodology, attempt, occurrence and promoted checkpoint State, with early
expectation persistence and shared report/inspection validation. Checkpoints remain
unchanged and named evidence remains path-bound; named archives/physical endpoints
are unsupported. Explicit physical opt-in separately supports fresh macro-free coarse
full execution, checked combinational repair transforms, all-configured cell corners
and linked extracted post-PNR evidence. The five-stage example closes routed timing
with normal physical/postchecks under the recorded nominal-RC profile; exhaustive
effects/per-check and analog consumer qualification remain open. See the
[physical qualification](development/timing-physical-qualification.md),
[PT.7c contract](development/pt7c-native-flow.md) and
[named synthesis contract](development/pt7-named-synthesis.md).
Resolved target constraints include drive/load and timing margins. Expose those
facts and the emitted constraint identity to observers rather than creating a
second editable configuration in Workbench. Generated clocks and CTS (clock-tree
synthesis) analytics are later capabilities.

Named flow profiles (§9) may set only typed, explicitly exposed backend
parameters, and only before bundle publication.

## 7. Build artifacts and provenance

Keep immutable build identity separate from execution, operation and UI job IDs.
Bundle identity covers generated files, copied declared sources and their Git
state, target references, settings and requested tool pins. The hash uses an
ordered manifest representation, so reordering fields changes identity. Old
manifests and archived evidence must stay readable without rewriting.

ASIC P6.5a will add generator/source-artifact identity to new bundles, identifying
the library and tooling source rather than the hardware design. Define the
inventory and optional field before implementing it. P6.5b then adds
generator/executor/expectation comparisons and waiver records.

The example's `asic-dependency.json` selects a recorded ASIC revision. Explicit
development refresh captures local edits without changing that record.
`scripts/asic_dependency.py` installs library and CLI into an isolated generation,
validates resolution and linking, then publishes the selection atomically; re-source
activation to adopt it. Receipts and checksums are local bookkeeping, not an
embedded source stamp, and the copied dependency record is an expectation, not
proof of linked-library identity. See the
[workflow](development/consumer-dependency-workflow.md).

`Bundle.publish` gives atomic visibility, verified immutable reuse and no-clobber
concurrent publication on local POSIX filesystems. Published objects must be
retained. No GC, mutable current pointer, NFS or power-loss durability is promised
([publication details](guides/bundle-emission.md#identity-addressed-publication)).

## 8. Provider observation direction

### Execution model

The public native CLI runs all execution itself. `stages` and checkpoint and
observation queries go to native services, and there is no silent fallback to
Python stage execution. Coarse Classic `execute` uses `Coarse_execution_service`,
binding implementation, optional postcheck, collection, acceptance and optional
archive to a checked plan and the shared `Runner`. Coarse collection is
best-effort after failure and never replaces the primary failure.

`Flow.Stage` / `Flow.Recipe` are pure typed OCaml declarations. The native
execution models and the retained Python readers validate the same schema-3
contract. Stage modules describe all 80 Classic occurrences, and preparation
checks the selected runtime's inventory and settings.

| Boundary | Classic endpoint | Standalone restart targets |
| --- | --- | --- |
| synthesis | `Yosys.Synthesis` | floorplan, placement |
| floorplan | `OpenROAD.Floorplan` | placement |
| placement | `OpenROAD.DetailedPlacement` | cts |
| cts | `OpenROAD.CTS` | routing |
| routing | `OpenROAD.DetailedRouting` | signoff |
| signoff | `Misc.ReportManufacturability` | none |

Recipes are contiguous: either fresh from synthesis or resumed immediately after
an explicit checkpoint. They include every intervening boundary; going directly
from synthesis to placement is only a standalone segment capability. Official
Classic successors, checkers and configured gates are retained: CTS's later
STA/repair belongs to routing, and routing's checkers/fill/extraction belong to
signoff. Boundary names group steps. They do not license skipping checks and are
not acceptance verdicts. Arbitrary DAGs, custom steps and per-stage settings are
not exposed.

### Checkpoints

`stages` binds the exact tested Docker image and engine and rechecks them around
backend calls. A versioned request/reply protocol invokes `librelane_bridge`,
which uses the official `State.loads` / `Flow.start` without copying backend
orchestration. Per-call transport records keep argv, resources, process and
cleanup outcomes.

The checkpoint publisher copies all state views, configuration and the hashed
host/image-only closure. It validates them in the bound runtime, removes write
bits and atomically promotes a unique no-clobber publication before announcing
readiness. Schema-2 checkpoints declare exact restart targets and path-bound
compatibility: copied views remove dependencies on the producer run, not on the
original input, support or PDK roots. `checkpoint-inspect` is host-only
validation, not runtime readiness. Schema-1 physical checkpoints are not upgraded.

### Journals and readers

CLI `stages` journals each fresh or resumed attempt by default to
`<workspace-parent>/observations` (`--observation-store` overrides it and
`--no-observation` disables it). The journal provides:

- early allocation and hash-chained atomic records under advisory writer locks;
- `describe`, `inspect`, cursor `read-updates` and foreground incremental `watch`;
- schema-2 producer UTC timestamps and monotonic elapsed seconds, while schema-1
  journals stay readable.

Publication precedes stage finish. Physical completion, outer outcome and
acceptance are distinct facts. Readers can reconnect after the process exits.
Writer loss leaves unresolved facts and never invents recovery or completion.
Liveness evidence is best-effort, based on the filesystem and flock, and assumes a
trusted local store of immutable records. Classic `run`/`execute` do not journal.
See [journal review and limits](development/flow-journal.md).

Machine callers request `--json` (contract 4). Observed stages and finalization
emit flushed allocation and terminal JSONL; watch uses separate `updates`/`status`
envelopes. Reader exit 0 means a terminal outcome was recorded, not that the flow
was accepted.

### Finalization

`finalization` allocates a separate operation snapshot that selects one completed
fresh six-stage execution. It maps actual synthesis and signoff evidence and
validates the retained runtime closure without repeating implementation. It can
optionally run TT precheck, gate replay and the existing acceptance policy. Stage
streams stay terminal and checkpoints keep `acceptance: not_evaluated`.

Not eligible: stitched resumed attempts and standalone signoff checkpoints. Not
delivered: segmented artifact archive/GC, other images and cross-machine
relocation. The evidence for this whole section is listed in the
[phase plan](phase_plan.md#explicit-stage-refactor-closeout).

### Proposed store-wide protocol

Not implemented. A [provisional synthetic fixture](../test/fixtures/provider_observation/v1/README.md)
models the intended logical semantics: store-wide cursors, request idempotency,
BUSY ownership through outer outcome, generation fencing, transitive readiness and
retention. Those are logical decisions, not proven locking or durability.

The broader contract would:

- describe capabilities and versions, and list or inspect explicit stores;
- correlate a request with an early allocated operation or run;
- inspect step occurrences, replay updates, and read versioned snapshots and
  evidence references.

Events describe allocation, lifecycle, publication and outcomes, never UI
actions. Domain rules for any implementation:

- One build may have several runs. Display names and numbered directories are not
  identities. Collecting or postchecking creates another operation attempt, not a
  new run; rerunning implementation normally creates a new run.
- Preserve legacy `run.json.status`; add the requested-operation outcome
  separately. A step finishing and its results being published are separate
  facts. Earlier results stay available after a later failure. A result snapshot
  is not a resumable checkpoint.
- Choose one coordinated write path for `execute` and standalone operations.
  Settle ordering, writer locks, snapshot/cursor consistency, retry semantics,
  incomplete writes and crashed producers first. Serialize conflicting mutations
  rather than adding a scheduler. Disconnected readers never block execution.
  Storage failure must be visible, and stale observation does not prove that a
  tool stopped.
- Publish complete snapshots before advertising them. Measurements keep producer
  step/attempt, tool, stage, corner, units, constraints and raw source. LibreLane
  metrics accumulate, so an unchanged number cannot distinguish inherited evidence
  from a new equal measurement: keep origin explicit.
- Archive policy must cover advertised history and evidence. Failed-run
  diagnostic export needs its own contract.

### LibreLane integration constraints

From source inspection: `SequentialFlow.run` calls `step.start` directly and
`Step.start` is final, so overriding `Flow.start_step` is not a lifecycle hook.
Step substitutions and `Step.run` are extension points. `Step.run` returns metric
updates before they merge with inherited state, but it does not cover gated steps
or failures before or after computation. Per-step state files do not expose every
lifecycle fact. Fine-grained observation therefore needs supported hooks with
stated limits, or narrow upstream instrumentation, and must never copy the
engine's orchestration. Full-flow acceptance must test the Dockerized path and
real descendant cancellation.

The first local probe (2026-10-12) was blocked by missing Tcl/Tk. Managed
Python/Tcl and the selected image later passed all 12 official-API cases. The
history is in the [snapshot](archive/architecture-2026-10-15.md#librelane-feasibility-gate).

## 9. Named methodologies and profiles

Projects declare **which methodology and configuration to run** as data. They do
not script their own stage sequence.

| Owner | Responsibility |
| --- | --- |
| Consumer project | Declares its flows, profiles and default selection in ordinary OCaml modules (the example: `asic/flow_catalog.ml`, `asic/flow_profiles.ml`), which are declared inputs |
| `hardcaml_asic` | Typed `Catalog`, `Methodology`, `Profile` and `Parameter` APIs; resolution, validation, execution and authoritative evidence |
| Workbench (proposed) | Displays choices, remembers a project-scoped `{flow_id, profile_id}` reference, launches and presents. It never holds a second copy of the methodology |
| LibreLane | Keeps its backend engine, step implementations and physical checks |

The current backend recipe is `librelane.classic.v1`, run as fresh, contiguous
stage prefixes. The typed parameter surface is `SYNTH_STRATEGY` and
`SYNTH_ABC_BUFFERING`; other parameters need an explicit backend contract first.

**Preview** resolves the selection, renders the bundle in memory and returns
effective settings, provenance and a plan identity. It publishes nothing and runs
no tools. **Run** resolves again, rejects a stale `--expected-plan-identity`,
publishes the exact bundle and invokes the provider. The provider validates the
plan against the bundle itself rather than trusting a profile label. Execution
records freeze the flow and profile IDs, settings and identities, so later catalog
changes never reinterpret history. Changing the active selection affects only
future requests.

See [named flows](guides/named-flows.md) and the
[design record](development/project-flow-catalog.md).

## 10. Refactor guidance

Keep the P0–P3 model; strengthen boundaries and refactor internals in bounded
slices. For each refactor, state the invariant, keep the existing positive and
negative tests, and add the new acceptance case. Do not combine
naming/package reshuffles, schema changes and new behavior in one unreviewable
rewrite.

| Area | Direction |
| --- | --- |
| Project/context/`Elaborated_project`, resource semantics | Reuse with current tests; no rewrite prerequisite |
| Bundle/manifest | Preserve format and determinism; provenance additions coordinate with P6.5a |
| Collection/reporting | Fine step extraction is open; keep existing acceptance policy |
| Archives | Preserve snapshot/evidence closure at an explicit revision before advertising scratch deletion |
| Resource matching, multi-target abstraction | Revisit on concrete demand ([backlog](backlog/README.md)) |
| `lib/` layout | Planned [namespace reorganization](development/lib-namespace-reorganization.md), independent of behavior |

## 11. Development specimen

The sibling [`hardcaml_asic_example`](../../hardcaml_asic_example/README.md) is
the reference consumer. It implements a fixed single-clock 16 × 8 controlled FIFO
with explicit register storage, an independent list-based scoreboard,
directed/seeded regressions, a TT wrapper/declaration, a project CLI
(`describe | emit | preflight | run | flow plan | flow run`), a `standard` flow
catalog and a standalone headless client.

Contract: synchronous clear takes priority over disable; pop responses are
registered; it reports occupancy, full and empty; acceptance decisions are made
pre-edge. When empty, a simultaneous push and pop accepts only the push (no
bypass). When full, both are accepted and the old head is returned. The TT
wrapper owns pin mapping and output/OE masking separately from synchronous reset.

The example owns its Dune files, design, scoreboard, regressions and ordinary
commands. ASIC owns flow and report semantics; Workbench owns adaptation and
presentation. The example's recorded ASIC pin predates the current `Project` API,
so it uses explicit development refresh until the owner adopts a committed
candidate ([phase plan](phase_plan.md#active-queue)).
