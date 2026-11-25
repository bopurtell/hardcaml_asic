"""Explicit CLI4 delegation; OCaml owns execution and persisted-record validation.

Installed helpers are selected from this package's prefix, never PATH. Source
adapters use the Dune-built equivalents and fail actionably when not built.
"""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys


PACKAGE = Path(__file__).resolve().parent


def binary(name):
    if PACKAGE.parent.name == "flow" and PACKAGE.parents[1].name == "hardcaml_asic" and PACKAGE.parents[2].name == "lib":
        path = PACKAGE.parents[3] / "bin" / ("hardcaml-asic-experimental-" + name.replace("_", "-"))
        remedy = "Reinstall hardcaml_asic into this prefix."
    else:
        path = PACKAGE.parents[1] / "_build/default/bin" / ("experimental_" + name + ".exe")
        # Dune test imports live in _build/default/flow already.
        if PACKAGE.parents[1].name == "default":
            path = PACKAGE.parents[1] / "bin" / ("experimental_" + name + ".exe")
        remedy = "Run dune build -j20 @install in the provider checkout."
    if not path.is_file() or not os.access(path, os.X_OK):
        raise OSError(f"OCaml backend is missing: {path}. {remedy}")
    return path


def query(name, arguments):
    result = subprocess.run([str(binary(name)), *map(str, arguments)],
                            capture_output=True, text=True, check=False)
    if result.returncode not in (0, 2):
        raise OSError(f"OCaml {name} exited {result.returncode}: {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise OSError(f"OCaml {name} returned no valid payload: {result.stderr.strip()}") from exc
    return result.returncode, value


def stages(args, cwd, absolute, interpreter_absolute):
    executable = binary("stages")
    workspace = absolute(args.workspace, cwd)
    evidence_root = workspace.parent / "stage-preflight"
    if evidence_root == workspace:
        evidence_root = workspace.parent / "stage-preflight-evidence"
    command = [str(executable), "--public-cli4", "--bundle", str(absolute(args.bundle, cwd)),
               "--resources", str(PACKAGE), "--workspace", str(workspace),
               "--checkpoint-store", str(absolute(args.checkpoint_store, cwd)),
                "--evidence-root", str(evidence_root),
               "--support-tools", str(absolute(args.support_tools, cwd)),
               "--pdk-root", str(absolute(args.pdk_root, cwd)),
               "--python", str(interpreter_absolute(args.python, cwd)),
               "--timeout", str(args.timeout), "--request", json.dumps({"caller_cwd": str(cwd.resolve()),
                   **{k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}}, allow_nan=False)]
    for name, flag in (("endpoint", "through"), ("plan", "plan"),
                       ("checkpoint", "checkpoint"), ("observation_store", "observation-store")):
        value = getattr(args, name)
        if value is not None:
            command += ["--" + flag, str(value if name == "endpoint" else absolute(value, cwd))]
    for name in ("json", "no_observation"):
        if getattr(args, name):
            command.append("--" + name.replace("_", "-"))
    # No supervising Python process: PID and signal delivery survive delegation.
    os.execv(str(executable), command)


def preflight(args):
    executable = binary("preflight")
    command = [str(executable), "--resources", str(PACKAGE)]
    for name in ("bundle", "support_tools", "pdk_root", "python", "results_root", "output"):
        value = getattr(args, name)
        if value is not None:
            command += ["--" + name.replace("_", "-"), str(value)]
    for name in ("native", "json", "verbose"):
        if getattr(args, name):
            command.append("--" + name)
    os.execv(str(executable), command)


def run_command(args):
    command = [str(binary("run")), "--resources", str(PACKAGE), "--json"]
    for name in ("bundle", "support_tools", "pdk_root", "python", "runs", "stage"):
        command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
    if args.native:
        command.append("--native")
    return command


def run(args):
    """Typed callback for the retained Python execute runner; no fallback."""
    from .coarse_flow_oracle import RunOutcome, FlowInterrupted
    process = subprocess.Popen(run_command(args), stdout=subprocess.PIPE, text=True)
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda n, _: process.send_signal(n))
        stdout, _ = process.communicate()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    try:
        value = json.loads(stdout)
        outcome = RunOutcome(value["run_id"], Path(value["run_dir"]), Path(value["run_record"]),
                             value["status"], value["exit_code"])
        if process.returncode != outcome.exit_code:
            raise ValueError("outcome exit code differs from helper")
        return outcome
    except (ValueError, KeyError, TypeError) as exc:
        if process.returncode in (130, 143):
            raise FlowInterrupted(process.returncode - 128) from exc
        raise OSError(f"OCaml run returned no valid outcome (exit {process.returncode})") from exc


def postcheck_command(args):
    command = [str(binary("postcheck")), "--json"]
    for name in ("run_dir", "support_tools", "pdk_root", "precheck_python", "testbench", "testbench_top"):
        command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
    for name in ("replay_trace", "timeout"):
        value = getattr(args, name, None)
        if value is not None:
            command += ["--" + name.replace("_", "-"), str(value)]
    return command


def postcheck(args):
    """Native provider callback retaining Python CheckOutcome and signal delivery."""
    from .coarse_flow_oracle import CheckOutcome, FlowInterrupted
    process = subprocess.Popen(postcheck_command(args), stdout=subprocess.PIPE, text=True)
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda n, _: process.send_signal(n))
        stdout, _ = process.communicate()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    try:
        value = json.loads(stdout)
        outcome = CheckOutcome(value["check_id"], Path(value["check_dir"]), Path(value["check_record"]),
                               value["status"], value["exit_code"])
        if process.returncode != outcome.exit_code:
            raise ValueError("outcome exit code differs from helper")
        return outcome
    except (ValueError, KeyError, TypeError) as exc:
        if process.returncode in (130, 143):
            raise FlowInterrupted(process.returncode - 128) from exc
        raise OSError(f"OCaml postcheck returned no valid outcome (exit {process.returncode})") from exc


def public_postcheck(args):
    command = postcheck_command(args)
    if not args.json:
        command.remove("--json")
    command.append("--public-cli4")
    if args.verbose:
        command.append("--verbose")
    os.execv(command[0], command)


def public_execute(args):
    command = [str(binary("execute")), "--resources", str(PACKAGE)]
    for name in ("bundle", "support_tools", "pdk_root", "python", "runs", "stage",
                 "precheck_python", "testbench", "testbench_top", "archive"):
        value = getattr(args, name, None)
        if value is not None:
            command += ["--" + name.replace("_", "-"), str(value)]
    for name in ("native", "json", "verbose", "force"):
        if getattr(args, name, False):
            command.append("--" + name)
    os.execv(command[0], command)


def public_run(args):
    command = run_command(args)
    if not args.json:
        command.remove("--json")
    command.append("--public-cli4")
    if args.verbose:
        command.append("--verbose")
    os.execv(command[0], command)


def collect_command(args):
    command = [str(binary("collect")), "--run-dir", str(args.run_dir)]
    if args.stdout:
        command.append("--stdout")
    else:
        command += ["--output", str(args.output)]
    for name in ("json", "quiet", "verbose"):
        if getattr(args, name, False):
            command.append("--" + name)
    return command


def collect(args):
    """Compatibility callback for transitional Python execute; authoritative native writer."""
    result = subprocess.run(collect_command(args), check=False, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise OSError(f"OCaml collect exited {result.returncode}")
    return 0


def public_collect(args):
    command = collect_command(args)
    os.execv(command[0], command)


def report_command(args):
    command = [str(binary("report")), "--run-dir", str(args.run_dir)]
    for name in ("json", "diagnostics_only", "verbose"):
        if getattr(args, name, False):
            command.append("--" + name.replace("_", "-"))
    return command


def report(args):
    """Transitional execute callback; preserve all three native acceptance exits."""
    result = subprocess.run(report_command(args), check=False, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def public_report(args):
    command = report_command(args)
    os.execv(command[0], command)


def archive_command(args):
    command = [str(binary("archive")), "--run-dir", str(args.run_dir)]
    if args.output is not None:
        command += ["--output", str(args.output)]
    for name in ("list", "force", "json", "quiet", "verbose"):
        if getattr(args, name, False):
            command.append("--" + name)
    return command


def archive(args):
    """Transitional aggregate execute callback; native policy/publication only."""
    result = subprocess.run(archive_command(args), check=False, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def public_archive(args):
    command = archive_command(args)
    os.execv(command[0], command)


def watch(args, directory):
    executable = binary("observation_read")
    command = [str(executable), "watch", str(directory), "--interval", str(args.interval)]
    if args.json:
        command.append("--json")
    for name, flag in (("stream_id", "--stream-id"), ("after", "--after")):
        value = getattr(args, name)
        if value is not None:
            command += [flag, str(value)]
    os.execv(str(executable), command)


def public_finalization(args, cwd, absolute, interpreter_absolute):
    command = [str(binary("finalization")), "--public-cli4"]
    if args.command == "finalization-inspect":
        command += ["--inspect", "--directory", str(absolute(args.attempt, cwd))]
    else:
        command += ["--execution", str(absolute(args.execution, cwd)),
                    "--store", str(absolute(args.operation_store, cwd))]
        for name in ("support_tools", "precheck_python", "testbench", "replay_trace"):
            value = getattr(args, name)
            if value is not None:
                resolve = interpreter_absolute if name == "precheck_python" else absolute
                command += ["--" + name.replace("_", "-"), str(resolve(value, cwd))]
        if args.testbench_top is not None:
            command += ["--testbench-top", args.testbench_top]
        if args.accept:
            command.append("--accept")
    for name in ("json", "verbose"):
        if getattr(args, name, False):
            command.append("--" + name)
    os.execv(command[0], command)
