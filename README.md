# hardcaml_asic

An OCaml/Hardcaml library and installed flow CLI that takes a Hardcaml design to
a reproducible ASIC implementation input, runs it through an open-source flow,
and keeps traceable, integrity-checked results.

One opam package (`hardcaml_asic`, version 0.1.0) installs:

- the **hardware library** `hardcaml_asic`: project declaration, registered
  resources (memory), target/constraint resolution and bundle emission;
- **[`hardcaml-asic-targets`](docs/guides/chip-targets.md)**: list and invoke
  independent chip entry points in a Dune project;
- **`hardcaml-asic-starter`**: render an editable Tiny Tapeout register project
  for a caller such as Silver to preview and publish;
- the **flow domain** `hardcaml_asic.flow_domain`: stages, operations, named
  methodologies, typed parameter profiles and the checked LibreLane mapping;
- the **execution library** `hardcaml_asic.execution`: preflight, run, stage
  checkpoints, observation/journals, collection, reporting, finalization and
  archives;
- the **`hardcaml-asic-flow` command**, a native OCaml CLI over that execution
  library, plus a thin installed Python runtime used only for the LibreLane
  bridge, probes and provisioning.

A consumer pins one revision and gets the library and CLI together.

## Install the provider

From a source checkout with a compatible OCaml switch and the package's build
dependencies available:

```sh
./install.sh
hardcaml_asic --version --json
```

The default installation makes the libraries available to projects in the
selected opam switch and the CLI available on `PATH`. With compatible dependencies
already installed, this builds the **ASIC package**, not the compiler or the
switch's copy of Hardcaml. Use the second command from any directory to see
which CLI contract the installed provider advertises. Installing the package
is separate from provisioning the PDK,
LibreLane and physical-flow tools (`./bootstrap.sh`).

For an experimental checkout, install under a different prefix instead of
replacing the normal installation:

```sh
./install.sh --prefix "$HOME/.local/opt/asic-dev"
"$HOME/.local/opt/asic-dev/bin/hardcaml_asic" --version --json
```

Use the same compatible compiler switch for a provider and the hardware
project that links its OCaml libraries. When testing a different release, pick
its executable *and* libraries together; changing which CLI is on `PATH` does
not by itself change the package Dune uses to compile a circuit.

## Supported scope

The path is deliberately narrow:

| | Supported |
| --- | --- |
| Harness | Tiny Tapeout `6x4` |
| Technology | IHP `ihp-sg13cmos5l` (CMOS5L) |
| Flow | LibreLane Classic, through the pinned container image |
| Memory | Behavioral simulation, explicit flops, or exact IHP 256×16 SRAM through fresh synthesis/full implementation |

The [independent memory example](../hardcaml_asic_example/memory/PHYSICAL.md)
passes physical/electrical checks, TT precheck and final-netlist zero-delay replay
through the public API. The supported physical profile is stock 256×16, R0,
checked Metal4 power access and real-GDS CMOS5L KLayout, with explicit abstract
macro-boundary LVS scope. Macro checkpoints remain open; timed-model conformance
is optional/deferred and does not block adoption.
See the [physical closeout](docs/development/sram-example-physical.md) for evidence
and limits, including the narrowly dispositioned raw Magic findings.

Other targets/flows and commercial adapters are not provided. A completed process
is **not** a signoff verdict; acceptance is a
separate, explicit check (see [interpreting results](docs/guides/usage.md#7-interpret-and-preserve-results)).

## How it fits together

**Inspect before EDA:** a project CLI can call `Project.prepare_resources` through
the durable preparation service, then use `hardcaml-asic-flow preparation-inspect`
to browse the saved resource decisions. This is a preparation operation, separate
from physical stages, with no bundle emission or PDK requirement. See
[resource preparation](docs/guides/resource-preparation.md) and the
[independent example commands](../hardcaml_asic_example/docs/resource_preparation.md).

```text
Project.create  ──►  Elaborated_project  ──►  Resolved_flow  ──►  bundle  ──►  execution  ──►  results / archive
 (declaration)       circuit, metadata,       validated TT/         immutable      separate run     collect, report,
                     intent, resources        LibreLane facts       inputs +       ID and records   finalize, archive
                     (no synthesis)                                 manifest
```

Elaboration and emission never invoke an EDA tool or read a PDK. The emitted
bundle is immutable and identity-addressed: if the configuration is wrong, fix
the declaration and emit again. Each flow attempt gets its own run ID, separate
from the bundle's build identity.

Execution comes in three forms, all through `hardcaml-asic-flow`:

- **Coarse runs**: `run`/`execute` with `--stage synthesis|full`.
- **Explicit stages**: `stages` with `--endpoint` across six sequential
  boundaries (synthesis → floorplan → placement → cts → routing → signoff), with
  checkpoints, restart and a journal you can watch live from another shell.
- **Named flows**: project-owned flow catalogs and parameter profiles (for
  example `standard/balanced` and `standard/synth_delay`), previewed to a plan
  identity and then run against that identity.

## Quick start

### Render a starter (no build or EDA)

After `./install.sh`, run `hardcaml-asic-starter render --name led-demo`.
The name must match `[a-z][a-z0-9_-]{0,63}`. Hyphens become underscores in
Dune and HDL identifiers. The command writes a deterministic JSON object to
stdout with `schema_version: 1`, `provider: "hardcaml_asic"`, `version: "0.1.0"`
and `files`, an array of relative `path` and UTF-8 `contents` strings. It takes
no destination and creates no files. Invalid arguments give exit status 2 and
a diagnostic on stderr. The caller chooses and checks the destination.

The generated README explains how to edit the design, build with Dune, run
its register/reset simulation, and print implementation RTL explicitly.
For an existing Dune root, `hardcaml-asic-starter render-existing --name led-demo`
uses the same descriptor format but places the design under `asic_led-demo/`.
It supplies no `dune-project`. Its `project.exe` exposes chip discovery and
the standard flow's `list`, `describe`, `plan` and `emit` commands. Silver
previews and publishes the corresponding host-specific ASIC and preparation
configuration and chip catalog registration. Build `asic_led-demo/project.exe`
before discovery. Planning and bundle emission use Git HEAD for provenance.
To verify the installed starter end to end from this checkout, run
`STARTER_TEST_CHECKOUT_DIR="$PWD/_build" python3 starter/test_render.py "$(command -v hardcaml-asic-starter)" --consumer`.

### Add an IP library or module to a Dune project

Install the provider with `./install.sh` in the selected opam switch. To
inspect exactly what Silver will create, run:

```sh
hardcaml-asic-starter render-ip --name decoder --template sample
hardcaml-asic-starter render-module --name priority
```

Both render deterministic file descriptors as above, without touching the
working tree. Use names matching `[a-z][a-z0-9_]{0,63}`. IP paths are
relative to the project root: `ip/decoder/dune` declares a private Dune
library named `ip_decoder`; its initial module is `block.ml`. `sample` provides
a bitwise inverter and a Hardcaml Cyclesim test for three eight-bit inputs.
`empty` provides an editable module and test stub. After creation, run:

```sh
dune build ip/decoder/ip_decoder.cmxa
dune runtest ip/decoder
```

To use the sample in another library in the same project, add `ip_decoder` to
its `(libraries ...)` and call `Ip_decoder.Block.create signal`. Module rendering
produces just `priority.ml`, relative to the selected library directory
(e.g. `ip/decoder/priority.ml`). The generated Dune stanza automatically
includes new modules. If a custom stanza lists modules explicitly, include
`priority` in that list yourself.

### Library only (no PDK, no Docker)

```sh
dune build
dune runtest

# PDK-free memory example: behavioral vs flop checks, optional RTL dump
dune exec examples/memory_example.exe
dune exec examples/memory_example.exe -- --rtl /tmp/memory_example.v

# Emit an immutable TT bundle (observable | memory) into a new directory
BUNDLE=$(mktemp -d)/bundle
dune exec examples/tt_bundle_example.exe -- memory "$BUNDLE"
```

Inspect `$BUNDLE/manifest.json`: it lists synthesis RTL (`src/`), separately
elaborated simulation RTL (`simulation/`), generated SDC and LibreLane config,
TT metadata, and copies of the declared source inputs.

### Provision the toolchain and run a flow

Provisioning is explicit and fetches substantial external collateral (PDK, TT
support tools, LibreLane image, managed Python/Tcl). It needs Bash, Git, Docker
and Nix on the host.

```sh
./bootstrap.sh            # opam check + .toolchain/ (a few minutes, ~1.5 GB)
./bootstrap.sh --check    # read-only readiness check
scripts/flow.sh           # prints help; starts nothing
scripts/flow.sh build
OUT=/tmp/p4-observable scripts/flow.sh emit
OUT=/tmp/p4-observable STAGE=synthesis scripts/flow.sh execute
```

`scripts/flow.sh` is a development driver for this repository's examples; after
emission it hands every operation to the same implementation the installed
`hardcaml-asic-flow` runs. `STAGE=full` adds place, route, signoff and the
postchecks, and is much more expensive. See [bootstrapping](docs/guides/bootstrap.md)
and [execution and results](docs/guides/flow.md).

### The installed command

```text
hardcaml-asic-flow <command> [options]

commands: provision, preflight, run, postcheck, collect, report, archive,
          execute, stages, finalize, finalization-inspect, checkpoint-inspect,
          observation
```

Output is a human summary by default; machine callers pass `--json` (CLI
contract 4). `--help`, `--version`, `collect`, `report` and `archive` need no
PDK, container or provisioning. Results default to `_flow_results/` under the
caller's working directory. See [consumer installation](docs/guides/consumer-installation.md)
for pinning, the installed layout and collateral resolution.

## Using it from another project

Pin a full commit and depend on the wrapped library:

```sh
opam pin add --no-action hardcaml_asic \
  "git+https://github.com/LeEmperor/hardcaml_asic.git#<full-commit-hash>"
opam install hardcaml_asic
```

```lisp
(executable
 (name asic_bundle)
 (libraries core hardcaml hardcaml_asic))
```

A design implements `Project.Design`; the project declares metadata, target,
flow, clocks, timing, pinout, source inputs and a mandatory resource policy, then
calls `Project.emit_bundle` or `Project.emit_bundle_to_store`. The
[user workflow](docs/guides/usage.md) walks through declaration, simulation,
emission, execution and archiving end to end.

The sibling [hardcaml_asic_example](../hardcaml_asic_example/README.md) is the
development specimen: a 16×8 controlled FIFO with an independent scoreboard, TT
wrapper, a project CLI (`describe | emit | preflight | run | flow plan | flow run`),
a declared `standard` flow catalog with two profiles, and a standalone headless
client. It consumes this repository through an isolated refresh/activation
workflow; see [consumer dependency workflow](docs/development/consumer-dependency-workflow.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `lib/` | Hardware library `hardcaml_asic`: qualified groups `resource/`, `flow/` (project flow selection, `Stage`, `Recipe`, plan JSON), `platform/` (targets, `Tt_cmos5l`, pinout, clock, timing) and `elaboration/` (`Elaborated_project`, `Resolved_flow`, `Bundle`); root modules `Project`, `Methodology_plan`, `Single_port_ram`; `hardcaml_asic.ml` is the public surface |
| `flow_domain/` | Flow domain library: stage recipes (`stages/`), LibreLane mapping (`backends/librelane/`), catalog, methodology, profiles, parameters |
| `execution/` | Execution library: runner, runtime/process supervision, storage and checkpoints, observation, results, archive, LibreLane backend protocol |
| `bin/` | `hardcaml-asic-flow` and the `hardcaml-asic-experimental-*` service helpers it routes to |
| `flow/hardcaml_asic_flow/` | Installed Python runtime (LibreLane bridge, probes, managed Python, provisioning data); retired service modules are kept as source-only references |
| `examples/` | `memory_example` and `tt_bundle_example` |
| `scripts/` | Development adapters: `flow.sh`, `toolchain.sh`, `ocaml-deps.sh`, legacy Python entry points |
| `test/` | Expect tests, OCaml consumers driven by Python checks, provider observation fixtures, installed-prefix and packaging tests |
| `evidence/` | Curated records of real synthesis, physical, observation and SRAM-investigation runs |
| `experiments/` | Probes and feasibility work (LibreLane observation, SRAM, managed Python) |
| `docs/` | Plans, architecture, guides, references, and history |
| `bootstrap.sh`, `toolchain.lock` | Toolchain provisioning and its pinned revisions |

## Status

As of 2026-10-25:

- **Delivered:** project declaration through bundle emission; native OCaml CLI
  and execution services (coarse runs, explicit stages with checkpoints,
  default journals and live watch, collection, reporting, postcheck,
  finalization, archives); default result stores; managed Python/Tcl
  provisioning; named flows/profiles with plan-identity enforcement; and exact
  256×16 SRAM fresh synthesis/full execution with the checked physical profile.
- **Resource preparation:** PT.4 provides structured pre-run inventory, durable
  Elaborate & Resolve attempts, historical inspection and explicit identity
  comparison. CLI/library acceptance is complete independently of Workbench.
- **Evidence:** a fresh installed consumer completed all six physical stages
  plus external acceptance (DRC/LVS/antenna, TT precheck, gate-level replay);
  the `balanced` and `synth_delay` profiles each passed real synthesis. See
  [`evidence/`](evidence/) and the [phase plan](docs/phase_plan.md).
- **SRAM evidence:** the independent example passes physical/electrical checks,
  TT precheck and routed-netlist functional replay. PT.0c and PT.2 are closed
  for the supported profile; see the [physical closeout](docs/development/sram-example-physical.md).
- **Next:** adopt the backend in Protemu (PT.3a), then verify its real system;
  expose selected/realized resource mappings to Workbench, then extend
  timing declarations and reproducible configuration studies. See the
  [active queue](docs/phase_plan.md#active-queue).
- **Open:** source-artifact stamping and independent-installation gates (ASIC
  P6.5/P6.6), committed-pin adoption by the example, and configuration-changing
  checkpoint reuse. These retain their historical status in the
  [previous plan](docs/archive/phase-plan-2026-10-24.md); they are not the current queue.

The public lifecycle was renamed to `Elaborated_project`/`Resolved_flow` with no
aliases; see the [migration notes](docs/phase_plan.md#public-api-rename-migration-and-evidence).
Installed Python service imports are retired; use the CLI or
`hardcaml_asic.execution` instead.

## Documentation

- [Documentation index](docs/README.md): where everything lives.
- [Phase plan](docs/phase_plan.md): the one active queue, status and evidence.
- [Architecture](docs/architecture.md): ownership and boundaries; read this
  before changing an interface.
- Guides: [user workflow](docs/guides/usage.md) ·
  [installation](docs/guides/consumer-installation.md) ·
  [bootstrapping](docs/guides/bootstrap.md) ·
  [bundle emission](docs/guides/bundle-emission.md) ·
  [execution and results](docs/guides/flow.md) ·
  [stage quickstart](docs/guides/stage-quickstart.md) ·
  [OCaml stage recipes](docs/guides/ocaml-stage-recipes.md) ·
  [named flows](docs/guides/named-flows.md) ·
  [memory example](docs/guides/memory-example.md)
- References: [TT/CMOS5L target](docs/reference/target-reference.md) ·
  [memory contract](docs/reference/program-memory-contract.md)
- [SRAM investigation](docs/sram/README.md) · [backlog](docs/backlog/README.md) ·
  [archive](docs/archive/README.md)
