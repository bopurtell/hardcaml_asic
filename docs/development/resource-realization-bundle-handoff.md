# PT.5 A — bundle snapshot handoff

Implemented 2026-10-26. Library/fixture evidence only; no EDA or realized-retention
claim. This is the bundle seam for the execution evidence owner.

## Wire and provenance

New `Bundle.render` manifests emit **`resource_snapshot`** immediately after
`resources`. Its value is exactly
`Resource_snapshot.to_json (Resource_snapshot.of_resolved resolved)`, using the
same `resolved` argument used for implementation RTL, settings and macro rendering.
There is no new elaboration, separate preparation identity or preparation link.
The outer bundle remains schema 1; its existing order-sensitive SHA-256 covers
the complete snapshot. Consequently newly emitted bundle identities change.

The snapshot uses the existing schema 1 (no execution-specific schema):

```text
schema_version: 1
project: string; top: string
mode: "implementation"; scope: "resolved"
resources: [                         # unique IDs, sorted by Resource.Id.compare
  id: {path: [string], name: string, display: string}
  request: {kind: string, contract: tagged_sexp}
  requirement: "flops" | "exact" | "exact_or_flop_fallback"
  policy: {kind: "default"}
        | {kind: "instance", resource: string}
        | {kind: "subtree", path: [string]}  # nonempty matching scope prefix
  selection:
    implementation: {kind: "flops"} | {kind: "macro", name: string}
    reason: {kind: "explicit_flops"}
          | {kind: "exact_mapping", technology: string}
          | {kind: "flop_fallback", technology: string}
          | {kind: "budget_exhausted", macro: string, limit: nonnegative integer}
  elaborated_as: "selected_implementation"
  sources: [{kind: "generated_synthesis_rtl"}
          | {kind: "collateral", collateral: collateral}]
  actual_instances: {status: "not_applicable"}  # flops
                  | {status: "rtl_verified", instances: [{module, instance}]}
  collateral: [collateral]
  physical_intent: {status: "unavailable"}
                 | {status: "resolved_intent", location_um: [number, number],
                    halo_um: number, orientation: "R0",
                    supplies: {"VDD!": "VPWR", "VDDARRAY!": "VPWR", "VSS!": "VGND"}}
]
```

`tagged_sexp` is recursively `{"atom": string}` or `{"list": [tagged_sexp]}`.
`collateral` has exactly `role`, `corner`, `reference`, `verification` fields;
roles are `simulation_model`, `timed_simulation_model`, `black_box`, `timing_model`,
`physical_abstract`, `physical_layout`, `spice_netlist`. Only `timing_model` has a
string corner; other corners are null. Verification is `unresolved_reference`.
The macro instance spelling is `Resource.Id.rtl_instance_name id`, checked by
resolution against implementation RTL. RTL verification is not synthesis retention.
Declared-only intent remains supported by the general snapshot reader; resolved
bundles normally carry resolved intent or unavailable intent.

Simulation stays in the existing `simulation_resources` and `simulation_sources`.
No `simulation_snapshot` field is introduced. A simulation snapshot made through
the separate inspection API has `mode: simulation`, `scope: elaborated`,
`elaborated_as: behavioral_model`, generated behavioral sources and not-applicable
instances, even when the selection says macro. It cannot replace the bundle's
resolved implementation snapshot.

## Readers and compatibility

`Bundle.verify_manifest_identity` preserves the historical hash algorithm and
accepts absence of `resource_snapshot` as the explicit legacy wire case. It never
rewrites or synthesizes a snapshot for such manifests. Present null, malformed,
unknown-version, duplicate-field or inconsistent snapshots fail closed. Validation
checks project/top, implementation/resolved role, the full logical inventory
against `resources` (S-expression semantic equality), and resource/module/instance
tuples, collateral inventories and physical intent against `macro_contract.macros`.
Collateral comparison covers role, timing corner and reference, ignores declaration
order and preserves multiplicity. Missing, additional or duplicate entries fail.
Rehashing conflicting inventories does not make them valid. This verifies assertions,
not producer authentication or disk RTL.

`Resource_snapshot.records : t -> Resource.Record.t list` provides the typed logical
projection used for inventory comparison; actual-instance linkage stays in the
snapshot. The strict decoder now rejects empty subtree policy paths, which cannot
be produced by the policy API.

`Methodology_plan` allows precisely this optional top-level field and calls bundle
verification. No-macro plan preparation, decoding and same-bundle publication work
with new snapshots and historical absence. Its fixed field/configuration restrictions
still reject macro named-flow plans; no macro/checkpoint support is claimed.
Other strict library consumer reviewed: `Flow.Methodology_inventory.verify` checks
nested file/source/config inventories, not an exact top-level manifest field list,
so it requires no change. Execution consumers are owned by B.

## Verification

All ASIC Dune commands used the shared lock, switch and explicit six-job bound:

```sh
flock /tmp/opencode/asic-pt5-build.lock opam exec --switch=5.2.0+ox -- dune build lib/hardcaml_asic.cma -j6
flock /tmp/opencode/asic-pt5-build.lock opam exec --switch=5.2.0+ox -- dune runtest test/resource_realization_bundle -j6
flock /tmp/opencode/asic-pt5-build.lock opam exec --switch=5.2.0+ox -- dune runtest test/plan_contract test/resource_preparation -j6
flock /tmp/opencode/asic-pt5-build.lock opam exec --switch=5.2.0+ox -- dune runtest test/resource_realization_bundle test/plan_contract test/resource_preparation -j6
```

Original combined run passed: **53 focused bundle assertions**, existing preparation
library tests and **108 plan domain/wire/render checks**. The new test directory
has its own Dune declaration; shared root declarations were not edited.

Focused coverage includes exact equality to the rendering resolution, constructor
counts, deterministic rerender without elaboration, empty/flop/fallback/multiple
resource and exact-macro inventories, instance/subtree policy roundtrips, resolved
physical intent and conflicting-intent rejection, identity coverage, ordinary tampering and rehashed-invalid schema,
project/top/scope/inventory failures, same-count renamed/wrong-type macro and duplicate
macro failures, simulation separation, historical manifest/plan roundtrip, new-plan
render/prepare/decode, exact publication/reuse, and publication corruption rejection.
Tests read the current checkout's declared input; they make no fixture commits and
mutate only their disposable publication store. No retained evidence is rewritten.

Initial focused runs exposed a deprecated temp-file API and an invalid empty-subtree
fixture; both were corrected before the passing runs above. No broad root runtest,
example acceptance, execution integration or EDA was run by A.

## Independent-review correction — collateral consistency

B's independent review reproduced a gap: resealing a new manifest after changing
`macro_contract.macros[].collateral` references or removing that inventory was
accepted even though the snapshot and logical resource sources still disagreed.
The bundle verifier now cross-checks the macro-contract inventory against the typed
logical collateral already verified against snapshot and legacy resource sources.
Macro roles are decoded as S-expressions, so timing corners participate in the
comparison without depending on whitespace. Each contract collateral entry must
contain exactly `role` and `reference`.

The focused suite now has **62 passing assertions**, including resealed mutations
of reference, role and timing corner; empty, missing, truncated and duplicate
collateral inventories; reordered-equivalent inventory acceptance; and historical
macro manifest acceptance without a snapshot. The historical absence path retains
its original verification semantics. This change does not inspect collateral bytes
or certify external PDK references.

Verification command:

```sh
flock /tmp/opencode/asic-pt5-build.lock opam exec --switch=5.2.0+ox -- dune runtest test/resource_realization_bundle -j6
```

The first added role-mutation fixture accidentally retained an existing Black_box
role; it was corrected to always change the role before the passing run. Only
A-owned bundle verification, focused tests and this handoff were changed. B-owned
source, retained evidence and execution integration were not modified; no EDA or
Git operations were performed for this correction.
