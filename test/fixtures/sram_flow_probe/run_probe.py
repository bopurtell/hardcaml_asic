#!/usr/bin/env python3
"""Snapshot and run one explicitly bounded SRAM flow probe (stdlib only)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MACRO = "RM_IHPSG13_1P_256x16_c2_bm_bist"
ARCHIVE = HERE / "retained-inputs.tar.gz"
ARCHIVE_HASH = "de16cf3d8a584e2bace6ec999651e4c73f2c2adfe28a0b042f531835e9843d43"
AUTHORED = (
    "config.json", "pdn_cfg.tcl", "constraints.sdc", "info.yaml",
    "tt_um_sram_probe.v", "tt_um_sram_probe_tb.sv", "run_behavioral.sh",
    f"{MACRO}.blackbox.v", "run_probe.py", "README.md",
)
STATES = {
    "sta": ("OpenROAD.STAPostPNR", "54-openroad-rcx/state_out.json"),
    "magic-gds": ("Magic.DRC", "62-magic-drc/state_in.json"),
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def paths(value):
    if isinstance(value, str):
        path = Path(value)
        yield path if path.is_absolute() else ROOT / path
    elif isinstance(value, dict):
        for item in value.values():
            yield from paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from paths(item)


def validate_state(parser, state, mode):
    if not isinstance(state, dict):
        parser.error("retained state must be a JSON object")
    if state.get("metrics") is not None and not isinstance(state["metrics"], dict):
        parser.error("retained state metrics must be an object or null")
    required = ("nl",) if mode == "sta" else ("gds",)
    for key in required:
        if not isinstance(state.get(key), str) or not state[key]:
            parser.error(f"retained state requires a nonempty {key} path for {mode}")
    if mode == "sta":
        spef = state.get("spef")
        if not isinstance(spef, dict) or not spef:
            parser.error("retained STA state requires a nonempty corner-keyed spef object")
        for corner, value in spef.items():
            entries = value if isinstance(value, list) else [value]
            if not corner or not entries or any(not isinstance(p, str) or not p for p in entries):
                parser.error("retained STA spef entries must contain nonempty file paths")

    def check_view(value):
        # LibreLane accepts nested corner maps, path lists, and null optional views.
        if value is None:
            return
        if isinstance(value, dict):
            for item in value.values():
                check_view(item)
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, str) or not item:
                    parser.error("retained view lists must contain nonempty file paths")
        elif not isinstance(value, str) or not value:
            parser.error("retained views must be paths, corner maps, path lists, or null")

    views = {k: v for k, v in state.items() if k != "metrics"}
    check_view(views)
    for path in paths(views):
        if not path.is_file():
            parser.error(f"missing retained state view: {path}")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_logged(command, cwd, env, log):
    print(shlex.join(command), flush=True)
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    print(f"exit={result.returncode}; log={log}", flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["sta", "magic-gds", "power-only", "corrected-full", "precheck-only"])
    parser.add_argument("--tag", required=True, help="fresh identity; letters, digits, _ and -")
    parser.add_argument("--jobs", required=True, type=int, help="allocated share of the 20-job pool")
    parser.add_argument("--retained-run", type=Path,
                        help="retained input run (required for precheck-only; STA/DRC default: attempt-005-full)")
    parser.add_argument("--precheck", action="store_true", help="after full run, in isolated TT copy")
    parser.add_argument("--tt-root", type=Path,
                        help="precheck checkout (default: historical tt-sram-s3-cfa06bae)")
    parser.add_argument("--dry-run", action="store_true", help="validate and print; create nothing")
    args = parser.parse_args()
    precheck_only = args.mode == "precheck-only"
    precheck_enabled = args.precheck or precheck_only
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.tag):
        parser.error("invalid tag (1–80 letters/digits/_/-, starting with letter/digit)")
    if not 1 <= args.jobs <= 20:
        parser.error("--jobs must be between 1 and 20; coordinate the shared allocation")
    if args.precheck and args.mode in STATES:
        parser.error("--precheck requires power-only or corrected-full")
    if precheck_only and not args.retained_run:
        parser.error("precheck-only requires explicit --retained-run")
    if args.retained_run and args.mode not in STATES and not precheck_only:
        parser.error("--retained-run requires sta, magic-gds, or precheck-only")
    if args.tt_root and not precheck_enabled:
        parser.error("--tt-root requires --precheck or precheck-only")

    output = HERE / "runs" / f"pt0-{args.tag}"
    inputs = output / "inputs"
    run = inputs / "runs" / args.tag
    if output.exists():
        parser.error(f"output already exists; choose a fresh tag: {output}")
    pdk = ROOT / ".toolchain/pdk"
    tt = (args.tt_root or ROOT / ".toolchain/tt-sram-s3-cfa06bae").resolve()
    python = ROOT / ".toolchain/.venv/bin/python"
    precheck_python = ROOT / ".toolchain/.venv-precheck/bin/python"
    config = {} if precheck_only else json.loads((HERE / "config.json").read_text())
    if args.mode == "power-only":
        if digest(ARCHIVE) != ARCHIVE_HASH:
            parser.error("historical archive hash mismatch")
        with tarfile.open(ARCHIVE) as archive:
            member = "experiments/sram_flow_feasibility/config.json"
            stream = archive.extractfile(member)
            if stream is None:
                parser.error(f"archive has no {member}")
            with stream:
                config = json.load(stream)

    state_path = None
    state = None
    if args.mode in STATES:
        retained = (args.retained_run or HERE / "runs/attempt-005-full").resolve()
        state_path = retained / STATES[args.mode][1]
        state = json.loads(state_path.read_text())
        validate_state(parser, state, args.mode)

    precheck_sources = {}
    retained = args.retained_run.resolve() if args.retained_run else None
    if precheck_only:
        # Match precheck's own YAML ancestor lookup and sibling LEF convention.
        yaml_root = next((p for p in (retained, *retained.parents)
                          if (p / "info.yaml").is_file()), None)
        if yaml_root is None:
            parser.error(f"no retained info.yaml found above {retained}")
        precheck_sources = {
            "gds/tt_um_sram_probe.gds": retained / "final/gds/tt_um_sram_probe.gds",
            "lef/tt_um_sram_probe.lef": retained / "final/lef/tt_um_sram_probe.lef",
            "info.yaml": yaml_root / "info.yaml",
        }
    template = None if precheck_only else Path(config["FP_DEF_TEMPLATE"])
    authored = ("run_probe.py", "README.md") if precheck_only else AUTHORED
    required = list(precheck_sources.values()) if precheck_only else [python, template]
    required += [HERE / name for name in authored]
    for macro in config.get("MACROS", {}).values():
        for key in ("gds", "lef", "lib", "spice"):
            values = macro[key]
            if isinstance(values, dict):
                values = [v for group in values.values() for v in group]
            required += [pdk / "ihp-sg13cmos5l" / v.removeprefix("pdk_dir::") for v in values]
    if precheck_enabled:
        required += [precheck_python, tt / "precheck/default.nix", tt / "precheck/precheck.py"]
        if not (tt / "tech").is_dir():
            parser.error(f"missing TT tech directory: {tt / 'tech'}")
    for path in required:
        if not path.is_file():
            parser.error(f"missing input/tool: {path}")

    command = [str(python), "-m", "librelane", "--docker-no-tty", "--dockerized",
               "--manual-pdk", "--pdk-root", str(pdk), "--pdk", "ihp-sg13cmos5l",
               "--jobs", str(args.jobs), "-c", f"OPENROAD_THREADS={args.jobs}",
               "-c", "STA_THREADS=1", "-c", f"KLAYOUT_DRC_THREADS={args.jobs}",
               "-c", f"KLAYOUT_XOR_THREADS={args.jobs}", "--run-tag", args.tag]
    if args.mode in STATES:
        command += ["--only", STATES[args.mode][0], "--with-initial-state", str(inputs / "state.json")]
    if args.mode == "magic-gds":
        config["MAGIC_DRC_USE_GDS"] = True
    if not precheck_only:
        config["FP_DEF_TEMPLATE"] = str(inputs / "floorplan.def")
    command += [str(inputs / "config.json")]
    if precheck_only:
        command = None

    # Both decks use `threads`, not the unrelated `thr` passed by precheck.py.
    launcher = '#!/bin/sh\nexec "${PT0_REAL_KLAYOUT:?}" "$@" -rd threads=' + str(args.jobs) + '\n'
    precheck_cwd = output / "tt/precheck"
    gds = inputs / "gds/tt_um_sram_probe.gds" if precheck_only else run / "final/gds/tt_um_sram_probe.gds"
    precheck_script = (
        'set -eu\nexport PT0_REAL_KLAYOUT="$(command -v klayout)"\n'
        f'export PATH={shlex.quote(str(output / "bin"))}:"$PATH"\n'
        f'exec {shlex.join([str(precheck_python), "precheck.py", "--tech", "ihp-sg13cmos5l", "--gds", str(gds)])}\n'
    )
    precheck_command = ["nix-shell", "default.nix", "--run", precheck_script]
    print(f"mode={args.mode}; output={output}")
    print("Geometry: retained (unchanged)" if state or precheck_only else "Geometry: corrected VPWR bridges")
    if command:
        print("Macro corners:", json.dumps(config["MACROS"][MACRO]["lib"], sort_keys=True))
        print("Fast models: standard cells -40 C; macro -55 C")
        print(shlex.join(command))
    if precheck_enabled:
        print(f"Precheck TT source: {tt} (revision/status recorded on execution)")
        print(f"precheck cwd={precheck_cwd}; launcher:\n{launcher}")
        print(shlex.join(precheck_command))
    if args.dry_run:
        return 0

    # mkdir without exist_ok reserves the identity; failed runs are never reused.
    output.parent.mkdir(exist_ok=True)
    output.mkdir()
    inputs.mkdir()
    for name in authored:
        shutil.copyfile(HERE / name, inputs / name)
    for name, source in precheck_sources.items():
        target = inputs / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
    if not precheck_only:
        shutil.copyfile(template, inputs / "floorplan.def")
        write_json(inputs / "config.json", config)
    if state is not None:
        shutil.copyfile(state_path, inputs / "state.json")
    manifest = {
        "mode": args.mode, "tag": args.tag, "jobs": args.jobs, "sta_threads": 1,
        "command": command, "cwd": str(ROOT), "retained_state": str(state_path) if state_path else None,
        "geometry": "retained" if state or precheck_only else "corrected",
        "precheck_retained_run": str(retained) if precheck_only else None,
        "precheck_source_sha256": {str(p): digest(p) for p in precheck_sources.values()},
        "power_only_baseline_archive": str(ARCHIVE) if args.mode == "power-only" else None,
        "power_only_baseline_sha256": ARCHIVE_HASH if args.mode == "power-only" else None,
        "floorplan_template_source": str(template) if template else None,
        "precheck_tt_root": str(tt) if precheck_enabled else None,
        "fast_temperature_difference": "standard cells -40 C; SRAM -55 C",
        "input_sha256": {str(p.relative_to(inputs)): digest(p) for p in inputs.rglob("*") if p.is_file()},
        "retained_view_sha256": {
            str(p): digest(p) for p in set(paths({k: v for k, v in state.items() if k != "metrics"}))
        } if state else {},
        "tool_revisions": {},
    }
    for name, checkout in (("pdk", pdk), ("tt", tt)):
        manifest["tool_revisions"][name] = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        manifest["tool_revisions"][name + "_status"] = subprocess.check_output(
            ["git", "-C", str(checkout), "status", "--porcelain"], text=True)
    if precheck_enabled:
        for name in ("precheck", "tech"):
            shutil.copytree(tt / name, output / "tt" / name,
                            ignore=shutil.ignore_patterns("reports", "__pycache__"))
        # Upstream expects this directory to exist; exclude old reports, not its destination.
        (precheck_cwd / "reports").mkdir()
        (output / "bin").mkdir()
        wrapper = output / "bin/klayout"
        wrapper.write_text(launcher)
        wrapper.chmod(0o555)
        manifest["precheck_command"] = precheck_command
        manifest["precheck_cwd"] = str(precheck_cwd)
        manifest["klayout_launcher"] = launcher
    write_json(output / "manifest.json", manifest)
    # Only files are sealed: LibreLane creates its fresh runs/ below inputs/.
    for path in inputs.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    (output / "manifest.json").chmod(0o444)
    env = os.environ.copy()
    env.update(PDK_ROOT=str(pdk), PDK="ihp-sg13cmos5l", OMP_NUM_THREADS=str(args.jobs),
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
    flow_rc = run_logged(command, ROOT, env, output / "flow.log") if command else 0
    status = {"flow_exit": flow_rc} if command else {}
    write_json(output / "status.json", status)
    if precheck_enabled:
        # A full Classic run may return failure for known checkers yet produce GDS.
        # Record that failure faithfully, but still permit independent precheck.
        if gds.is_file():
            status["precheck_exit"] = run_logged(precheck_command, precheck_cwd, env,
                                                  output / "precheck.log")
        else:
            status["precheck_skipped"] = "no final GDS"
            status["precheck_exit"] = 2
        write_json(output / "status.json", status)
    return flow_rc or status.get("precheck_exit", 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
