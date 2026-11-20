#!/usr/bin/env bash
# Install this checkout using the selected opam switch and the checkout's Dune cache.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./install.sh [--prefix DIR] [--print-prefix]
Default: install the package with opam in the selected switch and link its CLI commands in ~/.local/bin.
--prefix DIR    Install only in DIR (no global links).
--print-prefix  Print the selected absolute prefix without building or installing.
EOF
}
fail() { echo "install.sh: $*" >&2; exit 1; }

prefix=
global=true
print=false
while (($#)); do
  case $1 in
    --prefix) [[ -n ${2:-} ]] || fail '--prefix requires a directory'
              prefix=$2; global=false; shift 2 ;;
    --print-prefix) print=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown option: $1 (see --help)" ;;
  esac
done
switch_prefix=$(opam var prefix)
prefix=$(realpath -m -- "${prefix:-$switch_prefix}")
if "$print"; then printf '%s\n' "$prefix"; exit 0; fi

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
opam exec -- ocamlopt -version >/dev/null

# Do not overwrite unrelated launchers. An identical link from an earlier
# install is already correct, including when repairing a missing target.
check_link() {
  if [[ -L $2 ]]; then
    [[ $(readlink -- "$2") == "$1" ]] || fail "unrelated link already exists: $2"
  elif [[ -e $2 ]]; then
    fail "file already exists: $2"
  fi
}
link() { [[ -L $2 ]] || ln -sT -- "$1" "$2"; }
names=(hardcaml_asic hardcaml-asic-flow)
if "$global"; then
  flow=$(opam var bin)/hardcaml-asic-flow
  targets=${flow%/*}/hardcaml-asic-targets
  for name in "${names[@]}"; do check_link "$flow" "$HOME/.local/bin/$name"; done
  check_link "$targets" "$HOME/.local/bin/hardcaml-asic-targets"
  opam install -j4 --deps-only "$repo/hardcaml_asic.opam"
  # Regenerate the .install manifest even when the package version is
  # unchanged. opam --assume-built installs it (and records file ownership)
  # on every invocation; Dune only rebuilds changed checkout inputs.
  opam exec -- dune build -j4 --root "$repo" --only-packages hardcaml_asic \
    --promote-install-files=true @install
  opam install -j4 --assume-built "$repo/hardcaml_asic.opam"
  [[ -x $flow ]] || fail "opam did not install $flow"
  [[ -x $targets ]] || fail "opam did not install $targets"
  mkdir -p -- "$HOME/.local/bin"
  for name in "${names[@]}"; do link "$flow" "$HOME/.local/bin/$name"; done
  link "$targets" "$HOME/.local/bin/hardcaml-asic-targets"
  printf 'To select its commands: export PATH="$HOME/.local/bin:$PATH"\n'
else
  [[ $prefix != / && $prefix != "$(realpath -- "$switch_prefix")" ]] ||
    fail "choose an isolated install prefix: $prefix"
  flow=$prefix/bin/hardcaml-asic-flow
  check_link "$flow" "$prefix/bin/hardcaml_asic"
  opam exec -- dune build -j4 --root "$repo" --only-packages hardcaml_asic @install
  opam exec -- dune install -j4 --root "$repo" --prefix "$prefix" hardcaml_asic
  link "$flow" "$prefix/bin/hardcaml_asic"
  printf 'To select its libraries: export OCAMLPATH=%q${OCAMLPATH:+:$OCAMLPATH}\n' "$prefix/lib"
  printf 'To select its commands: export PATH=%q:$PATH\n' "$prefix/bin"
fi
printf 'Installed hardcaml_asic into %s\n' "$prefix"
