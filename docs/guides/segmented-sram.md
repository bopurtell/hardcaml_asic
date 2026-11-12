# Stock SRAM segmented execution — bounded release scope

## Complete tooling scope, qualified 2026-11-12

The provider supports one `RM_IHPSG13_1P_256x16_c2_bm_bist` macro, R0, on
`ihp-sg13cmos5l`, using a schema-2 Hardening macro contract and the existing checked
Metal4 power-access strategy. Supported fresh prefixes end at:

```text
Synthesis → Floorplan → Placement → CTS → Routing → Timing
```

This is real segmented execution, not a fallback to coarse `run --stage full`.
Native schema-3 checkpoints support adjacent explicit-stage restarts in the same
path-bound workspace. Named workflows remain fresh-only. Timing is terminal for
this capability. Macro Signoff, finalization, acceptance, PT.7 linked timing
exceptions, schema-1 physical continuation, relocation and archive are unsupported.
Multiple macros and other macro/placement profiles are outside this qualified scope.

Ordinary extracted STA at Timing is distinct from the PT.7 declaration/exception
producer. Timing completion is distinct from meeting a design's timing goal.

## Terminology

**Macro collateral** describes a predesigned block to each tool: Liberty timing
models, LEF size/pins/obstructions, GDS layout, CDL circuit connectivity, functional
and timed simulation models, and generated synthesis declarations. The generated
black box is not a functional simulation model.

A **named flow** is a project-declared workflow with profiles, assignments and
endpoints. Its **planner** resolves those selections into a deterministic explicit
recipe, configuration and bundle identity. Workbench previews and submits that
captured plan; the provider executes it. A catalog entry alone does not establish
that arbitrary macro inputs are executable.

## Capability and identity

CLI4 metadata under `cli_contract.capabilities` advertises:

```text
sram_segmented1 = true
sram_segmented1_scope.profile = stock256x16_r0_through_timing_v1
sram_segmented1_scope.checkpoint_schema = 3
```

The structured scope records the exact macro/target/orientation, six endpoints,
restart restrictions, unavailable operations, workers6 and STA1. Consumers must
validate the scope, not infer macro support from `namedflow1` or `timing_stage1`.

The tested installation was captured before qualification and its metadata retains
`physical_qualification = "not_run"`. That field is immutable build-time metadata,
not a live evidence-status query. The later qualification receipts below establish
what was actually tested; the tested binaries were not rewritten to change a label.

Shared preflight validates canonical macro resource/configuration linkage,
physical plan/PDN bytes, resolved collateral and exposure symlinks. Runtime helper
bytes enter input compatibility. Rechecks surround backend transport/publication.
Physical/generated settings are reconstructed for named planning rather than
accepting arbitrary path-bearing overrides.

Schema-3 checkpoints retain native State views and copy the Placement-produced
PDN geometry receipt transitively into each successor checkpoint's `views/`, with
hash, size, kind and original producer identity. Synthesis/Floorplan have no PDN
receipt; Placement through Timing require one. Prior producer directories are not
needed for continuation. Old native schema-1/2 readers remain supported; the retired
source-only Python checkpoint reader was not extended to schema3. Host-only
inspection is not selected-runtime validation or physical acceptance.

## Verification

Controlled qualification passes:

- `@test/sram-segmented`: six fresh prefixes, five restarts, collateral/helper/alias
  mutations, transitive PDN evidence, malformed/downgraded checkpoints, runtime
  geometry rejection and unsupported scopes.
- Segment service: 72 checks; checkpoint publisher: 52; checkpoint reader parity:
  34 accepted/142 rejected cases.
- SRAM bundle physical contract/linkage/preflight/coarse checks and coarse nonmacro
  execution/run regressions.
- Virtualenv-path regression: preserve `.venv/bin/python` activation rather than
  resolving it to the base interpreter.

Real installed qualification uses the independent example's program-memory
specimen, not Protemu:

```text
hardcaml_asic_example/.dev/sram-segmented-20261112T080824Z/
  generations/6aad7c011d6c49dbae5c2838beb69f9c/receipt.json
  real-qualification-20261112T082233Z-retry1/verified-summary.json
```

The capture records 839 source files and 1,100 installed file identities. It is a
development capture with explicit dirty-source attribution, not a clean release
commit or source-stamped P6 qualification.

**Actual execution:** six-stage independent checkpoint chain with five restarts,
plus a separate fresh named prefix through Timing. Predecessor producer directories
were moved away before continuation. All 12 schema-3 checkpoints and 24 execution/
publication-validation transports passed. ODB probes at Placement/CTS/Routing/Timing
verified the macro footprint, supply connections and retained PDN shapes. Netlists
retain one macro and the original 16 staging flops.

Both runs completed STA and met the example timing goal:

| Corner | Setup slack | Hold slack | Setup/hold violations |
| --- | ---: | ---: | ---: |
| Fast | 15.809827 ns | 0.223533 ns | 0 / 0 |
| Slow | 11.246561 ns | 0.814843 ns | 0 / 0 |
| Typical | 14.150889 ns | 0.430151 ns | 0 / 0 |

Qualification took 7m25s. Docker observations confirm six-CPU quotas and the pinned
image; all resolved configurations use STA1. These are CPU/tool limits, not a
six-total-OS-thread guarantee. All owned containers exited and locks were released.
The initial virtualenv-readiness failure remains retained; its harness correction
is now in source and covered by a genuine virtualenv regression.

For reproducible explicit qualification, use `test/qualify_sram_segmented.py --help`
with a preinstalled captured provider and built consumer. It does not silently
replace project installations. `--through timing --named-prefix` exercises both
paths; it runs real EDA and needs an allocated serial six-worker lane.

## Workbench proof and limits

The [Workbench release evidence](../../../hardcaml_workbench/docs/sram_segmented_release.md)
records an actual production TUI launch, six-stage observation, same-store restart,
cache revalidation and submission deduplication using this provider generation.

This qualifies tooling on the small example. It does not repair Protemu's failed
48 MHz timing or unresolved Magic findings, complete PT.3c design acceptance,
establish generic macro support, or qualify finalization/signoff.
