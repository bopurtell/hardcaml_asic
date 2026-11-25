"""External TT/LibreLane bundle preflight, execution, and result collection.

Retained source-only reference for the native `hardcaml-asic-flow`
preflight, run, postcheck and collect commands.
`scripts/coarse_flow_oracle.py` is the checkout adapter.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import uuid

from . import runtime_contract, runtime_observation, execution_transport, result_store
from .result_store import FlowInterrupted, interrupt_handler, interruption_handlers


class PrerequisiteError(Exception):
    pass


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    run_dir: Path
    run_record: Path
    status: str
    exit_code: int

    def as_dict(self):
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "run_record": str(self.run_record),
            "status": self.status,
            "exit_code": self.exit_code,
        }


@dataclass(frozen=True)
class CheckOutcome:
    check_id: str
    check_dir: Path
    check_record: Path
    status: str
    exit_code: int

    def as_dict(self):
        return {
            "check_id": self.check_id,
            "check_dir": str(self.check_dir),
            "check_record": str(self.check_record),
            "status": self.status,
            "exit_code": self.exit_code,
        }


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    result_store.save_json(path, value)


def read_json(path):
    return json.loads(path.read_text())


def safe_child(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise PrerequisiteError(f"invalid relative path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PrerequisiteError(f"path escapes its root: {relative}")
    return path


def command_output(argv, cwd=None, env=None):
    """Stdout of a probe, or a PrerequisiteError naming the command that failed.

    `env` is passed through rather than always inheriting this process's, because
    a probe that has to run under a pinned environment -- the precheck nix-shell
    below and its `NIX_PATH` -- must see the same variables the logged commands
    beside it see. `None` keeps the inherited environment.
    """
    result = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=cwd,
                            env=env)
    if result.returncode:
        raise PrerequisiteError(f"command failed: {argv!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def nixpkgs_pin(nix_file):
    """A `NIX_PATH` value for the nixpkgs revision that `nix_file` itself pins.

    `nix-shell --run` resolves `<nixpkgs>` for its own bashInteractive. With no
    channel and no `NIX_PATH` that lookup fails, and nix prints an evaluation
    error and falls back to the environment's bash. The pinned tools are
    unaffected -- the upstream `precheck/default.nix` pins nixpkgs by revision
    with `fetchTarball` and never consults the search path -- so it is noise, but
    it is noise in the middle of a postcheck log, and the wrapper shell then comes
    from outside the pin.

    The revision comes from the pinned collateral itself, so this adds no second
    authority and no new pin to maintain. Returns None when the file names no
    `fetchTarball`: an empty search path would be worse than the error, so the
    caller leaves `NIX_PATH` alone.
    """
    match = re.search(r'fetchTarball\s*"([^"]+)"', Path(nix_file).read_text())
    return f"nixpkgs={match.group(1)}" if match else None


def preflight(bundle, support_tools, pdk_root, python, allow_python_mismatch=False,
              native=False):
    issues = []
    waivers = []
    # Status is a read-only readiness probe: do not refresh collateral indexes.
    git_env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    manifest = read_json(bundle / "manifest.json")
    requested = manifest["requested_tools"]
    runtime = {"authority": "bundle.requested_tools", "normalized_policy": None,
               "execution_mode": "native" if native else "dockerized",
               "observations": {}, "comparisons": []}
    report = {"ready": False, "issues": issues, "waivers": waivers, "references": [],
              "requested": requested, "actual": {}, "runtime_contract": runtime,
              "support_tools": str(support_tools.resolve()),
              "pdk_root": str(pdk_root.resolve()), "python": str(python.absolute())}
    try:
        contract = runtime_contract.policy_from_requested(requested)
        runtime["normalized_policy"] = contract
    except runtime_contract.ContractError as exc:
        issues.append(f"runtime policy: {exc}")
        return report
    if manifest.get("schema_version") != 1 or manifest.get("adapter") != "LibreLane":
        issues.append("unsupported bundle schema or adapter")
    for entry in manifest["files"]:
        try:
            path = safe_child(bundle, entry["path"])
            if not path.is_file() or digest(path) != entry["sha256"]:
                issues.append(f"missing or changed bundle file: {entry['path']}")
        except (KeyError, PrerequisiteError) as exc:
            issues.append(str(exc))
    for root, expected, label in (
        (support_tools, requested["support_tools_revision"], "support tools"),
        (pdk_root, requested["pdk_revision"], "PDK"),
    ):
        try:
            actual = command_output(["git", "-C", str(root), "rev-parse", "HEAD"], env=git_env)
            if actual != expected:
                issues.append(f"{label} revision {actual}; requested {expected}")
        except PrerequisiteError as exc:
            issues.append(str(exc))
    references = []
    for ref in manifest["target"]["external_references"]:
        repo = support_tools if ref["source"] == "Support_tools" else pdk_root
        root = support_tools if ref["source"] == "Support_tools" else pdk_root / manifest["target"]["technology"]
        try:
            path = safe_child(root, ref["path"])
            if not path.is_file():
                issues.append(f"missing {ref['role']}: {path}")
            else:
                actual_hash = digest(path)
                references.append({"role": ref["role"], "path": str(path),
                                   "sha256": actual_hash})
                if ref.get("sha256") and actual_hash != ref["sha256"]:
                    issues.append(f"changed {ref['role']}: {path}")
                status = command_output(["git", "-C", str(repo), "status",
                                         "--porcelain", "--", str(path)], env=git_env)
                if status:
                    issues.append(f"modified {ref['role']}: {path}")
        except (KeyError, PrerequisiteError) as exc:
            issues.append(str(exc))
    versions = report["actual"]
    host = {}
    container = {}
    try:
        host = runtime_observation.observe_host(python)
        versions.update(python=host["python_version"], librelane=host["librelane_version"])
    except runtime_observation.ObservationError as exc:
        issues.append(str(exc))
    runtime["observations"]["host"] = host
    if not native:
        try:
            container = runtime_observation.observe_container(
                contract["implementation"]["dockerized"]["image_ref"])
            versions.update(docker_server=container["engine_identity"]["server_version"],
                            librelane_image=container["image_identity"]["image_id"],
                            container_python=container["python_version"],
                            container_librelane=container["librelane_version"],
                            container_requires_python=container["requires_python"])
        except runtime_observation.ObservationError as exc:
            issues.append(str(exc))
        runtime["observations"]["container"] = container
    evaluation = runtime_observation.evaluate_observations(
        contract, host, container, native=native, allow_python_mismatch=allow_python_mismatch)
    runtime["comparisons"] = evaluation["comparisons"]
    for key, destination in (("issues", issues), ("waivers", waivers)):
        for row in evaluation[key]:
            destination.append(f"{row['role']} {row['field']} {row['actual']!r}; requested {row['expected']!r}")
    try:
        report["execution_binding"] = execution_transport.from_preflight(report, native=native)
    except execution_transport.BindingError as exc:
        issues.append(f"execution binding: {exc}")
    report.update(ready=not issues, references=references)
    return report


def run_logged(argv, cwd, env, log, record, record_path, cleanup=None, timeout=None):
    record["commands"].append(argv)
    save(record_path, record)
    with log.open("a") as stream:
        stream.write(f"\n$ {shlex.join(argv)}\n")
        stream.flush()
        process = None
        failed = False
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            return process.wait() if timeout is None else process.wait(timeout=timeout)
        except BaseException:
            failed = True
            raise
        finally:
            def remove_owned_container():
                if cleanup is None:
                    return None
                try:
                    cleanup()
                    record.setdefault("container_cleanup", []).append({"status": "removed-or-absent"})
                except Exception as exc:
                    record.setdefault("container_cleanup", []).append({"status": "failed", "error": str(exc)})
                    return exc
                finally:
                    save(record_path, record)
                return None

            if failed and process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            cleanup_error = remove_owned_container()
            if failed and process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        record["client_termination"] = "failed: process did not exit after SIGKILL"
                        save(record_path, record)
                # A reaped client does not prove its descendants exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Retry after the client is stopped: an in-flight launch may have
                # raced the first rm. Neither client exit nor TERM proves removal.
                remove_owned_container()
            if cleanup_error is not None and not failed:
                raise RuntimeError(f"owned container cleanup failed: {cleanup_error}")


def run(args):
    with interruption_handlers():
        return _run(args)


def _run(args):
    bundle = args.bundle.resolve()
    manifest = read_json(bundle / "manifest.json")
    allocation = result_store.allocate(args.runs)
    run_id = allocation.identity
    run_dir = allocation.directory
    record_path = run_dir / "run.json"
    record = {"schema_version": 1, "run_id": run_id,
              "execution_binding_required": True,
              "build_identity": manifest["identity"], "bundle": str(bundle),
              "manifest_sha256": digest(bundle / "manifest.json"),
              "started_at": now(), "ended_at": None,
              "requested_stage": args.stage, "completed_stage": None,
              "status": "starting", "commands": [], "logs": ["execution.log"],
              "outputs": {"project": "project", "flow": "project/runs/asic"}}
    save(record_path, record)
    log = run_dir / "execution.log"
    try:
        checked = result_store.durable_preflight(
            bundle, run_dir / "preflight.json",
            lambda: preflight(bundle, args.support_tools, args.pdk_root, args.python,
                              args.allow_python_mismatch, args.native), save=save)
        check = checked.result
        record["environment"] = check
        if checked.status == "interrupted":
            raise FlowInterrupted(checked.exit_code - 128)
        if not check["ready"]:
            raise PrerequisiteError("; ".join(check["issues"]))
        binding = execution_transport.validate(check.get("execution_binding"))
        if binding != execution_transport.from_preflight(check, native=args.native):
            raise PrerequisiteError("execution binding differs from preflight observations")
        record["execution_binding"] = binding
        project = run_dir / "project"
        shutil.copytree(bundle, project)
        for ref in manifest["target"]["external_references"]:
            if ref["source"] == "Support_tools":
                source = safe_child(args.support_tools.resolve(), ref["path"])
                destination = safe_child(project / "tt", ref["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        (project / "runs/asic").mkdir(parents=True)
        if digest(project / "manifest.json") != record["manifest_sha256"]:
            raise PrerequisiteError("staged manifest differs from original bundle")
        env = execution_transport.environment()
        env["PATH"] = str(args.python.parent) + os.pathsep + env.get("PATH", "")
        env["PDK_ROOT"] = str(args.pdk_root.resolve())
        env["PDK"] = manifest["target"]["technology"]
        record["status"] = "running"
        save(record_path, record)
        # P3 already emitted the complete, resolved config. Calling TT's
        # create_user_config here would rewrite it and its WASM Yosys port probe
        # cannot read an absolute staged path. Run the exact emitted config.
        cleanup = None
        if args.native:
            flow = [binding["interpreter"], *binding["interpreter_flags"], "-m", "librelane"]
        else:
            record["execution_engine"] = execution_transport.verify_engine(binding)
            execution_transport.verify_image(binding)
            name = execution_transport.owned_name()
            record["container_name"] = name
            flow = execution_transport.docker_command(
                binding, project, args.pdk_root.resolve(), name=name,
                pdk=manifest["target"]["technology"])
            flow += [*binding["interpreter_flags"], "-m", "librelane"]
            cleanup = lambda: execution_transport.cleanup(binding, name)
        flow += ["--pdk-root", str(args.pdk_root.resolve()),
                "--pdk", manifest["target"]["technology"], "--manual-pdk",
                "--run-tag", "asic", "--force-run-dir", "runs/asic"]
        record["execution_mode"] = "native" if args.native else "dockerized"
        if args.stage == "synthesis":
            flow += ["--to", "Yosys.Synthesis"]
        flow += ["src/config.json"]
        if run_logged(flow, project, env, log, record, record_path, cleanup=cleanup):
            raise RuntimeError("LibreLane failed; see execution.log and project/runs/asic")
        if (digest(project / "manifest.json") != record["manifest_sha256"]
                or digest(bundle / "manifest.json") != record["manifest_sha256"]):
            raise RuntimeError("build manifest changed during execution")
        record["completed_stage"] = args.stage
        record["status"] = "completed"
    except (FlowInterrupted, KeyboardInterrupt) as exc:
        signum = exc.signum if isinstance(exc, FlowInterrupted) else signal.SIGINT
        record["status"] = "interrupted"
        record["error"] = f"interrupted by {signal.Signals(signum).name}"
        record["interrupted_by"] = signal.Signals(signum).name
        exit_code = 128 + signum
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        exit_code = 1
    else:
        exit_code = 0
    finally:
        record["ended_at"] = now()
        save(record_path, record)
    return RunOutcome(run_id, run_dir.resolve(), record_path.resolve(),
                      record["status"], exit_code)


def execute(args):
    """Standalone development adapter retaining the historical one-line path."""
    outcome = run(args)
    print(outcome.run_record)
    if outcome.status != "completed":
        print(read_json(outcome.run_record)["error"], file=sys.stderr)
    return outcome.exit_code


def check(args, *, context=None, replay_trace=None):
    run_dir = args.run_dir.resolve()
    run = read_json(run_dir / "run.json") if context is None else context
    if run["status"] != "completed" or run["completed_stage"] != "full":
        raise PrerequisiteError("postcheck requires a completed full flow")
    flow = run_dir / run["outputs"]["flow"]
    top = read_json(run_dir / "project/manifest.json")["top_module"]
    gds = flow / "final/gds" / f"{top}.gds"
    netlist = flow / "final/nl" / f"{top}.nl.v"
    for path in (gds, netlist, args.testbench, args.precheck_python,
                  args.support_tools / "precheck/default.nix", *([replay_trace] if replay_trace else [])):
        if not path.is_file():
            raise PrerequisiteError(f"missing postcheck input: {path}")
    check_id = uuid.uuid4().hex
    check_dir = run_dir / "checks" / check_id
    check_dir.mkdir(parents=True)
    record_path = check_dir / "postcheck.json"
    record = {"schema_version": 1, "run_id": run["run_id"],
              "build_identity": run["build_identity"], "started_at": now(),
              "ended_at": None, "status": "running", "commands": [],
              "checks": {"precheck": "pending", "gate_level": "pending"},
              "inputs": {"gds_sha256": digest(gds), "netlist_sha256": digest(netlist),
                          "testbench_sha256": digest(args.testbench),
                          "testbench_top": args.testbench_top},
               "logs": ["postcheck.log"], "tools": {}}
    if context is not None:
        record["evidence_origin"] = context["evidence_origin"]
    save(record_path, record)
    log = check_dir / "postcheck.log"
    env = execution_transport.environment()
    env["PDK_ROOT"] = str(args.pdk_root.resolve())
    env["PDK"] = "ihp-sg13cmos5l"
    work = check_dir / "tt"
    try:
        shutil.copytree(args.support_tools / "precheck", work / "precheck")
        (work / "tech").symlink_to((args.support_tools / "tech").resolve(),
                                   target_is_directory=True)
        pin = read_json(work / "precheck/tool-versions.json")["klayout"]
        # Both nix-shell invocations below take <nixpkgs> from the copied
        # default.nix's own pin; env carries it into the logged one as well.
        nix_path = nixpkgs_pin(work / "precheck/default.nix")
        if nix_path:
            env["NIX_PATH"] = nix_path
        native_klayout = command_output(
            ["nix-shell", "default.nix", "--run", "klayout -v"], cwd=work / "precheck",
            env=env)
        record["tools"]["klayout"] = native_klayout
        record["tools"]["klayout_requested"] = pin
        if native_klayout != f"KLayout {pin}":
            raise PrerequisiteError(f"precheck KLayout {native_klayout}; requested {pin}")
        record["tools"]["precheck_python_klayout"] = command_output(
            [str(args.precheck_python.absolute()), "-c",
             "from importlib.metadata import version; print(version('klayout'))"])
        if not record["tools"]["precheck_python_klayout"].startswith("0.28."):
            raise PrerequisiteError("precheck Python KLayout package must be 0.28.x")
        # The PDK flop models read $setuphold delayed nets, which Icarus 12 leaves
        # undriven (every flop simulates as X). Use the Icarus in the run's pinned
        # LibreLane image, with the run and PDK mounted at their host paths.
        image = f"ghcr.io/librelane/librelane:{run['environment']['requested']['librelane']}"
        pdk_root = args.pdk_root.resolve()
        binding = run.get("execution_binding")
        simulator_cleanup = None
        if (run.get("execution_binding_required")
                or "execution_binding" in run.get("environment", {})) and binding is None:
            raise PrerequisiteError("new run is missing execution binding")
        if "execution_binding" in run:
            execution_transport.validate(binding)
            if binding["execution_mode"] != "dockerized":
                raise PrerequisiteError("new native run has no recorded container simulator binding")
            saved_binding = run["environment"].get("execution_binding")
            if saved_binding is not None and saved_binding != binding:
                raise PrerequisiteError("run binding differs from saved preflight binding")
            record["tools"]["execution_binding"] = binding
            record["tools"]["execution_engine"] = execution_transport.verify_engine(binding)
            record["tools"]["simulator_image"] = execution_transport.verify_image(binding)
            name = execution_transport.owned_name()
            record["simulator_container_name"] = name
            simulator = execution_transport.docker_command(
                binding, run_dir, pdk_root, name=name, workdir=check_dir, entrypoint="env")
            simulator_cleanup = lambda: execution_transport.cleanup(binding, name)
        else:
            name = execution_transport.owned_name()
            record["simulator_container_name"] = name
            # Historical records retain tag/engine fallback, but still own and
            # clean their simulator containers independently of the client.
            simulator_cleanup = lambda: execution_transport.cleanup(
                {"engine_identity": {"executable": "docker"}}, name)
            simulator = ["docker", "run", "--rm", "--pull=never", "--name", name,
                     "--user", f"{os.getuid()}:{os.getgid()}",
                     "-v", f"{run_dir}:{run_dir}", "-v", f"{pdk_root}:{pdk_root}:ro",
                     "-w", str(check_dir), "--entrypoint", "env", image]
            record["tools"]["simulator_image"] = command_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image])
        probe_failed = False
        try:
            record["tools"]["iverilog"] = (runtime_observation.output(
                simulator + ["iverilog", "-V"]) if binding else command_output(
                simulator + ["iverilog", "-V"])).splitlines()[0]
        except BaseException:
            probe_failed = True
            raise
        finally:
            if simulator_cleanup:
                try:
                    simulator_cleanup()
                except Exception as exc:
                    record.setdefault("container_cleanup", []).append({"status": "failed", "error": str(exc)})
                    if not probe_failed:
                        raise
        save(record_path, record)
        precheck_command = ("exec env -u PYTHONPATH -u PYTHONHOME "
                            + " ".join(shlex.quote(x) for x in
                                       (str(args.precheck_python.absolute()), "precheck.py",
                                        "--gds", str(gds), "--tech", "ihp-sg13cmos5l")))
        code = run_logged(["nix-shell", "default.nix", "--run", precheck_command],
                          work / "precheck", env, log, record, record_path)
        record["checks"]["precheck"] = "pass" if code == 0 else "fail"
        save(record_path, record)
        cells = pdk_root / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog"
        models = [pdk_root / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v",
                  cells / "sg13cmos5l_udp.v", cells / "sg13cmos5l_stdcell.v"]
        if not all(path.is_file() for path in models):
            raise PrerequisiteError("missing gate-level PDK Verilog models")
        testbench = check_dir / "testbench.v"
        shutil.copy2(args.testbench, testbench)
        simulation_args = []
        if replay_trace is not None:
            trace = check_dir / "replay-trace.txt"
            shutil.copy2(replay_trace, trace)
            record["inputs"]["replay_trace_sha256"] = digest(trace)
            simulation_args = ["+trace=" + str(trace)]
            save(record_path, record)
        executable = check_dir / "gate.out"
        compile_command = simulator + ["iverilog", "-g2012", "-DFUNCTIONAL", "-DSIM",
                            "-s", args.testbench_top, "-o", str(executable),
                           *(str(path) for path in models), str(netlist), str(testbench)]
        if run_logged(compile_command, check_dir, env, log, record, record_path, cleanup=simulator_cleanup):
            record["checks"]["gate_level"] = "fail"
        elif run_logged(simulator + ["vvp", str(executable), *simulation_args], check_dir, env, log, record, record_path, cleanup=simulator_cleanup):
            record["checks"]["gate_level"] = "fail"
        else:
            record["checks"]["gate_level"] = "pass"
        record["status"] = "completed" if all(
            value == "pass" for value in record["checks"].values()) else "failed"
    except (FlowInterrupted, KeyboardInterrupt) as exc:
        signum = exc.signum if isinstance(exc, FlowInterrupted) else signal.SIGINT
        record["status"] = "interrupted"
        record["error"] = f"interrupted by {signal.Signals(signum).name}"
        record["interrupted_by"] = signal.Signals(signum).name
        exit_code = 128 + signum
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        exit_code = 1
    else:
        exit_code = 0 if record["status"] == "completed" else 1
    finally:
        record["reports"] = [str(path.relative_to(check_dir)) for path in
                             sorted((work / "precheck/reports").glob("*")) if path.is_file()]
        record["ended_at"] = now()
        save(record_path, record)
    return CheckOutcome(check_id, check_dir.resolve(), record_path.resolve(),
                        record["status"], exit_code)


def postcheck(args):
    """Standalone development adapter retaining the historical one-line path."""
    outcome = check(args)
    print(outcome.check_record)
    if outcome.status != "completed":
        print(read_json(outcome.check_record).get("error", "postcheck failed"),
              file=sys.stderr)
    return outcome.exit_code


def metric(value, unit, definition, stage, source, corner=None, reason=None):
    return {"value": value, "unit": unit, "definition": definition,
            "stage": stage, "corner": corner, "source": source,
            "unavailable_reason": reason if value is None else None}


def collect(run_dir, *, context=None, synthesis_state=None):
    record = read_json(run_dir / "run.json") if context is None else context
    flow = run_dir / record["outputs"]["flow"]
    synth = flow / "06-yosys-synthesis/reports/stat.json"
    synth_state = flow / "06-yosys-synthesis/state_out.json"
    metrics = flow / "final/metrics.csv"
    values = {}
    errors = []
    if metrics.is_file():
        try:
            with metrics.open(newline="") as stream:
                for row in csv.reader(stream):
                    if len(row) != 2:
                        raise ValueError(f"expected two columns, got {row!r}")
                    values[row[0]] = row[1]
        except (OSError, ValueError) as exc:
            errors.append(f"{metrics.relative_to(run_dir)}: {exc}")
            values = {}
    stats = None
    synth_creator = None
    if synth.is_file():
        try:
            report = read_json(synth)
            stats = report["design"]
            synth_creator = report.get("creator")
            if not isinstance(stats, dict):
                raise TypeError("design must be an object")
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{synth.relative_to(run_dir)}: {exc}")
            stats = None
    state_metrics = {}
    if synth_state.is_file():
        try:
            state_metrics = read_json(synth_state)["metrics"]
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{synth_state.relative_to(run_dir)}: {exc}")
    if synthesis_state is not None:
        # Segmented evidence has a published official state, not a Yosys stat
        # report. Preserve that origin instead of fabricating a stat.json.
        synth = synth_state = synthesis_state
        state_metrics = read_json(synthesis_state)["metrics"]
        stats = {"area": state_metrics.get("design__instance__area"),
                 "num_cells": state_metrics.get("design__instance__count")}
    # OpenSTA reports this magnitude when a mode has no constrained paths.
    unconstrained_threshold = 1e30
    def number(raw, label):
        if raw is None:
            return None, "report absent or metric missing"
        try:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("nonfinite")
            return value, None
        except (TypeError, ValueError):
            errors.append(f"malformed {label}: {raw!r}")
            return None, "malformed metric"
    output = {}
    for name, field, unit, definition in (
        ("mapped_area", "area", "um^2", "sum of mapped standard-cell areas"),
        ("sequential_area", "sequential_area", "um^2", "mapped sequential-cell area"),
        ("mapped_cells", "num_cells", "cells", "mapped standard-cell instance count"),
    ):
        value, reason = number(stats.get(field) if stats else None, name)
        output[name] = metric(value, unit, definition, "Yosys.Synthesis",
                              str(synth.relative_to(run_dir)), reason=reason)
    for name, prefix in (("setup_slack", "timing__setup__ws"),
                         ("hold_slack", "timing__hold__ws")):
        matches = [(key, raw) for key, raw in values.items() if key.startswith(prefix)]
        parsed = [(key, *number(raw, key)) for key, raw in matches]
        valid = [(key, value) for key, value, _ in parsed if value is not None]
        # Preserve the worst-slack policy over ALL evidence. Mapped official
        # states also contain anonymous aggregates: only an equal-value tie may
        # prefer an actual reported corner, never hide a worse aggregate.
        key, value = min(valid, key=lambda item: (
            item[1], synthesis_state is not None and "corner:" not in item[0])) if valid else (None, None)
        corner = key.split("corner:", 1)[1] if key and "corner:" in key else None
        reason = "report absent or metric missing"
        if value is not None and abs(value) >= unconstrained_threshold:
            value, corner = None, None
            reason = "no constrained timing paths reported"
        output[name] = metric(value, "ns", "worst slack across reported corners",
                              "final", str(metrics.relative_to(run_dir)), corner, reason)
        output[name]["mode"] = "setup" if name == "setup_slack" else "hold"
    for name, field, unit, definition in (
        ("final_standard_cell_area", "design__instance__area__stdcell", "um^2",
         "standard-cell area after the completed physical flow"),
        ("final_utilization", "design__instance__utilization__stdcell", "fraction",
         "standard-cell area divided by placement area"),
    ):
        value, reason = number(values.get(field), name)
        output[name] = metric(value, unit, definition, "final",
                              str(metrics.relative_to(run_dir)), reason=reason)
    checks = {}
    for name, keys in (("drc", ("route__drc_errors", "magic__drc_error__count")),
                       ("lvs", ("design__lvs_error__count",)),
                       ("antenna", ("antenna__violating__nets", "antenna__violating__pins"))):
        matches = {key: values[key] for key in keys if key in values}
        numeric = {key: number(raw, key)[0] for key, raw in matches.items()}
        if len(matches) != len(keys):
            status, reason = "unknown", "metric missing"
        elif any(value is None for value in numeric.values()):
            status, reason = "unknown", "malformed metric"
        else:
            status = "pass" if all(value == 0 for value in numeric.values()) else "fail"
            reason = None
        checks[name] = {"status": status, "source": str(metrics.relative_to(run_dir)),
                        "raw": matches, "reason": reason}
    slacks = [output[name] for name in ("setup_slack", "hold_slack")]
    unconstrained = [item["mode"] for item in slacks
                     if item["unavailable_reason"] == "no constrained timing paths reported"]
    constrained = [item["value"] for item in slacks if item["mode"] not in unconstrained]
    if any(value is None for value in constrained) or not constrained:
        timing_goal = "unknown"
    else:
        timing_goal = "pass" if all(value >= 0 for value in constrained) else "fail"
    cells = stats.get("num_cells_by_type") if stats else None
    synthesis_checks = {}
    for label, field in (("unmapped_instances", "design__instance_unmapped__count"),
                         ("synthesis_errors", "synthesis__check_error__count"),
                         ("inferred_latches", "design__inferred_latch__count")):
        value, reason = number(state_metrics.get(field), label)
        synthesis_checks[label] = metric(value, "count", field, "Yosys.Synthesis",
                                         str(synth_state.relative_to(run_dir)), reason=reason)
    manifest = read_json(run_dir / "project/manifest.json") if (
        run_dir / "project/manifest.json").is_file() else {}
    librelane_tool = {"name": "LibreLane", "version": record.get(
        "environment", {}).get("actual", {}).get("librelane")}
    container = record.get("environment", {}).get("runtime_contract", {}).get(
        "observations", {}).get("container", {})
    mode = record.get("execution_mode", record.get("environment", {}).get(
        "runtime_contract", {}).get("execution_mode"))
    if mode == "dockerized" and container.get("librelane_version"):
        librelane_tool["version"] = container["librelane_version"]
    yosys_tool = {"name": "Yosys", "version": synth_creator}
    for name, item in checks.items():
        item.update({"definition": f"reported {name} violation counts are zero",
                     "unit": "count", "stage": "final", "corner": None,
                     "tool": librelane_tool})
    for name, item in output.items():
        synthesis = item["stage"] == "Yosys.Synthesis"
        item["tool"] = yosys_tool if synthesis else librelane_tool
        item["mode"] = "synthesis" if synthesis else (
            "setup" if name == "setup_slack" else "hold" if name == "hold_slack"
            else "physical")
        if synthesis:
            item["corner"] = manifest.get("target", {}).get("corner")
    for item in synthesis_checks.values():
        item["tool"] = yosys_tool
        item["mode"] = "synthesis"
        item["corner"] = manifest.get("target", {}).get("corner")
    result = {"schema_version": 1, "run_id": record["run_id"],
              "build_identity": record["build_identity"], "process_status": record["status"],
              "requested_stage": record["requested_stage"],
              "completed_stage": record["completed_stage"],
              "tool": librelane_tool,
              "mode": "nominal and reported corners", "metrics": output, "checks": checks,
              "timing_goal": timing_goal,
              "timing_unconstrained_modes": unconstrained,
              "mapped_cell_types": cells,
              "synthesis_checks": synthesis_checks,
              "raw_artifacts": sorted(str(path.relative_to(run_dir)) for path in
                                      set((synth, metrics, synth_state,
                                           flow / "final/metrics.json")
                                          + tuple(flow.glob("*/reports/*.rpt"))
                                          + tuple(flow.glob("*/reports/*.json"))
                                          + tuple((flow / "final/gds").glob("*.gds"))
                                          + tuple((flow / "final/nl").glob("*.v")))
                                      if path.is_file()),
              "errors": errors}
    result["postchecks"] = [read_json(path) for path in
                            sorted((run_dir / "checks").glob("*/postcheck.json"))]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("preflight", "run"):
        p = sub.add_parser(name)
        p.add_argument("bundle", type=Path)
        p.add_argument("--support-tools", required=True, type=Path)
        p.add_argument("--pdk-root", required=True, type=Path)
        p.add_argument("--python", required=True, type=Path)
        p.add_argument("--allow-python-mismatch", action="store_true")
        p.add_argument("--native", action="store_true", help="Use locally installed EDA tools")
        if name == "preflight":
            p.add_argument("--output", type=Path,
                           help="Write the report here as well as to stdout")
        if name == "run":
            p.add_argument("--runs", required=True, type=Path)
            p.add_argument("--stage", choices=("synthesis", "full"), default="synthesis")
    p = sub.add_parser("collect")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--output", type=Path)
    p = sub.add_parser("postcheck")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--support-tools", required=True, type=Path)
    p.add_argument("--pdk-root", required=True, type=Path)
    p.add_argument("--precheck-python", required=True, type=Path)
    p.add_argument("--testbench", required=True, type=Path)
    p.add_argument("--testbench-top", required=True)
    args = parser.parse_args()
    try:
        if args.action == "preflight":
            result = preflight(args.bundle.resolve(), args.support_tools, args.pdk_root,
                               args.python, args.allow_python_mismatch, args.native)
            print(json.dumps(result, indent=2, sort_keys=True))
            # The same report as a file, so a run's evidence can keep it: the
            # report is the reason a run was allowed to start, and stdout from a
            # seven-step flow does not survive as a record.
            if args.output:
                save(args.output, result)
            return 0 if result["ready"] else 1
        if args.action == "run":
            return execute(args)
        if args.action == "postcheck":
            signal.signal(signal.SIGINT, interrupt_handler)
            signal.signal(signal.SIGTERM, interrupt_handler)
            return postcheck(args)
        result = collect(args.run_dir.resolve())
        if args.output:
            save(args.output, result)
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            PrerequisiteError) as exc:
        print(f"coarse flow oracle: {exc}", file=sys.stderr)
        return 2
    except FlowInterrupted as exc:
        print(f"coarse flow oracle: {exc}", file=sys.stderr)
        return 128 + exc.signum


if __name__ == "__main__":
    sys.exit(main())
