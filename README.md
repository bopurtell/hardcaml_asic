# hardcaml_asic

[![Native build and tests (oxcaml)](https://github.com/bopurtell/hardcaml_asic/actions/workflows/ci.yml/badge.svg?branch=oxcaml)](https://github.com/bopurtell/hardcaml_asic/actions/workflows/ci.yml?query=branch%3Aoxcaml)

An OCaml/Hardcaml library and command-line toolkit for declaring ASIC chip targets,
emitting immutable implementation inputs, running an open-source physical flow,
and retaining integrity-checked results. `hardcaml_asic` is an independent
[MIT-licensed](LICENSE) project built on Hardcaml; it is not developed or
maintained by Jane Street. No hardware workbench is required.

## Quick start

### 1. Install the prerequisites and clone

Use Linux with Bash, Git, opam, Python 3.9+, and the usual compiler/build tools.
For physical execution you will also need Docker (working daemon access), Nix,
network access and disk space for the PDK and container image. The synthesis
demo uses a six-worker execution budget; Docker must have at least six CPUs
available. Neither Docker nor Nix is needed for simulation or bundle generation.

The tested OCaml environment uses OxCaml `5.2.0+ox`, Dune `3.24.2`, and
Hardcaml/Core `v0.18~preview.130.106+341`. Ordinary stock OCaml is not the
qualified compiler for this checkout. The package dependencies are declared in
[`hardcaml_asic.opam`](hardcaml_asic.opam).

On Ubuntu, install the host build tools first:

```sh
sudo apt update
sudo apt install -y autoconf bubblewrap build-essential bzip2 ca-certificates curl git \
  libgmp-dev opam pkg-config python3 rsync unzip xz-utils zlib1g-dev
```

Clone the project and select its OCaml environment:

```sh
git clone --branch oxcaml https://github.com/bopurtell/hardcaml_asic.git
cd hardcaml_asic
opam init --bare --no-setup
scripts/ocaml-deps.sh --install
eval "$(opam env --switch=5.2.0+ox --set-switch)"
```

The dependency script creates the switch if missing and installs missing packages
from the public OxCaml and default opam repositories. Compiler creation can take
some time. For reproducible installations, record the Git commit and resolved opam
package versions; the branch and package repositories can change.

### 2. Install the provider

```sh
./install.sh
export PATH="$HOME/.local/bin:$PATH"
hardcaml_asic --version --json
hardcaml-asic-flow --help
```

This installs the libraries and commands into the selected opam switch and links
commands under `~/.local/bin`. The installer refuses conflicting launchers. Use
`./install.sh --prefix DIR` for an isolated installation; then select both its
`bin` directory on `PATH` and `lib` directory on `OCAMLPATH`. See
[consumer installation](docs/guides/consumer-installation.md).

### 3. Simulate and emit an example (no PDK or Docker)

From the checkout:

```sh
dune exec examples/memory_example.exe
DEMO=$(mktemp -d)
BUNDLE="$DEMO/bundle"
dune exec examples/tt_bundle_example.exe -- observable "$BUNDLE"
cat "$BUNDLE/manifest.json"
```

The memory example checks behavioral and flop implementations. The bundle example
emits RTL, SDC, LibreLane configuration, TT metadata and copied source inputs.
Treat the emitted directory as immutable; regenerate rather than edit it.

### 4. Provision the external tools and run synthesis (optional)

Continue in the same shell. Provisioning downloads substantial external collateral.

```sh
TOOLCHAIN="$PWD/.toolchain"
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN"
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN" --check
WORKSPACE="$DEMO/synthesis"
hardcaml-asic-flow stages "$BUNDLE" --endpoint synthesis \
  --python "$TOOLCHAIN/.venv/bin/python" \
  --support-tools "$TOOLCHAIN/tt" --pdk-root "$TOOLCHAIN/pdk" \
  --workspace "$WORKSPACE" --checkpoint-store "$WORKSPACE/checkpoints"
hardcaml-asic-flow report "$WORKSPACE"
```

The command publishes a synthesis checkpoint. See the
[stage quickstart](docs/guides/stage-quickstart.md) for physical endpoints,
restart and acceptance, or the [coarse execution guide](docs/guides/flow.md)
for the separate `run`/`execute` workflow. A completed synthesis is not signoff.

## Supported scope

| | Supported |
| --- | --- |
| Harness | Tiny Tapeout `6x4` |
| Technology | IHP `ihp-sg13cmos5l` (CMOS5L) |
| Flow | LibreLane Classic, pinned container image |
| Memory | Behavioral simulation, explicit flops, exact stock IHP 256×16 SRAM |

Macro execution is bounded: fresh synthesis and coarse full implementation for
the qualified stock R0 profile, plus explicit segmented execution through Timing
with restricted adjacent restarts. Read the [SRAM scope](docs/guides/segmented-sram.md)
before selecting macros. Macro LVS is abstract boundary/interconnect checking,
not internal-device verification; zero-delay replay is not timed-model conformance.
Other technologies, harnesses, macro profiles and commercial flow adapters are not
supported.

## Use from your own project

Pin a full commit of this repository with opam, using the same compatible compiler
switch for the provider and consumer. See [installation](docs/guides/consumer-installation.md)
for pinning and isolated installs. A Dune consumer links `hardcaml_asic`; add
`hardcaml_asic.flow_domain` or `hardcaml_asic.execution` for typed flow and execution APIs.

A design implements `Chip_target.Design`, declares metadata, target, flow, clocks,
timing, pinout, source inputs and a mandatory resource policy, then emits a bundle
through `Chip_target.emit_bundle` or `Chip_target.emit_bundle_to_store`.

```text
Chip_target → Elaborated_project → Resolved_flow → immutable bundle → execution → results/archive
```

Elaboration and emission do not invoke EDA or read a PDK. Build identity and run
identity are separate. Inspect resource selection before EDA with
[resource preparation](docs/guides/resource-preparation.md).

To generate an editable independent Dune project, inspect the deterministic file
descriptors from `hardcaml-asic-starter render --name led-demo`. The renderer writes
JSON to stdout, not files. See [chip targets](docs/guides/chip-targets.md) for
rendering, writing and registering targets and IP libraries.

## Documentation

- [Documentation index](docs/README.md)
- [Running native CI and comparing cache timings](docs/development.md)
- [Standalone FIFO and SRAM examples](https://github.com/bopurtell/hardcaml_asic_example/tree/oxcaml)
- [End-to-end user workflow](docs/guides/usage.md)
- [Consumer installation](docs/guides/consumer-installation.md) and [toolchain provisioning](docs/guides/bootstrap.md)
- [Bundle emission](docs/guides/bundle-emission.md), [execution](docs/guides/flow.md), [explicit stages](docs/guides/stage-quickstart.md), [named flows](docs/guides/named-flows.md)
- [Target reference](docs/reference/target-reference.md), [memory contract](docs/reference/program-memory-contract.md), [architecture](docs/architecture.md)
