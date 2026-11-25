"""Bounded coarse-synthesis linked STA. Invoked only by the native provider.

No Flow.start, project elaboration, generated-SDC modification, or checkpoint
publication. Native imports are delayed so orchestration can be fixture tested.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

PRODUCER = "constraints/pt7-native-coverage.tcl"
DRIVER = r'''if {[catch {
    # TclStep reroutes changed non-allowlisted variables into this file. Load
    # assignments before reading PT7 context; the normal corner script alone
    # owns linking and the single SDC load. Its own env bootstrap is harmless.
    source $::env(_TCL_ENV_IN)
    set f [open $::env(PT7_RECEIPT) r]
    set receipt [read $f]
    close $f
    puts "PT7_AUDIT_RECEIPT [string trim $receipt]"
    source $::env(PT7_CORNER_SCRIPT)
} message options]} {
    puts stderr "PT7_ERROR $message"
    exit 1
}
puts "PT7_AUDIT_COMPLETE"
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, separators=(",", ":"), allow_nan=False)
        stream.write("\n")


def digest(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "regular source required: " + str(path))
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def reference(root, path):
    path = Path(path)
    relative = path.relative_to(root)
    require(path.resolve() == path, "noncanonical source: " + str(path))
    return dict(path=str(relative), sha256=digest(path))


def copy(source, target, aliases=None):
    source_digest = digest if aliases is None else lambda path: named_digest(path, aliases)
    before = source_digest(source)
    require(not target.exists(), "fresh retained copy required")
    shutil.copyfile(source, target)
    require(source_digest(source) == digest(target) == before, "input changed while copying")


def named_aliases(request, publication):
    """B's named external_aliases wire. Metadata never inherits alias permission."""
    roots = [request["pdk_root"], str(Path(request["project"]) / "tt")]
    rows = publication.get("external_aliases", [])
    require(isinstance(rows, list) and len(rows) <= 4096, "invalid named external alias table")
    checkpoint_manifest = Path(request["checkpoint"]["path"]) / "manifest.json"
    named_digest(checkpoint_manifest, {})
    checkpoint = load(checkpoint_manifest)
    external = checkpoint.get("external", {})
    source_paths = {source["path"] for source in publication["sources"]}
    aliases = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "target", "root", "sha256", "bytes"},
                "unexpected named external alias fields")
        for key in ("path", "target", "root"):
            value = row[key]
            require(isinstance(value, str) and os.path.isabs(value) and os.path.normpath(value) == value and
                    "\0" not in value, "noncanonical named alias path spelling")
        require(row["path"] not in aliases and row["path"] != row["target"], "duplicate/non-alias declaration")
        root = Path(row["root"])
        require(row["root"] in roots and str(root) != "/" and str(root.resolve(strict=True)) == str(root) and root.is_dir(),
                "named alias outside recorded collateral roots")
        require(Path(row["path"]).is_relative_to(root) and Path(row["target"]).is_relative_to(root),
                "named alias escapes allowed collateral root")
        require(row["path"] in source_paths and row["path"] in external, "alias missing exact source/external key")
        expected = external[row["path"]]
        require(isinstance(row["bytes"], int) and not isinstance(row["bytes"], bool) and
                0 <= row["bytes"] <= 128 * 1024 * 1024 and
                expected["sha256"] == row["sha256"] and int(expected["bytes"]) == row["bytes"],
                "named alias hash/bytes differ from exact external closure key")
        aliases[row["path"]] = row
    for path in external:
        if not path.startswith("/nix/store/") and str(Path(path).resolve(strict=True)) != path:
            require(path in aliases, "external alias missing bound association")
    for path in aliases:
        named_digest(path, aliases)
    return aliases


def named_digest(path, aliases):
    """Hash original declared key, pin canonical resolution before/after bounded IO."""
    path = Path(path)
    key = str(path)
    resolved = str(path.resolve(strict=True))
    row = aliases.get(key)
    require(resolved == (row["target"] if row is not None else key),
            "named source alias resolution differs from bound target")
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= 128 * 1024 * 1024, "named source kind/size")
    if row is not None:
        require(before.st_size == row["bytes"], "named alias byte count differs")
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        require(signature(os.fstat(stream.fileno())) == signature(before), "named source changed before hashing")
        remaining = before.st_size
        while remaining:
            block = stream.read(min(65536, remaining))
            require(bool(block), "named source truncated while hashing")
            h.update(block)
            remaining -= len(block)
        require(signature(os.fstat(stream.fileno())) == signature(before), "named source changed while hashing")
    require(str(path.resolve(strict=True)) == resolved and signature(path.stat()) == signature(before),
            "named source alias changed after hashing")
    value = h.hexdigest()
    if row is not None:
        require(value == row["sha256"], "named alias hash differs")
    return value


def tcl_word(value):
    return '"' + ''.join({'\\': '\\\\', '"': '\\"', '$': '\\$', '[': '\\[',
                          ']': '\\]', '\n': '\\n', '\r': '\\r'}.get(c, c) for c in value) + '"'


def select_synthesis(root):
    flow = root / "project/runs/asic"
    found = []
    for directory in sorted(flow.iterdir()):
        if not directory.is_dir() or directory.name == "final":
            continue
        config = directory / "config.json"
        if config.is_file() and load(config).get("meta", {}).get("step") == "Yosys.Synthesis":
            found.append(directory)
    require(len(found) == 1, "missing/ambiguous exact synthesis producer")
    directory = found[0]
    state_path = directory / "state_out.json"
    state = load(state_path)
    netlist = Path(state["nl"])
    require(netlist.is_absolute() and netlist.is_relative_to(flow), "synthesis netlist outside selected run")
    refs = {name: reference(root, path) for name, path in
            (("config", directory / "config.json"), ("state", state_path),
             ("netlist", netlist), ("resolved", flow / "resolved.json"))}
    refs["state_netlist"] = state["nl"]
    return refs, state_path, flow / "resolved.json"


def select_named_synthesis(root, request, aliases=None):
    """Explicit publication input, exact originating call; never latest-run lookup."""
    publication_ref = request["publication"]
    require(digest(publication_ref["path"]) == publication_ref["sha256"], "publication changed")
    publication = load(publication_ref["path"])
    require(publication["expectation"] == request["expectation"], "publication expectation mismatch")
    if aliases is None:
        aliases = named_aliases(request, publication)
    require(len(publication["sources"]) <= 4096, "named publication source count exceeds 4096")
    seen, total = set(), 0
    for source in publication["sources"]:
        path = Path(source["path"])
        require(str(path) not in seen, "duplicate named publication source")
        seen.add(str(path))
        size = path.stat().st_size
        require(size <= 128 * 1024 * 1024, "named publication source exceeds 128MiB")
        total += size
        require(total <= 512 * 1024 * 1024, "named publication sources exceed 512MiB")
        require(named_digest(source["path"], aliases) == source["sha256"], "publication source changed")
    backend = publication["request"]
    require(backend["operation"] == "execute_segment" and backend["boundary"] == "synthesis" and
            backend["checkpoint"] is None, "fresh synthesis execute call required")
    flow = root / "project/src/runs" / backend["tag"]
    require(str(flow) == request["synthesis_run_dir"] == publication["segment"]["run_dir"], "originating run mismatch")
    require(publication["checkpoint"] == request["checkpoint"] and
            publication["checkpoint"]["boundary"] == "synthesis", "publication mismatch")
    checkpoint = Path(request["checkpoint"]["path"])
    require(request["state_path"] == str(checkpoint / "state.json") and
            request["resolved_path"] == str(checkpoint / "resolved.json"), "published input paths required")
    found = []
    for directory in flow.iterdir():
        config = directory / "config.json"
        if directory.is_dir() and config.is_file() and load(config).get("meta", {}).get("step") == "Yosys.Synthesis":
            found.append(directory)
    require(len(found) == 1, "missing/ambiguous exact synthesis producer")
    producer = found[0]
    steps = publication["segment"]["returned_steps"]
    require(steps.count("Yosys.Synthesis") == 1 and steps[-1] == "Yosys.Synthesis" and
            producer.name.split("-", 1)[0].isdigit() and
            int(producer.name.split("-", 1)[0]) == len(steps), "synthesis returned-step position mismatch")
    require(load(producer / "config.json")["meta"].get("librelane_version") == "3.1.0.dev3", "unsupported synthesis version")
    state_path = checkpoint / "state.json"
    resolved_path = checkpoint / "resolved.json"
    state = load(state_path)
    netlist = Path(state["nl"])
    require(netlist.is_relative_to(checkpoint / "views"), "netlist outside publication views")
    manifest = load(checkpoint / "manifest.json")
    require(manifest["identity"] == request["checkpoint"]["identity"], "checkpoint identity mismatch")
    relative = str(netlist.relative_to(checkpoint))
    require(manifest["files"][relative]["sha256"] == digest(netlist), "netlist outside checkpoint inventory")
    candidate_state = Path(backend["candidate"]) / "state.json"
    native_state = producer / "state_out.json"
    require(digest(Path(load(candidate_state)["nl"])) == digest(Path(load(native_state)["nl"])) == digest(netlist),
            "native/candidate/publication netlist mismatch")
    require(load(candidate_state) == load(native_state), "native/candidate state mismatch")
    refs = {name: reference(root, path) for name, path in
            (("config", producer / "config.json"), ("state", state_path),
             ("netlist", netlist), ("resolved", resolved_path),
             ("native_state", native_state), ("candidate_state", candidate_state))}
    refs["state_netlist"] = state["nl"]
    return refs, state_path, resolved_path


def hook(manifest):
    lines = ["source [file join $::env(PT7_AUDIT_DIR) native-producer.tcl]",
             "::hc_pt7_native::write $::env(PT7_AUDIT_DIR)"]
    stages = manifest["timing_intent"]["stages"]
    # Witness queries only; no guessed mapped cell names or added constraints.
    for mode in ("min", "max"):
        filename = "downstream_" + mode + ".rpt"
        lines += [f'set f [open [file join $::env(PT7_AUDIT_DIR) {filename}] w]',
                  'puts $f "Bounded declared downstream timing witnesses"', 'close $f']
        for stage in stages:
            if stage["index"] == 0:
                continue
            previous = [s for s in stages if s["chain"] == stage["chain"] and s["index"] == stage["index"] - 1]
            require(len(previous) == 1, "invalid chain witness inventory")
            lines += [
                'set q [::hc_pt7::one [::hc_pt7::outputs [::hc_pt7::exact get_nets ' + tcl_word(previous[0]["anchor"]) + ']] witness-Q]',
                'set next_q [::hc_pt7::one [::hc_pt7::outputs [::hc_pt7::exact get_nets ' + tcl_word(stage["anchor"]) + ']] witness-next-Q]',
                'set cell [get_full_name [::hc_pt7::one [get_cells -of_objects $next_q] witness-cell]]',
                'set d [::hc_pt7::pin $cell D input]',
                f'report_checks -from $q -to $d -path_delay {mode} -format full_clock_expanded -digits 6 >> [file join $::env(PT7_AUDIT_DIR) {filename}]']
    return "\n".join(lines) + "\n"


def execute(request_path):
    request = load(request_path)
    named = request.get("kind") == "hardcaml-asic.named-timing-request"
    root = Path(request["execution_root"]) if named else Path(request_path).resolve().parent.parent
    require(request["execution_root"] == str(root), "request execution root differs from actual run")
    out = Path(request["analysis_directory"]) if named else root / "timing-intent"
    require(request_path.resolve() == out / "request.json" and out.is_relative_to(root), "analysis request path mismatch")
    project = root / "project"
    manifest = load(project / "manifest.json")
    require(request["manifest_sha256"] == digest(project / "manifest.json") and
            request["build_identity"] == manifest["identity"], "request/build mismatch")
    require(request["producer_sha256"] == digest(project / PRODUCER), "producer changed")
    require(not manifest.get("macro_contract"), "Linked timing analysis currently supports macro-free synthesis only")
    if named:
        require(named_digest(request["publication"]["path"], {}) == request["publication"]["sha256"], "publication changed")
    publication = load(request["publication"]["path"]) if named else None
    aliases = named_aliases(request, publication) if named else None
    synthesis, state_path, resolved_path = select_named_synthesis(root, request, aliases) if named else select_synthesis(root)
    resolved = load(resolved_path)
    corner = resolved["DEFAULT_CORNER"]
    require(isinstance(corner, str) and corner and Path(corner).name == corner and corner not in (".", ".."), "invalid default corner")
    require(resolved["PNR_SDC_FILE"] == str(project / "constraints/top.sdc"), "resolved SDC differs from emitted SDC")
    require(not resolved.get("EXTRA_LIBS") and not resolved.get("EXTRA_VERILOG_MODELS"), "extra linked models are outside bounded linked timing route")
    effective = dict(resolved)
    effective.update(STA_CORNERS=[corner], STA_THREADS=1, OPENROAD_THREADS=1,
                     STA_EXTRA_CORNER_TCL_FILE=str(out / "hook.tcl"))
    # Step.load expects its own config inventory. Filtering is not PDK resolution;
    # values are deserialized from this run's complete resolved configuration.
    from librelane.steps.openroad import STAPrePNR
    from librelane.common import Toolbox
    if named:
        from concurrent.futures import ThreadPoolExecutor
        from librelane.common import set_tpe
        set_tpe(ThreadPoolExecutor(max_workers=1))
    keys = {v.name for v in STAPrePNR.get_all_config_variables()}
    effective = {k: v for k, v in effective.items() if k in keys or k == "meta"}
    effective["meta"] = dict(step="OpenROAD.STAPrePNR", librelane_version="3.1.0.dev3")
    write(out / "effective-config.json", effective)
    write(out / "configuration-delta.json", {k: dict(requested=resolved.get(k), effective=effective[k])
          for k in ("STA_CORNERS", "STA_THREADS", "OPENROAD_THREADS", "STA_EXTRA_CORNER_TCL_FILE")})
    (out / "hook.tcl").write_text(hook(manifest))
    (out / "driver.tcl").write_text(DRIVER)
    audits = []

    class LinkedSTA(STAPrePNR):
        # This is supplementary analysis, not substitution into Classic.Steps.
        id = "OpenROAD.STAPrePNR"

        def get_script_path(self):
            return str(out / "driver.tcl")

        def get_command(self):
            return ["sta", "-threads", "1", "-no_splash", "-exit", self.get_script_path()]

        def run_corner(self, state_in, current_env, actual_corner, corner_dir):
            require(actual_corner == corner and not audits, "unexpected/duplicate actual corner")
            directory = Path(corner_dir)
            _, files = self._get_corner_files(actual_corner, prioritize_nl=self.config["STA_MACRO_PRIORITIZE_NL"])
            require(not files.netlists and not files.spefs and not files.current_corner_spef and
                    not files.extra_spefs_backcompat, "unexpected hierarchical/parasitic analysis input")
            input_sources = []
            def retain(role, source, name):
                target = directory / name
                copy(Path(source), target, aliases=aliases)
                input_sources.append(dict(role=role, path=name, sha256=digest(target)))
            retain("manifest", project / "manifest.json", "manifest.json")
            retain("sdc", project / "constraints/top.sdc", "top.sdc")
            retain("netlist", root / synthesis["netlist"]["path"], "mapped.v")
            for i, library in enumerate(files.libs):
                retain("liberty", library, f"library-{i}.lib")
            require(bool(files.libs), "no actual linked libraries")
            retain("driver", out / "driver.tcl", "driver.tcl")
            retain("native_producer", project / PRODUCER, "native-producer.tcl")
            version = subprocess.run(["sta", "-version"], text=True, capture_output=True, timeout=20, check=True)
            require(version.stdout.strip() and not version.stderr.strip(), "unexpected native version output")
            require(version.stdout.strip() == "2.7.0", "unsupported native OpenSTA version for linked timing profile")
            (directory / "producer-version.txt").write_text(version.stdout)
            write(directory / "version-process.json", dict(argv=["sta", "-version"], exit_code=version.returncode,
                  stdout_sha256=digest(directory / "producer-version.txt"), stderr=version.stderr))
            producer = dict(tool="OpenSTA", version=version.stdout.strip())
            receipt = dict(schema=1, build_identity=request["build_identity"], audit_id=request["analysis_id"],
                           stage="synthesized_netlist", producer=producer, corner=actual_corner, inputs=input_sources)
            write(directory / "receipt.json", receipt)
            original_script = STAPrePNR.get_script_path(self)
            retain_script = directory / "librelane-corner.tcl"
            copy(Path(original_script), retain_script)
            current_env.update(PT7_RECEIPT=str(directory / "receipt.json"),
                               PT7_CORNER_SCRIPT=str(retain_script), PT7_AUDIT_DIR=str(directory),
                               _CURRENT_CORNER_NAME=actual_corner)
            # Execute the retained copies of native inputs, not mutable aliases.
            current_env["CURRENT_NL"] = str(directory / "mapped.v")
            from librelane.steps.tclstep import TclStep
            current_env["_CURRENT_CORNER_LIBS"] = TclStep.value_to_tcl(tuple(str(directory / f"library-{i}.lib") for i in range(len(files.libs))))
            current_env["_SDC_IN"] = str(directory / "top.sdc")
            # Keep explicit environment evidence, not the ambient environment.
            native_inputs = {k: current_env[k] for k in
                  ("CURRENT_NL", "_CURRENT_CORNER_LIBS", "_SDC_IN", "_CURRENT_CORNER_NAME",
                   "STA_EXTRA_CORNER_TCL_FILE", "PT7_RECEIPT", "PT7_CORNER_SCRIPT", "PT7_AUDIT_DIR") if k in current_env}
            # Ordered/multiplicity-preserving association to the exact copies
            # actually supplied to read_liberty. Original PDK aliases need not
            # exist during read-only archive replay.
            native_inputs["libraries"] = [dict(actual_path=str(directory / source["path"]),
                retained=reference(root, directory / source["path"]))
                for source in input_sources if source["role"] == "liberty"]
            native_inputs["corner_script"] = reference(root, retain_script)
            write(directory / "native-inputs.json", native_inputs)
            command = self.get_command()
            exit_code = None
            try:
                result = super().run_corner(state_in, current_env, actual_corner, corner_dir)
                exit_code = 0
            except subprocess.CalledProcessError as exc:
                exit_code = exc.returncode
                raise
            finally:
                write(directory / "process-result.json", dict(schema=1, receipt_sha256=digest(directory / "receipt.json"),
                                                             exit_code=exit_code, argv=command))
            # No complete audit can be published until the actual native process
            # returned successfully, including all standard reports after the hook.
            raw = (directory / "sta.log").read_text()
            (directory / "resolution.jsonl").write_text("".join(line + "\n" for line in raw.splitlines()
                                                               if line.startswith("HC_ASIC_TIMING_V1\t")))
            copy(directory / "native-state.sdc", directory / "exceptions.rpt")
            copy(directory / "native-checks.rpt", directory / "unconstrained.rpt")
            sources = input_sources + [dict(role=role, path=name, sha256=digest(directory / name)) for role, name in
                (("receipt", "receipt.json"), ("log", "sta.log"), ("resolution", "resolution.jsonl"),
                 ("exceptions", "exceptions.rpt"), ("unconstrained", "unconstrained.rpt"),
                 ("downstream_min", "downstream_min.rpt"), ("downstream_max", "downstream_max.rpt"),
                 ("process_result", "process-result.json"), ("producer_version", "producer-version.txt"),
                 ("native_sdc", "native-state.sdc"), ("native_data", "native-data.json"),
                 ("native_checks", "native-checks.rpt"), ("native_endpoints", "native-endpoints.rpt"))]
            write(directory / "audit.json", dict(schema=2, kind="hardcaml-asic.timing-intent-audit",
                  scope="standalone_synthesized_netlist_sta", build_identity=request["build_identity"],
                  audit_id=request["analysis_id"], stage="synthesized_netlist", producer=producer,
                  corner=actual_corner, sources=sources))
            audits.append(dict(corner=actual_corner, audit=reference(root, directory / "audit.json")))
            return result

    step = LinkedSTA.load(config=str(out / "effective-config.json"), state_in=str(state_path), pdk_root=request["pdk_root"])
    step.start(toolbox=Toolbox(str(out / "toolbox")), step_dir=str(out / "corners"))
    require(len(audits) == 1, "configured corner was not executed")
    if named:
        require(digest(request["publication"]["path"]) == request["publication"]["sha256"], "publication changed during native step")
        require(named_aliases(request, publication) == aliases, "named alias associations changed during native step")
        for source in publication["sources"]:
            require(named_digest(source["path"], aliases) == source["sha256"], "publication source changed during native step")
    for name in (("config", "state", "netlist", "resolved", "native_state", "candidate_state") if named else
                 ("config", "state", "netlist", "resolved")):
        r = synthesis[name]
        require(digest(root / r["path"]) == r["sha256"], "synthesis source changed during linked STA")
    identity = dict(kind="hardcaml-asic.named-timing-result", attempt_id=request["attempt_id"],
                    occurrence_id=request["occurrence_id"]) if named else dict(run_id=request["run_id"])
    write(out / "runtime-result.json", dict(schema=1, status="completed", **identity,
          build_identity=request["build_identity"], analysis_id=request["analysis_id"], synthesis=synthesis,
          effective_config=reference(root, out / "effective-config.json"),
          requested_corners=[corner], executed_corners=[a["corner"] for a in audits], audits=audits))


if __name__ == "__main__":
    require(len(sys.argv) == 2, "one provider request required")
    execute(Path(sys.argv[1]))
