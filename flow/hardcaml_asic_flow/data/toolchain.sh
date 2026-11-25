#!/usr/bin/env bash
# Flow layer of the bootstrap: the external collateral the runner needs before a
# preflight can pass. Project-local collateral lives under the selected root and is
# pinned by the toolchain.lock installed beside this script.
#
# This is the generic provisioner, installed as package data and normally reached as
# "hardcaml-asic-flow provision". scripts/toolchain.sh is the repository adapter onto
# it, and bootstrap.sh runs that after the OCaml layer.
#
# Usage:
#   hardcaml-asic-flow provision                 # converge on the lockfile, fetching what is missing
#   hardcaml-asic-flow provision --check         # report every gap, change nothing
#   hardcaml-asic-flow provision --offline       # reuse what is installed, fetch nothing
#   hardcaml-asic-flow provision --no-container  # skip the Docker/Podman prerequisite and image
#   hardcaml-asic-flow provision --python PATH   # base Python for new venvs, not runtime --python
#
# What it creates, all under $TOOLCHAIN (default .toolchain/ under the caller's cwd):
#   tt/                 tt-support-tools, detached at support_tools_revision
#   pdk/                IHP-Open-PDK, detached at pdk_revision; this is PDK_ROOT
#   .venv/              librelane at librelane_version
#   .venv-precheck/     the TT precheck requirements, which cannot share .venv
#   toolchain-env.sh    generated; a caller or a shell can source it for the locations
#
# Safe to rerun. Every step checks first and does nothing when it is already satisfied,
# so this is the command after a pull that changes toolchain.lock as well as the command
# on a new machine.
#
# Managed Python/Tcl is project-local; explicit external Python, container storage and Nix stores remain external to $TOOLCHAIN;
# an image pull uses the container engine's storage. The opam switch is the other
# script's business (scripts/ocaml-deps.sh in a checkout). The PDK is fetched but
# never written to by the flow.
#
# Careful: bootstrapping prepares an environment. It does NOT prove the design hardens,
# meets timing, or passes precheck. Those are the run/postcheck operations and their
# own exit codes.
#
# Both revisions are fetched by exact hash with --depth 1, not by branch. The
# support-tools branch head has already moved past the pinned revision, so a shallow
# clone of the branch would not contain it; the branch in the lockfile is only a hint for
# a reader and a fallback when a server refuses an unadvertised object.
#
# Exit codes, distinguishable so a caller can tell a missing tool from a bad network:
#   0  the toolchain matches the lockfile
#   2  a host prerequisite is missing, or --check found a gap
#   3  toolchain.lock is malformed
#   4  a fetch or install could not complete
#   5  something is present at the wrong revision or version
#   7  a tool ran and failed

set -euo pipefail

readonly EX_PREREQ=2
readonly EX_LOCKFILE=3
readonly EX_NETWORK=4
readonly EX_MISMATCH=5
readonly EX_TOOL=7

# This script is installed as package data next to the lock it reads, so it locates
# both from its own path and never from a repository root, a caller's cwd or a sibling
# checkout. In the source tree data/toolchain.lock is a symlink to the repository's
# toolchain.lock; an installation gets that same file's contents as a regular file.
data_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
readonly data_dir

# --lock-file is internal plumbing for the Python entry point, which passes the lock it
# resolved from the installed package. It is deliberately absent from the usage block
# above: the public "provision" command exposes no way to swap target pins.
lock_file="$data_dir/toolchain.lock"

# One directory holds the checkouts, venvs and generated environment paths, not host
# prerequisites or container/Nix storage. scripts/toolchain.sh keeps the repository default and the
# CLI resolves --toolchain / $TOOLCHAIN / ./.toolchain before calling here.
TOOLCHAIN=${TOOLCHAIN:-$PWD/.toolchain}
readonly TOOLCHAIN
readonly tt_dir="$TOOLCHAIN/tt"
readonly pdk_dir="$TOOLCHAIN/pdk"
readonly venv_dir="$TOOLCHAIN/.venv"
readonly precheck_venv_dir="$TOOLCHAIN/.venv-precheck"
readonly env_file="$TOOLCHAIN/toolchain-env.sh"

check=0
offline=0
no_container=0
base_python=""
host_python=""

usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --check) check=1 ;;
        --offline) offline=1 ;;
        --no-container) no_container=1 ;;
        --python) shift; [[ $# -gt 0 && -n $1 && $1 != --* ]] || { echo "toolchain: --python needs a path" >&2; exit 1; }; base_python=$1 ;;
        --lock-file) shift; [[ $# -gt 0 ]] || { echo "toolchain: --lock-file needs a path" >&2; exit 1; }; lock_file=$1 ;;
        --host-python) shift; [[ $# -gt 0 && -n $1 ]] || { echo "toolchain: --host-python needs a path" >&2; exit 1; }; host_python=$1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "toolchain: unknown option: $1 (try --help)" >&2; exit 1 ;;
    esac
    shift
done
readonly lock_file

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
# Counted so the closing summary can admit they happened. A warning is something the run
# survived but a later step may not, and the summary is the line people actually read.
warnings=0
warn() { warnings=$((warnings + 1)); printf '    warning: %s\n' "$*" >&2; }
net()  { printf '    [network] %s\n' "$*"; }

die() {
    local code=$1
    shift
    printf '\nerror: %s\n' "$1" >&2
    shift
    local line
    for line in "$@"; do printf '       %s\n' "$line" >&2; done
    exit "$code"
}

mutating() { [[ $check -eq 0 ]]; }

# Bound read-only subprocesses, including Git's optional index refreshes.
export GIT_OPTIONAL_LOCKS=0
read_probe() { timeout --kill-after=2s 20s "$@"; }
# Sanitize only children; ensurepip inherits the bytecode prohibition too.
python_child() (
    local name
    for name in $(compgen -e); do
        case $name in
            PYTHONHOME|PYTHONPATH|TCL_LIBRARY|TK_LIBRARY|TCLLIBPATH|LD_*|DYLD_*|GCONV_PATH|GLIBC_TUNABLES) unset "$name" ;;
        esac
    done
    export PYTHONDONTWRITEBYTECODE=1
    "$@"
)
python_probe() { local python=$1; shift; python_child read_probe "$python" -I -B "$data_dir/../probe.py" "$@"; }
# Installed package entry, using the CLI's internal orchestration interpreter.
# The observer owns its per-command deadlines and named-container cleanup; do
# not wrap it in a shorter timeout that could kill cleanup midway.
runtime_probe() {
    python_child "$host_python" -I -B -c '
import sys
sys.path.insert(0, sys.argv.pop(1))
from hardcaml_asic_flow.runtime_observation import main
sys.exit(main(sys.argv[1:]))
' "$data_dir/../.." "$@" --lock "$lock_file"
}
prereq_gap() {
    if [[ $check -eq 1 ]]; then gap "$*"; else die "$EX_PREREQ" "$*"; fi
}

# In --check mode a gap is recorded and the run continues, so one invocation reports
# every one of them rather than the first. Outside --check nothing calls this: the step
# fixes the gap instead.
problems=()
gap() {
    problems+=("$1")
    printf '    missing: %s\n' "$1" >&2
}

# One value out of toolchain.lock. The file is parsed as data and never sourced, so a
# stray line in it cannot run anything.
lock() {
    local key=$1 value
    value=$(sed -n "s/^${key}=//p" "$lock_file" | head -1)
    [[ -n $value ]] || die "$EX_LOCKFILE" "toolchain.lock has no '${key}=' entry" \
        "Expected at $lock_file"
    printf '%s' "$value"
}

# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------
read_lockfile() {
    step "Reading $lock_file"
    [[ -f $lock_file ]] || die "$EX_LOCKFILE" "no toolchain.lock at $lock_file"

    support_tools_repository=$(lock support_tools_repository)
    support_tools_branch=$(lock support_tools_branch)
    support_tools_revision=$(lock support_tools_revision)
    pdk_name=$(lock pdk)
    pdk_repository=$(lock pdk_repository)
    pdk_branch=$(lock pdk_branch)
    pdk_revision=$(lock pdk_revision)
    librelane_version=$(lock librelane_version)
    python_version=$(lock python)
    [[ $python_version =~ ^[0-9]+\.[0-9]+$ ]] || die "$EX_LOCKFILE" "invalid Python minor: $python_version"
    [[ -x $host_python ]] || die "$EX_PREREQ" "missing orchestration interpreter"
    local policy_detail
    if ! policy_detail=$(runtime_probe policy 2>&1); then
        die "$EX_LOCKFILE" "runtime policy: $policy_detail"
    fi

    # The image name is derived exactly as scripts/coarse_flow_oracle.py derives it, rather than
    # pinned separately, so the two cannot disagree about which image a run uses.
    librelane_image="ghcr.io/librelane/librelane:$librelane_version"

    local revision
    for revision in "$support_tools_revision" "$pdk_revision"; do
        [[ $revision =~ ^[0-9a-f]{40}$ ]] || die "$EX_LOCKFILE" \
            "not a full 40-character commit hash: $revision"
    done

    info "pdk $pdk_name, librelane $librelane_version, python $python_version"
    info "toolchain root $TOOLCHAIN"
}

# Does the group list $2 contain the group $1? The lists come from `id -nG`, which is
# space-separated and unordered, so the surrounding spaces are what keep "docker" from
# matching "docker-users".
has_group() { [[ " $2 " == *" $1 "* ]]; }

# Why a socket the user was told to gain access to is still refusing them. Three states
# look identical in the error text and want opposite advice, so they are separated here:
# `id -nG` reports the credentials this process is actually running with, while
# `id -nG "$USER"` re-reads the group database. They disagree precisely when usermod has
# already run and every process in this login session predates it -- the state where
# repeating the usermod advice sends people in circles, because the membership is already
# on disk and only a fresh set of credentials can pick it up.
#
# The advice goes to stdout one line per line, unindented, because the two callers route
# it differently: info indents by two more spaces, die by seven. $2 overrides the command
# that joins the group, since Docker's group may not exist yet and joining it is then two
# commands; $3 adds a command to check the daemon with when the group is not the problem.
group_gap_advice() {
    local group=$1
    local join=${2:-"sudo usermod -aG $group \$USER"}
    local daemon_check=${3:-}

    if ! has_group "$group" "$(id -nG "$USER" 2>/dev/null)"; then
        echo "Join '$group' with:"
        echo "  $join"
        echo "  exec sg $group bash      # then pick it up without logging out"
        return 0
    fi

    if has_group "$group" "$(id -nG 2>/dev/null)"; then
        echo "You are in '$group' and so is this shell, so the refusal is not about groups."
        [[ -z $daemon_check ]] || echo "  $daemon_check"
        return 0
    fi

    # Groups are set once by PAM at login and inherited across fork(), so nothing in an
    # existing session re-reads them. Restarting a shell, or tmux, under that session
    # inherits the stale set and looks like the fix failing.
    echo "You are already in '$group'; this login session predates the change and still"
    echo "carries the old group set. Pick it up with either of:"
    echo "  exec sg $group bash          # this shell, keeping its other groups"
    echo "  # a brand-new SSH connection  (restarting tmux is not enough: a new server"
    echo "  #   forked from this session inherits the same stale credentials, and ssh"
    echo "  #   ControlMaster will silently reuse the old session too)"
}

# ---------------------------------------------------------------------------
# Host prerequisites: what this script cannot install for you
# ---------------------------------------------------------------------------
check_prerequisites() {
    step "Checking host prerequisites"

    command -v git >/dev/null 2>&1 || die "$EX_PREREQ" "git is not installed"
    command -v timeout >/dev/null 2>&1 || die "$EX_PREREQ" "timeout is required for bounded readiness probes"
    local python detail
    # Existing environments must never be silently recreated on a minor mismatch.
    for python in "$venv_dir/bin/python" "$precheck_venv_dir/bin/python"; do
        if [[ -x $python ]]; then
            if ! detail=$(python_probe "$python" version "$python_version" 2>&1); then
                prereq_gap "$python: ${detail:-probe failed or timed out}; explicitly repair the environment"
            fi
        elif [[ -e ${python%/bin/python} ]]; then
            prereq_gap "broken environment at ${python%/bin/python}; explicitly repair it before provisioning"
        fi
    done
    if [[ -x $venv_dir/bin/python ]]; then
        if ! detail=$(python_probe "$venv_dir/bin/python" tcl "$python_version" 2>&1); then
            prereq_gap "$venv_dir: ${detail:-Tcl probe failed or timed out}"
        fi
        if [[ $(installed_version "$venv_dir/bin/python" librelane || true) == "$librelane_version" ]]; then
            if ! detail=$(python_probe "$venv_dir/bin/python" flow "$python_version" "$librelane_version" 2>&1); then
                prereq_gap "$venv_dir: ${detail:-flow probe failed or timed out}; explicitly repair the environment"
            fi
        fi
    fi
    venv_python=$base_python
    if [[ -n $base_python ]]; then
        if ! detail=$(python_probe "$venv_python" base "$python_version" 2>&1); then
            prereq_gap "base $venv_python: ${detail:-probe failed or timed out}; select --python PATH with Python $python_version and host Tcl/venv/ensurepip"
        fi
    fi
    if [[ -f $tt_dir/precheck/requirements.txt && -x $precheck_venv_dir/bin/python ]]; then
        if ! detail=$(python_probe "$precheck_venv_dir/bin/python" requirements "$python_version" "$tt_dir/precheck/requirements.txt" 2>&1); then
            prereq_gap "precheck requirements: $detail; no pip resolution attempted"
        fi
        if [[ -n $(installed_version "$precheck_venv_dir/bin/python" klayout || true) ]]; then
            if ! detail=$(python_child read_probe "$precheck_venv_dir/bin/python" -I -B -c 'import klayout' 2>&1); then
                prereq_gap "$precheck_venv_dir: ${detail:-klayout import failed}; explicitly repair the environment"
            fi
        fi
    fi
    # Parse already-present requirements before acquisition, even when the new
    # target interpreter is absent or the host runs a different Python minor.
    if [[ -f $tt_dir/precheck/requirements.txt ]]; then
        [[ -x $host_python ]] || die "$EX_PREREQ" "missing orchestration interpreter"
        if ! detail=$(python_child read_probe "$host_python" -I -B -c '
import sys
sys.path.insert(0, sys.argv[1])
from hardcaml_asic_flow.probe import exact_requirements
exact_requirements(sys.argv[2])
' "$data_dir/../.." "$tt_dir/precheck/requirements.txt" 2>&1); then
            prereq_gap "precheck requirements: $detail; no pip resolution attempted"
        fi
    fi
    check_precheck_nix

    if [[ $no_container -eq 1 ]]; then
        warn "container checks skipped; this is not a native EDA readiness certificate"
        return
    fi

    container=""
    if command -v docker >/dev/null 2>&1; then
        container=docker
    elif command -v podman >/dev/null 2>&1; then
        container=podman
    else
        die "$EX_PREREQ" "neither docker nor podman is installed" \
            "The LibreLane runner is Dockerized by default." \
            "Install one, or rerun with --no-container to skip this."
    fi

    # An installed client with an unreachable daemon is the common case after a fresh
    # install: the socket is root-owned and the user is not in its group yet. The fix is a
    # group change, so say so rather than reporting "docker is missing". Which of the three
    # group states applies is worth separating for the same reason it is in
    # check_precheck_nix, and the socket answers a question the group names cannot: a
    # socket that does not exist means the daemon is down, not that a group is missing.
    if ! read_probe "$container" info >/dev/null 2>&1; then
        local socket_group="" advice=()
        if [[ $container == docker ]]; then
            socket_group=$(stat -c '%G' /var/run/docker.sock 2>/dev/null) || socket_group=""
        fi

        if [[ -n $socket_group && $socket_group != root ]]; then
            advice=("Its socket is group-restricted to '$socket_group'.")
            mapfile -t -O "${#advice[@]}" advice < <(group_gap_advice "$socket_group" \
                "sudo groupadd -f $socket_group && sudo usermod -aG $socket_group \$USER" \
                "systemctl status $container")
        elif [[ $container == docker ]]; then
            advice=("Start it, or add yourself to its socket group:"
                "  sudo groupadd -f docker && sudo usermod -aG docker \$USER"
                "  exec sg docker bash      # or open a new login session")
        else
            # Rootless Podman gates its socket on the user, not a group, so none of the
            # Docker advice transfers.
            advice=("Start it, or rerun with --no-container to skip the container steps.")
        fi

        # A snap-installed Docker keeps its own view of the user's groups and picks a
        # change up only when the snap restarts, so the advice above is incomplete for it
        # in exactly the group cases. Naming it only when that Docker is the one installed
        # keeps it out of the way of everyone else.
        if [[ $container == docker ]] && read_probe snap list docker >/dev/null 2>&1; then
            advice+=("Docker here is a snap, which also needs:"
                "  sudo snap disable docker && sudo snap enable docker")
        fi

        die "$EX_PREREQ" "$container is installed but its daemon is not reachable" \
            "${advice[@]}"
    fi
    info "$container daemon reachable (engine readiness only; the flow runner requires Docker)"
}

# The host interpreter comes from Operations.provision, never PATH or a checkout.
# Import from the installed package parent explicitly under isolation. The helper
# owns all artifact pins, verification and acquisition deadlines.
select_python() {
    [[ -z $base_python ]] || return 0
    [[ ! -x $venv_dir/bin/python || ! -x $precheck_venv_dir/bin/python ]] || return 0
    step "Resolving managed Python"
    [[ -x $host_python ]] || die "$EX_PREREQ" "missing orchestration interpreter (--host-python internal plumbing)"
    local mode=() result status
    if [[ $check -eq 1 ]]; then mode=(--check)
    elif [[ $offline -eq 1 ]]; then mode=(--offline); fi
    if result=$(python_child "$host_python" -I -B -c '
import sys
sys.path.insert(0, sys.argv.pop(1))
minor = sys.argv.pop(1)
from hardcaml_asic_flow import managed_python as helper
try:
    if ".".join(helper.describe()["version"].split(".")[:2]) != minor:
        raise helper.Error("managed Python pin does not match toolchain.lock minor")
except helper.Error as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(exc.status)
sys.exit(helper.main(sys.argv[1:]))
' "$data_dir/../.." "$python_version" --root "$TOOLCHAIN" "${mode[@]}"); then
        if ! venv_python=$(python_child "$host_python" -I -B -c '
import json, sys
from pathlib import Path
value = json.loads(sys.argv[1])["interpreter"]
if not isinstance(value, str) or not Path(value).is_absolute() or any(c in value for c in "\n\r\0"):
    raise ValueError("invalid helper interpreter")
print(value)
' "$result"); then
            die "$EX_TOOL" "invalid managed Python helper JSON"
        fi
    else
        status=$?
        if [[ $check -eq 1 ]]; then
            gap "managed runtime not installed or not ready (helper status $status)"
            return 0
        fi
        case $status in
            2) status=$EX_PREREQ ;;
            3) status=$EX_MISMATCH ;;
            4) status=$EX_NETWORK ;;
            6) status=$EX_PREREQ ;;
            *) status=$EX_TOOL ;;
        esac
        die "$status" "managed Python acquisition failed; see helper diagnostic above"
    fi
}

# ---------------------------------------------------------------------------
# Is $dir the top level of its own git repository? A bare `-d $dir/.git` test is not
# enough. An interrupted `git init` or fetch leaves behind a .git directory whose HEAD is
# empty, which git does not recognise as a repository at all; discovery then walks *up*,
# and every later `git -C "$dir"` silently operates on the enclosing checkout instead.
# That is not hypothetical: it repointed this repository's own origin at the PDK and
# detached its HEAD onto a PDK commit. Nothing below aims a git command at $dir until
# this has returned true.
is_own_repo() {
    local dir=$1
    [[ -d $dir/.git ]] || return 1
    GIT_DIR="$dir/.git" GIT_WORK_TREE="$dir" read_probe git rev-parse --git-dir >/dev/null 2>&1
}

# Every git command aimed at a pinned checkout goes through here. Setting GIT_DIR and
# GIT_WORK_TREE explicitly turns repository discovery off, so a damaged or half-built
# .git fails loudly against $dir instead of quietly resolving to a parent repository.
repo_git() {
    local dir=$1
    shift
    case $1 in
        rev-parse|status) GIT_DIR="$dir/.git" GIT_WORK_TREE="$dir" read_probe git "$@" ;;
        *) GIT_DIR="$dir/.git" GIT_WORK_TREE="$dir" git "$@" ;;
    esac
}

# Pinned checkouts
# ---------------------------------------------------------------------------
# Bring one checkout to one exact revision, idempotently. Fetches by hash with --depth 1,
# so what lands is the pinned commit and nothing else; `git rev-parse HEAD` afterwards is
# the revision itself, which is what coarse_flow_oracle.py preflight compares against.
fetch_pinned() {
    local dir=$1 repository=$2 revision=$3 branch=$4 label=$5
    local actual=""

    is_own_repo "$dir" && actual=$(repo_git "$dir" rev-parse HEAD 2>/dev/null || true)

    if [[ $actual == "$revision" ]]; then
        info "$label at ${revision:0:12} (present)"
        return 0
    fi

    if [[ -n $actual ]]; then
        # Careful: never move a checkout with work in it. This directory is ours by
        # convention, but a reader may well have edited the PDK or support tools to debug
        # a run, and losing that silently to a bootstrap is not a tradeoff worth making.
        if [[ -n $(repo_git "$dir" status --porcelain 2>/dev/null) ]]; then
            die "$EX_MISMATCH" "$dir is at ${actual:0:12}, not ${revision:0:12}, and is dirty" \
                "Commit, stash or discard the changes there, then rerun." \
                "Nothing was changed."
        fi
    fi

    # Returns nonzero here so the caller can skip the checks that only make sense once
    # the checkout exists; two lines for one missing directory is noise, not detail.
    if [[ $check -eq 1 ]]; then
        if [[ -n $actual ]]; then
            gap "$label is at ${actual:0:12}, lockfile wants ${revision:0:12}"
        else
            gap "$label is not present at $dir"
        fi
        return 1
    fi

    [[ $offline -eq 0 ]] || die "$EX_NETWORK" \
        "$label is not at ${revision:0:12} and --offline was given"

    mkdir -p "$dir"

    # A .git that exists but is not a repository is the wreckage of an interrupted run,
    # and rerunning has to clear it rather than build on top of it. Discarding it is free:
    # $dir holds provisioned state, re-fetchable by definition, and this line is only
    # reached once the dirty check above found no work to lose.
    if [[ -d $dir/.git ]] && ! is_own_repo "$dir"; then
        warn "$dir/.git is not a valid repository (interrupted fetch?); reinitialising"
        rm -rf "$dir/.git"
    fi
    is_own_repo "$dir" || git init --quiet "$dir"
    is_own_repo "$dir" || die "$EX_MISMATCH" \
        "could not initialise a git repository at $dir" \
        "Remove $dir and rerun."

    repo_git "$dir" remote remove origin 2>/dev/null || true
    repo_git "$dir" remote add origin "$repository"

    net "fetching $label ${revision:0:12} from $repository"
    if ! repo_git "$dir" fetch --depth 1 --quiet origin "$revision"; then
        # Some servers refuse an unadvertised object. Falling back to the branch costs
        # history but still lands on the pinned commit, which is verified below either way.
        warn "fetch by hash refused; falling back to branch $branch"
        repo_git "$dir" fetch --quiet origin "$branch" || die "$EX_NETWORK" \
            "could not fetch $label from $repository"
    fi

    repo_git "$dir" checkout --quiet --detach "$revision" || die "$EX_NETWORK" \
        "$label fetched but revision ${revision:0:12} is not in it"

    actual=$(repo_git "$dir" rev-parse HEAD)
    [[ $actual == "$revision" ]] || die "$EX_MISMATCH" \
        "$label checked out $actual, lockfile wants $revision"
    info "$label at ${revision:0:12}"
}

fetch_support_tools() {
    step "Resolving tt-support-tools"
    if ! fetch_pinned "$tt_dir" "$support_tools_repository" "$support_tools_revision" \
        "$support_tools_branch" "tt-support-tools"; then
        return 0
    fi

    # The one file the emitted bundle actually points at; its hash is pinned in
    # lib/platform/target.ml and re-checked by preflight, so a wrong revision is caught twice.
    local floorplan="$tt_dir/tech/$pdk_name/def/tt_block_6x4_pgvdd.def"
    if [[ -f $floorplan ]]; then
        info "floorplan present: tech/$pdk_name/def/tt_block_6x4_pgvdd.def"
    elif [[ $check -eq 1 ]]; then
        gap "floorplan not found at $floorplan"
    else
        die "$EX_MISMATCH" "floorplan not found at $floorplan" \
            "The revision is right but the layout is not what the bundle expects."
    fi
}

fetch_pdk() {
    step "Resolving the $pdk_name PDK"
    if ! fetch_pinned "$pdk_dir" "$pdk_repository" "$pdk_revision" "$pdk_branch" "PDK"; then
        return 0
    fi

    # PDK_ROOT is the clone root and the flow reads views from $PDK_ROOT/$PDK/..., the
    # same layout coarse_flow_oracle.py resolves the manifest's technology references against.
    if [[ -d $pdk_dir/$pdk_name ]]; then
        info "PDK_ROOT $pdk_dir contains $pdk_name"
    elif [[ $check -eq 1 ]]; then
        gap "$pdk_dir has no $pdk_name directory"
    else
        die "$EX_MISMATCH" "$pdk_dir has no $pdk_name directory" \
            "PDK_ROOT must contain the process by name."
    fi
}

# ---------------------------------------------------------------------------
# Python environments
# ---------------------------------------------------------------------------
# The installed version of a package inside a venv, empty when it is not installed.
installed_version() {
    local python=$1 package=$2
    python_child read_probe "$python" -I -B -c 'import importlib.metadata, sys; print(importlib.metadata.version(sys.argv[1]))' "$package" 2>/dev/null
}

install_flow_environment() {
    step "Preparing the LibreLane environment"

    local python="$venv_dir/bin/python"
    local have=""
    [[ -x $python ]] && have=$(installed_version "$python" librelane || true)

    if [[ $have == "$librelane_version" ]]; then
        local detail
        if ! detail=$(python_probe "$python" flow "$python_version" "$librelane_version" 2>&1); then
            prereq_gap "$venv_dir: ${detail:-flow probe failed or timed out}; explicitly repair the environment"
            return 0
        fi
        info "librelane $have (present)"
        return 0
    fi

    if [[ $check -eq 1 ]]; then
        if [[ ! -x $python ]]; then
            gap "no Python environment at $venv_dir"
        else
            gap "librelane ${have:-absent}, lockfile wants $librelane_version"
        fi
        return 0
    fi

    [[ $offline -eq 0 ]] || die "$EX_NETWORK" \
        "librelane ${have:-is not installed} and --offline was given"

    if [[ ! -x $python ]]; then
        python_child "$venv_python" -I -B -m venv "$venv_dir" || { rm -rf "$venv_dir"; \
            die "$EX_TOOL" "failed to create $venv_dir"; }
        info "created $venv_dir"
    fi

    net "installing librelane==$librelane_version"
    python_child "$python" -I -B -m pip --cache-dir "$TOOLCHAIN/cache/pip" install --quiet --upgrade pip >/dev/null || die "$EX_NETWORK" \
        "could not upgrade pip inside $venv_dir"
    python_child "$python" -I -B -m pip --cache-dir "$TOOLCHAIN/cache/pip" install --quiet "librelane==$librelane_version" || die "$EX_NETWORK" \
        "failed to install librelane==$librelane_version"

    have=$(installed_version "$python" librelane)
    [[ $have == "$librelane_version" ]] || die "$EX_MISMATCH" \
        "librelane resolved to $have, lockfile wants $librelane_version"
    info "librelane $have"
}

# Precheck cannot share the LibreLane environment: upstream's precheck/requirements.txt
# pins its own KLayout, and installing both into one venv resolves to a single version
# that satisfies neither. Two environments is upstream's arrangement, not a choice here.
install_precheck_environment() {
    step "Preparing the precheck environment"

    local requirements="$tt_dir/precheck/requirements.txt"
    local python="$precheck_venv_dir/bin/python"

    if [[ ! -f $requirements ]]; then
        # Only reachable when the support-tools checkout is absent, which is already a
        # recorded gap in --check mode; outside it, fetch_support_tools has run.
        if [[ $check -eq 1 ]]; then
            gap "precheck requirements not found (needs tt-support-tools)"
            return 0
        fi
        die "$EX_MISMATCH" "precheck requirements not found: $requirements"
    fi

    local detail="no Python environment"
    if [[ -x $python ]] && detail=$(python_probe "$python" precheck "$python_version" "$requirements" 2>&1); then
        info "precheck environment present ($precheck_venv_dir)"
        return 0
    fi

    if [[ $check -eq 1 ]]; then
        gap "$precheck_venv_dir: ${detail:-precheck probe failed or timed out}"
        return 0
    fi

    [[ $offline -eq 0 ]] || die "$EX_NETWORK" \
        "no precheck environment and --offline was given"

    local requirements_python=$python
    [[ -x $requirements_python ]] || requirements_python=$venv_python
    python_probe "$requirements_python" requirements "$python_version" "$requirements" || die "$EX_PREREQ" \
        "precheck requirements cannot be checked as exact pins; no pip resolution attempted"

    if [[ ! -x $python ]]; then
        python_child "$venv_python" -I -B -m venv "$precheck_venv_dir" || { rm -rf "$precheck_venv_dir"; \
            die "$EX_TOOL" "failed to create $precheck_venv_dir"; }
        info "created $precheck_venv_dir"
    fi

    net "installing precheck requirements"
    python_child "$python" -I -B -m pip --cache-dir "$TOOLCHAIN/cache/pip" install --quiet --upgrade pip >/dev/null || die "$EX_NETWORK" \
        "could not upgrade pip inside $precheck_venv_dir"
    python_child "$python" -I -B -m pip --cache-dir "$TOOLCHAIN/cache/pip" install --quiet -r "$requirements" || die "$EX_NETWORK" \
        "failed to install $requirements"

    python_probe "$python" precheck "$python_version" "$requirements" || die "$EX_TOOL" \
        "klayout did not install into $precheck_venv_dir"
    info "precheck environment ready"
}

# Upstream's precheck also wants a pinned native KLayout and Magic from its default.nix,
# which pip cannot supply. Precheck is a later step than bootstrap, so this is reported
# and never fatal; the postcheck operation re-checks it.
#
# Availability only: a version query proves neither daemon access nor evaluation.
# In particular --check must not evaluate Nix or initialise a store/cache.
check_precheck_nix() {
    step "Checking Nix for the TT precheck"

    if ! command -v nix-shell >/dev/null 2>&1; then
        warn "nix-shell not found; the TT precheck needs Nix for its pinned KLayout and Magic"
        return 0
    fi

    local probe
    if probe=$(read_probe nix-store --version 2>&1); then
        info "${probe%$'\n'*} (availability only; no evaluation or daemon readiness claimed)"
        return 0
    fi

    warn "nix-shell is installed but Nix is not usable; the TT precheck will fail"
    info "  $probe"

    # The group-permission case is worth naming: the fix is one command, and the symptom
    # (a permission error on a socket the daemon is happily serving) reads like a broken
    # install rather than a missing group membership.
    local socket_group
    if [[ $probe == *daemon-socket* && $probe == *"Permission denied"* ]] \
        && socket_group=$(stat -c '%G' /nix/var/nix/daemon-socket 2>/dev/null); then
        info "  The daemon socket is group-restricted to '$socket_group'."
        local line
        while IFS= read -r line; do info "  $line"; done \
            < <(group_gap_advice "$socket_group" "" "systemctl status nix-daemon")
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Container image
# ---------------------------------------------------------------------------
pull_container_image() {
    [[ $no_container -eq 0 ]] || return 0
    step "Resolving the LibreLane image"

    if read_probe "$container" image inspect "$librelane_image" >/dev/null 2>&1; then
        info "$librelane_image (present)"
        validate_container_image
        return 0
    fi

    if [[ $check -eq 1 ]]; then
        gap "container image not pulled: $librelane_image"
        return 0
    fi

    [[ $offline -eq 0 ]] || die "$EX_NETWORK" \
        "$librelane_image is not pulled and --offline was given"

    net "pulling $librelane_image"
    "$container" pull "$librelane_image" || die "$EX_NETWORK" \
        "failed to pull $librelane_image"
    info "$librelane_image"
    validate_container_image
}

validate_container_image() {
    local detail status
    if detail=$(runtime_probe container --engine "$container" 2>&1); then
        info "container runtime metadata matches the provisioning policy"
    else
        status=$?
        if [[ $check -eq 1 ]]; then
            gap "container runtime: $detail"
        else
            die "$status" "container runtime: $detail"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Generated environment file
# ---------------------------------------------------------------------------
# Written for shells that run the tools by hand. Literal defaults yield to
# nonempty caller values. Readiness inspects the content as data, never sources it.
env_assignments() {
    local name value quoted
    for name in ASIC_TOOLCHAIN TT PDK PDK_ROOT FLOW_PY PRECHECK_PY; do
        case $name in
            ASIC_TOOLCHAIN) value=$TOOLCHAIN ;;
            TT) value=$tt_dir ;;
            PDK|PDK_ROOT) value=$pdk_dir ;;
            FLOW_PY) value=$venv_dir/bin/python ;;
            PRECHECK_PY) value=$precheck_venv_dir/bin/python ;;
        esac
        if [[ ${1:-} == legacy ]]; then
            printf ': "${%s:=%s}"\n' "$name" "$value"
        else
            quoted=${value//\'/\'\\\'\'}
            printf 'if [ -z "${%s:-}" ]; then %s=\x27%s\x27; fi\n' "$name" "$name" "$quoted"
        fi
    done
    printf 'export ASIC_TOOLCHAIN TT PDK PDK_ROOT FLOW_PY PRECHECK_PY\n'
}

env_file_matches() {
    [[ -f $env_file ]] || return 1
    local content
    content=$(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$env_file")
    [[ $content == "$(env_assignments)" ]] && return 0
    # Accept the previous generator's safe literal paths without forcing a rewrite.
    [[ $TOOLCHAIN != *[\$\`\"\\\}]* && $content == "$(env_assignments legacy)" ]]
}

write_env_file() {
    step "Checking environment paths at $env_file"

    if env_file_matches; then
        info "environment paths match (present)"
        return 0
    fi
    if [[ $check -eq 1 ]]; then
        gap "missing or stale environment file at $env_file (inspected as data)"
        return 0
    fi

    mkdir -p "$TOOLCHAIN"
    { printf '# Generated by the hardcaml_asic flow provisioner. Existing nonempty values win.\n'; env_assignments; } >"$env_file"
    info "wrote $env_file"
}

# ---------------------------------------------------------------------------
read_lockfile
check_prerequisites
select_python
fetch_support_tools
fetch_pdk
install_flow_environment
install_precheck_environment
pull_container_image
write_env_file

if [[ ${#problems[@]} -gt 0 ]]; then
    step "Summary"
    printf '    %d gap(s):\n' "${#problems[@]}"
    for problem in "${problems[@]}"; do printf '      - %s\n' "$problem"; done
    die "$EX_PREREQ" "the toolchain does not match toolchain.lock" \
        "Resolve host/repair diagnostics first; explicitly provision missing collateral when ready."
fi

step "Summary"
info "the toolchain matches toolchain.lock"
if [[ $warnings -gt 0 ]]; then
    info "$warnings warning(s) above; a later step can still fail on them"
fi
info "source $env_file, or pass these locations to the flow command"
