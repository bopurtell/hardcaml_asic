# PT.8 workload/result slice — bounded implementation plan

2026-11-05. Written before implementation. `docs/phase_plan.md` remains the only
active ASIC queue. Acceptance consumer: `hardcaml_asic_example`. Library closure
does not require Protemu adoption; that remains PT.3 and its consumer selection.

**Accepted 2026-11-05:** [actual closeout](pt8-workload-closeout.md) records
implementation, independent findings/corrections, installed acceptance and limits.
The original bounded plan follows.

## Scope and shared contract

Extend retained evidence and the existing explicit pair-comparison service with a
strict versioned workload/result record. Start with a bounded latency-one 1RW
memory trace protocol: logical operations, no initialization/reset of RAM, explicit
defined expected observations, deterministic seed/termination and half-open sample
window. This is an explicitly supported protocol, not a universal workload oracle.
Keep logical workload identity separate from exact stimulus/image bytes, actual
RTL/model/testbench/tool settings and exact bundle/hardware/simulation attempt.

The reader must replay the retained operations and observations to check expected
outputs and recompute cycles, accepted reads/writes, defined observations, read
latency and operation/cycle throughput. Counters supplied by a producer are checked
assertions, not authoritative measurements. Firmware size/fetches stay unavailable
for this non-firmware specimen. Ratios retain numerator/denominator; host runtime
and clock-projected rates are not measured hardware speed. Functional pass and
performance availability are separate. Failed/partial/interrupted records retain
their actual outcomes and cannot produce complete-window performance deltas.

Use `Retained_file` descriptor-bound nonblocking bounded reads and strict raw JSON
admission. Bind raw consulted bytes and replay the whole generation before return.
Reuse existing bundle and comparison verification, never weaken historical gates.
Integrity/replay is producer-evidence consistency, not authenticated execution/P6.

## Exclusive slices and dependencies

| Owner | Exclusive files | Acceptance / dependency |
| --- | --- | --- |
| A — workload evidence | New `execution/results/workload_*`, `test/workload_result/`, `docs/development/pt8-workload-handoff.md` | Publish concrete wire/API first; strict bounded schema, associations and trace-derived measurements; coherent false-counter negatives |
| B — comparison integration | Existing `variant_comparison.*`, `bin/experimental_compare.ml`, CLI routing/capabilities as necessary, `test/workload_comparison/`, guide | Depends on A handoff; optional explicit left/right workload result paths; exact target binding; metric-family controls, generation fencing, honest deltas; old calls compatible |
| C — independent example | New example workload adapter/tests/scripts/docs and `acceptance/pt8-workloads/`; isolated store/build | Depends on A wire; fresh matched Flops/Exact256 bundles and real emitted RTL functional simulation, stock vendor macro model; installed public acceptance and preservation |
| Coordinator | Plan/queue, interface agreement, orchestration, independent review and closeout | Review after initial suites; fix/retest actual findings; assess workload-scoped tradeoffs |

A's concrete handoff freezes wire/signatures before B/C depend on them. Use JSON
records/projections at the existing execution service boundary rather than another
runner. B owns shared CLI/build declaration edits; A/C request changes through B.
Consumers use public provider verification/comparison, not an example-owned parser.

## Acceptance

Matched 256×16 latency-one memory, explicit Flops vs supported Exact SRAM, same
logical workload and adapter/clock/measurement definitions. Exercise all addresses,
boundary 0/127/128/255, meaningful data, disabled reads and writes, holds/stalls,
and before/after-edge latency. Never compare unspecified initial/write responses.
Stock vendor functional RTL must actually execute for Exact; behavioral Simulation
elaboration alone is insufficient evidence of that implementation.

Negatives: wrong logical workload/artifact/variant/bundle/attempt associations;
ignored hardware/workload parameters; changed stimulus/expected values/windows;
coherently rebound false counters/results over unchanged trace; truncated/missing/
interrupted evidence; mismatched stage/corner/clock/model/measurement definitions;
source replacement and stale captured generations. Preserve genuine failures.

Evaluate observed equal cycle throughput honestly. Retained physical SRAM evidence
is qualified context, not a matched new A/B physical study. Inspect retained evidence
for comparability before using any area/timing delta. A useful tradeoff assessment
may explicitly withhold unestablished area/physical-speed claims. Fresh synthesis
requires a written measurement gap, allocation and thread limits; P&R is not default.

## Operating budget and preservation

At most three implementation agents, each at most six jobs, two reserved (global
maximum20 including nested builds/tools). Every Dune invocation has explicit `-j6`
or lower, switch `5.2.0+ox`. Serialize ALL Dune operations per checkout with
`/tmp/opencode/asic-pt7-build.lock` and `/tmp/opencode/example-pt7-build.lock`,
including provider capture nested builds. New store `.dev/asic-pt8-workloads`,
build `_build_pt8_workloads`. **EDA allocation zero initially**; functional Icarus
simulation is allowed within the owning six-job allocation. No Git mutations,
existing provider/config/daemon changes, historical evidence edits, Protemu,
Workbench/UI, arbitrary optimization, scheduler or new SRAM feasibility work.

## Exit

Strict public workload/result verification and controlled comparison pass pure and
meaningful negative tests, then installed independent-example acceptance; independent
review findings are resolved and retested. Record exact sources/provider/commands,
outcomes, failures/skips and limitations, with an evidence-based tradeoff for this
workload. This can close PT.8 **library/example scope**. Protemu correctness, adoption
and best-configuration claims remain independently pending on the consumer track.
