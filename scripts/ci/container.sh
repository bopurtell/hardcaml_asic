#!/usr/bin/env bash
# Native CI in a disposable Ubuntu container, with /checkout mounted read-only.
set -euo pipefail

die() { printf 'CI error: %s\n' "$*" >&2; exit 1; }

# Run under the unprivileged CI account. Kept as a function so the cache
# lifecycle can also be exercised offline with stand-in tools.
worker() {
  local phase=$1
  local state=$XDG_DATA_HOME/asic
  local pins=scripts/ci/toolchain.env
  local jobs=${ASIC_BUILD_JOBS:-2}
  local cache_key=${ASIC_CACHE_KEY:?ASIC_CACHE_KEY is required}
  local cache_hit=${ASIC_CACHE_HIT:-false}
  [[ $cache_hit == true || $cache_hit == false ]] || die 'invalid cache hit flag'
  [[ $jobs =~ ^[1-9][0-9]*$ ]] || die 'invalid build job count'
  local status
  status=$(git status --porcelain --untracked-files=all --ignored)
  [[ -z $status ]] ||
    die 'CI requires a clean checkout without existing build products'
  # shellcheck source=scripts/ci/toolchain.env
  source "$pins"
  export OPAMROOT=$state/opam OPAM_SWITCH=asic DUNE_CACHE=disabled
  export OPAMJOBS=$jobs
  local -a packages=(
    ocaml.5.2.0 ocaml-variants.5.2.0+ox "oxcaml-compiler.$ASIC_OXCAML"
    "dune.$ASIC_DUNE" "core.$ASIC_JANE" "hardcaml.$ASIC_JANE"
    "ppx_jane.$ASIC_JANE" "ppx_hardcaml.$ASIC_JANE"
    "expect_test_helpers_core.$ASIC_JANE" "yojson.$ASIC_YOJSON"
    "cryptokit.$ASIC_CRYPTOKIT" "zarith.$ASIC_ZARITH"
    "spawn.$ASIC_SPAWN" "conf-python-3.$ASIC_CONF_PYTHON" "ocamlfind.$ASIC_OCAMLFIND"
    # The emitted starter's independent project links core_unix.sys_unix.
    "core_unix.$ASIC_JANE"
  )

  installed_packages() {
    opam list --switch=asic --installed --columns=package --short | LC_ALL=C sort
  }

  verify_dependencies() {
    local installed package
    installed=$(installed_packages)
    if grep -q '^hardcaml_asic\.' <<< "$installed"; then
      die 'cached switch contains the installed provider'
    fi
    for package in "${packages[@]}"; do
      grep -Fxq "$package" <<< "$installed" || die "missing exact dependency $package"
    done
    [[ $(opam exec --switch=asic -- ocamlc -version) == 5.2.0+ox ]] ||
      die 'unexpected compiler version'
    [[ $(opam exec --switch=asic -- dune --version) == "$ASIC_DUNE" ]] ||
      die 'unexpected dune version'
    opam exec --switch=asic -- ocamlfind printconf >/dev/null
    if opam exec --switch=asic -- ocamlfind query hardcaml_asic >/dev/null 2>&1; then
      die 'cached switch contains the installed provider library'
    fi
    # The ordinary bootstrap checker is authoritative for required package
    # names, including any additions to the project's build/test dependencies.
    bash scripts/ocaml-deps.sh
  }

  verify_cache() {
    [[ -f $state/ci-cache-key && $(cat "$state/ci-cache-key") == "$cache_key" ]] ||
      die 'no matching successful provisioning marker'
    cmp -s "$pins" "$state/toolchain.env" || die 'toolchain pins do not match'
    [[ -w $state && -w $OPAMROOT && -w $OPAMROOT/asic ]] ||
      die 'cached switch is not writable'
    [[ -z $(find "$state" ! -uid "$EUID" -print -quit) ]] ||
      die 'cached files are owned by another user'
    local installed
    installed=$(installed_packages)
    [[ -n $installed && $installed == "$(cat "$state/ci-installed-packages")" ]] ||
      die 'installed package set does not match the provisioning receipt'
    verify_dependencies
  }

  case $phase in
    provision)
      if [[ $cache_hit == true ]]; then
        verify_cache
        return
      fi
      [[ ! -e $state || ( -d $state && -z $(ls -A "$state") ) ]] ||
        die 'cache miss requires an empty managed toolchain directory'
      mkdir -p "$state"
      local name snapshot revision staging
      for name in default ox; do
        if [[ $name == default ]]; then
          snapshot=ocaml/opam-repository
          revision=$ASIC_OPAM_REPOSITORY
        else
          snapshot=oxcaml/opam-repository
          revision=$ASIC_OX_REPOSITORY
        fi
        staging=$(mktemp -d "$state/snapshot.XXXXXXXX")
        curl -fsSL --retry 3 "https://codeload.github.com/$snapshot/tar.gz/$revision" \
          -o "$staging/archive.tar.gz"
        mkdir "$state/$name"
        tar -xzf "$staging/archive.tar.gz" --strip-components=1 -C "$state/$name"
        rm "$staging/archive.tar.gz"
        rmdir "$staging"
      done
      unset OPAM_SWITCH
      opam init --bare --no-setup --no-opamrc --yes --disable-sandboxing default "$state/default"
      opam repository add --dont-select --yes ox "$state/ox"
      opam switch create --no-switch --yes --jobs="$jobs" --repos=ox,default asic \
        --packages="ocaml-variants.5.2.0+ox,oxcaml-compiler.$ASIC_OXCAML"
      export OPAM_SWITCH=asic
      opam install --switch=asic --yes --jobs="$jobs" "${packages[@]}"
      # Pick up transitive and newly declared test/build dependencies through
      # the package manifest. Do not pin, build or install the package itself.
      opam install --switch=asic --yes --jobs="$jobs" --deps-only --with-test ./hardcaml_asic.opam
      opam clean --switch=asic --download-cache --switch-cleanup
      verify_dependencies
      cp "$pins" "$state/toolchain.env"
      installed_packages > "$state/ci-installed-packages"
      printf '%s\n' "$cache_key" > "$state/ci-cache-key.tmp"
      mv "$state/ci-cache-key.tmp" "$state/ci-cache-key"
      ;;
    test)
      verify_cache
      command -v tclsh >/dev/null
      strace -o /dev/null true
      export CI_JOBS=$jobs
      exec opam exec --switch=asic -- bash scripts/ci/check.sh
      ;;
    *) die "unknown worker phase: $phase" ;;
  esac
}

main() {
  [[ $EUID == 0 && -f /.dockerenv ]] ||
    die 'run as root inside a fresh Docker container'
  local phase=${1:-}
  local home=/home/asic-ci
  local source_dir=$home/source
  case $phase in setup|provision|test) ;; *) die "unknown phase: $phase" ;; esac
  if [[ $phase == setup ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
      autoconf build-essential bzip2 ca-certificates curl git gzip libgmp-dev \
      opam pkg-config python3 python3-venv rsync unzip xz-utils zlib1g-dev \
      iverilog strace tcl
    apt-get clean
    local uid=${ASIC_CI_UID:-1001} gid=${ASIC_CI_GID:-1001} existing revision
    [[ $uid =~ ^[1-9][0-9]*$ && $gid =~ ^[1-9][0-9]*$ ]] || die 'invalid CI UID/GID'
    if ! getent group "$gid" >/dev/null; then groupadd --gid "$gid" asic-ci; fi
    if existing=$(getent passwd "$uid"); then
      [[ ${existing%%:*} == ubuntu ]] || die "unexpected existing account for UID $uid"
      usermod --login asic-ci --gid "$gid" --groups '' \
        --home "$home" --shell /bin/bash ubuntu
    else
      useradd --uid "$uid" --gid "$gid" --create-home --shell /bin/bash asic-ci
    fi
    mkdir -p "$home/.local"
    chown "$uid:$gid" "$home" "$home/.local"
    install -d -o "$uid" -g "$gid" -m 0700 /tmp/opencode
    git config --global --add safe.directory "$PWD"
    revision=$(git rev-parse HEAD)
    git init --quiet "$source_dir"
    git -C "$source_dir" fetch --quiet --depth=1 "$PWD" HEAD
    git -C "$source_dir" checkout --quiet --detach FETCH_HEAD
    [[ $(git -C "$source_dir" rev-parse HEAD) == "$revision" ]]
    chown -R "$uid:$gid" "$source_dir"
    echo "=== Test revision $revision ==="
    return
  fi

  cd "$source_dir"
  # The unprivileged inner Bash receives the phase as its own $1.
  # shellcheck disable=SC2016
  exec runuser -u asic-ci -- env -i \
    HOME="$home" USER=asic-ci LOGNAME=asic-ci PATH=/usr/bin:/bin LC_ALL=C.UTF-8 \
    XDG_DATA_HOME="$home/.local/share" XDG_CONFIG_HOME="$home/.config" \
    XDG_CACHE_HOME="$home/.cache" \
    ASIC_CACHE_KEY="${ASIC_CACHE_KEY:?}" ASIC_CACHE_HIT="${ASIC_CACHE_HIT:-false}" \
    ASIC_BUILD_JOBS="${ASIC_BUILD_JOBS:-2}" ASIC_TEST_JOBS="${ASIC_TEST_JOBS:-1}" \
    bash -c 'source scripts/ci/container.sh; worker "$1"' bash "$phase"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
