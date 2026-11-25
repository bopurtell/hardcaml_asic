# Bootstrapping the toolchain

Provisioning installs the external collateral the flow needs: TT support tools,
the IHP PDK, LibreLane and precheck venvs, a managed Python/Tcl runtime, and the
LibreLane container image. All of them are pinned by
[`toolchain.lock`](../../toolchain.lock). Provisioning is an explicit environment
operation. It is never part of elaboration or emission, and it does not prove
that a design hardens.

There are two entry points to the same provisioner:

- **`hardcaml-asic-flow provision`** is installed with the package and needs no
  source checkout. Consumers use it, often through a thin `./bootstrap.sh` of
  their own.
- **`./bootstrap.sh` in this repository** also checks the OCaml development layer
  and defaults to `.toolchain/`.

## Quick start

From a checkout of this repository:

```sh
./bootstrap.sh          # opam check + .toolchain/; a few minutes and ~1.5 GB
./bootstrap.sh --check  # read-only; everything should be green
scripts/flow.sh         # the flow driver's help; then build, emit, execute
```

From anywhere, with the package installed:

```sh
hardcaml-asic-flow provision --toolchain /absolute/path/to/tool-root
hardcaml-asic-flow provision --toolchain /absolute/path/to/tool-root --check
```

If the opam switch is missing packages, `./bootstrap.sh` stops and prints the
`opam install` line rather than changing a switch shared with your other
checkouts. `./bootstrap.sh --install-deps` opts in to that change.

The default non-macro coarse runtime probe requests a 20-CPU quota from Docker.
A daemon exposing fewer CPUs rejects it even when the image and PDK pins match.
Use a host/daemon with sufficient CPUs for coarse execution; provisioning alone
is not a runtime-readiness verdict for a bundle.

## Tool-directory ownership

The tool root is any directory you choose: `--toolchain PATH`, or
`TOOLCHAIN=PATH`. This repository defaults to `.toolchain/`. Consumers can choose an absolute, gitignored tool root. Their bootstrap should use the installed provider's pins, with no copied pins, no source fallback or automatic provider refresh.

| Storage | Owner / configuration |
| --- | --- |
| Provider library and `hardcaml-asic-flow` CLI | Installation prefix (opam, or a consumer's isolated generation) |
| `python`, `tt`, `pdk`, `.venv`, `.venv-precheck`, `toolchain-env.sh` | The provisioner, under the selected tool root |
| Host CLI Python ≥ 3.9, OCaml/opam, Git, container runtime | The host; a venv still depends on its base interpreter and system libraries |
| Container images | Docker's (or Podman's) image store, not the tool root |
| Nix packages used by precheck | The Nix store, normally `/nix/store`, not the tool root |
| Bundles, runs and archives | Consumer-selected stores, separate from tools |

One directory groups the managed collateral, but no tool root can hold every
tool, system library, image and Nix object. Deleting the tool root removes none
of the other rows. A tool root may be shared by several consumers.

### Consumer check

From any directory with the installed provider on `PATH`:

```sh
hardcaml-asic-flow provision --toolchain "$TOOLCHAIN" --check
```

The check applies role-specific runtime policy
(host Python 3.11; official image with Python 3.10–3.14). `--no-container` skips
all engine and image queries and does not certify native EDA tools. To validate a
specific bundle, use [preflight](flow.md#runtime-policy-and-launch-binding).

## The two layers

This repository's `./bootstrap.sh` orders two independent scripts. They fail for
unrelated reasons and are fixed by unrelated commands, and either can run on its
own.

| Layer | Script | Owns | Default behaviour |
| --- | --- | --- | --- |
| 1. OCaml | `scripts/ocaml-deps.sh` | The `5.2.0+ox` opam switch and the packages the dune files name | Reports; installs only with `--install` |
| 2. Flow | `scripts/toolchain.sh` | Managed collateral under the selected tool root | Fetches and installs what is missing |

Layer 1 only reports by default because the switch is shared with every other
Hardcaml checkout on the machine. Layer 2 is a development adapter for the same
provisioner that ships in the package as
`flow/hardcaml_asic_flow/data/toolchain.sh`, which `hardcaml-asic-flow provision`
runs.

## What gets provisioned

| Path under the tool root | What | Size |
| --- | --- | --- |
| `python/` | Pinned managed Python/Tcl generation and its verified archive cache | ~31 MB archive plus extracted runtime |
| `tt/` | tt-support-tools, detached at `support_tools_revision` | ~9 MB |
| `pdk/` | IHP-Open-PDK, detached at `pdk_revision`; this is `PDK_ROOT` | ~1.3 GB |
| `.venv/` | `librelane` at `librelane_version` | ~200 MB |
| `.venv-precheck/` | The TT precheck requirements | ~100 MB |
| `toolchain-env.sh` | Generated; see [Overriding paths](#overriding-paths) | — |

Also provisioned is the container image
`ghcr.io/librelane/librelane:<librelane_version>`, which lives in Docker's own
storage. The provisioner and the runtime contract derive the same tag from the
same version, so they cannot disagree about which image a run uses.

The precheck environment cannot share `.venv`. Upstream's
`precheck/requirements.txt` pins its own KLayout, and installing both into one
environment resolves to a version that satisfies neither.

Both checkouts are fetched **by exact hash** with `--depth 1`, never by branch:
the `ihp-sg13cmos5l` branch head has already moved past the pinned support-tools
revision. After checkout, `git rev-parse HEAD` in each directory is the pinned
revision that preflight compares against. The lockfile's `*_branch` entries are a
hint for readers and a fallback for servers that refuse unadvertised objects.

### Python

New venvs get a base interpreter from one of two places:

- **The managed runtime (default):** a pinned CPython/Tcl archive, verified by
  digest before extraction and probed after.
- **`--python BASE`:** an explicit external override. Its major/minor must match
  the lock (currently 3.11), and it must provide `venv`, `ensurepip` and working
  headless Tcl. An invalid override fails before anything is written.

There is no host-discovery or other-minor fallback. Existing venvs are checked in
place, including their LibreLane/precheck metadata. A mismatched one needs
explicit repair; it is never silently recreated.

`provision --python` selects the base for **new venvs**. Preflight and run
`--python` select the **flow venv interpreter** (normally
`$TOOLCHAIN/.venv/bin/python`), and its lexical path is kept so venv symlinks are
honored. Initial managed-runtime support is Linux glibc x86_64 ≥ 2.17; see
[managed Python integrity](consumer-installation.md#managed-python-integrity-and-initial-platform).

## Options

```sh
./bootstrap.sh                  # converge both layers
./bootstrap.sh --check          # report the state of both, change nothing
./bootstrap.sh --install-deps   # also install missing packages into the opam switch
./bootstrap.sh --offline        # reuse what is installed; fetch nothing
./bootstrap.sh --no-container   # skip the Docker/Podman prerequisite and the image
./bootstrap.sh --python /path/to/python3.11   # external base for new venvs
./bootstrap.sh --help
```

`hardcaml-asic-flow provision` accepts `--toolchain`, `--python`, `--check`,
`--offline` and `--no-container`. It uses the bootstrap's human output and
rejects `--json`/`--verbose`.

- `--check` and `--install-deps` contradict each other and are rejected together.
- `--offline` turns any gap into an error instead of a fetch, which is what you
  want when validating a machine that should already be ready.
- `--no-container` is for runs that will use `--native` with locally installed
  EDA executables (synthesis only). It does not test those executables.

Each script also takes its own options, documented in its `--help`:
`scripts/ocaml-deps.sh [--install]` and
`scripts/toolchain.sh [--check] [--offline] [--no-container] [--python BASE]`.

## Checking a machine

`--check` reports gaps without downloads, installations, root creation, bytecode,
index updates or environment rewrites. Python probes are bounded and run with
`-I -B`. Generated environment files are checked as data, never sourced. A fully
provisioned machine ends with:

```
==> Summary
    the toolchain matches toolchain.lock
    source .../.toolchain/toolchain-env.sh, or pass these locations to the flow command
```

Exit codes distinguish a missing tool from a bad network:

| Code | Meaning |
| --- | --- |
| 0 | The toolchain matches `toolchain.lock` |
| 2 | A host prerequisite is missing, or `--check` found a gap |
| 3 | `toolchain.lock` is malformed |
| 4 | A fetch or install could not complete |
| 5 | Something is present at the wrong revision or version |
| 7 | A tool ran and failed |

## Troubleshooting

### `docker is installed but its daemon is not reachable`

The socket is `root:docker` and your session is not in the `docker` group. Check
in this order:

```sh
id -nG | tr ' ' '\n' | grep -x docker        # is this session in the group?
docker info --format '{{.ServerVersion}}'    # can it reach the daemon?
docker run --rm hello-world                  # can it actually run a container?
```

If the group is missing entirely:

```sh
sudo groupadd -f docker && sudo usermod -aG docker "$USER"
sudo snap disable docker && sudo snap enable docker   # snap installs only
```

If the group lists you but `id -nG` does not, your session predates the change:
log out and back in. `newgrp docker` fixes one shell only, and programs started
earlier (an editor and anything it spawns) won't have the group until they
restart.

### Python 3.11 missing, or an existing venv has the wrong minor

The bundle requests the minor version in `toolchain.lock`, and both provisioning
and preflight reject a mismatch. Normal provisioning acquires the managed base
when needed; `--python BASE` selects an external one. Nothing waives a mismatch:
the installed command has no `--allow-python-mismatch`, and recorded waivers are not implemented. Acquiring a new base does not change an existing venv. A
mismatched venv needs explicit repair.

### `<checkout> is at <hash>, not <hash>, and is dirty`

Bootstrap will not move a checkout with uncommitted work, even one it created,
because a debugging edit to the PDK or support tools is not worth losing
silently. Commit, stash or discard the changes there and rerun. Nothing was
changed.

### `nix-shell not found`

Reported, never fatal. TT precheck needs a pinned native KLayout and Magic from
its `default.nix`, which pip cannot supply. Precheck runs later than bootstrap,
in `postcheck`.

### `nix-shell is installed but Nix is not usable`

Bootstrap reports Nix availability as a nonfatal diagnostic. It does not evaluate
Nix or prove daemon access. Postcheck needs a usable Nix, so resolve evaluation or
permission failures before a physical run. A distro Nix restricts its daemon
socket to a group without necessarily adding anyone to it:

```
error: getting status of /nix/var/nix/daemon-socket/socket: Permission denied
```

After a group change, log out and back in rather than using `newgrp`. Postcheck
shells out to both `nix-shell` and `docker`, and `newgrp` sets one primary group,
so a shell that gains `nix-users` that way can lose `docker`.

### `could not list installed packages in switch`

This is an opam failure, reported with opam's own message, and is different from
a missing package. `opam list` exits nonzero with empty output when it fails;
reading that as data would report every package as missing.

## Overriding paths

Provisioning writes `toolchain-env.sh` into the tool root. `scripts/flow.sh`
sources it, and you can source it by hand to run `librelane` or `python`
directly:

```sh
source .toolchain/toolchain-env.sh
```

Generated values are safely quoted, and existing **nonempty** environment values
win over them. The command line wins over both:

```sh
TT=/some/other/tt-support-tools scripts/flow.sh preflight
TOOLCHAIN=/scratch/asic-toolchain ./bootstrap.sh    # provision somewhere else
```

The variables are `TOOLCHAIN`, `TT`, `PDK`, `PDK_ROOT`, `FLOW_PY` and
`PRECHECK_PY`. `scripts/flow.sh` adds its own; see
[execution and results](flow.md#development-driver-scriptsflowsh).

## Pins and drift

The authority for every pin is this repository's OCaml source, because that is
what ends up in an emitted bundle and what preflight checks collateral against:

| Pin | Defined in |
| --- | --- |
| `support_tools_revision` | `lib/platform/target.ml`, `Inputs.reference_cmos5l_6x4` |
| `pdk_revision` | `lib/platform/target.ml`, `Inputs.reference_cmos5l_6x4` |
| `librelane_version` | `lib/elaboration/bundle.ml`, the `requested_tools` object |
| `python` | `lib/elaboration/bundle.ml`, the `requested_tools` object |

`toolchain.lock` is a **copy** of those values, because the provisioner runs
before anything can be built and cannot ask the library. The copy cannot drift:
`test/check_bundle.py` compares it with the emitted manifest's `requested_tools`
and fails `dune runtest` when they disagree:

```
AssertionError: toolchain.lock librelane_version=9.9.9, manifest requested_tools
librelane=3.1.0.dev3; the lockfile is a copy of lib/, update it
```

Repository and branch entries have no counterpart in a manifest and are not
checked. A bundle deliberately pins revisions, not fetch locations, so the same
declaration means the same thing on any machine and behind any mirror.

To change a pin:

1. change it in `lib/platform/target.ml` or `lib/elaboration/bundle.ml`;
2. change it in `toolchain.lock`;
3. run `dune runtest`, which fails if you changed only one of the two;
4. run `./bootstrap.sh`, which re-resolves the affected checkout or environment
   and leaves the rest alone.

## What bootstrapping does not do

- It does **not** prove that a design hardens, meets timing, passes physical
  verification or passes precheck. Those are flow operations with their own exit
  codes.
- It does not install host `opam`, `python3`, `git`, a container runtime or a
  daemon, or put anything on your `PATH`. Those are reported as prerequisites,
  along with the command that fixes each.
- It does not change the opam switch unless you pass `--install-deps`.
- Native EDA capability checks and Podman runner generalization are not
  implemented.
