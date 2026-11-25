# Named flows and parameter profiles

A project can declare named flows (methodologies) and parameter profiles, and
any client can discover and preview them through the project CLI, then execute the frozen plan through the installed provider. This
works without Workbench, and Workbench or any other client uses the same public
interfaces. The ownership model is in the
[architecture](../architecture.md#9-named-methodologies-and-profiles).

The current backend recipe is `librelane.classic.v1`, with fresh, contiguous
physical-stage prefixes. Profiles configure supported parameters before bundle
publication; they do not change the backend operation order or enable
configuration-changing checkpoint reuse.

## Example project

The generated starter declares flow `standard` with the `balanced` profile, which leaves both supported settings at their backend defaults. A project may add a delay-focused profile, for example:

| Profile | `SYNTH_STRATEGY` | `SYNTH_ABC_BUFFERING` |
| --- | --- | --- |
| `balanced` (default) | `AREA 0` | `false` |
| `synth_delay` (example addition) | `DELAY 1` | `false` |

The chip target owns its `flows/flow_standard.ml` declaration. In that directory,
only direct `flow_*.ml` modules are entries. Each exports
`create ~additional_profiles` and returns a checked `Flow.Catalog.Entry.t`.
`flows/<flow-id>/profiles/*.ml` modules export `create ()`, returning one
checked `Flow.Profile.t`. `flows/dune` opts this collection into provider
codegen. The generator orders modules by path and emits wiring and the source
inventory under `_build`. A helper module does not register an entry.
The project catalog owns both explicit defaults.

Profile-directory registration is a separate, per-entry opt-in. The entry
must include the exact standalone line `(* hardcaml-asic profile directory v1 *)`
and combine `additional_profiles` with its inline profiles. The generated
starter and newly rendered flows do this. An unmarked
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

Create, commit and build a standalone `first` starter using the
[chip target guide](chip-targets.md#render-a-standalone-project). Run the commands
below from that project's root with the compatible opam switch selected.
To execute, use an already provisioned tool root (for example the absolute
`TOOLCHAIN` path from the README demo):

```sh
ASIC_TOOLCHAIN="$TOOLCHAIN"
hardcaml-asic-flow provision --toolchain "$ASIC_TOOLCHAIN" --check
```

Named execution requires CLI contract 4 with
`cli_contract.capabilities.namedflow1` equal to boolean `true`. Discovery and
description use the linked project declarations and do not look up a provider
executable or launch tools. Refresh/activation and tool provisioning remain
separate operations; existing provisioned `_flow_tools` can be used.

## Discover and describe

```bash
dune exec ./asic_first/project.exe -- flow list --json
dune exec ./asic_first/project.exe -- flow describe --flow standard --json
```

Description includes the flow's profiles and supported parameters. It is not an
execution or a readiness verdict. These commands work without Workbench, Git
inspection, physical tools or project elaboration.

## Preview and run

First preview a concrete selection (requires `jq`):

```bash
RUN=$(mktemp -d)
dune exec ./asic_first/project.exe -- flow plan \
  --flow standard --profile balanced --endpoint synthesis --json \
  > "$RUN/plan.json"

PLAN_ID=$(jq -er '.plan_identity' "$RUN/plan.json")
```

Preview elaborates and inspects the declared inputs to render a concrete bundle
in memory. It exposes effective settings, provenance and identities, but publishes
no execution workspace and runs no EDA tools.

Emit with the same selection and expected identity, then launch the installed provider:

```sh
dune exec ./asic_first/project.exe -- flow emit \
  --flow standard --profile balanced --endpoint synthesis --json \
  --expected-plan-identity "$PLAN_ID" --results-root "$RUN" > "$RUN/emission.json"
BUNDLE=$(jq -er .output_dir "$RUN/emission.json")
hardcaml-asic-flow stages "$BUNDLE" --methodology-plan "$RUN/plan.json" \
  --expected-plan-identity "$PLAN_ID" --workspace "$RUN/synthesis" \
  --checkpoint-store "$RUN/synthesis/checkpoints" \
  --python "$ASIC_TOOLCHAIN/.venv/bin/python" --support-tools "$ASIC_TOOLCHAIN/tt" \
  --pdk-root "$ASIC_TOOLCHAIN/pdk" --json
```

The project resolves again, rejects stale previews before publication and publishes the exact rendered bundle. The provider independently validates the plan and retains a checked snapshot; it does not trust a profile label alone.

After declaring an additional profile, repeat preview and execution with its ID. Do not
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
an error. Consumer CLIs that expose `run --endpoint` and `run --stage` need not consult an ambient active-profile selection.

## Full-flow execution budget

Both plan and emit require an endpoint. For a fresh full flow, repeat the preview and emission with `--endpoint signoff`, a new workspace, and that new plan identity. Pass `--timeout 3600` to `stages` if needed. The timeout is a positive number of seconds per provider segment/publication call (default 600) and does not change plan identity.

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

## Standalone headless clients

Clients can use the public CLI protocol to discover, select, describe, preview, run, observe, report and reconnect without importing private runtime modules. Keep exact project/provider executable paths, roots and plan identities. A client receipt is a cache, not provider authority; reconnect does not replan or relaunch.

## Verification and scope

Two fresh actual synthesis executions passed through the headless workflow:

| Profile | Mapped cells | Mapped cell area (µm²) |
| --- | ---: | ---: |
| `balanced` | 673 | 13041.6048 |
| `synth_delay` (example addition) | 685 | 13277.4012 |

Their generated and backend-resolved parameters matched their profiles, RTL/SDC
hashes were identical, and plan/methodology/bundle identities differed. This is
synthesis evidence, not a timing-improvement claim or named full-flow acceptance.
Subsequent publication-race, client-setup cleanup and timeout-interface corrections
have separate regression checks; the physical record remains its original snapshot.

See [historical methodology payloads](../reference/native-historical-methodology.md).
Source-artifact stamping, arbitrary operation
substitution and configuration-changing checkpoint branches remain separate work.
