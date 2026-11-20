# Inspect selected and realized resources

Preparation selects resources; bundle emission records their expectations;
synthesis and physical result validation establish which instances survived.
These are separate facts, not another physical execution stage.

## Read-only inspection

```sh
hardcaml-asic-flow resources-inspect "$RUN_DIR"
hardcaml-asic-flow resources-inspect "$RUN_DIR" --json
```

The public library equivalent is:

```ocaml
Hardcaml_asic_execution.Resource_evidence.inspect ~run_dir
(* (Evidence_json.t, string) result *)
```

Inspection reads retained evidence without EDA, current sources, PDK discovery or
publication. Supported inputs are ordinary coarse run directories and the retained
example synthesis/physical snapshots described below. Exit codes:

- **0:** no failed required-macro reconciliation checks. A resource-free/flop-only
  design may have no required macro checks; consult coverage and availability.
- **1:** required reconciliation failed, required evidence is unresolved, or supplied
  physical/timing provenance or measurement evidence is contradictory.
- **2:** invalid input or provenance.

The current schema-2 `hardcaml-asic.resource-evidence` object exposes manifest-derived
`resource_inventory`, stage-specific `observations`, `check_outcomes`, provenance,
coverage and hashed `evidence_refs`. Expectations and observed instances remain
separate. Missing, unexpected and unresolved outcomes have explicit reasons;
absence is never assumed to prove optimization. Instance identity matters even
when aggregate counts agree.

Schema 2 adds per-resource physical geometry, structured component-area/ratio
evidence and sampled macro timing/electrical links. Schema-1 readers and full source
replay remain supported: replay uses the **recorded schema's semantics**, without
adding new assertions or sources to historical schema-1 reports. Current collection
and read-only reporting emit schema 2. CLI capability `resource_evidence2` advertises
the new format alongside explicit supported read/write schemas.

New bundles contain an identity-covered `resource_snapshot` made from the same
resolved implementation used to render the bundle. Historical manifests without
it remain readable through an explicitly labeled legacy inventory adapter.
`Chip_target.prepare_resources` and `Methodology_plan.prepare` are different APIs:
neither shared names nor similar inventories prove that a separately retained
PT.4 attempt produced a bundle. `preparation_link` currently remains unavailable.

## Collection, acceptance and archives

Ordinary collection embeds the same object in `results.json`. Authoritative
`Report_service.run` rechecks source integrity and reconciles expectations in
memory, including historical results that predate this field. Required macro
mismatches fail the relevant acceptance checks. Historical files are not rewritten
by reporting. Existing physical/checker and external postcheck requirements remain.

`Resource_evidence.validate` checks schema and internal consistency, not the truth
of an arbitrary assertion against external files. Use `verify_sources ~run_dir`
for source-aware verification: it rechecks advertised hashes and replays the full
reconciliation. Explicit in-memory execution contexts require the matching
`~context`. Report and archive use this replay. Genuine failed evidence remains
archivable; forged passing assertions over failed source evidence are rejected.
Archives retain every advertised resource evidence source.

## Interpret counts and areas

Every measurement has a unit, definition, stage, scope, evidence references and
an unavailable reason where needed. In particular:

- Mapped cell totals include macros. Standard-cell count excludes declared macros.
- Synthesis standard-cell area is cross-checked against retained mapped library
  attributes. Macro area requires bound, hash-verified default-corner Liberty.
  Their sum is a **combined cell-area estimate**, not placed/routed or die area.
- Final netlist counts include fillers/decaps/taps/ties present in that netlist;
  they are not the native logical standard-cell count from the placement database.
- Final physical standard-cell area is separate from the synthesis estimate.
  Standard-cell utilization is not whole-design occupancy; the retained producer
  percentage does not establish its denominator or excluded regions.
- Sequential counts/area are whole-design evidence, not per-resource flop cost.
  Optimized flop ownership remains unavailable.
- Where retained proof supports it, physical macro footprint is derived from
  executed geometry, actual DEF placement and exact final-netlist ownership.
  It excludes halo, supply bridges, routing and surrounding repair logic.
- Complete bound LEF/DEF coverage provides separate nonspacer, spacer/fill/decap,
  macro and all-component footprint sums. Missing LEFs leave exact sums unavailable;
  known partial coverage still rejects contradictions it can prove. Native rounded
  producer metrics remain separate from exact DBU/LEF-derived values.

## Physical footprint and gross-area ratios

Final observations expose `physical.floorplan`, `physical.measurements` and
`physical.ratios`; resource rows expose `geometry` and footprint `area`. Each ratio
retains explicit numerator and denominator measurements and evidence references.
Core/die denominators are **gross bounding rectangles**, not usable placement sites.
Numerators are sums of component rectangles, **not geometric unions or routed-metal
coverage**. Ratios do not reverse-engineer the producer's utilization definition.

Supported physical proof currently uses the retained `cmos5l_real_gds_v1` checker
contract for the stock macro/R0 profile. Unsupported layouts, transformations and
missing measurement sources are explicit; this does not extend physical qualification
to arbitrary macros. Contradictory supplied geometry, counts or areas fails the
measurement observation, including after disposable fixture hashes are rebound.

## Sampled resource timing and electrical observations

With retained post-PNR reports, a resource's `timing_links.paths` identifies exact
data endpoints, macro pins, corner, min/max mode, group, signed slack and report
line spans. `electrical_links.points` retains observed slew, reported output load,
fanout, delay, arrival, edge and data/launch-clock/capture-clock roles. Blank columns
stay null. Capacitance on a macro output is **reported load**, not intrinsic pin cap.

Every link retains producer/run/build/netlist/config/state/log/report/unit-library
provenance. External model paths/hashes are recorded references, not claims that
the model bytes were retained or re-read. Fast −40°C analysis remains distinct
from its bound −55°C SRAM model. Unit declarations must be direct library attributes.
Malformed report sections cannot turn a clock pin into a data endpoint.

Coverage is `sampled_reported_paths_not_exhaustive`; electrical compliance is
`not_assessed`. Partial/unreadable reports produce diagnostics. Resource retention
can pass while some optional timing links remain unavailable; inspect diagnostics.
A valid negative-slack path is retained measurement evidence, not a macro-retention
failure. Existing global timing acceptance checks remain independent.

This slice does not establish exhaustive timing coverage, per-pin electrical limits
or margins, timed SRAM conformance, resource power, IR/EM qualification, or flop
ownership. The typed parser supports the validated native post-PNR report format;
unknown units/layouts are diagnosed rather than guessed.

Supported mapped standard-cell classification requires known library namespaces,
finite area attributes and blackbox leaf semantics. Unsupported hierarchy becomes
unresolved rather than hiding nested macros. A mapped file alone does not prove a
completed synthesis boundary. Physical checks use the retained checked final
netlist and artifact hashes, not a synthesis file with a convenient basename.
Checkpoint-only stage reports explicitly lack per-resource instance evidence;
this feature does not add named-flow macro/checkpoint execution support.

## Validated example

From `hardcaml_asic_example`, using its isolated PT.5b capture:

```sh
P="$PWD/.dev/asic-pt5b/current/bin/hardcaml-asic-flow"
"$P" resources-inspect acceptance/sram/8b48d7d0-functional --json
"$P" resources-inspect acceptance/sram/physical-p4 --json
"$P" resources-inspect acceptance/resource-measurements/accepted-live-01/run --json
```

The synthesis specimen reports 176 total cells: **175 standard cells + one SRAM**.
Its standard-cell area is **2,182.7232 µm²**, bound macro Liberty area
**28,127.104 µm²**, and combined estimate **30,309.8272 µm²**. The separate physical
run reports **4,793.64 µm²** final standard-cell area. Its final netlist contains
68,928 non-macro instances including fillers/decaps, unlike the native metric's
389 logical standard cells. The physical snapshot lacks bound synthesis stat and
Liberty area inputs; inspection does not borrow them from the other run.

Physical schema-2 evidence additionally establishes:

| Quantity | Value |
| --- | ---: |
| Macro rectangular footprint | 28,127.104 µm² |
| Nonspacer component footprints | 4,793.6448 µm² / 389 cells |
| Spacer/fill/decap footprints | 866,655.4176 µm² / 68,539 cells |
| Nonspacer + macro footprint sum | 32,920.7488 µm² |
| Gross core rectangle | 902,417.2416 µm² |
| Macro / gross core | 3.116862434% |
| Nonspacer + macro / gross core | 3.648062923% |
| All components including spacers / gross core | 99.685170554% |

The new augmented capture retains unchanged raw reports from the same accepted run,
yielding **186 linked sampled paths / 372 electrical observations**. For example:

- Slow setup, macro `A_DOUT[11]` → `uo_out[3]`: slack **11.246561 ns**,
  macro output slew **0.067838 ns**, reported load **0.004815 pF**.
- Fast hold, `_203_/Q` → macro `A_ADDR[6]`: slack **0.223533 ns**,
  macro input slew **0.167545 ns**; its blank Cap field remains null.

Original `physical-p4` has no raw STA reports, so its resource timing still stays
unavailable; the inspector does not borrow reports from the new capture. All old
evidence and app-provider selections remain preserved.

See the [PT.5b closeout](../development/resource-measurements-plan.md#implementation-and-acceptance-closeout),
[original reconciliation record](../development/resource-realization-plan.md#implementation-and-review-closeout-2026-10-27)
and [measurement acceptance guide](../../../hardcaml_asic_example/docs/resource_measurements.md)
for identities, negative fixtures and limits. The old physical qualification
remains unchanged: raw Magic=3 with exact disposition, abstract macro-boundary LVS,
and no new timed-model/IR-EM claim.
