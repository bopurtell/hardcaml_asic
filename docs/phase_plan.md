# hardcaml_asic phase plan

**Current queue, adopted 2026-10-24; library/example scope clarified 2026-11-05:
make the ASIC flow independently usable for SRAM-backed implementation and resource
studies. Protemu adoption remains a separate consumer track.**

This is the only active implementation queue. [Architecture](architecture.md)
owns boundaries; the [memory contract](reference/program-memory-contract.md)
owns RAM semantics. Detailed designs and evidence belong in `development/`,
`sram/`, and `evidence/`, linked from the relevant item here.

For replacement-session orientation across ASIC, Workbench and the example, see
the [cross-repository checkpoint](../../hardcaml_workbench/docs/session_checkpoint.md).
This file remains the only active ASIC queue; the checkpoint distinguishes bounded
PT.0–PT.8 completion, separate PT.3 adoption and paused deployment acceptance.

**Revised direction, 2026-10-24:** ongoing SRAM acceptance belongs in
`hardcaml_asic_example` through public APIs. The old Verilog probe remains a
diagnostic/history artifact. Do not add architectural registers or change latency
implicitly; permit recorded behavior-preserving physical optimization. The
[example integration plan](development/sram-example-integration.md) records the
agent assignments and blog-backed KLayout/CDL checking approach.

The previous plan is preserved in the [2026-10-24 archive](archive/phase-plan-2026-10-24.md),
including completed milestones, open P6 gates, and the former next steps.
Archiving an open item does not complete it. New work uses **ASIC PT.0–PT.8**
to avoid renumbering historical ASIC P/S tasks or Protemu's own phases.

## Current baseline

**Normal consumer update, 2026-11-12:** [Protemu installed adoption](../../hardcaml_protemu/docs/adoption-release.md)
is accepted in bounded tooling scope: public flows/preparation, Resources UI,
seven simulation cases, controlled core+RAM workload comparison, cancellation and
large-report negative timing reinspection. The Magic counterfactual/profile recheck
passes in a separate record; the original failed run remains unchanged. One
behavior-preserving timing-repair candidate improves slow setup to −3.0998 ns but
does not close it. Full Protemu physical/constraint/selection obligations remain open.

**Tooling release update, 2026-11-12:** [stock SRAM segmented support](guides/segmented-sram.md)
is complete in the bounded single-stock256×16/R0 through-Timing scope, including
path-bound schema-3 checkpoint restarts and fresh named execution. Installed real
example qualification and Workbench production TUI launch/reopen pass. Macro
Signoff/finalization/PT.7 linked exceptions remain unsupported. This qualification
is separate from the open Protemu physical/timing findings in PT.3c.

- Project/resource declaration, behavioral and flop RAM implementations, immutable
  bundles, execution/reporting/acceptance, explicit stages, and named flow profiles
  are implemented. Their evidence remains in the archives.
- Protemu's `asic/cli/asic_bundle.ml` now uses `Project.emit_bundle`; its `program`
  resource remains 256×16, 1RW, latency one with explicit instance Flops/Exact
  variants. PT.3a emission/resource adoption and PT.3b vendor-functional system
  regression pass; implemented-netlist/physical acceptance remains pending.
- CMOS5L now has an exact 256×16 SRAM mapping, role-complete collateral and fresh
  synthesis/full support. The independent example passes physical/electrical
  acceptance, TT precheck and final-gate functional replay while retaining one SRAM
  and its original 16 staging flops. Stock single-macro path-bound checkpoints
  through Timing are now qualified; timed-model conformance remains open.
- SRAM S.1 inventory, S.2a functional-model tests, and qualified S.3 physical
  feasibility have evidence. The [October 23 checkpoint](sram/sram-status.md#2026-10-23-update)
  resolves eligibility and records the support-tools marker fix. Production
  physical support is demonstrated for the exact example profile; timed-model
  conformance remains open. Earlier authority-gate wording
  in historical records is superseded by that update.
- Workbench already has flow integration, synthesis cell tables, timing and
  physical inspection. The new work is logical-resource mapping and its connection
  to implementation evidence; see its [current overview](../../hardcaml_workbench/README.md).

Current implementation evidence is in the [physical closeout](development/sram-example-physical.md#implementation--execution-record)
and [earlier synthesis record](development/sram-example-integration.md#evidence).

## Ownership

| Owner | Responsibility |
| --- | --- |
| `hardcaml_asic` | Resource contracts and selection, macro wrappers/collateral, physical and timing intent validation, backend rendering, mapping evidence, public inspection interfaces |
| `hardcaml_protemu` | Hardware parameters, program packing, loading/fetch validity, synchronization architecture, project policy/placement/constraints, workloads and whole-system verification |
| `hardcaml_workbench` | Discovery and presentation of ASIC-owned data; resource browsing, comparison and user controls through public provider interfaces |

ASIC must remain usable headlessly and must not depend on Workbench. Cross-repo
items below are coordination deliverables; their implementation lives with the
named owner and does not reuse that repository's phase IDs.

## Active queue

### Status terminology

Use **complete** when the stated scope's required implementation and acceptance
gates have passed. Always retain the scope qualification: complete for a supported
profile or library/example scope does not establish arbitrary-design or Protemu
acceptance. Unsupported capabilities and separately deferred optional work are not
unfinished gates of that completed scope.

Use **implemented; acceptance pending** when implementation exists but required
acceptance remains open; **partial**, **blocked**, **pending**, or **deferred** for
unfinished work, with the remaining gate or reason stated explicitly. Do not use
“delivered” as a status: it does not distinguish implementation from acceptance.
Older records using that word must be interpreted from their checklists and evidence,
not automatically treated as complete. Apply these terms in downstream summaries.
PT.7/PT.8 library/example completion and their open Protemu consumer obligations
must be reported separately.

| Order | Item | Dependencies | State |
| --- | --- | --- | --- |
| 1 | PT.0 — Repair and refresh SRAM feasibility evidence | Existing S.3 records | Complete in revised example-centered scope; historical probe qualifications retained |
| 2 | PT.1 — Exact production SRAM backend | PT.0 corrected power/corner findings | Required scope complete; PT.1d optional/deferred |
| 3 | PT.2 — Macro collateral and physical controls | PT.1 mapping/wrapper contract | Complete for stock 256×16/R0 fresh full profile; example acceptance passed |
| 4 | PT.3 — Macro-backed Protemu acceptance | Required PT.1 scope and PT.2; consumer API migration | PT.3a/b and normal tooling adoption complete; routed functionality passes, setup/full physical acceptance open |
| 5 | PT.4 — Resource-mapping inspection API | Existing records; PT.1–PT.2 for macro fields | Complete: CLI/library preparation and independent-example acceptance |
| 6 | PT.5 — Requested-versus-realized resource checks | PT.2, PT.4 | Complete in supported retained-evidence scope: reconciliation, physical measurements and sampled resource timing |
| 7 | PT.6 — Workbench resource inspector | PT.4; PT.5 for realized evidence | Complete in bounded inspector scope: exact sources, qualified comparison and declared controls |
| 8 | PT.7 — Synchronizer/reset-aware timing declarations | Endpoint reference design; independent example acceptance | Complete in agreed bounded library/example synthesis scope; physical qualification tracked separately |
| 9 | PT.8 — Reproducible design-variant comparisons | PT.4–PT.7; independent-example workload acceptance | Library/example scope complete; Protemu fixed loader association and core+RAM functional comparison complete; package physical/performance selection open |

This is the preferred execution order, not a requirement to serialize design
work. PT.4 can begin against existing flop records; PT.7 can begin independently.
PT.3 can establish functional integration before PT.7, but final Protemu physical
selection requires reviewed timing constraints. Workbench is optional for all
headless acceptance. Global job ceiling is20 including nested builds and tools;
every Dune invocation has an explicit limit. Current slices use at most six jobs
per agent and the shared checkout locks documented in their implementation plan.

## PT.0 — Repair and refresh SRAM feasibility evidence

**Owner: ASIC. First bounded task: correct the S.3 power bridges and corner map.**

Implementation started 2026-10-24; see the [agent-sized implementation plan](development/pt0-implementation.md)
for assignments, the `hardcaml_asic_example` testbed, resource limits and evidence.

- [x] **PT.0a — Power access.** Correct the two misplaced VPWR bridge rectangles;
  re-extract and verify adequate contact to both positive supply pins and ground.
  Completed 2026-10-24: emitted GDS/DEF show 1.95 µm positive-pin contact and
  0.28 µm adjacent-ground clearance; zero extraction overlap and correct extracted
  supplies. The first proposed shift exposed a real spacing defect, corrected by
  narrowing both bridges. [Evidence](../evidence/sram-pt0/README.md).
- [x] **PT.0b — Corner binding.** Replace ambiguous corner globs with validated
  mappings. Record the actual macro Liberty loaded in every STA corner, including
  the standard-cell/macro fast-corner temperature difference; rerun affected STA.
  Completed 2026-10-24: exact single-match libraries confirmed in corner-only and
  fresh physical STA; setup/hold pass in every corner. Fast −40/−55 °C model
  difference remains explicit. [Evidence](../evidence/sram-pt0/README.md#corners-and-timing).
- [x] **PT.0c — Residuals and retained evidence.** Reassess macro-boundary slew/cap
  violations with correct corners and buffer/resize as necessary. Recheck the
  LEF-obstruction DRC finding against real GDS. Preserve routed DEF, P&R netlist,
  extracted SPICE, reports, exact inputs and restoration instructions in a new
  evidence set; retain the original archive and digest unchanged.
  Partial 2026-10-24: a verified/restored 8.38 MB evidence archive now retains the
  required physical views and attribution runs; all nine current TT prechecks
  pass. Fresh flow exits 2 on three abstract Magic M3.f markers. Slew violations
  remain (fast/slow/typical: 12/25/21), with 12 capacitance violations per corner.
  Retained-GDS Magic reports 29,294 macro-internal findings requiring a deck/model
  disposition in that historical probe. **Closed 2026-10-25 in the revised public
  example scope:** all-corner slew/cap pass without changed limits or registers;
  real-GDS KLayout is clean; three abstract Magic markers have exact layer-control
  disposition; power/LVS and external TT/gate checks pass. The original probe is
  not relabeled as clean. [Closeout and retained evidence](development/sram-example-physical.md).

The [example's registered-memory specimen](../../hardcaml_asic_example/memory/README.md)
now exercises public Exact selection, actual vendor-functional RTL and the
technology-mapped and routed netlists. Unsupported shapes still fail explicitly.
Physical acceptance now applies to its exact stock R0 profile, not arbitrary memories.

**Exit:** the known probe defects have corrected, reproducible evidence, with
remaining findings explicitly dispositioned. This supplements S.3's qualified
closure; it does not relabel that historical probe as production acceptance.
Use the [S.3 disposition](sram/sram-s3-disposition.md) for technical detail and
the October 23 checkpoint for current authority/pin context.

## PT.1 — Exact production SRAM backend

**Owner: ASIC. Implements the production mapping portion of historical S.4.**

- [x] **PT.1a — Mapping and wrapper.** Map the existing 256×16 latency-one 1RW
  request exactly to `RM_IHPSG13_1P_256x16_c2_bm_bist`. Implement enables,
  whole-word mask and BIST/test tie-offs, with stable wrapper/instance names.
- [x] **PT.1b — Contract conformance.** Run the shared contract suite against the
  behavioral, flop and actual macro functional models. Compare only defined
  results; check each backend's own hold behavior after writes and disabled cycles.
  Preserve no reset, no initialization, and simulation-only poison behavior.
- [x] **PT.1c — Selection diagnostics.** Exercise `Exact`, explicit `Flops`, and
  explicitly permitted fallback. Unsupported contracts fail under `Exact`;
  fallback records its reason. No silent dimension rounding or macro composition.
  PT.1a–c completed 2026-10-24: canonical request, injective resource names,
  direct macro instantiation without new registers, three-backend contract replay
  (2,307 edges each), unsupported-shape/fallback diagnostics, independent example
  RTL and mapped-netlist replay. [Record](development/sram-example-integration.md#evidence).
- [ ] **PT.1d — Timed-model conformance (optional, deferred).** Establish a simulator path that executes
  the macro's delayed timing checks, with positive and intentional-violation
  cases. Keep this evidence separate from functional tests and Liberty STA.
  Owner decision, 2026-10-25: this is not a completion gate for the supported
  backend, PT.2 or Protemu adoption. No competition/vendor requirement for it has
  been established. Reactivate for a specific timing-sensitive verification gap,
  an explicit external requirement, or a deliberate decision to qualify/rely on
  the vendor timed model. Questa is the intended candidate when available;
  installation and whole-chip SDF simulation are not current prerequisites.

**Exit:** supported exact requests elaborate to the production wrapper and pass
functional backend conformance and selection diagnostics (PT.1a–c). **Required
scope is complete.** PT.1d remains unperformed optional work, not a claimed pass.
Correct Liberty/corner/constraint handling, STA/electrical checks and physical
acceptance remain required for implementation; making timed simulation optional
does not weaken those checks.

## PT.2 — Macro collateral and physical controls

**Owner: ASIC; project settings supplied by Protemu.**

- [x] **PT.2a — Collateral lifecycle.** Register, resolve, validate and identify
  synthesis black boxes, functional/timed models, Liberty corners, LEF, GDS and
  CDL/SPICE. Keep simulation and synthesis source roles distinct. Resolve PDK
  files at the appropriate execution/preflight boundary, not ordinary elaboration.
  Completed 2026-10-24: generated synthesis stub, separate behavioral/functional/
  timed roles, exact corners and PDK-file identity checked at preflight and after
  execution. Runtime resource/instance consistency and mutation negatives pass.
- [x] **PT.2b — LibreLane integration.** Derive `MACROS`, corner bindings, layout
  assembly and macro-aware LVS inputs from registered resources. Connect
  `VDD!`/`VDDARRAY!` to `VPWR` and ground to `VGND` through the supported target
  integration. Record the accepted target assumptions and actual connectivity.
  Completed 2026-10-25: placement-aware edge power access verified in final GDS;
  exact corners, real-GDS KLayout and actual schematic CDL consumption verified.
  LVS scope is abstract macro-boundary/interconnect, not internal-device LVS.
- [x] **PT.2c — Placement intent.** Add resource-ID-keyed position, orientation,
  halo/clearance and supported power-grid controls. Validate target bounds,
  overlap, legal orientations and unknown resource references. Support multiple
  explicitly declared instances with distinct identities; start with manual placement.
  Completed for R0 with a common halo and the checked Metal4 edge-access strategy.
  Dynamic pin/grid/OBS validation rejects unsupported geometry. Multi-resource
  generation/negative fixtures pass; actual physical acceptance is one example macro.
- [x] **PT.2d — Physical acceptance.** Run a normal emitted macro bundle through
  synthesis, physical implementation, required checks and TT precheck using the
  current pinned support tools. Verify actual corner binding, power access,
  macro-boundary electrical limits and CDL-backed LVS. Preserve complete evidence.
  Completed 2026-10-25: run `be961b2c95f74c81ad6af81ca4d2b269`, external postcheck
  `0553f6d19c8f42d28c057fc65ed23510`, consumer audit and restored replay pass.
  Worst setup/hold: **11.246561 / 0.223533 ns**. Raw Magic=3, narrowly dispositioned;
  routing/KLayout/overlap/electrical/LVS/antenna checks pass. [Physical record](development/sram-example-physical.md).

**Exit:** the normal library/CLI path implements the exact macro reproducibly
without hand-editing emitted bundles or borrowing experimental configuration.
Missing collateral, ambiguous corners and invalid placement produce actionable errors.

## PT.3 — Macro-backed Protemu acceptance

**Owners: Protemu for adoption/system checks; ASIC for backend defects and evidence.**

**Remaining design step: PT.3c physical/timing acceptance.** PT.3a/b, actual routed
functional replay and [normal installed tooling](../../hardcaml_protemu/docs/adoption-release.md)
are verified. One behavior-preserving post-GRT repair candidate remains below the
48 MHz setup target. Further design/physical tuning is separate from the accepted
Workbench tooling scope; no pipeline or cycle-contract revision is implicit.

First actual-loader physical attempt and independent final-netlist replay are
recorded in [PT.3c evidence](../../hardcaml_protemu/docs/pt3c-physical-attempt.md).
Run `0d5ed7ff0dfb4e44bcb6459bbb251fca` remains failed: slow setup −4.0586 ns/503
violations and nine original Magic findings. Final-netlist functional replay passes
all18 scenarios separately. Later exact same-DEF controls prove the narrow OBS
disposition in a separate passing profile recheck. Candidate `b591d335b32a49b8a019739ef7044e4e`
completes segmented Timing with −3.0998 ns/444 violations. Installed reinspection
reports that failure without changing historical unknown/failed records. The
original run is not relabelled, and no constraints were weakened.

- [x] **PT.3a — Consumer adoption.** Migrate Protemu's emitter to the current public
  API and deliberately adopt a compatible dependency/toolchain revision. Preserve
  an explicit flop configuration and add an exact-SRAM configuration for `program`.
  Verified against recorded ASIC `b0b3384acbe22c8a30264cbdf0c19008c58bf3c5` in a
  private Protemu prefix: complete repeated bundle bytes, stable contract/resource
  identity, explicit policy, source-role separation, exact RTL linkage and existing
   regressions pass. The original PT.3a scope was emission-only. Later loader Exact
   Hardening and normal named-flow adoption are documented separately above.
- [x] **PT.3b — Real system regression.** Exercise the loader/core/program-store
  path: load, readback, fetch, stalls, reset/disable, image validity, rejected host
  accesses during execution, and independence from unspecified memory outputs.
  Verify both emitted source roles with the applicable models.
  Completed 2026-11-12: nine positive functional replays and nine diagnostic-specific
  oracle self-checks pass, including actual vendor SRAM execution, behavioral/Flops
  controls and real loader/core replay under zero/ones/X unspecified-output policies.
  Pending READ/FETCH cancellation and partial-load disable are witnessed. Independent
  review and legacy Verilator regressions pass; no physical/timed claim is implied.
- [ ] **PT.3c — Implemented-design replay.** Run the actual macro-backed system
  through the adopted flow and replay its final netlist with the macro model.
   Record timing/check outcomes and any provisional constraint assumptions;
   an RTL-only fixture is not final-netlist evidence.
   - [x] Actual routed loader and exact final-netlist functional replay retained.
   - [x] Same-DEF Magic diagnostic controls and separate profile recheck retained.
   - [x] Real Workbench timing-repair candidate and typed negative timing inspection.
   - [ ] Protemu setup closure/full physical acceptance and final selection.

**Exit:** the actual Protemu system works with the selected SRAM and has linked
consumer/backend/run evidence. Final physical selection additionally requires
PT.7's reviewed constraints and PT.8's configuration decision. Historical small
observable-top and flop-memory measurements do not substitute for this gate.

## PT.4 — Resource-mapping inspection API

**Owner: ASIC. Extend existing records rather than introduce another selector.**

Completed 2026-10-25: [preparation operation contract, agent slices and evidence](development/resource-preparation.md).
This is a first-class overall-workflow preparation operation, not another LibreLane
physical stage. CLI/library and independent-example testing come first; Workbench
integration is outside this implementation slice. PT.3 adoption is not a prerequisite.

- [x] **PT.4a — Pre-run inventory.** Expose a versioned, machine-readable
  CLI/provider view available without EDA: resource ID, requested contract,
  matched policy, selected implementation, fallback/rejection explanation,
  and actual elaboration role (behavioral versus selected implementation).
- [x] **PT.4b — Physical linkage.** Include generated wrapper/macro instance
  identities, required collateral and corners, placement and supply intent.
  Distinguish unresolved references from validated files and measured facts.
- [x] **PT.4c — Stable provenance.** Bind inspection to exact source/configuration/
  circuit/preparation identities; support deterministic output and historical inspection. Verify
  flop, macro, fallback and unsupported-selection cases headlessly.
  Preparation occurs before bundle emission: no bundle identity is fabricated.
  Linking a separately emitted bundle requires explicit future verification.

Completed scope includes `Project.prepare_resources`, structured pure resource snapshots, durable
attempt lifecycle, `preparation1` capability and the installed `preparation-inspect`
reader. Example `prepare`/`preview`/`resources` cover memory and resource-free FIFO.
All **89 installed-consumer checks** and both repositories' full test suites pass
with their documented historical/opt-in fixture limits. Original v1 attempts remain
readable; current workflow descriptor v2 reports actual phases. No Workbench changes.
See the [operating guide](guides/resource-preparation.md).

**Exit:** a client can answer “what will `program` become, and why?” without
parsing RTL or running synthesis. Simulation inventory cannot be mistaken for
the implementation selected for fabrication.

## PT.5 — Requested-versus-realized resource checks

**Owner: ASIC; Workbench consumes the results.**

Implementation authorized 2026-10-26 after separate flow work finished:
[bounded assignments, shared evidence contract and acceptance plan](development/resource-realization-plan.md).
Preparation selects resources, bundle emission binds expectations, and synthesis/
physical result checks establish realization. Separate PT.4-attempt linkage is
optional and unavailable unless verified. Reconciliation, physical measurements and
sampled resource timing are now complete in the supported evidence scope below.
Library/CLI and the independent example are the testbed;
Protemu adoption and Workbench integration are independent.

- [x] **PT.5a — Reconciliation.** Check expected macro type/count/identity against
  synthesis and final physical instances. Report missing, unexpected, unresolved
  and optimized-away instances distinctly, retaining stage and producer identity.
  Completed 2026-10-27 for supported flat mapped/final-netlist evidence; unsupported
  hierarchy is unresolved, and absence never implies optimization. New bundles bind
  a structured snapshot to their exact resolution; historical inventories remain
  explicitly legacy. Separate PT.4 attempt linkage is optional/unavailable.
- [x] **PT.5b — Resource evidence.** Report macro versus standard-cell area,
  utilization and links to macro-boundary timing paths. Attribute flop-resource
  costs only where the mapping survives synthesis; otherwise report unavailable.
  **Completed in supported scope, 2026-10-27:** schema 2 provides checker/DEF-bound
  macro footprint, optional exact LEF-class component sums, explicit gross-core/die
  ratios, and resource-linked sampled timing paths/slew/output loads. Existing
  synthesis estimates and native rounded physical metrics remain distinct. Final
  installed acceptance: 15 tests, 186 sampled paths / 372 electrical observations,
  complete 76-reference archive closure and historical schema-1 replay. No EDA rerun.
  [Contract and closeout](development/resource-measurements-plan.md#implementation-and-acceptance-closeout).
  Coverage remains the validated stock/R0 physical checker and retained native
  post-PNR report format; unsupported inputs are unavailable/diagnosed. Flop ownership,
  exhaustive timing, per-pin compliance margins, usable-site utilization, IR/EM and
  resource power remain unestablished—not implied by gross-area ratios or sampled links.
- [x] **PT.5c — Acceptance integration.** Make required-macro mismatches fail the
  relevant implementation checks, with negative fixtures and actual-run evidence.
  Completed 2026-10-27: public library/CLI, collection/report and archive validation;
  corrected installed example passed 20 focused tests / 76 API-CLI calls, preserving
  276 historical files. Both full suites passed with documented fixture limits.
  Independent review defects were corrected and reverified. See the
  [closeout](development/resource-realization-plan.md#implementation-and-review-closeout-2026-10-27)
  and [operating guide](guides/resource-realization.md).

**Exit:** selected mapping and verified implementation are separate inspectable
facts. A declaration alone cannot establish that a run retained its SRAM.

## PT.6 — Workbench resource inspector

**Owner: Workbench; ASIC owns and tests the public data contract.**

Implementation authorized 2026-10-28. [Cross-repository integration contract](../../hardcaml_workbench/docs/resource_inspector_plan.md)
defines typed bundle/coarse/execution/finalization targets, bounded index/detail/source
queries, explicit historical import and Resources workspace acceptance. Existing
PT.5 raw inspection remains compatible; UI does not duplicate selection/reconciliation.

- [x] **PT.6a — Resource table.** Display resource identity, shape/contract, policy,
  implementation and selection reason, distinguishing explicit flops from fallback.
- [x] **PT.6b — Drilldown.** Link a logical resource to its hierarchy, collateral,
  placement and realized-mapping/timing evidence; show unavailable or pending data.
- [x] **PT.6c — Controls and comparison.** Compare two configurations/runs and
  expose project-supported selection controls through public preview/new-build
  interfaces. Do not mutate immutable bundles or duplicate selection logic in UI.

Completed in bounded inspector scope, 2026-10-28: provider-owned `resource-inspect` bounded bridge and Resources
workspace with explicit bundle/coarse/execution/finalization sources, paged details
and authorized artifacts. Exact A/B captures compare selection differences and
compatible Observed/Derived scalars; estimates/unknowns have no measured delta.
Controls reuse project-advertised flow/profile/assignments; unsupported memory-policy
knobs and automatic variant sweeps are not introduced. Actual augmented SRAM UI
acceptance verifies `program`, stock macro, matched synthesis/final observations,
physical area and sampled paths; empty FIFO and unavailable historical inventory
are distinct. [Operating guide](../../hardcaml_workbench/docs/resource_inspector.md),
[acceptance](../../hardcaml_workbench/docs/resource_inspector_acceptance.md).
Both full suites and independent correction review pass. Named-checkpoint realization,
unlinked preparation history and PT.5b physical/timing limits remain explicit; no EDA
or new named macro execution is claimed. Application provider upgrade was separately
approved; user visual acceptance after restart remains distinct from native PTY proof.

**Exit:** selecting `program` shows its contract, selected macro and whether the
chosen run actually retained it. The same facts remain available headlessly.

## PT.7 — Synchronizer/reset-aware timing declarations

Started 2026-10-28: [bounded implementation plan and shared contract](development/pt7-timing-intent.md).
Generic library/example acceptance is independent of Protemu adoption. Consumer-specific
protocol/reset/I/O review remains pending and is not authorized in this slice.

**Closeout decision:** PT.7 is complete in its documented library/example synthesis
scope. The original checklist requires implemented declarations, post-synthesis
endpoint resolution and reviewable evidence; it does not require routed timing
closure or exhaustive classification of every timing-check context. Existing
coverage limitations and negative hold observations remain unchanged.

Separate follow-up issue drafts (GitHub publication blocked by missing CLI authentication):

- [All-corner pre-PNR timing qualification](development/ticket-all-corner-timing.md).
- [Post-PNR preservation and timing closure](development/ticket-post-pnr-timing.md),
  using the first ticket's reviewed corner/model policy.

These are physical-qualification follow-ups, not additional PT.7 completion gates.
**Physical follow-up accepted, 2026-10-29:**
[bounded all-corner/physical qualification plan](development/timing-physical-qualification.md).
One ordinary coarse full run of the unchanged five-register example closed setup,
hold, slew and capacitance at fast/typical/slow cell-PVT corners using the nominal
extracted RC view. Ordinary post-CTS repair added nine combinational delay cells,
no registers or latency. The 20 ns period, 0.25 ns uncertainty and I/O environment
remain unchanged. Named physical support remains deferred.

Run `23a6954b852d4205b542d3815634d772`, build
`ac513e0e7e9cfa935f73d697caebca8f601aeeb4ea62c2a2ccf61c10a10789cd`:

| Cell corner, nominal RC | Worst setup ns | Worst hold ns | Setup/hold/slew/cap violations |
| --- | ---: | ---: | --- |
| Fast | +16.058595 | +0.102353 | All zero |
| Typical | +15.729074 | +0.276686 | All zero |
| Slow | +15.113664 | +0.590927 | All zero |

Routing/Magic DRC, antenna and LVS pass; all nine TT checks and 512 final-netlist
functional observations pass. Exact extracted inputs, clock propagation, qualified
five-stage paths and separate native coverage/closure outcomes are retained.
Corrected public archives restore all 423 raw artifacts with exact public response
parity and original PDK/source hidden. [Actual evidence](../../hardcaml_asic_example/docs/timing_physical_full_result.md),
[independent final review](development/timing-physical-final-review.md).
This is this example/profile's physical timing acceptance, not arbitrary designs,
all RC extremes, analog reset/CDC adequacy or Protemu qualification. Original
synthesis hold failures remain unchanged historical evidence. Physical execution
provider `4856e6af993642988c18c07f47a8c689` is distinct from archive correction
provider `1ba205c2a11c4904b03d98b0e7a3f616`; no second P&R run was needed.
Exhaustive per-check/effect instrumentation remains a documented limitation, to be
promoted only for a concrete requirement. Consumer-specific review belongs to PT.3
and the consumer's final physical-selection acceptance.

**Owners: ASIC for declarations, validation/rendering and evidence; example for
generic acceptance; Protemu for its separate circuit intent and consumer review.**

- [x] **PT.7a — Endpoint references (bounded subset).** Exact scalar/bit inputs and
  registered scalar Q anchors resolve after synthesis to supported CMOS5L pins.
  Empty/ambiguous/merged/unsupported matches and changed clock/data/reset connectivity
  reject before applying exceptions. Native bit/scalar acceptance completed 2026-10-28.
- [x] **PT.7b — Narrow timing intent (bounded subset).** Reasoned external capture
  to one first-stage D and enumerated externally active-low/reset-to-zero controls;
  requested Yosys `keep=1` preserves five original stages in the tested mapping.
  All three sampled downstream setup/hold links remain timed. No automatic state
  insertion, general false paths, blanket chain exceptions or physical-preservation claim.
- [x] **PT.7c — Reviewable evidence (bounded scope).** Completed identity-covered requested intent,
  producer-reported resolution, command-completed application, hash-bound raw reports
  and strict read-only library/CLI inspection for standalone synthesized-netlist audits.
  **Accepted 2026-10-28:** [native-state/coarse-flow slice](development/pt7c-native-flow.md).
  New identity-covered producers collect native `write_sdc` state, seven category-bound
  diagnostic groups and native endpoint inventory. The reader verifies exact stored
  relations, native design and full returned diagnostics without executing Tcl.
  Ordinary macro-free coarse synthesis runs a separate linked STAPrePNR analysis;
  collect/report/inspection/archive replay bind exact run/build/synthesis/runtime/
  libraries/corner/producer sources, without rerender or current PDK lookup.
  **Outside this completed scope:** exhaustive exception effects/per-check analysis
  and separately tracked post-PNR/other-corner acceptance. Current evidence proves stored
  scope and bounded capture-witness suppression, not all affected paths or reset
  effect/adequacy. Overall inspection remains **exit 1 / unavailable**; ordinary
  synthesis report exit 0 is not a timing-closure verdict.
  **Named synthesis accepted:** [bounded integration and closeout](development/pt7-named-synthesis.md).
  Fresh area/delay profiles bind exact frozen methodology, attempt, occurrence and
  promoted checkpoint State to required linked STA; early expectation/failure evidence,
  checkpoint preservation and public report/inspection parity are independently checked.
  Named evidence remains path-bound; named archives and physical endpoints are unsupported.
- [ ] **Consumer review (separate pending track).** Review Protemu's protocol inputs,
  sampled host link, reset and board/pad I/O assumptions, then rerun affected physical
  checks. No Protemu inspection, modification or timing-correctness claim in this slice.

Accepted example provider `74e9a52b43a14c69bff2763325b39522`, source snapshot
`a6723afb6a88bce6152d7b05662ea614a6e1a75352b870b4e20391e7c2d3f684`.
Fresh audits `sta-bit-r3` and `sta-scalar-r3` use Yosys 0.66 / OpenSTA 2.7.0,
typical CMOS5L corner and unmodified public-bundle SDC. Sampled setup is
**+19.432287 ns**, hold **−0.044244 ns (violated)**; this proves sampled timing
preservation, not closure. No P&R was run. Native lookup/boundary-net and evidence
validation defects found after initial suites passed were fixed and retested;
all earlier failures remain retained.
[Operating guide](guides/timing-intent.md),
[actual example evidence](../../hardcaml_asic_example/docs/pt7_timing_final.md),
[independent final review](development/pt7-final-review.md).

PT.7c native-v2 coarse run `83a02bdcb4b34f22bbc03b0ec5223b41`, bundle
`9d01f5c006c697dd103762c04038fd73e8322d3b546f2bf56dfcad0de19cc8c6`, retains five
stages, six exact stored relations, seven explicit diagnostic categories and 12
endpoints. Actual execution provider `4d4316e3a33942c2a577d6f55acaa34a` is separate
from final corrected inspector `4c9ae1a7a50444179c8ab69469443c19`; the reader-only
LF grammar fix replays the unchanged native artifacts. Native controls expose
removed/restricted capture exceptions and missing delays/clocks. Declared downstream
hold is still −0.044244 ns; broader native min samples reach −0.243063 ns. No timing
closure or P&R is claimed. [Native evidence](../../hardcaml_asic_example/docs/pt7c_native_v2_final.md),
[final reader qualification](../../hardcaml_asic_example/docs/pt7c_reader_r1b_final.md),
[independent final review](development/pt7c-final-review.md).
The intermediate native-v1 format is explicitly unsupported by the current reader;
its frozen provider/history remain intact. Original standalone audit schema1 retains
its original supported semantics.

Named native attempts `cb5e7cd92dbf43b4a6c00a74b61c6d72` (area) and
`9374823306a64897959e1b2295f52c84` (delay) each preserve five stages, six relations,
seven categories and 12 endpoints. All seven checkpoint files remain unchanged;
1584 verified sources include 1062 explicit declared-path/canonical-target alias
proofs without dropping the conservative PDK closure. Provider generation
`ce6be246fafa4d0dbe1dd00da980482b` produced both runs. The final external-pin
validation correction was reader/publisher-only; current helpers replay both
unchanged outputs, separately identified from their execution provider.
[Native acceptance](../../hardcaml_asic_example/docs/pt7named_native_final.md),
[corrected-reader receipt](../../hardcaml_asic_example/docs/pt7named_reader_r4_final.md),
[independent review](development/pt7-named-final-review.md).
Both profiles retain −0.044244 ns downstream hold violations. No P&R or closure claim.

**Exit:** generic implemented hardware has reviewed, traceable constraints with
explicit coverage and producer limitations. Protemu's final physical selection
additionally requires its own consumer review and affected checks. Digital
phase-sweep tests and ordinary STA do not establish analog metastability reliability.

## PT.8 — Reproducible design-variant comparisons

Generic library/example first slice accepted 2026-10-29:
[bounded variant/comparison plan](development/pt8-variant-plan.md).
Hardware assignments remain distinct from resource policy and existing flow
profiles. New declarations plus read-only evidence comparisons do not establish
Protemu parameter conformance, workload equivalence or final configuration selection.
Library/example workload implementation follows the
[bounded workload plan](development/pt8-workload-plan.md). Generic infrastructure
plus independent-example acceptance can close PT.8 library scope. Protemu adoption
is not a prerequisite; consumer application/selection stays pending independently.

- [x] **Library/example foundation:** checked typed `Hardware_variant`,
  `Project.of_variant` factory association, optional identity-covered manifest metadata
  and historical absence semantics. Depth128/256 adapter behavior and a deliberate
  ignored-depth failure are independently exercised; hashes alone are not conformance.
- [x] **Read-only pair comparison:** public library/CLI over explicit bundle/coarse/
  execution targets, declared axis, exact source identities/differences/outcomes,
  per-metric comparable/confounded/unavailable reasons and eligible right-minus-left
  deltas. Existing parsers/source replay are reused; no winner or tool execution.
- [x] **Workload/result contract:** strict retained logical workload, exact variant-specific
  artifacts, bundle/hardware/simulation binding and independent trace replay for the
  bounded 256×16 latency-one 1RW protocol. Cycles, accepted reads/writes/operations,
  defined observations, read latency and operation/cycle throughput are verified.
  Firmware size/fetch counts remain unsupported for this non-firmware workload;
  historical/unsupplied results remain unavailable rather than inferred.

Corrected installed provider `f205e0d66f8d4f2b8d1e5f098171bb21`, source snapshot
`1f5ac45882b29c7d577937b4ba2eb8960a899bd2221eaae5b3e115a111de3213`.
204 example assertions and18 targeted correction cases pass. Six functional
simulations include five positives and an ignored-depth conformance failure;
no EDA. Independent reviews corrected unchecked coarse input copies, unsafe stored
metadata reads, missing raw-generation fencing and duplicate-field loss before
archive validation. Existing named-macro preparation remains unsupported.
[Guide](guides/variant-comparison.md),
[corrected acceptance](../../hardcaml_asic_example/docs/pt8_corrected_final.md),
[variant review](development/pt8-variant-review.md),
[comparison review](development/pt8-comparison-review.md).

**Owners: ASIC for generic identities/results/comparison; independent example for
workload acceptance and workload-scoped evaluation.**

- [x] **Example workload acceptance:** fresh matched 256×16 latency-one explicit
  Flops/Exact SRAM functional evidence, trace-checked counters and latency/throughput,
  controlled comparisons and meaningful association/rebinding/partial negatives.
- [x] **Example tradeoff assessment:** evidence-based choice or tradeoff for this
  workload, with unmatched synthesis/physical evidence and unavailable claims explicit.

**Library exit:** verified workload/result infrastructure, workload-linked functional/
cycle measurements, controlled actual-variant comparison and independently reviewed
example tradeoff acceptance. Existing named-flow profiles/services are reused.
No universal winner, UI, scheduler or new SRAM feasibility gate is required.

**Library/example exit accepted 2026-11-05.**
[Workload closeout](development/pt8-workload-closeout.md),
[installed example](../../hardcaml_asic_example/docs/pt8_workloads_final.md),
[result review](development/pt8-workload-result-review.md) and
[comparison review](development/pt8-workload-comparison-review.md) record actual
scope and corrected defects. Reader generation `1d3e1195ba4f45fb93eb986ca23cfb75`,
captured source `86d7b71e7992429cf84b239ae70fe7fd3fbbd2b449227760ef64e0dc57e2d466`,
is distinct from simulation producer `59aeb673765d4ef5b9308eb611f8cf0f`.
Both actual emitted implementations pass 1307 cycles, latency1 and787/1307
operations/cycle; seven workload rows have zero deltas. Ignored workload/address
executions fail functionally and expose no performance values. No new synthesis,
P&R or STA: retained historical SRAM physical evidence is qualified context, not
a matched physical A/B. Structural portability/integration tradeoffs are assessed;
no physical area or speed winner is claimed. Independent review corrected a
directory-read descriptor leak, late raw-source/discovery generation gaps and an
auxiliary alias compatibility regression. No remaining blocker in this bounded
scope; sequential audit/ABA and producer-authentication limits remain explicit.

### Separate Protemu application/selection track (pending)

Depends on PT.3 adoption and consumer-specific review; only claims about Protemu
correctness or its best configuration require this work. These obligations do not
block completion of the PT.8 library/example scope above.

- [ ] **PT.8a — Variant declarations.** Parameterize supported memory shapes,
  packing/encoding, FIFO depths, engine configuration and clocks in Protemu.
  Record hardware parameters separately from resource policy and flow profiles.
  Reject unsupported exact mappings; larger/multiple-macro mappings require their
  own explicit capability and conformance work.
- [ ] **PT.8b — Comparable results.** Link firmware size, fetch counts, latency and
  throughput to the exact variant, bundle, flow profile and run. Compare mapped
  and physical area, slack, utilization and acceptance with consistent workloads;
  keep estimates, synthesis evidence and physical evidence distinct.
- [ ] **PT.8c — Selection gate.** Run promising candidates with PT.7 constraints,
  record resource/timing margin and choose the Protemu configuration. Rerun
  affected regressions and preserve reproducible inputs/results for that choice.

**Consumer exit:** Protemu's P5 studies can compare explicit flops and supported SRAM
configurations without ad hoc edits or ambiguous provenance. Existing named-flow
profiles are reused rather than rebuilt as a second experiment system.

## Milestones

1. **Usable macro path:** PT.0–PT.3 complete; real Protemu requests and implements
   an exact SRAM through the ordinary flow, with backend/system acceptance.
2. **Inspectable mappings:** PT.4–PT.6 complete; Workbench and headless clients
   show requested, selected and realized resources with provenance.
3. **Physical selection ready:** bounded PT.7, PT.8, separately qualified all-corner/
   post-PNR timing and consumer review support the selected configuration. Bounded
   PT.7 completion alone is not physical timing acceptance.

Automatic banking/shape adaptation, automatic floorplan optimization, SRAM-backed
tiny FIFOs/register files, new targets and general flow scheduling are outside this
queue unless a concrete consumer requirement promotes them.

## Previous work and retained obligations

The [archived queue](archive/phase-plan-2026-10-24.md#active-queue) retains
per-operation invocation, configuration-changing checkpoint reuse, consumer pin
adoption and source cleanup. These are deferred, not completion claims. Promote
only a concrete dependency of the PT work; record that decision here.

<a id="asic-p6--shared-tooling-distribution"></a>

**ASIC P6:** source-artifact identity, waiver/provisioning authority and independent
installation gates remain open at their [recorded status](archive/phase-plan-2026-10-24.md#asic-p6--shared-tooling-distribution).
Keep P6 IDs when resuming them. Packaging already implemented is not new PT work.

**ASIC P7/S/T:** [historical definitions and disposition](archive/phase-plan-2026-10-24.md#asic-p7-s-and-t--retained-independent-tracks)
remain available. PT.0 supplements S.3; PT.1–PT.2 deliver S.4's production work
without claiming the remaining S.2 timed-model gate passed. Reassess the ABC issue
only for measurements affected by it. See also the [backlog](backlog/README.md).

### Historical evidence links

Stable anchors below preserve incoming links from guides and sibling repositories;
each routes to the archived record rather than a second active queue.

<a id="ocaml-first-flow-infrastructure"></a>
<a id="explicit-stage-refactor-closeout"></a>
<a id="default-flow-journal-closeout"></a>
<a id="default-result-store-and-cli-2-closeout"></a>

- [Native execution and stage delivery](archive/phase-plan-2026-10-24.md#ocaml-first-flow-infrastructure),
  [stage closeout](archive/phase-plan-2026-10-24.md#explicit-stage-refactor-closeout),
  [journals](archive/phase-plan-2026-10-24.md#default-flow-journal-closeout),
  [result stores](archive/phase-plan-2026-10-24.md#default-result-store-and-cli-2-closeout).

<a id="hostcontainer-runtime-policy-closeout"></a>
<a id="tool-environment-closeout-acceptance"></a>
<a id="tool-environment-ownership-first-slice"></a>

- [Runtime policy](archive/phase-plan-2026-10-24.md#hostcontainer-runtime-policy-closeout),
  [tool environment](archive/phase-plan-2026-10-24.md#tool-environment-closeout-acceptance),
  [tool ownership](archive/phase-plan-2026-10-24.md#tool-environment-ownership-first-slice).

<a id="project-centered-entrypoint-acceptance"></a>
<a id="consumer-refresh-and-real-checkout-emitter-closeout"></a>
<a id="public-api-rename-migration-and-evidence"></a>
<a id="controlled-fifo-development-specimen-evidence"></a>

- [Project entry point](archive/phase-plan-2026-10-24.md#project-centered-entrypoint-acceptance),
  [consumer refresh](archive/phase-plan-2026-10-24.md#consumer-refresh-and-real-checkout-emitter-closeout),
  [API migration](archive/phase-plan-2026-10-24.md#public-api-rename-migration-and-evidence),
  [FIFO specimen](archive/phase-plan-2026-10-24.md#controlled-fifo-development-specimen-evidence).

<a id="resolved-build-boundary-hardening-evidence"></a>
<a id="provider-observation-fixture-acceptance"></a>
<a id="librelane-feasibility-probe-review"></a>

- [Resolved-build boundary](archive/phase-plan-2026-10-24.md#resolved-build-boundary-hardening-evidence),
  [observation fixture](archive/phase-plan-2026-10-24.md#provider-observation-fixture-acceptance),
  [initial LibreLane probe](archive/phase-plan-2026-10-24.md#librelane-feasibility-probe-review).

## Recording progress

- Mark an item complete only with its stated evidence; distinguish partial
  implementation, functional verification, physical execution and acceptance.
- Add dated evidence links beside completed PT items. Keep detailed commands,
  tool/source identities and findings in the relevant feature record.
- Keep ordinary tests PDK-free; run physical/model-tool acceptance explicitly.
- Preserve historical IDs and archived evidence. Update this queue when priorities
  change, and archive it before the next substantial replacement.
