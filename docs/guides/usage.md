# User workflow

The end-to-end path from a Hardcaml design to preserved evidence. The supported
physical path is deliberately narrow: Tiny Tapeout `6x4`, IHP `ihp-sg13cmos5l`
and LibreLane. Registered memory uses a behavioral simulation model; implementation
can select flops or an exact technology macro within the
[qualified SRAM scope](segmented-sram.md).

The workflow has three separate layers:

1. **Library:** install one pinned `hardcaml_asic` package, then declare,
   elaborate, simulate and emit a bundle. No tools or PDK are involved.
2. **Environment:** provision the external PDK and toolchain explicitly.
3. **Execution:** run the immutable bundle, collect and interpret the results,
   and archive the evidence.

Each step links to the guide that owns its details. The earlier version of this
page, which used the deprecated `phase4.py` development commands, is in the
[archive](../archive/usage-guide-2026-10-15.md).

## 1. Install an exact revision

Pin a full commit rather than a moving branch:

```sh
ASIC_REV=<full-hardcaml_asic-commit-hash>
opam pin add --no-action hardcaml_asic \
  "git+https://github.com/LeEmperor/hardcaml_asic.git#$ASIC_REV"
opam install hardcaml_asic
```

This installs the OCaml libraries and the `hardcaml-asic-flow` command together.
A consumer Dune stanza uses the wrapped library directly:

```lisp
(executable
 (name asic_bundle)
 (libraries core hardcaml hardcaml_asic))
```

No sibling `../hardcaml_asic` checkout is needed. An isolated-prefix
installation is also supported, and
[`test/package_consumer_smoke.sh`](../../test/package_consumer_smoke.sh)
exercises it. See [consumer installation](consumer-installation.md) for both
routes, and the [consumer dependency workflow](../development/consumer-dependency-workflow.md)
for developing a consumer against a local provider checkout.

## 2. Declare a chip target in the project

A design implements `Chip_target.Design`: Hardcaml `I`/`O` interfaces, a top module
`name`, and `create : Elaboration.Context.t -> Signal.t I.t -> Signal.t O.t`.
Resource constructors receive that context. The excerpt below is not a complete
program. [`examples/tt_bundle_example.ml`](../../examples/tt_bundle_example.ml)
is the maintained runnable example, with complete `Design` modules, all 24 pin
descriptions, source-input enumeration and error handling.

```ocaml
open Core
open Hardcaml
open Hardcaml_asic

let chip_target (design : (module Chip_target.Design)) pinout ~source_inputs =
  Chip_target.create
    ~name:"my_asic"
    ~metadata:
      { title = "My ASIC"
      ; author = "Example author"
      ; description = "Registered input and memory experiment"
      }
    ~design
    ~target:
      { harness = Tiny_tapeout { tiles = T6x4 }
      ; technology = Technology.ihp_sg13cmos5l
      }
    ~flow:(Flow.librelane ~overrides:(Tt_cmos5l.overrides ()) Hardening)
    ~clocks:[ { Clock.port = "clk"; period = Time_float.Span.of_sec (1. /. 48e6) } ]
    ~timing:Timing.empty
    ~pinout
    ~source_inputs
    ~policy:(Resource_policy.create ~default:Flops [] |> Or_error.ok_exn)
    ()
  |> Or_error.ok_exn
```

What the consumer owns:

- **TT boundary:** `Tt_cmos5l.I`/`Tt_cmos5l.O` are optional conveniences, and
  `Tt_cmos5l.overrides ()` supplies the pinned template defaults.
  `Tt_cmos5l.synchronised_reset` and `Tt_cmos5l.gate` are opt-in helpers. Reset
  semantics, behavior while `ena` is low, and arbitration of `uio_out`/`uio_oe`
  remain the consumer's.
- **Pinout:** every `ui`, `uo` and `uio` bit needs a meaningful `Pinout.t`
  description.
- **Timing:** declare the primary clock and any real min/max I/O timing
  assumptions. Do not fabricate board timing.
- **Resource policy** is mandatory:
  - `Flops` requests synthesizable flops even when a macro matches.
  - `Exact` rejects a shape without an exact technology mapping.
  - `Exact_or_flop_fallback` allows flops only as a recorded fallback, never a
    silent one.
  - Optional [`~macro_limits`](resource-preparation.md#limit-the-number-of-selected-macro-instances)
    caps selections per macro name in registration order.

The [target reference](../reference/target-reference.md) defines the accepted
boundary, timing subset, protected settings and helper behavior.

## 3. Simulate and elaborate resources

| Call | Returns | Use |
| --- | --- | --- |
| `Chip_target.elaborate chip_target ~mode:Simulation` | `Elaborated_project.t` | Behavioral resources for Hardcaml simulation |
| `Chip_target.elaborate chip_target ~mode:Implementation` | `Elaborated_project.t` | Synthesizable implementations |
| `Chip_target.elaborate_for_flow chip_target` | `Resolved_flow.t` | Implementation elaboration plus target, interface, timing and adapter validation |

An elaborated project is the circuit plus metadata, intent and resources, not a
synthesis result. A resolved flow adds concrete TT/LibreLane facts and keeps the
original in its `elaborated_project` field. None of these invoke an EDA tool or
read a PDK.

Run the maintained PDK-free memory example:

```sh
dune exec examples/memory_example.exe
dune exec examples/memory_example.exe -- --rtl /tmp/memory_example.v
```

`Single_port_ram` is a whole-word 1RW memory with one-cycle registered reads.
When disabled, its output holds. These are all unspecified: output after a
write, reading an unwritten word, and an out-of-range address. The behavioral
model uses poison to make misuse visible, and the flop implementation has no
reset or initialization. The consumer therefore owns validity, bounds checks,
access gating and response validity, and must reset its own metadata rather than
assume reset clears storage. See the [memory example](memory-example.md) and the
normative [memory contract](../reference/program-memory-contract.md).

## 4. Emit and inspect an immutable bundle

From this repository, with no PDK, into a new directory:

```sh
BUNDLE=$(mktemp -d)/bundle
dune exec examples/tt_bundle_example.exe -- memory "$BUNDLE"
```

In a consumer:

- **Declare every source input** that generation depends on, via
  `Chip_target.create ~source_inputs`. The default `[]` is fine for ordinary
  elaboration, but emission rejects it before constructing the design.
- **Emit** with `Chip_target.emit_bundle chip_target ~source_root ~output_dir`, where
  `source_root` is the *consumer* repository, never the library checkout.
  Optional `?inputs` supplies target inputs. `~include_simulation:false` skips
  the separate simulation elaboration and reuses synthesis sources.
- **Store-based emission:** `Chip_target.emit_bundle_to_store` publishes, or reuses,
  a verified immutable identity-addressed bundle instead of writing a directory.
- **Project CLI:** a consumer typically wraps all this in a small CLI. The
  example's `asics/fifo_demo/cli/chip.exe describe|emit|preflight|run` emits into
  `ROOT/_flow_results/bundles` by default.

See the [bundle guide](bundle-emission.md), including its
[project entry point](bundle-emission.md#project-centered-entry-point).

Inspect `manifest.json` before running anything. The bundle contains:

- synthesis RTL in `src/` and independently elaborated behavioral RTL in
  `simulation/`;
- the generated SDC and LibreLane configuration;
- TT metadata;
- exact copies of the declared source inputs under `inputs/`.

**Never compile the two RTL sets together.**

The manifest identity is the **build identity**. It covers emitted bytes, source
input bytes and Git status, and source revision, so a formatting-only change or a
new commit can change it even when the generated RTL is byte-identical. Each flow
attempt gets a separate random run ID in `run.json`; never use that as the build
identity.

Treat the bundle as immutable. If the configuration or constraints are wrong,
fix the declaration and emit a fresh bundle. Never patch `config.json`, SDC, RTL,
metadata or the manifest.

## 5. Prepare the external environment

Provision with the installed command. No source checkout is needed:

```sh
TOOLCHAIN=/absolute/path/to/asic-toolchain
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN"
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN" --check   # read-only readiness check
```

Provisioning fetches substantial external collateral (TT support tools, the IHP
PDK, LibreLane and precheck venvs, a managed Python/Tcl runtime) and the
LibreLane container image. It needs Bash, Git, Docker and Nix on the host, and is
never part of elaboration or emission. In a checkout of this repository,
`./bootstrap.sh` does the same and also checks the OCaml layer.

See [bootstrapping](bootstrap.md) for prerequisites, disk use, offline and
container options, and troubleshooting. See
[consumer installation](consumer-installation.md#resolve-ttlibrelane-collateral)
for how collateral is resolved and the managed-Python details.

## 6. Preflight, execute and collect

Pass `--toolchain` (or set `TOOLCHAIN`) and the CLI fills in the support-tools,
PDK and venv paths. Results go to `_flow_results/` under your working directory
unless `--results-root` or `--runs` says otherwise.

```sh
hardcaml-asic-flow preflight "$BUNDLE" --toolchain "$TOOLCHAIN"

# Expensive: mapped synthesis. Prints the new run directory.
hardcaml-asic-flow run "$BUNDLE" --toolchain "$TOOLCHAIN" --stage synthesis

RUN=/absolute/path/printed-by-run
hardcaml-asic-flow collect "$RUN"
hardcaml-asic-flow report "$RUN"
```

`--stage full` (much more expensive) adds place, route and signoff. A completed
full run then needs the external postchecks, TT precheck and gate-level
simulation of the final netlist:

```sh
hardcaml-asic-flow postcheck "$RUN" --toolchain "$TOOLCHAIN" \
  --testbench /absolute/path/to/testbench.v --testbench-top TOP
hardcaml-asic-flow collect "$RUN"
hardcaml-asic-flow report "$RUN"
```

`execute` runs that whole fixed sequence in one command, and can archive at the
end:

```sh
hardcaml-asic-flow execute "$BUNDLE" --toolchain "$TOOLCHAIN" --stage full \
  --testbench /absolute/path/to/testbench.v --testbench-top TOP \
  --archive /absolute/path/to/evidence-directory
```

Notes:

- Output is a human summary by default. Scripts must pass `--json` (CLI
  contract 4).
- `run` prints its run directory even on failure, and you can collect a failed
  or partial run.
- Later operations always take an explicit `RUN`; the newest run is never
  guessed.

Beyond a single coarse run:

- **Explicit stages** (synthesis → floorplan → placement → cts → routing →
  signoff) with checkpoints, restart and a live journal you can watch from
  another shell: see the [stage quickstart](stage-quickstart.md).
- **Named flows and parameter profiles** declared by your project: see
  [named flows](named-flows.md).

The [flow guide](flow.md) owns full command details, storage defaults, exit
codes, the `scripts/flow.sh` development driver and the observation schemas.

## 7. Interpret and preserve results

Keep these claims distinct:

- `process_status: completed` means the requested flow stage completed.
- **Mapped synthesis** additionally needs mapped cells/area and zero synthesis
  errors, inferred latches and unmapped instances.
- **Physical timing** needs a full run with setup and hold results at the
  reported corners and no unconstrained modes.
- **Physical acceptance** also needs antenna/DRC/LVS, TT precheck and the
  design-specific final-netlist verification.

Missing metrics remain `null`/`unknown` with a reason; they are never passes or
zeroes. `report` checks the requested stage against both the run record and the
collected results:

- **Synthesis run:** exits 0 only with valid mapped cell/area evidence, no
  collection errors, and explicit zeros for all three synthesis checks.
  Physical timing, signoff and postchecks are labelled *not evaluated*.
- **Full request:** never falls back to the synthesis policy. It must complete
  the full stage and also provide constrained setup/hold timing; passing
  DRC/LVS/antenna; and passing TT precheck and final-netlist verification.
- Unknown or contradictory stages, and absent or malformed required evidence,
  exit nonzero.

Archive one explicit run. The archive keeps the selected run evidence **and** the
complete immutable input bundle:

```sh
EVIDENCE=/absolute/path/to/evidence-directory
hardcaml-asic-flow archive "$RUN" --list                  # preview the selection
hardcaml-asic-flow archive "$RUN" --output "$EVIDENCE"
```

The result has `reports.tar.gz`, `input-bundle.tar.gz`, `input-bundle.sha256`, the
plain JSON records and `archive.json`. The input tarball holds the original
manifest bytes and exactly its declared files, including copied source inputs and
both RTL sets. Every hash is verified on the way in, and undeclared run, tool, PDK
and support files are excluded.

The evidence directory can be verified with no checkout, original bundle, run
directory, PDK or opam switch. The
[restore-and-verify recipe](flow.md#archiving-and-restoring-evidence) shows how.
Re-running the restored bundle still needs the separately recorded toolchain and
collateral. Archives made before complete-input preservation may lack
`input-bundle.tar.gz`, and reports alone cannot reconstruct the generated RTL.

## Diagnostics and limits

- **Unsupported target or shape:** only TT `6x4` with IHP CMOS5L resolves for
  the flow. Other combinations fail before emission.
- **Missing or conflicting policy:** every resource needs exactly one applicable
  policy. Duplicate selectors, unused rules, unsupported `Exact` and unavailable
  exact mappings are errors, not implicit fallbacks.
- **Override conflicts:** clock, generated source list, die/floorplan/PDK values,
  target constraints and `MACROS` are protected. Change their typed owner, not a
  raw LibreLane override.
- **Bundle input errors:**
  - Declared `source_inputs` must be nonempty, relative, existing files inside a
    committed Git source root.
  - An explicit output directory must be new or empty.
  - Identity-addressed stores allow only complete, verified, exact reuse.
- **Environment errors:** preflight reports missing or mismatched tools,
  revisions, hashes, PDK views, support tools, Python and container image. It
  never installs or silently substitutes them.
- **Memory:** see [segmented SRAM](segmented-sram.md) for the supported macro and
  physical stages. Flop synthesis evidence does not prove macro placement or routing.
- **ABC delay print:** the pinned LibreLane ABC renderer passes a `cell/pin`
  token where ABC expects a cell name. That invalidates only ABC's printed delay
  estimate, not mapped cells/area or the post-route OpenSTA results (tracked as
  ASIC T.1).
- **Not yet available:** commercial adapters, other targets and flows, broader
  memory/resource support, and source-artifact identity in `--version` (ASIC P6.5).

Status and history are in the [phase plan](../phase_plan.md); they are not part
of the onboarding route.
