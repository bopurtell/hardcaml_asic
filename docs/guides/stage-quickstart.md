# Explicit-stage quickstart

`hardcaml-asic-flow stages` runs the implementation as six sequential
boundaries, each publishing a checkpoint you can inspect, restart from and watch
live:

```text
synthesis → floorplan → placement → cts → routing → signoff
```

The coarse `run`/`execute` path is still supported alongside it. This page is
the short path. The [flow guide](flow.md#explicit-sequential-stages-and-checkpoints)
has the full contracts, and [OCaml stage recipes](ocaml-stage-recipes.md) covers
typed recipe construction. To run a project's named flow or profile, see
[named flows](named-flows.md).

## Requirements

- An emitted, published bundle that can be implemented physically, and a
  provisioned tool root ([bootstrap](bootstrap.md)).
- Docker and the exact tested image
  `sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
- An installed provider with CLI contract 4. Activating an older consumer pin
  does not upgrade it.

<a id="run-and-observe"></a>
## Run from the example project

After activating a contract-4 installation, from `hardcaml_asic_example`:

```sh
. .dev/asic/current/activate.sh
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- run --endpoint synthesis
```

Endpoints are `synthesis`, `floorplan`, `placement`, `cts`, `routing` and
`signoff`. The project CLI:

- emits or reuses the bundle (or skips that with `--bundle`);
- resolves its source, results and tool roots;
- picks a new, unique workspace under its results with checkpoints inside it;
- execs the provider's `stages`, forwarding its output unchanged.

`--stage synthesis|full` selects the Classic path instead and cannot be combined
with `--endpoint`. See the
[consumer quickstart](../../../hardcaml_asic_example/README.md#explicit-stage-run-and-independent-progress-reader).

## Run with the installed command

Choose a **new** workspace whose parent exists. Checkpoints go inside it, and
observations default to `<workspace-parent>/observations`, outside it.

```sh
BUNDLE=/absolute/path/to/published/bundle
TT=/absolute/path/to/support-tools
PDK=/absolute/path/to/pdk
FLOW_PYTHON=/absolute/path/to/flow-venv/bin/python
WORKSPACE=/absolute/path/to/new-workspace

hardcaml-asic-flow stages "$BUNDLE" --endpoint signoff \
  --support-tools "$TT" --pdk-root "$PDK" --python "$FLOW_PYTHON" \
  --workspace "$WORKSPACE" --checkpoint-store "$WORKSPACE/checkpoints" \
  --timeout 7200
```

This is an expensive full implementation. For a short first run, use
`--endpoint floorplan`. `--timeout` applies to each launch and defaults to 600
seconds, which is too short for full signoff. Instead of `--endpoint`, you can
pass `--plan RECIPE.json` with a schema-3 recipe:

```json
{"schema_version":3,"stages":["synthesis","floorplan","placement","cts","routing","signoff"],"initial_boundary":null}
```

The command immediately flushes an allocation receipt naming the observation
attempt, then prints a stage and publication summary. It keeps
`WORKSPACE/execution-<attempt>.json`. Add `--json` for allocation/terminal JSONL;
failures use the
[terminal envelope](flow.md#default-persistent-stage-observation-schema-2-reads-schema-1).
`--observation-store ROOT` moves the journal and `--no-observation` disables it.
Every fresh or resumed command allocates a new stream.

## Watch and inspect

Use the attempt path and stream ID from the receipt. All readers print human
reports by default; add `--json` for machine payloads.

```sh
hardcaml-asic-flow observation watch "$ATTEMPT"        # foreground; Ctrl-C stops only the reader
hardcaml-asic-flow observation watch "$ATTEMPT" --stream-id "$STREAM_ID" --after "$CURSOR" --json
hardcaml-asic-flow observation inspect "$ATTEMPT"
hardcaml-asic-flow observation read-updates "$ATTEMPT" --stream-id "$STREAM_ID" --after 0 --json
hardcaml-asic-flow observation describe --json         # contract and schemas
hardcaml-asic-flow checkpoint-inspect "$FLOORPLAN_CHECKPOINT" --restart-to placement
```

- **Cursors:** save a cursor only after fully processing its `updates` envelope,
  and reconnect with the stream and cursor together.
- **Watch exit codes:** 0 means a terminal outcome was recorded (even a failed
  one), 3 means unresolved writer loss, 2 means a query or corruption error, and
  130 means you pressed Ctrl-C in the reader. See the
  [watch protocol](flow.md#foreground-incremental-watch).
- **`checkpoint-inspect`** validates host closure only. Execution separately
  validates image-only closure and compatibility.
- **Distinct facts:** publication, stage completion, outer outcome and acceptance
  are separate.

## Report

Reading results runs no tools:

```sh
hardcaml-asic-flow report "$WORKSPACE"
hardcaml-asic-flow report "$CHECKPOINT" --verbose     # every retained Yosys cell type
hardcaml-asic-flow report "$WORKSPACE/execution-$ATTEMPT_ID.json" --json
```

A workspace must contain exactly one direct execution record; otherwise name the
execution JSON. The default cell table shows up to 10 rows, and JSON has the full
inventory. See [read-only reporting](flow.md#read-only-reporting).

## Resume from a checkpoint

From a floorplan checkpoint, endpoint shorthand expands placement through signoff:

```sh
hardcaml-asic-flow stages "$BUNDLE" --endpoint signoff \
  --checkpoint "$FLOORPLAN_CHECKPOINT" \
  --support-tools "$TT" --pdk-root "$PDK" --python "$FLOW_PYTHON" \
  --workspace "$WORKSPACE" --checkpoint-store "$WORKSPACE/checkpoints" \
  --timeout 7200
```

Use the **retained original workspace**, the same bundle, tool and PDK roots, and
a compatible runtime. The schema-3 equivalent has stages
`placement, cts, routing, signoff` and `initial_boundary: "floorplan"`.

Allowed restarts:

| From checkpoint | Can resume at |
| --- | --- |
| synthesis | floorplan, placement |
| floorplan | placement |
| placement | cts |
| cts | routing |
| routing | signoff |
| signoff | nowhere (terminal) |

Recipes never skip intervening boundaries; going straight from synthesis to
placement is only a standalone segment capability. Nothing ever picks the newest
checkpoint for you.

## Finalize separately

External checks and acceptance are a separate operation. Only **one completed
fresh six-stage execution** is eligible, so a resumed attempt cannot be finalized
by stitching history.

```sh
hardcaml-asic-flow finalize "$EXECUTION_JSON" --operation-store "$OPERATIONS"
hardcaml-asic-flow finalize "$EXECUTION_JSON" --operation-store "$OPERATIONS" \
  --accept --support-tools "$TT" --precheck-python "$PRECHECK_PYTHON" \
  --testbench "$TESTBENCH" --testbench-top "$TESTBENCH_TOP" --replay-trace "$TRACE"
hardcaml-asic-flow finalization-inspect "$FINALIZATION_ATTEMPT"
```

The first command publishes results with acceptance `not_evaluated`. The second
also runs TT precheck, gate-level replay in the bound image, and the full
acceptance policy. Finalization never reruns implementation or reopens the stage
stream. See [finalization](flow.md#segmented-terminal-results-and-external-acceptance-opt-in).

Keep the observations, execution JSON, checkpoints, operation tree, and the
original input, support and PDK roots and image. All of them are path-bound.

## Not supported

- Archive or GC of segmented artifacts.
- Relocation, native or macro flows, and images other than the tested one.
- Arbitrary DAGs, custom steps or per-stage settings.
- Execution recovery, store-wide idempotency, or fsync durability.
- Workbench application integration.

Real six-stage physical, observed and accepted runs are recorded in the
[phase plan](../phase_plan.md#explicit-stage-refactor-closeout).
