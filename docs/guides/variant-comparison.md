# Declare hardware variants and compare retained evidence

The first PT.8 slice records hardware parameters in ordinary project/bundle
identities and compares two explicit retained targets through public library/CLI
interfaces. It does not launch experiments or choose a winning design.

## Keep the three configuration domains separate

| Domain | Example | Existing owner |
| --- | --- | --- |
| Hardware assignment | Memory depth128 versus256 | `Hardware_variant`, consumed by the project factory |
| Resource policy | Explicit flops versus Exact SRAM | `Resource_policy` |
| Backend configuration | Balanced versus delay-focused synthesis | Existing flow catalog, profile and typed parameters |

`Hardcaml_asic.Hardware_variant` supports bounded integers, booleans and closed
enums. Schemas have unique stable keys; assignments supply every key exactly once.
The checked codec normalizes ordering and identity, rejects duplicates/unknown
keys/types/limits, and bounds serialized input to128KiB. Integer domains are
inclusive ranges; consumers still reject unsupported tuples or gaps in a range.

Use `Project.of_variant ~variant ~create` to pass the same immutable assignment
to the project factory and attach it to its result. The factory is called once;
already-attached projects reject. Ordinary `Project.create` stays compatible.

```ocaml
let open Core.Or_error.Let_syntax in
let module H = Hardcaml_asic.Hardware_variant in
let%bind parameter = H.Parameter.create ~key:"depth"
  ~kind:(Integer { minimum = 128; maximum = 256 }) in
let%bind schema = H.Schema.create ~design_id:"example_memory"
  ~schema_id:"example_memory.v1" ~parameters:[parameter] in
let%bind variant = H.create ~schema ~assignments:["depth", Int 128] in
Hardcaml_asic.Project.of_variant ~variant ~create:(fun checked ->
  let%bind depth = H.get_int checked ~key:"depth" in
  if depth <> 128 && depth <> 256 then Core.Or_error.error_string "unsupported depth"
  else make_project ~depth ())
```

Here `make_project` is the consumer's normal project constructor. See the complete
reviewed adapter in
[`variants/variant_memory.ml`](../../../hardcaml_asic_example/variants/variant_memory.ml).

The optional `hardware_variant` manifest object is identity-covered alongside
actual RTL, SDC, configuration and declared source copies. Existing methodology
identities cover the resulting bundle. Historical absence means **parameters not
recorded**, not inferred defaults. Resource preparation includes the declaration in
its configuration provenance; its restored generic declaration is opaque metadata,
not independent typed hardware-use validation or preparation-to-bundle proof.

**A valid declaration is not proof the circuit uses it.** A factory could ignore
depth and always construct256 words. The example intentionally does that in a
negative test: metadata and hashes remain valid, but independent registered-request
and address-boundary checks fail. Supported adapters need such semantic checks.
No compiled-source/P6 or authenticated execution claim follows from these identities.

## Compare two explicit targets

```sh
hardcaml-asic-flow compare \
  --left-kind bundle --left "$DEPTH128_BUNDLE" \
  --right-kind bundle --right "$DEPTH256_BUNDLE" \
  --axis hardware --json

hardcaml-asic-flow compare \
  --left-kind execution --left "$AREA_EXECUTION_JSON" \
  --right-kind execution --right "$DELAY_EXECUTION_JSON" \
  --axis flow_configuration --json
```

Kinds: `bundle`, `coarse_run`, `execution` (exact execution JSON file).
Axes: `hardware`, `resource_policy`, `flow_configuration`, `descriptive`.
No kind/most-recent run/current-catalog inference is performed. Named inputs keep
their existing path-bound reference requirements.

Public API:

```ocaml
Hardcaml_asic_execution.Variant_comparison.inspect
  ~left:(Bundle left_path) ~right:(Bundle right_path)
  ~axis:Hardware ()
(* (Evidence_json.t, string) result *)
```

Capability `variant_comparison1`, envelope `hardcaml-asic.variant-comparison`,
schema1, policy `bounded-pair-v1`. The whole response is bounded at1MiB, with
4096 inventory/difference rows and256 projected metrics per side. Limits reject
rather than truncate. There is no paging/sweep/publication service in this slice.

## Interpret the result

The response retains left/right source digests and exact bundle/attempt/plan/
publication identities, frozen settings, resource observations, actual output
hashes, separate outcomes, domain-keyed differences and a comparison digest.
Different attempts remain different even if netlist bytes match.

Each metric is independently classified:

- **Comparable:** observed/derived values with established matching required
  context receive `right_minus_left`. Counts preserve exact integer arithmetic;
  slack stays signed.
- **Confounded:** values exist but required stage/model/corner/RC/environment or
  other controls differ; delta is null with reasons.
- **Unavailable:** a value or required control is missing/unsupported; no delta.
  Estimates stay estimates and receive no measured delta.

The axis states intended differences, not causality or permission to ignore other
controls. A bundle/netlist change is expected for hardware variants, but irrelevant
model/workload/environment differences still matter. The small metric registry
uses existing source-aware resource and native timing readers; it does not parse
new STA/Yosys reports or promote sampled witnesses to whole-design worst slack.

For example, comparing the accepted physical run to itself yields18 native
per-corner zero deltas. Comparing its synthesis baseline to the routed run yields
18 **confounded** native rows, because stage/RC/environment differ. Physical closure
pass, historical negative hold, and unavailable exhaustive coverage remain separate
facts. Equal profile-pair netlists do not imply equal attempts or a performance winner.

Exit0 means a readable comparison, including confounded/unavailable measurements
or failed source outcomes. Exit2 means invalid/corrupt/inconsistent/unsupported
input or a limit violation. Neither exit is design acceptance.

Readers verify actual retained member/input bytes, source-aware evidence and both
captured generations before returning. Consulted stored metadata bytes are part
of the generation fence; even a formatting-only change changes its digest.
Regular-file admission is bounded and nonblocking, rejecting FIFOs/symlinks and
concurrent replacement. Reads launch no tools/renderers, write no evidence and
perform no network/current-PDK discovery. Existing source-reader limits still apply.

## Accepted scope and limits

[Corrected installed acceptance](../../../hardcaml_asic_example/docs/pt8_corrected_final.md)
covers fresh depth128/256 and Flops/Exact256 bundles, profile-only changes, ignored
parameters, actual historical named profiles, physical self and synthesis/physical
comparisons, API/CLI parity and corruption/race negatives. No EDA was run.

Exact256 bundle emission is supported; **Exact macro `Methodology_plan.prepare`
remains a pre-existing unsupported named-macro boundary**. This feature does not
enable it. Historical records are never retrofitted with hardware metadata.

Firmware size and fetch count remain unsupported for the memory protocol below.
There is no automatic winner, experiment scheduler or Protemu configuration selection.
The provider remains headless; the separately delivered
[Workbench integration](../../../hardcaml_workbench/docs/pt8_workloads.md) consumes
its public results and comparison decisions.
The [PT.8 queue](../phase_plan.md#pt8--reproducible-design-variant-comparisons)
owns those remaining consumer/workload obligations.

## Explicit checked workload results

### Terminology: stimulus, workload, test and suite

**Stimulus is a component of a workload, not the entire workload.** In this guide,
a workload contract describes a scenario and how its execution is evaluated:

```text
Workload contract
├── Stimulus: operations, transactions and logical input data
├── Execution assumptions/rules: initial state, ordering, timing and handshakes
├── Completion rules: when the scenario ends
├── Expected behavior: which observations are defined and what must hold
└── Measurement rules: what to count and over which sample window
```

These are logical responsibilities, not required software classes or a promise
that the current codec can represent arbitrary scenarios. For the supported
memory protocol some rules are fixed by the versioned protocol; operations,
expected observations, seed declaration and measurement window are explicit.

For a UVM reader, sequence items are the raw stimulus; a sequence/virtual sequence
is closest to the traffic/scenario portion. The broader workload contract also
specifies assumptions, expected behavior and measurement rules that UVM might
distribute across test configuration, assertions, a reference model, scoreboard
and monitors. Reusable drivers/checkers implement that contract; they do not have
to be physically contained in one workload object. DUT variant, concrete artifacts,
verification environment and execution attempt remain separately associated facts.

A **test** applies checks to an execution. A **case** is a runnable configured
test/scenario; a **suite** organizes selectable cases; an **attempt** is one actual
execution. A case can conceptually consume one or more workloads, and a workload
can be executed on one candidate or reused across several. Those relationships
are not a requirement to compare multiple designs every time a workload runs.

Current PT.8 cases `flops256` and `exact256` each execute the same single fixed
logical memory workload against their respective implementation. Its fill/read/
hold/boundary phases are part of that one workload. Arbitrary user-authored
composition of N workloads per case is **not implemented**. Workbench uses the
existing test-suite/case runner to execute these cases; workload verification is
an additional result contract, not a second test runner.

Logical workload identity stays separate from variant-specific stimulus/image
bytes, driver/model/tool artifacts, actual hardware/bundle and simulation attempt.
The same logical job can have different concrete encodings. A label, equal input
bytes or a successful provider process alone establishes neither equivalence nor
functional success. Exact concrete evidence is retained and replay-checked.

### Inspect and compare explicit evidence

Attach either or both explicit version1 memory workload result files:

```sh
hardcaml-asic-flow compare \
  --left-kind bundle --left "$FLOPS_BUNDLE" --left-workload "$FLOPS_RESULT" \
  --right-kind bundle --right "$SRAM_BUNDLE" --right-workload "$SRAM_RESULT" \
  --axis resource_policy --json
```

```ocaml
Hardcaml_asic_execution.Variant_comparison.inspect
  ~left:(Bundle flops_bundle) ~right:(Bundle sram_bundle)
  ~axis:Resource_policy ~left_workload:flops_result ~right_workload:sram_result ()
```

Both optional arguments precede the final unit. Existing calls and the
`variant_comparison1` capability/schema/policy remain compatible. Additional
capability `workload_comparison1` advertises the explicit options. Omitted paths
preserve the old envelope; one supplied side exposes checked evidence but cannot
produce a two-sided workload delta. Wrong target associations are errors, including
valid workload evidence for a different verified bundle. Hardware variant identity
must be present and match exactly. The simulation is associated with the verified
bundle and hardware, **not** with a coarse-run or execution attempt merely because
they share that bundle. Its own simulation attempt remains in `side.workload.binding`.

Public `hardcaml-asic-flow workload-inspect RESULT --json` (and
`Workload_result.inspect`) additionally exposes `binding.bundle`, the absolute
canonical selected root checked by the bundle verifier. Workbench can capture
this exact locator directly. The existing `binding.bundle_identity` stays the
identity; the locator is descriptive verified-root context, not another identity
or an authentication claim. It is additive to the inspection projection only:
retained result/simulation records and the FS2 result-reference artifact schema
are unchanged. Older cached/synthetic projections may omit it; consumers retain
their explicit-path fallback. Fresh inspection/source digests cover the locator.

`Workload_result` checks the bounded `memory-1rw-latency1-v1` protocol: 256×16,
undefined initial RAM, one-edge read latency, explicit operations and before/after
observations, and a nonempty half-open measurement window. It independently replays
the expected memory semantics and trace and checks every stored counter. See the
[frozen wire/API contract](../development/pt8-workload-handoff.md). Simulation process
completion and functional pass are distinct. Failed, partial, interrupted or
functionally failing records stay readable when internally consistent, with
unavailable performance values and no deltas.

The workload metric family requires equal checked logical workload identity
(including seed, operations, expected observations and window), adapter, clock,
measurement definition, tool artifact hashes, RTL mode and functional model
semantics. Stage, scope/window, units, classification, quantity and each metric
definition also must match. Missing context is unavailable; differing context is
confounded. These controls are specific to functional simulation: physical library,
RC and flow controls continue to govern physical metrics independently.

Flops and SRAM may have different actual RTL and concrete model bytes while using
the same supported adapter/interface semantics and functional model definition.
Those bytes are never substituted for one another: each side retains actual
artifact references, source hashes and simulation identity in workload provenance.
A different declared semantic model context cannot yield a delta; concrete model
file sets can differ under that common context. The shared
model declaration is checked retained producer context, not independent proof that
arbitrary model implementations satisfy their declared semantics.

Cycles, reads, writes, operations, defined observations and read latency use exact
integer deltas. Throughput is operations/cycle: both source numerator/denominator
pairs remain visible and `right_minus_left_ratio` gives the reduced exact rational
delta alongside the numeric `right_minus_left`. Equal Flops/SRAM cycle counts and
throughput are reported as zero deltas, not as a speedup. Neither host runtime nor
a clock-projected physical rate is measured hardware speed. This does not establish
matched physical area/timing or firmware size/fetch evidence.

Every admitted raw workload source, including the raw result and simulation JSON,
stimulus, trace, RTL, model, testbench and tool artifacts, is digest-bound. The
comparison replays both existing target generations and `Workload_result.recheck`
before a final bounded raw-byte audit of their combined consulted source closure.
When collection discovers postchecks, the fence also checks `checks/` existence,
resolved location, child membership and `postcheck.json` presence, including an
initially absent or empty directory. Directory enumeration is limited to8192 child
entries; the final raw union is limited to16384 paths/2GiB, with existing reader
limits still applying. Permitted target/auxiliary aliases retain their existing
admission semantics and have their canonical resolutions pinned and checked;
workload-relative references retain descriptor-bound no-follow admission. No deep
source replay follows the final audit. This sequential fence detects the tested
persistent changes during opposite-side validation; it is not an atomic cross-file
snapshot or an adversarial ABA-history detector.
With a workload attached, the side `source_digest` binds both target
and workload digests; the nested workload digest remains independently visible.
Changing only JSON whitespace also changes the generation. Source and descriptor
admission gates remain in force. The complete comparison still rejects above1MiB;
it never truncates evidence. Hash/replay consistency is not authenticated execution,
semantic source-use proof, adversarial ABA-history detection, timing/SDF validation
or P6 compiled-source consistency.

The [workload closeout](../development/pt8-workload-closeout.md) records final
installed example acceptance and independent review. PT.8 library/example scope
is complete for this supported protocol; Protemu application/selection remains
separate. The matched example reports equal cycle latency/throughput and an
implementation/integration tradeoff, with physical area/speed deltas unavailable.
