# Documentation

Start with the [standalone quick start](../README.md#quick-start), then follow the
[user workflow](guides/usage.md) from declaration to preserved evidence.

## Setup and design

| Guide | Covers |
| --- | --- |
| [Consumer installation](guides/consumer-installation.md) | Pinning, isolated installs, CLI and runtime layout |
| [Toolchain provisioning](guides/bootstrap.md) | Tool roots, pins, readiness and troubleshooting |
| [Chip targets](guides/chip-targets.md) | Starter generation and independent targets in Dune |
| [Bundle emission](guides/bundle-emission.md) | Project API, immutable inputs, identity and provenance |
| [Memory example](guides/memory-example.md) | PDK-free behavioral and flop checks |
| [Resource preparation](guides/resource-preparation.md) | Durable resource selection before EDA |
| [Timing intent](guides/timing-intent.md) | Bounded timing declarations, exact endpoint resolution and audit |

## Execution and inspection

| Guide | Covers |
| --- | --- |
| [Execution and results](guides/flow.md) | CLI, storage, collection, reporting, acceptance, archives |
| [Explicit-stage quickstart](guides/stage-quickstart.md) | Checkpoints, restart and live observation |
| [OCaml stage recipes](guides/ocaml-stage-recipes.md) | Typed stage and recipe declarations |
| [Named flows](guides/named-flows.md) | Catalogs, profiles, preview, execution and plan identity |
| [Segmented SRAM](guides/segmented-sram.md) | Qualified stock macro scope and restrictions |
| [Resource realization](guides/resource-realization.md) | Reconciliation, area, sampled timing/electrical evidence |
| [Bounded resource inspection](guides/resource-inspection.md) | Source-bound detail and artifact pages |
| [Variants and comparison](guides/variant-comparison.md) | Hardware assignments, evidence comparison and checked workloads |

## Reference

- [Architecture](architecture.md): public boundaries and ownership.
- [TT/CMOS5L target](reference/target-reference.md): accepted target, timing subset and protected settings.
- [Program memory contract](reference/program-memory-contract.md): normative `Single_port_ram` behavior.
- [Historical methodology payloads](reference/native-historical-methodology.md): named execution reporting/observation schema.
