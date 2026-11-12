"""Opt-in REAL stock macro qualification using an already installed prefix/exporter.

No builds, installation, provisioning, Signoff, finalization, or design acceptance.
Run only after coordinator allocation. All new evidence and failures are retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

STAGES = ("synthesis", "floorplan", "placement", "cts", "routing", "timing")
STOCK = "RM_IHPSG13_1P_256x16_c2_bm_bist"


def load(path):
    return json.loads(Path(path).read_text())


def record(path):
    path = Path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def save(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def interpreter_path(path):
    """Preserve the invocation path that selects a virtualenv's pyvenv.cfg."""
    path = Path(path).absolute()
    if not path.is_file():
        raise FileNotFoundError(f"Python interpreter does not exist: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prefix", "provider-receipt", "exporter", "source-root", "support-tools", "pdk-root", "python", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--through", choices=("placement", "timing"), default="placement")
    parser.add_argument("--timeout", type=int, default=600, help="per stage transport budget, seconds")
    parser.add_argument("--named-prefix", action="store_true", help="also execute a fresh named prefix to --through")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    for key in ("prefix", "provider_receipt", "exporter", "source_root", "support_tools", "pdk_root"):
        setattr(args, key, getattr(args, key).resolve(strict=True))
    # Resolving a venv's executable symlink selects the bare interpreter and loses
    # its package metadata. Match the public CLI's lexical interpreter handling.
    try:
        args.python = interpreter_path(args.python)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    output = args.output.absolute()
    output.mkdir()  # new only; never adopt or remove an existing experiment
    cli = args.prefix / "bin/hardcaml-asic-flow"
    evidence = {"schema_version": 1, "scope": "real_segmented_macro_qualification_not_design_acceptance",
                "status": "running", "acceptance": "not_evaluated", "calls": [], "checkpoints": [],
                "provider_receipt": record(args.provider_receipt), "provider_receipt_contents": load(args.provider_receipt),
                "exporter": record(args.exporter), "public_cli": record(cli), "prefix": str(args.prefix),
                "source_root": str(args.source_root), "requested_endpoint": args.through,
                "limits": {"workers": 6, "sta_threads": 1, "stage_timeout_seconds": args.timeout}}
    # Exporter linkage must be established by the coordinator's isolated-prefix build;
    # these hashes record what ran, rather than inferring linkage from PATH.
    env = dict(os.environ, PATH=str(args.prefix / "bin") + os.pathsep + os.environ.get("PATH", ""))
    failure = None
    pdn_origins = {}

    def call(label, argv, *, timeout=None, expected_success=True):
        argv = list(map(str, argv))
        index = len(evidence["calls"])
        stem = output / f"{index:02d}-{label}"
        save(stem.with_suffix(".command.json"), {"argv": argv, "cwd": str(output),
             "timeout_seconds": timeout or args.timeout + 120})
        started = time.time()
        with stem.with_suffix(".stdout").open("xb") as stdout, stem.with_suffix(".stderr").open("xb") as stderr:
            process = subprocess.Popen(argv, cwd=output, env=env, stdout=stdout, stderr=stderr)
            try:
                code = process.wait(timeout=timeout or args.timeout + 120)
                status = {"exit_code": code, "timed_out": False}
            except subprocess.TimeoutExpired:
                # Give the provider supervisor time to cancel its owned container
                # and retain transport records, rather than killing it immediately.
                process.terminate()
                forced = False
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.kill()
                    process.wait()
                status = {"exit_code": 124, "timed_out": True, "forced_kill": forced}
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        status.update(label=label, started_at_unix=started, ended_at_unix=time.time(),
                      command=record(stem.with_suffix(".command.json")),
                      stdout=record(stem.with_suffix(".stdout")), stderr=record(stem.with_suffix(".stderr")))
        evidence["calls"].append(status)
        save(stem.with_suffix(".result.json"), status)
        text = stem.with_suffix(".stdout").read_text()
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        if expected_success and status["exit_code"]:
            raise RuntimeError(f"{label}: exit {status['exit_code']}; retained logs at {stem}")
        return value

    def stages(workspace, endpoint, checkpoint=None, methodology=None):
        argv = [cli, "stages", bundle, "--json", "--no-observation", "--support-tools", args.support_tools,
                "--pdk-root", args.pdk_root, "--python", args.python, "--workspace", workspace,
                "--checkpoint-store", workspace / "checkpoints", "--timeout", args.timeout]
        if methodology:
            argv += ["--methodology-plan", methodology, "--expected-plan-identity", export["plan_identity"]]
        else:
            argv += ["--endpoint", endpoint]
        if checkpoint:
            argv += ["--checkpoint", checkpoint]
        # Host timeout allows the bounded individual segments plus publication/probes.
        return call("stages-" + endpoint + ("-named" if methodology else ""), argv,
                    timeout=(args.timeout + 120) * (STAGES.index(endpoint) + 1))

    def check(artifact):
        path = Path(artifact["path"])
        inspected = call("inspect-" + artifact["boundary"], [cli, "checkpoint-inspect", path, "--json"])
        manifest = inspected["manifest"]
        assert inspected["validation"] == "host_only" and not inspected["runtime_closure_validated"]
        assert manifest["schema_version"] == 3 and manifest["identity"] == artifact["identity"]
        assert manifest["macro_profile"] == "stock256x16_r0_through_timing_v1"
        if artifact["boundary"] == "timing":
            assert manifest["restart_targets"] == [] and not manifest["restart_supported"]
        assert manifest["acceptance"] == "not_evaluated" and manifest["closure_scope"] == "path_bound"
        state = load(path / "state.json")
        segment = load(path / "segment.json")
        assert not any("LVS" in s or "StreamOut" in s or "Manufacturability" in s for s in segment["returned_steps"])
        closure = manifest["external"]
        for entry in contract["macros"][0]["collateral"]:
            ref = entry["reference"]
            if ref.startswith("pdk_dir::"):
                dependency = args.pdk_root / "ihp-sg13cmos5l" / ref.removeprefix("pdk_dir::")
                assert closure[str(dependency)]["sha256"] == record(dependency)["sha256"], ref
        for name in ("physical/pdn.tcl", "physical/macro-plan.json", "bridge/physical_runtime.py",
                     f"macros/{STOCK}.blackbox.v"):
            dependency = Path(manifest["compatibility"]["project"]) / name
            assert closure[str(dependency)]["sha256"] == record(dependency)["sha256"], name
        item = {"artifact": artifact, "manifest": record(path / "manifest.json"),
                "state": record(path / "state.json"), "segment": segment,
                "external_count": len(closure)}
        evidence["checkpoints"].append(item)
        if artifact["boundary"] in STAGES[2:]:
            entries = manifest["macro_evidence"]
            assert len(entries) == 1 and entries[0]["producer_boundary"] == "placement"
            entry = entries[0]
            assert Path(entry["path"]).is_relative_to(path / "views")
            assert record(entry["path"])["sha256"] == entry["sha256"]
            project = manifest["compatibility"]["project"]
            origin = (entry["producer_tag"], entry["sha256"])
            if artifact["boundary"] == "placement":
                pdn_origins[project] = origin
            else:
                assert pdn_origins[project] == origin, "transitive PDN producer or bytes changed"
            item["macro_evidence"] = entries
            odb_probe(artifact, manifest, state, item)
        return state, segment

    def odb_probe(artifact, manifest, state, item):
        binding = manifest["compatibility"]["binding"]
        assert binding["execution_mode"] == "dockerized"
        name = "hc-sram-qualification-" + uuid.uuid4().hex
        destination = output / (artifact["boundary"] + "-" + artifact["identity"][:12] + "-odb.json")
        engine = binding["engine_identity"]["executable"]
        argv = [engine, "run", "--rm", "--name", name, "--cpus=6", "--network=none",
                "-v", f"{output}:{output}", "-e", "OMP_NUM_THREADS=6", "-e", "OPENROAD_THREADS=6",
                "-e", "HC_SRAM_ODB_INPUT=" + state["odb"], "-e", "HC_SRAM_ODB_OUTPUT=" + str(destination)]
        if not binding["engine_identity"]["rootless"]:
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        argv += ["--entrypoint", "openroad", binding["image_identity"]["image_id"], "-exit", str(output / "sram_odb_probe.tcl")]
        try:
            call("odb-" + artifact["boundary"], argv, timeout=120)
            item["odb_observation"] = record(destination)
            item["odb_observation_contents"] = load(destination)
        finally:
            call("cleanup-odb", [engine, "rm", "-f", name], timeout=30, expected_success=False)

    try:
        shutil.copyfile(Path(__file__).with_name("sram_odb_probe.tcl"), output / "sram_odb_probe.tcl")
        bundle = output / "bundle"
        export = call("export", [args.exporter, "export", args.source_root, bundle, args.through])
        assert export and export["preparation"] and export["plan"]["bundle_identity"]
        save(output / "methodology-plan.json", export["plan"])
        save(output / "resource-preparation.json", export["preparation"])
        manifest = load(bundle / "manifest.json")
        contract = manifest["macro_contract"]
        assert manifest["identity"] == export["plan"]["bundle_identity"]
        assert contract["schema_version"] == 2 and contract["execution"] == "fresh_full"
        assert len(contract["macros"]) == 1 and contract["macros"][0]["module"] == STOCK
        physical = contract["physical_plan"]["macros"][0]
        assert physical["location_um"] == [40., 81.] and physical["orientation"] == "R0"
        evidence["bundle"] = record(bundle / "manifest.json")
        workspace = output / "restart-workspace"
        summary = stages(workspace, "synthesis")
        assert [a["boundary"] for a in summary["artifacts"]] == ["synthesis"]
        source = summary["artifacts"][0]
        _, segment = check(source)
        old = Path(segment["run_dir"])
        retained = old.with_name("retained-" + old.name)
        old.rename(retained)
        evidence["isolation"] = {"original_run": str(old), "retained_run": str(retained)}
        summary = stages(workspace, "placement", source["path"])
        assert [a["boundary"] for a in summary["artifacts"]] == ["floorplan", "placement"]
        assert not old.exists()
        for artifact in summary["artifacts"]:
            check(artifact)
        geometry = list((workspace / "project/src/runs").glob("**/macro-power-geometry.json"))
        assert geometry, "real placement emitted no macro power geometry evidence"
        evidence["pdn_geometry"] = [record(p) for p in geometry]
        for p in geometry:
            observed = load(p)["macros"]
            assert len(observed) == 1 and observed[0]["instance"] == "hc_resource_7_program"
            assert {c["pin"]: c["net"] for c in observed[0]["contacts"]} == {
                "VDD!": "VPWR", "VDDARRAY!": "VPWR", "VSS!": "VGND"}
        if args.through == "timing":
            summary = stages(workspace, "timing", summary["artifacts"][-1]["path"])
            assert [a["boundary"] for a in summary["artifacts"]] == ["cts", "routing", "timing"]
            for artifact in summary["artifacts"]:
                check(artifact)
        if args.named_prefix:
            summary = stages(output / "named-workspace", args.through, methodology=output / "methodology-plan.json")
            assert [a["boundary"] for a in summary["artifacts"]] == list(STAGES[:STAGES.index(args.through) + 1])
            for artifact in summary["artifacts"]:
                check(artifact)
        evidence["status"] = "completed"
    except BaseException as exc:
        failure = exc
        evidence["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        evidence["error"] = repr(exc)
    finally:
        # Retain an inventory even when a later tool/design stage fails. These are
        # file observations, not claims that the external closure is currently ready.
        retained = []
        for path in sorted(output.glob("*-workspace/checkpoints/*/manifest.json")):
            item = {"manifest": record(path)}
            try:
                manifest = load(path)
                item.update(boundary=manifest["boundary"], identity=manifest["identity"],
                            inventory_intact=all(record(path.parent / name)["sha256"] == expected["sha256"]
                                                 for name, expected in manifest["files"].items()))
            except (OSError, ValueError, KeyError) as exc:
                item["error"] = repr(exc)
            retained.append(item)
        evidence["retained_checkpoints"] = retained
        save(output / "qualification.json", evidence)
    print(output / "qualification.json")
    if failure:
        print(str(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
