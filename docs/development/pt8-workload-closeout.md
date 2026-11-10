# PT.8 workload/result closeout

2026-11-05. **Accept the bounded PT.8 library/example scope.** The
[preimplementation plan](pt8-workload-plan.md) and
[wire/API handoff](pt8-workload-handoff.md) define the supported protocol.
`docs/phase_plan.md` remains the only active queue. Protemu adoption, correctness
and application-specific configuration selection remain pending independently.

## Delivered

- Public `Hardcaml_asic_execution.Workload_result.inspect`, `snapshot`, `recheck`:
  strict versioned records, bounded descriptor-safe raw source admission, logical
  workload identity separate from artifacts, checked bundle/hardware and exact
  simulation-attempt association, independent memory oracle and trace replay.
- Existing `Variant_comparison.inspect` accepts optional explicit `left_workload`
  and `right_workload`. Public CLI accepts `--left-workload` / `--right-workload`;
  additive capability `workload_comparison1`. No new experiment runner/scheduler.
- Workload-family eligibility controls and exact signed integer/rational deltas.
  Observed functional failures remain readable; failed/partial/interrupted sources
  do not expose complete-performance values. A bundle-bound simulation is not
  falsely associated with a synthesis/physical run attempt.
- Fresh example Flops and supported Exact SRAM implementation RTL executed under
  Icarus with the same logical stimulus; Exact executes both unchanged stock vendor
  functional models. Reused historical physical evidence is never rewritten.

The initial protocol is explicitly **256×16 latency-one single-port memory**, not
a universal firmware/execution oracle. Seed is a retained declaration, not PRNG
regeneration authority. Firmware size/fetches are unsupported here. Logical-cycle
throughput is not host simulation speed, clock-projected MHz or measured Fmax.

## Accepted workload tradeoff

| Measurement | Flops | Exact SRAM |
| --- | ---: | ---: |
| Sample cycles | 1307 | 1307 |
| Accepted reads | 525 | 525 |
| Accepted writes | 262 | 262 |
| Accepted operations | 787 | 787 |
| Defined before/after observations | 2083 | 2083 |
| Defined read latency | 1 edge | 1 edge |
| Operation throughput | 787/1307 | 787/1307 |

All seven workload rows are comparable with zero deltas. The entire API/CLI
responses agree. Workload identity:
`d647a2c4bd126b9e1e5891b3284bbb5fcb03f19f6c25d9fc2c09bf1d08fac74e`.
The workload covers all256 addresses, 0/127/128/255 boundaries, writes/readbacks,
disabled writes/holds, undefined initial values and before/after-edge observations.
The direct TT adapter restricts high write-data byte to address, exposing full
16-bit output; `rst_n` is a write-command input in this specimen, not RAM reset.

**Assessment for this workload:** there is no cycle-performance reason to prefer
one representation. Flops offers implementation portability; Exact uses the
supported vendor storage macro and requires its integration/collateral. This is
an actual structural/integration tradeoff, not a demonstrated physical area/speed
win. The historical accepted SRAM P4 has a different adapter, staging registers,
clock and physical profile, with no matched physical Flops counterpart. Its area
and timing cannot supply a controlled A/B delta for the new workload. No new
synthesis/P&R/STA was justified or run. Raw Magic=3 with its narrow disposition,
abstract macro-boundary LVS and no timed SRAM/SDF/internal-device LVS/IR-EM claim
remain qualifications of that historical evidence.

## Exact evidence and provenance

[Final installed example](../../../hardcaml_asic_example/docs/pt8_workloads_final.md)
records commands, failures, limits and preservation. Its new
`acceptance/pt8-workloads/final-reviewed-receipt.json` SHA256 is
`2dbeecc2fafbe23704557b0535a8a2b03c0d902e7904980ac0f992de21243bd8`.

- Final reader generation: `1d3e1195ba4f45fb93eb986ca23cfb75`.
- Reader captured source: `86d7b71e7992429cf84b239ae70fe7fd3fbbd2b449227760ef64e0dc57e2d466`.
- Original simulation producer: `59aeb673765d4ef5b9308eb611f8cf0f`.
- Actual authoritative executions: `acceptance/pt8-workloads/simulation-04/`.
- Final public acceptance/controls: `acceptance-final-01`, `controls-final-01`.
- Installed corrected negatives: `regressions-final-01`, `discovery-alias-final-01`.

Reader-only recaptures reused unchanged simulation evidence after source/RTL/bench
equality checks; they are not new executions. Earlier failed/superseded captures,
simulations and receipts remain intact. Producer identities are not reattributed.
These are local source/execution observations, not authenticated execution or P6.

## Independent review and actual checks

[Result review](pt8-workload-result-review.md) independently exercised92 probes and
found a descriptor leak on directory-to-channel conversion failure. Fix closes the
raw fd before propagating the original exception; 64 original reviewer calls now
leave5 descriptors unchanged, and640 durable directory-reference failures have
zero growth. The installed example separately checks500 failures in one process.

[Comparison review](pt8-workload-comparison-review.md) found late raw-generation
and directory-discovery fence gaps; supplementary internal flow aliases also
regressed during the first correction. Final code fences the raw consulted closure,
discovery membership/existence and allowed alias resolution after deep replay.
Original repros remain unchanged, corrected wrappers verify rejection. Independent
checks:38 late-fence controls, three discovery cases, eight alias/bounds receipts.
All findings closed; sequential checks are not atomic cross-file/ABA authentication.

Final focused provider checks pass: workload50, workload comparison49, durable late
fence39, existing comparison96 and corrected admission43; `@install` builds. These
are distinct suites, not a summed universal coverage claim. Existing retained
named/physical API/CLI matrix preserves1860 files and original digests:18 native
physical-self zero deltas,18 cross-stage confounded rows, distinct named attempts.

Coordinator independently read constructor/producer/reader code and ran
`test/workload_example_review/review.py` against the final installed public API.
It checks all four actual raw simulator logs against retained rows and testbench
steps, independently recomputes the memory oracle/counters, verifies selected RTL,
stock model hashes and compile/execution receipts, and checks seven zero deltas.
Receipt: `acceptance/pt8-workloads/coordinator-review-01/review.json`. Both deliberate
semantic failures are detected (ignored workload2075, ignored address1024 defined
observation mismatches). No extra simulation was needed for this audit.

## Limits and preservation

Records:1MiB;1..4096 operations; up to32 artifacts,8MiB each/32MiB aggregate;
comparison whole response1MiB. Final raw audit:16384 distinct paths/2GiB and at
most8192 discovery children. Unsupported definitions reject or withhold deltas;
concrete models remain retained separately from producer-declared semantics.
Source replay proves consistency with retained artifacts, not that arbitrary
consumer code uses every declared parameter. Actual example semantic controls
provide the bounded evidence of use here. Named macro preparation remains unsupported.

Original141450 files/1678 symlinks and correction-history7720 files/2 symlinks
are preserved with zero changes in the final example seal. Existing source edits,
old provider selections/configuration and all original evidence remain intact.
All Dune builds use switch5.2.0+ox, explicit limits and shared checkout locks;
global budget20, agent ceilings6 including nested captures. EDA allocation/use0.
No Git publication/authorship operations, live-daemon changes, Protemu or Workbench
work occurred. This closeout postdates the tested capture; it changes no tested
implementation bytes or historical evidence.
