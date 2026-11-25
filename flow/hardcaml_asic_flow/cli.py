"""Retained compatibility CLI/API and differential oracle.

The installed `hardcaml-asic-flow` command is owned by bin/flow_cli.ml; it never
imports this module. The Python parser/injected Operations seam remains for
transitional consumers and tests pending coordinated resource-validator cleanup.

The dispatcher owns human/explicit-JSON presentation, result-store defaults,
argument parsing, path and tool-location precedence, exit
status mapping; the shared runner owns the coarse `execute` composition.
Stages exec the linked OCaml preparation/runner/journal implementation;
checkpoint and observation cold queries delegate to the typed OCaml readers.
Run, preflight and collection delegate to installed native services. The remaining
aggregate runner and provisioner use Python.
Foreground watch execs the same native observation helper.

P6.4a packages that dispatcher. It does not add identity or waiver semantics:
`--dependency-lock` and `--waiver` still parse (so the option surface is settled
and testable) and are then rejected with an explicit P6.5 diagnostic, rather than
being silently accepted and ignored. `--version` reports the package version and
says plainly that source identity is unstamped; see `identity`.
"""

import argparse
import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys

from . import MINIMUM_PYTHON, archive as archiver, identity, paths, coarse_flow_oracle, result_store
from . import report as reporter
from . import execution_plan, runner, presentation


PROGRAM = "hardcaml-asic-flow"
WAIVER_CODES = {
    "python-version",
    "tool-source",
    "dependency-revision",
    "generator-tool-mismatch",
    "legacy-generator-identity",
}


class ContractError(Exception):
    pass


class ContractInterrupted(Exception):
    def __init__(self, signum):
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


class Operations:
    """Delegation to the existing implementations, with no duplicated policy."""

    def __init__(self):
        self.coarse_flow_oracle = coarse_flow_oracle
        self.reporter = reporter
        self.archiver = archiver

    @staticmethod
    def require_p6_5(args):
        if getattr(args, "dependency_lock", None) or getattr(args, "waiver", None):
            raise ContractError(
                "dependency identity and waiver production handling belongs to P6.5")

    def provision(self, args):
        """Run the packaged provisioner against the packaged lock.

        `bash` is named explicitly rather than relying on the script's execute
        bit, so provisioning works from a read-only prefix and does not depend on
        which Dune install section placed the file. `--lock-file` is internal
        plumbing supplied here from `paths`: the public `provision` command has
        no option for it, so a caller cannot quietly point the flow at different
        target pins. Private `--host-python` retains this orchestration
        interpreter for the installed managed-runtime helper, including when
        the host runs a different minor from the venv base.
        """
        script = paths.require(paths.PROVISIONER, "the flow provisioner")
        lock = paths.require(paths.TOOLCHAIN_LOCK, "the toolchain lock")
        environment = os.environ.copy()
        environment["TOOLCHAIN"] = str(args.toolchain)
        command = ["bash", str(script), "--lock-file", str(lock),
                   "--host-python", sys.executable]
        if args.python is not None:
            command.extend(["--python", str(args.python)])
        for option in ("check", "offline", "no_container"):
            if getattr(args, option):
                command.append("--" + option.replace("_", "-"))
        return subprocess.run(command, env=environment, check=False).returncode

    def preflight(self, args):
        self.require_p6_5(args)
        outcome = result_store.preflight_operation(
            args.bundle, results_root=args.results_root, output=args.output,
            probe=lambda: self.coarse_flow_oracle.preflight(
                args.bundle, args.support_tools, args.pdk_root, args.python,
                False, args.native))
        if args.json:
            print(json.dumps(outcome.result, indent=2, sort_keys=True))
            print(f"preflight: report {outcome.report_path}", file=sys.stderr)
        else:
            print(f"preflight: {outcome.status}")
            if outcome.receipt_path:
                print(f"operation: {outcome.receipt_path}")
            print(f"report: {outcome.report_path}")
        for issue in outcome.result["issues"]:
            print(f"preflight: {issue}", file=sys.stderr)
        if args.verbose:
            for name, value in outcome.result.items():
                if name not in ("ready", "issues"):
                    print(f"preflight {name}: {value}", file=sys.stderr)
        return outcome.exit_code

    def run(self, args):
        self.require_p6_5(args)
        from . import ocaml_backend
        try:
            return ocaml_backend.run(args)
        except self.coarse_flow_oracle.FlowInterrupted as exc:
            raise ContractInterrupted(exc.signum) from exc

    def postcheck(self, args):
        self.require_p6_5(args)
        from . import ocaml_backend
        try:
            return ocaml_backend.postcheck(args)
        except self.coarse_flow_oracle.FlowInterrupted as exc:
            raise ContractInterrupted(exc.signum) from exc
        except self.coarse_flow_oracle.PrerequisiteError as exc:
            raise ContractError(str(exc)) from exc

    def collect(self, args):
        from . import ocaml_backend
        return ocaml_backend.collect(args)

    def report(self, args):
        from . import ocaml_backend
        return ocaml_backend.report(args)

    def archive(self, args):
        from . import ocaml_backend
        return ocaml_backend.archive(args)


def add_toolchain(parser, *, flow_python=False, precheck_python=False):
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--support-tools", type=Path)
    parser.add_argument("--pdk-root", type=Path)
    if flow_python:
        parser.add_argument("--python", type=Path)
    if precheck_python:
        parser.add_argument("--precheck-python", type=Path)


def add_identity_seam(parser):
    parser.add_argument("--dependency-lock", type=Path)
    parser.add_argument("--waiver", action="append", default=[], metavar="CODE=REASON")


def nonempty_path(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("expected a nonempty path")
    return Path(value)


def add_presentation(parser):
    # SUPPRESS preserves a global flag when the command-local spelling is absent.
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print the machine-readable payload (default: human summary)")
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="expanded diagnostics on stderr")


def add_results_root(parser):
    parser.add_argument("--results-root", type=nonempty_path,
                        help="result store root (default: caller cwd/_flow_results)")


def build_parser():
    # The program name is fixed rather than taken from argv[0]: the launcher
    # invokes this module through `python3 -I -c`, where argv[0] is "-c", and a
    # repository adapter should still print the installed command's usage.
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Provision, validate, run, collect, report and archive "
                    "TT/LibreLane work from an emitted hardcaml_asic bundle.")
    parser.add_argument("--version", action="store_true",
                        help="package version and source-identity state")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable --version or command payload")
    parser.add_argument("--verbose", action="store_true",
                        help="expanded diagnostics on stderr")
    sub = parser.add_subparsers(dest="command")

    provision = sub.add_parser("provision")
    provision.add_argument("--toolchain", type=Path)
    provision.add_argument("--python", type=nonempty_path,
                           help="external base for new venvs (default: pinned managed Python; must match the locked minor and provide venv, ensurepip and Tcl)")
    provision.add_argument("--check", action="store_true")
    provision.add_argument("--offline", action="store_true")
    provision.add_argument("--no-container", action="store_true")

    preflight = sub.add_parser("preflight")
    add_presentation(preflight)
    add_results_root(preflight)
    preflight.add_argument("bundle", type=nonempty_path)
    add_toolchain(preflight, flow_python=True)
    add_identity_seam(preflight)
    preflight.add_argument("--native", action="store_true")
    preflight.add_argument("--output", type=nonempty_path,
                           help="exact report file; parent must exist (no operation directory)")

    run = sub.add_parser("run")
    add_presentation(run)
    add_results_root(run)
    run.add_argument("bundle", type=nonempty_path)
    run.add_argument("--runs", type=nonempty_path,
                     help="override implementation store (default: results-root/runs)")
    run.add_argument("--stage", required=True, choices=("synthesis", "full"))
    add_toolchain(run, flow_python=True)
    add_identity_seam(run)
    run.add_argument("--native", action="store_true")

    postcheck = sub.add_parser("postcheck")
    add_presentation(postcheck)
    postcheck.add_argument("run_dir", type=Path)
    postcheck.add_argument("--testbench", required=True, type=Path)
    postcheck.add_argument("--testbench-top", required=True)
    postcheck.add_argument("--replay-trace", type=Path)
    postcheck.add_argument("--timeout", type=float, help="optional per-tool timeout in seconds (default: no limit)")
    add_toolchain(postcheck, precheck_python=True)
    add_identity_seam(postcheck)

    collect = sub.add_parser("collect")
    add_presentation(collect)
    collect.add_argument("run_dir", type=Path)
    destination = collect.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true",
                             help="legacy read-only JSON output; does not write results.json")

    report = sub.add_parser("report")
    add_presentation(report)
    report.add_argument("run_dir", type=Path)

    archive = sub.add_parser("archive")
    add_presentation(archive)
    archive.add_argument("run_dir", type=Path)
    destination = archive.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--list", action="store_true")
    archive.add_argument("--force", action="store_true")

    execute = sub.add_parser("execute")
    add_presentation(execute)
    add_results_root(execute)
    execute.add_argument("bundle", type=nonempty_path)
    execute.add_argument("--runs", type=nonempty_path,
                         help="override implementation store (default: results-root/runs)")
    execute.add_argument("--stage", required=True, choices=("synthesis", "full"))
    add_toolchain(execute, flow_python=True, precheck_python=True)
    add_identity_seam(execute)
    execute.add_argument("--native", action="store_true")
    execute.add_argument("--testbench", type=Path)
    execute.add_argument("--testbench-top")
    execute.add_argument("--archive", type=Path)
    execute.add_argument("--force", action="store_true")
    stages = sub.add_parser("stages", help="opt-in sequential physical recipe (acceptance separate)")
    add_presentation(stages)
    stages.add_argument("bundle", type=nonempty_path)
    for name in ("support-tools", "pdk-root", "python", "workspace", "checkpoint-store"):
        stages.add_argument("--" + name, type=nonempty_path, required=True)
    recipe = stages.add_mutually_exclusive_group(required=True)
    recipe.add_argument("--endpoint", choices=("synthesis", "floorplan", "placement", "cts", "routing", "timing", "signoff"),
                         help="expand contiguous stages through this endpoint")
    recipe.add_argument("--plan", type=nonempty_path, help="strict version-2 endpoint or version-3 stage sequence JSON")
    stages.add_argument("--checkpoint", type=nonempty_path, help="published path-bound handoff")
    stages.add_argument("--timeout", type=int, default=600)
    observation_mode = stages.add_mutually_exclusive_group()
    observation_mode.add_argument("--observation-store", type=nonempty_path,
                        help="journal root (default: resolved workspace parent/observations)")
    observation_mode.add_argument("--no-observation", action="store_true",
                        help="disable journaling and retain single-object machine output")
    finalize = sub.add_parser("finalize", help="opt-in terminal segmented results and acceptance operation")
    add_presentation(finalize)
    finalize.add_argument("execution", type=nonempty_path)
    finalize.add_argument("--operation-store", type=nonempty_path, required=True)
    finalize.add_argument("--accept", action="store_true", help="run external postchecks and existing full acceptance policy")
    for name in ("support-tools", "precheck-python", "testbench", "replay-trace"):
        finalize.add_argument("--" + name, type=nonempty_path)
    finalize.add_argument("--testbench-top")
    receipt = sub.add_parser("finalization-inspect", help="read separate schema-1 operation receipt")
    add_presentation(receipt)
    receipt.add_argument("attempt", type=nonempty_path)
    inspect_checkpoint = sub.add_parser("checkpoint-inspect", help="read-only host checkpoint validation (not runtime readiness)")
    add_presentation(inspect_checkpoint)
    inspect_checkpoint.add_argument("checkpoint", type=nonempty_path)
    inspect_checkpoint.add_argument("--compatibility", type=nonempty_path,
                                    help="expected compatibility JSON; defaults to recorded compatibility")
    inspect_checkpoint.add_argument("--restart-to", choices=("synthesis", "floorplan", "placement", "cts", "routing", "timing", "signoff"))
    observation = sub.add_parser("observation", help="read-only structured stage observation API")
    add_presentation(observation)
    queries = observation.add_subparsers(dest="query", required=True)
    describe = queries.add_parser("describe")
    add_presentation(describe)
    inspect = queries.add_parser("inspect")
    add_presentation(inspect)
    inspect.add_argument("attempt", type=nonempty_path)
    updates = queries.add_parser("read-updates")
    add_presentation(updates)
    updates.add_argument("attempt", type=nonempty_path)
    updates.add_argument("--stream-id", required=True)
    updates.add_argument("--after", type=int, required=True)
    watch = queries.add_parser("watch", help="foreground read-only incremental follow")
    add_presentation(watch)
    watch.add_argument("attempt", type=nonempty_path)
    watch.add_argument("--stream-id")
    watch.add_argument("--after", type=int)
    watch.add_argument("--interval", type=positive_interval, default=0.5,
                       help="positive finite polling seconds (default: 0.5)")
    return parser


def positive_interval(value):
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be positive and finite") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("interval must be positive and finite")
    return interval


def observation_watch(args, directory):
    from . import ocaml_backend
    return ocaml_backend.watch(args, directory)


def silence_broken_stdout():
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        pass


def absolute(path, cwd):
    if path is None:
        return None
    return (path if path.is_absolute() else cwd / path).resolve()


def interpreter_absolute(path, cwd):
    """Anchor an interpreter without dereferencing its venv leaf or parents.

    Keep lexical ``..`` components too: collapsing them across a symlink can
    select a different environment. Directory arguments still use ``absolute``.
    """
    if path is None:
        return None
    return (path if path.is_absolute() else cwd / path).absolute()


def validate_waivers(parser, args):
    for waiver in getattr(args, "waiver", []):
        code, separator, reason = waiver.partition("=")
        if not separator or code not in WAIVER_CODES or not reason.strip():
            parser.error("--waiver must be a recognized CODE=REASON with a nonempty reason")


def resolve_paths(parser, args, environ, cwd):
    for name in ("bundle", "run_dir", "runs", "results_root", "output", "archive",
                 "testbench", "replay_trace", "dependency_lock", "toolchain",
                  "support_tools", "pdk_root"):
        if hasattr(args, name):
            setattr(args, name, absolute(getattr(args, name), cwd))
    for name in ("python", "precheck_python"):
        if hasattr(args, name):
            setattr(args, name, interpreter_absolute(getattr(args, name), cwd))

    if args.command == "provision":
        root = args.toolchain or absolute(Path(environ.get("TOOLCHAIN", ".toolchain")), cwd)
        args.toolchain = root
        return

    if args.command in ("preflight", "run", "execute"):
        args.results_root = args.results_root or absolute(Path("_flow_results"), cwd)
        if args.command in ("run", "execute"):
            args.runs = args.runs or args.results_root / "runs"

    required = {
        "preflight": ("support_tools", "pdk_root", "python"),
        "run": ("support_tools", "pdk_root", "python"),
        "postcheck": ("support_tools", "pdk_root", "precheck_python"),
        "execute": ("support_tools", "pdk_root", "python"),
    }.get(args.command, ())
    root = getattr(args, "toolchain", None)
    if root is None and environ.get("TOOLCHAIN"):
        root = absolute(Path(environ["TOOLCHAIN"]), cwd)
    defaults = {
        "support_tools": "tt",
        "pdk_root": "pdk",
        "python": ".venv/bin/python",
        "precheck_python": ".venv-precheck/bin/python",
    }
    for name in required:
        if getattr(args, name) is None and root is not None:
            normalize = interpreter_absolute if name in ("python", "precheck_python") else absolute
            setattr(args, name, normalize(root / defaults[name], cwd))
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} or --toolchain/TOOLCHAIN is required")
    if args.command == "execute" and args.stage == "full":
        if args.precheck_python is None and root is not None:
            args.precheck_python = interpreter_absolute(root / defaults["precheck_python"], cwd)
        if args.precheck_python is None:
            parser.error("--precheck-python or --toolchain/TOOLCHAIN is required for full execution")

    if hasattr(args, "output") and args.output is None and args.command == "collect":
        args.output = args.run_dir / "results.json"


def validate_contract(parser, args):
    validate_waivers(parser, args)
    if args.command == "provision" and (args.json or args.verbose):
        parser.error("provision uses bootstrap human output; --json/--verbose are unsupported")
    if args.command == "archive" and args.list and args.json:
        parser.error("--json with archive --list is unsupported; use human --list")
    if args.command in ("run", "execute"):
        args.allow_python_mismatch = False
    if getattr(args, "force", False) and not (
            args.command == "archive" and args.output
            or args.command == "execute" and args.archive):
        parser.error("--force requires an explicit archive destination")
    if args.command in ("run", "execute") and args.native and args.stage == "full":
        parser.error("native execution supports synthesis only")
    if args.command == "execute":
        postcheck_values = (args.testbench, args.testbench_top, args.precheck_python)
        if args.stage == "full" and not all(postcheck_values):
            parser.error("full execution requires --testbench, --testbench-top, and precheck Python")
        if args.stage == "synthesis" and any(postcheck_values):
            parser.error("synthesis execution rejects postcheck-only inputs")


def execute_sequence(args, operations):
    """Render the shared runner's result using the historical CLI contract."""
    def observe(_action, check):
        if args.json:
            outcome_diagnostics(check, "postcheck", args.verbose)
        else:
            summarize_outcome(check, "postcheck", args.verbose)

    plan = execution_plan.resolve(args.stage, archive=args.archive is not None)
    execution = runner.execute(plan, runner.ServiceAdapter(args, operations), observe=observe)
    outcome = execution.run_outcome
    # Keep the legacy aggregate, including its best-effort collection convention,
    # separate from the runner's richer occurrence status and acceptance facts.
    steps = [dict(step) for step in execution.steps]
    for step in steps:
        if step.get("best_effort"):
            step["status"] = "failed" if "error" in step else "completed"
            if "error" in step:
                step["exit_code"] = 2
        if step["operation"] == "postcheck" and step["status"] == "interrupted":
            step.pop("error", None)
    result = {"run_id": outcome.run_id, "run_dir": str(outcome.run_dir),
              "run_record": str(outcome.run_record), "steps": steps,
              "status": execution.status, "exit_code": execution.exit_code}
    if execution.archive is not None:
        result["archive"] = execution.archive
    output = lambda text: print(text, file=sys.stderr if args.json else sys.stdout)
    for step in result["steps"]:
        output(f"execute: {step['operation']} {step['status']} "
               f"({step.get('duration_seconds', 0):.3f}s)")
        if step.get("error"):
            print(f"execute: {step['operation']}: {step['error']}", file=sys.stderr)
    output(f"execute: {execution.status}")
    if args.json:
        output(f"execute: run {outcome.run_dir}")
    else:
        summarize_outcome(outcome, "run", verbose=args.verbose)
    if result.get("archive"):
        output(f"execute: archive {result['archive']}")
    if args.json:
        outcome_diagnostics(outcome, "run", args.verbose)
        print(json.dumps(result, sort_keys=True))
    return execution.exit_code


def unsupported_python():
    """A diagnostic when the host interpreter predates what the flow modules use."""
    if sys.version_info >= MINIMUM_PYTHON:
        return None
    wanted = ".".join(str(part) for part in MINIMUM_PYTHON)
    running = ".".join(str(part) for part in sys.version_info[:3])
    return (f"{PROGRAM}: requires Python {wanted} or newer to run the flow "
            f"modules; this interpreter is {running} ({sys.executable})")


def outcome_diagnostics(outcome, operation, verbose=False):
    record = outcome.run_record if operation == "run" else outcome.check_record
    if Path(record).is_file():
        payload = coarse_flow_oracle.read_json(record)
        if payload.get("error"):
            print(f"{operation}: {payload['error']}", file=sys.stderr)
        if verbose:
            for name, value in payload.items():
                if name != "error":
                    print(f"{operation} {name}: {value}", file=sys.stderr)


def summarize_outcome(outcome, operation, verbose=False):
    print(f"{operation}: {outcome.status} (exit {outcome.exit_code})")
    if operation == "run":
        print(f"run id: {outcome.run_id}")
        print(f"run: {outcome.run_dir}")
        print(f"record: {outcome.run_record}")
        for name in ("preflight.json", "results.json", "execution.log"):
            path = outcome.run_dir / name
            if path.is_file():
                print(f"{name}: {path}")
    else:
        print(f"check id: {outcome.check_id}")
        print(f"check: {outcome.check_dir}")
        print(f"record: {outcome.check_record}")
        log = outcome.check_dir / "postcheck.log"
        if log.is_file():
            print(f"log: {log}")
    outcome_diagnostics(outcome, operation, verbose)


def main(argv=None, operations=None, environ=None, cwd=None):
    unsupported = unsupported_python()
    if unsupported is not None:
        print(unsupported, file=sys.stderr)
        return 2
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    # --version is answered before any path resolution or operation dispatch, so
    # it needs no bundle, toolchain, PDK, container or Git and touches nothing.
    if args.version:
        return identity.report(as_json=args.json)
    if args.command is None:
        parser.print_help()
        return 0
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    if args.command == "stages" or args.command == "checkpoint-inspect" or (
            args.command == "observation" and args.query != "watch"):
        from . import ocaml_backend
        try:
            if args.command == "stages":
                return ocaml_backend.stages(args, cwd, absolute, interpreter_absolute)
            if args.command == "checkpoint-inspect":
                path = absolute(args.checkpoint, cwd)
                command = ["--json", str(path)]
                if args.compatibility:
                    command += ["--compatibility", str(absolute(args.compatibility, cwd))]
                if args.restart_to:
                    command += ["--restart-to", args.restart_to]
                code, value = ocaml_backend.query("checkpoint_inspect", command)
                if args.json:
                    print(json.dumps(value, sort_keys=True, allow_nan=False))
                elif code:
                    presentation.inspection_error("checkpoint", path, value)
                else:
                    presentation.checkpoint(value, path=path, restart_to=args.restart_to)
                if code:
                    print(f"checkpoint-inspect: {value['error']}", file=sys.stderr)
            else:
                path = absolute(args.attempt, cwd) if hasattr(args, "attempt") else "describe"
                command = [args.query]
                if args.query != "describe":
                    command.append(str(path))
                if args.query == "read-updates":
                    command += ["--stream-id", args.stream_id, "--after", str(args.after)]
                code, value = ocaml_backend.query("observation_read", command)
                if args.json:
                    print(json.dumps(value, sort_keys=True, allow_nan=False))
                elif code:
                    presentation.inspection_error("observation", path, value)
                elif args.query == "describe":
                    presentation.observation_description(value)
                elif args.query == "read-updates":
                    presentation.observation_updates(value, path=path)
                else:
                    presentation.observation(value, path=path)
                if code:
                    print(f"observation {args.query}: {value['error']['detail']}", file=sys.stderr)
            return code
        except OSError as exc:
            print(f"hardcaml-asic-flow: {exc}", file=sys.stderr)
            return 2
    def allocated(value):
        if args.json:
            print(json.dumps(value), flush=True)
        else:
            presentation.allocation(value, output=lambda line: print(line, flush=True))

    if args.command in ("finalize", "finalization-inspect"):
        if operations is None:
            from .ocaml_backend import public_finalization
            try:
                return public_finalization(args, cwd, absolute, interpreter_absolute)
            except OSError as exc:
                print(f"{args.command}: {exc}", file=sys.stderr)
                return 2
        from . import finalization, checkpoint
        try:
            if args.command == "finalization-inspect":
                value = finalization.inspect(absolute(args.attempt, cwd))
                if args.json:
                    print(json.dumps(value, sort_keys=True, allow_nan=False))
                else:
                    presentation.finalization(value, path=absolute(args.attempt, cwd))
                return 0
            directory, value = finalization.finalize(execution=absolute(args.execution, cwd),
                store=absolute(args.operation_store, cwd), accept=args.accept,
                support_tools=absolute(args.support_tools, cwd),
                precheck_python=interpreter_absolute(args.precheck_python, cwd),
                testbench=absolute(args.testbench, cwd), testbench_top=args.testbench_top,
                replay_trace=absolute(args.replay_trace, cwd),
                allocated=lambda directory, attempt: allocated({"contract": finalization.CONTRACT,
                    "schema_version": 1, "event": "allocated", "attempt": str(directory), "attempt_id": attempt}))
            if args.json:
                print(json.dumps({"attempt": str(directory), **value}, sort_keys=True, allow_nan=False))
            else:
                presentation.finalization(value, path=directory)
            if value.get("error"):
                print(f"finalize: {value['error']}", file=sys.stderr)
            elif value["exit_code"]:
                print(f"finalize: {value['status']} (exit {value['exit_code']}); "
                      f"acceptance {value['acceptance']}", file=sys.stderr)
            return value["exit_code"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            value = {"contract": finalization.CONTRACT, "schema_version": 1, "error": str(exc)}
            print(f"{args.command}: {exc}", file=sys.stderr)
            if args.json:
                print(json.dumps(value, sort_keys=True))
            else:
                presentation.inspection_error("finalization", absolute(
                    args.attempt if args.command == "finalization-inspect" else args.execution, cwd), value)
            return 2
    if args.command == "observation" and args.query == "watch":
        try:
            return observation_watch(args, absolute(args.attempt, cwd))
        except OSError as exc:
            print(f"observation watch: {exc}", file=sys.stderr)
            return 2
    resolve_paths(parser, args, environ, cwd)
    validate_contract(parser, args)
    try:
        if args.command == "execute" and operations is None:
            Operations.require_p6_5(args)
            from . import ocaml_backend
            return ocaml_backend.public_execute(args)
        if args.command == "run" and operations is None:
            Operations.require_p6_5(args)
            from . import ocaml_backend
            return ocaml_backend.public_run(args)
        if args.command == "postcheck" and operations is None:
            Operations.require_p6_5(args)
            from . import ocaml_backend
            return ocaml_backend.public_postcheck(args)
        if args.command == "preflight" and operations is None:
            Operations.require_p6_5(args)
            from . import ocaml_backend
            return ocaml_backend.preflight(args)
        if args.command == "collect" and operations is None:
            from . import ocaml_backend
            return ocaml_backend.public_collect(args)
        if args.command == "report" and operations is None:
            from . import ocaml_backend
            return ocaml_backend.public_report(args)
        if args.command == "archive" and operations is None:
            from . import ocaml_backend
            return ocaml_backend.public_archive(args)
        operations = operations or Operations()
        if args.command == "execute":
            return execute_sequence(args, operations)
        result = getattr(operations, args.command)(args)
        if args.command in ("run", "postcheck"):
            if args.json:
                print(json.dumps(result.as_dict(), sort_keys=True))
                outcome_diagnostics(result, args.command, args.verbose)
            else:
                summarize_outcome(result, args.command, args.verbose)
            return result.exit_code
        return result
    except (ContractError, OSError, ValueError, KeyError, TypeError,
            AttributeError) as exc:
        print(f"hardcaml-asic-flow: {exc}", file=sys.stderr)
        return 2
    except ContractInterrupted as exc:
        print(f"hardcaml-asic-flow: {exc}", file=sys.stderr)
        return 128 + exc.signum


if __name__ == "__main__":
    sys.exit(main())
