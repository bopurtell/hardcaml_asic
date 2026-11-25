# Stock SRAM segmented execution — bounded release scope

**Synthesis-only bundles:** `sram_synthesis1` is a separate capability from
the physical flow below. It accepts schema-1 `Synthesis` bundles with one or
more registered stock 256×16 SRAM instances, including designs mixing SRAMs
and flop fallback. Only a fresh synthesis endpoint is supported. Its checkpoint
has profile `stock256x16_fresh_synthesis_v1` and no restart targets. It makes
no placement or timing qualification claim.

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
this capability. Macro Signoff, finalization, acceptance, linked timing-intent
exceptions, schema-1 physical continuation, relocation and archive are unsupported.
Multiple macros and other macro/placement profiles are outside this qualified scope.

Ordinary extracted STA at Timing is distinct from the timing-intent declaration/exception
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

This qualifies the tooling on a small example; it does not establish generic macro support or qualify segmented finalization/signoff.
