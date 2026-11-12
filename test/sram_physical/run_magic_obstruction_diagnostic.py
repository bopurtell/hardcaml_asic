"""Prepare by default; explicit execute runs four serial diagnostic Magic controls.

Original workspace is mounted read-only; only a fresh diagnostic directory is
writable. No routing, installation, KLayout, postcheck or acceptance relabeling.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
RUNTIME = ROOT / "flow/hardcaml_asic_flow/physical_runtime.py"


def runtime_module():
    spec = importlib.util.spec_from_file_location("physical_runtime", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(run, rt):
    record = rt.load(run / "run.json")
    rt.require(record["status"] == "failed", "expected preserved failed run")
    project = run / "project"
    manifest, plan = rt.validate_plan(project)
    flow = project / "runs/asic"
    steps = list(flow.glob("*-magic-drc"))
    rt.require(len(steps) == 1, "unique Magic step required")
    step = steps[0]
    config = rt.load(flow / "resolved.json")
    names = {m["module"] for m in plan["macros"]}
    rt.require(len(names) == 1, "single registered macro model required")
    refs = record["environment"]["references"]
    paths = [run / "run.json", run / "results.json", project / "manifest.json",
             project / "physical-runtime.py", flow / "resolved.json", flow / "final/metrics.json",
             step / "_env.tcl", step / "state_in.json", step / "reports/drc.magic.rpt",
             Path(rt.load(step / "state_in.json")["def"]), Path(config["MAGICRC"]),
             project / plan["plan_file"], project / plan["pdn_tcl"], RUNTIME, Path(__file__).resolve()]
    for ref in refs:
        path = Path(ref["path"])
        rt.require(rt.sha(path) == ref["sha256"], "run-bound collateral changed: " + str(path))
        paths.append(path)
    for name in names:
        paths.extend(Path(p) for p in config["MACROS"][name]["lef"])
    paths.extend(Path(p) for p in config["TECH_LEFS"].values())
    paths.extend(Path(p) for p in config["CELL_LEFS"])
    paths.extend(flow.glob("*-openroad-generatepdn/macro-power-geometry.json"))
    paths.extend((flow / "final/gds").glob("*.gds"))
    paths.extend((flow / "final/nl").glob("*.v"))
    # Bind the retained outcome and original receipt inventory, not just the flow.
    paths.extend([run / "preflight.json", run.parent.parent / "outcome.json",
                  run.parent.parent.parent / "closeout.json"])
    rt.require(all(p.resolve().is_relative_to(WORKSPACE) for p in paths), "input outside read-only workspace")
    return record, plan, {str(p): rt.sha(p) for p in paths}


def verify(hashes, rt):
    rt.require(all(rt.sha(p) == digest for p, digest in hashes.items()), "preserved diagnostic input changed")


def prepare(run, out, rt, *, case_timeout=300):
    rt.require(case_timeout in (300, 480), "unsupported per-case diagnostic budget")
    rt.require(not out.exists() and not out.is_relative_to(run), "fresh separate diagnostic output required")
    rt.require(out.is_relative_to(WORKSPACE), "output must be inside workspace")
    record, _, hashes = inputs(run, rt)
    image = record["environment"]["execution_binding"]["image_identity"]["image_id"]
    rt.require(image.startswith("sha256:") and len(image) == 71, "immutable recorded image required")
    out.mkdir()
    name = "asic-magic-diagnostic-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network", "none",
               "--cpus=6", "--user", f"{os.getuid()}:{os.getgid()}",
               "-v", f"{WORKSPACE}:{WORKSPACE}:ro", "-v", f"{out}:{out}:rw", "-w", str(out),
               "-e", "OMP_NUM_THREADS=1", "-e", "OPENROAD_THREADS=1", "-e", "_OPENLANE_MAX_CORES=6",
               "--entrypoint", "python3", image, "-I", "-B", str(Path(__file__).resolve()),
               "inside", "--output", str(out)]
    rt.write(out / "plan.json", {"schema_version": 1, "run": str(run), "original_status": record["status"],
             "run_id": record["run_id"], "build_identity": record["build_identity"], "input_sha256": hashes,
             "command": command, "container_name": name, "timeout_seconds": 4 * case_timeout + 60,
             "budget": {"exclusive_eda_lane_cpus": 6, "parallel_magic_jobs": 1, "threads_per_job": 1,
                        "cases": 4, "per_case_timeout_seconds": case_timeout}, "physical_acceptance": False})
    print(shlex.join(["python3", "-B", str(Path(__file__).resolve()), "execute", "--output", str(out)]))


def inside(out, rt):
    receipt = rt.load(out / "plan.json")
    verify(receipt["input_sha256"], rt)
    run = Path(receipt["run"])
    _, plan, _ = inputs(run, rt)
    flow = run / "project/runs/asic"
    report = next(flow.glob("*-magic-drc/reports/drc.magic.rpt"))
    findings = rt.magic_findings(report)
    # This experiment always requires the full two-layer factorial controls.
    rt.require({rt.obstruction_layer(rule) for rule, _ in findings} == {"Metal2", "Metal3"},
               "expected both M2.f and M3.f findings")
    try:
        evidence = rt.wide_obstruction_counterfactual(flow, plan, findings, out / "controls", evidence_only=True,
                    tool_timeout_seconds=receipt["budget"]["per_case_timeout_seconds"])
        evidence.update(original_run_status=receipt["original_status"], run_id=receipt["run_id"],
                        build_identity=receipt["build_identity"], raw_report_sha256=rt.sha(report))
        rt.write(out / "result.json", evidence)
    finally:
        verify(receipt["input_sha256"], rt)


def execute(out, rt):
    receipt = rt.load(out / "plan.json")
    verify(receipt["input_sha256"], rt)
    rt.require(not (out / "execution.json").exists(), "prepared diagnostic already executed")
    start = time.time()
    rt.write(out / "execution.json", {"status": "running", "started_unix": start, "physical_acceptance": False})
    status = "failed"
    code = None
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        with (out / "container.log").open("w") as log, (out / "monitor.jsonl").open("w") as monitor:
            process = subprocess.Popen(receipt["command"], stdout=log, stderr=subprocess.STDOUT)
            while True:
                memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
                monitor.write(json.dumps({"unix": time.time(), "mem_available": memory["MemAvailable"].strip(),
                                         "disk_free_bytes": shutil.disk_usage(out).free,
                                         "loadavg": os.getloadavg()}) + "\n")
                monitor.flush()
                if time.time() - start > receipt["timeout_seconds"]:
                    raise TimeoutError("diagnostic wall budget exhausted")
                try:
                    code = process.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    pass
        status = "completed" if code == 0 else "failed"
    finally:
        # Remove only this diagnostic's owned container, including on interruption.
        cleanup = subprocess.run(["docker", "rm", "-f", receipt["container_name"]], capture_output=True, text=True)
        if "process" in locals():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        signal.signal(signal.SIGTERM, previous)
        rt.write(out / "execution.json", {"status": status, "exit_code": code, "started_unix": start,
                 "ended_unix": time.time(), "cleanup_returncode": cleanup.returncode,
                 "cleanup_stdout": cleanup.stdout, "cleanup_stderr": cleanup.stderr, "physical_acceptance": False})
        verify(receipt["input_sha256"], rt)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "execute", "inside"])
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-timeout", type=int, choices=[300, 480], default=300,
                        help="prepare only; choose only the coordinator-allocated case budget")
    args = parser.parse_args()
    rt = runtime_module()
    out = args.output.resolve()
    if args.mode == "prepare":
        if args.run is None:
            parser.error("prepare requires --run")
        prepare(args.run.resolve(), out, rt, case_timeout=args.case_timeout)
    elif args.mode == "inside":
        inside(out, rt)
    else:
        return execute(out, rt)


if __name__ == "__main__":
    raise SystemExit(main())
