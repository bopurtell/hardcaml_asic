# Consuming `hardcaml_asic` from another project

One opam package, `hardcaml_asic` (version 0.1.0), installs:

| Library / binary | Use |
| --- | --- |
| `hardcaml_asic` | The wrapped hardware library: `Hardcaml_asic.Project`, `Single_port_ram`, `Bundle`, `Tt_cmos5l`, … |
| `hardcaml_asic.flow_domain` | Stage, catalog, methodology, profile and parameter types, for projects that declare named flows |
| `hardcaml_asic.execution` | Typed OCaml access to execution, records and observation |
| `hardcaml-asic-flow` | The flow CLI, plus `hardcaml-asic-experimental-*` service helpers it routes to |

A consumer writes `(libraries hardcaml_asic)` in its Dune stanza and adds the
sub-libraries only if it needs them. The generated
[`hardcaml_asic.opam`](../../hardcaml_asic.opam) lists the OCaml dependencies.
No registry publication is needed.

**Breaking changes to know about:**

- The public API rename has no aliases: `Build` → `Elaborated_project`,
  `Resolved_build` → `Resolved_flow`, the resolved field `build` →
  `elaborated_project`, and `Bundle.render ?simulation_build` →
  `?simulation_project`
  ([details](../phase_plan.md#public-api-rename-migration-and-evidence)).
- `Project.create ?source_inputs` must be nonempty before emission. Emission goes
  through `Project.emit_bundle` / `Project.emit_bundle_to_store`
  ([entry-point guide](bundle-emission.md#project-centered-entry-point)).
- The installed Python service APIs are retired ([below](#the-installed-hardcaml-asic-flow-command)).

## Pin the library source

Choose a full commit hash containing the package files and record it in your
dependency documentation or lockfile; a branch name can move. With an opam switch
that has the OxCaml/Hardcaml toolchain:

```sh
ASIC_REV=<full-hardcaml_asic-commit-hash>
opam pin add --no-action hardcaml_asic "git+https://github.com/LeEmperor/hardcaml_asic.git#$ASIC_REV"
opam install hardcaml_asic
```

Pinning changes that opam switch, so use a project-local switch for an isolated
dependency set. Upgrade by changing `ASIC_REV` deliberately and rebuilding. The
package does not need to live in a sibling directory.

To build from a local source snapshot without touching the shared switch, install
to an isolated prefix:

```sh
cd /path/to/hardcaml_asic
PREFIX=/tmp/asic-package-prefix
dune build @install
dune install --prefix "$PREFIX" hardcaml_asic
cd /path/to/consumer
OCAMLPATH="$PREFIX/lib${OCAMLPATH:+:$OCAMLPATH}" dune build
```

Record the source snapshot separately; a dirty worktree cannot be identified by a
commit hash. [`test/package_consumer_smoke.sh`](../../test/package_consumer_smoke.sh)
checks this route with a fresh prefix and an unrelated Dune project.

For day-to-day development of a consumer against a local provider checkout, the
example uses an isolated refresh/activation workflow with recorded and development
modes; see the [consumer dependency workflow](../development/consumer-dependency-workflow.md).

## The installed `hardcaml-asic-flow` command

```text
$PREFIX/bin/hardcaml-asic-flow                            native OCaml command
$PREFIX/bin/hardcaml-asic-experimental-*                  native service helpers
$PREFIX/lib/hardcaml_asic/flow/hardcaml_asic_flow/        nine private runtime modules
$PREFIX/lib/hardcaml_asic/flow/hardcaml_asic_flow/data/   toolchain.sh, toolchain.lock, managed-python.json, version.txt
```

The command works through `PATH` or a symlink. It finds its modules and data
relative to its own installed location only, never searches for a source
checkout, and reports an incomplete installation instead of working around it.
**Only the standard `--prefix` layout is supported**: the launcher expects
`../lib/hardcaml_asic/flow` relative to itself.

Host requirements:

| Operation | Needs |
| --- | --- |
| `--help`, `--version`, `collect`, `report`, `archive` | Nothing beyond the installation |
| `provision` | Python ≥ 3.9 (declared as `conf-python-3`), POSIX shell, Bash, Git, `timeout`, usual Unix utilities |
| `preflight`, `run`, `stages`, `postcheck`, `finalize --accept` | Provisioned collateral plus Docker; postcheck also needs Nix |

Installing the package fetches none of the external tools.

### Opt-in strict synthesis task envelope

`HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=6` selects `synthesis-total-tasks-v1` for
fresh segmented/named synthesis and its publication/report collection. It is an
explicit public environment interface; absence preserves the existing transport.
Other values and duplicate entries are rejected. Physical/resumed recipes,
native timing-intent producers, coarse preflight/run and `finalize --accept` are excluded.

Every selected container receives an explicit `--pids-limit=6` in its retained
launch argv, independently of the six-CPU quota and six-worker configuration.
This counts **all** Linux tasks, including Python controllers, statistics threads
and idle executor threads. It therefore bounds native threads to at most six but
does not promise six usable executor slots. Exhaustion is a failed attempt, never
permission to expand the limit. Opt-in `finalize` collects the actual synthesis
checkpoint/netlist/metrics, without fabricating signoff state, GDS or LEF.

Before resource admission, independently bind the provider installation and
capture to a cap receipt verifying both Docker `HostConfig.PidsLimit=6` and the
effective cgroup-v2 `pids.max=6`. A CPU quota or an observer-ready boolean alone
does not establish this envelope. Keep an exclusive EDA interval under the global
budget; no flow activation is implied by selecting this environment variable.

The installed Python runtime is exactly nine modules: `__init__`,
`backend_protocol`, `librelane_bridge`, `stage_metadata_probe`, `probe`,
`runtime_observation`, `runtime_contract`, `managed_python` and `paths`.

**Python API break:** the installed `execution_plan`, `flow_observation`,
`IncrementalReader`, service/collector/report/archive and compatibility CLI
imports are retired. Use the CLI or the `hardcaml_asic.execution` OCaml library.
Their sources remain in the checkout as references. The retired modules are
`__main__`, `archive`, `cli`, `ocaml_backend`, `checkpoint`, `stages`,
`identity`, `execution_transport`, `execution_plan`, `phase4`, `report`,
`presentation`, `result_store`, `runner`, `flow_observation` and
`finalization`. `PYTHONPATH=/path/to/checkout/flow` reaches them, but that is not
installed support. Package 0.1.0 and CLI contract 4 are unchanged because the
CLI contract itself is unchanged.

Until ASIC P6.5, `--version` reports no source revision or checkout state and
does not verify that the linked OCaml library was built from the same artifact as
the command. It says so explicitly in both human and `--json` output.

### Migrating from earlier CLI contracts

`--version --json` reports `cli_contract.version = 4`, which is separate from
package 0.1.0, record schema 1 and source identity. Changes since contract 1:

- **Human output is the default.** Every parsing invocation must pass `--json`.
  Observed `stages` and `finalize` emit JSONL with a flushed early receipt.
  `--verbose` writes only to stderr. This is intentionally not backward
  compatible.
- **Default stores.** `run`/`execute` no longer require `--runs`; they default to
  `<caller-cwd>/_flow_results/runs` (override with `--results-root` or `--runs`).
  Standalone preflight saves an operation and report by default, and
  `--output FILE` writes only that file.
- **Default journals (contract 4).** Fresh and resumed `stages` journal under
  `<workspace-parent>/observations`. Parse the allocation/terminal JSONL, or pass
  `--no-observation`. Caught preparation failures and interruptions deliver a
  defined terminal envelope.
- Unchanged: `collect --json` still writes `results.json`; `collect --stdout` and
  `report` stay read-only; `archive --list --json` is unsupported.

The example accepts exactly contracts 2, 3 and 4 for Classic operations and
requires 4 for `project run --endpoint`. After refreshing a consumer's private
prefix, source its `current/activate.sh` in the shell that runs Dune and the CLI
(a refresh cannot activate its parent shell). The full matrix is in the
[flow guide](flow.md#default-result-store-and-cli-contract-4).

## Resolve TT/LibreLane collateral

`toolchain.lock` supplies exact support-tools and PDK commits and the LibreLane
and Python versions, and ships as the installed command's runtime data. Its
values mirror the library's emitted `manifest.json`, and
[`check_bundle.py`](../../test/check_bundle.py) checks the copy for drift. Once a
bundle exists, the bundle is authoritative, and preflight rejects collateral that
disagrees with it.

```sh
TOOLCHAIN=/path/for/asic-toolchain
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN"
source "$TOOLCHAIN/toolchain-env.sh"
```

- **New venvs** use the pinned **managed Python** by default, kept under
  `$TOOLCHAIN/python/`. `provision --python /path/to/python3.11` is the explicit
  external override. Its major/minor must match the lock, and it must provide
  `venv`, `ensurepip` and headless Tcl. An invalid override fails before anything
  is written, and there is no host-discovery fallback.
- **Existing venvs** are checked in place. A wrong Python version needs explicit
  repair, never automatic recreation.
- **`toolchain-env.sh`** exports `ASIC_TOOLCHAIN`, `TT`, `PDK`, `PDK_ROOT`,
  `FLOW_PY` and `PRECHECK_PY`. Values are safely quoted, and existing nonempty
  environment values win.
- **Execution `--python`** selects the *flow venv interpreter* (normally
  `$TOOLCHAIN/.venv/bin/python`). Its lexical path is kept, including symlinks,
  so the base Python is never selected by accident.

`provision --check --no-container --toolchain "$TOOLCHAIN"` is bounded and
read-only. It does no downloads, installation, root creation, env rewrite,
container execution or Nix evaluation. It checks:

- the LibreLane metadata, import and Tcl;
- the precheck Python, its exact-pinned requirement metadata, and the `klayout`
  import.

Imports run installed package code, so the checks assume trusted tools. Nix
availability is only a nonfatal diagnostic. With container checks enabled, the
official image's Python and LibreLane metadata are checked too
([runtime policy](flow.md#runtime-policy-and-launch-binding)).

A consumer's thin bootstrap should delegate to the installed provider, with no
copied pins, no source fallback and no automatic provider refresh. The example
selects `--toolchain` > `TOOLCHAIN` > its own absolute `_flow_tools/`; see
[consumer check](bootstrap.md#consumer-check).

Validate an emitted bundle against the installed collateral:

```sh
hardcaml-asic-flow preflight "$BUNDLE" --toolchain "$TOOLCHAIN"
```

Preflight checks the bundle's file hashes, external reference hashes and
requested tool revisions and versions. It reports a mismatch instead of silently
using nearby collateral. [Bootstrapping](bootstrap.md) covers provisioning, and
[execution and results](flow.md) covers running. When emitting, pass your **own**
repository as the source root and list your own source inputs. Never pass the
library checkout.

### Managed Python integrity and initial platform

The installed [archive pin](../../flow/hardcaml_asic_flow/data/managed-python.json)
selects:

- CPython **3.11.17 / PBS 20261009**, `x86_64-unknown-linux-gnu`,
  `install_only_stripped`;
- **30,808,475 bytes**, SHA-256
  `89db7607ebadb77a64709d159b86555d995368ec385a2ab33b00477790c543b0`.

Acquisition verifies size and digest before extraction or execution, bounds
extraction, and rejects unsafe paths, links and special members. It probes the
staged runtime and then the relocated final one before publishing a receipt.
Generations are kept and never replaced, including failed ones; repair is an
explicit action.

- **Platform:** initial support is **Linux glibc x86_64 ≥ 2.17**. Atomic
  no-replace publication uses `renameat2` and needs kernel/filesystem support,
  and unsupported publication fails explicitly. The distribution supplies Python
  and Tcl without system packages. OS libraries, certificates, Docker and Nix
  remain separate prerequisites.
- **`--check`** only inspects the receipt and probes readiness.
- **`--offline`** may install from the verified archive cache, fails on a cache
  miss, and never falls back to another interpreter. It is not read-only.
- **Not attestation:** archive integrity plus a local receipt is not P6.5
  source/runtime attestation.

[Runtime evidence](../../evidence/managed-python/actual-runtime.json) records
Tcl 9.0.4, Tk 9.0, OpenSSL 3.5.9 and wheel-only LibreLane 3.1.0.dev3 evaluating
official Tcl. To reproduce that experiment, you must explicitly opt in to
downloads; it is not a read-only check:

```sh
python3 -I -B experiments/managed_python/accept.py \
  --example /absolute/path/to/hardcaml_asic_example --acquire-and-install
```
