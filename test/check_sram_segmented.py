"""Stock SRAM segmented integration with real host/bridge code and fake EDA APIs.

Requires already-built executables. Never builds, installs, provisions, or runs EDA.
The PDN marker is synthetic state-carryover evidence, not physical qualification.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

STAGES = ("synthesis", "floorplan", "placement", "cts", "routing", "timing")
STOCK = "RM_IHPSG13_1P_256x16_c2_bm_bist"


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=90, **kwargs)


def require_ok(result):
    assert result.returncode == 0, (result.args, result.stdout, result.stderr)
    return json.loads(result.stdout)


def inspect_artifact(inspector, artifact):
    path = Path(artifact["path"])
    result = require_ok(run([inspector, "--json", path]))
    assert result["validation"] == "host_only" and not result["runtime_closure_validated"]
    manifest = result["manifest"]
    assert manifest["schema_version"] == 3 and manifest["identity"] == artifact["identity"]
    assert manifest["macro_profile"] == "stock256x16_r0_through_timing_v1"
    if artifact["boundary"] == "timing":
        assert not manifest["restart_supported"] and manifest["restart_targets"] == []
    elif artifact["boundary"] == "synthesis":
        assert manifest["restart_targets"] == ["floorplan"]
    assert manifest["closure_scope"] == "path_bound" and manifest["acceptance"] == "not_evaluated"
    for name, record in manifest["files"].items():
        child = path / name
        assert not child.is_symlink() and sha(child) == record["sha256"]
        assert child.stat().st_size == record["bytes"]
    def leaves(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [p for v in (value.values() if isinstance(value, dict) else value) for p in leaves(v)]
    state = load(path / "state.json")
    for view in leaves({k: v for k, v in state.items() if k != "metrics"}):
        assert Path(view).is_relative_to(path / "views") and Path(view).is_file(), view
    return manifest, state


def verify(args, root):
    started = time.monotonic()
    def progress(message):
        print(f"SRAM segmented [{time.monotonic() - started:.1f}s]: {message}", flush=True)
    repo = Path(__file__).resolve().parents[1]
    resources = args.resources.resolve()
    fixtures = repo / "test"
    templates = {}
    for mode in ("physical", "synthesis", "linked"):
        progress("emit " + mode)
        bundle = root / ("template-" + mode)
        p = run([args.emitter, "emit-single-" + mode, repo, bundle])
        assert p.returncode == 0, (p.stdout, p.stderr)
        templates[mode] = bundle
    discovery = require_ok(run([args.plan_inspect], input=json.dumps({
        "schema_version": 4, "stages": [*STAGES, "signoff"], "initial_boundary": None})))
    def find_image(value):
        if isinstance(value, dict):
            if "tested_image_id" in value:
                return value["tested_image_id"]
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            return None
        return next((image for child in children if (image := find_image(child))), None)
    image = find_image(discovery)
    assert image
    inventory = [o["backend"] for o in discovery["operation_sequence"]]
    settings = {o["backend"]["implementation"]: o["settings"] for o in discovery["operation_sequence"]}
    serial = 0

    def case(mode="physical"):
        nonlocal serial
        serial += 1
        directory = root / f"case-{serial}"
        directory.mkdir()
        bundle = directory / "bundle"
        shutil.copytree(templates[mode], bundle)
        manifest = load(bundle / "manifest.json")
        (directory / "classic-inventory.json").write_text(json.dumps(inventory))
        (directory / "classic-settings.json").write_text(json.dumps(settings))
        pdk, support = directory / "pdk", directory / "tt"
        pdk.mkdir()
        support.mkdir()
        def put(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic collateral: " + path.name)
            return path
        collateral = []
        for macro in manifest["macro_contract"]["macros"]:
            for entry in macro["collateral"]:
                reference = entry["reference"]
                if reference.startswith("pdk_dir::"):
                    path = pdk / "ihp-sg13cmos5l" / reference.removeprefix("pdk_dir::")
                    if path not in collateral:
                        collateral.append(put(path))
        for entry in manifest["target"]["external_references"]:
            base = support if entry["source"] == "Support_tools" else pdk / "ihp-sg13cmos5l"
            path = put(base / entry["path"])
            if entry["source"] == "Support_tools" and path.suffix == ".def":
                path.write_text("floorplan fixture")
        put(pdk / "ihp-sg13cmos5l/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc")
        put(pdk / "ihp-sg13g2/libs.tech/klayout/tech/drc/transitive.fixture")
        for relative in ("libs.tech/magic/fixture.tcl", "libs.tech/netgen/fixture.tcl",
                         "libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v",
                         "libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v"):
            put(pdk / "ihp-sg13cmos5l" / relative)
        bins = directory / "bin"
        bins.mkdir()
        for name in ("python", "docker", "git"):
            shutil.copyfile(fixtures / "stage_preparation_engine.py", bins / name)
            (bins / name).chmod(0o700)
        shutil.copyfile(fixtures / "segment_service_engine.py", bins / "segment_service_engine.py")
        requested = manifest["requested_tools"]
        env = dict(os.environ, SERVICE_ENGINE_ROOT=str(directory),
            SERVICE_ENGINE_FIXTURE=str(fixtures / "segment_service_fixture.py"),
            PREPARATION_IMAGE=image, BRIDGE_FIXTURE_BUNDLE_CONFIG="1", BRIDGE_FIXTURE_PDN_STATE="1",
            PREPARATION_REVISIONS=json.dumps({str(pdk): requested["pdk_revision"],
                                            str(support): requested["support_tools_revision"]}))
        return dict(directory=directory, bundle=bundle, pdk=pdk, support=support,
                    bins=bins, env=env, collateral=collateral, workspace=directory / "workspace")

    def execute(c, endpoint, checkpoint=None, extra=()):
        argv = [args.cli, "--json", "--no-observation", "--bundle", c["bundle"],
                "--support-tools", c["support"], "--pdk-root", c["pdk"],
                "--python", c["bins"] / "python", "--docker", c["bins"] / "docker",
                "--git", c["bins"] / "git", "--resources", c.get("resources", resources),
                "--workspace", c["workspace"], "--checkpoint-store", c["workspace"] / "checkpoints",
                "--evidence-root", c["directory"] / "evidence", "--timeout", "3", "--probe-timeout", "3",
                "--through", endpoint]
        if checkpoint:
            argv += ["--checkpoint", checkpoint]
        return run([*argv, *extra], env=c["env"])

    def requests(c):
        return sorted((c["workspace"] / "project").glob("*-request.json"))

    def assert_no_launch(c, result, before=()):
        assert result.returncode != 0, result.stdout
        assert requests(c) == list(before), (result.stdout, result.stderr)

    # Each fresh supported prefix is independent; no ready artifact inferred from exit alone.
    for endpoint in STAGES:
        progress("fresh prefix through " + endpoint)
        c = case()
        summary = require_ok(execute(c, endpoint))
        assert summary["plan"]["schema_version"] == 4
        assert [a["boundary"] for a in summary["artifacts"]] == list(STAGES[:STAGES.index(endpoint) + 1])
        previous = None
        execute_requests = [load(p) for p in requests(c) if load(p)["operation"] == "execute_segment"]
        validate_requests = [load(p) for p in requests(c) if load(p)["operation"] == "validate_publication"]
        assert len(execute_requests) == len(validate_requests) == len(summary["artifacts"])
        for artifact in summary["artifacts"]:
            manifest, state = inspect_artifact(args.inspector, artifact)
            segment = load(Path(artifact["path"]) / "segment.json")
            assert segment["source_boundary"] == previous
            assert not any("LVS" in s or "StreamOut" in s or "Manufacturability" in s for s in segment["returned_steps"])
            closure = manifest["external"]
            for path in c["collateral"]:
                assert closure[str(path)]["sha256"] == sha(path), path
            project = c["workspace"] / "project"
            for name in ("physical/macro-plan.json", "physical/pdn.tcl", f"macros/{STOCK}.blackbox.v",
                         "bridge/physical_runtime.py"):
                assert closure[str(project / name)]["sha256"] == sha(project / name), name
            if artifact["boundary"] in STAGES[2:]:
                assert load(state["odb"])["fixture_pdn"]["instance"] == "hc_resource_7_program"
                entries = manifest["macro_evidence"]
                assert len(entries) == 1 and entries[0]["producer_boundary"] == "placement"
                entry = entries[0]
                assert Path(entry["path"]).is_relative_to(Path(artifact["path"]) / "views")
                assert entry["sha256"] == sha(entry["path"])
                if artifact["boundary"] == "placement":
                    producer = entry["producer_tag"]
                    geometry_sha = entry["sha256"]
                else:
                    assert entry["producer_tag"] == producer and entry["sha256"] == geometry_sha
            else:
                assert manifest["macro_evidence"] == []
            previous = artifact["boundary"]
        if endpoint == "timing":
            timing_case, timing_summary = c, summary

    # Every source handoff is independently restarted, with old backend run paths absent.
    c = case()
    summary = require_ok(execute(c, "synthesis"))
    checkpoint = summary["artifacts"][-1]["path"]
    for endpoint in STAGES[1:]:
        progress("restart to " + endpoint)
        report = load(Path(checkpoint) / "segment.json")
        original = Path(report["run_dir"])
        original.rename(original.with_name("retained-" + original.name))
        result = require_ok(execute(c, endpoint, checkpoint))
        assert [a["boundary"] for a in result["artifacts"]] == [endpoint]
        artifact = result["artifacts"][0]
        inspect_artifact(args.inspector, artifact)
        tag = Path(load(Path(artifact["path"]) / "segment.json")["run_dir"]).name
        req = load(c["workspace"] / "project" / (tag + "-request.json"))
        assert req["checkpoint"] == checkpoint and not original.exists()
        checkpoint = artifact["path"]

    # Reject unsupported macro endpoint and synthesis-only physical adoption BEFORE backend launch.
    for mode, endpoint in (("physical", "signoff"), ("synthesis", "placement"), ("linked", "synthesis")):
        progress("unsupported scope " + mode + "/" + endpoint)
        c = case(mode)
        assert_no_launch(c, execute(c, endpoint))
    before = requests(timing_case)
    assert_no_launch(timing_case, execute(timing_case, "signoff", timing_summary["artifacts"][-1]["path"]), before)
    execution = timing_case["workspace"] / ("execution-" + timing_summary["attempt_id"] + ".json")
    engine_events = timing_case["directory"] / "engine-events.jsonl"
    engine_before = engine_events.read_bytes()
    bench = timing_case["directory"] / "bench.v"
    bench.write_text("module fixture_tb; endmodule\n")
    finalized = run([args.finalizer, "--json", "--execution", execution,
                     "--store", timing_case["directory"] / "finalization", "--accept",
                     "--support-tools", timing_case["support"], "--precheck-python", timing_case["bins"] / "python",
                     "--testbench", bench, "--testbench-top", "fixture_tb"], env=timing_case["env"])
    assert finalized.returncode != 0, finalized.stdout
    assert requests(timing_case) == before
    assert engine_events.read_bytes() == engine_before

    # Re-signed malformed metadata exercises schema semantics rather than failing
    progress("checkpoint metadata and semantic geometry negatives")
    # merely because the outer manifest digest is stale.
    c = case()
    summary = require_ok(execute(c, "placement"))
    source = summary["artifacts"][-1]
    manifest_path = Path(source["path"]) / "manifest.json"
    original = manifest_path.read_bytes()
    base = json.loads(original)
    before = requests(c)
    def write_manifest(value):
        value.pop("identity", None)
        value["identity"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                     allow_nan=False).encode()).hexdigest()
        manifest_path.chmod(0o600)
        manifest_path.write_text(json.dumps(value))
    for mutate in (
        lambda m: m.update(macro_profile="unknown"),
        lambda m: m.update(restart_targets=["cts", "signoff"]),
        lambda m: m.update(macro_evidence=[]),
        lambda m: m["macro_evidence"][0].update(producer_boundary="routing"),
        lambda m: m["macro_evidence"][0].update(path=str(c["directory"] / "outside")),
    ):
        changed = copy.deepcopy(base)
        mutate(changed)
        write_manifest(changed)
        rejected = run([args.inspector, "--json", source["path"]])
        assert rejected.returncode != 0, rejected.stdout
        assert_no_launch(c, execute(c, "cts", source["path"]), before)
    downgraded = copy.deepcopy(base)
    downgraded.update(schema_version=2)
    downgraded.pop("macro_profile")
    downgraded.pop("macro_evidence")
    write_manifest(downgraded)
    require_ok(run([args.inspector, "--json", source["path"]]))  # structurally valid generic evidence
    assert_no_launch(c, execute(c, "cts", source["path"]), before)  # not a macro restart capability
    manifest_path.write_bytes(original)
    manifest_path.chmod(0o444)
    inspect_artifact(args.inspector, source)

    # Fully rehashed but semantically false geometry is NOT rejected by host-only
    # inspection. The selected-runtime bridge must reject it before Flow.start.
    entry = base["macro_evidence"][0]
    geometry_path = Path(entry["path"])
    segment_path = Path(source["path"]) / "segment.json"
    segment_original = segment_path.read_bytes()
    geometry_original = geometry_path.read_bytes()
    geometry = json.loads(geometry_original)
    geometry["macros"][0]["contacts"][0]["net"] = "wrong"
    geometry_path.chmod(0o600)
    geometry_path.write_text(json.dumps(geometry))
    changed = copy.deepcopy(base)
    updated = {"sha256": sha(geometry_path), "bytes": geometry_path.stat().st_size}
    changed["macro_evidence"][0].update(updated)
    changed["files"][str(geometry_path.relative_to(Path(source["path"])))] = updated
    # Keep the segment's producer binding consistent as well. Otherwise the
    # host correctly rejects the stale evidence link before semantic validation
    # in the selected runtime can be exercised.
    segment = json.loads(segment_original)
    segment["macro_evidence"][0].update(updated)
    segment_path.chmod(0o600)
    segment_path.write_text(json.dumps(segment))
    changed["files"]["segment.json"] = {"sha256": sha(segment_path), "bytes": segment_path.stat().st_size}
    write_manifest(changed)
    require_ok(run([args.inspector, "--json", source["path"]]))
    rejected = execute(c, "cts", source["path"])
    assert rejected.returncode != 0, (rejected.stdout, rejected.stderr)
    new_requests = [p for p in requests(c) if p not in before]
    assert len(new_requests) == 1
    attempted = load(new_requests[0])
    assert attempted["operation"] == "execute_segment"
    assert "contact" in (c["workspace"] / (attempted["tag"] + ".log")).read_text()
    assert not (c["workspace"] / "project/src/runs" / attempted["tag"]).exists()
    geometry_path.write_bytes(geometry_original)
    geometry_path.chmod(0o444)
    segment_path.write_bytes(segment_original)
    segment_path.chmod(0o444)
    manifest_path.write_bytes(original)
    manifest_path.chmod(0o444)
    inspect_artifact(args.inspector, source)

    # A real completed publication is retained when a later fixture tool fails.
    c = case()
    c["env"]["BRIDGE_FIXTURE_FAIL_ENDPOINT"] = "OpenROAD.DetailedPlacement"
    failed = execute(c, "timing")
    assert failed.returncode != 0
    summary = json.loads(failed.stdout)
    assert [a["boundary"] for a in summary["artifacts"]] == ["synthesis", "floorplan"]
    for artifact in summary["artifacts"]:
        inspect_artifact(args.inspector, artifact)
    assert list(c["workspace"].glob("*-transport-result.json")) and list(c["workspace"].glob("*.log"))

    # Each collateral role must be checked on restart, not just recorded once.
    # Reuse one immutable source only after restoring and re-inspecting its full
    # closure. Every missing-input fresh preparation remains a separate case.
    collateral_names = [p.name for p in timing_case["collateral"]]
    c = case()
    source = require_ok(execute(c, "synthesis"))["artifacts"][0]
    before = requests(c)
    for name in collateral_names:
        progress("collateral mutation/missing " + name)
        target = next(p for p in c["collateral"] if p.name == name)
        original_bytes = target.read_bytes()
        target.write_bytes(original_bytes + b" mutation")
        assert_no_launch(c, execute(c, "placement", source["path"]), before)
        # Original checkpoint bytes remain intact even though its external closure is now invalid.
        assert load(Path(source["path"]) / "manifest.json")["identity"] == source["identity"]
        target.write_bytes(original_bytes)
        inspect_artifact(args.inspector, source)
        missing = case()
        target = next(p for p in missing["collateral"] if p.name == name)
        target.unlink()
        assert_no_launch(missing, execute(missing, "synthesis"))

    # Successful fake tool exits must lose to a detected during-call collateral mutation.
    c = case()
    c["env"]["SRAM_COLLATERAL_MUTATION"] = str(next(p for p in c["collateral"] if p.suffix == ".lib"))
    failed = execute(c, "timing")
    assert failed.returncode != 0, failed.stdout
    summary = json.loads(failed.stdout)
    assert [a["boundary"] for a in summary["artifacts"]] == ["synthesis", "floorplan"]
    assert len(list((c["workspace"] / "checkpoints").glob("placement-*"))) == 0

    for mode in ("missing", "duplicate", "wrong_supply", "wrong_instance", "bad_clearance"):
        progress("PDN negative " + mode)
        c = case()
        c["env"]["SRAM_PDN_MODE"] = mode
        failed = execute(c, "timing")
        assert failed.returncode != 0, (mode, failed.stdout)
        summary = json.loads(failed.stdout)
        assert [a["boundary"] for a in summary["artifacts"]] == ["synthesis", "floorplan"], mode
        assert not list((c["workspace"] / "checkpoints").glob("placement-*"))

    # Runtime-validation failure cannot promote an otherwise completed candidate.
    c = case()
    source = require_ok(execute(c, "synthesis"))["artifacts"][0]
    c["env"]["SERVICE_FIXTURE_MODE"] = "validate_exit17"
    failed = execute(c, "placement", source["path"])
    assert failed.returncode != 0, failed.stdout
    assert not json.loads(failed.stdout).get("artifacts")
    inspect_artifact(args.inspector, source)
    assert len(list((c["workspace"] / "checkpoints").glob("synthesis-*"))) == 1
    assert not list((c["workspace"] / "checkpoints").glob("floorplan-*"))

    c = case()
    c["env"].update(NAMED_INPUT_MUTATION="bridge/physical_runtime.py", NAMED_MUTATE_BOUNDARY="placement")
    failed = execute(c, "timing")
    assert failed.returncode != 0, failed.stdout
    assert [a["boundary"] for a in json.loads(failed.stdout)["artifacts"]] == ["synthesis", "floorplan"]
    assert not list((c["workspace"] / "checkpoints").glob("placement-*"))

    # Same-byte shared-PDK alias retarget during a successful tool call must fail
    # the preparation snapshot's resolved-path check, not only content hashing.
    c = case()
    alias = c["pdk"] / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram"
    shared = c["pdk"] / "shared-sram-original"
    alias.rename(shared)
    replacement = c["pdk"] / "shared-sram-replacement"
    shutil.copytree(shared, replacement)
    alias.symlink_to(shared, target_is_directory=True)
    c["env"].update(SRAM_ALIAS_MUTATION=str(alias), SRAM_ALIAS_DESTINATION=str(replacement))
    failed = execute(c, "timing")
    assert failed.returncode != 0, failed.stdout
    assert [a["boundary"] for a in json.loads(failed.stdout)["artifacts"]] == ["synthesis", "floorplan"]
    assert "alias" in (failed.stdout + failed.stderr).lower(), (failed.stdout, failed.stderr)

    c = case()
    c["resources"] = c["directory"] / "runtime-resources"
    shutil.copytree(resources, c["resources"], ignore=shutil.ignore_patterns("__pycache__"))
    helper = c["resources"] / "physical_runtime.py"
    helper.chmod(0o600)  # private copy; Dune source artifacts are read-only
    helper.write_text(helper.read_text() + "\n# changed installed helper\n")
    assert_no_launch(c, execute(c, "synthesis"))

    # Installed/staged helper bytes and bundle physical inputs are part of restart provenance.
    c = case()
    source = require_ok(execute(c, "synthesis"))["artifacts"][0]
    before = requests(c)
    for relative in ("physical/pdn.tcl", "physical/macro-plan.json", f"macros/{STOCK}.blackbox.v",
                      "bridge/librelane_bridge.py", "bridge/physical_runtime.py"):
        progress("restart input mutation " + relative)
        target = c["workspace"] / "project" / relative
        original_bytes = target.read_bytes()
        target.write_bytes(original_bytes + b" mutation")
        assert_no_launch(c, execute(c, "placement", source["path"]), before)
        target.write_bytes(original_bytes)
        inspect_artifact(args.inspector, source)

    print("SRAM segmented FIXTURE qualification: six fresh prefixes, five restarts, copied PDN marker, "
          "collateral/input mutation vetoes, unsupported-before-launch and retained prior publications; NO physical EDA")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emitter", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--plan-inspect", type=Path, required=True)
    parser.add_argument("--inspector", type=Path, required=True)
    parser.add_argument("--finalizer", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    for name in ("emitter", "cli", "plan_inspect", "inspector", "finalizer"):
        setattr(args, name, getattr(args, name).resolve())
    with tempfile.TemporaryDirectory(prefix="sram-segmented-", dir="/tmp/opencode") as tmp:
        verify(args, Path(tmp))


if __name__ == "__main__":
    main()
