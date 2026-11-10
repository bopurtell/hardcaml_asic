# PT.8 independent cross-review: A Workload_result

2026-11-05. Reviewer: B. Review target: `execution/results/workload_result.ml`
and `.mli`, the FROZEN `pt8-workload-handoff.md`, and the source-admission and
bundle-verification code they invoke. This review adds only
`test/workload_result_review/` and this report. A's production/tests and frozen
handoff were not edited. No EDA, simulator, provider changes or Git mutations.
Independent example review belongs to the coordinator.

## Decision

**R1 is independently verified corrected and closed (2026-11-05 follow-up).**
The unchanged original probe now measures zero descriptor growth, and A's durable
same-process regression also passes. The original medium-severity shared-helper
leak and its original receipt are retained below as review history.

The adversarial evidence probes did not establish a wrong-association, forged
counter, memory-oracle, window, latency, strict-JSON or persistent-generation
bypass. Several intentional admissions expose important source/semantic boundaries
described below; they are not execution-authentication findings.

## Reproduce

From `/home/wayne/devel/jane/hardcaml_asic`:

```sh
flock /tmp/opencode/asic-pt7-build.lock \
  opam exec --switch=5.2.0+ox -- \
  dune runtest -j6 --build-dir _build_pt8_workloads test/workload_result_review
```

For an explicit replay even when Dune's alias result is cached:

```sh
flock /tmp/opencode/asic-pt7-build.lock \
  opam exec --switch=5.2.0+ox -- \
  dune runtest -j6 --force --build-dir _build_pt8_workloads test/workload_result_review
```

The isolated executable calls only public `Workload_result.inspect`/`recheck` and
the hardware-variant constructor. Python independently constructs the memory
oracle, expected trace and counters; it imports none of A's fixtures/tests.
All mutations affect a temporary synthetic bundle and evidence files. Queries run
with `PATH=/no-tools`. The C preload helper supplies pipe rendezvous at specific
`openat` calls; race checks use no sleep/timing assumptions. Compilation uses one
ordinary C compiler process under Dune's six-job ceiling.

The script prints named per-probe JSON receipts, including descriptor counts.
The original pre-correction run completed **92 named probes**; all expected admissions,
rejections and outcome assertions matched, and the R1 characterization measured
64 leaked descriptors. This is independent adversarial coverage, not a rerun of
A's50 initial tests.
The directory-leak probe is a **characterization**, so a completed review suite
does not imply the finding is fixed. Its receipt must have `growth: 0` after the
correction. Other assertions reject incorrect admissions/outcomes.

## R1 — directory rejection leaks the admitted descriptor (medium; closed)

**Location:** `execution/results/retained_file.ml:6-8`, reached through
`workload_result.ml:49-57`. The utility predates this review; the finding is in a
shared dependency used by A, not a claim that A introduced that utility.

Original vulnerable ordering:

```ocaml
let fd = open_readonly path in
let ic = Unix.in_channel_of_descr fd in
Fun.protect ~finally:(fun () -> close_in_noerr ic) ...
```

In the original implementation, `open_readonly` admitted a directory descriptor.
On this OCaml/Linux build,
`Unix.in_channel_of_descr` raises `Invalid_argument` for that descriptor **before**
`Fun.protect` is installed. `Workload_result.guard` converts the exception to
`Error`, but the descriptor remains open. Neither the subsequent regular-file
check nor channel cleanup executes.

### Concrete public-API probe and result

1. Construct valid workload evidence; verify the positive baseline.
2. Replace its referenced `stimulus.json` regular file with a directory.
3. Call `Workload_result.inspect result_path` 64 times **in the same process**.
4. Count `/proc/self/fd` before and after.

Receipt `directory_rejection_descriptor_growth`:

```json
{"before":5,"after":69,"errors":64,"growth":64}
```

The ordinary single-query receipt is exit2, `in_channel_of_descr : Invalid argument`.
There is one leaked descriptor per rejected inspection; this is not inferred from
runtime or stale counters. The subprocess exits afterward, keeping the review's
host resource use bounded.

### Exact correction request

Coordinate ownership of the shared `Retained_file` helper, then:

1. Ensure the raw descriptor is closed if `Unix.in_channel_of_descr` raises.
   Install cleanup immediately after opening, or explicitly close/re-raise on
   failed channel conversion. Transfer ownership to channel cleanup only after
   successful conversion; avoid double-close.
2. Preserve component-wise `O_NOFOLLOW`, nonblocking open, regular-file checks,
   bounds, descriptor/named-file identity checks and generation replay.
3. Add a durable same-process rejection regression: inspect an explicit directory
   result and a valid result referencing a directory repeatedly, require `Error`
   every time and **zero descriptor growth**. Checking only exit status misses R1.
4. Rerun this review and A's workload-result suite, plus existing corrected
   comparison admission/generation checks because the helper is shared.

No workload wire/schema change, counter relaxation or broader bundle-reader
rewrite is needed to correct R1.

### Independent correction verification — closed

Read A's corrected `retained_file.ml:6-13`: failed channel conversion now closes
the raw descriptor and re-raises the original exception with its backtrace.
Successful conversion transfers cleanup to the existing channel `Fun.protect`;
there is no additional close on the successful path. The component-wise native
open, no-follow/nonblocking flags, file-type/size checks and post-read identity
checks remain in place. B did not edit A production or tests. The original
`test/workload_result_review/{probes.py,review_api.ml,fence.c,dune}` were unchanged
for this verification.

Actual independent command, forced to execute rather than reuse cached results:

```sh
flock /tmp/opencode/asic-pt7-build.lock \
  opam exec --switch=5.2.0+ox -- \
  dune build -j6 --force --build-dir _build_pt8_workloads \
  @test/workload_result_review/runtest \
  @test/workload_result/fd_regression @test/workload_result/runtest
```

Observed results:

- **92 unchanged independent review probes completed.** The original
  `directory_rejection_descriptor_growth` receipt is now:

  ```json
  {"before":5,"after":5,"errors":64,"growth":0}
  ```

- **640 durable same-process directory errors, zero descriptor growth.** Each
  of the five cases reports before5/after5,128 errors, growth0: explicit directory
  result, directory stimulus, directory trace, directory simulation and directory
  model artifact. The executable interleaves a valid inspection every16 errors,
  checking successful channel ownership as well as rejection cleanup, without
  relying on GC or process exit.
- **A's50 workload-result checks passed.** Original oracle, association,
  strict-admission and deterministic generation checks in the independent suite
  also retain their expected results.

Reviewed correction/probe SHA256 values:

```text
6f69c0bea1f987f0c48c908264d3ad195db5f49a07cd624ec32e5a0369729fcc  execution/results/retained_file.ml
f7ed3931a6397069b23e1f64a0692449d8b27050620e2cb830350ddf0e29aa12  test/workload_result_review/probes.py
0af3f72e5595bcdf2eae80cd76d22ec06ed5da9cab4271691cb8b2be6f4d0b71  test/workload_result_review/review_api.ml
8faef96941ffe63675272465765aa2f38b8fdbd91fa72b7ea2098f7952dc63c2  test/workload_result_review/fence.c
c181ba0b24aa232e5a30746d5a8b383afc4ed7647cd1100b465ffa6fa9afdaa4  test/workload_result/fd_regression.ml
06957c23fba09b01a1e5a8293b7c287a3bc7d4df34414bf409dad0574097d94a  _build_pt8_workloads/default/test/workload_result_review/review_api.exe
```

**Disposition: R1 closed on independent actual zero-growth evidence**, not merely
on a passing characterization rule. D1/D2 remain the admission/reporting boundaries
described below; the separate B compatibility concern in the final section does
not reopen A's descriptor-leak finding.

## Evidence-policy and replay results

The baseline uses addresses0/127/128/255, distinct data, read-after-write,
consecutive output transitions, disabled writes, a final hold and undefined
initial/write outputs. Its measurement window is `[2,14)` within15 operations.
Verified baseline: cycles12, reads8, writes3, operations11,
defined-observations17, latency1, throughput numerator11/denominator12.

| Probe family | Mutation / independently checked result |
| --- | --- |
| Exact bundle, hardware, workload association | Each simulation identity replaced with64 zeroes and outer simulation/result references freshly rebound: exit2 association/workload mismatch. |
| RTL selection | `behavioral` mode with unchanged implementation RTL: exit2. Supplying exactly the distinct declared behavioral bytes: admitted, mode remains visible. |
| Artifact inventory | Missing required roles, duplicate RTL entries and36 artifact entries rejected. |
| False counters over unchanged trace | Rebound each of cycles/reads/writes/operations/defined-observations/read-latency separately: all rejected by replay, not stale-reference rejection. |
| False expected observations | Change expected read output and recompute workload identity plus simulation/result refs, leave trace unchanged: exit2 independent-oracle disagreement. |
| Ignored wire parameters | Coherently rebound logical depth128, width8, unsupported protocol and out-of-range seed rejected; unsupported clock/adapter/measurement rejected. |
| True before/after behavior | Wrong defined before/after sample becomes readable functional failure, performance unavailable. One-cycle-delayed outputs produce7 mismatches. |
| Full trace vs measurement window | Wrong defined sample outside window still fails. Completed missing final row rejected even though the missing row is outside the sample window. |
| Undefined observations | Arbitrary in-range unspecified write output admitted, no invented initial/write response. A disabled-write hold corruption fails. |
| Counter window semantics | A one-cycle defined hold yields cycles1/operations0/defined2/latency null. An initial undefined read yields cycles1/operations1/defined0/latency null. |
| Incomplete/process outcomes | failed/partial/interrupted with0,4 and15 rows retain actual status and unavailable performance. Complete retained rows do not override recorded process failure. |
| Missing/extra rows | Missing prefix, duplicate cycle/suffix and incomplete completed trace rejected. |
| Strict raw JSON | Escaped duplicate key, integer-as-float version, NaN, exponent overflow, invalid UTF8, truncated JSON and258 nesting rejected. |
| Byte/count bounds | Result/simulation/stimulus/trace over1MiB, artifact over8MiB, combined sources over32MiB, and0/4097 operations rejected without truncation. |
| Relative member paths | Parent traversal, absolute/dot/empty components, backslash, NUL and513-byte paths rejected. |
| Referenced-file admission | Leaf symlink, symlink ancestor, FIFO and directory rejected. The original directory cleanup defect R1 is independently corrected, with zero growth on follow-up. |
| Actual descriptor binding | After trace descriptor open, replace named file with a different inode having identical bytes: exit2 `changed during read`. |
| Open-time special-file replacement | Replace trace with FIFO immediately before descriptor open: nonblocking rejection, no hang. |
| Captured digest | Coherently replace model bytes and rebind simulation/result: `recheck` with the previous digest rejects stale generation. |
| Whole-generation replay | Stop at second result read, change result/simulation/stimulus/trace/model/tool/testbench with coherent refs: every case rejects `source generation changed`. |
| RTL/bundle generation | Coherently change RTL artifact + bundle member + manifest identity + simulation binding, preserving a valid new generation: replay rejects generation change. Raw bundle-manifest whitespace alone also rejects. |

The generation probes establish detection of persistent replacement during these
specific capture intervals. They do not claim detection of adversarial ABA history
that restores identical bytes before observation.

## Confirmed boundaries and documentation requests

### D1 — no-symlink wording must identify its root boundary

`workload_result.ml:46` canonicalizes the explicitly selected result's **parent**
with `Unix.realpath`; an explicit `selected-parent-link/result.json` is admitted.
Relative references below that canonical root reject both leaf and ancestor
symlinks. Separately, the inherited bundle reader uses `safe`/`Unix.realpath` in
`resource_inspection_service.ml:21-25,80-97`: an internal bundle member symlink to
identical bytes inside the bundle is admitted. This does not bypass the new
relative-member descriptor gate; it is a different existing admission boundary.

**Documentation correction request to A:** make the frozen handoff/closeout
wording explicit: selected result-parent aliases are canonicalized, new relative
workload references are descriptor/no-follow admitted, and bundle closure uses
the existing bundle service's policies. Do not describe *all consulted sources*
as passing `Retained_file`'s no-symlink gate. No inherited-provider rewrite is
requested by this review.

### D2 — semantic use and simulation attempt are declarations, not execution proof

Two deliberate coherent admissions were observed:

- Rebind a valid hardware declaration from depth256 to depth128, retain the same
  synthetic RTL and depth256 trace (including address255), recompute bundle and
  simulation associations: admitted as a functional pass of the **logical trace**.
- Change only a valid simulation-attempt identifier and rebind its raw record:
  admitted with the new declared attempt and a different source digest.

These are within the frozen non-authentication/non-semantic-use boundary. Generic
hardware-variant keys are not a protocol-specific schema-to-RTL oracle, and a
retained trace cannot authenticate which process produced it. The actual RTL in
this review is intentionally synthetic; no simulator executes it. A should not
infer simulator use of hardware parameters, actual process identity or stock
vendor-model execution from these admissions.

**Documentation/closeout request:** retain that distinction wherever reporting a
functional pass or exact identity binding. An adapter's independently executed
ignored-parameter and latency negatives are needed for semantic-use acceptance;
the coordinator's separate example review owns that evidence. No global attempt
registry, arbitrary parameter-name inference or execution-authentication feature
is requested.

## Closure criteria

R1's zero-growth closure criteria are met by the independent follow-up above.
D1 needs precise admission wording; D2 is an acceptance/reporting
boundary, not an additional wire feature. The review otherwise supports the
bounded replay/measurement contract for the exercised evidence, subject to these
limits and the coordinator's independent example review.

## Separate B self-inspection — final-fence alias compatibility concern

This follow-up also inspected B's own new final raw fence, independently of A's
parallel review of B. B production was not changed during this reviewer task.
Reviewed `variant_comparison.ml` SHA256:
`fdb4b6275ed23e8febe0411a00dfc611fbffb50c242553f5b3cd0f2c8603c053`.

### Existing retained selections — readable

With the current build-tree public comparison consumer and `PATH=/no-tools`:

- Actual retained physical-self comparison of
  `hardcaml_asic_example/.dev/asic-timing-physical/full-runs/20261029T075052976305Z_23a6954b`:
  **exit0**,406112 response bytes, empty stderr.
- Actual retained named area/delay comparison of
  `.dev/asic-pt7named/workspaces/area-run-final-04/execution-cb5e7cd92dbf43b4a6c00a74b61c6d72.json`
  versus
  `.dev/asic-pt7named/workspaces/delay-run-final-03/execution-9374823306a64897959e1b2295f52c84.json`:
  **exit0**,112809 response bytes, empty stderr.

These are read-only compatibility samples, not a claim that every admitted alias
layout is covered. No retained source/provider was changed. An initial build
attempt timed out waiting/working under the shared lock; the600s retry completed.

### B-A1 — confirmed auxiliary flow-alias rejection (medium compatibility regression)

`variant_comparison.ml:323-325` resolves admitted resource/timing reference aliases
and the audit pins/rechecks their resolutions. Checkpoint external aliases are
also handled that way. However, the auxiliary collection paths at340-341 use
`add ~optional:true` **without** alias resolution. Resource evidence stores the
canonical member path (`resource_evidence.ml:42-56`), so a declared flow alias can
be enrolled later under a second lexical name and hit the strict no-follow reader.

Isolated reproducible diagnostic (new B file; no original review-probe changes):

```sh
flock /tmp/opencode/asic-pt7-build.lock \
  opam exec --switch=5.2.0+ox -- \
  dune build -j6 --build-dir _build_pt8_workloads \
  test/workload_comparison/consumer.exe

python3 -B test/workload_comparison/alias_compatibility.py \
  --consumer _build_pt8_workloads/default/test/workload_comparison/consumer.exe
```

The diagnostic creates a disposable canonical coarse fixture, verifies comparison,
renames `project/runs/asic` to `asic-real`, and makes `asic` an internal directory
symlink. The record still names the same declared flow; raw source bytes are
unchanged. Results:

| Case | Actual result |
| --- | --- |
| Canonical comparison control | exit0,33509 response bytes |
| Aliased-flow public `Report_service` | exit0,22830 response bytes |
| Aliased-flow comparison with the new inventory | exit2, no response |

Comparison rejection:

```text
retained_open .../project/runs/asic/06-yosys-synthesis/reports/stat.json: Not a directory
```

**Correction request to B/coordinator:** preserve the existing source reader's
admitted auxiliary alias semantics. Resolve and pin an allowed auxiliary alias
to its captured canonical source, verify its bytes, and check that resolution
again in the final audit. Reuse the admitted canonical source where possible;
do not globally relax `Retained_file` or the stricter new workload-relative path
gate. Cover this diagnostic as an acceptance regression, plus alias retargeting
rejection. Audit the analogous auxiliary postcheck/input paths for the same issue.
Keep A's independent final-fence review separate. B-A1 remains open here.
