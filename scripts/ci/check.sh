#!/usr/bin/env bash
# Run in a fresh, isolated source checkout with the qualified opam switch selected.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
repo=$PWD
if [[ -e _build ]]; then
  echo "CI requires a fresh checkout without _build: $repo" >&2
  exit 2
fi

: "${OPAM_SWITCH_PREFIX:?Select the qualified opam switch with opam exec or opam env}"
jobs=${CI_JOBS:-${ASIC_BUILD_JOBS:-2}}
test_jobs=${ASIC_TEST_JOBS:-1}
export HARDCAML_ASIC_TEST_WORKERS=${HARDCAML_ASIC_TEST_WORKERS:-4}
if [[ ! $jobs =~ ^[1-9][0-9]*$ || ! $test_jobs =~ ^[1-9][0-9]*$ ]]; then
  echo "CI_JOBS and ASIC_TEST_JOBS must be positive integers" >&2
  exit 2
fi
if [[ ! $HARDCAML_ASIC_TEST_WORKERS =~ ^([2-9]|1[0-6])$ ]]; then
  echo "HARDCAML_ASIC_TEST_WORKERS must be between 2 and 16" >&2
  exit 2
fi

# Do not inherit developer tools, external package paths or Dune caches.
export PATH="$OPAM_SWITCH_PREFIX/bin:/usr/bin:/bin"
unset OCAMLPATH OCAMLFIND_CONF DUNE_BUILD_DIR DUNE_INSTALL_PREFIX DESTDIR \
  PYTHONPATH PYTHONHOME TOOLCHAIN IVERILOG_BIN IVERILOG_BASE VVP_BIN
export DUNE_CACHE=disabled
export PYTHONDONTWRITEBYTECODE=1
# Fixture Git commands must never inherit an outer worktree or index.
while IFS= read -r variable; do
  unset "$variable"
done < <(git rev-parse --local-env-vars)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="CI fixture" GIT_AUTHOR_EMAIL="ci@example.invalid"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

for tool in dune ocamlfind python3 git cc tclsh strace iverilog vvp; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 2; }
done
for tool in dune ocamlfind ocamlc; do
  [[ -x "$OPAM_SWITCH_PREFIX/bin/$tool" ]] || {
    echo "Missing selected-switch tool: $tool" >&2
    exit 2
  }
done
git rev-parse --verify HEAD >/dev/null || {
  echo "CI fixtures require a Git checkout with an initial commit" >&2
  exit 2
}

# Some existing fixture suites use this absolute temporary directory.
mkdir -p /tmp/opencode
stage=$(mktemp -d /tmp/opencode/hardcaml-asic-ci.XXXXXX)
trap 'rm -rf "$stage"' EXIT

# The selected switch must contain dependencies only. A missing library in
# the disposable installation must never fall back to an older provider.
if [[ -e "$OPAM_SWITCH_PREFIX/lib/hardcaml_asic" ]] ||
   ocamlfind query hardcaml_asic >/dev/null 2>&1; then
  echo "Select a dependency-only switch without an installed hardcaml_asic" >&2
  exit 2
fi
mkdir "$stage/home"
export HOME="$stage/home"
export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache" XDG_DATA_HOME="$HOME/.local/share"

timed() {
  local label=$1
  shift
  printf '\n[CI] %s\n' "$label"
  TIMEFORMAT="[CI] $label: %3R seconds"
  time "$@"
}

timed "Clean package build (@install)" dune build --root "$repo" -j "$jobs" @install
opam lint hardcaml_asic.opam
git diff --exit-code -- hardcaml_asic.opam

# Native inline tests, Python record/transport fixtures, Tcl model tests,
# generated bundles and starter rendering. Nested installed consumer probes
# are intentionally separate from Dune's runtest graph.
timed "Native and fixture tests (@runtest)" dune runtest --root "$repo" -j "$test_jobs"
timed "Segmented SRAM fixture tests" dune build --root "$repo" -j "$test_jobs" @test/sram-segmented

# The bundle test also simulates generated RTL when Icarus is present. Run the
# small memory bench explicitly: this gate cannot silently skip simulation.
ASIC_BUILD_JOBS=$jobs timed "Real generated RTL simulation" bash test/run_rtl_sim.sh "$stage/rtl"

installed_consumer() (
  set -e
  local prefix="$stage/prefix" consumer="$stage/consumer"
  dune install --root "$repo" --prefix "$prefix" hardcaml_asic
  export OCAMLPATH="$prefix/lib"
  export PATH="$prefix/bin:$PATH"
  for package in hardcaml_asic hardcaml_asic.execution hardcaml_asic.flow_domain; do
    [[ $(ocamlfind query "$package") == "$prefix"/lib/* ]]
  done
  mkdir "$consumer"
  cd "$consumer"

  # Render an actual standalone consumer with the freshly installed starter.
  "$prefix/bin/hardcaml-asic-starter" render --name ci-demo > "$stage/starter.json"
  python3 - "$stage/starter.json" <<'PY'
import json
from pathlib import Path
import sys

descriptor = json.loads(Path(sys.argv[1]).read_text())
assert descriptor["provider"] == "hardcaml_asic"
for entry in descriptor["files"]:
    path = Path(entry["path"])
    assert not path.is_absolute() and ".." not in path.parts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry["contents"])
PY
  printf '/_build/\n' > .gitignore
  git init -q
  git add .
  git commit -qm "consumer fixture"
  dune build --root . -j "$jobs"
  dune runtest --root . -j "$test_jobs"
  local project=./_build/default/asic_ci-demo/project.exe
  "$project" flow plan --endpoint synthesis --source-root "$consumer" --json > "$stage/plan.json"
  local plan_id
  plan_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plan_identity"])' "$stage/plan.json")
  "$project" flow emit --endpoint synthesis --source-root "$consumer" \
    --expected-plan-identity "$plan_id" --results-root "$stage/results" --json > "$stage/emission.json"
  local bundle
  bundle=$(python3 - "$stage/emission.json" "$plan_id" <<'PY'
import json
from pathlib import Path
import sys

receipt = json.loads(Path(sys.argv[1]).read_text())
assert receipt["plan_identity"] == sys.argv[2] and not receipt["reused"]
bundle = Path(receipt["output_dir"])
manifest = json.loads((bundle / "manifest.json").read_text())
assert manifest["identity"] == receipt["bundle_identity"]
assert manifest["synthesis_sources"]
print(bundle)
PY
  )
  "$prefix/bin/hardcaml-asic-flow" --version --json > "$stage/version.json"
  "$prefix/bin/hardcaml-asic-flow" compare \
    --left-kind bundle --left "$bundle" --right-kind bundle --right "$bundle" \
    --axis descriptive --json > "$stage/comparison.json"
  python3 - "$stage" "$prefix" <<'PY'
import json
from pathlib import Path
import sys

stage, prefix = map(Path, sys.argv[1:])
version = json.loads((stage / "version.json").read_text())
assert version["toolchain_lock_present"]
assert Path(version["module_dir"]) == prefix / "lib/hardcaml_asic/flow/hardcaml_asic_flow"
comparison = json.loads((stage / "comparison.json").read_text())
receipt = json.loads((stage / "emission.json").read_text())
assert comparison["schema_version"] == 1 and not comparison["truncated"]
assert comparison["axis"] == "descriptive"
assert comparison["left"]["kind"] == comparison["right"]["kind"] == "bundle"
for side in ("left", "right"):
    assert comparison[side]["identities"]["bundle_identity"] == receipt["bundle_identity"]
print("Installed starter, independent consumer, bundle publication and installed comparison passed")
PY
)

timed "Installed consumer and CLI" installed_consumer
