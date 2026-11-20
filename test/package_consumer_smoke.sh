#!/usr/bin/env bash
# Build this checkout as an installed package, then link it from an unrelated project.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/hardcaml-asic-consumer.XXXXXX")
trap 'rm -rf "$stage"' EXIT

OPAMROOT=$(opam var root)
OPAMSWITCH=$(opam switch show)
export OPAMROOT OPAMSWITCH
export HOME="$stage/home"
mkdir "$HOME"
cd "$stage"
installer=$repo_root/install.sh
export PATH="$HOME/.local/bin:$PATH"
verify_cli() {
  "$1" --version --json | python3 -c '
import json, pathlib, sys
i = json.load(sys.stdin)
assert i["tool"] == "hardcaml-asic-flow" and i["toolchain_lock_present"]
assert pathlib.Path(i["module_dir"]) == pathlib.Path(sys.argv[1])/"lib/hardcaml_asic/flow/hardcaml_asic_flow"
' "$2"
}
other="$stage/another prefix"
"$installer" --prefix "$other"
verify_cli "$other/bin/hardcaml_asic" "$other"
[[ ! -e $HOME/.local/bin/hardcaml_asic ]]

mkdir "$stage/consumer"
cat > "$stage/consumer/dune-project" <<'EOF'
(lang dune 3.17)
(name asic_package_consumer)
EOF
cat > "$stage/consumer/dune" <<'EOF'
(executable
 (name main)
 (libraries hardcaml_asic))
EOF
cat > "$stage/consumer/main.ml" <<'EOF'
let () =
  let config =
    Hardcaml_asic.Single_port_ram.Config.create_exn
      ~width:8 ~depth:4 ~read_latency:1
  in
  Printf.printf "address=%d storage=%d\n"
    (Hardcaml_asic.Single_port_ram.Config.address_bits config)
    (Hardcaml_asic.Single_port_ram.Config.storage_bits config)
EOF

cd "$stage/consumer"
export OCAMLPATH="$other/lib${OCAMLPATH:+:$OCAMLPATH}"
for package in hardcaml_asic hardcaml_asic.execution hardcaml_asic.flow_domain; do
  [[ $(opam exec -- ocamlfind query "$package") == "$other"/lib/* ]]
done
opam exec -- dune build -j4
actual=$(_build/default/main.exe)
if [[ "$actual" != 'address=2 storage=32' ]]; then
  printf 'unexpected consumer output: %s\n' "$actual" >&2
  exit 1
fi
printf 'external consumer: %s\n' "$actual"

# Exercise actual opam ownership and refresh with a tiny Dune package.
# All mutating opam commands below must address this disposable root.
unset OPAMSWITCH OPAM_SWITCH_PREFIX OCAMLPATH
export OPAMROOT="$stage/opam" OPAMYES=1
mkdir -p "$stage/empty-repo/packages" "$stage/fixture"
echo 'opam-version: "2.0"' > "$stage/empty-repo/repo"
opam init --bare --no-setup --no-opamrc --disable-sandboxing local "$stage/empty-repo"
opam switch create fixture --empty
export OPAMSWITCH=fixture
prefix=$(opam var prefix)
[[ $prefix == "$stage/opam/fixture" ]]
cd "$stage/fixture"
cp "$installer" install.sh
cat > dune-project <<'EOF'
(lang dune 3.17)
(name hardcaml_asic)
EOF
cat > dune <<'EOF'
(executable (name main) (public_name hardcaml-asic-flow) (package hardcaml_asic))
(install (section bin) (files (main.exe as hardcaml-asic-targets)))
(install (section lib) (files version.txt))
EOF
cat > hardcaml_asic.opam <<'EOF'
opam-version: "2.0"
version: "0.1.0"
synopsis: "Package installation fixture"
maintainer: "Fixture <fixture@example.invalid>"
authors: "Fixture"
homepage: "https://example.invalid/"
bug-reports: "https://example.invalid/issues"
license: "MIT"
EOF
for version in first second; do
  echo "$version" > version.txt
  echo "let () = print_endline \"$version\"" > main.ml
  ./install.sh
  [[ $(hardcaml_asic) == "$version" && $(hardcaml-asic-flow) == "$version" ]]
  [[ $("$HOME/.local/bin/hardcaml-asic-targets") == "$version" ]]
  [[ $(realpath "$HOME/.local/bin/hardcaml-asic-targets") == "$prefix/bin/hardcaml-asic-targets" ]]
  [[ $(cat "$prefix/lib/hardcaml_asic/version.txt") == "$version" ]]
  [[ $(realpath "$HOME/.local/bin/hardcaml_asic") == "$prefix/bin/hardcaml-asic-flow" ]]
done
rm "$HOME/.local/bin/hardcaml_asic"
echo 'unrelated launcher' > "$HOME/.local/bin/hardcaml_asic"
if ./install.sh; then echo 'overwrote conflicting launcher' >&2; exit 1; fi
[[ $(cat "$HOME/.local/bin/hardcaml_asic") == 'unrelated launcher' ]]
opam remove -j4 hardcaml_asic
[[ ! -e $prefix/bin/hardcaml-asic-flow && ! -e $prefix/lib/hardcaml_asic/version.txt ]]
