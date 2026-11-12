"""Independent read-only physical recheck from completed controls, never run relabeling.

Host mode uses the original immutable image; inside mode only parses/revalidates
retained evidence and GDS polygons. No Magic, routing, KLayout executable or LVS
process is launched. The existing public provider acceptance is not bypassed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
RUNTIME = ROOT / "flow/hardcaml_asic_flow/physical_runtime.py"


def runtime_module():
    spec = importlib.util.spec_from_file_location("physical_runtime", RUNTIME)
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    return rt


def original_inputs(diagnostic, rt):
    plan = rt.load(diagnostic / "plan.json")
    execution = rt.load(diagnostic / "execution.json")
    rt.require(execution["status"] == "completed" and execution["exit_code"] == 0,
               "completed diagnostic execution required")
    # Runtime source is intentionally revised after the experiment. Every other
    # frozen input, including original provider helper, models and run receipts,
    # must still match. Preserve both checker versions in the new receipt.
    hashes = {p: h for p, h in plan["input_sha256"].items() if Path(p) != RUNTIME}
    rt.require(all(rt.sha(p) == h for p, h in hashes.items()), "original diagnostic input changed")
    run = Path(plan["run"])
    record = rt.load(run / "run.json")
    rt.require(record["status"] == plan["original_status"] == "failed" and record["run_id"] == plan["run_id"] and
               record["build_identity"] == plan["build_identity"], "original run identity/status changed")
    for ref in record["environment"]["references"]:
        rt.require(rt.sha(ref["path"]) == ref["sha256"], "run-bound source reference changed")
    # Original closeout retains independent raw reports and exact assembled GDS.
    for entry in rt.load(run.parent.parent.parent / "closeout.json")["artifact_inventory"]:
        rt.require(rt.sha(entry["path"]) == entry["sha256"], "original closeout artifact changed")
    return plan, record, hashes


def inside(diagnostic, out, rt):
    plan, record, frozen = original_inputs(diagnostic, rt)
    project = Path(plan["run"]) / "project"
    flow = project / "runs/asic"
    before = {str(p): rt.sha(p) for p in flow.rglob("*") if p.is_file()}
    receipt = {"schema_version": 1, "record_kind": "independent_physical_recheck", "run_id": plan["run_id"],
               "build_identity": plan["build_identity"], "original_run_status": "failed", "provider_acceptance": False,
               "original_provider_checker_sha256": rt.sha(project / "physical-runtime.py"),
               "diagnostic_checker_sha256": plan["input_sha256"][str(RUNTIME)], "recheck_checker_sha256": rt.sha(RUNTIME),
               "diagnostic_plan_sha256": rt.sha(diagnostic / "plan.json"),
               "diagnostic_execution_sha256": rt.sha(diagnostic / "execution.json"),
               "original_input_sha256": frozen, "physical_checks": "pending", "full_qualification": "not_accepted"}
    try:
        evidence = rt.check(project, Path(record["environment"]["pdk_root"]),
                            evidence_output=out, magic_control_dir=diagnostic / "controls")
        receipt.update(physical_checks=evidence["status"], physical_checks_sha256=rt.sha(out / "physical-checks.json"))
        metrics = rt.load(flow / "final/metrics.json")
        corners = rt.load(project / "manifest.json")["flow_configuration"]["STA_CORNERS"]
        receipt["timing"] = {corner: {key: metrics[key + "__corner:" + corner] for key in (
            "timing__setup__ws", "timing__hold__ws", "timing__setup_vio__count", "timing__hold_vio__count")} for corner in corners}
        receipt["timing_qualification"] = "fail" if any(
            c["timing__setup__ws"] < 0 or c["timing__hold__ws"] < 0 or
            c["timing__setup_vio__count"] or c["timing__hold_vio__count"] for c in receipt["timing"].values()) else "pass"
    except Exception as exc:
        receipt.update(physical_checks="fail", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        receipt["original_flow_unchanged"] = before == {str(p): rt.sha(p) for p in flow.rglob("*") if p.is_file()}
        receipt["original_inputs_unchanged"] = all(rt.sha(p) == h for p, h in frozen.items())
        rt.write(out / "reevaluation.json", receipt)
        rt.require(receipt["original_flow_unchanged"] and receipt["original_inputs_unchanged"], "original evidence mutated")


def summarize(diagnostic, out, rt):
    recheck = rt.load(out / "reevaluation.json")
    evidence = rt.load(out / "physical-checks.json")
    rt.require(rt.sha(out / "physical-checks.json") == recheck["physical_checks_sha256"], "recheck evidence changed")
    controls = rt.load(diagnostic / "result.json")
    execution = rt.load(diagnostic / "execution.json")
    samples = [json.loads(line) for line in (diagnostic / "monitor.jsonl").read_text().splitlines()]
    rt.write(out / "qualification.json", {
        "schema_version": 1, "physical_profile_recheck": recheck["physical_checks"],
        "timing_qualification": recheck["timing_qualification"], "full_qualification": recheck["full_qualification"],
        "provider_acceptance": False, "original_run_status": recheck["original_run_status"],
        "run_id": recheck["run_id"], "build_identity": recheck["build_identity"],
        "original_flow_unchanged": recheck["original_flow_unchanged"],
        "original_inputs_unchanged": recheck["original_inputs_unchanged"],
        "exact_layer_control_relationships": controls["exact_layer_control_relationships"],
        "control_counts": {label: case["raw_count"] for label, case in controls["controls"].items()},
        "source_lef_sha256": controls["source_lef_sha256"], "routed_def_sha256": controls["routed_def_sha256"],
        "gds_sha256": evidence["gds_sha256"], "raw_magic_report_sha256": evidence["magic_disposition"]["raw_report_sha256"],
        "power_geometry_sha256": evidence["power_geometry_sha256"], "schematic_cdl_sha256": evidence["schematic_cdl_sha256"],
        "recheck_checker_sha256": recheck["recheck_checker_sha256"],
        "reevaluation_sha256": rt.sha(out / "reevaluation.json"),
        "physical_checks_sha256": rt.sha(out / "physical-checks.json"),
        "diagnostic_execution_sha256": rt.sha(diagnostic / "execution.json"),
        "resources": {"diagnostic_elapsed_seconds": execution["ended_unix"] - execution["started_unix"],
                      "monitor_samples": len(samples),
                      "minimum_host_available_memory_kib": min(int(s["mem_available"].split()[0]) for s in samples),
                      "minimum_disk_free_bytes": min(s["disk_free_bytes"] for s in samples)},
        "contact_attribution": evidence["magic_disposition"]["contact_attribution"], "timing": recheck["timing"],
        "timeout_scope": {"experiment_per_case_seconds": 480, "experiment_supervisor_seconds": 1980,
                          "production_control_per_case_seconds": 300, "standalone_baseline_seconds": 180,
                          "production_timeout_policy_changed": False}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--summary", action="store_true", help="summarize completed receipts without executing tools")
    args = parser.parse_args()
    diagnostic, out = args.diagnostic.resolve(), args.output.resolve()
    rt = runtime_module()
    if args.summary:
        summarize(diagnostic, out, rt)
        return
    if args.inside:
        inside(diagnostic, out, rt)
        return
    plan, record, _ = original_inputs(diagnostic, rt)
    rt.require(not out.exists() and out.is_relative_to(WORKSPACE) and not out.is_relative_to(Path(plan["run"])),
               "fresh separate output required")
    out.mkdir()
    image = record["environment"]["execution_binding"]["image_identity"]["image_id"]
    name = "asic-physical-recheck-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network", "none", "--cpus=6",
               "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{WORKSPACE}:{WORKSPACE}:ro", "-v", f"{out}:{out}:rw",
               "-w", str(out), "-e", "OMP_NUM_THREADS=1", "--entrypoint", "python3", image, "-I", "-B",
               str(Path(__file__).resolve()), "--inside", "--diagnostic", str(diagnostic), "--output", str(out)]
    rt.write(out / "command.json", {"command": command, "timeout_seconds": 300,
             "checker_sha256": rt.sha(RUNTIME), "recheck_script_sha256": rt.sha(Path(__file__)), "provider_acceptance": False})
    try:
        with (out / "recheck.log").open("w") as log:
            return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300).returncode
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
