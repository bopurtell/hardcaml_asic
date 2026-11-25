# OCaml stage declarations and recipes

Typed OCaml construction of explicit-stage recipes and named methodology plans,
and how they reach the installed `stages` command. For the short command-line
path, see the [stage quickstart](stage-quickstart.md).

The public `Hardcaml_asic.Flow.Stage` and `Flow.Recipe` modules describe the
provider's opt-in explicit-stage execution contract. Their sources are
`lib/flow/stage.ml` and `lib/flow/recipe.ml`, and their `.mli` files are the API
reference. Construction and JSON rendering are pure and require no toolchain.

`Chip_target.create ~flow:(Flow.librelane Hardening)` still selects the target's adapter
settings and technology views. Emit/publish the input bundle with the existing
Project API, and supply a **separate recipe file** to `hardcaml-asic-flow stages
--plan`. The recipe does not enter the bundle manifest or bundle identity. Existing
`Synthesis`/`Hardening`, overrides, bundle schemas, `run` and `execute` retain their
contracts. A recipe does not change project settings or make a synthesis-only
bundle suitable for physical implementation.

## Frozen named methodology launch (`namedflow1`)

Publish the exact `Methodology_plan.Prepared.bundle` retained at preview, and save
`Methodology_plan.to_string (Methodology_plan.Prepared.plan prepared)` as PLANFILE.
Launch through the native CLI:

```sh
hardcaml-asic-flow stages BUNDLE \
  --methodology-plan PLANFILE --expected-plan-identity HASH \
  --support-tools /tools/tt --pdk-root /tools/pdk --python /tools/.venv/bin/python \
  --workspace /results/fresh-attempt --checkpoint-store /results/fresh-attempt/checkpoints \
  --json
```

`HASH` is the preview's `Methodology_plan.plan_identity`, required for every named
launch. `--methodology-plan`, `--endpoint` and ordinary `--plan` are mutually
exclusive. Named launches are fresh-only and reject `--checkpoint`. Runtime paths
and timeout remain request facts outside the plan hash. CLI contract version stays
4; `--json --version` advertises `cli_contract.capabilities.namedflow1: true`.

The native parser uses main-library plan validation and derives the ordinary
schema-4 prefix from the frozen closed backend contract. It checks the actual
bundle manifest identity, settings/config bytes, all inventory digests and source
input associations before probes or staging. It does not consult current consumer
catalogs or reinterpret plan data as executable commands.

For named launches, the allocation/request identifies the pending snapshot path.
After validation, an atomic no-clobber snapshot is published at
`<observation-attempt>/methodology-plan.json` (or the exact preflight operation
directory with `--no-observation`). The preflight receipt/report, prepared metadata
and execution JSON retain its explicit checksummed `methodology` reference. Ready
preflight receipts include that reference. Failure/interruption can retain a checked
plan reference while runtime readiness or physical completion remains false.

Reports and full-flow finalization validate the required sidecar. Removing or
changing the original PLANFILE has no effect; removing or changing the referenced
snapshot is an error. `Stage_execution_record.methodology` and
`Methodology_execution_record.plan` expose the checked historical snapshot to later
consumers. Historical execution JSON without the optional field keeps its old
encoding.

### Historical reports and observation inspection

```sh
hardcaml-asic-flow report /results/attempt/execution-ID.json
hardcaml-asic-flow report /results/attempt/execution-ID.json --verbose
hardcaml-asic-flow observation inspect /results/observations/ATTEMPT --json
```

Named reports show the frozen flow/profile IDs and labels, requested endpoint,
preview plan identity and actual explicitly effective typed settings before the
checkpoint tables. `--verbose` includes complete winning/overridden provenance,
frozen definitions and snapshot reference. Observation inspection presents the
same normalized methodology, even for failed preflight with no workspace, and
returns the retained native request outside the hashed journal records.

These host-only reads validate snapshot and recorded checkpoint contents, including
backend-resolved assigned values. They never consult today's catalog, source plan
or active profile; missing required evidence is exit 2. Unassigned backend defaults
remain unobserved. Old stage/coarse report bytes keep their format; the report API
explicitly identifies absent methodology as `not_recorded`. See the
[native schema/API reference](../reference/native-historical-methodology.md) for
the exact additive payload and old/new presentation boundaries.

## Typed declarations

```ocaml
open Core
open Hardcaml_asic

let recipe =
  Flow.Recipe.create
    ~initial_boundary:Flow.Stage.Floorplan
    [ Flow.Stage.Placement; Cts; Routing; Timing; Signoff ]
  |> Or_error.ok_exn

let () = printf "%s\n" (Flow.Recipe.to_string recipe)
```

This renders exactly schema 4:

```json
{"schema_version":4,"stages":["placement","cts","routing","timing","signoff"],"initial_boundary":"floorplan"}
```

- `Flow.Stage.t`: `Synthesis | Floorplan | Placement | Cts | Routing | Timing | Signoff`.
  `all`, `to_string`, and strict lowercase `of_string` support command frontends.
- `Flow.Stage.capability`: exact backend endpoint, immediate successor,
  standalone restart targets, tested image ID, typed `Path_bound` closure scope
  and `Not_evaluated` acceptance. These are static demonstrated capabilities;
  actual readiness is provider-owned.
- `Flow.Recipe.create ?initial_boundary stages`: nonempty contiguous composition.
  A fresh recipe begins at synthesis. A resume begins immediately after its
  declared source. Repeats, reversal and omissions return `Or_error` before I/O.
- `Flow.Recipe.through ?initial_boundary endpoint`: endpoint shorthand, expanded
  to the same schema-4 sequence. `through Signoff` includes all seven stages;
  `through ~initial_boundary:Floorplan Placement` includes placement only.
  Endpoints at/before the source and terminal sources return errors.
- `Flow.Recipe.compose first second`: joins declarations when the second source
  equals the first endpoint, retaining the first source. For example,
  `through Floorplan` composed with `through ~initial_boundary:Floorplan Signoff`
  gives the fresh seven-stage recipe. This does not stitch historical executions.
- `stages`, `initial_boundary`, `endpoint`, `required_input`, `declarations` expose
  typed facts. Inputs are `Input.Bundle` or `Input.Checkpoint stage`; each ordered
  declaration names its stage, predecessor input and output checkpoint boundary.
  A boundary requirement does not prove readiness of any checkpoint directory.
- `to_yojson` and `to_string` emit only the three strict schema-4 fields. Recipes
  are abstract: callers cannot forge an unchecked record. There is no raw backend
   JSON argument or per-stage settings API.

Composable recipes always include every intervening boundary, matching the
provider's contiguous schema-4 contract (going directly from synthesis to placement
is only a standalone segment capability). Backend successor checkers stay
included: CTS ends at `OpenROAD.CTS` and its later STA/repair work begins routing;
routing ends at detailed routing and its checkers/fill/extraction and extracted STA belong to Timing.
The retired Python `execution_plan` module is no longer installed; use this API or
`stages --plan`.

## Discovering operations and source ownership

The installed pure library `hardcaml_asic.flow_domain` exposes
`Hardcaml_asic_flow_domain`. Dune uses a separate wrapped library and
`include_subdirs qualified`; hardware consumers gain only this Core-dependent
declaration library, not execution dependencies. `Flow.Stage.t` aliases its
`Stage_id.t`, so existing constructors, `Flow.Stage`/`Flow.Recipe` APIs and
recipe schemas remain compatible.

| Source | Namespace / responsibility |
| --- | --- |
| `flow_domain/stage_id.ml`, `registry.ml` | IDs/order and domain operations/restart targets |
| `flow_domain/stages/{synthesis,floorplan,placement,cts,routing,timing,signoff}/recipe.ml` | Maintained standard domain compositions |
| `flow_domain/stages/placement/{global_placement,design_repair,detailed_placement}.ml` | Placement operation contracts |
| `flow_domain/analysis/timing.ml` | Shared pre/mid/post-PNR timing contracts |
| `flow_domain/backends/librelane/mapping.{ml,mli}` | Exact Classic implementation/artifact/gate/setting mapping; derived endpoints/successors |
| `execution/backends/librelane/classic_contract.{ml,mli}` | JSON discovery and selected-runtime validation |

`Registry.operations Flow.Stage.Placement` describes its domain groups.
`Backends.Librelane.Mapping.composition Flow.Stage.Placement` expands them into
21 ordered backend occurrences, including all setup, power/PDN, IO/template,
header/check, STA, repair/manual and detailed-placement work. Flattening all seven
groups gives Classic's 80 occurrences without omissions. Endpoints and immediate
successors are derived from this same mapping; the bridge's literal tables are
still checked inertly against them.

`hardcaml-asic-experimental-plan-inspect recipe.json` returns additive
`operation_groups` (capability discovery) and `operation_sequence` (the requested
contiguous plan). Entries carry stable occurrence IDs, semantic operation keys,
pre/postconditions, effects, native view inputs/outputs, boolean Classic gate
names/defaults and bounded placement-setting types/units/defaults. Configuration
uses the unchanged resolved backend configuration and native validation. These
are descriptive contracts, not per-step override or arbitrary composition APIs.

Gate defaults describe configuration, not observed execution. Entries deliberately
carry null `execution_evidence`; repeated STA occurrences have separate IDs.
Runtime preparation import-probes the exact selected Classic and rejects ordered
ID, implementation, artifact, gate/default or declared placement-setting drift
before workspace staging. Native artifact sets are sorted (upstream diode-step
view order can vary by Python process); step order is exact.

The existing `stages --plan` composition is runnable through protocol v1. Physical
per-step selection/handoffs still require a separate adapter-protocol slice and
selected-runtime execution/artifact comparison against that coarse path. There
are no new durable step checkpoint boundaries.

## Runnable consumer example

A consumer may wrap the typed recipe API above in a small executable that writes a recipe JSON to stdout without a PDK or Docker.
For a fresh implementation, select an existing published bundle and explicit
toolchain roots, then request a new workspace whose parent already exists.
CLI journaling defaults to `<resolved-workspace-parent>/observations`. Keep an
explicit override outside that workspace; `--no-observation` is a mutually
exclusive single-object-output escape hatch. Standalone runner APIs still
choose no store implicitly:

```sh
# BUNDLE and TOOLCHAIN come from the README demo.
TT="$TOOLCHAIN/tt"
PDK="$TOOLCHAIN/pdk"
FLOW_PYTHON="$TOOLCHAIN/.venv/bin/python"
ATTEMPT_ROOT=$(mktemp -d)
WORKSPACE="$ATTEMPT_ROOT/implementation"
OBSERVATIONS="$ATTEMPT_ROOT/observations"
cat > "$ATTEMPT_ROOT/floorplan-recipe.json" <<'JSON'
{"schema_version":4,"stages":["synthesis","floorplan"],"initial_boundary":null}
JSON
hardcaml-asic-flow stages "$BUNDLE" --plan "$ATTEMPT_ROOT/floorplan-recipe.json" \
  --support-tools "$TT" --pdk-root "$PDK" --python "$FLOW_PYTHON" \
  --workspace "$WORKSPACE" --checkpoint-store "$WORKSPACE/checkpoints" \
  --observation-store "$OBSERVATIONS"
```

Execution prints a flushed allocation receipt and a human stage/publication
summary (add `--json` for allocation/terminal JSONL), and keeps an
`execution-<attempt>.json`. Watch, inspect and `checkpoint-inspect` the result as
shown in the [stage quickstart](stage-quickstart.md#watch-and-inspect); the exact
payloads, exit codes and host-only validation semantics are in the
[flow guide](flow.md#default-persistent-stage-observation-schema-2-reads-schema-1).

To resume, save the schema-4 resume recipe shown above to a new JSON file and
pass it as `--plan`, the floorplan publication as
`--checkpoint`, and **the retained original workspace and checkpoint-store paths**
to the same `stages` command. Supply the same bundle/tool roots. The provider
checks the publication's actual source against the recipe and validates complete
runtime/bridge/configuration compatibility. It never chooses a newest checkpoint.

## Execution boundary and limits

This library does not start Python or Docker. Application-owned command execution
is the boundary: write recipe JSON, invoke the installed CLI using argument arrays
in the application's runtime, consume structured receipts/results and preserve
exit/signal semantics. There are no fabricated OCaml lifecycle callbacks, async
handles or phantom checkpoint types. The provider's separate observation reader
CLI supplies inspection and cursor updates; see [flow guide](flow.md) for schemas.

The stage service requires Docker and the exact tested image
`sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
Keep path-bound workspace, inputs, support tools, PDK roots and bridge/image
closure. Native/relocated/other-image handoffs, arbitrary DAGs/ranges,
per-stage configuration and recovered execution are future contracts.
Limited macro execution is described in [segmented SRAM](segmented-sram.md). Static capabilities do not upgrade older checkpoints.

Signoff completion/publication has `acceptance: not_evaluated`. External checks
and acceptance use the separate `finalize` operation. Currently finalization
requires one completed fresh non-macro full execution; composed declarations are
supported, stitching resumed multi-attempt evidence for finalization is not.

## Contract verification

From the provider root, use the selected compatible opam switch:

```sh
dune build @test/ocaml-stage-recipes @test/stage-checkpoint @test/flow-cli
python3 -B test/test_installed_prefix.py
```

The focused tests exercise the actual typed recipe emitter, composition,
endpoint expansion and checkpoint boundaries. The separate installed-prefix
check builds external consumers against a private provider installation and
checks the runtime layout. These checks need no physical EDA tools.
