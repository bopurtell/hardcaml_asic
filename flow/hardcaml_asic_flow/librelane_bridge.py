"""Explicitly transported isolated-runtime bridge; official Classic APIs only.

No host imports, step hooks or alternate sequencing engine. The
host owns the process and publication. This module emits a candidate only after
Flow.start (including final snapshot and deferred checks) returns successfully.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import types
import importlib.util

from librelane.common import Path as ToolPath
from librelane.flows import Flow
from librelane.state import State


ENDPOINTS = {"synthesis": "Yosys.Synthesis", "floorplan": "OpenROAD.Floorplan",
             "placement": "OpenROAD.DetailedPlacement", "cts": "OpenROAD.CTS",
             "routing": "OpenROAD.DetailedRouting", "timing": "OpenROAD.STAPostPNR",
             "signoff": "Misc.ReportManufacturability"}
SUCCESSORS = {"synthesis": "Checker.YosysUnmappedCells", "floorplan": "OpenROAD.DumpRCValues",
              "placement": "OpenROAD.CTS", "cts": "OpenROAD.STAMidPNR-1",
              "routing": "Odb.RemoveRoutingObstructions", "timing": "OpenROAD.IRDropReport"}
RESTART_TARGETS = {"synthesis": ("floorplan", "placement"), "floorplan": ("placement",),
                   "placement": ("cts",), "cts": ("routing",), "routing": ("timing",),
                   "timing": ("signoff",)}

# This literal is part of the bridge digest used by checkpoint compatibility.
# Load only these verified bytes, including under Python -I (no sys.path edits).
PROTOCOL_SHA256 = "eb6490db856f551ffe2ab35ea872bdc85c86801339e67a9d5bdf427238b25ae1"
MACRO_PROFILE = "stock256x16_r0_through_timing_v1"
PHYSICAL_RUNTIME_SHA256 = "20b4288ce4ecb89577454edcaa489583fd8eeaed14f663888b59c9eba7fa9048"


def protocol_module():
    support = Path(__file__).with_name("backend_protocol.py")
    source = support.read_bytes()
    if hashlib.sha256(source).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("transported backend protocol hash mismatch")
    module = types.ModuleType("backend_protocol")
    exec(compile(source, str(support), "exec"), module.__dict__)
    return module


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def record(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"missing declared dependency: {path}")
    return {"sha256": digest(path), "bytes": path.stat().st_size}


def metrics(raw):
    result = {}
    for key, value in raw.items():
        # Backend sentinels stay raw in state.json. No finite surrogate or zero.
        try:
            finite = not isinstance(value, (str, bool)) and math.isfinite(value)
        except (TypeError, ValueError, OverflowError):
            finite = False
        result[key] = ({"availability": "available", "value": float(value)} if finite else
                       {"availability": "unavailable", "reason": "nonfinite_or_nonnumeric",
                        "raw": str(value)})
    return result


def closure(config, project, pdk_root):
    """Conservative declared closure, not a minimal artifact cache.

    Inventory typed config paths recursively (including corners/lists), all
    bundle inputs and support references, and the entire target PDK tree to cover
    indirect Tcl/collateral reads. Runtime-only files are tied to the exact image.
     The bounded macro profile includes its transported helper bytes. Produced
     PDN evidence is deliberately separate from this stable input closure.
    """
    validate_macro_bundle(project)
    paths = set()

    def tree(path, ancestors=()):
        path = Path(path)
        resolved = path.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError(f"cyclic dependency directory: {path}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                tree(child, (*ancestors, resolved))
        else:
            paths.add(str(path.absolute()))

    def visit(value):
        if isinstance(value, ToolPath):
            tree(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for key, value in config.items():
        if key not in ("DESIGN_DIR", "PDK_ROOT"):
            visit(value)
    manifest_path = project / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    paths.add(str(manifest_path))
    for entry in manifest["files"]:
        tree(project / entry["path"])
    for ref in manifest["target"]["external_references"]:
        if ref["source"] == "Support_tools":
            tree(project / "tt" / ref["path"])
    tree(pdk_root / manifest["target"]["technology"])
    if manifest.get("macro_contract", {}).get("execution") == "fresh_full":
        tree(pdk_root / "ihp-sg13g2/libs.tech/klayout/tech/drc")
        tree(project / "bridge/physical_runtime.py")
    return {p: record(p) for p in sorted(paths)}


def validate_macro_bundle(project):
    """Validate the raw public bundle before LibreLane expands paths/defaults.

    This admits no custom macros. Execution/transition admission is separately
    bounded by macro_runtime and schema3. Supplying CDL is an identified
    dependency, not a transistor-LVS claim.
    """
    manifest = json.loads((project / "manifest.json").read_text())
    raw = json.loads((project / "src/config.json").read_text())
    contract = manifest.get("macro_contract")
    if contract is None:
        if raw.get("MACROS"):
            raise ValueError("MACROS require a public macro contract")
        return False
    name = "RM_IHPSG13_1P_256x16_c2_bm_bist"
    prefix = "pdk_dir::libs.ref/sg13cmos5l_sram/"
    def path(directory, suffix):
        return prefix + directory + "/" + name + suffix
    corners = {"nom_fast_1p32V_m40C": "fast_1p32V_m55C",
               "nom_slow_1p08V_125C": "slow_1p08V_125C",
               "nom_typ_1p20V_25C": "typ_1p20V_25C"}
    physical = contract.get("schema_version") == 2 and contract.get("execution") == "fresh_full"
    if ((not physical and (contract.get("schema_version") != 1 or contract.get("execution") != "synthesis_only"))
            or manifest.get("operation") != ("Hardening" if physical else "Synthesis")
            or raw != manifest.get("flow_configuration")
            or raw.get("PDK") != "ihp-sg13cmos5l"
            or raw.get("STA_CORNERS") != list(corners)
            or raw.get("DEFAULT_CORNER") != "nom_typ_1p20V_25C"
            or set(raw.get("MACROS", {})) != {name}):
        raise ValueError("unsupported SRAM synthesis bundle contract")
    stub = "macros/" + name + ".blackbox.v"
    if (raw.get("EXTRA_VERILOG_MODELS") != ["dir::../" + stub]
            or contract.get("blackbox_sources") != [stub]):
        raise ValueError("SRAM requires its generated synthesis stub")
    macro = raw["MACROS"][name]
    for key, expected in {"lef": [path("lef", ".lef")],
                          "gds": [path("gds", ".gds")],
                          "spice": [path("cdl", ".cdl")],
                          "lib": {c: [path("lib", "_" + suffix + ".lib")]
                                  for c, suffix in corners.items()}}.items():
        if macro.get(key) != expected:
            raise ValueError("incorrect exact SRAM collateral: " + key)
    entries = contract.get("macros", [])
    if not entries or any(m.get("module") != name for m in entries):
        raise ValueError("invalid SRAM resource inventory")
    instances = [m["instance"] for m in entries]
    if len(set(instances)) != len(instances):
        raise ValueError("duplicate SRAM RTL instance")
    # Resource records are independently emitted from elaboration. Compare their
    # structured sexps, not whitespace-stripped strings or the contract itself.
    def sexp(source):
        if not isinstance(source, str):
            raise ValueError("invalid resource inventory sexp")
        offset = 0
        def parse(depth=0):
            nonlocal offset
            if depth > 64:
                raise ValueError("resource inventory sexp too deep")
            while offset < len(source) and source[offset].isspace():
                offset += 1
            if offset == len(source):
                raise ValueError("truncated resource inventory sexp")
            if source[offset] == '(':
                offset += 1
                result = []
                while True:
                    while offset < len(source) and source[offset].isspace():
                        offset += 1
                    if offset == len(source):
                        raise ValueError("unclosed resource inventory sexp")
                    if source[offset] == ')':
                        offset += 1
                        return result
                    result.append(parse(depth + 1))
            if source[offset] == '"':
                value, end = json.JSONDecoder().raw_decode(source, offset)
                if not isinstance(value, str):
                    raise ValueError("invalid resource inventory atom")
                offset = end
                return value
            start = offset
            while offset < len(source) and not source[offset].isspace() and source[offset] not in '()"':
                offset += 1
            if offset == start:
                raise ValueError("invalid resource inventory atom")
            return source[start:offset]
        result = parse()
        if source[offset:].strip():
            raise ValueError("trailing resource inventory sexp")
        return result
    def field(record, key):
        if not isinstance(record, list):
            raise ValueError("invalid resource inventory record")
        values = [v[1] for v in record if isinstance(v, list) and len(v) == 2 and v[0] == key]
        if len(values) != 1:
            raise ValueError("missing or duplicate resource field: " + key)
        return values[0]
    expected_request = sexp("((kind single_port_ram) (contract ((width 16) (depth 256) "
                            "(read_latency 1) (port 1rw) (disabled_output hold) (write_output unspecified))))")
    registered = {}
    resource_ids = set()
    for r in manifest["resources"]:
        resource = r["id"]
        if resource in resource_ids:
            raise ValueError("duplicate registered resource")
        resource_ids.add(resource)
        implementation = field(sexp(r["selection"]), "implementation")
        if implementation == "Flops":
            continue
        if implementation != ["Macro", name]:
            raise ValueError("unsupported registered macro implementation")
        if sexp(r["request"]) != expected_request or sexp(r["elaborated_as"]) != "Selected_implementation":
            raise ValueError("registered macro requires elaborated exact256x16 implementation")
        components = resource.split('/')
        if any(not c or not c.isascii() or c[0].isdigit()
               or any(not (ch.isalnum() or ch == '_') for ch in c) for c in components):
            raise ValueError("invalid registered macro resource ID")
        registered[resource] = "hc_resource_" + '_'.join(str(len(c)) + '_' + c for c in components)
    resources = [m["resource"] for m in entries]
    if len(resources) != len(set(resources)) or set(resources) != set(registered):
        raise ValueError("macro resources differ from registered exact-SRAM inventory")
    placements = {}
    for m in entries:
        if m["instance"] != registered[m["resource"]]:
            raise ValueError("macro instance differs from canonical resource encoding")
        intent = m.get("physical_intent")
        if intent is not None:
            location = intent.get("location_um")
            if (intent.get("orientation") != "R0" or not isinstance(location, list) or len(location) != 2
                    or any(type(x) is not float or not (-float('inf') < x < float('inf')) for x in location)):
                raise ValueError("invalid macro placement intent")
            placements[m["instance"]] = {"location": location, "orientation": "R0"}
    if macro.get("instances") != placements:
        raise ValueError("configuration placements differ from declared macro intent inventory")
    if physical:
        plan = contract.get("physical_plan", {})
        expected = {"schema_version": 1, "strategy": "cmos5l_m4_edge_access_v1",
                    "plan_file": "physical/macro-plan.json", "pdn_tcl": "physical/pdn.tcl",
                    "checking_profile": "cmos5l_real_gds_v1",
                    "lvs_scope": "abstract_macro_boundary_with_schematic_cdl",
                    "macros": [{"resource": m["resource"], "instance": m["instance"], "module": m["module"],
                                "location_um": m["physical_intent"]["location_um"], "orientation": "R0",
                                "halo_um": m["physical_intent"]["halo_um"], "supplies": m["physical_intent"]["power"]}
                               for m in entries if m.get("physical_intent") is not None]}
        if len(placements) != len(entries) or plan != expected:
            raise ValueError("physical plan differs from complete macro intent")
        if json.loads((project / plan["plan_file"]).read_text()) != plan:
            raise ValueError("physical plan file differs from manifest")
        for filename in (plan["plan_file"], plan["pdn_tcl"]):
            if filename not in {f["path"] for f in manifest["files"]}:
                raise ValueError("missing physical input: " + filename)
        opts = {k: "false" for k in ("no_feol", "no_beol", "no_offgrid", "no_angle", "no_pin", "no_forbidden", "no_recommended", "precheck_drc")}
        opts.update(log="drc-klayout.log", threads=6)
        checks = {"PDN_CFG": "dir::../physical/pdn.tcl", "PRIMARY_GDSII_STREAMOUT_TOOL": "klayout",
                  "KLAYOUT_DRC_RUNSET": "pdk_dir::libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",
                  "KLAYOUT_DRC_OPTIONS": opts, "EXTRA_SPICE_MODELS": [path("cdl", ".cdl")],
                  "MAGIC_EXT_ABSTRACT_CELLS": [name], "MAGIC_DRC_USE_GDS": False}
        checks.update({k: True for k in ("RUN_KLAYOUT_DRC", "RUN_MAGIC_DRC", "ERROR_ON_ILLEGAL_OVERLAPS")})
        for key, value in checks.items():
            if raw.get(key) != value:
                raise ValueError("unsupported physical checking setting: " + key)
        for key in ("ERROR_ON_KLAYOUT_DRC", "ERROR_ON_LVS_ERROR", "ERROR_ON_PDN_VIOLATIONS", "ERROR_ON_DISCONNECTED_PINS", "ERROR_ON_TR_DRC"):
            if key in raw and raw[key] is not True:
                raise ValueError("physical checker cannot be disabled: " + key)
    for m in entries:
        for c in m["collateral"]:
            ref = c["reference"]
            if ref != "generated:" + name + ".blackbox.v":
                if not ref.startswith(prefix) or any(p in ("", ".", "..") for p in ref[9:].split("/")):
                    raise ValueError("unsupported SRAM collateral reference")
    return True


def macro_runtime(project, boundary):
    """sram_segmented1: single stock schema2 Hardening, through ordinary STA.

    Signoff/LVS adaptation and PT7 are intentionally not part of this profile.
    The existing coarse helper validates the same physical inputs, from pinned
    transported bytes. It never runs EDA during preparation/publication checks.
    """
    manifest = json.loads((project / "manifest.json").read_text())
    contract = manifest["macro_contract"]
    if (contract.get("schema_version") != 2 or contract.get("execution") != "fresh_full"
            or manifest.get("operation") != "Hardening"):
        raise ValueError("segmented SRAM requires schema2 Hardening; schema1 physical continuation is unsupported")
    if len(contract["macros"]) != 1:
        raise ValueError("segmented SRAM supports exactly one stock256x16 R0 macro")
    if boundary == "signoff":
        raise ValueError("segmented SRAM Signoff is unsupported; maximum endpoint is Timing")
    if any(manifest.get(k) is not None for k in
           ("timing_intent", "timing_native_coverage", "timing_physical_qualification")):
        raise ValueError("segmented SRAM does not support PT7 linked timing intent or exceptions")
    helper = project / "bridge/physical_runtime.py"
    if digest(helper) != PHYSICAL_RUNTIME_SHA256:
        raise ValueError("transported macro runtime hash mismatch")
    spec = importlib.util.spec_from_file_location("segmented_macro_runtime", helper)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.validate_plan(project)
    return manifest


def macro_evidence(project, boundary, entries, *, publication=None):
    """Validate bounded output evidence independently of State/input closure.

    Each publication copies these files into its own views/ inventory. Producer
    tag/boundary survive transitively; no dependency on the original PDN run.
    publication maps final paths to private staging paths before promotion.
    """
    fields = {"kind", "path", "sha256", "bytes", "producer_boundary", "producer_tag"}
    expected = 0 if boundary in ("synthesis", "floorplan") else 1
    if not isinstance(entries, list) or len(entries) != expected:
        raise ValueError("missing/duplicate transitive macro PDN evidence")
    plan = json.loads((project / "physical/macro-plan.json").read_text())
    for entry in entries:
        if (set(entry) != fields or entry["kind"] != "macro_power_geometry"
                or entry["producer_boundary"] != "placement"
                or not isinstance(entry["producer_tag"], str) or not entry["producer_tag"]
                or "/" in entry["producer_tag"]):
            raise ValueError("unsupported macro evidence producer")
        path = Path(entry["path"])
        if not path.is_absolute():
            raise ValueError("absolute macro evidence path required")
        if publication is not None:
            staging, location = publication
            relative = path.relative_to(Path(location) / "views")
            path = staging / "views" / relative
        if record(path) != {k: entry[k] for k in ("sha256", "bytes")}:
            raise ValueError("macro PDN evidence changed")
        geometry = json.loads(path.read_text())
        if (geometry.get("schema_version") != 1 or geometry.get("strategy") != plan["strategy"]
                or not isinstance(geometry.get("dbu_per_um"), int) or geometry["dbu_per_um"] <= 0
                or not isinstance(geometry.get("manufacturing_grid_dbu"), int)
                or geometry["manufacturing_grid_dbu"] <= 0):
            raise ValueError("unsupported macro power geometry")
        macros = geometry.get("macros", [])
        if len(macros) != 1:
            raise ValueError("macro power geometry inventory mismatch")
        macro, declared = macros[0], plan["macros"][0]
        if any(macro.get(k) != declared[k] for k in ("resource", "instance", "module")):
            raise ValueError("macro power geometry resource mismatch")
        units = geometry["dbu_per_um"]
        x, y = declared["location_um"]
        expected_footprint = [round(v * units) for v in (x, y, x + 236.8, y + 118.78)]
        if (macro.get("orientation") != "R0" or macro.get("footprint_dbu") != expected_footprint
                or macro.get("halo_dbu") != round(declared["halo_um"] * units)):
            raise ValueError("macro power geometry placement mismatch")
        contacts = macro.get("contacts", [])
        if len(contacts) != 3 or {c.get("pin") for c in contacts} != {"VDD!", "VDDARRAY!", "VSS!"}:
            raise ValueError("macro power geometry supply contacts missing")
        for contact in contacts:
            net = "VGND" if contact["pin"] == "VSS!" else "VPWR"
            if (contact.get("net") != net
                    or type(contact.get("minimum_clearance_dbu")) is not int
                    or contact["minimum_clearance_dbu"] < round(.6 * units)
                    or contact.get("measured_minimum_clearance_dbu", -1) < contact["minimum_clearance_dbu"]):
                raise ValueError("macro power geometry contact/net/clearance mismatch")
        if {p.get("net") for p in geometry.get("public_pins", [])} != {"VPWR", "VGND"}:
            raise ValueError("macro public supply pins missing")
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    protocol = protocol_module()
    request = protocol.validate_request(protocol.loads(args.request.read_text(encoding="utf-8")))
    if digest(__file__) != request["bridge_sha256"]:
        raise ValueError("transported bridge hash mismatch")
    if Path(request["reply_path"]).exists():
        raise ValueError("backend reply must be fresh")
    try:
        execute(request)
        protocol.write_reply(request)
    except Exception as exc:
        # Ordinary backend failures have diagnostic replies AND nonzero exit.
        # BaseException (signals/SystemExit/KeyboardInterrupt) is never completed.
        protocol.write_reply(request, error=str(exc) or type(exc).__name__)
        raise


def execute(request):
    # Same official executor API used by LibreLane's --jobs option. No recipe or
    # Step substitution; resource overrides are resolved identically for execute
    # and publication validation.
    from concurrent.futures import ThreadPoolExecutor
    from librelane.common import set_tpe
    set_tpe(ThreadPoolExecutor(max_workers=6))
    project, pdk = Path(request["project"]), Path(request["pdk_root"])
    candidate = Path(request["candidate"])
    boundary = request["boundary"]
    validating = request["operation"] == "validate_publication"
    macro = validate_macro_bundle(project)
    if macro:
        macro_runtime(project, boundary)
    if not validating:
        candidate.mkdir()  # fresh output only, never read a stale success file
    classic = Flow.factory.get("Classic")
    overrides = ["OPENROAD_THREADS=6", "STA_THREADS=1", "KLAYOUT_DRC_THREADS=6", "KLAYOUT_XOR_THREADS=6"]
    if macro:
        overrides += ["MAX_SLEW_VIOLATION_CORNERS=*", "MAX_CAP_VIOLATION_CORNERS=*"]
    flow = classic(str(project / "src/config.json"), pdk_root=str(pdk), config_override_strings=overrides)
    resolved = flow.config.dumps()
    dependencies = closure(flow.config, project, pdk)
    if validating:
        if resolved != (candidate / "resolved.json").read_text():
            raise ValueError("publication resolved configuration mismatch")
        if dependencies != json.loads((candidate / "closure.json").read_text()):
            raise ValueError("publication config/input/collateral/runtime closure mismatch")
        if macro:
            published = json.loads((candidate / "manifest.json").read_text())
            if published.get("schema_version") != 3 or published.get("macro_profile") != MACRO_PROFILE:
                raise ValueError("macro publication requires checkpoint schema3 profile")
            macro_evidence(project, boundary, published["macro_evidence"],
                           publication=(candidate, published["location"]))
        return  # no Flow.start; validation cannot produce readiness or artifacts
    initial, frm, evidence = None, None, []
    if request["checkpoint"] is not None:
        source = Path(request["checkpoint"])
        if resolved != (source / "resolved.json").read_text():
            raise ValueError("resolved configuration mismatch before continuation")
        if dependencies != json.loads((source / "closure.json").read_text()):
            raise ValueError("configuration/input/collateral closure mismatch before continuation")
        initial = State.loads((source / "state.json").read_text())
        initial.validate()
        ids = [cls.id for cls in flow.Steps]
        source_boundary = request["source_boundary"]
        source_manifest = json.loads((source / "manifest.json").read_text())
        if macro:
            if source_manifest.get("schema_version") != 3 or source_manifest.get("macro_profile") != MACRO_PROFILE:
                raise ValueError("macro continuation requires checkpoint schema3 profile")
            if boundary not in source_manifest.get("restart_targets", []):
                raise ValueError("untested macro checkpoint transition")
            evidence = macro_evidence(project, source_boundary, source_manifest["macro_evidence"])
        if source_manifest["boundary"] != source_boundary or boundary not in RESTART_TARGETS.get(source_boundary, ()):
            raise ValueError("unsupported checkpoint transition")
        frm = ids[ids.index(ENDPOINTS[source_boundary]) + 1]
        if frm != SUCCESSORS[source_boundary] or ids.index(frm) > ids.index(ENDPOINTS[boundary]):
            raise ValueError("unsupported Classic immediate successor")
    elif boundary != "synthesis":
        raise ValueError("physical segment requires a tested checkpoint")
    state = flow.start(tag=request["tag"], with_initial_state=initial,
                       frm=frm, to=ENDPOINTS[boundary])
    # Mutation during a segment invalidates publication even after tool success.
    if closure(flow.config, project, pdk) != dependencies:
        raise ValueError("declared closure changed during segment")
    if not flow.step_objects or flow.step_objects[-1].id != ENDPOINTS[boundary]:
        raise ValueError("endpoint was not reached")
    if initial is not None and flow.step_objects[0].id != frm:
        raise ValueError("successor checks were not preserved")
    if macro:
        if boundary == "placement":
            paths = list(Path(flow.run_dir).glob("*-openroad-generatepdn/macro-power-geometry.json"))
            if len(paths) != 1:
                raise ValueError("missing/duplicate executed macro PDN geometry")
            evidence = [{"kind": "macro_power_geometry", "path": str(paths[0]), **record(paths[0]),
                         "producer_boundary": "placement", "producer_tag": request["tag"]}]
        macro_evidence(project, boundary, evidence)
    (candidate / "state.json").write_text(state.dumps())
    (candidate / "resolved.json").write_text(resolved)
    (candidate / "closure.json").write_text(json.dumps(dependencies, allow_nan=False))
    report = {"status": "completed", "boundary": boundary, "from": frm,
              "source_boundary": request.get("source_boundary"),
              "to": ENDPOINTS[boundary], "run_dir": flow.run_dir,
              "returned_steps": [step.id for step in flow.step_objects],
              "bridge_sha256": digest(__file__), "metrics": metrics(dict(state.metrics)),
              "metric_origin": "cumulative_final_state; not per-step measurement provenance",
              "configured_gates": {pattern: {var: flow.config[var] for var in variables}
                                   for pattern, variables in flow.gating_config_vars.items()},
              "gate_evidence": "configured Classic gates; returned_steps are successful outer step objects, not body/skip provenance",
               "acceptance": "not_evaluated"}
    if macro:
        report.update(macro_profile=MACRO_PROFILE, macro_evidence=evidence)
    (candidate / "segment.json").write_text(json.dumps(report, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
