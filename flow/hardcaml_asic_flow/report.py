"""Human-readable summary of one TT/LibreLane run directory.

Replaces the ad hoc "cat | grep" and "python3 -c" one-liners around a finished
run: it reads the same results.json that coarse_flow_oracle.py collect writes (computing it
in memory when it is missing), adds the per-corner timing table, the TT precheck
rows, and the resizer's own account of what it repaired, then exits nonzero when
anything is not a pass.

An unconstrained timing mode counts as a failure on purpose. A "-max" only SDC
leaves hold with no paths at all, which every individual check reports as fine.

The public `hardcaml-asic-flow report` route now delegates to native
Report_service. This retained implementation is the differential oracle and the
transitional Python finalization API; `scripts/report.py` still adapts this
development command line. Its `--runs` newest-run lookup remains a documented
compatibility seam until top-level CLI migration: the installed contract requires an explicit
run directory everywhere, because guessing which run someone meant is how
evidence gets attached to the wrong attempt.

Usage:
  hardcaml-asic-flow report RUN_DIR
  hardcaml-asic-flow report RUN_DIR --json   # the collected record, unformatted
  scripts/report.py --runs RUNS_DIR          # development: newest run under RUNS_DIR
"""

import argparse
import csv
import json
import math
from pathlib import Path
import re
import sys

from . import coarse_flow_oracle


UNCONSTRAINED_THRESHOLD = 1e30


def newest_run(runs):
    candidates = [path for path in runs.iterdir() if (path / "run.json").is_file()]
    if not candidates:
        raise SystemExit(f"report: no run directory under {runs}")
    # st_mtime_ns avoids the float rounding st_mtime has at current epoch
    # values; the name breaks the tie when two runs share a timestamp, so the
    # choice never depends on directory order.
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def results_for(run_dir):
    """The collected record; the stored one when present, else collected now."""
    stored = run_dir / "results.json"
    if stored.is_file():
        return json.loads(stored.read_text()), str(stored.relative_to(run_dir))
    return coarse_flow_oracle.collect(run_dir), "collected in memory"


def flow_dir(run_dir, record):
    return run_dir / record.get("outputs", {}).get("flow", "project/runs/asic")


def corner_timing(metrics_csv):
    """Per-corner setup/hold worst slack and violation counts from metrics.csv.

    Hold slack is reported as 1e39 when STA found no hold path at all; that is
    kept as None here rather than printed as a number.
    """
    if not metrics_csv.is_file():
        return {}
    corners = {}
    pattern = re.compile(r"^timing__(setup|hold)__(ws|tns)__corner:(.+)$")
    counts = re.compile(r"^timing__(setup|hold)_vio__count__corner:(.+)$")
    with metrics_csv.open(newline="") as stream:
        for row in csv.reader(stream):
            if len(row) != 2:
                continue
            key, raw = row
            match = pattern.match(key) or counts.match(key)
            if not match:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if pattern.match(key):
                mode, field, corner = match.groups()
            else:
                mode, corner = match.groups()
                field = "vio"
            if field == "ws" and abs(value) >= UNCONSTRAINED_THRESHOLD:
                value = None
            corners.setdefault(corner, {})[f"{mode}_{field}"] = value
    return corners


def precheck_rows(run_dir):
    """(check, result) pairs from every TT precheck results.md under the run."""
    rows = []
    for report in sorted(run_dir.glob("checks/*/tt/precheck/reports/results.md")):
        for line in report.read_text().splitlines():
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if len(cells) == 2 and cells[0] not in ("Check", "") and "---" not in cells[1]:
                rows.append((cells[0], cells[1]))
    return rows


def hold_repair(run_dir, record):
    """What the post-CTS resizer said about hold; its log is the only account.

    "No hold violations found" means nothing was repaired, which is the right
    outcome only when the SDC actually constrained hold. See docs/reference/target-reference.md.
    """
    lines = []
    for log in sorted(flow_dir(run_dir, record).glob("*resizer*/*.log")):
        for line in log.read_text().splitlines():
            if re.search(r"RSZ-00(33|98)|repair_timing .*-hold|Hold violation", line):
                lines.append((log.parent.name, line.strip()))
    return lines


def number(value, digits=3):
    return (f"{value:.{digits}f}" if isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value) else "n/a")


def numeric(value, *, count=False, nonnegative=False):
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value)):
        return False
    if nonnegative and value < 0:
        return False
    return not count or float(value).is_integer()


def report(run_dir, results, source, output=print, *, context=None):
    problems = []
    out = output

    record = json.loads((run_dir / "run.json").read_text()) if context is None else context
    out(f"run        {results.get('run_id')}  ({run_dir})")
    out(f"bundle     {record.get('bundle')}")
    out(f"status     {results.get('process_status')}"
        f"   stage {results.get('completed_stage')} of {results.get('requested_stage')}"
        f"   source {source}")
    for field in ("run_id", "build_identity"):
        if record.get(field) is None or results.get(field) is None:
            problems.append(f"missing {field} in run or results record")
        elif record[field] != results[field]:
            problems.append(f"contradictory {field}: run={record[field]!r}, results={results[field]!r}")
    for run_field, result_field in (("status", "process_status"),
                                    ("requested_stage", "requested_stage"),
                                    ("completed_stage", "completed_stage")):
        if record.get(run_field) != results.get(result_field):
            problems.append(f"contradictory {result_field}: run={record.get(run_field)!r}, "
                            f"results={results.get(result_field)!r}")

    requested = record.get("requested_stage")
    completed = record.get("completed_stage")
    result_requested = results.get("requested_stage")
    result_completed = results.get("completed_stage")
    valid_stages = {"synthesis", "full"}
    if requested not in valid_stages or result_requested not in valid_stages:
        problems.append(f"unknown requested stage: run={requested!r}, results={result_requested!r}")
        scope = None
    elif requested != result_requested:
        scope = None
    else:
        scope = requested
    if completed not in valid_stages | {None} or result_completed not in valid_stages | {None}:
        problems.append(f"unknown completed stage: run={completed!r}, results={result_completed!r}")
    if record.get("status") != "completed" or results.get("process_status") != "completed":
        problems.append(f"process did not complete: run={record.get('status')!r}, "
                        f"results={results.get('process_status')!r}")
    if scope is not None and (completed != scope or result_completed != scope):
        problems.append(f"requested {scope} but completed stage is "
                        f"run={completed!r}, results={result_completed!r}")

    out(f"scope      {scope + '-only acceptance' if scope == 'synthesis' else 'full-flow acceptance' if scope == 'full' else 'invalid/unknown'}")

    errors = results.get("errors")
    if not isinstance(errors, list):
        problems.append("collection errors field missing or malformed")
    else:
        for error in errors:
            problems.append(f"collection error: {error}")

    metrics = results.get("metrics")
    out("\nSYNTHESIS EVIDENCE")
    if not isinstance(metrics, dict):
        problems.append("metrics missing or malformed")
        metrics = {}
    for name, count in (("mapped_area", False), ("mapped_cells", True)):
        item = metrics.get(name)
        value = item.get("value") if isinstance(item, dict) else None
        reason = item.get("unavailable_reason") if isinstance(item, dict) else None
        out(f"  {name:<22} {number(value, 0 if count else 3)}"
            f"{'   ' + str(reason) if reason is not None else ''}")
        if reason is not None:
            problems.append(f"{name} unavailable: {reason}")
        elif not numeric(value, count=count, nonnegative=True):
            problems.append(f"{name} missing or malformed")

    synthesis_checks = results.get("synthesis_checks")
    if not isinstance(synthesis_checks, dict):
        problems.append("synthesis_checks missing or malformed")
        synthesis_checks = {}
    for name in ("unmapped_instances", "synthesis_errors", "inferred_latches"):
        item = synthesis_checks.get(name)
        value = item.get("value") if isinstance(item, dict) else None
        reason = item.get("unavailable_reason") if isinstance(item, dict) else None
        out(f"  {name:<22} {number(value, 0)}"
            f"{'   ' + str(reason) if reason is not None else ''}")
        if reason is not None:
            problems.append(f"{name} unavailable: {reason}")
        elif not numeric(value, count=True, nonnegative=True):
            problems.append(f"{name} missing or malformed")
        elif value != 0:
            problems.append(f"{name} = {number(value, 0)}")

    if scope == "full":
        out("\nTIMING")
        goal = results.get("timing_goal")
        unconstrained = results.get("timing_unconstrained_modes")
        out(f"  goal {goal}")
        if goal != "pass":
            problems.append(f"timing goal {goal}")
        if not isinstance(unconstrained, list) or any(
                not isinstance(mode, str) for mode in unconstrained):
            problems.append("timing_unconstrained_modes missing or malformed")
        elif unconstrained:
            out(f"  UNCONSTRAINED: {', '.join(unconstrained)}"
                "  (no constrained paths reported; the SDC does not check this mode)")
            problems.append(f"unconstrained timing modes: {', '.join(unconstrained)}")
        for name in ("setup_slack", "hold_slack"):
            item = metrics.get(name)
            value = item.get("value") if isinstance(item, dict) else None
            corner = item.get("corner") if isinstance(item, dict) else None
            reason = item.get("unavailable_reason") if isinstance(item, dict) else None
            out(f"  {name:<12} {number(value):>10} ns   corner {corner or '-'}"
                f"{'   ' + str(reason) if reason else ''}")
            if reason is not None:
                problems.append(f"{name} unavailable: {reason}")
            elif (not numeric(value, nonnegative=True)
                  or abs(value) >= UNCONSTRAINED_THRESHOLD
                  or not isinstance(corner, str) or not corner):
                problems.append(f"{name} missing or malformed")
        corners = corner_timing(flow_dir(run_dir, record) / "final/metrics.csv")
        if corners:
            out(f"  {'corner':<22}{'setup ws':>10}{'setup vio':>11}"
                f"{'hold ws':>10}{'hold vio':>10}")
            for corner, values in sorted(corners.items()):
                out(f"  {corner:<22}{number(values.get('setup_ws')):>10}"
                    f"{number(values.get('setup_vio'), 0):>11}"
                    f"{number(values.get('hold_ws')):>10}"
                    f"{number(values.get('hold_vio'), 0):>10}")

        out("\nSIGNOFF CHECKS")
        checks = results.get("checks")
        if not isinstance(checks, dict):
            problems.append("physical checks missing or malformed")
            checks = {}
        for name in ("drc", "lvs", "antenna"):
            item = checks.get(name)
            status = item.get("status") if isinstance(item, dict) else None
            note = item.get("reason") if isinstance(item, dict) else None
            out(f"  {name:<10} {status or 'missing'}{('  ' + str(note)) if note else ''}")
            if status != "pass":
                problems.append(f"{name} {status or 'missing'}")

        postchecks = results.get("postchecks")
        out("\nPOSTCHECKS")
        if not isinstance(postchecks, list) or not postchecks:
            problems.append("required postchecks missing or malformed")
        else:
            for index, entry in enumerate(postchecks):
                if not isinstance(entry, dict):
                    problems.append(f"postcheck {index + 1} malformed")
                    continue
                if entry.get("status") != "completed":
                    problems.append(f"postcheck {index + 1} status {entry.get('status')}")
                entry_checks = entry.get("checks")
                if not isinstance(entry_checks, dict):
                    entry_checks = {}
                    problems.append(f"postcheck {index + 1} checks missing or malformed")
                for name in ("precheck", "gate_level"):
                    status = entry_checks.get(name)
                    out(f"  {name:<12} {status or 'missing'}")
                    if status != "pass":
                        problems.append(f"{name} {status or 'missing'}")
        for check, result in precheck_rows(run_dir):
            out(f"    {check:<44} {result}")

        repair = hold_repair(run_dir, record)
        out("\nHOLD REPAIR (post-CTS resizer)" if repair
            else "\nHOLD REPAIR  no resizer log found")
        for step, line in repair:
            out(f"  [{step}] {line}")
    else:
        reason = ("not evaluated / not required for requested synthesis operation"
                  if scope == "synthesis" else "not evaluated: acceptance scope is invalid")
        out(f"\nTIMING  {reason}")
        out(f"\nSIGNOFF CHECKS  {reason}")
        out(f"\nPOSTCHECKS  {reason}")
        out(f"\nHOLD REPAIR  {reason}")

    sdc = run_dir / "project/constraints/top.sdc"
    if sdc.is_file():
        out(f"\nCONSTRAINTS ({sdc.relative_to(run_dir)})")
        for line in sdc.read_text().splitlines():
            out(f"  {line}")

    out("")
    if problems:
        out("PROBLEMS")
        for problem in problems:
            out(f"  - {problem}")
        return 1
    if scope == "synthesis":
        out("synthesis acceptance passed; physical timing and signoff were not evaluated")
    else:
        out("full-flow acceptance passed")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", nargs="?", type=Path)
    parser.add_argument("--runs", type=Path, help="Report on the newest run under this directory")
    parser.add_argument("--json", action="store_true", help="Print the collected record instead")
    args = parser.parse_args()
    if bool(args.run_dir) == bool(args.runs):
        parser.error("give exactly one of RUN_DIR or --runs")
    run_dir = (args.run_dir or newest_run(args.runs.resolve())).resolve()
    if not (run_dir / "run.json").is_file():
        raise SystemExit(f"report: not a run directory (no run.json): {run_dir}")
    results, source = results_for(run_dir)
    if args.json:
        status = report(run_dir, results, source,
                        output=lambda *values: print(*values, file=sys.stderr))
        print(json.dumps(results, indent=2, sort_keys=True))
        return status
    return report(run_dir, results, source)


def run():
    """`main` plus the command line's top-level diagnostic mapping.

    A named function rather than only the `__main__` guard below, so the
    repository adapter and `python -m hardcaml_asic_flow.report` get identical
    behaviour instead of one of them losing the unreadable-input mapping.
    """
    try:
        return main()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())
