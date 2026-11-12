"""PDK-free public bundle and execution-contract checks; no EDA imports/runs."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

consumer, bridge = map(lambda p: Path(p).resolve(), sys.argv[1:])


def run(*args, ok=True):
    p = subprocess.run([str(consumer), *map(str, args)], capture_output=True, text=True)
    assert (p.returncode == 0) == ok, p.stdout + p.stderr


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # Empty-tree commit without changing any Git configuration or staging files.
    tree = subprocess.run(["git", "-C", str(root), "mktree"], input="", text=True,
                          capture_output=True, check=True).stdout.strip()
    import os
    env = dict(os.environ, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    commit = subprocess.run(["git", "-C", str(root), "commit-tree", tree, "-m", "fixture"],
                            env=env, capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", commit], check=True)
    (root / "generator.ml").write_text("(* fixture source inventory *)\n")
    bundle = root / "bundle"
    run("emit", root, bundle)
    run("validate", bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    config_bytes = (bundle / "src/config.json").read_bytes()
    config = json.loads(config_bytes)
    contract = manifest["macro_contract"]
    assert contract["execution"] == "synthesis_only"
    assert len(contract["macros"]) == 2
    assert len({m["instance"] for m in contract["macros"]}) == 2
    assert all(m["physical_intent"]["power"]["VDDARRAY!"] == "VPWR" for m in contract["macros"])
    stub = bundle / contract["blackbox_sources"][0]
    assert "(* blackbox *)" in stub.read_text()
    assert stub.relative_to(bundle).as_posix() not in manifest["simulation_sources"]
    assert contract["implementation_simulation"]["defines"] == ["FUNCTIONAL"]
    macro = next(iter(config["MACROS"].values()))
    assert set(macro["lib"]) == set(config["STA_CORNERS"])
    assert "m55C.lib" in macro["lib"]["nom_fast_1p32V_m40C"][0]
    # Load only this pure validator: importing the bridge requires real LibreLane.
    tree = ast.parse(bridge.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate_macro_bundle")
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(bridge), "exec"), ns)
    assert ns["validate_macro_bundle"](bundle)
    original = copy.deepcopy(manifest)
    def write_resealed(value, raw):
        # Exercise semantic validation rather than stale identity/file hashes.
        (bundle / "src/config.json").write_text(json.dumps(raw))
        for entry in value["files"]:
            if entry["path"] == "src/config.json":
                entry["sha256"] = hashlib.sha256((bundle / entry["path"]).read_bytes()).hexdigest()
        payload = {k:v for k,v in value.items() if k != "identity"}
        value["identity"] = hashlib.sha256(json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        (bundle / "manifest.json").write_text(json.dumps(value))

    def swap_instances(m):
        a, b = m["macro_contract"]["macros"]
        a["instance"], b["instance"] = b["instance"], a["instance"]

    linkage_cases = [
        ("unknown resource", lambda m: m["macro_contract"]["macros"][0].update(resource="nonexistent_resource")),
        ("unknown resource with canonical name", lambda m: m["macro_contract"]["macros"][0].update(resource="nonexistent_resource",instance="hc_resource_20_nonexistent_resource")),
        ("unknown instance", lambda m: m["macro_contract"]["macros"][0].update(instance="nonexistent_instance")),
        ("swapped distinct instances", swap_instances),
        ("duplicate resource", lambda m: m["macro_contract"]["macros"][0].update(resource=m["macro_contract"]["macros"][1]["resource"])),
        ("omitted resource", lambda m: m["macro_contract"]["macros"].pop()),
        ("wrong registered request", lambda m: m["resources"][0].update(request=m["resources"][0]["request"].replace("width 16", "width 8"))),
        ("registered flops", lambda m: m["resources"][0].update(selection="((implementation Flops) (reason Explicit_flops))")),
        ("duplicate registration", lambda m: m["resources"].append(copy.deepcopy(m["resources"][0]))),
        ("behavioral registration", lambda m: m["resources"][0].update(elaborated_as="Behavioral_model")),
        ("unknown config instance", lambda m: next(iter(m["flow_configuration"]["MACROS"].values()))["instances"].update(nonexistent_instance={"location":[42.0,81.0],"orientation":"R0"})),
        ("missing config placement", lambda m: next(iter(m["flow_configuration"]["MACROS"].values()))["instances"].pop(m["macro_contract"]["macros"][0]["instance"])),
    ]
    for label, mutate in linkage_cases:
        bad = copy.deepcopy(original)
        mutate(bad)
        write_resealed(bad, bad["flow_configuration"])
        run("validate", bundle, ok=False)
        try:
            ns["validate_macro_bundle"](bundle)
        except ValueError:
            pass
        else:
            raise AssertionError("Python bridge accepted " + label)
    # Synthesis may declare only a subset of placements, but every declared intent
    # must correspond exactly to its config entry; both distinct resources remain.
    for omit in (1, 2):
        partial = copy.deepcopy(original)
        for m in partial["macro_contract"]["macros"][:omit]:
            m["physical_intent"] = None
            next(iter(partial["flow_configuration"]["MACROS"].values()))["instances"].pop(m["instance"])
        write_resealed(partial, partial["flow_configuration"])
        run("validate", bundle)
        assert ns["validate_macro_bundle"](bundle)
    (bundle / "src/config.json").write_bytes(config_bytes)
    for mutate in (lambda m: m["macro_contract"].update(execution="physical"),
                   lambda m: m["flow_configuration"].update(STA_CORNERS=["nom_*"]),
                   lambda m: m["macro_contract"]["macros"][0].update(module="unknown")):
        bad = copy.deepcopy(original)
        mutate(bad)
        (bundle / "manifest.json").write_text(json.dumps(bad))
        run("validate", bundle, ok=False)
        try:
            ns["validate_macro_bundle"](bundle)
        except ValueError:
            pass
        else:
            raise AssertionError("bridge accepted malformed macro contract")
    (bundle / "manifest.json").write_text(json.dumps(original))
    # Exercise real OCaml preflight against synthetic external files/runtimes.
    # The technology exposure may link to shared collateral within the PDK checkout.
    import importlib.util
    spec = importlib.util.spec_from_file_location("preflight_fixture", Path(__file__).parent.parent / "check_preflight.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bin_dir, tools, pdk, probes = [root / n for n in ("bin", "tools", "pdk", "probes")]
    for p in (bin_dir, tools, pdk, probes):
        p.mkdir()
    for name in ("git", "python"):
        p = bin_dir / name
        p.write_text(fixture.FAKE.replace("INTERPRETER", sys.executable))
        p.chmod(0o755)
    (root / "mode.json").write_text("{}")
    runtime_manifest = copy.deepcopy(original)
    runtime_manifest["requested_tools"].update(support_tools_revision="revision", pdk_revision="revision")
    for reference in runtime_manifest["target"]["external_references"]:
        base = tools if reference["source"] == "Support_tools" else pdk / "ihp-sg13cmos5l"
        p = base / reference["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("synthetic reference")
        reference["sha256"] = None
    shared = pdk / "shared_sram"
    shared.mkdir()
    (pdk / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram").symlink_to(shared, target_is_directory=True)
    external = set()
    for macro in contract["macros"]:
        for c in macro["collateral"]:
            if c["reference"].startswith("pdk_dir::"):
                p = pdk / "ihp-sg13cmos5l" / c["reference"][9:]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("synthetic macro collateral")
                external.add(p)
    (bundle / "manifest.json").write_text(json.dumps(runtime_manifest))
    env = dict(os.environ, PATH=str(bin_dir), FIXTURE=str(root))
    def preflight():
        fresh = Path(tempfile.mkdtemp(dir=probes))
        result = subprocess.run([str(consumer), "preflight", str(bundle), str(tools), str(pdk),
                                 str(bin_dir / "python"), str(bridge.parent), str(fresh)],
                                env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    report = preflight()
    assert report["ready"], report
    assert len([r for r in report["references"] if r["role"].startswith("macro:")]) >= 8
    missing = next(p for p in external if p.suffix == ".cdl")
    missing.unlink()
    report = preflight()
    assert not report["ready"] and any("missing macro Spice_netlist" in i for i in report["issues"]), report
    missing.write_text("synthetic macro collateral")
    (root / "mode.json").write_text('{"dirty":true}')
    report = preflight()
    assert not report["ready"] and any("modified macro" in i for i in report["issues"]), report
    (root / "mode.json").write_text('{}')
    launch_fixture = r'''
if '-m' in a and a[a.index('-m')+1] == 'librelane':
    assert a[a.index('--jobs')+1] == '6'
    assert a[a.index('--to')+1] == 'Yosys.Synthesis'
    assert all(x in a for x in ('OPENROAD_THREADS=6', 'STA_THREADS=1', 'KLAYOUT_DRC_THREADS=6'))
    assert os.environ['OMP_NUM_THREADS'] == '6'
    project = pathlib.Path.cwd()
    manifest = json.loads((project/'manifest.json').read_text())
    assert (project/manifest['macro_contract']['blackbox_sources'][0]).is_file()
    config = json.loads((project/'src/config.json').read_text())
    assert config['MACROS']
    if mode.get('mutate_macro_path'):
        pathlib.Path(mode['mutate_macro_path']).write_text('changed during execution')
    if mode.get('mutate_stub'):
        (project/manifest['macro_contract']['blackbox_sources'][0]).write_text('changed stub during execution')
    print('synthetic SRAM synthesis launch; no EDA')
    sys.exit(0)
'''
    fake = fixture.FAKE.replace("if pathlib.Path(sys.argv[0]).name == 'docker':",
                                launch_fixture + "\nif pathlib.Path(sys.argv[0]).name == 'docker':", 1)
    (bin_dir / "python").write_text(fake.replace("INTERPRETER", sys.executable))
    launches = root / "launches"
    launches.mkdir()
    cmd = [str(bundle), str(tools), str(pdk), str(bin_dir / "python"), str(bridge.parent), str(launches)]
    result = subprocess.run([str(consumer), "coarse-synthesis", *cmd], env=env,
                            capture_output=True, text=True, check=True)
    outcome = json.loads(result.stdout)
    assert outcome["status"] == "completed", outcome
    record = json.loads(Path(outcome["run_record"]).read_text())
    assert record["commands"][-1][-1] == "src/config.json"
    liberty_for_mutation = next(p for p in external if p.suffix == ".lib")
    for mode in ({"mutate_macro_path":str(liberty_for_mutation)}, {"mutate_stub":True}):
        (root / "mode.json").write_text(json.dumps(mode))
        failed = subprocess.run([str(consumer), "coarse-synthesis", *cmd], env=env,
                                capture_output=True, text=True)
        assert failed.returncode == 1, failed.stderr
        failed_outcome = json.loads(failed.stdout)
        assert failed_outcome["status"] == "failed"
        evidence = json.loads(Path(failed_outcome["run_record"]).read_text())
        assert "changed during execution" in evidence["error"], evidence
        liberty_for_mutation.write_text("synthetic macro collateral")
    (root / "mode.json").write_text("{}")
    rejected = subprocess.run([str(consumer), "coarse-full", *cmd], env=env,
                              capture_output=True, text=True)
    assert rejected.returncode and "physical execution" in rejected.stderr
    assert len(list(launches.iterdir())) == 3  # full rejected before allocating a run
    for reference in runtime_manifest["target"]["external_references"]:
        if reference["source"] == "Support_tools":
            p = bundle / "tt" / reference["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("synthetic reference")
    functions = [n for n in ast.parse(bridge.read_text()).body if isinstance(n, ast.FunctionDef)
                 and n.name in ("digest", "record", "closure", "validate_macro_bundle")]
    ns = {"json": json, "hashlib": hashlib, "Path": Path, "ToolPath": Path}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(bridge), "exec"), ns)
    before = ns["closure"]({}, bundle, pdk)
    assert str(stub) in before
    assert any(p.endswith(".cdl") for p in before)
    liberty = next(p for p in external if p.suffix == ".lib")
    liberty.write_text("changed macro Liberty")
    assert ns["closure"]({}, bundle, pdk) != before
    # The physical capability is independently validated, not inferred from the
    # presence of a placement or a successful mocked implementation process.
    physical_bundle = root / "physical-bundle"
    run("emit-physical", root, physical_bundle)
    run("validate", physical_bundle)
    assert ns["validate_macro_bundle"](physical_bundle)
    pm = json.loads((physical_bundle / "manifest.json").read_text())
    assert pm["macro_contract"]["schema_version"] == 2
    assert pm["macro_contract"]["physical_plan"] == json.loads((physical_bundle / "physical/macro-plan.json").read_text())
    physical_original = copy.deepcopy(pm)
    negatives = [
        lambda m: m["macro_contract"]["physical_plan"].update(lvs_scope="device_lvs"),
        lambda m: m["macro_contract"]["physical_plan"]["macros"][0].update(instance="not_registered"),
        lambda m: m["macro_contract"]["macros"][0].update(physical_intent=None),
        lambda m: m["flow_configuration"].update(EXTRA_SPICE_MODELS=[]),
        lambda m: m["flow_configuration"].update(RUN_MAGIC_DRC=False),
        lambda m: m["flow_configuration"].update(ERROR_ON_ILLEGAL_OVERLAPS=False),
        lambda m: m["flow_configuration"].update(ERROR_ON_LVS_ERROR=False),
        lambda m: m["flow_configuration"]["KLAYOUT_DRC_OPTIONS"].update(no_feol="true"),
        lambda m: m["flow_configuration"]["KLAYOUT_DRC_OPTIONS"].update(no_recommended=1),
        lambda m: m["flow_configuration"]["KLAYOUT_DRC_OPTIONS"].update(threads=20),
    ]
    for mutate in negatives:
        bad = copy.deepcopy(physical_original)
        mutate(bad)
        (physical_bundle / "manifest.json").write_text(json.dumps(bad))
        (physical_bundle / "src/config.json").write_text(json.dumps(bad["flow_configuration"]))
        run("validate", physical_bundle, ok=False)
        try:
            ns["validate_macro_bundle"](physical_bundle)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError("Python bridge accepted malformed physical contract")
    (physical_bundle / "manifest.json").write_text(json.dumps(physical_original))
    (physical_bundle / "src/config.json").write_text(json.dumps(physical_original["flow_configuration"]))
    # Configure a fake pinned PDK checking tree. No EDA is imported/executed.
    for relative in ("ihp-sg13cmos5l/libs.tech/klayout", "ihp-sg13g2/libs.tech/klayout/tech/drc",
                     "ihp-sg13cmos5l/libs.tech/magic", "ihp-sg13cmos5l/libs.tech/netgen"):
        directory = pdk / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "fixture-dependency").write_text("pinned transitive checking input")
    previous_replay_models = {}
    for name in ("sg13cmos5l_stdcell.v", "sg13cmos5l_udp.v"):
        path = pdk / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog" / name
        previous_replay_models[path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pinned replay model")
    pm = copy.deepcopy(physical_original)
    for entry in pm["files"]:
        if entry["path"] == "src/config.json":
            entry["sha256"] = hashlib.sha256((physical_bundle / entry["path"]).read_bytes()).hexdigest()
    pm["requested_tools"] = copy.deepcopy(runtime_manifest["requested_tools"])
    pm["target"]["external_references"] = copy.deepcopy(runtime_manifest["target"]["external_references"])
    (physical_bundle / "manifest.json").write_text(json.dumps(pm))
    for name in ("python", "docker"):
        p = bin_dir / name
        p.write_text(fixture.FAKE.replace("INTERPRETER", sys.executable))
        p.chmod(0o755)
    # Reuse the actual preflight boundary with this physical bundle.
    old_bundle = bundle
    bundle = physical_bundle
    report = preflight()
    assert report["ready"], report
    assert len([r for r in report["references"] if r["role"] == "physical:checking_collateral"]) == 6
    plan_file = bundle / "physical/macro-plan.json"
    saved_plan = plan_file.read_bytes()
    plan_file.write_text('{}')
    assert not preflight()["ready"]
    plan_file.write_bytes(saved_plan)
    pdn_file = bundle / "physical/pdn.tcl"
    saved_pdn = pdn_file.read_bytes()
    pdn_file.write_bytes(saved_pdn + b"\n# resealed noncanonical physical input\n")
    resealed = copy.deepcopy(pm)
    for entry in resealed["files"]:
        if entry["path"] == "physical/pdn.tcl":
            entry["sha256"] = hashlib.sha256(pdn_file.read_bytes()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(resealed))
    assert any("canonical generation" in issue for issue in preflight()["issues"])
    pdn_file.write_bytes(saved_pdn)
    (bundle / "manifest.json").write_text(json.dumps(pm))
    # Fake Docker observes the real emitted argv and effective environment. The
    # second launch is mandatory physical-evidence validation, not a success flag.
    physical_launch = r'''
if pathlib.Path(sys.argv[0]).name == 'docker' and a[0] == 'run' and '-v' in a:
    assert '--cpus=6' in a
    assert all(x in a for x in ('OMP_NUM_THREADS=6', 'OPENROAD_THREADS=6', '_OPENLANE_MAX_CORES=6'))
    project = pathlib.Path(a[a.index('-w')+1])
    if any(x.endswith('/macro-flow.py') for x in a):
        assert (project/'physical-runtime.py').is_file()
        assert '--to' not in a and a[a.index('--jobs')+1] == '6'
        assert 'STA_THREADS=1' in a
        assert 'MAX_SLEW_VIOLATION_CORNERS=*' in a
        assert 'MAX_CAP_VIOLATION_CORNERS=*' in a
        assert 'ERROR_ON_MAGIC_DRC=false' in a
    else:
        assert a[-3:] == ['check', str(project), str(root/'pdk')]
        if mode.get('physical_check_fail'): sys.exit(9)
        (project/'runs/asic/physical-checks.json').write_text('{"status":"fixture-only"}')
    sys.exit(0)
'''
    fake = fixture.FAKE.replace("if pathlib.Path(sys.argv[0]).name == 'docker':",
                                physical_launch + "\nif pathlib.Path(sys.argv[0]).name == 'docker':", 1)
    (bin_dir / "docker").write_text(fake.replace("INTERPRETER", sys.executable))
    cmd = [str(bundle), str(tools), str(pdk), str(bin_dir / "python"), str(bridge.parent), str(launches)]
    for mode, expected in (({}, "completed"), ({"physical_check_fail": True}, "failed")):
        (root / "mode.json").write_text(json.dumps(mode))
        result = subprocess.run([str(consumer), "coarse-full", *cmd], env=env, capture_output=True, text=True)
        assert result.returncode == (0 if expected == "completed" else 1), result.stderr
        outcome = json.loads(result.stdout)
        assert outcome["status"] == expected, outcome
        record = json.loads(Path(outcome["run_record"]).read_text())
        assert len(record["commands"]) == 2
        if expected == "completed":
            completed_physical = Path(outcome["run_dir"])
    # Exercise the native postcheck service through actual subprocesses. Docker
    # simulates only tools; model derivation and the run-local TT adapter are real.
    spec = importlib.util.spec_from_file_location("postcheck_fixture", Path(__file__).parent.parent / "check_postcheck.py")
    post_fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(post_fixture)
    pre = tools / "precheck"
    pre.mkdir(exist_ok=True)
    (pre / "default.nix").write_text('let pkgs = import (fetchTarball "https://example.invalid/pinned.tar.gz") {}; in pkgs.mkShell {}')
    (pre / "tool-versions.json").write_text(json.dumps({"klayout": "0.28.17"}))
    (pre / "precheck.py").write_text(post_fixture.PRECHECK + '''
def unused_pinned_cmos5l_invocation(gds):
    return klayout_drc(gds, "sg13cmos5l",
        f"{PDK_ROOT}/{PDK_NAME}/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",
    )
''')
    import venv
    import shutil
    virtual = root / "precheck-venv"
    venv.EnvBuilder(with_pip=False).create(virtual)
    dist = next((virtual / "lib").glob("python*/site-packages")) / "klayout-0.28.17.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: klayout\nVersion: 0.28.17\n")
    (bin_dir / "env").symlink_to(shutil.which("env"))
    (bin_dir / "python3").symlink_to(sys.executable)
    model_launch = r'''
    if 'models' in a:
        assert '--cpus=6' in a and 'OMP_NUM_THREADS=6' in a
        index = a.index('models')
        sys.exit(subprocess.call([sys.executable, *a[index-1:]]))
'''
    fake = post_fixture.FAKE.replace("    if 'iverilog' in a:", model_launch + "    if 'iverilog' in a:", 1)
    fake = fake.replace("if name=='nix-shell':", "if name=='nix-shell':\n    assert a[a.index('--max-jobs')+1]=='1' and a[a.index('--cores')+1]=='6'", 1)
    fake = fake.replace("    if 'iverilog' in a:", '''    if os.environ.get('OMP_NUM_THREADS')=='6': assert '--cpus=6' in a
    if 'iverilog' in a:
''', 1)
    fake = fake.replace("        block('compile')", '''        block('compile')
        assert any(x.endswith('stdcell-functional.v') for x in a)
        assert len([x for x in a if pathlib.Path(x).name.startswith('model-')]) == 3
        assert not any(x.endswith('.blackbox.v') or x.endswith('sg13cmos5l_stdcell.v') for x in a)
''', 1)
    fake = fake.replace("if os.environ.get('OMP_NUM_THREADS')=='20': assert len(os.sched_getaffinity(0))<=20",
                        "if os.environ.get('OMP_NUM_THREADS')=='6': assert len(os.sched_getaffinity(0))<=6")
    for name in ("docker", "nix-shell"):
        path = bin_dir / name
        path.write_text(fake.replace("INTERPRETER", sys.executable))
        path.chmod(0o755)
    record_path = completed_physical / "run.json"
    record = json.loads(record_path.read_text())
    binding = record["execution_binding"]
    binding["engine_identity"].update(rootless=False, server_version="29")
    record["environment"]["execution_binding"] = copy.deepcopy(binding)
    flow = completed_physical / "project/runs/asic"
    (flow / "final/gds").mkdir(parents=True)
    (flow / "final/nl").mkdir()
    top = pm["top_module"]
    gds = flow / f"final/gds/{top}.gds"
    gds.write_text("assembled fixture GDS")
    (flow / f"final/nl/{top}.nl.v").write_text("sg13cmos5l_buf_1 buffer(.A(a),.X(x));")
    evidence_path = completed_physical / "physical-checks.json"
    evidence_path.write_text(json.dumps({"status": "pass", "gds_sha256": hashlib.sha256(gds.read_bytes()).hexdigest(), "artifacts": {}}))
    record["physical_checks_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record))
    model_dir = pdk / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog"
    (model_dir / "sg13cmos5l_stdcell.v").write_text("module sg13cmos5l_buf_1(input A, output X); assign X=A; endmodule\n")
    run_preflight = json.loads((completed_physical / "preflight.json").read_text())
    for reference in run_preflight["references"]:
        path = Path(reference["path"])
        reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (completed_physical / "preflight.json").write_text(json.dumps(run_preflight))
    record["environment"]["references"] = copy.deepcopy(run_preflight["references"])
    record_path.write_text(json.dumps(record))
    bench = root / "physical-replay.v"
    bench.write_text("module memory_tb; endmodule\n")
    (root / "mode.json").write_text("{}")
    result = subprocess.run([str(consumer), "postcheck-physical", str(completed_physical), str(tools), str(pdk),
                             str(virtual / "bin/python"), str(bench), str(bridge.parent)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    post = json.loads(result.stdout)
    post_record = json.loads(Path(post["check_record"]).read_text())
    assert post_record["checks"] == {"precheck": "pass", "gate_level": "pass"}
    assert post_record["resource_limits"]["container_cpus"] == 6
    assert "replay-models.json" in post_record["artifacts"]
    assert post_record["resource_limits"]["tool_timeout_seconds"] == 1800
    model_ref = pm["macro_contract"]["implementation_simulation"]["models"][0]
    model_file = pdk / "ihp-sg13cmos5l" / model_ref[9:]
    original_model = model_file.read_bytes()
    model_file.write_text("model changed after recorded preflight")
    rejected = subprocess.run([str(consumer), "postcheck-physical", str(completed_physical), str(tools), str(pdk),
                               str(virtual / "bin/python"), str(bench), str(bridge.parent)], env=env, capture_output=True, text=True)
    assert rejected.returncode == 2 and "physical postcheck collateral changed" in rejected.stderr, rejected.stdout + rejected.stderr
    model_file.write_bytes(original_model)
    print("Physical runtime: schema-2 positive, 10 OCaml/Python semantic negatives, checked plan/closure, Docker6/STA1 and mandatory evidence-check failure passed")
    bundle = old_bundle
    for dependency in pdk.rglob("fixture-dependency"):
        dependency.unlink()
    for path, content in previous_replay_models.items():
        if content is None:
            path.unlink()
        else:
            path.write_bytes(content)
    execute = next(n for n in ast.parse(bridge.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "execute")
    exec(compile(ast.Module(body=[execute], type_ignores=[]), str(bridge), "exec"), ns)
    candidate = root / "must-not-publish"
    executor_budgets = []
    def set_tpe(executor):
        executor_budgets.append(executor._max_workers)
        executor.shutdown()
    # Match the bridge's executor API without importing the real EDA package.
    with patch.dict(sys.modules, {"librelane": types.ModuleType("librelane"),
                                 "librelane.common": types.SimpleNamespace(set_tpe=set_tpe)}):
        try:
            ns["execute"]({"project":str(bundle), "pdk_root":str(pdk), "candidate":str(candidate),
                           "boundary":"synthesis", "operation":"execute_segment", "checkpoint":None})
        except ValueError as e:
            assert "segmented checkpoints are unsupported" in str(e)
        else:
            raise AssertionError("macro checkpoint capability must not be minted")
    assert executor_budgets == [6]
    assert not candidate.exists()
    liberty.write_text("synthetic macro collateral")
    assert ns["closure"]({}, bundle, pdk) == before
    stub.write_text(stub.read_text() + "// changed stub\n")
    assert ns["closure"]({}, bundle, pdk) != before
    print(f"Public SRAM bundle: {len(linkage_cases)} resealed linkage rejections in OCaml/Python; two-resource and partial-placement positives; deterministic emission, preflight, launch and closure checks passed")
