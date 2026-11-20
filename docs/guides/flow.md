# TT/CMOS5L flow execution and results

How an emitted bundle is run through LibreLane and how a run becomes preserved
evidence. [Bundle emission](bundle-emission.md) produces the input and
[bootstrapping](bootstrap.md) provisions the tools. This guide covers everything
after that, for this repository's examples and consumer bundles alike.

- New to explicit stages? Start with the [stage quickstart](stage-quickstart.md).
  The typed OCaml facade is in [OCaml stage recipes](ocaml-stage-recipes.md).
- Running a project's named flow/profile? See [named flows](named-flows.md).
- Earlier dated notes and development-adapter commands are in the
  [2026-10-15 snapshot](../archive/flow-guide-2026-10-15.md).

The bundle is immutable input. `hardcaml-asic-flow` verifies its file hashes and
pinned target references, creates a unique directory for each attempt, runs
LibreLane and collects results. It never installs tools or changes
`manifest.json`.

## Commands at a glance

| Command | What it does | Needs tools/PDK/Docker |
| --- | --- | --- |
| `provision` | Provision managed collateral under a tool root ([bootstrap](bootstrap.md)) | Network, Docker, Nix |
| `preflight BUNDLE` | Check a bundle against the provisioned environment and save a readiness report | Yes, read-only |
| `run BUNDLE --stage synthesis\|full` | One coarse Classic implementation attempt | Yes |
| `postcheck RUN` | TT precheck and gate-level simulation of a completed full run | Yes |
| `collect RUN` | Write `RUN/results.json` from a completed or partial run | No |
| `report TARGET` | Summarize a coarse run, stage checkpoint, stage workspace or execution JSON | No |
| `archive RUN` | Package records, reports and the complete input bundle as evidence | No |
| `execute BUNDLE` | Fixed sequence: run → postcheck (full only) → collect → report → optional archive | Yes |
| `stages BUNDLE` | Explicit sequential stages with checkpoints and a journal | Yes |
| `checkpoint-inspect` | Host-only validation of a published checkpoint | No |
| `observation …` | `describe`, `inspect`, `read-updates` and `watch` on a stage journal | No |
| `finalize EXECUTION` | Terminal results, and optionally external acceptance, for a six-stage execution | `--accept` only |
| `finalization-inspect` | Read a finalization receipt | No |

`--help` and `--version` need nothing. `--dependency-lock` and `--waiver` are
parsed but rejected: dependency identity and waivers belong to ASIC P6.5.

<a id="default-result-store-and-cli-contract-3"></a>
## Default result store and CLI contract 4

### Output contract

The CLI prints concise human summaries by default. **All machine callers request
`--json`.** `--version --json` advertises this as `cli_contract.version = 4`,
which is separate from package version 0.1.0 and record schema 1. JSON stdout is
the command's raw payload; paths, issues and diagnostics go to stderr.
`--verbose` expands stderr diagnostics only; it never changes the payload or
enables execution.

`--json` works before the command, anywhere after it, and at either observation
parser level. An omitted inner flag never resets an outer one:

```sh
hardcaml-asic-flow --json observation describe
hardcaml-asic-flow observation --json read-updates "$ATTEMPT" --stream-id "$STREAM_ID" --after 0
hardcaml-asic-flow observation read-updates "$ATTEMPT" --stream-id "$STREAM_ID" --after 0 --json | jq '.cursor'
hardcaml-asic-flow finalization-inspect --json "$FINALIZATION_ATTEMPT"
```

Observed `stages --json` and `finalize --json` emit **JSONL**: parse each line
separately, keep the early allocation receipt, and handle either a terminal
result or an error. `stages --no-observation --json` returns one execution object.
Unsupported combinations are parser errors (exit 2), for example
`archive --list --json`, `provision --json`, `describe --after` and
`finalization-inspect --accept`.

### Storage and run selection

| Command | Default storage / output | Explicit selection |
| --- | --- | --- |
| Project `describe` | Local declaration S-expression, no storage | `--json` wraps the S-expression and project name; not Workbench data |
| Project `emit` | Verified immutable `ROOT/_flow_results/bundles/<identity>`; prints created/reused path and identity | `--output-dir DIR` writes to a new/empty directory instead |
| Project `preflight` | Emits/reuses the current bundle, saves a separate operation and readiness report | `--bundle DIR` skips emission; `--output FILE` writes an exact report with no operation receipt |
| Installed `preflight BUNDLE` | `<results-root>/operations/<UTC-timestamp>_<id-prefix>/{operation,preflight}.json` | `--results-root DIR`, `--output FILE`, `--json` |
| Project/installed `run` | New `<results-root>/runs/<UTC-timestamp>_<id-prefix>`; full random run ID in `run.json` | `--stage synthesis\|full` required; `--runs DIR` overrides the run store |
| Installed `collect RUN` | Writes `RUN/results.json`, prints its path | `--output FILE`; legacy `--stdout` emits read-only JSON |
| Installed `report TARGET` | Read-only summary | Coarse `--json` keeps raw results and acceptance exit; stage `--json` emits one `hardcaml-asic.report` envelope |
| Installed `execute BUNDLE` | The fixed sequence above | Stage required; `--json` selects the sequence payload; no project counterpart |
| Installed `archive RUN` | `--output DIR` creates an archive; `--list` shows the selection | `--json --output DIR` supported; `--json --list` is not |

Installed roots default to `<caller-cwd>/_flow_results`, never a guessed project
root. The project CLI defaults to `ROOT/_flow_results`, and its tool root to
`ROOT/_flow_tools` (`--toolchain` > nonempty `TOOLCHAIN` > default). Relative
overrides are relative to the caller's working directory. Project discovery walks
up to a directory with `dune-project` and `asic-dependency.json`, and refuses to
discover from inside `_build`, `_flow_results` or `.dev`; give an explicit root
there.

The project CLI owns discovery and emission; the installed provider owns
readiness and implementation. Project `preflight`/`run` check that the provider
supports contract 2, 3 or 4 before emitting, then `execv` the provider, preserving
its exit status and signals. Project `run` is **implementation only**: it never
implicitly collects, postchecks, reports or archives. It takes either Classic
`--stage synthesis|full` or explicit-stage `--endpoint STAGE` (which requires
contract 4); mixing them is rejected before emission.

Later operations always take an explicit existing run. There is no newest-run
guessing, arbitrary resume or GC:

```sh
RUN=/absolute/path/to/the-recorded-run-directory
hardcaml-asic-flow collect "$RUN" --json
hardcaml-asic-flow report "$RUN" --json
```

Records and failure behavior:

- Preflight operations are diagnostics, **not implementation runs**. A pending
  record is saved before probes. A caught probe failure becomes a saved failed
  readiness report and exits 1; usage and output-path errors exit 2.
- Run records are written before readiness probes. Failed readiness leaves no
  staged project or implementation commands.
- SIGINT/SIGTERM save interrupted diagnostics where storage permits.
- Run status describes implementation, not report acceptance.
- Atomic JSON replacement does not promise fsync or power-loss recovery.
- Classic commands have no live journal; only `stages` journals
  ([below](#default-persistent-stage-observation-schema-2-reads-schema-1)).

## Coarse Classic runs

### Running it

With a provisioned tool root, `--toolchain` (or `TOOLCHAIN`) fills in
`--support-tools ROOT/tt`, `--pdk-root ROOT/pdk`, `--python ROOT/.venv/bin/python`
and, for postcheck, `--precheck-python ROOT/.venv-precheck/bin/python`. Any of
them can be given explicitly instead.

```sh
TOOLCHAIN=/absolute/path/to/tool-root
BUNDLE=/absolute/path/to/emitted-bundle

hardcaml-asic-flow preflight "$BUNDLE" --toolchain "$TOOLCHAIN"
hardcaml-asic-flow run "$BUNDLE" --toolchain "$TOOLCHAIN" --stage synthesis
# prints the new run directory; use it explicitly from here on
RUN=/absolute/path/printed-by-run
hardcaml-asic-flow collect "$RUN"
hardcaml-asic-flow report "$RUN"
```

`--stage full` adds place and route and LibreLane's physical checks. A completed
full run then needs the external postchecks:

```sh
hardcaml-asic-flow postcheck "$RUN" --toolchain "$TOOLCHAIN" \
  --testbench test/tt_bundle_tb.v --testbench-top tb_observable
hardcaml-asic-flow collect "$RUN"
hardcaml-asic-flow report "$RUN"
```

Or run the whole sequence at once:

```sh
hardcaml-asic-flow execute "$BUNDLE" --toolchain "$TOOLCHAIN" --stage synthesis
hardcaml-asic-flow execute "$BUNDLE" --toolchain "$TOOLCHAIN" --stage full \
  --testbench test/tt_bundle_tb.v --testbench-top tb_observable \
  --archive /absolute/path/to/evidence-directory
```

`execute --stage full` requires the testbench, its top and precheck Python;
`--stage synthesis` rejects them. With `--json`, `execute` prints one stderr
line per step (status and duration), the run directory and any archive path, then
a single JSON object with the run identity, per-step statuses and final outcome.
It does this on failure and interruption too, so a stopped attempt still names
the run holding its diagnostics. A failed step keeps its own status rather than
being flattened into the sequence's.

Notes:

- `run` prints its run directory even when it fails; `execution.log` and partial
  `project/runs/asic` contents remain, and retries get distinct IDs.
- `--native` uses locally installed EDA executables instead of Docker. It
  supports **synthesis only**.
- The runner copies the pinned floorplan into its per-run staging directory so the
  container can resolve `dir::../tt/...`, and reads PDK views from the PDK root.
  Docker must be reachable; see [bootstrap troubleshooting](bootstrap.md#troubleshooting).
- For scale: the reference observable bundle takes roughly 24 minutes to harden
  and 8 more to postcheck. A completed full run directory is around 235 MB of
  OpenROAD, Magic and KLayout output. Keep run stores **outside** any Git
  working tree; what belongs in a repository is the curated archive.

### Development driver: `scripts/flow.sh`

In this repository, `scripts/flow.sh` emits the maintained examples and fills in
example, testbench and tool paths. Every flow operation goes to the same native
`hardcaml-asic-flow` implementation, built from this checkout. It takes one
command per invocation, and with no argument prints its help instead of starting
anything:

```sh
scripts/flow.sh                                         # help; nothing runs
scripts/flow.sh build                                   # dune build && dune runtest
OUT=/tmp/try KIND=memory scripts/flow.sh emit
OUT=/tmp/try STAGE=synthesis scripts/flow.sh execute
OUT=/tmp/try RUN=/tmp/try/runs/<id> scripts/flow.sh report --json
```

Every path is an overridable environment variable (`TOOLCHAIN`, `TT`, `PDK`,
`FLOW_PY`, `PRECHECK_PY`, `OUT`, `BUNDLE`, `RUNS`, `KIND`, `STAGE`, `TESTBENCH`,
`TESTBENCH_TOP`, `RUN`). Defaults come from `.toolchain/toolchain-env.sh`, which
`./bootstrap.sh` writes; values already in the environment win. `STAGE` (for
`run`/`execute`) and `RUN` (for every later operation) are **required**, because
guessing either is how an expensive run or a misattributed record happens. `$OUT`
defaults to `../p4-$KIND`, outside this repository. `ALLOW_PYTHON_MISMATCH` is
gone, and the driver stops if it is still set. Extra arguments pass straight
through, for example `scripts/flow.sh archive --output /tmp/evidence`.

### Preflight records

Use the saved preflight report. It records why a run was allowed to start: every
external file hash, the actual tool and container versions, and the runtime
policy comparisons. Scrollback does not survive as evidence. `run` performs its
own preflight and saves it as the attempt's `preflight.json`, so the record in a
run is the one that actually gated it; `run.json` stores the same structure under
`environment`. Preflight checks the specific bundle against installed files and
never fetches or installs anything.

### Postcheck

Postcheck runs TT precheck under the pinned support-tools `precheck/default.nix`,
with a separate Python environment for its KLayout dependency. Gate-level
simulation runs Icarus from the run's bound LibreLane image, because the CMOS5L
flop models depend on `$setuphold` delayed nets that Icarus 12 leaves undriven. It
compiles the PDK I/O, UDP and standard-cell models with the final netlist, and
records GDS, netlist and testbench hashes, simulator image and version, the
command log and both verdicts under `checks/<id>/`.

The bundle's LibreLane settings must include the TT template's physical settings
as reasoned overrides; `Tt_cmos5l.overrides` supplies them. Without
`FP_PDN_MULTILAYER=0` and the template's PDN and LEF-pin settings, LibreLane puts
the power grid on `TopMetal1`. TT precheck then rejects the layout even though
LibreLane's own DRC, LVS and antenna checks pass.

### Records and acceptance

`run.json` records build identity, manifest hash, actual environment, command
arguments, requested/completed stage, status, timestamps and output paths.
`results.json` separates process completion from timing goals and physical
checks:

- Synthesis area is mapped standard-cell area in µm²; the cell-type inventory
  comes from Yosys `stat.json`.
- Setup and hold slack are in ns, with the worst corner when available.
- Missing metrics carry reasons, and raw reports stay linked relative to the run.

Collection is a separate command and also works on failed or partial runs.

`report` decides acceptance from the requested stage recorded consistently in
`run.json` and `results.json`, and exits **0** (pass), **1** (fail) or **2**
(unusable input):

- **Synthesis** passes only when both records say synthesis was requested and
  completed, collection has no errors, mapped cell and area metrics are finite
  and valid, and unmapped instances, synthesis errors and inferred latches are
  all explicitly present and zero. Zero mapped area or cells is valid for a
  design that legitimately maps that way. Physical timing, antenna, DRC, LVS,
  postchecks and hold repair are reported as *not evaluated*, never as passes.
  The verdict says `synthesis acceptance passed`, not signoff.
- **Full** additionally requires full completion in both records; finite,
  cornered, constrained setup and hold with a passing timing goal; DRC, LVS and
  antenna each present and passing; and a completed postcheck passing both TT
  precheck and gate-level verification. For a full run, the report prints timing
  per corner, signoff checks, TT precheck rows and what the post-CTS resizer did
  about hold.

A full request that stopped after synthesis fails rather than being reclassified.
Unknown, missing or contradictory stage fields fail with diagnostics. Missing
metrics stay unavailable, never zero or pass.

## Archiving and restoring evidence

```sh
hardcaml-asic-flow archive "$RUN" --list                 # the selection; writes nothing
hardcaml-asic-flow archive "$RUN" --output "$EVIDENCE"   # records and both tarballs
```

The archive contains the plain JSON records and:

- `reports.tar.gz`: the logs and reports an acceptance decision is read from,
  plus `final/gds` and `final/nl`. The GDS and netlist are the run's actual
  output and the only inputs to a precheck or gate-level rerun that would
  otherwise need hardening again (about 500 KB compressed). Rebuildable
  intermediates such as `odb`, `mag`, `mag_gds` and compiled simulators stay out.
- `input-bundle.tar.gz`: the original `manifest.json` bytes plus exactly every
  path in `manifest.files`, pinned by `input-bundle.sha256`.
- `archive.json`, which names every member and absence and records the bundle
  archive digest, size, and whether its bytes came from the original or staged
  bundle.

Every hash the run already pinned is rechecked: the staged manifest against
`run.json`, build identity against the manifest, every bundle file and
source-input copy against the manifest, and GDS/netlist/testbench against
`postcheck.json`. Paths must be unique, relative, contained regular files;
symlinks and traversal are rejected. The archiver prefers the original bundle
path in `run.json` and accepts `RUN/project` only if that staged copy is
independently complete. Undeclared staging files are never included.

Construction finishes in a sibling temporary directory before publication, so a
failure leaves an existing evidence directory unchanged. `--force` replaces only
archiver-owned files and keeps `README.md` and other user files. A missing
reports input is reported, not skipped, which is how a synthesis-only run
archives without pretending to have a layout. The input tarball is deterministic.
The archiver never writes the `README.md` beside the records: that note is the
human account of what the run showed.

Restore and verify the bundle from the evidence directory alone, with no
checkout, original bundle, run directory, PDK or opam switch:

```sh
RESTORE=$(mktemp -d)
(cd "$EVIDENCE" && sha256sum --check input-bundle.sha256)
tar -xzf "$EVIDENCE/input-bundle.tar.gz" -C "$RESTORE"
python3 - "$RESTORE/bundle" "$EVIDENCE/run.json" <<'PY'
import hashlib, json, pathlib, sys
bundle, run_path = map(pathlib.Path, sys.argv[1:])
manifest_bytes = (bundle / "manifest.json").read_bytes()
manifest = json.loads(manifest_bytes)
run = json.loads(run_path.read_text())
assert hashlib.sha256(manifest_bytes).hexdigest() == run["manifest_sha256"]
assert manifest["identity"] == run["build_identity"]
for entry in manifest["files"]:
    data = (bundle / entry["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]
print("verified", len(manifest["files"]), "bundle files for", run["run_id"])
PY
```

Re-running or preflighting the restored design still needs the separately
recorded toolchain and collateral. Never edit emitted inputs to make restoration
or preflight pass. Archives made before complete-input preservation may lack
`input-bundle.tar.gz` and cannot be reconstructed from `reports.tar.gz`.

## Explicit sequential stages and checkpoints

### Stage plans and boundaries

The OCaml `Execution_plan` / `Runner` modules run both the coarse Classic
sequence and explicit stage plans. Preflight binds the runtime once; each stage
then runs in a fresh owned process and publishes a checkpoint before its
occurrence can complete. A StagePlan schema 3 has exactly these fields:

```json
{"schema_version":3,"stages":["synthesis","floorplan","placement","cts","routing","signoff"],"initial_boundary":null}
```

Fresh sequences start at synthesis; resumed sequences start immediately after
`initial_boundary`. Empty, repeated, reordered or skipped stages, extra fields and
duplicate JSON keys are rejected. `--plan FILE`, `--endpoint STAGE` and
`--methodology-plan FILE` are mutually exclusive. Endpoint shorthand expands to a
contiguous sequence, and with `--checkpoint` it infers the source. Legacy
schema-2 endpoint/resume JSON is still readable. OCaml callers use
[`Flow.Stage` / `Flow.Recipe`](ocaml-stage-recipes.md). Methodology plans come
from [named flows](named-flows.md) and are fresh-only.

| Boundary | Endpoint | Immediate successor | Standalone restart targets |
| --- | --- | --- | --- |
| synthesis | `Yosys.Synthesis` | `Checker.YosysUnmappedCells` | floorplan, placement |
| floorplan | `OpenROAD.Floorplan` | `OpenROAD.DumpRCValues` | placement |
| placement | `OpenROAD.DetailedPlacement` | `OpenROAD.CTS` | cts |
| cts | `OpenROAD.CTS` | `OpenROAD.STAMidPNR-1` | routing |
| routing | `OpenROAD.DetailedRouting` | `Odb.RemoveRoutingObstructions` | signoff |
| signoff | `Misc.ReportManufacturability` | none | none |

Recipes include every intervening boundary; going straight from synthesis to
placement is only a standalone segment capability. CTS's later STA/repair belongs
to routing, and routing's checkers/fill/extraction belong to signoff. Official
Classic gates and successors stay intact. There is no arbitrary DAG,
custom-step or per-stage-settings API.

Requirements:

- Docker and exactly the tested image
  `sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
- Explicit `--support-tools`, `--pdk-root`, `--python`, `--workspace` and
  `--checkpoint-store` (no `--toolchain` shorthand).
- A fresh workspace must not exist, but its parent must; the checkpoint store
  goes inside it. Resume requires the original retained workspace and an explicit
  `--checkpoint`.
- `--timeout` bounds each launch and defaults to 600 seconds, which is too short
  for full signoff; physical evidence used 1,800–7,200 seconds.
- Native execution, macros, other images and relocated handoffs are not
  supported.

### Checkpoints

Publication copies the official state, configuration, closure, segment metadata
and every non-null view. A hashed schema-2 manifest declares exact restart
targets, compatibility, file inventories and `acceptance: not_evaluated`. Host
and image-only closure are validated before write bits are removed and the
checkpoint is atomically promoted (no clobber). A publication failure prevents
successor readiness.

Checkpoints are **path-bound**: copied views remove dependence on old producer
runs, not on the original input, support and PDK roots or the bound bridge and
image. Keep them. This is visibility and integrity, not fsync durability,
execution recovery or archive/GC. Schema-1 physical publications are not
upgraded.

`checkpoint-inspect CHECKPOINT --restart-to STAGE` checks host closure and the
transition only; `--compatibility EXPECTED_JSON` checks a caller-supplied object.
It reports `host_only` and `runtime_closure_validated: false`, and execution
validates image-only closure again. Public metrics are cumulative evidence, not
per-step origin or an acceptance verdict.

Bundle public paths are symlinks to verified `.objects/<identity>.<unique>` data.
Preflight and run canonicalize them, so saved records may name the backing path;
both refer to the same immutable bundle
([publication details](bundle-emission.md#identity-addressed-publication)).

### Read-only reporting

```sh
hardcaml-asic-flow report "$WORKSPACE"
hardcaml-asic-flow report "$CHECKPOINT"
hardcaml-asic-flow report "$CHECKPOINT" --verbose  # complete cell-type table
hardcaml-asic-flow report "$WORKSPACE/execution-ATTEMPT.json" --json
```

`report TARGET` accepts a coarse run, a checkpoint directory, a stage workspace
containing exactly one **direct** `execution-*.json`, or an explicit segmented
execution JSON (plan schema 2/3). Conflicting structural markers, malformed
evidence and multiple executions are errors; name the execution file for
multi-attempt workspaces. Discovery never scans recursively or picks the newest
record. For observation directories, use `observation inspect`.

Stage reports validate checkpoint inventory, hashes and host closure without tool
calls or writes. A successful query exits **0**, including for recorded failed,
interrupted or incomplete executions. Malformed targets or invalid checkpoint
evidence exit **2**. Acceptance and external postcheck stay `not_evaluated`.
Physical `full` requires a selected, completed signoff occurrence and its
validated publication; it does not imply acceptance. Coarse-run reports keep
their 0/1/2 acceptance exits.

Stage `--json` emits one object with contract `hardcaml-asic.report`, schema 1:
the target/selected record, the original validated execution (or null),
completion, checkpoints, provenance, availability-wrapped metrics and
diagnostics. In JSON mode the human summary goes to stderr. Unknown units stay
unknown and invalid counts stay unavailable. Metrics describe the accumulated
boundary snapshot, not measurements necessarily made in that stage. Named
executions add a `methodology` section
([reference](../reference/native-historical-methodology.md)). See also
[checkpoint reporting](../development/checkpoint-reporting.md).

When the checkpoint's inventoried `segment.json.run_dir` still has
`06-yosys-synthesis/reports/stat.json`, the report adds Yosys's cell-type
inventory and sequential cell area in µm²:

- The default table shows the 10 largest counts, ties sorted by name, with type
  count and total.
- `--verbose` shows every row, and `--json` always includes the full sorted map.

Each `supplementary` entry records `kind: yosys_stat`, the exact path, raw-byte
SHA-256, creator/tool, area values/units and inventory. It is labelled
**retained producer report; not checkpoint-inventoried**. A missing report is
fine. A malformed one produces diagnostics without replacing checkpoint metrics
or failing the query.

### Default persistent stage observation (schema 2; reads schema 1)

CLI `stages` journals every fresh or resumed attempt by default. Standalone
runner APIs still need explicit writer composition, and Classic `run`/`execute`
do not journal.

```sh
hardcaml-asic-flow stages "$BUNDLE" --support-tools "$TT" --pdk-root "$PDK" --python "$FLOW_PYTHON" \
  --workspace "$NEW_WORKSPACE" --checkpoint-store "$NEW_WORKSPACE/checkpoints" \
  --endpoint floorplan --observation-store "$OBSERVATIONS"
hardcaml-asic-flow observation describe
hardcaml-asic-flow observation inspect "$ATTEMPT_DIRECTORY"
hardcaml-asic-flow observation inspect "$ATTEMPT_DIRECTORY" --json
hardcaml-asic-flow observation read-updates "$ATTEMPT_DIRECTORY" --stream-id "$STREAM_ID" --after 5
hardcaml-asic-flow checkpoint-inspect "$CHECKPOINT" --restart-to cts --json
```

The default root is `<resolved-workspace-parent>/observations`, outside the
workspace. `--observation-store ROOT` overrides it and `--no-observation`
disables journaling; the two are mutually exclusive, and the store must be
outside the workspace. Each attempt is exclusively allocated, and every resume
gets a new stream and never reopens the source attempt.

**Allocation and terminal delivery.** Before any recipe or checkpoint reading,
preflight or tool execution, `stages` flushes an early allocation receipt. With
`--json` this is one JSON line containing `contract`, `schema_version`,
`event: observation_allocated`, `stream_id` and the absolute `attempt` directory.
When execution returns, the next line is the complete execution JSON, including
for runner-returned failure or interruption. The execution file is published
before the journal outcome.

Caught preparation, plan, storage and output failures instead produce a CLI
delivery envelope. It is not a journal event or a persisted schema:

| Field | Meaning |
| --- | --- |
| `contract`, `schema_version` | `hardcaml-asic.stage-observation`, 1 |
| `event` | `observation_terminal` |
| `status`, `exit_code` | `failed`, 2; or `interrupted`, `128 + signal` |
| `attempt`, `stream_id` | Allocated absolute path and identity, or null if allocation failed |
| `execution_file` | Absolute execution JSON path only after successful publication, otherwise null |
| `observation` | Complete read-only inspector payload after releasing locks, or null |
| `recording_error` | Additional journal publication/inspection error, or null |
| `error` | `{code, detail}`: `BUSY`, `INTERRUPTED`, `EXECUTION_FAILED`, or an observation error code |

When allocation fails, only this envelope is emitted, with no invented identity.
Parser errors stay plain diagnostics. After allocation, failures normally record
an outer outcome even if delivering the receipt fails. A storage failure can
leave no outcome, but completed stage facts are kept. An abrupt exit or broken
stdout cannot promise a terminal line. A recorded interruption does not prove
external cleanup; consult the referenced transport evidence.

**Queries.** All observation queries and `checkpoint-inspect` print labelled text
by default and the complete machine payload with `--json`, including structured
errors. Inspection exits 0 even for a recorded failed execution; invalid or
missing records, or failed checkpoint validation, exit 2.

- `describe` returns contract `hardcaml-asic.stage-observation`,
  `schema_version: 2`, `supported_schema_versions: [1, 2]`, capabilities, closed
  field descriptions under `schemas`, and the writer and retention policies.
- `inspect` returns a consistent recorded prefix and its cursor.
- `read-updates` returns later events after a cursor. Human output shows up to 20
  chronological events (the first and last ten when there are more), publication
  paths, the omitted count and an explicit cursor. An empty read does not imply
  completion.

These commands need no tools, no PDK and no producer process. Readers never
acknowledge, append, repair or take ownership. From OCaml, use
`Hardcaml_asic_execution.Observation` / `Observation.Reader`.

#### Foreground incremental watch

```sh
hardcaml-asic-flow observation watch "$ATTEMPT_DIRECTORY"
hardcaml-asic-flow observation watch "$ATTEMPT_DIRECTORY" --interval 0.5 --json
hardcaml-asic-flow observation watch "$ATTEMPT_DIRECTORY" --stream-id "$STREAM_ID" --after "$CURSOR" --json
```

Watch is a separate foreground reader with no daemon, provider subprocess, state
file or execution control. Human output prints **every** delivered event
chronologically, then labelled reader status and recorded results. `--json` (at
any level) selects flushed JSONL, one complete object per line:

| Envelope | Exact top-level fields |
| --- | --- |
| `updates` | `event: "updates"`, `contract`, `schema_version`, `stream_id`, `after`, `cursor`, `events` |
| `status` | `event: "status"`, `contract`, `schema_version`, `stream_id`, `cursor`, `writer_active`, `observation_status`, `outcome`, `final_results` |

- **Versions:** both envelopes use the stream's schema version (1 for legacy
  journals, 2 for new ones).
- **`updates`:** the complete `read-updates` payload plus the discriminator, with
  full journal records in `events`. It is emitted only for a nonempty suffix.
  `after` is the supplied cursor on first delivery, then the last delivered
  cursor.
- **`status`:**
  - `writer_active` is a boolean or null.
  - `observation_status` is `live`, `final` or `writer_lost_or_unknown`.
  - `outcome` is the recorded `{status, exit_code, error}` or null.
  - `final_results` matches `inspect`.

  It is emitted on attachment and on change, including a final status before
  stopping.
- These envelopes are reader delivery, never journal events.

**Reconnect.** Without reconnect arguments, the reader validates the full prefix
and delivers from zero. `--stream-id` and `--after` must be given **together**.
A wrong stream, or a negative, noninteger or beyond-head cursor, fails with
`RESET_REQUIRED` (exit 2). Cursor zero is valid; a cursor at the head delivers
only status until more events arrive. A cold reconnect still validates the whole
retained prefix. Persist your stream and cursor only after fully processing an
`updates` envelope: delivery is cursor-based, with no acknowledgement or
exactly-once promise.

`--interval` must be positive and finite (default 0.5 s) and controls idle
polling. Polls read and hash only newly promoted records, with the same strict
validation as a cold replay. Deletion, replacement or metadata change of retained
records, gaps, truncation, and hash, stream or grammar corruption all fail closed:
discard the reader and reconnect. When writer activity looks absent, the reader
drains the committed tail once more before declaring loss, which avoids a race
with final publication.

| Reader exit | Meaning |
| --- | --- |
| 0 | A terminal outcome is recorded, whether completed, failed or interrupted |
| 3 | Unresolved writer loss or unknown activity |
| 2 | Corruption, cursor or query error, or broken stdout |
| 130 | Reader interrupted with Ctrl-C |

The execution's own `outcome.exit_code` is independent. Disconnect, broken
stdout, Ctrl-C and slow readers affect only delivery; they never cancel, write to,
or lock the executor's workspace.

Costs: polls list and stat O(prefix length) metadata and keep events in memory.
Integrity metadata (device, inode, size, nanosecond mtime/ctime) assumes a
trusted local store of immutable files. It does not protect against a hostile
owner. There is no store-wide subscription, heartbeat, repair, takeover,
backend-step lifecycle or execution recovery.

#### Journal schema

An attempt directory holds atomically promoted `000000000001.json`,
`000000000002.json`, … records, plus `writer.lock` and, once preflight is
reached, `preflight.json`. Each schema-2 record has exactly:

```text
contract, schema_version, stream_id, sequence, previous_sha256,
event, data, recorded_at, elapsed_seconds, sha256
```

| Field | Schema-2 meaning and strict validation |
| --- | --- |
| `recorded_at` | Producer recording time in UTC, exactly `YYYY-MM-DDTHH:MM:SS.ffffffZ`; valid calendar date/time, six fractional digits, no offsets or leap seconds |
| `elapsed_seconds` | Finite nonnegative JSON number; monotonic seconds since attempt creation; nondecreasing across the stream |

Both timing fields are covered by the record hash. Recording time is sampled
before atomic promotion; it is not tool-start, fsync or reader-receipt time.
Lifecycle events are appended synchronously while execution runs, not batched at
the end. `started` precedes the stage operation, `published` follows validated
checkpoint promotion, and `finished` follows the stage's return. Sequence is
authoritative even if the wall clock moves backward. Elapsed time ignores
wall-clock adjustments and differs from each occurrence's `duration_seconds`.

Schema-1 records are identical except they lack `recorded_at` and
`elapsed_seconds`. Readers keep their original facts, hashes and payloads
without inventing times. A stream must use one schema throughout; missing or
extra timing, invalid UTC, invalid or regressing elapsed values, and mixed
streams fail closed. Human output appends
`[recorded 2026-10-13T12:00:00.123456Z; +1.250s]` to timed events. JSON keeps
full precision.

Negotiate journal support through `observation describe --json`
(`supported_schema_versions`, `producer_event_timestamps`,
`monotonic_elapsed_seconds`). Identity capabilities also report it:
`stage_observation_schemas: {write: 2, read: [1, 2]}` plus both timing flags.
Allocation, terminal and error delivery envelopes keep their own schema 1.
Identity's `supported_record_schemas: [1]` describes the coarse
runner/collector/reporter/archiver records, not the journal.

Hashing: sequence starts at 1. `previous_sha256` is null for record 1 and
otherwise the previous record's hash. `sha256` hashes UTF-8 JSON of the other
fields with sorted keys, compact separators and no NaN. The stream/attempt ID is
a fresh 32-character lowercase UUID hex; occurrence IDs are
`STREAM_ID/implementation.STAGE`.

| Event | Exact `data` fields | Meaning |
| --- | --- | --- |
| `allocated` | `request`, `workspace`, `checkpoint_store`, `lock_path`, `preflight_record` | Intent and explicit path-bound references, before preflight |
| `planned` | `plan` | Validated StagePlan schema 2 or 3; all occurrences start `unreached` |
| `prepared` | `runtime_record` | Staging/binding returned; runtime facts are in a separate transport record |
| `started` | `occurrence`, `artifact` | Stage running; artifact null |
| `published` | `occurrence`, `artifact` | Validated checkpoint published; stage still running |
| `finished` | `occurrence`, `artifact` | Stage completed, failed or interrupted; completed requires its prior publication |
| `outcome` | `status`, `exit_code`, `error` | Final outer outcome, after the execution record is published |

Occurrence fields are exactly `occurrence_id`, `action_key`, `status`,
`exit_code`, `duration_seconds`, `best_effort` (false), `error` and `verdict`
(null). Status is `unreached`, `running`, `completed`, `failed` or `interrupted`.
Unreached successors are never inferred as skipped. Checkpoint references have
`path`, `identity`, `boundary`, `restart_supported`, `restart_targets`,
`closure_scope`, `tested_image_id` and `acceptance`.

`inspect` returns `contract`, `schema_version`, `stream_id`, `cursor`, `state`,
`writer_active`, `observation_status` and `final_results`. `state` holds
`attempt_id`, all allocation fields, `phase` (`preparing`, `executing` or
`finished`), `plan`, `occurrences`, `artifacts`, `runtime_record` and `outcome`;
missing values are null. Preparation errors leave planned stages unreached, stage
failures block successors, and a final outer failure can coexist with completed
physical work.

`final_results` has `physical_completion` (`completed` only after a recorded
signoff-stage finish, otherwise `not_recorded`), explicit `checkpoint_refs`,
`acceptance: not_evaluated` and `external_postcheck: not_evaluated`. External
postcheck and acceptance come from the
[separate finalization operation](#segmented-terminal-results-and-external-acceptance-opt-in),
which never revises the stage stream.

`read-updates` returns `contract`, `schema_version`, `stream_id`, `after`,
`cursor` and `events` (complete records strictly after the cursor). Bad cursors
produce `RESET_REQUIRED`. Missing, malformed or gapped records, hash-chain
failures, duplicate keys, nonfinite JSON and invalid lifecycle transitions
produce `INVALID_RECORD`, and inspection then returns no success snapshot. JSON
errors are `{contract,schema_version,error:{code,detail}}` with exit 2.

#### Ownership, interruption and retention

- **Locks:** observed writers hold a nonblocking Linux/POSIX advisory workspace
  lock across allocation, preparation and execution, even with different
  observation roots. A per-attempt lock gives best-effort writer presence.
  Unobserved Classic executions and external programs do not take these locks.
- **Released writers:** a released writer cannot append, and a new attempt does
  not revive the old one. There is no takeover, recovery or request idempotency.
- **Interruption:** SIGINT/SIGTERM are recorded when handling and storage
  succeed. SIGKILL, crashes or failed journal writes leave the last committed
  prefix: `outcome` stays null and `observation_status` becomes
  `writer_lost_or_unknown`. A stale `running` status is not liveness, and an
  artifact publication is not stage completion.
- **Durability:** hidden temporary files are ignored, but an incomplete promoted
  record is an error. Atomic replacement survives process restarts, not power
  loss. Hashes detect corruption, not hostile mutation.
- **Retention:** keep the observation directory, workspace, checkpoints and
  external input/support/PDK roots. `inspect` reports historical publication
  facts; it does not revalidate current checkpoint contents.

## Segmented terminal results and external acceptance (opt-in)

`finalize` selects the six published checkpoints from one completed **fresh**
six-stage execution JSON. It validates their identities, full host closure, shared
configuration and exact runtime binding, then rechecks the image-only closure
with the retained bridge in the bound image. It never calls `Flow.start` or
repeats implementation. Stitched resumed executions are not eligible.

```sh
hardcaml-asic-flow finalize WORKSPACE/execution-ATTEMPT.json --operation-store OPERATIONS
hardcaml-asic-flow finalize WORKSPACE/execution-ATTEMPT.json --operation-store OPERATIONS \
  --accept --support-tools TT --precheck-python PRECHECK_PYTHON \
  --testbench CONSUMER/acceptance/replay.v --testbench-top replay --replay-trace TRACE.txt
hardcaml-asic-flow finalization-inspect OPERATIONS/OPERATION_DIRECTORY
```

The operation store is explicit. Without `--accept`, the operation publishes
results with external postcheck and acceptance `not_evaluated`. With `--accept`,
it runs the same postcheck and acceptance policy as Classic `postcheck` and
`report`:

- TT precheck under its pinned Nix environment;
- gate-level simulation in the implementation's exact bound container;
- the full acceptance policy. Missing or malformed evidence, unconstrained timing
  and failed checks remain failures.

`--replay-trace` is optional for self-contained benches. For the example's replay
bench, it copies the reference trace (produced by the consumer's
`acceptance/export_trace.exe`) into the check attempt and passes it as
`+trace=...`; record that command and hash when producing evidence. No Dune
command is hidden inside finalization.

### Separate operation snapshot contract

Each invocation allocates a new attempt and atomically updates its
`operation.json`, contract **`hardcaml-asic.segment-finalization`, schema 1**. The
CLI flushes an early allocation receipt and then summarizes status, selected
publications, result path, requested versus actual acceptance and external
postchecks. With `--json`, it flushes an allocation line (`contract`,
`schema_version`, `event: allocated`, `attempt`, `attempt_id`) before validation
and prints the final receipt plus `attempt` afterwards.

| Exit | Meaning |
| --- | --- |
| 0 | Requested operation completed |
| 1 | Validation, checks or acceptance failed |
| 2 | Allocation/storage errors prevented recording |
| 128 + signal | Interrupted |

`finalization-inspect` reads the receipt strictly, without writing. A failed
validation does not imply acceptance rejection; `acceptance: fail` is an actual
rejection. This snapshot API is separate from the stage journal, and the terminal
stage stream is never reopened. Required receipt fields:

- `contract`, `schema_version`, fresh `attempt_id`, `request` (execution path,
  accept flag, explicit later-check inputs), `started_at`, nullable `ended_at`;
- `status`: `running`, `completed`, `failed` or `interrupted`; `phase`: the last
  entered of `validation`, `collection`, `external_postcheck` or `acceptance`;
- `external_postcheck`: `not_evaluated`, `running`, `completed`, `failed` or
  `interrupted`; `acceptance`: `not_evaluated`, `pass` or `fail`;
- `commands` (actual runtime-validation argv), nullable `results` and `error`;
  terminal receipts also have `exit_code`.

Optional evidence fields appear as work progresses:

- `execution` (original path, SHA-256 and attempt ID), `binding` and
  `selected_checkpoints` (six path/identity/boundary references);
- `runtime_validation: completed`, `selected_evidence`, `mapped_files`
  (bytes/SHA-256 inventory), `adapter` and `evidence_context`;
- `postcheck_collateral`, `postcheck_record` / `postcheck_sha256`, and
  `acceptance_report` (path/SHA-256);
- `container_cleanup` diagnostics, which transport may append.

`results` has an exact path and SHA-256. A `running` snapshot after writer loss
is unresolved; inspection never claims liveness or completion it did not record.

The adapter copies the official synthesis and signoff states, final
GDS/netlist/LEF, manifest, constraints and TT metadata into the new attempt.
Metrics come from the cumulative official signoff metrics and the synthesis
checkpoint, never from inherited counts or a fabricated Yosys report. Every
public metric or physical check carries `origin_boundary`, and `provenance` names
the adapter, operation and selected publications. The evaluation context uses the
new operation ID as its `run_id`, and `full` means the selected six-stage
completion. **No synthetic `run.json` or `stat.json` is written.** Results are
published only after collection and consistency checks; failed acceptance still
keeps collected evidence and the `acceptance.txt` diagnostics.

For the minimal Workbench terminal integration, consume this operation's exact
`results.path` and verify `results.sha256`; a finalization directory is not a
`report TARGET` target. The [terminal client contract](../development/workbench-terminal-contract.md)
describes normalized timing, physical checks, units and declared clock intent.

### Retention

Keep the operation directory, the selected checkpoints, the original path-bound
workspace, bundle, collateral and PDK roots, and the exact image. Receipt
references record historical selection, not lasting readiness. Finalization
rechecks readiness each time. Its copied evidence is a bounded acceptance package,
not a restart closure or a Classic archive input: preserve the operation tree
directly rather than fabricating a run record for `archive`.

## Runtime policy and launch binding

Bundles record `requested_tools.runtime_policy`:

- **Host flow venv:** the scalar `python=3.11` requests this exact minor. It is
  distinct from the host CLI Python (≥ 3.9), and native execution uses this host
  runtime.
- **Docker:** implementation requests the official
  `ghcr.io/librelane/librelane:3.1.0.dev3` image, the exact LibreLane version, and
  a container Python in 3.10..3.14 (`Requires-Python >=3.10`). A host 3.11 with a
  container 3.13 is therefore valid.
- **Host readiness:** host LibreLane and headless Tcl must also be ready.
- **Older bundles:** an absent policy means legacy exact-minor checks for
  **both** host and container.
- **Invalid policy:** malformed or contradictory explicit policy fails closed.
  Installed defaults never override the bundle's request.

**To get new semantics, emit a fresh bundle; never edit an old manifest.** For
example, from the example checkout:

```sh
source .dev/asic/current/activate.sh
BUNDLE="$PWD/.dev/runtime-checks/bundle-NEW"   # must not exist
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- emit --source-root "$PWD" --output-dir "$BUNDLE"
hardcaml-asic-flow preflight "$BUNDLE" --toolchain "$PWD/_flow_tools"
```

Preflight keeps the flat `actual` keys and adds a structured `runtime_contract`
with the policy, per-role observations and comparisons. `run.json` adds
`execution_binding`: the observed local image ID, engine and interpreter flags.
RepoDigests are recorded separately and are not interchangeable with image IDs;
the official tag is mutable across machines.

Docker transport launches that exact image ID with the same flags. It mounts the
staged project read-write and the PDK read-only, uses a rootless-aware UID,
removes ambient image overrides, and owns cleanup with its own deadline. This
changes transport only, not LibreLane's flow engine. Postcheck verifies the
recorded image and engine and records the simulator identity separately. Older
records fall back to the tag and engine. Readiness checks alone do not establish
synthesis or physical compatibility.

## Deprecated Python references

The installed package ships only nine Python modules (`__init__`,
`backend_protocol`, `librelane_bridge`, `stage_metadata_probe`, `probe`,
`runtime_observation`, `runtime_contract`, `managed_python`, `paths`) and four
data files. The installed Python planner, runner, checkpoint, observation reader
(`IncrementalReader`), collector, reporter and archiver APIs, including
`execution_plan` and `flow_observation`, are retired. This is a **Python API
break**. Package 0.1.0 and CLI contract 4 are unchanged because the native CLI
contract is unchanged.

Use `hardcaml-asic-flow` or the typed `hardcaml_asic.execution` OCaml library
(`Execution_plan`, `Runner`, `Checkpoint`, `Observation`/`Observation.Reader` and
the service modules). The retired sources remain in the checkout as references and
test oracles, reachable only with `PYTHONPATH=/path/to/checkout/flow`.
`scripts/phase4.py`, `scripts/report.py` and `scripts/archive.py` run those
references. They are **not** the installed implementation, keep historical
output and option spellings (such as `--allow-python-mismatch`), and should not
be used for new evidence. `scripts/flow.sh` and `scripts/flow_cli.py` use the
native CLI.

## Known limitations

- **ABC timing print:** the pinned LibreLane ABC renderer passes `cell/pin` where
  ABC expects a cell name, so ABC's printed delay estimate is not timing evidence.
  It does not affect mapped cell/area evidence or post-route OpenSTA timing
  (ASIC T.1 tracks the fix).
- **Native execution** (`--native`) supports synthesis only.
- **Not yet available:** dependency locks and waivers (P6.5), source-stamped
  `--version` output, store-wide observation and execution recovery.
- **Physical evidence:** six disconnected pins and one max-fanout violation
  remain visible in raw final metrics, outside the current acceptance gates.

## Evidence

The curated records live under [`evidence/`](../../evidence). The current
explicit-stage and finalization evidence is listed in the
[phase plan](../phase_plan.md#explicit-stage-refactor-closeout). The historical
P4 records are [memory synthesis](../../evidence/p4/memory-synthesis/README.md)
(P4.4) and [observable physical](../../evidence/p4/observable-physical/README.md)
(P4.5); see the [original P4 gates](../archive/phase-plan-through-p6.4b.md#7-p4--execution-results-and-physical-integration).
The P4.5 run used this repository's own example. It proves the physical path,
but it is not consumer-adoption evidence.
