# Native build and tests

[Native CI](https://github.com/bopurtell/hardcaml_asic/actions/workflows/ci.yml)
checks pushes and pull requests to `oxcaml`. The pull-request check tests GitHub's
proposed merge revision. Updating that PR cancels its superseded check. Pushes
and manual runs are independent.

## What a green check covers

Every run builds `@install` from scratch. `dune runtest` covers the public
library, bundle emission and identity, timing, inspection, and native CLI
contracts. The additional `@test/sram-segmented` suite drives the stage,
checkpoint and reporting code against fixture tool outputs. These tests do not
invoke physical EDA. Two checks go beyond those fixtures:

- Icarus Verilog compiles and simulates the emitted memory RTL against the
  read/write/hold testbench in `test/memory_example_tb.v`.
- A separate Dune project builds against a disposable installation of the
  package. It exercises the installed starter, previews and emits a bundle,
  and calls the installed flow CLI to inspect it. The user's switch is not the
  installation destination.

For an actual PDK-backed synthesis run, use [the README synthesis
walkthrough](../README.md#4-provision-the-external-tools-and-run-synthesis-optional).
For segmented SRAM on a provisioned host, see [the qualification
guide](guides/segmented-sram.md). These are opt-in, outside the native workflow.

## Run the native checks locally

For a local iteration, use a **fresh checkout** with no `_build` and an
active [supported OxCaml environment](../README.md#quick-start) containing the
dependencies (including `core_unix` for the generated starter) but **no
installed `hardcaml_asic`**. On Ubuntu, the additional
native test tools include `iverilog`, `tcl`, `strace` and `python3-venv`.
From its root:

```sh
python3 -B scripts/ci/test_cache.py
opam exec -- bash scripts/ci/check.sh
```

The first command uses offline stand-ins to exercise the cache lifecycle,
including rejection of partial or contaminated state. The second runs the
actual native builds and tests. A real `@install` and the normal `_build`
layout are important: some contract tests launch the built executables by
their development paths. Using `_build` from a previous checkout can hide
missing dependencies.

One complete local run took **35m20s** with an existing dependency-only switch,
six build jobs, three test jobs and four result-store workers:

| Gate | Local elapsed |
| --- | ---: |
| Clean `@install` | 40s |
| Native `@runtest` | 17m42s |
| Segmented SRAM fixtures (six fresh prefixes, five restarts) | 16m49s |
| Real Icarus RTL simulation | 3s |
| Installed consumer and CLI | 7s |

This excludes provisioning and cache transfer. The hosted job uses two build
jobs and one test job; compare its own phase logs for cold and warm timings.

To reproduce the pinned CI environment instead, run this from the revision to
test. It checks out **Git HEAD** inside a disposable container and does a cold
compiler build. No host switch or Docker socket is passed to the tests.

```sh
image=$(sed -n 's/^ *UBUNTU_IMAGE: //p' .github/workflows/ci.yml)
docker run --rm --platform linux/amd64 --env ASIC_CACHE_KEY=local-cold \
  --volume "$PWD:/checkout:ro" --workdir /checkout "$image" bash -ec '
    bash scripts/ci/container.sh setup
    bash scripts/ci/container.sh provision
    bash scripts/ci/container.sh test'
```

## Check cache reuse on GitHub

The cache holds the private opam switch and its dependency metadata. The exact
key includes the container image, platform, installed system package versions,
opam repository snapshots, [package pins](../scripts/ci/toolchain.env), package
manifests and CI recipes. Neither `hardcaml_asic` itself nor `_build`, test
results, PDKs or flow outputs are restored.

1. Push the workflow to `oxcaml`, or choose **Actions → Run workflow** on that
   branch. Leave the cold benchmark **off**. For a new key, expect a restore
   miss, compiler/dependency provisioning, then a successful cache save **before**
   the project build. A PR can restore an eligible base cache but cannot populate it.
2. Run again at the same revision, or push a source-only change. Look for an
   exact hit, verification of the restored switch, no compiler installation and
   no cache save. The application build and tests must still run. If the key
   differs, compare the image, host packages, toolchain pins and build recipes;
   GitHub may also evict unused caches.
3. For a cold comparison, dispatch on the same revision with **Benchmark a
   cold provisioning** enabled. Restore and save are both skipped; an existing
   warm cache is left alone. Compare total job times including cache transfer,
   as well as provisioning, native build and test timings in the logs.
