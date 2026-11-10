# PT.8 independent comparison cross-review (A reviewing B)

2026-11-05. Review performed after B/C's initial passing suites. **F1 is independently
verified corrected; F2 and the alias correction are now independently verified
corrected at the final SHA recorded below. No remaining established blocker.** B production
and tests were not edited. No EDA, Git mutations, Protemu or Workbench inspection.

## Scope and method

Read `git diff -- execution/results/variant_comparison.ml
execution/results/variant_comparison.mli bin docs/guides`, the resulting policy and
attachment/fence code, B's test coverage, and C's
`hardcaml_asic_example/docs/pt8_workloads_final.md` solely for comparison assumptions.
No independent-example implementation/evidence investigation was repeated.

New isolated probes: `test/workload_comparison_review/{dune,probe.ml,review.py,fence.c}`.
They call public `Variant_comparison.inspect`, `compare_metric`, and
`Workload_result.inspect`; use synthetic disposable retained fixtures; do not
invoke simulators/tools from queries. B's existing fixture constructor is imported
without modifying it. The LD_PRELOAD hook only synchronizes read boundaries;
the Python parent modifies disposable fixtures. There are no sleeps.

Reproduction (ASIC root, switch5.2.0+ox, six jobs, shared lock):

```sh
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune runtest -j6 --build-dir _build_pt8_workloads test/workload_comparison_review
```

Result: **38 public API checks completed**, including two successful reproductions
of the open finding. First review build/run passed; no review-harness failures.
The executable rule prints JSON receipts, including captured/current source hashes.
Dune can cache a passing rule; to rerun after no source changes use the same locked
command with `--force` (still explicit `-j6`).

## F1 — medium, now corrected: final workload replay can invalidate an already checked generation

The following reproduction records describe the original failing implementation
and are preserved unchanged. Current correction verification follows at the end.

Locations: `execution/results/variant_comparison.ml:338–344`;
`variant_comparison.mli:7–10`; guide's final generation-fencing paragraph.

The final sequence is:

1. Recapture both target closures and compare their original generation digests.
2. Run the complete left workload `recheck`.
3. Run the complete right workload `recheck`.
4. Return the already constructed response without rechecking target metadata or
   the earlier workload's raw source closure.

The new workload replay phase opens a substantial cross-domain gap after the last
target check, and the later workload replay opens a gap after the earlier workload
check. The initial suite mutates left evidence at the *first* right capture; it
does not exercise these later phases.

### Deterministic reproduction A: late left workload mutation

`review.py` pauses the seventh descriptor-bound `result.json` open. Opens1/2 are
left capture,3/4 right capture,5/6 left recheck,7/8 right recheck. At open7, the
parent appends one newline to left `result.json`, then releases the reader.
The JSON remains valid; no reference points back to that outer result file.

Observed public result:

- Exit0, all seven workload metrics still `comparable`.
- Returned left result source SHA256:
  `4967922a9d5ed159ae14461c2b380a22e1dbf91228db8f35382d624caeb349eb`.
- Current retained source SHA256:
  `8a78abbbfe8f72d9b4ef997e42524b4d0d589ca33d2848eb98f8985bb4ef1f37`.

The same mutation at open3 is correctly rejected with `stale workload`, proving
the probe reaches a real uncovered timing phase rather than bypassing admission.

### Deterministic reproduction B: target changes during workload validation

Use a valid coarse-run target sharing the workload's verified bundle/hardware.
Pause open5 (start of left workload recheck), append a newline to selected coarse
`run.json`, then release. The workload only binds to the bundle/hardware, so its
replay has no reason to read that coarse-run record again.

Observed public result:

- Exit0 with the original target generation and `record_sha256`.
- Returned record SHA256:
  `ea573db5be9aa61ba12daf58e1d1473d542ca51fb430cb6bbf184fd6eed2d4a9`.
- Current retained record SHA256:
  `a08e4b3fff843ba76f06f6e4d355e7d7367e5b5c6a45adbbc6d71f27b290c138`.

Hashes include disposable absolute paths and will differ on a fresh fixture run;
the invariant is returned/current inequality while the API returns success.

### Impact and recommendation to B

This is a stale-generation/provenance issue, not proof of incorrect trace arithmetic
or source authentication. Neither reproduction uses ABA restoration or changes
bytes after the last right workload check. It contradicts the intended detection
of target/source changes while the opposite closure is being validated.

After expensive semantic replay, perform a bounded final audit of the combined
captured target/workload raw-source closure, including selected target records and
earlier-side workload files. Use descriptor-bound reads and the captured hashes;
do not merely recapture the outer record or move the same expensive sequential
loops into a different order. Add late-phase regression cases for both domains.

No finite sequential audit provides an atomic filesystem snapshot: document that
residual limit honestly. If arbitrary concurrent mutation is outside the intended
contract instead, explicitly require immutable/stable retained inputs and narrow
the current blanket stale-generation claims. This finding concerns changes during
substantial added validation work, not an impossible guarantee against mutations
in the final machine instruction before return.

## Other adversarial results / boundaries

### Metric-family controls — pass

Each workload control (`workload`, `adapter`, `clock`, `model`, `measurement`,
`rtl_mode`, `tool`) was separately changed and then removed/null: differing context
confounds, missing context is unavailable, no delta leaks. Stage, scope, definition,
unit, quantity and classification mismatches also withhold deltas. Unrelated
physical library/RC/environment controls do not incorrectly gate functional metrics.
The public wire fixes the supported adapter/clock/measurement and functional stage;
arbitrary unsupported ones are not legitimized by equal labels in the policy seam.

### Concrete model sets versus semantic model declarations — intentional boundary

A coherently admitted extra concrete model artifact, under the same declared
functional model semantics, still yields comparable workload metrics. Concrete
model bytes and member count are provenance, not equal-set controls. Changing the
semantic `model` control (including a set-shaped value at the pure policy seam)
confounds. This matches the design allowing Flops versus vendor SRAM models.

Guide wording “different functional model definition/model set cannot yield a
delta” should be read as **different declared semantic model context**, not different
concrete artifact sets. Consider making that wording explicit. Trace replay proves
only this supported workload's observed behavior; equal model/adapter declarations
do not authenticate model implementations or prove arbitrary future behavior.
C's final document keeps actual vendor model execution separate from this claim.

### Stage/run association — pass, explicitly limited

A valid coarse run and a separate simulation sharing the verified bundle/hardware
attach successfully, retaining coarse `run_id=fixture` and simulation
`simulation_attempt_id=flops` separately. The projection explicitly says
`verified_bundle_and_hardware_only_not_execution_attempt`. There is no fabricated
coarse-run execution of the workload. C's reported results use bundle targets and
do not assume a matched physical execution. Exact execution-checkpoint association
is not supplied by wirev1 and must not be inferred from these cycle deltas.

### Ratios — pass within the verified-projection seam

Verified production ratios remain visible. Independent policy probes checked
negative reduced deltas, equal unreduced fractions, zero numerator, arbitrary
precision integers, and zero/negative/missing denominators. Exact rational deltas
were correct; invalid denominators withheld deltas. The numeric float delta may
have ordinary binary64 rounding; `right_minus_left_ratio` is the exact result.

`compare_metric` explicitly trusts already verified projections. Synthetic
nonzero-ratio seam probes are arithmetic coverage, not new simulator evidence.
For the currently fixed-cycle logical protocol, complete matching workloads
normally have identical operation/cycle counts; zero deltas are the honest result.

### Whole response bounds — pass

Constructed two separately admitted workload projections below1MiB using long but
valid paths and32 small retained artifacts per side. Combined comparison exceeded
1MiB and returned `comparison whole JSON exceeds 1MiB`, without partial output.
The whole-response check remains after workload attachment and response digesting.
No truncation/pagination bypass was observed.

### CLI/routing/guide inspection

The diff routes both explicit workload paths through the public grammar to the
native helper; helper duplicate/empty argument handling remains strict. Capability
addition is additive, old omitted-path envelope is preserved, and development
build-directory recognition includes the requested isolated build directory.
No additional CLI defect was identified by inspection. Independent probes here
exercise public OCaml services; initial B/C API/CLI parity suites are distinct
evidence and were not represented as new review executions.

## Original review disposition (historical)

F1 is open and returned to the coordinator/B with exact read-boundary reproduction.
No B files were changed. Review probes deliberately assert the current failing
behavior to reproduce it; their passing status is **not an acceptance test** for
the fence. Once B fixes it, change these isolated reproduction expectations to
require rejection and rerun the late-phase cases before closeout.

## Independent verification of B's F1 correction

Current disposition: **F1 closed for its reproduced file-mutation windows; F2 below
blocks claiming complete final generation fencing.** No B production/tests or
original `review.py`/`fence.c` characterization records were changed.

Added `corrected_review.py`: it reads the original review source, verifies exact
replacement anchors, and executes the same scenarios with only the two original
late-window assertions replaced by exit2 expectations. It prints the unchanged
original source SHA256:
`b06e62ccb0975a8f26fe33829d8a735743a2f4fbaceeb428d95899df599c59ad`.
The first wrapper run expected only the explicit generation-error message; actual
safe rejection was `retained source byte bound exceeded` because the captured raw
length is also the final allocation bound. The wrapper was corrected to accept
that rejection; production was not changed. Final result: **38 checks completed**,
including both original open7/open5 cases returning2 with no comparison JSON.

```
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune build -j6 --build-dir _build_pt8_workloads \
  @test/workload_comparison_review/corrected_review \
  test/variant_comparison/consumer.exe bin/flow_cli.exe bin/experimental_compare.exe
```

### Fence inspection and bounds

The correction inventories raw source hashes, records optional-file absence,
includes selected records, consulted report metadata, checkpoint inventories,
external collateral and workload artifacts, and separately resolves the workload's
own selected bundle (which need not be the comparison target's retained copy).
Its final raw audit follows all expensive semantic replays; no subsequent deep
parsing/replay follows that audit. Root and permitted alias resolutions are checked.

New audit reads use `Retained_file`, including regular-file/nonblocking/no-follow
admission and generation checks. Workload sources use captured byte-length bounds;
workload bundle members retain32MiB bounds. Target inventory defaults to128MiB per
source,8192 entries/1GiB per target inventory; final union is limited to16384
paths/2GiB. Existing deep-reader limits still apply before these additional reads.
Sequential audit still cannot provide an atomic snapshot or detect restored ABA
history. No separate descriptor admission defect was found here.

### Current B focused suites — clean positives and negatives

```
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune runtest -j6 --force --build-dir _build_pt8_workloads \
  test/workload_comparison test/variant_comparison
```

Passed: workload API/CLI49, B late-final-fence24, variant96, and corrected
admission/generation/archive43. The24 include same-size raw changes, all workload
artifact roles, both retained bundle copies, selected stage records/checkpoint
members and external host collateral. These successes do not cover F2.

### Historical symlink/PDK and physical/named replay — pass

Ran the unchanged `test/variant_comparison/retained.py` with the freshly built
consumer/CLI, original exact named execution paths, and actual retained physical
and synthesis runs from the prior handoffs. No relocation, recapture or EDA.

```
flock /tmp/opencode/asic-pt7-build.lock python3 -B test/variant_comparison/retained.py \
  --consumer _build_pt8_workloads/default/test/variant_comparison/consumer.exe \
  --cli _build_pt8_workloads/default/bin/flow_cli.exe \
  --area /home/wayne/devel/jane/hardcaml_asic_example/.dev/asic-pt7named/workspaces/area-run-final-04/execution-cb5e7cd92dbf43b4a6c00a74b61c6d72.json \
  --delay /home/wayne/devel/jane/hardcaml_asic_example/.dev/asic-pt7named/workspaces/delay-run-final-03/execution-9374823306a64897959e1b2295f52c84.json \
  --synthesis /home/wayne/devel/jane/hardcaml_asic_example/.dev/asic-timing-physical/synthesis-runs/20261029T070532999179Z_4821044b \
  --physical /home/wayne/devel/jane/hardcaml_asic_example/.dev/asic-timing-physical/full-runs/20261029T075052976305Z_23a6954b
```

Result: passed API/CLI parity, **1860 files preserved** (hash/size/mtime),2 named
metrics,18 comparable physical-self native zero deltas,18 stage/RC-confounded
synthesis/physical native metrics. Original corrected handoff digests reproduced:

```
7cf3dd62688e5835fcb30437380d280c401243f9a4535803f1801022a0c2d991
755ffb7a8b8dc6caeda8eceda4c432d0957bc2c377c807ecce0ced1857f342e3
0969f1b9450053eda28502bad1a964b5fb60a8e31deac127c6a7f268092e00d7
```

No historical symlink/PDK regression observed for these actual supported targets.
`--check-declared-inputs` was not requested here; the retained corruption-copy
extension was not rerun or claimed as fresh evidence.

## F2 — medium, now corrected: directory-driven postcheck discovery is not fenced

The following original characterization and receipts are historical; the final
independent correction verification follows below.

Locations: `variant_comparison.ml:342–356` captures only currently enumerated
`checks/*/postcheck.json` paths, not `checks/` existence or relevant membership;
`final_raw_fence` audits only the resulting file inventory.
`Collect_service.collect` at `collect_service.ml:206–227` uses that directory
discovery to determine reported postchecks.

Independent probe: `test/workload_comparison_review/discovery_review.py`, new and
separate from original F1 records. It pauses at original open5, after final target
recapture but while left workload rechecking begins. The parent creates
`checks/new-failed-postcheck/postcheck.json` containing a genuine failed-check
record, then releases the query. It runs both with `checks/` initially absent and
with an initially empty directory.

```
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune build -j6 --build-dir _build_pt8_workloads \
  @test/workload_comparison_review/discovery_review
```

Both cases reproduce **exit0 with `postchecks: []`**, while an immediate clean
query returns the new failed postcheck and a different comparison digest:

```
returned: eb43c22e4187c884f7290e5d20cc45928c0a453b804cebedbf96e47d81338414
current:  7ad2bda7d80e5a8a94550cf1af034e727e72980fa29644d2a6675a8962a09446
```

Digests depend on disposable absolute paths. The invariant is stale reported
postcheck membership and unequal digests, not those particular values. No
previously captured file bytes change, so final hash auditing alone cannot detect
the mutation. This is not ABA or a mutation after the final audit; it occurs at
the same substantial late-validation window that F1 corrected.

**Required correction:** capture and fence discovery decisions as part of the
generation: directory absence/presence and relevant child membership, including
postcheck-file presence below selected child directories. Recheck these after
deep replay in the final bounded audit without another late semantic collection.
Use bounded directory iteration rather than allocating an unbounded `Sys.readdir`
array before checking count. Review other discovery-driven closure decisions for
the same omission. Preserve existing selected-root/alias policies and retained
historical positives.

F2 is the sole remaining independently established comparison blocker in this
review. The reproduction alias characterizes the current defect; a passing alias
does not close it. B must correct it and the reviewer must rerun with rejection
expectations before claiming complete final generation fencing. No B source was
edited by this reviewer.

## Final independent F2/discovery + alias verification — closed

Verified source SHA256 before and after testing:

```
348ed85a31dd82e4c6ec748cafae358f4cc5080b157e35de2fc13131317d1a29  execution/results/variant_comparison.ml
```

**Disposition: F1 and F2 closed for the supported retained-evidence scope. No
remaining independently established comparison blocker.** This is independent
verification of B's correction, not a production change by the reviewer.

### Preserved original evidence, new corrected expectations

Original `review.py` and `discovery_review.py` were not edited; hashes remain:

```
b06e62ccb0975a8f26fe33829d8a735743a2f4fbaceeb428d95899df599c59ad  review.py
0defed1a3a90053b99959cfaa6ae8cb323f1b0bff20da007dbdcdd22ac8aa7c5  discovery_review.py
```

New `corrected_discovery.py` executes the original F2 source with exit2 assertions
in memory, retaining the original mutation/read boundary. It adds the third case
of an existing child whose postcheck file is initially absent. All three cases
now reject at open5 with `comparison final postcheck discovery generation changed`:

1. Initially absent `checks/`, new child and postcheck created late.
2. Initially empty `checks/`, new child and postcheck created late.
3. Existing `checks/new-failed-postcheck/`, missing postcheck file created late.

After each rejection, a clean query succeeds and reports the new failed postcheck.
No comparison JSON escapes the rejected query. The corrected F1 wrapper also
reran and passed38 checks against this final source.

### Independent alias and boundedness probes

New `alias_review.py` produced eight named receipts:

- Stable internal **flow alias** admitted; workload metrics remain comparable.
- Late flow-alias retarget to a separate copy with **identical bytes** rejects2:
  `comparison final raw source alias changed`.
- Stable **checks-directory alias** admitted.
- Late checks alias retarget with identical empty membership rejects2:
  `comparison final postcheck discovery generation changed`.
- A FIFO at the checks root returns promptly with inherited no-postchecks behavior;
  no writer is needed and no FIFO stream is opened.
- A child FIFO rejects promptly with `Not a directory`; it is not opened/read as
  a stream. The first harness invocation expected admission here and failed on
  that safe rejection; the harness expectation was corrected, production unchanged.
- **4096** existing child directories without postcheck files: clean query passes.
- **8193** child directories: exit2, `comparison check directory count exceeds 8192`.

Code review confirms `discover_postchecks` at lines178–192 uses protected
`Unix.opendir`/`readdir`, counts non-dot entries as it iterates, and stops at the
8193rd entry rather than first allocating an unbounded array. Handles close on
success and exceptions. Child probes are metadata existence checks. Directory
absence/presence, canonical resolution, names and per-child postcheck presence are
captured before reporting, checked after capture, and checked both before and
after the final raw audit. The final phase does no new semantic report collection.
No new unchecked FIFO blocking or unlimited directory enumeration was found in
the correction. The existing raw file/count/aggregate audit bounds remain active.

These local Linux probes establish nonblocking special-file behavior for the
supported environment, not an absolute deadline for arbitrary filesystem IO.
The documented sequential-snapshot/ABA limit remains unchanged.

### Forced final commands and results

```
flock /tmp/opencode/asic-pt7-build.lock opam exec --switch=5.2.0+ox -- \
  dune build -j6 --force --build-dir _build_pt8_workloads \
  @test/workload_comparison_review/corrected_review \
  @test/workload_comparison_review/corrected_discovery \
  @test/workload_comparison_review/alias_review \
  @test/workload_comparison/runtest @test/variant_comparison/runtest
```

All passed: independent F1 wrapper38, independent F2 cases3, independent alias/bound
receipts8; B workload API/CLI49, extended late-final-fence39, variant96 and corrected
admission/generation/archive43. B's extended39 additionally cover discovery-directory
creation/removal and alias retargets. Original characterization aliases remain
historical reproduction code; the new corrected aliases are the acceptance calls.

Rebuilt the retained consumer/CLI for this source and reran the **exact unchanged
historical `retained.py` invocation above** under the shared ASIC lock. It passed
again: API/CLI parity,1860 files preserved,2 named metrics,18 comparable physical-self
native zero deltas,18 cross-stage-confounded native metrics; all three historical
comparison digests reproduced exactly. No symlink/PDK/named compatibility blocker
was observed for the actual original supported targets. No EDA or Git mutation.
