# Named flows and parameter profiles

A project can declare named flows (methodologies) and parameter profiles, and
any client can discover, preview and run them through the project's CLI. This
works without Workbench, and Workbench or any other client uses the same public
interfaces. The ownership model is in the
[architecture](../architecture.md#9-named-methodologies-and-profiles).

The current backend recipe is `librelane.classic.v1`, with fresh, contiguous
physical-stage prefixes. Profiles configure supported parameters before bundle
publication; they do not change the backend operation order or enable
configuration-changing checkpoint reuse.

## Example project

`hardcaml_asic_example` declares flow `standard` with these profiles:

| Profile | `SYNTH_STRATEGY` | `SYNTH_ABC_BUFFERING` |
| --- | --- | --- |
| `balanced` (default) | `AREA 0` | `false` |
| `synth_delay` | `DELAY 1` | `false` |

FIFO's `asics/fifo_demo/flows/flow_standard.ml` declares the methodology and
uses `standard_profiles.ml` for its two built-in profiles. In that directory,
only direct `flow_*.ml` modules are entries. Each exports
`create ~additional_profiles` and returns a checked `Flow.Catalog.Entry.t`.
`flows/<flow-id>/profiles/*.ml` modules export `create ()`, returning one
checked `Flow.Profile.t`. `flows/dune` opts this collection into provider
codegen. The generator orders modules by path and emits wiring and the source
inventory under `_build`. A helper module does not register an entry.
`project/flow_catalog.ml` still owns both explicit defaults.

Profile-directory registration is a separate, per-entry opt-in. The entry
must include the exact standalone line `(* hardcaml-asic profile directory v1 *)`
and combine `additional_profiles` with its inline profiles. The generated
starter, newly rendered flows and FIFO already do this. An unmarked
hand-written entry still registers its methodology in the collection. Its
profile directory is ignored; Duplicate Profile returns a declaration snippet
for manual registration and leaves the source unchanged.

New chip targets have the same convention. An authoring client queries the
chosen chip's `flow list --json` and the installed starter's
`describe-flow-authoring`. `render-flow` and `render-profile` accept one JSON
request argument and describe an exclusive, single-file addition. A profile
copy contains the source profile's **explicit** assignments, with validated
edits; a null edit drops an assignment. It does not materialize methodology
defaults. For a manual catalog these commands return a snippet and no files.
The host previews/applies the description; rebuild and list the owning chip
to verify registration. Creating a module neither selects a flow nor runs it.

The initial typed parameter surface is synthesis strategy (`AREA 0` through
`AREA 3`, `DELAY 0` through `DELAY 4`) and boolean ABC buffering. Other LibreLane
parameters are not automatically exposed by this catalog version.

## Install and activate the provider

From `hardcaml_asic_example`, in the existing OCaml environment:

```bash
python3 -B scripts/asic_dependency.py refresh \
  --source /absolute/path/to/hardcaml_asic \
  --mode development
source .dev/asic/current/activate.sh

hardcaml-asic-flow --version --json
```

Named execution requires CLI contract 4 with
`cli_contract.capabilities.namedflow1` equal to boolean `true`. Discovery and
description use the linked project declarations and do not look up a provider
executable or launch tools. Refresh/activation and tool provisioning remain
separate operations; existing provisioned `_flow_tools` can be used.

## Discover and describe

```bash
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- flow list --json
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- flow describe --flow standard --json
```

Description includes the flow's profiles and supported parameters. It is not an
execution or a readiness verdict. These commands work without Workbench, Git
inspection, physical tools or project elaboration.

## Preview and run

First preview a concrete selection:

```bash
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- flow plan \
  --flow standard --profile balanced --endpoint synthesis --json \
  > /tmp/fifo-balanced-plan.json

PLAN_ID=$(jq -er '.plan_identity' /tmp/fifo-balanced-plan.json)
```

Preview elaborates and inspects the declared inputs to render a concrete bundle
in memory. It exposes effective settings, provenance and identities, but publishes
no execution workspace and runs no EDA tools.

Launch with the same selection and expected identity:

```bash
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- flow run \
  --flow standard --profile balanced --endpoint synthesis \
  --expected-plan-identity "$PLAN_ID"
```

The project resolves again, rejects stale previews before publication, publishes
the exact rendered bundle, and invokes the public provider. Native execution
independently validates the plan against its bundle and retains a checked snapshot.
It does not trust a profile label alone.

Repeat preview and run with `--profile synth_delay` for the alternative. Do not
reuse the balanced preview identity. Source, catalog, request or Git-provenance
changes can make a preview stale; preview again rather than bypassing the check.

Typed overrides use repeatable `--set KEY=VALUE`, for example
`--set SYNTH_ABC_BUFFERING=true`. Use identical overrides on preview and run.
Unknown keys, duplicate assignments, invalid enums and nonboolean buffering
values are rejected. Generated starter CLIs accept the same options on
`flow plan` and `flow emit`. Quote enum assignments containing spaces, for
example `--set 'SYNTH_STRATEGY=DELAY 1'`.

With no selection flags, the declared catalog default is used. An explicit flow
without a profile uses that flow's default profile. A profile without a flow is
an error. Existing `run --endpoint` and `run --stage` commands keep their existing
behavior; they do not consult an ambient active-profile selection.

## Full-flow execution budget

Both plan and run require an endpoint. For a fresh full flow, preview with
`--endpoint signoff`, then launch with that preview identity and:

```bash
dune exec -j 20 asics/fifo_demo/cli/chip.exe -- flow run \
  --flow standard --profile balanced --endpoint signoff \
  --expected-plan-identity "$PLAN_ID" --timeout 3600
```

`--timeout` is an execution-only, positive integer number of seconds per provider
segment/publication call. It does not change plan identity. Omitting it preserves
the provider's 600-second default, which was too short for the example's earlier
Magic DRC runs. This example must use a **signoff** preview identity, not the
synthesis identity from above.

## Observe and report

Allocation and terminal JSONL retain explicit observation references. Named
terminal output also supplies `execution_file` only after the persisted record
has been validated. Observation inspection exposes that reference for reconnecting
clients; it is null before a record is readable.

```bash
hardcaml-asic-flow observation watch "$ATTEMPT"
hardcaml-asic-flow observation inspect "$ATTEMPT" --json
hardcaml-asic-flow report "$EXECUTION" --verbose
```

Use paths returned by the request, not newest-record searches. Named reports show
frozen flow/profile labels and IDs, effective settings/provenance, and plan/bundle
identities. They do not consult a current catalog to reinterpret an old execution.
Legacy records retain their existing format and have no recorded methodology.

Checkpoint integrity, backend-resolved parameter agreement and required snapshot
references are validated. A successful report query is not an acceptance verdict.
Fresh full executions still use the separate `finalize --accept` operation.

## Standalone headless client

The example's `scripts/headless_flow.py` demonstrates the full public protocol:
discover, select, describe, preview, run, observe, report and reconnect. It needs
explicit project/provider executable paths and roots, imports no provider Python
modules or Workbench packages, and invokes neither Dune nor private helpers at
runtime. Build the project executable beforehand.

See the consumer's
[headless client guide](../../../hardcaml_asic_example/docs/headless_flow.md).
Its `--timeout` is an overall client budget; its separate `--stage-timeout` is
forwarded to the project's per-call provider timeout. A receipt is a client-owned
cache, not provider authority, and reconnect does not replan or relaunch.

## Verification and scope

Two fresh actual synthesis executions passed through the headless workflow:

| Profile | Mapped cells | Mapped cell area (µm²) |
| --- | ---: | ---: |
| `balanced` | 673 | 13041.6048 |
| `synth_delay` | 685 | 13277.4012 |

Their generated and backend-resolved parameters matched their profiles, RTL/SDC
hashes were identical, and plan/methodology/bundle identities differed. This is
synthesis evidence, not a timing-improvement claim or named full-flow acceptance.
Subsequent publication-race, client-setup cleanup and timeout-interface corrections
have separate regression checks; the physical record remains its original snapshot.

See [curated evidence](../../evidence/namedflow/20261015-real-synthesis.json),
[implementation progress](../development/project-flow-catalog-progress.md), and
[historical methodology payloads](../reference/native-historical-methodology.md).
Source-artifact stamping, Workbench application integration, arbitrary operation
substitution and configuration-changing checkpoint branches remain separate work.
