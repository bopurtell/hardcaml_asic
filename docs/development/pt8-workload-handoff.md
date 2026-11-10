# PT.8 A: frozen workload/result wire and API

Public entry: `Hardcaml_asic_execution.Workload_result.inspect : string ->
(Evidence_json.t, string) result`. Input is an explicit result JSON file.
`snapshot` has the same signature; `recheck : string -> expected_digest:string ->
(unit,string) result` replays and compares `source_digest`. No execution/publication.
B can use `inspect`/`recheck`; no shared declaration edits requested initially.

## Wire v1 (retained input objects exact; JSON integers, not floats)

Paths in references are relative to the result directory, no dot/empty/parent
components, symlinks or absolute paths. Each reference is exactly
`{"path":"...","sha256":"<lowercase hex>","bytes":123}`. The explicitly selected
bundle path is absolute and may be a published root link. All other reads use
Retained_file. Hashes cover raw bytes, not decoded/re-encoded JSON.

Result object, exactly:

```
{"contract":"hardcaml-asic.workload-result","schema_version":1,
 "bundle":"/absolute/bundle", "workload":WORKLOAD,
 "simulation":REF, "stimulus":REF, "trace":REF,
 "counters":{"cycles":N,"reads":N,"writes":N,"defined_observations":N,
             "read_latency":1,"operations":N}}
```

WORKLOAD, exactly (identity is Identity_json.identity of the object without identity):

```
{"identity":"...","protocol":"memory-1rw-latency1-v1",
 "seed":0,"depth":256,"width":16,
 "window":{"start":0,"stop":N},
 "operations":[{"enable":true,"write":false,"address":0,"data":0},...],
 "expected":[{"before":null,"after":123},...]}
```

One operation per cycle, cycle indices 0..N-1. Window is nonempty half-open and
inside operations. Seed is an unsigned 32-bit declaration included in identity;
this protocol does not claim to regenerate operations from a PRNG seed. Expected
is independently checked by the provider's memory oracle, not producer authority.
Memory begins undefined; no reset/initialization. Before is the previous output;
enabled read samples pre-edge memory and produces after-edge data (one edge
latency). Enabled write updates memory and makes after output unspecified;
disabled operations hold the previous output (including undefined). Null expected
means undefined; no claim about actual output is made then. Defined expected
values MUST equal the oracle, including exact null/defined positions.

Stimulus file is exactly `{"operations":[OP,...]}` matching workload operations.
Trace file is exactly `{"rows":[{"cycle":0,"input":OP,"before":null,"after":123},...]}`.
Observed before/after are null or 0..65535; defined oracle output mismatches are
readable functional failures (including a completed simulator process), never
performance evidence. Wrong logical expected values are malformed evidence.
Undefined observations may contain any in-range value or null. Rows start at zero,
are contiguous, and cover at most the operation count. No ignored trace suffix.

Simulation file, exactly:

```
{"contract":"hardcaml-asic.workload-simulation","schema_version":1,
 "attempt_id":"unique-producer-attempt", "status":"completed",
 "bundle_identity":"...", "hardware_variant_identity":"...",
 "workload_identity":"...", "stimulus":REF,"trace":REF,
 "rtl_mode":"implementation", "artifacts":[
   {"role":"rtl","source":REF}, {"role":"model","source":REF},
   {"role":"testbench","source":REF}, {"role":"tool","source":REF}],
 "adapter":"memory-1rw-latency1-v1", "clock":"posedge-single-clock-v1",
 "model":"producer-specific-versioned-model-definition",
 "measurement":"memory-1rw-window-v1"}
```

Allowed status: completed/failed/interrupted/partial. rtl_mode:
implementation/behavioral; RTL role hashes must exactly cover the selected bundle
synthesis_sources/simulation_sources respectively. All four artifact roles required;
multiple RTL/model artifacts allowed. Tool is a retained JSON/text settings/version
artifact; no claim of authenticated tool execution. The model string describes
functional semantics, not concrete model bytes (those remain artifact refs).
Present hardware variant is required and verified against the bundle; no null or
historical inference. Simulation attempt is nonempty <=128 ASCII identifier chars.
Simulation record references must exactly match result stimulus/trace refs.

Completed requires all rows and full measurement window. Genuine noncompleted
records may retain a valid prefix; counters still describe observed window rows.
Contradictory logical expected/input/counter evidence is rejected regardless of
status. Noncompleted results never expose performance metric values. A wrong
observed output remains a readable functional failure rather than an IO/schema
failure; process completion does not assert functional success.

Counters: cycles = retained rows in window; reads/writes = enabled read/write rows;
operations = reads+writes; defined_observations = defined before plus after samples
in window; read_latency = 1 if at least one defined enabled read occurs in window,
otherwise null. All six fields required, values compared against trace replay.

## Projection for B

Projection fields: `contract`, `schema_version`, `source_digest`, `binding`,
`controls`, `provenance`, `outcome`, `metrics`.
binding: `bundle`, `bundle_identity`, `hardware_variant_identity`, `workload_identity`,
`simulation_attempt_id`, `simulation_sha256`, `rtl_mode`.
`binding.bundle` is an additive inspection-projection locator: the absolute
canonical selected root passed to the existing bundle verifier. It lets consumers
capture that exact verified root without asking users to re-enter its path.
`bundle_identity` remains the exact verified identity. The locator is descriptive,
not a new identity or authenticated-execution proof. It is covered by the current
projection/source digest; old inspection digests are not silently relabelled.
Retained result/simulation schemas and bytes are unchanged. Consumers of older
cached/synthetic projections without this additive field should keep their
explicit-locator fallback rather than treating omission as malformed evidence.
controls: `workload`, `adapter`, `clock`, `model`, `measurement`, `rtl_mode`,
`tool` (list of retained tool hashes). Concrete RTL/model/stimulus hashes belong
to provenance, not logical workload equality. B owns metric-family policy.
Each metric: `metric_id`, `name`, `value`, `quantity`, `classification`, `unit`,
`stage`, `definition`, `scope`, `controls`, `sources`; throughput additionally
`numerator`, `denominator`. IDs `workload/{cycles,reads,writes,operations,
defined_observations,read_latency,throughput}`. Stage `functional_simulation`;
classification `derived`, quantity integer except throughput real. Unavailable
values null; complete pass required for all performance values. Firmware/fetch
metrics are absent (unsupported), not zero. `outcome` reports recorded status,
functional pass/unavailable and performance available/unavailable separately.
`provenance.sources` lists every admitted raw source plus verified bundle closure.
Digest covers projection before adding source_digest. Recheck repeats full replay.

Limits: result/workload/simulation/stimulus/trace each <=1MiB, 1..4096 operations,
256x16 only, <=32 artifacts, <=8MiB/artifact, <=32MiB combined workload sources,
<=1MiB projection; member paths <=512 bytes; bundle service retains its existing
512-member/64MiB bounds.
Bounds are failures, never truncation. Hash/replay consistency is not execution
authentication, semantic use proof, timing/SDF conformance or P6 source consistency.

## Implementation / focused verification

Implemented `Workload_result` in the execution library. `inspect` captures/replays
twice and compares the digest of all raw consulted identities and the verified
bundle source closure. `recheck` repeats that operation and requires the caller's
captured digest. No metadata is re-encoded before raw hash checking.

Focused command (switch 5.2.0+ox, serialized, six jobs):

```
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune runtest -j6 --build-dir _build_pt8_workloads test/workload_result
```

Final focused result: 50 public API checks passed. These are synthetic retained evidence
tests, not real simulator acceptance; C owns independently executed RTL evidence.
Includes all addresses, nonzero window start, undefined outputs, disabled writes,
counter-forging with coherent outer rebindings, independent expected oracle,
wrong bundle/variant/workload bindings, stale raw generation, FIFO/symlink/size
admission, strict duplicate/nonfinite JSON, and readable failures/partial records.
Also covers coherently rebound changed stimulus/window, wrong before-edge output,
arbitrary in-range unspecified write output, extra/duplicate trace cycles, missing
artifact roles, symlinked ancestors, escaped duplicate keys and exponent overflow.

First build failed solely on an unused helper warning; helper removed and focused
suite passed. No changes to comparison/bin/shared declarations were needed by A.

Review boundaries: unique attempt IDs are retained producer declarations, not a
global uniqueness/authentication service. The wire binds simulation to bundle and
trace, not an ASIC execution checkpoint; B must not infer one from a shared bundle.
The bundle service's existing verification/admission remains its own boundary;
new workload sources use Retained_file directly. Whole-generation replay detects
changed retained evidence, not an authenticated execution or adversarial ABA
history that restores identical bytes between observations. Seed is declarative,
not a pseudo-random generator oracle. The model control string names supported
functional semantics; concrete model bytes and RTL mode remain separately visible.
