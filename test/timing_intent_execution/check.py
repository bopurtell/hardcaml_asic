"""Named association reader fixtures. No EDA or production Python parser import.

Genuine public bundle/plan/checkpoint readers surround synthetic native output.
Mutations reseal advertised inventories to test association, not incidental hashes.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types

EMITTER, API, INSPECT, REPORT, CLI, HELPER = map(lambda p: Path(p).resolve(), sys.argv[1:])
COUNT = 0
IMAGE = "sha256:d109140b8f17fc54f4fca998beb8124f4944ec52e339eebd2250854a18b5a"


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.chmod(0o600)
    p.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def read(p):
    return json.loads(p.read_bytes())


def sha(p):
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def ref(p):
    return dict(path=str(p), sha256=sha(p))


def file_record(p):
    return dict(sha256=sha(p), bytes=p.stat().st_size)


def process_record(code=0, outcome="exited", cleanup_error=None):
    return dict(outcome=outcome, code=code, error=None, pid=1234,
                child_status=dict(outcome="exited", code=code) if outcome == "exited" else None,
                elapsed_seconds=0.01, stdout_path=None, stderr_path=None, cleanup_error=cleanup_error)


def call(args, code=0, diagnostic=None):
    global COUNT
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True, timeout=30,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
    if diagnostic:
        assert diagnostic in result.stderr, (diagnostic, result.stderr)
    COUNT += 1
    return json.loads(result.stdout) if result.stdout.strip() else None


def argv(root, pdk, payload, technology=None):
    q = root.parent / "argv.json"
    write(q, dict(root=str(root), pdk=str(pdk), payload=list(map(str, payload)), technology=technology))
    return call([API, "argv", q])


def fixture(base, bundle, plan, attempt="attempt"):
    root = base / "workspace"
    shutil.copytree(bundle, root / "project")
    project = root / "project"
    meta = base / "metadata"
    meta.mkdir()
    methodology = call([API, "methodology", meta, attempt, json.dumps(plan)])
    binding = call([API, "binding"])
    image = binding["image_identity"]["image_id"]
    manifest = read(project / "manifest.json")
    tag = "synthesis-native-fixture"
    run = project / "src/runs" / tag
    producer = run / "06-yosys-synthesis"
    producer.mkdir(parents=True)
    (producer / "mapped.v").write_text("// modeled mapped RTL, no native execution\n")
    write(producer / "config.json", dict(meta=dict(step="Yosys.Synthesis", librelane_version="3.1.0.dev3")))
    native_state = dict(nl=str(producer / "mapped.v"), metrics={})
    write(producer / "state_out.json", native_state)
    candidate = project / "candidates" / tag
    candidate.mkdir(parents=True)
    write(candidate / "state.json", native_state)
    resolved = dict(DEFAULT_CORNER="nom_typ_1p20V_25C", STA_CORNERS=["fast", "nom_typ_1p20V_25C", "slow"],
                    STA_THREADS=1, OPENROAD_THREADS=6, PNR_SDC_FILE=str(project / "constraints/top.sdc"),
                    STA_EXTRA_CORNER_TCL_FILE=None, STA_MACRO_PRIORITIZE_NL=True,
                    DESIGN_NAME=manifest["top_module"], EXTRA_LIBS=None, EXTRA_VERILOG_MODELS=None)
    write(candidate / "resolved.json", resolved)
    closure_file = base / "retained-collateral"
    closure_file.write_text("host-bound fixture collateral")
    closure = {str(closure_file): file_record(closure_file)}
    closure.update({str(project / entry["path"]): file_record(project / entry["path"]) for entry in manifest["files"]})
    closure[str(project / "manifest.json")] = file_record(project / "manifest.json")
    # The genuine prepared plan pins this exact staged support locator. Retain it
    # as actual conservative input, independently of whether its pin is null.
    for reference in manifest["target"]["external_references"]:
        if reference["source"] == "Support_tools":
            staged = project / "tt" / reference["path"]
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text("floorplan fixture")
            closure[str(staged)] = file_record(staged)
    write(candidate / "closure.json", closure)
    bridge_hash = "b" * 64
    segment = dict(status="completed", boundary="synthesis", source_boundary=None, **{"from": None},
                   to="Yosys.Synthesis", run_dir=str(run),
                   returned_steps=["Verilator.Lint", "Checker.LintTimingConstructs", "Checker.LintErrors",
                                   "Checker.LintWarnings", "Yosys.JsonHeader", "Yosys.Synthesis"],
                   bridge_sha256=bridge_hash, metrics={},
                   metric_origin="cumulative_final_state; not per-step measurement provenance", configured_gates={},
                   gate_evidence="configured Classic gates; returned_steps are successful outer step objects, not body/skip provenance",
                   acceptance="not_evaluated")
    write(candidate / "segment.json", segment)
    checkpoint = root / "checkpoints/synthesis"
    (checkpoint / "views").mkdir(parents=True)
    shutil.copyfile(producer / "mapped.v", checkpoint / "views/mapped.v")
    (checkpoint / "views/header.json").write_text("{}")
    state = dict(nl=str(checkpoint / "views/mapped.v"), json_h=str(checkpoint / "views/header.json"), metrics={})
    write(checkpoint / "state.json", state)
    for name in ("resolved.json", "closure.json", "segment.json"):
        shutil.copyfile(candidate / name, checkpoint / name)
    compatibility = dict(bundle_identity=manifest["identity"], target=manifest["target"],
                         requested_tools=manifest["requested_tools"], binding=binding,
                         bridge_sha256=bridge_hash, project=str(project), pdk_root=str(base / "pdk"))
    cp_manifest = dict(schema_version=2, location=str(checkpoint), boundary="synthesis", restart_supported=True,
                       restart_targets=["floorplan", "placement"], tested_image_id=image, closure_scope="path_bound",
                       compatibility=compatibility,
                       files={str(p.relative_to(checkpoint)): file_record(p) for p in checkpoint.rglob("*") if p.is_file()},
                       external=closure, metrics={}, acceptance="not_evaluated")
    cp_manifest["identity"] = hashlib.sha256(json.dumps(cp_manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    write(checkpoint / "manifest.json", cp_manifest)
    checkpoint_ref = dict(path=str(checkpoint), identity=cp_manifest["identity"], boundary="synthesis", restart_supported=True,
                          restart_targets=["floorplan", "placement"], tested_image_id=image,
                          closure_scope="path_bound", acceptance="not_evaluated")
    request_path = project / f"{tag}-request.json"
    reply_path = project / f"{tag}-reply.json"
    request = dict(protocol_version=1, operation="execute_segment", request_id=tag, reply_path=str(reply_path),
                   boundary="synthesis", checkpoint=None, source_boundary=None, candidate=str(candidate),
                   project=str(project), pdk_root=str(base / "pdk"), tag=tag, bridge_sha256=bridge_hash)
    write(request_path, request)
    request_hash = call([API, "request_identity", request_path])
    reply = {k: request[k] for k in ("protocol_version", "operation", "request_id", "boundary", "candidate", "bridge_sha256")}
    reply.update(request_sha256=request_hash, outcome="completed", error=None,
                 artifacts={name: dict(path=str(candidate / name), **file_record(candidate / name)) for name in
                            ("state.json", "resolved.json", "closure.json", "segment.json")})
    write(reply_path, reply)
    correlation = dict(request_id=tag, request_path=str(request_path), request_sha256=request_hash)
    launch_path = root / f"{tag}-transport.json"
    terminal_path = root / f"{tag}-transport-result.json"
    launch = dict(**correlation, kind="backend_transport_launch", fresh_run_dir=str(run),
                  cwd=str(project), resources=dict(cpus=6, openlane_max_cores=6, openroad_threads=6, omp_num_threads=6),
                  argv=argv(root, base / "pdk", [project / "bridge/librelane_bridge.py", request_path], manifest["target"]["technology"]),
                  host_uid=os.getuid(), host_gid=os.getgid(), binding=binding)
    terminal = dict(**correlation, kind="backend_transport_result", transport_succeeded=True,
                    process=process_record(), cleanup=dict(error=None,
                        attempts=[dict(process=process_record(1), diagnostic="No such container: fixture")]))
    write(launch_path, launch)
    write(terminal_path, terminal)
    directory = root / "timing-intent" / attempt
    directory.mkdir(parents=True)
    expectation = dict(schema_version=1, kind="hardcaml-asic.named-timing-expectation", attempt_id=attempt,
                       occurrence_id=f"{attempt}/implementation.synthesis", profile="opensta-2.7-native-state-v2",
                       directory=str(directory), request=str(directory / "request.json"),
                       publication=str(directory / "publication.json"), companion=str(directory / "analysis.json"))
    occurrence = dict(occurrence_id=expectation["occurrence_id"], action_key="implementation.synthesis", status="running",
                      exit_code=None, duration_seconds=0, best_effort=False, error=None, verdict=None)
    plan_wire = dict(schema_version=4, stages=["synthesis"], initial_boundary=None)
    publication_sources = [request_path, reply_path, launch_path, terminal_path]
    publication_sources += list(candidate.iterdir())
    publication_sources += [p for p in checkpoint.rglob("*") if p.is_file()]
    publication = dict(schema_version=1, kind="hardcaml-asic.named-timing-publication", expectation=expectation,
                       methodology=methodology, plan=plan_wire, occurrence=copy.deepcopy(occurrence), checkpoint=checkpoint_ref,
                       request=request, reply=reply, segment=segment, sources=[ref(p) for p in publication_sources])
    write(directory / "publication.json", publication)
    shutil.copyfile(HELPER, directory / "timing_intent_runtime.py")
    analysis_request = dict(schema=1, kind="hardcaml-asic.named-timing-request", expectation=expectation,
                            publication=ref(directory / "publication.json"), execution_root=str(root), project=str(project),
                            analysis_directory=str(directory), host_uid=os.getuid(), host_gid=os.getgid(), binding=binding,
                            pdk_root=str(base / "pdk"), helper_sha256=sha(directory / "timing_intent_runtime.py"),
                            producer_sha256=sha(project / "constraints/pt7-native-coverage.tcl") if (project / "constraints/pt7-native-coverage.tcl").exists() else "0" * 64,
                            manifest_sha256=sha(project / "manifest.json"), build_identity=manifest["identity"],
                            analysis_id=attempt + "/pre-pnr-sta", attempt_id=attempt, occurrence_id=expectation["occurrence_id"],
                            checkpoint=checkpoint_ref, synthesis_run_dir=str(run), state_path=str(checkpoint / "state.json"),
                            resolved_path=str(checkpoint / "resolved.json"))
    write(directory / "request.json", analysis_request)
    write(directory / "transport.json", dict(request_sha256=sha(directory / "request.json"), binding=binding,
          argv=argv(root, base / "pdk", [directory / "timing_intent_runtime.py", directory / "request.json"]),
          workers=6, sta_threads=1, timeout_seconds=600))
    write(directory / "transport-result.json", dict(request_sha256=sha(directory / "request.json"),
          succeeded=True, outcome="exited", code=0, cleanup_error=None))
    occurrence.update(status="completed", exit_code=0, duration_seconds=1)
    execution = root / f"execution-{attempt}.json"
    write(execution, dict(attempt_id=attempt, plan=plan_wire, occurrences=[occurrence], artifacts=[checkpoint_ref],
                         status="completed", exit_code=0, methodology=methodology, timing_intent=expectation))
    return execution, directory, resolved


def native_fixture(execution, directory, resolved):
    """Actual retained helper runs over a modeled native Step API."""
    root = execution.parent
    spec = importlib.util.spec_from_file_location("named_fixture_runtime", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    library = root.parent / "cells.lib"
    library.write_text('library (fixture) { time_unit : "1ns"; }\n')
    script = root.parent / "corner.tcl"
    script.write_text("# model native script\n")

    class Step:
        @classmethod
        def get_all_config_variables(cls):
            return [types.SimpleNamespace(name=k) for k in resolved]

        @classmethod
        def load(cls, config, state_in, pdk_root):
            step = cls()
            step.config = read(Path(config))
            step.state = read(Path(state_in))
            assert step.state["nl"] == read(directory / "request.json")["checkpoint"]["path"] + "/views/mapped.v"
            return step

        def get_script_path(self):
            return str(script)

        def _get_corner_files(self, corner, prioritize_nl):
            return corner, types.SimpleNamespace(libs=[str(library)], netlists=[], spefs=[], current_corner_spef=None, extra_spefs_backcompat=None)

        def start(self, toolbox, step_dir):
            d = Path(step_dir)
            d.mkdir()
            write(d / "config.json", self.config)
            write(d / "state_in.json", self.state)
            corner = resolved["DEFAULT_CORNER"]
            (d / corner).mkdir()
            self.run_corner(self.state, dict(CURRENT_NL=self.state["nl"], _CURRENT_CORNER_NAME=corner,
                            STA_EXTRA_CORNER_TCL_FILE=self.config["STA_EXTRA_CORNER_TCL_FILE"]), corner, str(d / corner))
            write(d / "state_out.json", self.state)
            (d / "runtime.txt").write_text("00:00:00.010")

        def run_corner(self, state, env, corner, corner_dir):
            d = Path(corner_dir)
            receipt = read(d / "receipt.json")
            def event(name, **fields):
                return dict(schema_version=1, event=name, **fields)
            counts = dict(stage_count=2, exception_count=1)
            scope = dict(id="capture", kind="async_capture", source="ena", targets=["u0/D"])
            stages = [event("stage", id=f"rx{i}", anchor=f"hc_pt7_rx{i}", cell=f"u{i}", master="sg13cmos5l_dfrbpq_1",
                            q_pin=f"u{i}/Q", data_pin=f"u{i}/D", async_pin=f"u{i}/RESET_B") for i in range(2)]
            events = [event("begin", **counts), *stages, event("resolved", **scope), event("validated", **counts),
                      event("applied", **scope), event("complete", **counts)]
            log = "PT7_AUDIT_RECEIPT " + json.dumps(receipt, separators=(",", ":")) + "\n"
            log += "".join("HC_ASIC_TIMING_V1\t" + json.dumps(e, separators=(",", ":")) + "\n" for e in events)
            log += "HC_ASIC_NATIVE_V2_COMPLETE\nPT7_AUDIT_COMPLETE\n"
            (d / "sta.log").write_text(log)
            (d / "native-state.sdc").write_text("current_design " + resolved["DESIGN_NAME"] + "\nset_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]\n")
            categories = ("no_input_delay", "no_output_delay", "multiple_clock", "no_clock", "unconstrained_endpoints", "loops", "generated_clocks")
            write(d / "native-data.json", dict(schema=2, contract="hardcaml-asic.timing-native-coverage",
                  profile="opensta-2.7-native-state-v2", complete=True,
                  categories=[dict(name=n, group_count=0, groups=[]) for n in categories], endpoint_count=2, endpoints=["u0/D", "u1/D"]))
            (d / "native-checks.rpt").write_text("HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2\n" + "".join(
                f"HC_ASIC_NATIVE_CATEGORY_BEGIN {n}\nHC_ASIC_NATIVE_CATEGORY_END {n}\n" for n in categories) + "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n")
            (d / "native-endpoints.rpt").write_text('"u0/D"\n"u1/D"\nHC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n')
            for name in ("downstream_min.rpt", "downstream_max.rpt"):
                (d / name).write_text("modeled VIOLATED witness; not native acceptance\n")
            return {}

    modules = {"librelane": types.ModuleType("librelane"), "librelane.steps": types.ModuleType("librelane.steps"),
               "librelane.steps.openroad": types.SimpleNamespace(STAPrePNR=Step),
               "librelane.steps.tclstep": types.SimpleNamespace(TclStep=types.SimpleNamespace(value_to_tcl=lambda xs: " ".join(helper.tcl_word(str(x)) for x in xs))),
               "librelane.common": types.SimpleNamespace(Toolbox=lambda path: path, set_tpe=lambda executor: None)}
    old = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    original = subprocess.run
    try:
        subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout="2.7.0\n", stderr="", returncode=0)
        helper.execute(directory / "request.json")
    finally:
        subprocess.run = original
        for k, v in old.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


with tempfile.TemporaryDirectory(prefix="named-timing-reader-") as tmp:
    tmp = Path(tmp)
    bundle = tmp / "bundle"
    plan = call([EMITTER, bundle])
    execution, directory, resolved = fixture(tmp / "baseline", bundle, plan)
    call([API, "decode", execution])
    native_fixture(execution, directory, resolved)
    call([API, "publish", directory, "true"])
    before_queries = {p: (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in execution.parent.parent.rglob("*") if p.is_file()}
    baseline = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert call([CLI, "timing-intent-inspect", execution, "--kind", "execution", "--json"], 1) == baseline
    capability = call([CLI, "--version", "--json"])["cli_contract"]["capabilities"]["timing_intent_execution1"]
    assert capability["target"] == "exact_execution_json" and capability["named_archive"] is False and capability["relocation"] is False
    assert baseline["status"] == "completed" and baseline["verdict"] == "unavailable"
    assert baseline["corners"][0]["native_coverage"]["stored_exception_scope"]["status"] == "matched"
    report = call([REPORT, "--target", execution, "--json"])
    assert report["timing_intent"] == baseline
    assert call([API, "sources", execution]) == baseline["sources"]
    assert before_queries == {p: (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
                              for p in execution.parent.parent.rglob("*") if p.is_file()}
    frozen = {p: p.read_bytes() for p in execution.parent.rglob("*") if p.is_file()}
    frozen.update({p: p.read_bytes() for p in execution.parent.parent.joinpath("metadata").rglob("*") if p.is_file()})

    def restore():
        for p in execution.parent.rglob("*"):
            if p.is_file() and p not in frozen:
                p.unlink()
        for p, raw in frozen.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                p.chmod(0o600)
            p.write_bytes(raw)

    def reseal(directory=directory):
        pub_path = directory / "publication.json"
        pub = read(pub_path)
        pub["sources"] = [ref(Path(r["path"])) for r in pub["sources"]]
        write(pub_path, pub)
        req_path = directory / "request.json"
        req = read(req_path)
        req["publication"] = ref(pub_path)
        write(req_path, req)
        for name in ("transport.json", "transport-result.json"):
            j = read(directory / name)
            j["request_sha256"] = sha(req_path)
            write(directory / name, j)
        a_path = directory / "analysis.json"
        if a_path.exists():
            a = read(a_path)
            a["publication"] = ref(pub_path)
            a["request"] = ref(req_path)
            a["sources"] = [ref(Path(r["path"])) for r in a["sources"]]
            write(a_path, a)

    def mutate(path, f):
        j = read(path)
        f(j)
        write(path, j)

    def reject(path, f, diagnostic, seal=True):
        restore()
        mutate(path, f)
        if seal:
            reseal()
        call([INSPECT, execution, "--kind", "execution", "--json"], 2, diagnostic)
        call([REPORT, "--target", execution, "--json"], 2, diagnostic)

    def reject_current(diagnostic, execution=execution):
        call([INSPECT, execution, "--kind", "execution", "--json"], 2, diagnostic)
        call([REPORT, "--target", execution, "--json"], 2, diagnostic)

    def rebind_checkpoint(execution=execution, directory=directory):
        current = read(execution)
        cp = Path(current["artifacts"][0]["path"])
        cm = read(cp / "manifest.json")
        cm["files"] = {name: file_record(cp / name) for name in cm["files"]}
        cm["external"] = read(cp / "closure.json")
        cm.pop("identity")
        cm["identity"] = hashlib.sha256(json.dumps(cm, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        write(cp / "manifest.json", cm)
        current["artifacts"][0]["identity"] = cm["identity"]
        write(execution, current)
        for path in (directory / "publication.json", directory / "request.json"):
            mutate(path, lambda j: j["checkpoint"].update(identity=cm["identity"]))
        pub = read(directory / "publication.json")
        reply_path = Path(pub["request"]["reply_path"])
        reply = read(reply_path)
        for name, artifact in reply["artifacts"].items():
            artifact.update(file_record(Path(artifact["path"])))
        write(reply_path, reply)
        mutate(directory / "publication.json", lambda j: j.update(reply=reply))

    # R1: all incidental identities are coherently rebound. The original frozen
    # manifest/plan alone supplies the expected member/copy bytes, never a renderer.
    m = read(execution.parent / "project/manifest.json")
    for member in (m["synthesis_sources"][0], "src/config.json", m["source_inputs"][0]["copy"],
                   "constraints/top.sdc", "constraints/pt7-native-coverage.tcl"):
        restore()
        path = execution.parent / "project" / member
        if member == "src/config.json":
            mutate(path, lambda j: j.update(CLOCK_PERIOD=123))
        else:
            path.write_bytes(path.read_bytes() + b"\n// changed retained input, original frozen hash remains\n")
        pub = read(directory / "publication.json")
        for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
            mutate(closure_path, lambda j: j.update({str(path): file_record(path)}))
        rebind_checkpoint()
        reseal()
        reject_current("frozen bundle member hash differs: " + member)
        if member == "src/config.json":
            (directory / "analysis.json").unlink()
            call([API, "publish", directory, "true"], 2, "frozen bundle member hash differs: " + member)
    restore()
    missing = execution.parent / "project/src/config.json"
    pub = read(directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.pop(str(missing)))
    rebind_checkpoint()
    reseal()
    reject_current("frozen bundle member missing/different in published input closure")
    # A truthful rebound unrelated collateral closure remains valid.
    restore()
    collateral = execution.parent.parent / "retained-collateral"
    old_collateral = collateral.read_bytes()
    collateral.write_text("new independently retained collateral")
    pub = read(directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update({str(collateral): file_record(collateral)}))
    rebind_checkpoint()
    reseal()
    truthful = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == truthful
    collateral.write_bytes(old_collateral)
    restore()

    # R4: the staged template has its own immutable target-reference authority,
    # distinct from bundle members and the checkpoint's rebound closure hashes.
    floor_reference = next(r for r in m["target"]["external_references"] if r["role"] == "Floorplan_def")
    floorplan = execution.parent / "project/tt" / floor_reference["path"]
    floor_bytes = floorplan.read_bytes()
    floorplan.write_bytes(floor_bytes + b"\nchanged pinned template\n")
    pub = read(directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update({str(floorplan): file_record(floorplan)}))
    rebind_checkpoint(); reseal()
    reject_current("frozen external pin differs from published closure")
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"], 2, "frozen external pin differs from published closure")
    restore()
    # Publisher must also compare actual bytes, not just a retained closure record
    # that still repeats the correct frozen pin. Inspection independently rejects
    # that unchanged closure versus coherently updated source inventory.
    floorplan.write_bytes(floor_bytes + b"\nchanged bytes, old closure pin left intact\n")
    reseal()
    reject_current("changed external dependency: " + str(floorplan))
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"], 2, "frozen external pin differs from retained bytes")
    restore()
    # Correct bytes at the same basename in another directory are not the exact
    # supported source locator. No fallback by role label, hash or basename.
    substitute = execution.parent / "project/tt/other" / floorplan.name
    substitute.parent.mkdir(parents=True)
    substitute.write_bytes(floor_bytes)
    pub = read(directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: (j.pop(str(floorplan)), j.update({str(substitute): file_record(substitute)})))
    mutate(directory / "analysis.json", lambda j: j["sources"].append(ref(substitute)))
    rebind_checkpoint(); reseal()
    reject_current("pinned frozen external locator missing from published closure")
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"], 2, "pinned frozen external locator missing from published closure")
    restore()

    def reference_case(label, references):
        folder = tmp / label
        folder.mkdir()
        query = folder / "authority.json"
        write(query, dict(plan=plan, references=references))
        changed_plan = call([API, "reference-plan", query])
        changed_bundle = folder / "bundle"
        shutil.copytree(bundle, changed_bundle)
        (changed_bundle / "manifest.json").write_text(changed_plan["bundle_manifest"])
        selected, selected_directory, selected_resolved = fixture(folder / "fixture", changed_bundle, changed_plan)
        return selected, selected_directory, selected_resolved

    references = m["target"]["external_references"]
    # Source/role locator validation and duplicate/conflicting immutable pins.
    for label, alter, diagnostic in (
        ("r4-duplicate", lambda refs: refs.append(copy.deepcopy(refs[0])), "duplicate frozen external reference locator"),
        ("r4-conflict", lambda refs: refs.append(dict(refs[0], sha256="0" * 64)), "conflicting frozen external pins"),
        ("r4-role", lambda refs: refs[0].update(role="(Technology_view Technology_lef)"), "unsupported frozen external source/role locator"),
        ("r4-source", lambda refs: refs[0].update(source="Unknown_source"), "unsupported frozen external source/role locator"),
    ):
        refs = copy.deepcopy(references)
        alter(refs)
        selected, selected_directory, selected_resolved = reference_case(label, refs)
        native_fixture(selected, selected_directory, selected_resolved)
        call([API, "publish", selected_directory, "true"], 2, diagnostic)
        # Make a complete hash-consistent companion purely as a negative record;
        # publication itself must refuse to create it. Preserve every source.
        pub = read(selected_directory / "publication.json")
        source_paths = {r["path"] for r in pub["sources"]}
        source_paths.update(str(p) for p in selected_directory.rglob("*") if p.is_file())
        source_paths.update(str(p) for p in (selected.parent / "project").rglob("*") if p.is_file())
        source_paths.add(read(selected)["methodology"]["snapshot_path"])
        # Include the genuine outside-workspace collateral source too.
        source_paths.update(read(Path(pub["checkpoint"]["path"]) / "closure.json"))
        source_paths = {p for p in source_paths if not p.startswith("/nix/store/")}
        write(selected_directory / "analysis.json", dict(schema_version=1, kind="hardcaml-asic.named-timing-analysis",
              expectation=pub["expectation"], publication=ref(selected_directory / "publication.json"),
              request=ref(selected_directory / "request.json"), status="completed", failure=None,
              sources=[ref(Path(p)) for p in sorted(source_paths)]))
        reject_current(diagnostic, selected)

    # A genuinely unpinned frozen external can change coherently; no byte pin is
    # manufactured by inspecting today's target catalog or resolving new inputs.
    unpinned = copy.deepcopy(references)
    unpinned[0]["sha256"] = None
    selected, selected_directory, selected_resolved = reference_case("r4-unpinned", unpinned)
    staged = selected.parent / "project/tt" / unpinned[0]["path"]
    staged.write_text("truthful unpinned template update")
    pub = read(selected_directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update({str(staged): file_record(staged)}))
    rebind_checkpoint(selected, selected_directory); reseal(selected_directory)
    native_fixture(selected, selected_directory, selected_resolved)
    call([API, "publish", selected_directory, "true"])
    unpinned_view = call([INSPECT, selected, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", selected, "--json"])["timing_intent"] == unpinned_view

    # A pinned PDK Technology_view is looked up at its exact declared technology
    # locator; a legitimate alias still needs its original immutable alias proof.
    pdks = copy.deepcopy(references)
    pinned_pdk = next(r for r in pdks if r["source"] == "Pdk")
    pinned_data = b"pinned external PDK collateral\n"
    pinned_pdk["sha256"] = hashlib.sha256(pinned_data).hexdigest()
    selected, selected_directory, selected_resolved = reference_case("r4-pdk-alias", pdks)
    pdk_root = selected.parent.parent / "pdk"
    pinned_target = pdk_root / "canonical-pin.dat"
    pinned_target.parent.mkdir(parents=True, exist_ok=True)
    pinned_target.write_bytes(pinned_data)
    pdk_declared = pdk_root / m["target"]["technology"] / pinned_pdk["path"]
    pdk_declared.parent.mkdir(parents=True, exist_ok=True)
    pdk_declared.symlink_to(pinned_target)
    query = tmp / "r4-pdk-alias-query.json"
    write(query, dict(roots=[str(pdk_root)], paths=[str(pdk_declared)]))
    rows = call([API, "aliases", query])
    pub = read(selected_directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update({str(pdk_declared): file_record(pdk_declared)}))
    mutate(selected_directory / "publication.json", lambda j: (j.update(external_aliases=rows), j["sources"].append(ref(pdk_declared))))
    rebind_checkpoint(selected, selected_directory); reseal(selected_directory)
    native_fixture(selected, selected_directory, selected_resolved)
    call([API, "publish", selected_directory, "true"])
    pinned_view = call([INSPECT, selected, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", selected, "--json"])["timing_intent"] == pinned_view
    pinned_target.write_bytes(pinned_data + b"changed bytes with coherent outer rehash\n")
    pub = read(selected_directory / "publication.json")
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update({str(pdk_declared): file_record(pdk_declared)}))
    mutate(selected_directory / "publication.json", lambda j: j["external_aliases"][0].update(sha256=sha(pdk_declared), bytes=pdk_declared.stat().st_size))
    rebind_checkpoint(selected, selected_directory); reseal(selected_directory)
    reject_current("frozen external pin differs from published closure", selected)
    (selected_directory / "analysis.json").unlink()
    call([API, "publish", selected_directory, "true"], 2, "frozen external pin differs from published closure")

    # Optional explicit link to A's early durable preflight expectation. Original
    # publication schema1 without the field remains the baseline positive above.
    early = tmp / "early-timing-intent-expectation.json"
    write(early, read(execution)["timing_intent"])
    mutate(directory / "publication.json", lambda j: (j.update(timing_intent_snapshot=ref(early)), j["sources"].append(ref(early))))
    mutate(directory / "analysis.json", lambda j: j["sources"].append(ref(early)))
    reseal()
    linked_early = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == linked_early
    mutate(early, lambda j: j.update(profile="opensta-2.7-native-state-v1"))
    mutate(directory / "publication.json", lambda j: j.update(timing_intent_snapshot=ref(early)))
    reseal()
    reject_current("early timing expectation snapshot/final expectation differs")
    restore()

    # Explicit external declared->canonical association. Distinct declared aliases
    # charge/count separately even when their targets and bytes are identical.
    pdk = execution.parent.parent / "pdk"
    (pdk / "canonical").mkdir(parents=True, exist_ok=True)
    target = pdk / "canonical/cells.lib"
    twin = pdk / "canonical/twin.lib"
    data = b"same external collateral bytes\n"
    target.write_bytes(data); twin.write_bytes(data)
    support = execution.parent / "project/tt"
    support.mkdir(exist_ok=True)
    support_target = support / "support.dat"
    support_target.write_bytes(data)
    leaf = pdk / "declared.lib"
    other = pdk / "other-declared.lib"
    directory_alias = pdk / "directory-alias"
    support_alias = support / "declared-support.dat"

    def make_links():
        leaf.symlink_to("canonical/cells.lib")
        other.symlink_to("canonical/cells.lib")
        directory_alias.symlink_to("canonical", target_is_directory=True)
        support_alias.symlink_to("support.dat")

    make_links()
    declared = [leaf, other, directory_alias / "cells.lib", target, support_alias, support_target]
    roots = [str(pdk), str(support)]
    alias_query = tmp / "alias-query.json"
    write(alias_query, dict(roots=roots, paths=list(map(str, declared))))
    admitted = call([API, "precheck", alias_query])
    assert admitted["source_count"] == 6 and admitted["declared_alias_count"] == 4
    assert admitted["total_bytes"] == 6 * len(data) and admitted["hashes_verified"] is False
    rows = call([API, "aliases", alias_query])
    assert len(rows) == 4 and len({row["target"] for row in rows}) == 2
    pub = read(directory / "publication.json")
    records = {str(path): file_record(path) for path in declared}
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update(records))
    rebind_checkpoint()
    mutate(directory / "publication.json", lambda j: (j.update(external_aliases=rows), j["sources"].extend(ref(path) for path in declared)))
    reseal()
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"])
    alias_view = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == alias_view
    assert alias_view["association"]["external_aliases"] == rows
    assert set(map(str, declared)).issubset({r["path"] for r in call([API, "sources", execution])})
    # No checkpoint external key was canonicalized or replaced by a target.
    cp = Path(read(execution)["artifacts"][0]["path"])
    assert all(str(path) in read(cp / "closure.json") for path in declared)
    alias_frozen = {p: p.read_bytes() for p in execution.parent.rglob("*") if p.is_file() and not p.is_symlink()}

    def restore_alias():
        for path in (leaf, other, directory_alias, support_alias, pdk / "unlisted"):
            if path.is_symlink(): path.unlink()
        audit_path = directory / "corners" / resolved["DEFAULT_CORNER"] / "audit.json"
        if audit_path.is_symlink(): audit_path.unlink()
        restore()
        for path, raw in alias_frozen.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists(): path.chmod(0o600)
            path.write_bytes(raw)
        make_links()

    restore_alias()
    mutate(directory / "publication.json", lambda j: next(row for row in j["external_aliases"] if row["path"] == str(leaf)).update(target=str(twin)))
    reseal()
    reject_current("declared alias resolution differs from bound target")
    restore_alias()
    leaf.unlink(); leaf.symlink_to("canonical/twin.lib")
    reseal()  # identical bytes/hash, but a different target must still reject
    reject_current("declared alias resolution differs from bound target")
    restore_alias()
    outside = tmp / "outside-collateral.dat"
    outside.write_bytes(data)
    leaf.unlink(); leaf.symlink_to(outside)
    reseal()
    reject_current("alias target outside allowed canonical root")
    restore_alias()
    unlisted = pdk / "unlisted"
    unlisted.symlink_to("canonical/cells.lib")
    extra = dict(path=str(unlisted), target=str(target), root=str(pdk), sha256=sha(target), bytes=len(data))
    mutate(directory / "publication.json", lambda j: j["external_aliases"].append(extra))
    reseal()
    reject_current("alias is not an exact external closure key")
    restore_alias()
    mutate(directory / "publication.json", lambda j: j.pop("external_aliases"))
    reseal()
    reject_current("external alias missing bound association")
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"], 2, "external alias missing bound association")
    restore_alias()
    mutate(directory / "publication.json", lambda j: j["external_aliases"][0].update(root=str(pdk / "canonical")))
    reseal()
    reject_current("alias root differs from recorded collateral roots")
    restore_alias()
    mutate(directory / "publication.json", lambda j: j["external_aliases"].append(j["external_aliases"][0]))
    reseal()
    reject_current("duplicate alias declaration")
    restore_alias()
    audit = directory / "corners" / resolved["DEFAULT_CORNER"] / "audit.json"
    audit_copy = pdk / "audit-copy.json"
    audit_copy.write_bytes(audit.read_bytes())
    audit.unlink(); audit.symlink_to(audit_copy)
    reseal()
    reject_current("linked/noncanonical source")
    for path in (leaf, other, directory_alias, support_alias, pdk / "unlisted"):
        if path.is_symlink(): path.unlink()
    if audit.is_symlink(): audit.unlink()
    restore()

    # The actual conservative PDK shape fits the newly authorized named outer
    # bounds. Keep every required external, exercise publisher + both readers,
    # and hash the 67,485,696-byte source in streaming blocks.
    extra_dir = execution.parent / "conservative-pdk"
    extra_dir.mkdir()
    extra_paths = []
    for i in range(1485):
        path = extra_dir / f"collateral-{i}.dat"
        path.write_text(f"required collateral {i}\n")
        extra_paths.append(path)
    large = extra_dir / "largest-collateral.gds"
    with large.open("wb") as stream:
        stream.truncate(67_485_696)
    extra_paths.append(large)
    paths_file = tmp / "precheck-paths.json"
    write(paths_file, list(map(str, extra_paths)))
    admission = call([API, "precheck", paths_file])
    assert admission["source_count"] == 1486 and admission["largest_bytes"] == 67_485_696
    assert admission["limits"]["sources"] == 4096 and admission["limits"]["source_bytes"] == 128 * 1024 * 1024
    pub = read(directory / "publication.json")
    extra_records = {str(path): file_record(path) for path in extra_paths}
    for closure_path in (Path(pub["checkpoint"]["path"]) / "closure.json", Path(pub["request"]["candidate"]) / "closure.json"):
        mutate(closure_path, lambda j: j.update(extra_records))
    rebind_checkpoint()
    mutate(directory / "publication.json", lambda j: j["sources"].extend(ref(path) for path in extra_paths))
    reseal()
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"])
    enlarged = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == enlarged
    assert set(map(str, extra_paths)).issubset({source["path"] for source in enlarged["sources"]})
    assert call([API, "sources", execution]) == enlarged["sources"]
    # Exact count admission; source count is checked before attempting to read a
    # nonexistent 4097th entry. No source is silently discarded to fit the bound.
    for i in range(4096 - len(extra_paths)):
        path = extra_dir / f"empty-{i}.dat"
        path.touch()
        extra_paths.append(path)
    write(paths_file, list(map(str, extra_paths)))
    assert call([API, "precheck", paths_file])["source_count"] == 4096
    write(paths_file, list(map(str, extra_paths)) + [str(extra_dir / "does-not-exist")])
    call([API, "precheck", paths_file], 2, "source count exceeds 4096")
    restore()
    mutate(directory / "analysis.json", lambda j: j.update(sources=j["sources"] * (4097 // len(j["sources"]) + 1)))
    reject_current("source count exceeds 4096")
    restore()
    mutate(directory / "publication.json", lambda j: j.update(sources=j["sources"] * (4097 // len(j["sources"]) + 1)))
    (directory / "analysis.json").unlink()
    call([API, "publish", directory, "true"], 2, "source count exceeds 4096")
    restore()
    # Oversized individual/aggregate admissions reject before digest comparison.
    # Sparse files make these genuine byte-bound tests without huge allocations.
    for count, size, diagnostic in ((1, 128 * 1024 * 1024 + 1, "source exceeds 128MiB"),
                                    (5, 128 * 1024 * 1024, "sources exceed 512MiB")):
        restore()
        oversized = []
        for i in range(count):
            path = execution.parent / f"oversized-{i}"
            with path.open("wb") as stream:
                stream.truncate(size)
            oversized.append(dict(path=str(path), sha256="0" * 64))
        mutate(directory / "analysis.json", lambda j: j["sources"].extend(oversized))
        reject_current(diagnostic)
    restore()

    reject(directory / "publication.json", lambda j: j["expectation"].update(attempt_id="other"), "publication/expectation mismatch")
    reject(directory / "publication.json", lambda j: j["methodology"].update(plan_identity="0" * 64), "publication/final execution mismatch: methodology")
    reject(directory / "publication.json", lambda j: j["checkpoint"].update(identity="0" * 64), "publication/final checkpoint mismatch")
    reject(directory / "publication.json", lambda j: j["occurrence"].update(status="completed", exit_code=0), "publication must bind Running")
    reject(directory / "publication.json", lambda j: j["occurrence"].update(occurrence_id="other/implementation.synthesis"), "publication occurrence mismatch")
    reject(directory / "publication.json", lambda j: j["request"].update(operation="validate_publication"), "not validation")
    reject(directory / "request.json", lambda j: j.update(analysis_id="other/pre-pnr-sta"), "named request association differs: analysis_id")
    reject(directory / "request.json", lambda j: j.update(state_path="/wrong/state.json"), "named request association differs: state_path")
    reject(directory / "transport.json", lambda j: j["argv"].insert(6, "--cpus=20"), "transport argv differs")
    reject(directory / "transport-result.json", lambda j: j.update(cleanup_error="cleanup failed"), "transport success contradicts")
    reject(directory / "runtime-result.json", lambda j: j["synthesis"].update(state=j["synthesis"]["native_state"]), "linked synthesis differs from exact publication sources")
    reject(directory / "runtime-result.json", lambda j: j.update(executed_corners=["fast"]), "requested/executed corners mismatch")
    pub = read(directory / "publication.json")
    native_config = Path(pub["segment"]["run_dir"]) / "06-yosys-synthesis/config.json"
    reject(native_config, lambda j: j["meta"].update(step="Yosys.Other"), "named producer metadata/position mismatch")
    execute_launch = execution.parent / (pub["request"]["tag"] + "-transport.json")
    execute_terminal = execution.parent / (pub["request"]["tag"] + "-transport-result.json")
    # R3: correct cleanup proof, not just a null summary error. Earlier failed
    # removals are legal if the last attempt proves success/definitive absence.
    reject(execute_terminal, lambda j: j.update(cleanup=dict(error=None, attempts=[dict(
        process=process_record(23), diagnostic="owned removal failed") ])), "cleanup attempts contradict")
    reject(execute_terminal, lambda j: j.update(cleanup=dict(error=None, attempts=[])), "cleanup attempts missing")
    reject(execute_terminal, lambda j: j.update(cleanup=None), "requires owned cleanup evidence")
    reject(execute_terminal, lambda j: j.update(cleanup=dict(error=None, attempts=[dict(
        process=process_record(0, cleanup_error="supervisor cleanup failed"), diagnostic="") ])), "cleanup attempts contradict")
    contradictory_child = process_record()
    contradictory_child["child_status"]["code"] = 23
    reject(execute_terminal, lambda j: j.update(cleanup=dict(error=None, attempts=[dict(
        process=contradictory_child, diagnostic="") ])), "process/child status differs")
    for last in (dict(process=process_record(), diagnostic=""), dict(process=process_record(1), diagnostic="No such container: fixture")):
        restore()
        mutate(execute_terminal, lambda j: j.update(cleanup=dict(error=None, attempts=[
            dict(process=process_record(23), diagnostic="temporary removal failure"), last])))
        reseal()
        truthful = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
        assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == truthful
    reject(execute_launch, lambda j: j["resources"].update(cpus=20), "named execute resource policy differs")
    reject(execute_launch, lambda j: j.update(request_id="validation-other"), "named execute transport/request correlation differs")
    reject(execute_launch, lambda j: j["argv"].insert(6, "--cpus=20"), "named execute transport argv/resources differ")
    reject(directory / "analysis.json", lambda j: j["sources"].append(j["sources"][0]), "duplicate named source path")
    reject(execution, lambda j: j.pop("timing_intent"), "missing persisted expectation")
    reject(execution, lambda j: j["timing_intent"].update(profile="opensta-2.7-native-state-v1"), "expectation contract/attempt/occurrence/profile mismatch")
    restore()
    (directory / "analysis.json").unlink()
    call([INSPECT, execution, "--kind", "execution", "--json"], 2)
    call([REPORT, "--target", execution, "--json"], 2)
    restore()
    replacement = tmp / "replacement.json"
    value = read(execution)
    value["occurrences"][0]["duration_seconds"] += 1
    write(replacement, value)
    call([API, "snapshot-race", execution, replacement], 2, "changed from captured snapshot")
    restore()
    # Exact file target; no directory/latest/checkpoint discovery.
    call([INSPECT, execution.parent, "--kind", "execution", "--json"], 2)
    call([INSPECT, execution, "--kind", "coarse_run", "--json"], 2)
    # Genuine failed linked transport: partial native audits cannot confer coverage.
    mutate(execution, lambda j: (j.update(status="failed", exit_code=124), j["occurrences"][0].update(status="failed", exit_code=124, error="timeout")))
    failure = dict(kind="failed", exit_code=124, signal=None, error="timeout", secondary=[])
    mutate(directory / "transport-result.json", lambda j: j.update(succeeded=False, outcome="timed_out", code=None))
    mutate(directory / "analysis.json", lambda j: j.update(status="failed", failure=failure))
    reseal()
    failed = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
    assert failed["status"] == "failed" and "corners" not in failed
    assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == failed
    # R2: the same timeout/final occurrence cannot describe another transport.
    timeout_execution = execution.read_bytes()
    timeout_a = (directory / "analysis.json").read_bytes()
    for outcome, code in (("cancelled", 2), ("exited", 9), ("signaled", 15), ("failed", None)):
        write(execution, json.loads(timeout_execution))
        write(directory / "analysis.json", json.loads(timeout_a))
        mutate(directory / "transport-result.json", lambda j: j.update(succeeded=False, outcome=outcome, code=code))
        reseal()
        reject_current("primary failure contradicts analysis transport")
    # Truthful unsuccessful causes and successful-transport post-verification
    # failures stay readable and retain unavailable coverage.
    for outcome, code, status, exit_code, signal, cleanup_error in (
        ("cancelled", 2, "interrupted", 130, 2, None),
        ("cancelled", 15, "interrupted", 143, 15, None),
        ("exited", 9, "failed", 9, None, None),
        ("signaled", 15, "failed", 143, None, None),
        ("failed", None, "failed", 2, None, None),
        ("exited", 0, "failed", 2, None, "owned cleanup failed"),
        ("exited", 0, "failed", 2, None, None),
        ("exited", 0, "interrupted", 143, 15, None),
    ):
        restore()
        mutate(execution, lambda j: (j.update(status=status, exit_code=exit_code), j["occurrences"][0].update(
            status=status, exit_code=exit_code, error="truthful retained cause")))
        mutate(directory / "analysis.json", lambda j: j.update(status="failed", failure=dict(
            kind=status, exit_code=exit_code, signal=signal, error="truthful retained cause", secondary=[cleanup_error] if cleanup_error else [])))
        mutate(directory / "transport-result.json", lambda j: j.update(outcome=outcome, code=code,
            succeeded=outcome == "exited" and code == 0 and cleanup_error is None, cleanup_error=cleanup_error))
        reseal()
        truthful = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
        assert truthful["status"] == "failed" and "corners" not in truthful
        assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == truthful
    # R2S: primary truth alone cannot drop known supervision/owned cleanup causes.
    for outcome, code, status, exit_code, signal in (("exited",9,"failed",9,None),
                                                    ("cancelled",2,"interrupted",130,2),
                                                    ("exited",0,"failed",2,None)):
        restore()
        cleanup = "supervisor cleanup failed; owned removal failed"
        mutate(execution, lambda j: (j.update(status=status, exit_code=exit_code), j["occurrences"][0].update(
            status=status, exit_code=exit_code, error="truthful retained cause")))
        mutate(directory / "transport-result.json", lambda j: j.update(outcome=outcome, code=code, succeeded=False, cleanup_error=cleanup))
        mutate(directory / "analysis.json", lambda j: j.update(status="failed", failure=dict(
            kind=status, exit_code=exit_code, signal=signal, error="truthful retained cause",
            secondary=["additional storage observation", "supervisor cleanup failed", "owned removal failed", "later diagnostic"])))
        reseal()
        truthful = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
        assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == truthful
        for secondary in ([], ["unrelated observation warning"], ["supervisor cleanup failed"]):
            mutate(directory / "analysis.json", lambda j: j["failure"].update(secondary=secondary))
            reseal()
            reject_current("cleanup error missing from companion secondary causes")
    restore()
    # Supported pre-analysis failure/unreached: no publication, no native claims.
    for state in ("unreached", "failed", "interrupted"):
        restore()
        shutil.rmtree(directory)
        value = read(execution)
        value.update(status="interrupted" if state == "interrupted" else "failed", exit_code=130 if state == "interrupted" else 2, artifacts=[])
        value["occurrences"][0].update(status=state, exit_code=None if state == "unreached" else value["exit_code"],
                                       duration_seconds=0 if state == "unreached" else 1,
                                       error=None if state == "unreached" else "failed before publication")
        write(execution, value)
        result = call([INSPECT, execution, "--kind", "execution", "--json"], 1)
        assert result["status"] == "not_produced" and "corners" not in result
        assert call([REPORT, "--target", execution, "--json"])["timing_intent"] == result
        directory.mkdir(parents=True)
    restore()
    # Two independently valid retained attempts and an actual alternative frozen
    # profile provide substitution controls, rather than invented identity strings.
    for label in ("other-attempt", "alternative"):
        other_bundle = bundle
        other_plan = plan
        if label == "alternative":
            other_bundle = tmp / "alternative-bundle"
            other_plan = call([EMITTER, other_bundle, "alternative"])
            assert other_plan["plan_identity"] != plan["plan_identity"]
        other_execution, other_directory, other_resolved = fixture(tmp / label, other_bundle, other_plan, label)
        native_fixture(other_execution, other_directory, other_resolved)
        call([API, "publish", other_directory, "true"])
        other_result = call([INSPECT, other_execution, "--kind", "execution", "--json"], 1)
        assert other_result["status"] == "completed"
        other_methodology = read(other_execution)["methodology"]
        reject(directory / "publication.json", lambda j: j.update(methodology=other_methodology), "publication/final execution mismatch: methodology")
        other_checkpoint = read(other_execution)["artifacts"][0]
        reject(directory / "publication.json", lambda j: j.update(checkpoint=other_checkpoint), "publication/final checkpoint mismatch")
    restore()
    # Producer-free historical named records omit the additive report section.
    legacy_bundle = tmp / "legacy-bundle"
    legacy_plan = call([EMITTER, legacy_bundle, "legacy"])
    legacy_execution, legacy_directory, _ = fixture(tmp / "legacy", legacy_bundle, legacy_plan)
    legacy_value = read(legacy_execution)
    legacy_expectation = legacy_value.pop("timing_intent")
    write(legacy_execution, legacy_value)
    shutil.rmtree(legacy_directory)
    legacy = call([INSPECT, legacy_execution, "--kind", "execution", "--json"], 1)
    assert legacy["status"] == "unavailable"
    assert "timing_intent" not in call([REPORT, "--target", legacy_execution, "--json"])
    legacy_value["timing_intent"] = legacy_expectation
    write(legacy_execution, legacy_value)
    call([INSPECT, legacy_execution, "--kind", "execution", "--json"], 2, "advertised producer is missing")
    assert call([INSPECT, execution, "--kind", "execution", "--json"], 1) == baseline
    assert all(p.read_bytes() == raw for p, raw in frozen.items())
    print(f"Named timing execution evidence: {COUNT} public/API checks passed; modeled native output, no EDA")
