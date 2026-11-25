"""Provider orchestration and public retained-source replay, without EDA.

The real bundle emitter, runtime helper, schema-2 reader, collector/reporter and
archive selector run here. Only the LibreLane/native subprocess boundary is fake.
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
import tarfile
import tempfile
import types

ENGINE = len(sys.argv) == 3 and sys.argv[1] == "--engine"
if ENGINE:
    HELPER = Path(sys.argv[2]).resolve().parent / "timing_intent_runtime.py"
else:
    EMITTER, CONSUMER, CLI, ARCHIVE, HELPER = map(lambda x: Path(x).resolve(), sys.argv[1:])
checks = 0


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, separators=(",", ":")) + "\n")


def read(p):
    return json.loads(p.read_text())


def call(args, code=0, diagnostic=None):
    global checks
    p = subprocess.run(list(map(str, args)), text=True, capture_output=True, timeout=30,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
    if diagnostic is not None:
        assert diagnostic in p.stderr, (args, diagnostic, p.stderr)
    checks += 1
    return json.loads(p.stdout) if p.stdout.strip() else None


def fixture(root, bundle):
    shutil.copytree(bundle, root / "project")
    flow = root / "project/runs/asic"
    synth = flow / "06-yosys-synthesis"
    synth.mkdir(parents=True)
    (synth / "mapped.v").write_text("// Synthetic mapped netlist: no tool execution\n")
    write(synth / "config.json", dict(meta=dict(step="Yosys.Synthesis", librelane_version="3.1.0.dev3")))
    write(synth / "state_out.json", dict(nl=str(synth / "mapped.v"), metrics={
        "design__instance_unmapped__count": 0, "synthesis__check_error__count": 0,
        "design__inferred_latch__count": 0}))
    write(synth / "reports/stat.json", dict(design=dict(area=10, num_cells=2, num_cells_by_type={})))
    resolved = dict(DEFAULT_CORNER="nom_typ_1p20V_25C", STA_CORNERS=["fast", "nom_typ_1p20V_25C", "slow"],
                    STA_THREADS=6, OPENROAD_THREADS=6,
                    PNR_SDC_FILE=str(root / "project/constraints/top.sdc"),
                    STA_EXTRA_CORNER_TCL_FILE=None, STA_MACRO_PRIORITIZE_NL=True,
                    DESIGN_NAME="tt_um_pt7_flow_fixture", EXTRA_LIBS=None, EXTRA_VERILOG_MODELS=None)
    write(flow / "resolved.json", resolved)
    binding = call([CONSUMER, "binding"])
    synth_argv = call([CONSUMER, "argv", root, "synthesis"])
    manifest = read(root / "project/manifest.json")
    record = dict(schema_version=1, run_id="fixture-run", status="completed", requested_stage="synthesis",
                  timing_intent_expected=True,
                  completed_stage="synthesis", build_identity=manifest["identity"],
                  manifest_sha256=sha(root / "project/manifest.json"), execution_binding=binding,
                  synthesis_process=dict(outcome="exited", code=0, succeeded=True, argv=synth_argv), commands=[synth_argv],
                  outputs=dict(project="project", flow="project/runs/asic"), bundle=str(bundle), environment={})
    write(root / "run.json", record)
    out = root / "timing-intent"
    out.mkdir()
    shutil.copyfile(HELPER, out / "timing_intent_runtime.py")
    request = dict(schema=1, run_id=record["run_id"], build_identity=record["build_identity"],
                   manifest_sha256=record["manifest_sha256"], analysis_id="fixture-run", binding=binding,
                   pdk_root=str(root.parent / "pdk"), helper_sha256=sha(out / "timing_intent_runtime.py"),
                   execution_root=str(root), host_uid=os.getuid(), host_gid=os.getgid(),
                   synthesis_process=record["synthesis_process"],
                   producer_sha256=sha(root / "project/constraints/pt7-native-coverage.tcl"))
    write(out / "request.json", request)
    write(out / "transport.json", dict(request_sha256=sha(out / "request.json"), binding=binding,
          argv=call([CONSUMER, "argv", root, "analysis"]), workers=6, sta_threads=1, timeout_seconds=600))
    write(out / "transport-result.json", dict(request_sha256=sha(out / "request.json"), succeeded=True,
          outcome="exited", code=0, cleanup_error=None))
    return root, resolved


def runtime(root, resolved, mode="good"):
    spec = importlib.util.spec_from_file_location("pt7_runtime_test", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    library = root.parent / "cells.lib"
    library.write_text('library (fixture) { time_unit : "1ns"; }\n')
    other_library = root.parent / "other-cells.lib"
    other_library.write_text('library (other_fixture) { time_unit : "1ns"; }\n')
    corner_script = root.parent / "corner.tcl"
    corner_script.write_text("# native script fixture, not EDA\n")
    calls = []

    class FakeStep:
        @classmethod
        def get_all_config_variables(cls):
            return [types.SimpleNamespace(name=k) for k in resolved]

        @classmethod
        def load(cls, config, state_in, pdk_root):
            step = cls()
            step.config = read(Path(config))
            step.state = read(Path(state_in))
            assert step.config["STA_THREADS"] == step.config["OPENROAD_THREADS"] == 1
            assert step.config["STA_CORNERS"] == [resolved["DEFAULT_CORNER"]]
            assert step.state["nl"] == str(root / "project/runs/asic/06-yosys-synthesis/mapped.v")
            return step

        def get_script_path(self):
            return str(corner_script)

        def _get_corner_files(self, corner, prioritize_nl):
            return corner, types.SimpleNamespace(libs=[str(library), str(other_library)], netlists=[], spefs=[],
                    current_corner_spef=None, extra_spefs_backcompat=None)

        def start(self, toolbox, step_dir):
            step_dir = Path(step_dir)
            step_dir.mkdir()
            write(step_dir / "config.json", self.config)
            write(step_dir / "state_in.json", self.state)
            if mode == "unexecuted":
                return
            corner = resolved["DEFAULT_CORNER"]
            d = step_dir / corner
            d.mkdir()
            env = dict(CURRENT_NL=self.state["nl"], _CURRENT_CORNER_NAME=corner,
                       STA_EXTRA_CORNER_TCL_FILE=self.config["STA_EXTRA_CORNER_TCL_FILE"])
            self.run_corner(self.state, env, corner, str(d))
            write(step_dir / "state_out.json", self.state)
            (step_dir / "runtime.txt").write_text("00:00:00.010")

        def run_corner(self, state, env, corner, corner_dir):
            command = self.get_command()
            assert command[:6] == ["sta", "-threads", "1", "-no_splash", "-exit", str(root / "timing-intent/driver.tcl")]
            d = Path(corner_dir)
            assert env["CURRENT_NL"] == str(d / "mapped.v")
            assert env["_SDC_IN"] == str(d / "top.sdc")
            assert env["_CURRENT_CORNER_LIBS"] == " ".join(helper.tcl_word(str(d / f"library-{i}.lib")) for i in range(2))
            assert env["PT7_CORNER_SCRIPT"] == str(d / "librelane-corner.tcl")
            receipt = read(d / "receipt.json")
            event = lambda event_name, **fields: dict(schema_version=1, event=event_name, **fields)
            counts = dict(stage_count=2, exception_count=1)
            scope = dict(id="capture", kind="async_capture", source="ena", targets=["u0/D"])
            stages = [event("stage", id=f"rx{i}", anchor=f"hc_pt7_rx{i}", cell=f"u{i}",
                            master="sg13cmos5l_dfrbpq_1", q_pin=f"u{i}/Q", data_pin=f"u{i}/D", async_pin=f"u{i}/RESET_B")
                      for i in range(2)]
            events = [event("begin", **counts), *stages, event("resolved", **scope), event("validated", **counts),
                      event("applied", **scope), event("complete", **counts)]
            log = "PT7_AUDIT_RECEIPT " + json.dumps(receipt, separators=(",", ":")) + "\n"
            log += "".join("HC_ASIC_TIMING_V1\t" + json.dumps(e, separators=(",", ":")) + "\n" for e in events)
            log += "HC_ASIC_NATIVE_V2_COMPLETE\nPT7_AUDIT_COMPLETE\n"
            (d / "sta.log").write_text(log)
            if mode == "later_failure":
                raise subprocess.CalledProcessError(9, command)
            (d / "native-state.sdc").write_text("current_design " + self.config["DESIGN_NAME"] + "\nset_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]\n")
            write(d / "native-data.json", dict(schema=2, contract="hardcaml-asic.timing-native-coverage",
                  profile="opensta-2.7-native-state-v2", complete=True,
                  categories=[dict(name=n, group_count=0, groups=[]) for n in
                  ("no_input_delay", "no_output_delay", "multiple_clock", "no_clock", "unconstrained_endpoints", "loops", "generated_clocks")],
                  endpoint_count=2, endpoints=["u0/D", "u1/D"]))
            (d / "native-checks.rpt").write_text("HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2\n" +
                "".join(f"HC_ASIC_NATIVE_CATEGORY_BEGIN {name}\nHC_ASIC_NATIVE_CATEGORY_END {name}\n" for name in
                ("no_input_delay", "no_output_delay", "multiple_clock", "no_clock", "unconstrained_endpoints", "loops", "generated_clocks")) +
                "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n")
            (d / "native-endpoints.rpt").write_text('"u0/D"\n"u1/D"\nHC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n')
            for name in ("downstream_min.rpt", "downstream_max.rpt"):
                (d / name).write_text("slack -0.044244 VIOLATED (synthetic witness)\n")
            calls.append(command)
            return {}

    modules = {"librelane": types.ModuleType("librelane"), "librelane.steps": types.ModuleType("librelane.steps"),
               "librelane.steps.openroad": types.SimpleNamespace(STAPrePNR=FakeStep),
               "librelane.steps.tclstep": types.SimpleNamespace(TclStep=types.SimpleNamespace(value_to_tcl=lambda xs: " ".join(helper.tcl_word(str(x)) for x in xs))),
               "librelane.common": types.SimpleNamespace(Toolbox=lambda path: path)}
    previous = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    original = subprocess.run
    def version(args, **kwargs):
        assert args == ["sta", "-version"]
        return types.SimpleNamespace(stdout="2.7.0\n", stderr="", returncode=0)
    try:
        subprocess.run = version
        helper.execute(root / "timing-intent/request.json")
    finally:
        subprocess.run = original
        for k, value in previous.items():
            if value is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = value
    assert len(calls) == 1


if ENGINE:
    engine_root = Path(sys.argv[2]).resolve().parent.parent
    runtime(engine_root, read(engine_root / "project/runs/asic/resolved.json"), os.environ.get("PT7_TEST_MODE", "good"))
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="linked timing ordinary [labels] $root ") as tmp:
    tmp = Path(tmp)
    bundle = tmp / "bundle"
    call([EMITTER, bundle])
    root, resolved = fixture(tmp / "run", bundle)
    synthesis_before = {p: sha(p) for p in (root / "project/runs/asic").rglob("*") if p.is_file()}
    runtime(root, resolved)
    assert all(sha(p) == h for p, h in synthesis_before.items())
    call([CONSUMER, "publish", root, "true"])
    baseline = call([CONSUMER, "inspect", root])
    assert baseline["status"] == "completed" and baseline["verdict"] == "unavailable"
    assert baseline["corners"][0]["native_coverage"]["status"] == "interpreted_supported_dimensions"
    assert call([CLI, "timing-intent-inspect", root, "--kind", "coarse_run", "--json"], 1) == baseline
    assert call([CLI, "--version", "--json"])["cli_contract"]["capabilities"]["timing_intent_flow1"]["named_execution"] is False
    collected = call([CONSUMER, "collect", root])
    assert collected["timing_intent"] == baseline
    write(root / "results.json", collected)
    assert call([CONSUMER, "report", root])["timing_intent"] == baseline
    members = call([CONSUMER, "archive", root])
    assert {r["path"] for r in baseline["sources"]}.issubset(members)
    archive = tmp / "archive"
    call([CLI, "archive", root, "--output", archive, "--json"])
    # Restore actual encoded source closure at a different root. No current PDK needed.
    restored = tmp / "restored"
    restored.mkdir()
    shutil.copyfile(archive / "run.json", restored / "run.json")
    with tarfile.open(archive / "reports.tar.gz") as stream:
        stream.extractall(restored, filter="data")
    unpack = tmp / "unpack"
    unpack.mkdir()
    with tarfile.open(archive / "input-bundle.tar.gz") as stream:
        stream.extractall(unpack, filter="data")
    shutil.copytree(unpack / "bundle", restored / "project", dirs_exist_ok=True)
    (tmp / "cells.lib").unlink()
    (tmp / "other-cells.lib").unlink()
    assert call([CONSUMER, "inspect", restored]) == baseline
    protected = {p: sha(p) for p in root.rglob("*") if p.is_file()}
    call([CONSUMER, "inspect", root])
    assert all(sha(p) == h for p, h in protected.items())

    def negative(label, mutate, reseal=False, expected="inspect", diagnostic=None, success=True):
        case = tmp / label
        shutil.copytree(root, case)
        mutate(case)
        if reseal:
            (case / "timing-intent/analysis.json").unlink()
            call([CONSUMER, "publish", case, "true" if success else "false"])
        call([CONSUMER, expected, case], 2, diagnostic=diagnostic)
        return case

    def change(p, field, value):
        j = read(p)
        j[field] = value
        write(p, j)

    negative("tampered-native", lambda p: (p / "timing-intent/corners/nom_typ_1p20V_25C/native-state.sdc").write_text("changed\n"))
    negative("run-id", lambda p: change(p / "run.json", "run_id", "different"))
    negative("request-id", lambda p: change(p / "timing-intent/request.json", "run_id", "different"), True)
    negative("missing-expected", lambda p: (p / "timing-intent/analysis.json").unlink())
    negative("successful-hook-failed-process", lambda p: change(p / "timing-intent/transport-result.json", "code", 9), True)
    def startup_provenance(p, mode):
        directory = p / "timing-intent/corners/nom_typ_1p20V_25C"
        log = (directory / "sta.log").read_text()
        wire = (directory / "resolution.jsonl").read_text()
        if mode == "late_receipt":
            receipt_line, rest = log.split("\n", 1)
            assert rest.startswith(wire)
            log = wire + receipt_line + "\n" + rest[len(wire):]
        elif mode == "double_sdc":
            log = log.replace(wire, wire + wire, 1)
            (directory / "resolution.jsonl").write_text(wire + wire)
        else:
            # Faithful diagnostic from the failed real startup, coherently
            # hash-bound even though the retained process assertion says exit 0.
            log = 'Error: driver.tcl, 1 can\'t read "::env(PT7_RECEIPT)": no such variable\n'
        (directory / "sta.log").write_text(log)
        audit = read(directory / "audit.json")
        for source in audit["sources"]:
            if source["role"] in ("log", "resolution"):
                source["sha256"] = sha(directory / source["path"])
        write(directory / "audit.json", audit)
        result = read(p / "timing-intent/runtime-result.json")
        result["audits"][0]["audit"]["sha256"] = sha(directory / "audit.json")
        write(p / "timing-intent/runtime-result.json", result)
    negative("receipt-after-resolution", lambda p: startup_provenance(p, "late_receipt"), True,
             diagnostic="missing/reordered producer receipt/completion")
    negative("sdc-loaded-twice", lambda p: startup_provenance(p, "double_sdc"), True,
             diagnostic="extra/repeated timing audit block")
    negative("bootstrap-error-zero-exit", lambda p: startup_provenance(p, "zero_exit"), True,
             diagnostic="retained STA error")
    negative("changed-library", lambda p: (p / "timing-intent/corners/nom_typ_1p20V_25C/library-0.lib").write_text("changed"), expected="archive")
    negative("symlink", lambda p: ((p / "timing-intent/driver.tcl").unlink(), (p / "timing-intent/driver.tcl").symlink_to(root / "timing-intent/driver.tcl")))
    negative("duplicate-key", lambda p: (p / "timing-intent/analysis.json").write_text('{"schema":1,"schema":1}'))
    negative("unexecuted-corner", lambda p: change(p / "timing-intent/runtime-result.json", "executed_corners", []), True)
    def config_delta(p):
        change(p / "timing-intent/effective-config.json", "PNR_SDC_FILE", "/different")
        h = read(p / "timing-intent/runtime-result.json")
        h["effective_config"]["sha256"] = sha(p / "timing-intent/effective-config.json")
        write(p / "timing-intent/runtime-result.json", h)
    negative("config-delta", config_delta, True)
    negative("synthesis-process", lambda p: change(p / "run.json", "synthesis_process", dict(outcome="exited", code=9, succeeded=False)))
    def wrong_image(p):
        for file, key in (("run.json", "execution_binding"), ("timing-intent/request.json", "binding"), ("timing-intent/transport.json", "binding")):
            j = read(p / file)
            j[key]["image_identity"]["image_id"] = "sha256:" + "0" * 64
            write(p / file, j)
        digest = sha(p / "timing-intent/request.json")
        for file in ("timing-intent/transport.json", "timing-intent/transport-result.json"):
            change(p / file, "request_sha256", digest)
    negative("coherent-wrong-image", wrong_image, True)
    negative("wrong-corner", lambda p: change(p / "timing-intent/runtime-result.json", "requested_corners", ["different"]), True)

    # Independent-review repros: retain all other assertions and reseal every
    # affected hash, so failures exercise semantic association rather than hashes.
    corner_relative = "timing-intent/corners/nom_typ_1p20V_25C"
    def native_inputs_change(p, key, value):
        change(p / corner_relative / "native-inputs.json", key, value)
    def relink_inner(p):
        directory = p / corner_relative
        audit = read(directory / "audit.json")
        for source in audit["sources"]:
            source["sha256"] = sha(directory / source["path"])
        write(directory / "audit.json", audit)
        result = read(p / "timing-intent/runtime-result.json")
        result["audits"][0]["audit"]["sha256"] = sha(directory / "audit.json")
        write(p / "timing-intent/runtime-result.json", result)
    lib_paths = [a["actual_path"] for a in read(root / corner_relative / "native-inputs.json")["libraries"]]
    for label, raw in (("unrelated", "/outside/unrelated.lib"), ("empty", ""),
                       ("missing", "{" + lib_paths[0] + "}"),
                       ("reordered", " ".join("{" + p + "}" for p in reversed(lib_paths))),
                       ("duplicate", " ".join("{" + lib_paths[0] + "}" for _ in lib_paths))):
        negative("B-R1-liberty-" + label, lambda p, raw=raw: native_inputs_change(p, "_CURRENT_CORNER_LIBS", raw), True,
                 diagnostic="actual native Liberty list differs")
    negative("B-R1-missing-associations", lambda p: native_inputs_change(p, "libraries", []), True,
             diagnostic="actual Liberty association is missing/incomplete")
    def swap_associations(p):
        j = read(p / corner_relative / "native-inputs.json")
        j["libraries"].reverse()
        j["_CURRENT_CORNER_LIBS"] = " ".join("{" + a["actual_path"] + "}" for a in j["libraries"])
        write(p / corner_relative / "native-inputs.json", j)
    negative("B-R1-coherent-reordered-associations", swap_associations, True,
             diagnostic="actual Liberty association differs from inner audit")
    sentinel = tmp / "must-not-be-executed"
    negative("B-R1-no-Tcl-eval", lambda p: native_inputs_change(p, "_CURRENT_CORNER_LIBS", "[exec touch " + str(sentinel) + "]"), True,
             diagnostic="actual native Liberty list differs")
    assert not sentinel.exists()
    quoted = tmp / "B-R1-equivalent-list-and-restored-root"
    shutil.copytree(root, quoted)
    native_inputs_change(quoted, "_CURRENT_CORNER_LIBS", " \t".join("{" + p + "}" for p in lib_paths))
    (quoted / "timing-intent/analysis.json").unlink()
    call([CONSUMER, "publish", quoted, "true"])
    assert call([CONSUMER, "inspect", quoted])["status"] == "completed"
    # Rootful historical UID/GID and a relocated current directory remain valid;
    # replay must use the recorded execution labels, not the current user's IDs.
    rootful = tmp / "B-R2-rootful-historical-restore"
    shutil.copytree(root, rootful)
    r = read(rootful / "run.json"); q = read(rootful / "timing-intent/request.json")
    launch = read(rootful / "timing-intent/transport.json")
    for b in (r["execution_binding"], q["binding"], launch["binding"]):
        b["engine_identity"]["rootless"] = False
    def rootful_argv(args):
        i = args.index("--cpus=6") + 1
        return args[:i] + ["--user", "4242:4343"] + args[i:]
    r["synthesis_process"]["argv"] = rootful_argv(r["synthesis_process"]["argv"])
    r["commands"] = [r["synthesis_process"]["argv"]]
    q.update(synthesis_process=r["synthesis_process"], host_uid=4242, host_gid=4343)
    launch["argv"] = rootful_argv(launch["argv"])
    write(rootful / "run.json", r); write(rootful / "timing-intent/request.json", q)
    launch["request_sha256"] = sha(rootful / "timing-intent/request.json")
    write(rootful / "timing-intent/transport.json", launch)
    change(rootful / "timing-intent/transport-result.json", "request_sha256", launch["request_sha256"])
    (rootful / "timing-intent/analysis.json").unlink()
    call([CONSUMER, "publish", rootful, "true"])
    assert call([CONSUMER, "inspect", rootful])["status"] == "completed"

    def launched_wrong_root(p):
        j = read(p / "timing-intent/transport.json")
        j["argv"][-2:] = ["/another/run/timing-intent/timing_intent_runtime.py", "/another/run/timing-intent/request.json"]
        write(p / "timing-intent/transport.json", j)
    negative("B-R2-helper-request-root", launched_wrong_root, True,
             diagnostic="transport argv differs from qualified")
    def wrong_driver(p):
        j = read(p / corner_relative / "process-result.json")
        j["argv"][-1] = "/another/run/timing-intent/driver.tcl"
        write(p / corner_relative / "process-result.json", j)
        relink_inner(p)
    negative("B-R2-coherent-driver-root", wrong_driver, True,
             diagnostic="native process driver differs from retained historical source")
    def unrelated_hook(p):
        for relative in ("timing-intent/effective-config.json", "timing-intent/corners/config.json", corner_relative + "/native-inputs.json"):
            change(p / relative, "STA_EXTRA_CORNER_TCL_FILE", "/outside/unrelated-hook.tcl")
        delta = read(p / "timing-intent/configuration-delta.json")
        delta["STA_EXTRA_CORNER_TCL_FILE"]["effective"] = "/outside/unrelated-hook.tcl"
        write(p / "timing-intent/configuration-delta.json", delta)
        h = read(p / "timing-intent/runtime-result.json")
        h["effective_config"]["sha256"] = sha(p / "timing-intent/effective-config.json")
        write(p / "timing-intent/runtime-result.json", h)
    negative("B-R2-coherent-unretained-hook", unrelated_hook, True,
             diagnostic="executed hook path differs from retained source")
    negative("B-R2-unrelated-corner-script", lambda p: native_inputs_change(p, "PT7_CORNER_SCRIPT", "/outside/unrelated.tcl"), True,
             diagnostic="executed corner script differs from retained source")
    negative("B-R2-different-corner-root", lambda p: native_inputs_change(p, "PT7_AUDIT_DIR", "/another/run/" + corner_relative), True,
             diagnostic="actual native input/corner receipt differs")

    downgrade = tmp / "B-R3-archive-downgrade"
    shutil.copytree(root, downgrade)
    m = read(downgrade / "project/manifest.json")
    m.pop("timing_native_coverage")
    m["files"] = [r for r in m["files"] if r["path"] != "constraints/pt7-native-coverage.tcl"]
    write(downgrade / "project/manifest.json", m)
    change(downgrade / "run.json", "manifest_sha256", sha(downgrade / "project/manifest.json"))
    (downgrade / "results.json").unlink()  # Avoid an incidental stored resource-source mismatch.
    for operation in ("inspect", "archive"):
        call([CONSUMER, operation, downgrade], 2, diagnostic="new run's advertised producer declaration is missing")
    rejected_archive = tmp / "B-R3-rejected-archive"
    call([CLI, "archive", downgrade, "--output", rejected_archive, "--json"], 2,
         diagnostic="new run's advertised producer declaration is missing")
    assert not rejected_archive.exists()
    assert call([CONSUMER, "collect", downgrade])["timing_intent"]["status"] == "invalid"
    call([CONSUMER, "report", downgrade], 2, diagnostic="bundle manifest payload identity or resource snapshot invalid")

    negative("B-R4-cleanup-contradiction", lambda p: change(p / "timing-intent/transport-result.json", "cleanup_error", "owned container removal failed"), True,
             diagnostic="transport success contradicts process/cleanup status")
    def malformed_failure(p):
        j = read(p / "timing-intent/transport-result.json")
        j.update(succeeded="not-a-boolean", outcome="invented", code="not-an-exit-code")
        write(p / "timing-intent/transport-result.json", j)
    negative("B-R4-failed-malformed", malformed_failure, True, success=False,
             diagnostic="invalid transport success Boolean")
    for label, updates in (("outcome", dict(succeeded=False, outcome="invented", code=None)),
                           ("timeout-code", dict(succeeded=False, outcome="timed_out", code=0)),
                           ("extra-field", dict(unrecorded_result=True))):
        def corrupt_terminal(p, updates=updates):
            j = read(p / "timing-intent/transport-result.json"); j.update(updates)
            write(p / "timing-intent/transport-result.json", j)
        negative("B-R4-failed-" + label, corrupt_terminal, True, success=False,
                 diagnostic="unexpected receipt fields" if label == "extra-field" else "invalid transport outcome/code")
    cleanup_failed = tmp / "B-R4-valid-cleanup-failure"
    shutil.copytree(root, cleanup_failed)
    j = read(cleanup_failed / "timing-intent/transport-result.json")
    j.update(succeeded=False, cleanup_error="owned container removal failed")
    write(cleanup_failed / "timing-intent/transport-result.json", j)
    (cleanup_failed / "timing-intent/analysis.json").unlink()
    call([CONSUMER, "publish", cleanup_failed, "false"])
    failed_view = call([CONSUMER, "inspect", cleanup_failed])
    assert failed_view["status"] == "failed" and failed_view["verdict"] == "unavailable" and "corners" not in failed_view

    for label, mutation in (("two-token-CPU-20", lambda a: a[:7] + ["--cpus", "20"] + a[7:]),
                            ("duplicate-CPU-6", lambda a: a[:7] + ["--cpus=6"] + a[7:]),
                            ("overridden-worker-env", lambda a: a[:-2] + ["-e", "OMP_NUM_THREADS=20"] + a[-2:]),
                            ("wrong-entrypoint", lambda a: ["sh" if i and a[i-1] == "--entrypoint" else x for i, x in enumerate(a)])):
        def resource_override(p, mutation=mutation):
            j = read(p / "timing-intent/transport.json"); j["argv"] = mutation(j["argv"])
            write(p / "timing-intent/transport.json", j)
        negative("B-R5-" + label, resource_override, True, diagnostic="transport argv differs from qualified")
    # JSON object order is presentation, including observed nested runtime identity.
    equivalent = tmp / "object-order"
    shutil.copytree(root, equivalent)
    r = read(equivalent / "run.json")
    r["execution_binding"] = dict(reversed(list(r["execution_binding"].items())))
    write(equivalent / "run.json", r)
    assert call([CONSUMER, "inspect", equivalent]) == baseline
    invalid = tmp / "tampered-native"
    assert call([CONSUMER, "collect", invalid])["timing_intent"]["status"] == "invalid"
    assert call([CONSUMER, "report", invalid])["timing_intent"]["status"] == "invalid"
    missing_manifest = tmp / "missing-manifest"
    shutil.copytree(root, missing_manifest)
    (missing_manifest / "project/manifest.json").unlink()
    assert call([CONSUMER, "collect", missing_manifest])["timing_intent"]["status"] == "invalid"

    for mode in ("later_failure", "unexecuted"):
        case, config = fixture(tmp / mode, bundle)
        try:
            runtime(case, config, mode)
            raise AssertionError("accepted failed native boundary")
        except (ValueError, subprocess.CalledProcessError):
            pass
        assert not (case / "timing-intent/runtime-result.json").exists()
        change(case / "timing-intent/transport-result.json", "succeeded", False)
        change(case / "timing-intent/transport-result.json", "code", 9)
        call([CONSUMER, "publish", case, "false"])
        assert call([CONSUMER, "inspect", case])["status"] == "failed"
    legacy = tmp / "legacy"
    shutil.copytree(root, legacy)
    m = read(legacy / "project/manifest.json")
    m.pop("timing_native_coverage")
    m["files"] = [r for r in m["files"] if r["path"] != "constraints/pt7-native-coverage.tcl"]
    write(legacy / "project/manifest.json", m)
    change(legacy / "run.json", "manifest_sha256", sha(legacy / "project/manifest.json"))
    change(legacy / "run.json", "timing_intent_expected", False)
    assert call([CONSUMER, "inspect", legacy])["status"] == "unavailable"

    # Actual Execution_transport supervision/immutable-image argv, backed by a
    # fake Docker executable. No daemon and no real native tool is contacted.
    docker = tmp / "docker"
    docker.write_text(f'''#!{sys.executable}
import os, signal, sys, time
if sys.argv[1] == "rm":
    if os.environ.get("PT7_TEST_MODE") == "cleanup":
        print("fixture owned removal failed", file=sys.stderr); sys.exit(9)
    sys.exit(0)
assert sys.argv[1] == "run" and "--cpus=6" in sys.argv
assert "--pull=never" in sys.argv
if os.environ.get("PT7_TEST_MODE") == "timeout": time.sleep(5)
if os.environ.get("PT7_TEST_MODE") == "signaled": os.kill(os.getpid(), signal.SIGTERM)
os.execv(sys.executable, [sys.executable, "-B", {str(Path(__file__).resolve())!r}, "--engine", sys.argv[-1]])
''')
    docker.chmod(0o755)
    for mode, code in (("good", 0), ("later_failure", 2), ("timeout", 124), ("cancel", 130), ("cleanup", 2), ("signaled", 2), ("post_verify", 2)):
        case, _ = fixture(tmp / ("transport-" + mode), bundle)
        capture_record = read(case / "run.json")
        capture_record["synthesis_process"]["argv"][0] = str(docker)
        capture_record["commands"] = [capture_record["synthesis_process"]["argv"]]
        write(case / "run.json", capture_record)
        shutil.rmtree(case / "timing-intent")
        os.environ["PT7_TEST_MODE"] = mode
        call([CONSUMER, "capture", case, docker, HELPER.parent, "0.1" if mode == "timeout" else "10", mode], code)
        view = call([CONSUMER, "inspect", case])
        assert view["status"] == ("completed" if mode == "good" else "failed")
        terminal = read(case / "timing-intent/transport-result.json")
        assert terminal["outcome"] == {"good": "exited", "later_failure": "exited", "timeout": "timed_out", "cancel": "cancelled", "cleanup": "exited", "signaled": "signaled", "post_verify": "exited"}[mode]
        if mode == "cleanup":
            assert terminal["succeeded"] is False and terminal["code"] == 0 and terminal["cleanup_error"]
        if mode == "signaled":
            assert terminal["succeeded"] is False and terminal["code"] == 15
        if mode == "post_verify":
            assert terminal["succeeded"] is True and terminal["code"] == 0 and terminal["cleanup_error"] is None
            assert "corners" not in view and view["verdict"] == "unavailable"
    os.environ.pop("PT7_TEST_MODE", None)

    # Fresh actual public coarse CLI orchestration with all external tools fake.
    # This checks automatic opt-in and six-worker limits from preflight onward.
    fake_bin = tmp / "ordinary-bin"
    fake_bin.mkdir()
    tools, pdk = tmp / "tools", tmp / "pdk"
    tools.mkdir(); pdk.mkdir()
    ordinary_bundle = tmp / "ordinary-bundle"
    shutil.copytree(bundle, ordinary_bundle)
    manifest = read(ordinary_bundle / "manifest.json")
    qualified_binding = call([CONSUMER, "binding"])
    for r in manifest["target"]["external_references"]:
        path = (tools if r["source"] == "Support_tools" else pdk / manifest["target"]["technology"]) / r["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture collateral\n")
        r["sha256"] = None
    write(ordinary_bundle / "manifest.json", manifest)
    write(ordinary_bundle / "manifest.json", call([CONSUMER, "seal", ordinary_bundle / "manifest.json"]))
    fake_source = f'''#!{sys.executable}
import json, os, pathlib, sys
a = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
fixture = pathlib.Path({str(tmp)!r})
manifest = json.loads(pathlib.Path({str(ordinary_bundle / 'manifest.json')!r}).read_text())
with (fixture / "ordinary-calls.jsonl").open("a") as f: f.write(json.dumps([name, a]) + "\\n")
if name == "git":
    if "rev-parse" in a:
        print(manifest["requested_tools"]["support_tools_revision" if a[1] == {str(tools)!r} else "pdk_revision"])
    sys.exit(0)
if name == "docker":
    if a[0] == "info":
        print(json.dumps(dict(ServerVersion="29.0", SecurityOptions=["rootless"], ID="daemon", Name="fixture"))); sys.exit(0)
    if a[:2] == ["image", "inspect"]:
        image = {qualified_binding['image_identity']['image_id']!r}
        print(image if "--format" in a else json.dumps([dict(Id=image, RepoDigests=[], Os="linux", Architecture="amd64")])); sys.exit(0)
    if a[0] == "rm": sys.exit(0)
    assert a[0] == "run" and "--cpus=6" in a and "--pull=never" in a
    if "-m" in a and a[a.index("-m")+1] == "librelane":
        assert a[a.index("--to")+1] == "Yosys.Synthesis"
        assert a[a.index("--jobs")+1] == "6" and "STA_THREADS=1" in a and "OPENROAD_THREADS=6" in a
        project = pathlib.Path.cwd(); flow = project / "runs/asic"; synth = flow / "06-yosys-synthesis"
        synth.mkdir(parents=True); (synth / "reports").mkdir()
        (synth / "mapped.v").write_text("// synthetic normal CLI synthesis\\n")
        (synth / "config.json").write_text(json.dumps(dict(meta=dict(step="Yosys.Synthesis", librelane_version="3.1.0.dev3"))))
        metrics = dict(design__instance_unmapped__count=0, synthesis__check_error__count=0, design__inferred_latch__count=0)
        (synth / "state_out.json").write_text(json.dumps(dict(nl=str(synth / "mapped.v"), metrics=metrics)))
        (synth / "reports/stat.json").write_text(json.dumps(dict(design=dict(area=10, num_cells=2, num_cells_by_type={{}}))))
        resolved = dict(DEFAULT_CORNER="nom_typ_1p20V_25C", STA_CORNERS=["fast", "nom_typ_1p20V_25C", "slow"],
            STA_THREADS=1, OPENROAD_THREADS=6, PNR_SDC_FILE=str(project / "constraints/top.sdc"),
            STA_EXTRA_CORNER_TCL_FILE=None, STA_MACRO_PRIORITIZE_NL=True,
            DESIGN_NAME=manifest["top_module"], EXTRA_LIBS=None, EXTRA_VERILOG_MODELS=None)
        (flow / "resolved.json").write_text(json.dumps(resolved)); sys.exit(0)
    if a[-1].endswith("/timing-intent/request.json"):
        os.execv(sys.executable, [sys.executable, "-B", {str(Path(__file__).resolve())!r}, "--engine", a[-1]])
host = name != "docker"
code = a[a.index("-c")+1]
value = dict(python_version="3.11.9" if host else "3.13.1", librelane_version="3.1.0.dev3", requires_python=">=3.10", metadata_error=None)
if host: value["tcl"] = dict(ready=True, version="8.6", error=None)
if "stage_runtime_metadata" in code:
    value = dict(protocol_version=1, kind="stage_runtime_metadata", role="host" if host else "container", metadata=value)
print(json.dumps(value))
'''
    for name in ("python", "git", "docker"):
        executable = fake_bin / name
        executable.write_text(fake_source)
        executable.chmod(0o755)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = str(fake_bin)
    try:
        outcome = call([CLI, "run", ordinary_bundle, "--stage", "synthesis", "--support-tools", tools,
                        "--pdk-root", pdk, "--python", fake_bin / "python", "--runs", tmp / "ordinary-runs", "--json"])
        assert outcome["status"] == "completed"
        ordinary = Path(outcome["run_dir"])
        record = read(ordinary / "run.json")
        assert record["resource_limits"]["tool_workers"] == 6
        assert call([CLI, "timing-intent-inspect", ordinary, "--kind", "coarse_run", "--json"], 1)["status"] == "completed"
        old_profile = tmp / "unsupported-native-v1-bundle"
        shutil.copytree(ordinary_bundle, old_profile)
        old_manifest = read(old_profile / "manifest.json")
        old_manifest["timing_native_coverage"].update(schema=1, profile="opensta-2.7-native-state-v1")
        write(old_profile / "manifest.json", old_manifest)
        write(old_profile / "manifest.json", call([CONSUMER, "seal", old_profile / "manifest.json"]))
        before_tools = (tmp / "ordinary-calls.jsonl").read_bytes()
        before_runs = set((tmp / "ordinary-runs").iterdir())
        call([CLI, "run", old_profile, "--stage", "synthesis", "--support-tools", tools,
              "--pdk-root", pdk, "--python", fake_bin / "python", "--runs", tmp / "ordinary-runs", "--json"],
             2, diagnostic="unsupported native producer profile")
        assert (tmp / "ordinary-calls.jsonl").read_bytes() == before_tools
        assert set((tmp / "ordinary-runs").iterdir()) == before_runs
    finally:
        os.environ["PATH"] = old_path

print(f"Linked timing flow: {checks} public/orchestration/replay checks passed; synthetic native boundary, no EDA")
