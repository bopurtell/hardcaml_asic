# Declare and inspect narrow timing intent

Timing intent extends the existing `Timing` declaration and generated bundle SDC. The first
qualified subset is scalar synchronizer stages, one rising primary `clk`, exact
input bits/scalars, and selected CMOS5L mapped reset flops. It does not insert
registers or alter latency/reset behavior.

## Declare existing hardware

Define `Timing.Stage.create ~id ~chain ~index` references, with contiguous indices
from zero and at least two scalar stages per chain. During design construction,
register each existing register signal:

```ocaml
Elaboration.Context.register_timing_stage_exn context ~stage ~q;
```

Registration attaches a reserved Q anchor and Yosys `keep=1`. Resolution validates
the actual circuit, primary clock, scalar register kind, absence of enable/clear,
exact registration inventory and direct predecessor-Q/downstream-D connections.
The attribute is preservation intent; successful native mapping is separate evidence.

Use `Timing.create ~input_delays ~output_delays ~stages ~exceptions ()` in
`Chip_target.create`. Existing two-field Timing record literals need migration to
`Timing.create` (or explicit new empty `stages`/`exceptions` fields). `Timing.empty`
retains no timing exceptions. Existing min/max I/O delay semantics are unchanged.

Supported exception constructors:

```ocaml
Timing.Exception.async_capture
  ~id:"capture_input" ~reason:"Reviewed asynchronous input assumption"
  ~source:(Timing.Port.bit (Timing.Port.input "ui_in") 0)
  ~first_stage;

Timing.Exception.async_reset
  ~id:"reset_controls" ~reason:"Explicit external reset-control waiver"
  ~source:(Timing.Port.input "rst_n") ~targets:reset_stages;
```

Each ID is unique and each reason nonblank. Capture targets exactly one index-zero
data pin. Reset targets enumerate only the intended asynchronous-control pins,
including later stages if appropriate to the declared hardware. The supported reset
subset is externally active-low/reset-to-zero. **This deliberately excepts timing
checks at those asynchronous controls; it is not proof that reset release is safe.**
Downstream data paths and other reset fanout are outside that scope. Review reset
protocol and recovery/removal assumptions before adopting a waiver.

No CDC, bus-coherence or metastability adequacy is inferred from these declarations.

## Exact mapped resolution

The identity-covered manifest `timing_intent` records requested intent separately
from tool results. Generated SDC resolves **all** endpoints before applying any
exceptions. Native full-name equality and exact cardinality checks reject empty,
ambiguous, merged, renamed/optimized-away and unsupported endpoints.

The initial pin table supports `sg13cmos5l_dfrbpq_{1,2}` and
`sg13cmos5l_dfrbp_{1,2}` with noninverting Q, D, CLK and RESET_B. Source/clock/reset
tracing accepts direct wiring or up to eight whitelisted noninverting CMOS5L buffers.
Unknown transforms, constants, inversion, wrong inputs and disconnected nets fail
clearly. Interstage data connections must remain direct. Other cell families and
post-PNR transforms are not qualified by this slice.

Missing resolution never becomes a warning-and-skip waiver. Command-completed audit
events mean `set_false_path` returned; they do not prove exhaustive path effect.

## Inspect retained audits

```sh
hardcaml-asic-flow timing-intent-inspect "$AUDIT_DIRECTORY" --json
```

Public API:

```ocaml
Hardcaml_asic_execution.Timing_intent_evidence.inspect ~directory ()
(* (Evidence_json.t, string) result *)
```

This reads a standalone per-corner audit. For an ordinary coarse run, use the
explicit target kind:

```sh
hardcaml-asic-flow timing-intent-inspect "$RUN" --kind coarse_run --json
```

The corresponding API is
`Hardcaml_asic_execution.Timing_intent_flow.inspect ~run_dir ()`.
Both readers probe no PDK, run no tools and rewrite no evidence. Capabilities
`timing_intent_evidence1`, `timing_intent_evidence2` and `timing_intent_flow1`
advertise the supported boundaries. Named executions use the separate `execution`
kind below; checkpoints are not inspection targets.

Inspection verifies build identity, selected bundle-envelope cross-links, complete
SDC hash, exact build/audit/stage/producer/corner and source hashes. It separates
`requested`, `resolved` (`producer_reported`) and
`applied` (`command_completed`). Recorded process provenance is not cryptographic
producer authentication or proof of which source compiled a constructor.

**Inspection exit codes:**

- **1:** readable audit/companion, but overall coverage remains `unavailable`.
  Inspect status: historical absence and retained failed analyses are not completed
  successful native audits.
- **2:** invalid query, missing/corrupt/inconsistent or unsupported evidence.
- There is currently **no passing coverage outcome**. Do not turn exit 1 into pass.

### Supported native coverage dimensions

New outer audit schema2 uses native profile `opensta-2.7-native-state-v2`:

- **Stored exception scope:** native `write_sdc` serialization matches the exact
  source/target relation set, unrestricted transitions and both min/max scopes.
  Missing/extra/broad/restricted relations and unsupported masking constructs reject.
  Native `current_design` must match the verified manifest top.
- **Diagnostic enumeration:** every group/message/detail returned for all seven
  native categories is retained: missing input/output delays, multiple/no clocks,
  unconstrained endpoints, loops and generated clocks. Fixed category framing binds
  structured data to native printed reports. Empty complete groups mean no findings
  from those algorithms, not that every possible timing-check context was analyzed.
- **Endpoint inventory:** the complete returned native endpoint list, explicitly
  separate from unconstrained diagnostic details and ranked timing reports.

The reader never evaluates retained Tcl. Its qualified native SDC grammar is
**LF-only**; CR/CRLF and continued comments reject rather than receiving guessed
Tcl semantics. `report_checks -unconstrained` is not an unconstrained-only census.
Selected before/after capture witnesses establish bounded suppression only.
Per-check analysis and exhaustive exception effects stay unavailable; ordinary
STA does not establish reset-release safety or metastability adequacy.

The qualified OpenSTA 2.7.0 lacks `report_exceptions`; schema2 uses native serialized
state instead. Schema2 verifies the retained producer member/profile/hash, not
equality to today's renderer. Original audit schema1 retains current SDC suffix
replay and its unavailable-native semantics. The intermediate native profile1 is
explicitly unsupported by the corrected reader: inspect it with its immutable
historical provider, or produce a fresh profile2 attempt. No historical record is
rewritten or upgraded merely by inspecting it.

## Ordinary synthesis integration

New timing-intent bundles contain an identity-covered native coverage producer
declared by the manifest's `timing_native_coverage` field. For a **macro-free coarse synthesis** run,
the provider automatically follows completed synthesis with a separate linked
`OpenROAD.STAPrePNR` Step invocation. It consumes the exact retained synthesis
state/netlist, resolved configuration, unmodified emitted SDC and native producer;
it does not re-elaborate the project or run Floorplan/P&R. The normal flow recipe
and checkpoint stages are unchanged.

The first supported runtime is the qualified LibreLane 3.1.0.dev3 / OpenSTA 2.7.0
container. Without the explicit physical opt-in below, full execution is unsupported;
native/macro combinations also remain outside this timing-evidence route.
The default linked analysis selects only `DEFAULT_CORNER`, explicitly recording the
original configured corner list and effective delta. It does not claim all-corner
coverage. Synthesis/transport ceilings are six workers; STA is serialized and
single-threaded.

`RUN/timing-intent/analysis.json` and its source inventory bind ordinary run/build,
synthesis producer/state, analysis attempt, actual process/cleanup/version, runtime
image, ordered loaded libraries, configuration, SDC, producer and reports. A hook
completion marker alone is insufficient. Source inspection requires consistent
historical execution paths while permitting verified relocation for restoration.

```sh
hardcaml-asic-flow run "$BUNDLE" --stage synthesis --toolchain "$TOOLS" --json
# Use the exact RUN returned by the command.
hardcaml-asic-flow timing-intent-inspect "$RUN" --kind coarse_run --json
hardcaml-asic-flow collect "$RUN" --stdout --json
hardcaml-asic-flow report "$RUN" --json
hardcaml-asic-flow archive "$RUN" --output "$NEW_ARCHIVE_DIRECTORY" --json
```

Collection/report include a `timing_intent` section, and archives retain its verified
source closure. Missing advertised evidence or corrupt sources cannot downgrade to
historical absence. Collection can exit 0 with explicit collected errors; report
and inspection enforce their own validity rules. A valid ordinary **synthesis**
report may exit 0 while timing inspection exits 1/unavailable: neither proves timing
closure. Use `collect --stdout` for read-only collection of historical evidence.

Archives replay with the original PDK/source/provider checkout hidden, using retained
bytes and a compatible installed reader. See [archive restoration](flow.md#archiving-and-restoring-evidence).

## Opt-in all-corner synthesis and coarse physical qualification

`Bundle.render ~timing_physical_qualification:true` explicitly emits an
identity-covered physical-v2 resolution policy and all-configured analysis request.
Use a resolved Synthesis project for the pre-PNR baseline and a resolved Hardening
project for full execution. Default rendering, including default Hardening rendering,
does not silently enable this contract.

Physical-v2 preserves direct architectural declarations while accepting up to eight
checked noninverting buffer/delay hops on supported physical connections, including
interstage data and moved anchors. Supported exact cells/pins are qualified across
the three cell libraries; the resolver verifies the entire sequential inventory
equals the declared stages. Unknown logic, extra registers, merged stages,
inversion, cycles or wrong roots reject before applying exceptions. Witnesses use
the same checked endpoint map. No blanket Q-net protection obstructs hold repair.

```sh
hardcaml-asic-flow run "$SYNTH_BUNDLE" --stage synthesis --toolchain "$TOOLS" --json
hardcaml-asic-flow run "$HARDENING_BUNDLE" --stage full --toolchain "$TOOLS" --json
# Use the exact completed full RUN. Run normal external checks before acceptance.
hardcaml-asic-flow postcheck "$RUN" --toolchain "$TOOLS" \
  --testbench "$TESTBENCH" --testbench-top "$TB_TOP" --timeout 1800 --json
hardcaml-asic-flow timing-intent-inspect "$RUN" --kind coarse_run --json
hardcaml-asic-flow collect "$RUN" --stdout --json
hardcaml-asic-flow report "$RUN" --json
hardcaml-asic-flow archive "$RUN" --output "$NEW_ARCHIVE_DIRECTORY" --json
```

The supported physical route is fresh, macro-free **coarse full** under the
qualified runtime. Named physical/restart and arbitrary macro profiles remain
unsupported. Configured/requested/executed corners are distinct. Physical audit3
identifies `post_pnr` / extracted scope, consumes the original STAPostPNR **input
State**, and verifies the routed netlist, RCX/SPEF/ODB chain, actual loaded libraries,
annotation filter/input, native serialized propagated clock and enclosing processes.
It is a separately identified linked analysis, not the original in-flow STA process.

The separate all-corner timing goal requires actual per-corner valid-unit native
log/State results, nonnegative setup/hold and zero required violation counts. Missing
or malformed metrics cannot pass. Native stored-scope/diagnostic coverage remains
distinct: inspection still exits1 for unavailable exhaustive exception effects,
even when the **separate timing-closure goal passes**. Normal full report additionally
requires physical/electrical/external acceptance; a successful process is insufficient.

Macro-free final replay uses the reviewed used-cell zero-delay functional view,
retaining unchanged vendor/UDP inputs and derivation provenance. It establishes
digital behavior, not timed-model conformance or metastability adequacy.
Public archives retain all advertised physical reports and native source closure.

The accepted first full run closes setup/hold at fast/typical/slow **cell-PVT corners
using one nominal extracted RC view**. Five flops remain; native post-CTS repair
added nine delay cells. Worst hold is +0.102353 / +0.276686 / +0.590927 ns, with
unchanged 20 ns clock, 0.25 ns uncertainty, derates and I/O budgets. Physical checks,
nine TT checks, 512 functional observations and hidden-original-source archive replay
pass.

The executed six-worker allocation was respected. Dedicated all-corner/linked STA
used one thread; three repeated mid-PNR OpenROAD STA occurrences requested six.
A tested forward fix sets those dedicated aliases to one thread while retaining
six for physical repair; it was not executed in the accepted run. Historical thread
records and provider identities are preserved, not overwritten by the correction.

## Named synthesis integration

Fresh macro-free named **synthesis-only** plans run required linked STA after
publishing the exact synthesis checkpoint. The expectation is persisted before
preflight/input validation; Runner durably retains the checkpoint before analysis.
Failure/cancellation stops execution while preserving the promoted checkpoint and
primary/secondary diagnostics. Analysis consumes published State without changing
checkpoint bytes or rerendering the bundle.

```sh
hardcaml-asic-flow stages "$BUNDLE" --methodology-plan "$PLAN" \
  --expected-plan-identity "$PLAN_ID" --support-tools "$TT" \
  --pdk-root "$PDK" --python "$PYTHON" --workspace "$WORKSPACE" \
  --checkpoint-store "$WORKSPACE/checkpoints" --timeout 600 --json
# Use the exact execution_file returned above, never the latest directory.
hardcaml-asic-flow timing-intent-inspect "$EXECUTION_JSON" --kind execution --json
hardcaml-asic-flow report "$EXECUTION_JSON" --json
```

APIs: `Timing_intent_execution.inspect ~execution ()` and
`Timing_intent_execution.source_files ~execution ()` in `Hardcaml_asic_execution`.
Capability: `timing_intent_execution1`. Report/inspection share a checked snapshot
and timing value. Valid inspection remains exit1/unavailable; report-query success
is not timing closure. Missing required or contradictory evidence rejects; legacy
absence and pre-analysis failure are explicit. Named physical endpoints, restart
and macro combinations are not qualified by this slice.

Sources bind frozen manifest members/input copies/external pins, exact methodology/
attempt/occurrence, execute transport, publication and native producer position.
The complete conservative checkpoint closure is retained: named outer limits are
4096 sources, 128 MiB/source, 512 MiB total; coarse/inner-native limits are unchanged.
Declared external aliases have explicit path/target/root/hash/size proofs; retargets
reject even with identical bytes. Metadata/native evidence remain strict regular
files. Hashing is bounded and streaming; aliases retain separate accounting.

**Named evidence remains path-bound.** The source inventory does not establish named
archive/export or relocated runnable checkpoints. Readers recheck recorded paths,
without discovering a replacement PDK.

## Accepted boundary

A synthesis qualification demonstrates actual
Yosys mapping and OpenSTA consumption of unmodified bundle SDC for scalar and bit
sources. Five original stages survive. Three downstream setup/hold path samples
remain timed, with setup **+19.432287 ns** and hold **−0.044244 ns (violated)**.
This is sampled timing preservation, not closure. Graph and provenance negatives
have passing positive controls and retained intended diagnostics.

A native coarse qualification adds actual ordinary-flow production, native stored-state/diagnostic interpretation and public archive/restore. The five
original flops, six stored relations, seven categories and 12 endpoints are explicit.
Downstream hold remains −0.044244 ns; other native min samples reach −0.243063 ns.

Two real named executions established unchanged checkpoints and exact report/inspection parity. Each retains 1584 sources and 1062 alias proofs; readers enforce frozen external pins.

The separate coarse physical follow-up above now qualifies this exact example at
the three supported cell-PVT corners and nominal RC view; original negative
synthesis/named reports remain historical failures, not relabelled physical results.

Exhaustive effects/per-check coverage, named archives/physical execution, other RC
views/designs, reset-effect/adequacy qualification and consumer-specific review remain outside the qualified scope.
No historical SRAM run proves these new declarations were applied.
