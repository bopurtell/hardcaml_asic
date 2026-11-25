#!/usr/bin/env bash
# OCaml layer of the bootstrap: the opam switch and the packages dune needs. Everything
# else in this repository goes through dune, and dune needs this, so it is plain shell
# with no dependency on the repository having been built. See bootstrap.sh.
#
# Usage:
#   scripts/ocaml-deps.sh              # check and print the commands that fix gaps
#   scripts/ocaml-deps.sh --install    # create the switch and install what is missing
#
# The switch is shared with every other Hardcaml checkout on the machine (the reference
# setup has a dozen under ~/devel/jane), so the default is to check and report. Changes
# happen only with --install, and only ever add: nothing here upgrades, downgrades or
# recompiles a package another checkout depends on.
#
# This repository has a generated opam package for consumers. The bootstrap check
# still names all repo build/test dependencies, including examples and test libraries,
# because it checks the shared switch without installing the package into it.
#
# Careful: the list has to be updated by hand when a dune file gains a library. The cost
# of missing one is a clear dune error at build time ("Library X not found"), not a wrong
# result, so it is checked rather than generated.
#
# Exit codes:
#   0  the switch exists and has every package
#   2  something is missing; the commands that fix it are printed

set -euo pipefail

readonly EX_PREREQ=2

SWITCH="${OPAM_SWITCH:-5.2.0+ox}"
readonly SWITCH
readonly OX_REPOSITORY=git+https://github.com/oxcaml/opam-repository.git

# Every package named by a dune file in this repository:
#   core hardcaml yojson cryptokit  lib/dune (libraries)
#   ppx_jane ppx_hardcaml           lib/dune, test/dune, examples/dune (preprocess)
#   expect_test_helpers_core        test/dune (libraries)
#   ocaml dune                      the toolchain itself
# unix is part of the compiler distribution and is not an opam package, so it is absent.
readonly PACKAGES=(
    ocaml dune
    core hardcaml yojson cryptokit zarith spawn conf-python-3
    ppx_jane ppx_hardcaml
    expect_test_helpers_core
)

install=0

usage() {
    cat <<'USAGE'
Usage: scripts/ocaml-deps.sh [--install]

Checks that the OxCaml opam switch exists and has the packages this repository's dune
files name. Changes nothing unless --install is given.

Options:
  --install    Create the switch if it is missing (about 30 minutes) and install missing
               packages into it. Installed packages are never upgraded or recompiled.
  -h, --help   Show this message.

Environment:
  OPAM_SWITCH  Switch to use. Default: 5.2.0+ox
USAGE
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --install) install=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
    shift
done

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }

die() {
    local code=$1
    shift
    printf '\nerror: %s\n' "$1" >&2
    shift
    local line
    for line in "$@"; do printf '       %s\n' "$line" >&2; done
    exit "$code"
}

step "Checking the OCaml layer (opam switch $SWITCH)"

command -v opam >/dev/null 2>&1 || die "$EX_PREREQ" "opam is not installed" \
    "Install it with your package manager (apt install opam), then run:" \
    "  opam init --bare --no-setup"

opam var root >/dev/null 2>&1 || die "$EX_PREREQ" "opam is not initialised" \
    "Run: opam init --bare --no-setup"

if ! opam switch list --short 2>/dev/null | grep -qx "$SWITCH"; then
    create=(opam switch create "$SWITCH" "ocaml-variants.5.2.0+ox"
            --repos "ox=$OX_REPOSITORY,default" --yes)
    if [[ $install -eq 0 ]]; then
        die "$EX_PREREQ" "opam switch '$SWITCH' does not exist" \
            "Rerun with --install to create it (about 30 minutes), or run:" \
            "  ${create[*]}"
    fi
    info "creating switch $SWITCH; this takes about 30 minutes"
    "${create[@]}"
fi

version=$(opam exec --switch="$SWITCH" -- ocamlc -version)
case $version in
    5.2.0+ox*) info "OCaml $version" ;;
    *) die "$EX_PREREQ" "switch '$SWITCH' has OCaml $version, expected 5.2.0+ox" ;;
esac

# Careful: opam's exit status is checked rather than dropped, and its stderr is kept.
# `opam list` exits nonzero with EMPTY stdout when it fails at all (a stale lock, an
# unreadable root, a switch that vanished mid-run), and empty stdout read as data means
# "no package is installed" -- which would report every package as missing and send the
# reader off to reinstall a switch that was fine. An opam failure is reported as an opam
# failure instead.
listing=$(opam list --switch="$SWITCH" --installed --short "${PACKAGES[@]}" 2>&1) || \
    die "$EX_PREREQ" "could not list installed packages in switch '$SWITCH'" \
        "opam exited $? and said:" "$listing"

mapfile -t installed <<<"$listing"
missing=()
for package in "${PACKAGES[@]}"; do
    printf '%s\n' "${installed[@]}" | grep -qx "$package" || missing+=("$package")
done

if [[ ${#missing[@]} -eq 0 ]]; then
    info "all ${#PACKAGES[@]} opam packages installed"
    exit 0
fi

if [[ $install -eq 0 ]]; then
    die "$EX_PREREQ" "missing opam packages: ${missing[*]}" \
        "Rerun with --install, or run:" \
        "  opam install --switch=$SWITCH ${missing[*]}"
fi

info "installing ${missing[*]}"
opam install --switch="$SWITCH" --yes "${missing[@]}"
