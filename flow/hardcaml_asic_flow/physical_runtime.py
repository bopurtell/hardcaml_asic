"""Fail-closed evidence and functional replay for the stock CMOS5L profile.

Invoked inside the recorded runtime; never runs placement or routing. Bounded
Magic diagnostics use the pinned reader/deck. Physical evidence is an observation,
not an editable waiver input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import xml.etree.ElementTree as ET


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def validate_plan(project):
    project = Path(project)
    manifest = load(project / "manifest.json")
    contract = manifest["macro_contract"]
    plan = contract["physical_plan"]
    require(plan["schema_version"] == 1 and plan["strategy"] == "cmos5l_m4_edge_access_v1", "physical strategy")
    require(plan["plan_file"] == "physical/macro-plan.json" and plan["pdn_tcl"] == "physical/pdn.tcl", "physical input paths")
    require(load(project / plan["plan_file"]) == plan, "physical plan file differs from manifest")
    for name in (plan["plan_file"], plan["pdn_tcl"]):
        entries = [e for e in manifest["files"] if e["path"] == name]
        require(len(entries) == 1 and sha(project / name) == entries[0]["sha256"], "physical input hash: " + name)
    require(plan["checking_profile"] == "cmos5l_real_gds_v1", "checking profile")
    require(plan["lvs_scope"] == "abstract_macro_boundary_with_schematic_cdl", "LVS scope")
    return manifest, plan


def finite_count(metrics, key):
    value = metrics.get(key)
    require(type(value) in (int, float) and value >= 0 and value < 1e30 and int(value) == value,
            "missing/malformed required metric: " + key)
    return value


def normalize_cdl(text):
    """Injective local-symbol rename only: <bit> to [bit], VDD!/VSS! to plain.

    No devices, values, order or connectivity are removed. Reject a rename that
    would merge distinct symbols within any subcircuit. Supplied bytes are kept.
    """
    output, reverse, changes = [], {}, Counter()
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("*"):
            output.append(line)
            continue
        if line.lower().startswith(".subckt"):
            reverse = {}
        require(not line.lower().startswith(".global"), "unsupported global CDL declaration")

        def token(match):
            source = match.group()
            normalized = re.sub(r"^([A-Za-z_][A-Za-z0-9_]*)<(\d+)>$", r"\1[\2]", source)
            if normalized in ("VDD!", "VDDARRAY!", "VSS!"):
                normalized = normalized[:-1]
            key = normalized.casefold()  # Netgen SPICE is case-insensitive
            require(key not in reverse or reverse[key] == source.casefold(), "noninjective CDL symbol normalization: " + source)
            reverse[key] = source.casefold()
            if normalized != source:
                changes[source + " -> " + normalized] += 1
            return normalized

        output.append(re.sub(r"\S+", token, line))
    require(changes, "stock CDL naming adapter made no changes")
    return "".join(output), dict(changes)


def normalize_powered_verilog(text, macros):
    """Change only three named supply ports on the exact registered SRAM calls."""
    changes = []
    for macro in macros:
        pattern = r"\b" + re.escape(macro["module"]) + r"\s+" + re.escape(macro["instance"]) + r"\s*\((.*?)\);"
        matches = list(re.finditer(pattern, text, re.S))
        require(len(matches) == 1, "missing/duplicate registered powered macro instance")
        match = matches[0]
        body = match[1]
        for pin in ("VDD", "VDDARRAY", "VSS"):
            require(not re.search(r"\." + pin + r"\s*\(", body), "duplicate canonical macro supply port")
            body, count = re.subn(r"\.\\" + pin + r"!\s+\(", "." + pin + "(", body)
            require(count == 1, "missing/duplicate exact powered macro supply port: " + pin)
        text = text[:match.start(1)] + body + text[match.end(1):]
        changes.append({"instance": macro["instance"], "ports": {p + "!": p for p in ("VDD", "VDDARRAY", "VSS")}})
    return text, changes


def prepare_lvs_inputs(project, cdl, powered, output):
    manifest, _ = validate_plan(project)
    cdl, powered, output = Path(cdl), Path(powered), Path(output)
    source = cdl.read_text()
    converted, renames = normalize_cdl(source)
    verilog, ports = normalize_powered_verilog(powered.read_text(), manifest["macro_contract"]["macros"])
    derived_cdl, derived_verilog = output / "macro-reader.cdl", output / "powered-reader.v"
    derived_cdl.write_text(converted)
    derived_verilog.write_text(verilog)
    write(output / "lvs-reader.json", {"schema_version": 1, "strategy": "cmos5l_cdl_names_v1",
          "scope": "abstract_macro_boundary_with_schematic_cdl", "source_cdl": str(cdl), "source_cdl_sha256": sha(cdl),
          "source_powered_netlist": str(powered), "source_powered_sha256": sha(powered),
          "derived_cdl_sha256": sha(derived_cdl), "derived_powered_sha256": sha(derived_verilog),
          "cdl_symbol_renames": renames, "powered_supply_renames": ports})
    return derived_cdl, derived_verilog


def stock_lvs_ports():
    pins = {"VDD", "VDDARRAY", "VSS", "A_CLK", "A_REN", "A_WEN", "A_MEN", "A_DLY",
            "A_BIST_EN", "A_BIST_CLK", "A_BIST_REN", "A_BIST_WEN", "A_BIST_MEN"}
    for bus, width in (("A_ADDR", 8), ("A_BIST_ADDR", 8), ("A_BIST_BM", 16), ("A_BIST_DIN", 16),
                       ("A_BM", 16), ("A_DIN", 16), ("A_DOUT", 16)):
        pins.update(bus + "[" + str(i) + "]" for i in range(width))
    return pins


def validate_lvs_statistics(stats, manifest):
    """A Netgen exit or 'match' line is insufficient: inspect every cell/pin."""
    require(type(stats) is list and stats, "Netgen comparison JSON missing")
    for entry in stats:
        require(not any(entry.get(key) for key in ("properties", "badnets", "badelements")), "Netgen electrical/property comparison failed")
    top = stats[-1]
    nets = top.get("nets")
    require(type(nets) is list and len(nets) == 2 and nets[0] == nets[1], "LVS net inventory differs")
    expected = Counter(m["module"] for m in manifest["macro_contract"]["macros"])
    require(set(expected) == {"RM_IHPSG13_1P_256x16_c2_bm_bist"}, "unsupported LVS macro identity")
    require(len(top.get("devices", [])) == 2, "Netgen top device inventory missing")
    for devices in top["devices"]:
        actual = {name: count for name, count in devices if name.startswith("RM_")}
        require(actual == dict(expected), "macro boundary disappeared or duplicated during LVS")
    require(dict(top["devices"][0]) == dict(top["devices"][1]), "LVS device inventories differ")
    for entry in stats:
        pins = entry.get("pins", [[], []])
        require(len(pins) == 2 and len(pins[0]) == len(pins[1]), "LVS pin inventory length differs")
        for a, b in zip(*pins):
            require(not a.startswith("(") and not b.startswith("("), "unmatched LVS pin placeholder")
            require(a.lower().rstrip("!") == b.lower().rstrip("!"), "unmatched/renamed LVS pin: " + a + " / " + b)
    wanted = {pin.lower() for pin in stock_lvs_ports()}
    matched = [entry for entry in stats[:-1] if "pins" in entry and len(entry["pins"][0]) == len(wanted) and
               {pin.lower().rstrip("!") for pin in entry["pins"][0]} == wanted]
    require(len(matched) == 1, "complete 109-pin stock macro comparison missing or duplicated")


def validate_lvs_result(step, manifest):
    validate_lvs_statistics(load(step / "reports/lvs.netgen.json"), manifest)


def validate_lvs_reader(step, manifest, pdk_root):
    refs = manifest["flow_configuration"]["EXTRA_SPICE_MODELS"]
    require(len(refs) == 1 and refs[0].startswith("pdk_dir::"), "exact schematic CDL required")
    cdl = (pdk_root / "ihp-sg13cmos5l" / refs[0][9:]).resolve(strict=True)
    script_path, log_path = step / "lvs_script.lvs", step / "netgen-lvs.log"
    script = script_path.read_text()
    reader = load(step / "lvs-reader.json")
    require(reader["strategy"] == "cmos5l_cdl_names_v1" and Path(reader["source_cdl"]).resolve() == cdl,
            "unsupported schematic CDL reader strategy")
    require(reader["source_cdl_sha256"] == sha(cdl), "reader source CDL changed")
    derived, derived_powered = step / "macro-reader.cdl", step / "powered-reader.v"
    require(derived.read_text() == normalize_cdl(cdl.read_text())[0] and reader["derived_cdl_sha256"] == sha(derived),
            "derived CDL is not the declared injective source normalization")
    powered = Path(reader["source_powered_netlist"])
    require(reader["source_powered_sha256"] == sha(powered) and
            derived_powered.read_text() == normalize_powered_verilog(powered.read_text(), manifest["macro_contract"]["macros"])[0] and
            reader["derived_powered_sha256"] == sha(derived_powered), "derived powered reader input changed")
    reads = re.findall(r"^readnet spice (.+) \$circuit2$", script, re.M)
    consumed = [p for p in reads if Path(p).resolve() == derived.resolve()]
    require(len(consumed) == 1, "Netgen did not consume declared schematic CDL exactly once")
    require(("Reading SPICE netlist file '" + consumed[0] + "'...") in log_path.read_text(),
            "executed Netgen log does not confirm schematic CDL read")
    require("-blackbox -json" in script, "unexpected LVS comparison scope")
    validate_lvs_result(step, manifest)
    return cdl, powered, derived, derived_powered


def effective_deck_options(resolved, declared):
    """Pinned Union[int,bool,str] coercion turns declared 'false' into 0."""
    require(set(resolved) == set(declared), "effective deck option keys differ")
    for key, expected in declared.items():
        actual = resolved[key]
        if expected == "false":
            require(actual == "false" or actual is False or type(actual) is int and actual == 0,
                    "effective deck switch differs: " + key)
        else:
            require(type(actual) is type(expected) and actual == expected, "effective deck option differs: " + key)


def deck_logs(project, flow, checked_gds):
    steps = list(flow.glob("*-klayout-drc"))
    require(len(steps) == 1, "unique KLayout DRC step required")
    # LibreLane captures stdout in the step, but the deck's relative $log is
    # resolved against the project process cwd, not the step directory.
    captured, deck = steps[0] / "klayout-drc.log", project / "drc-klayout.log"
    for path in (captured, deck):
        text = path.read_text()
        require("Starting running IHP-SG13CMOS5L KLayout DRC runset on " + str(checked_gds) in text,
                "KLayout log does not cover checked GDS")
        for switch in ("FEOL", "BEOL", "OFFGRID", "ANGLE", "PIN", "FORBIDDEN", "RECOMMENDED"):
            require(switch + " enabled: true" in text, "KLayout coverage switch missing: " + switch)
        require("PreCheck DRC enabled: false" in text, "unexpected restricted precheck deck scope")
    return captured, deck


def verify_stock_geometry(layout, manifest, pdk_root, db):
    """Compare assembled macro polygons to the pinned supplied GDS, not its LEF."""
    for macro in {m["module"] for m in manifest["macro_contract"]["macros"]}:
        refs = manifest["flow_configuration"]["MACROS"][macro]["gds"]
        require(len(refs) == 1 and refs[0].startswith("pdk_dir::"), "exact stock GDS required")
        source = pdk_root / "ihp-sg13cmos5l" / refs[0][9:]
        stock = db.Layout()
        stock.read(str(source))
        require(stock.dbu == layout.dbu, "unsupported assembled/stock GDS DBU mismatch")
        original, actual = stock.cell(macro), layout.cell(macro)
        require(original is not None and actual is not None, "missing supplied/assembled macro")
        layers = {(l.layer, l.datatype) for l in stock.layer_infos()} | {(l.layer, l.datatype) for l in layout.layer_infos()}
        for layer, datatype in layers:
            left, right = stock.find_layer(layer, datatype), layout.find_layer(layer, datatype)
            a = db.Region() if left is None else db.Region(original.begin_shapes_rec(left))
            b = db.Region() if right is None else db.Region(actual.begin_shapes_rec(right))
            require((a ^ b).is_empty(), "assembled macro polygons differ from pinned GDS: " + macro)


def geometry_evidence(flow, plan, layout, db):
    reports = list(flow.glob("*-openroad-generatepdn/macro-power-geometry.json"))
    require(len(reports) == 1, "missing unique executed macro power geometry report")
    geometry = load(reports[0])
    require(geometry["schema_version"] == 1 and geometry["strategy"] == plan["strategy"], "power geometry schema")
    units = geometry["dbu_per_um"]
    require(type(units) is int and units > 0, "invalid geometry DBU")
    require(type(geometry["manufacturing_grid_dbu"]) is int and geometry["manufacturing_grid_dbu"] > 0, "invalid geometry grid")
    scale = 1 / (units * layout.dbu)
    layer = layout.find_layer(50, 0)  # pinned layers_def.drc: Metal4 drawing
    require(layer is not None, "final GDS has no Metal4 drawing")
    metal = db.Region(layout.top_cell().begin_shapes_rec(layer)).merged()

    def rect(value):
        require(len(value) == 4 and all(type(x) is int for x in value) and value[0] < value[2] and value[1] < value[3], "invalid power rectangle")
        scaled = [x * scale for x in value]
        require(all(abs(x - round(x)) < 1e-6 for x in scaled), "power rectangle not representable in GDS DBU")
        return db.Box(*[round(x) for x in scaled])

    def covered(value):
        box = rect(value)
        require((db.Region(box) - metal).is_empty(), "power geometry lost/changed in final GDS")
        return box

    expected = {m["instance"]: m for m in plan["macros"]}
    physical_instances = [inst for inst in layout.top_cell().each_inst() if inst.cell.name.startswith("RM_")]
    require(len(physical_instances) == len(expected) and all(inst.cell.name in {m["module"] for m in plan["macros"]}
            for inst in physical_instances), "final GDS macro instance inventory differs from plan")
    require(len(geometry["macros"]) == len(expected), "geometry macro inventory mismatch")
    seen = set()
    for macro in geometry["macros"]:
        name = macro["instance"]
        require(name in expected and name not in seen, "unknown/duplicate geometry instance")
        seen.add(name)
        declaration = expected[name]
        require(macro["resource"] == declaration["resource"] and macro["module"] == declaration["module"] and macro["orientation"] == "R0", "geometry identity mismatch")
        footprint = macro["footprint_dbu"]
        require(footprint == [round(x * units) for x in [*declaration["location_um"], declaration["location_um"][0] + 236.8, declaration["location_um"][1] + 118.78]], "geometry placement mismatch")
        origin = rect(footprint)
        placed = [inst for inst in layout.top_cell().each_inst()
                  if inst.cell.name == declaration["module"] and inst.trans.disp.x == origin.left and inst.trans.disp.y == origin.bottom
                  and inst.trans.angle == 0 and not inst.trans.is_mirror()]
        require(len(placed) == 1, "final real macro placement differs from physical plan")
        contacts = macro["contacts"]
        require(len(contacts) == 3 and {c["pin"] for c in contacts} == {"VDD!", "VDDARRAY!", "VSS!"}, "all three supply contacts required")
        for contact in contacts:
            require(contact["net"] == declaration["supplies"][contact["pin"]], "power contact net mismatch")
            for key, minimum in (("minimum_clearance_dbu", 0.6), ("minimum_contact_width_dbu", 1.4), ("minimum_overlap_dbu", 0.4)):
                require(contact[key] >= round(minimum * units), "power access policy weakened")
            require(type(contact.get("measured_minimum_clearance_dbu")) is int and
                    contact["measured_minimum_clearance_dbu"] >= contact["minimum_clearance_dbu"], "reported actual power clearance violates policy")
            boxes = [covered(contact[key]) for key in ("pin_rect_dbu", "neck_rect_dbu", "landing_rect_dbu", "grid_rect_dbu")]
            # Every consecutive piece must share area, not just touch at a point.
            require(all(not (a & b).empty() and (a & b).area() > 0 for a, b in zip(boxes, boxes[1:])), "disconnected power access chain")
    public = geometry["public_pins"]
    require(len(public) == 2 and {p["net"] for p in public} == {"VPWR", "VGND"}, "public supply pin inventory")
    for pin in public:
        require(pin["layer"] == "Metal4", "public supply layer")
        covered(pin["rect_dbu"])
    final_lef = flow / "final/lef" / (layout.top_cell().name + ".lef")
    validate_public_lef(final_lef.read_text(), public, units)
    return reports[0]


def validate_public_lef(text, public, units):
    """Template application must not replace the selected public power pins."""
    for pin in public:
        name = pin["net"]
        matches = re.findall(r"(?ms)^\s*PIN " + re.escape(name) + r"\s*\n(.*?)^\s*END " + re.escape(name) + r"\s*$", text)
        require(len(matches) == 1, "final LEF public supply pin missing/duplicated: " + name)
        body = matches[0]
        require("USE " + ("POWER" if name == "VPWR" else "GROUND") + " ;" in body, "final LEF supply use differs")
        layers = re.findall(r"\bLAYER\s+(\w+)\s*;", body)
        rectangles = re.findall(r"\bRECT\s+([^;]+);", body)
        require(layers == ["Metal4"] and len(rectangles) == 1, "final LEF public supply geometry differs")
        coordinates = [Decimal(v) * units for v in rectangles[0].split()]
        require(coordinates == pin["rect_dbu"], "final public supply pin differs from generated power geometry")


def magic_findings(path):
    from librelane.common import DRC
    with Path(path).open() as stream:
        drc, count = DRC.from_magic(stream)
    findings = []
    for violation in drc.violations.values():
        for box in violation.bounding_boxes:
            findings.append((violation.description, tuple(Decimal(str(x)) for x in box)))
    require(len(findings) == count, "Magic raw report/count disagreement")
    return findings


def classify_magic(findings, baseline, plan, geometry):
    """Only exact intrinsic abstract findings, away from new access geometry.

    This is deliberately not a category-name, wildcard, or bounding-box waiver.
    The baseline is freshly generated from stock LEF using the same pinned Magic
    setup. A new bridge can lie inside the macro, so its clearance envelope is
    excluded even if a rule/rectangle happens to match the stock baseline.
    """
    units = Decimal(geometry["dbu_per_um"])
    available = Counter()
    for macro in plan["macros"]:
        x, y = map(lambda x: Decimal(str(x)), macro["location_um"])
        report = next(m for m in geometry["macros"] if m["instance"] == macro["instance"])
        for rule, box in baseline:
            shifted = (box[0] + x, box[1] + y, box[2] + x, box[3] + y)
            # Only intrinsic findings strictly inside the placed macro. Findings
            # spanning its boundary are integration-sensitive and cannot be waived.
            if not (x < shifted[0] <= shifted[2] < x + Decimal("236.8") and
                    y < shifted[1] <= shifted[3] < y + Decimal("118.78")):
                continue
            near_bridge = False
            for contact in report["contacts"]:
                guard = Decimal(contact["minimum_clearance_dbu"]) / units
                for key in ("neck_rect_dbu", "landing_rect_dbu"):
                    r = [Decimal(v) / units for v in contact[key]]
                    if not (shifted[2] < r[0] - guard or shifted[0] > r[2] + guard or
                            shifted[3] < r[1] - guard or shifted[1] > r[3] + guard):
                        near_bridge = True
            if not near_bridge:
                available[(rule, shifted)] += 1
    unmatched = Counter(findings) - available
    require(not unmatched, "unclassified/integration-sensitive Magic DRC findings: " + str(list(unmatched)[:3]))
    return {"status": "dispositioned_stock_abstract", "raw_count": len(findings),
            "accepted_count": len(findings), "unclassified_count": 0,
            "basis": "fresh_exact_stock_lef_baseline_excluding_boundary_and_bridge_clearance"}


M2_OBS_RULE = "Metal2 > 10.0um with runlength > 10.0um spacing to unrelated m2 < 120 (M2.f)"
M3_OBS_RULE = "Metal3 > 10.0um with runlength > 10.0um spacing to unrelated m3 < 120 (M3.f)"


def obstruction_layer(rule):
    # Magic's %d rule distance can render in internal 0.005um units or um.
    # The same-stock control must reproduce every marker, not just its rule ID.
    for layer, text in (("Metal2", M2_OBS_RULE), ("Metal3", M3_OBS_RULE)):
        if rule in (text, text.replace("< 120", "< 0.6um")):
            return layer
    raise ValueError("unclassified Magic rule: " + rule)


def obstruction_findings(findings):
    return Counter((obstruction_layer(rule), box) for rule, box in findings)


def without_wide_obstructions(text, layers):
    """Remove only the two exact stock wide OBS rectangles in diagnostic views.

    All signal pins, narrow Metal2 obstructions and every other layer survive.
    This is never implementation, streamout, extraction or LVS collateral.
    """
    require(set(layers) <= {"Metal2", "Metal3"} and layers, "unsupported diagnostic obstruction layers")
    matches = list(re.finditer(r"(?m)^  OBS\s*$", text))
    require(len(matches) == 1, "unique stock LEF obstruction section required")
    start = matches[0].end()
    obs = text[start:]
    end = re.search(r"(?m)^  END\s*$", obs)
    require(end is not None, "stock LEF obstruction end missing")
    blocks = list(re.finditer(r"(?m)^    LAYER (\w+)[^\n]*\n", obs[:end.start()]))
    removed, pieces = Counter(), [obs[:blocks[0].start()]] if blocks else []
    targets = {"Metal2": "      RECT 0 0.52 236.8 118.78 ;\n", "Metal3": "      RECT 0 0 236.8 118.78 ;\n"}
    for index, match in enumerate(blocks):
        stop = blocks[index + 1].start() if index + 1 < len(blocks) else end.start()
        block = obs[match.start():stop]
        layer = match[1]
        if layer in layers:
            count = block.count(targets[layer])
            require(count <= 1, "duplicate stock wide obstruction rectangle")
            if count:
                block = block.replace(targets[layer], "")
                removed[layer] += count
                if not block[len(match.group()):].strip():
                    block = ""  # avoid an empty Metal3 LAYER block
        pieces.append(block)
    require(all(removed[layer] == 1 for layer in layers), "exact stock wide obstruction rectangle missing")
    return text[:start] + "".join(pieces) + obs[end.start():]


def without_m3_obstruction(text):
    return without_wide_obstructions(text, ("Metal3",))


# Witnessed by PT3c's same-DEF 9/7/2/0 experiment, not a generalized edge
# inequality. These four *local* rectangles are the entire added coverage.
# Any coordinate change, even one 0.005um grid step, needs new review/evidence.
PT3C_STOCK_LEF_SHA256 = "bea0d602296a03ea2020420501767ff321f53186d7444ea0faefa4d49dac047b"
PT3C_MACRO = "RM_IHPSG13_1P_256x16_c2_bm_bist"
PT3C_EDGE_PROJECTIONS = {
    ("Metal2", tuple(map(Decimal, ("-0.600", "117.805", "-0.540", "128.785")))),
    ("Metal3", tuple(map(Decimal, ("0.000", "119.240", "181.660", "119.380")))),
    ("Metal3", tuple(map(Decimal, ("202.980", "119.240", "236.800", "119.380")))),
    ("Metal3", tuple(map(Decimal, ("222.240", "-0.460", "236.800", "-0.260")))),
}


def wide_obstruction_geometry(findings, plan, *, reviewed_projections=False):
    require(findings, "nonempty obstruction findings required")
    attribution = []
    witnessed_inventory = set()
    for rule, box in findings:
        layer = obstruction_layer(rule)
        adjacent = []
        for macro in plan["macros"]:
            x, y = [Decimal(str(v)) for v in macro["location_um"]]
            halo = Decimal(str(macro["halo_um"]))
            influence = min(halo, Decimal("0.6"))
            right, top = x + Decimal("236.8"), y + Decimal("118.78")
            along_x = x < box[0] < box[2] < right and box[2] - box[0] > 10
            bottom_band = y - influence <= box[1] < box[3] < y
            top_band = top < box[1] < box[3] <= top + influence
            m3_band = along_x and (bottom_band or top_band) and box[3] - box[1] <= Decimal("0.6")
            left_band = x - influence <= box[0] < box[2] < x
            right_band = right < box[0] < box[2] <= right + influence
            # M2's exact stock rectangle starts at y+0.52. Magic's >10um
            # runlength projection can overhang its end by 10um + one grid unit.
            obs_bottom = y + Decimal("0.52")
            overlap = min(box[3], top) - max(box[1], obs_bottom)
            m2_band = ((left_band or right_band) and box[2] - box[0] <= Decimal("0.6") and
                       box[1] >= obs_bottom and box[3] <= top + Decimal("10.005") and overlap > 10)
            local = (box[0] - x, box[1] - y, box[2] - x, box[3] - y)
            witnessed = (reviewed_projections and macro.get("module") == PT3C_MACRO and halo >= Decimal("0.6") and
                         (layer, local) in PT3C_EDGE_PROJECTIONS)
            if layer == "Metal3" and m3_band or layer == "Metal2" and m2_band or witnessed:
                adjacent.append({"instance": macro["instance"], "layer": layer,
                                 "coverage": "pt3c_exact_edge_projection_v1" if witnessed else "strict_influence_band"})
        require(len(adjacent) == 1, "Magic marker is not a unique stock obstruction influence-band artifact")
        if adjacent[0]["coverage"] == "pt3c_exact_edge_projection_v1":
            key = (adjacent[0]["instance"], layer, box)
            require(key not in witnessed_inventory, "duplicate reviewed edge projection")
            witnessed_inventory.add(key)
        attribution.append({**adjacent[0], "rule": rule, "box_um": [str(x) for x in box]})
    return attribution


def classify_wide_obstructions(findings, counterfactual, plan, *, controls=None, source_lef_sha256=None):
    require(not counterfactual and findings, "counterfactual Magic DRC must be clean")
    if controls is not None:
        validate_obstruction_control_markers(findings, controls)
    attribution = wide_obstruction_geometry(findings, plan, reviewed_projections=(
        controls is not None and source_lef_sha256 == PT3C_STOCK_LEF_SHA256))
    return {"status": "dispositioned_stock_abstract", "raw_count": len(findings), "accepted_count": len(findings),
             "unclassified_count": 0, "basis": "exact_M2_M3_f_wide_OBS_layer_controls_clean_real_GDS_required",
             "findings": attribution}


def classify_m3_obstruction(findings, counterfactual, plan):
    require(all(obstruction_layer(rule) == "Metal3" for rule, _ in findings), "unclassified Magic rule")
    return classify_wide_obstructions(findings, counterfactual, plan)


def validate_obstruction_control_markers(findings, observed):
    """Exact multiset causality only; this does not establish physical acceptance."""
    require(findings, "nonempty obstruction findings required")
    layers = sorted({obstruction_layer(rule) for rule, _ in findings})
    required = {"stock", *("without-" + layer for layer in layers)}
    if len(layers) > 1:
        required.add("without-combined")
    require(set(observed) == required, "complete layer-specific Magic controls required")
    require(obstruction_findings(observed["stock"]) == obstruction_findings(findings), "same-stock Magic control does not reproduce raw markers")
    remaining = []
    for layer in layers:
        remaining = observed["without-" + layer]
        expected = [(rule, box) for rule, box in findings if obstruction_layer(rule) != layer]
        require(obstruction_findings(remaining) == obstruction_findings(expected), "layer-specific obstruction control changed unrelated markers or retained target markers")
    if len(layers) > 1:
        remaining = observed["without-combined"]
    require(not remaining, "combined obstruction control must be clean")


def validate_obstruction_controls(findings, observed, plan, *, source_lef_sha256=None):
    validate_obstruction_control_markers(findings, observed)
    remaining = observed.get("without-combined", observed["without-" + obstruction_layer(findings[0][0])])
    return classify_wide_obstructions(findings, remaining, plan, controls=observed, source_lef_sha256=source_lef_sha256)


def wide_obstruction_counterfactual(flow, plan, findings, output, *, evidence_only=False, tool_timeout_seconds=300):
    """Same routed DEF; stock, per-layer and combined diagnostic-only controls.

    This never becomes implementation/LVS/streamout collateral. Authoritative
    real-GDS DRC and exact stock polygon/power checks must pass independently.
    """
    from librelane.common import get_script_dir
    require(type(tool_timeout_seconds) is int and 0 < tool_timeout_seconds <= 480 and
            (evidence_only or tool_timeout_seconds == 300), "unsupported Magic diagnostic timeout")
    if evidence_only:
        require(findings, "nonempty obstruction findings required")
        obstruction_findings(findings)  # unknown rules still fail before launching
    else:
        config = load(flow / "resolved.json")
        source = Path(config["MACROS"][plan["macros"][0]["module"]]["lef"][0])
        # Candidate geometry may launch controls, but is never a disposition.
        wide_obstruction_geometry(findings, plan, reviewed_projections=sha(source) == PT3C_STOCK_LEF_SHA256)
    output = Path(output)
    output.mkdir()
    config = load(flow / "resolved.json")
    require(config.get("MAGIC_DRC_USE_GDS") is False and config.get("MAGIC_DRC_MAGLEFS") is None,
            "unsupported Magic reader scope for obstruction diagnostic")
    name = plan["macros"][0]["module"]
    source = Path(config["MACROS"][name]["lef"][0])
    drc_step = next(flow.glob("*-magic-drc"))
    state = load(drc_step / "state_in.json")
    input_def = Path(state["def"])
    paths = [str(output), str(drc_step / "_env.tcl"), str(get_script_dir())]
    require(not any(c in path for path in paths for c in "{}\n\r"), "unsafe Magic diagnostic path")
    body = (Path(get_script_dir()) / "magic/drc.tcl").read_text()
    marker = "set report_dir $::env(STEP_DIR)/reports"
    require(body.count(marker) == 1, "unsupported pinned Magic DRC script")
    controls = {}

    def run_case(label, layers):
        folder = output / label
        folder.mkdir()
        (folder / "reports").mkdir()
        derived = folder / "diagnostic.lef"
        derived.write_text(without_wide_obstructions(source.read_text(), layers) if layers else source.read_text())
        prefix = "drc off\ncrashbackups disable\nlocking disable\nsource {" + str(drc_step / "_env.tcl") + "}\n"
        if evidence_only:
            copied_def = folder / "diagnostic.def"
            copied_def.write_bytes(input_def.read_bytes())
            require(sha(copied_def) == sha(input_def), "diagnostic DEF copy differs")
            prefix += "set ::env(CURRENT_DEF) {" + str(copied_def) + "}\n"
        prefix += "set ::env(MACRO_LEFS) {" + str(derived) + "}\nset ::env(SCRIPTS_DIR) {" + str(get_script_dir()) + "}\n"
        prefix += "source $::env(SCRIPTS_DIR)/magic/common/read.tcl\nread_tech_lef\nread_pdk_lef\nread_macro_lef\nread_extra_lef\nread_pad_lef\nread_def\n"
        prefix += "set ::env(STEP_DIR) {" + str(folder) + "}\nset ::env(DESIGN_NAME) {" + config["DESIGN_NAME"] + "}\n"
        script = folder / "counterfactual.tcl"
        script.write_text(prefix + body[body.index(marker):] + "\nquit -noprompt\n")
        with (folder / "magic.log").open("w") as log:
            result = subprocess.run(["magic", "-dnull", "-noconsole", "-rcfile", config["MAGICRC"], str(script)],
                                    cwd=folder, stdout=log, stderr=subprocess.STDOUT, timeout=tool_timeout_seconds,
                                    env={**os.environ, "OMP_NUM_THREADS": "1", "OPENROAD_THREADS": "1"})
        require(result.returncode == 0, "Magic obstruction diagnostic failed")
        report = folder / "reports/drc.magic.rpt"
        observed = magic_findings(report)
        controls[label] = {"removed_wide_rectangles": list(layers), "raw_count": len(observed),
                            "diagnostic_lef_sha256": sha(derived), "script_sha256": sha(script), "report_sha256": sha(report)}
        if evidence_only:
            require(sha(copied_def) == sha(input_def), "diagnostic DEF changed")
            controls[label].update(routed_def_sha256=sha(copied_def),
                                   findings=[{"rule": rule, "box_um": [str(x) for x in box]} for rule, box in observed])
            write(folder / "observation.json", controls[label])
        return observed

    layers = sorted({obstruction_layer(rule) for rule, _ in findings})
    cases = [("stock", ())] + [("without-" + layer, (layer,)) for layer in layers]
    if len(layers) > 1:
        cases.append(("without-combined", layers))
    # Independent single-worker Magic processes; at most four jobs inside the
    # route's six-CPU ceiling. Large full-DEF checks otherwise serialize minutes
    # of identical parsing/checking and can exhaust a single diagnostic timeout.
    workers = 1 if evidence_only else min(4, len(cases))
    if evidence_only:
        # Do not queue the later controls: Executor.__exit__ otherwise waits for
        # them even after the first case has timed out and invalidated the set.
        observed = {}
        for label, removed in cases:
            try:
                observed[label] = run_case(label, removed)
            except Exception as exc:
                write(output / "evidence.json", {
                    "schema_version": 1, "status": "diagnostic_inconclusive", "physical_acceptance": False,
                    "failed_case": label, "error_type": type(exc).__name__, "error": str(exc),
                    "controls": controls, "source_lef_sha256": sha(source), "routed_def_sha256": sha(input_def),
                    "diagnostic_limits": {"parallel_magic_jobs": 1, "threads_per_job": 1, "tool_timeout_seconds": tool_timeout_seconds}})
                raise
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            observed = dict(zip((label for label, _ in cases), pool.map(lambda case: run_case(*case), cases)))
    if evidence_only:
        evidence = {"schema_version": 1, "status": "diagnostic_only", "physical_acceptance": False,
                    "source_lef_sha256": sha(source), "routed_def_sha256": sha(input_def), "controls": controls,
                    "diagnostic_limits": {"parallel_magic_jobs": 1, "threads_per_job": 1, "tool_timeout_seconds": tool_timeout_seconds}}
        # Retain observations even when the controls disprove the hypothesis.
        try:
            validate_obstruction_control_markers(findings, observed)
            evidence["exact_layer_control_relationships"] = "pass"
        except ValueError as exc:
            evidence.update(exact_layer_control_relationships="fail", error=str(exc))
        write(output / "evidence.json", evidence)
        return evidence
    disposition = validate_obstruction_controls(findings, observed, plan, source_lef_sha256=sha(source))
    disposition.update(source_lef_sha256=sha(source), routed_def_sha256=sha(input_def), controls=controls,
                       diagnostic_limits={"parallel_magic_jobs": min(4, len(cases)), "threads_per_job": 1, "tool_timeout_seconds": 300})
    write(output / "disposition.json", disposition)
    return disposition


def m3_obstruction_counterfactual(flow, plan, findings, output):
    classify_m3_obstruction(findings, [], plan)
    return wide_obstruction_counterfactual(flow, plan, findings, output)


def retained_obstruction_identity(flow, plan, directory):
    """Bind external diagnostics to this exact failed run, not just its markers.

    The checker source may be deliberately revised after diagnosis; it is not an
    implementation input. All other frozen inputs and run-bound collateral must
    still match. This API never promotes a failed run to provider acceptance.
    """
    flow, directory = Path(flow).resolve(), Path(directory).resolve()
    project, diagnostic = flow.parents[1], directory.parent
    run = project.parent
    require(flow == project / "runs/asic" and directory.name == "controls" and
            not directory.is_relative_to(run), "separate canonical diagnostic control directory required")
    frozen = load(diagnostic / "plan.json")
    execution = load(diagnostic / "execution.json")
    record, manifest = load(run / "run.json"), load(project / "manifest.json")
    require(frozen["schema_version"] == 1 and Path(frozen["run"]) == run and
            frozen["run_id"] == record["run_id"] and frozen["build_identity"] == record["build_identity"] and
            frozen["original_status"] == record["status"] == "failed" and frozen["physical_acceptance"] is False,
            "retained diagnostic run identity/status mismatch")
    require(execution["status"] == "completed" and type(execution["exit_code"]) is int and execution["exit_code"] == 0 and
            execution["physical_acceptance"] is False, "completed diagnostic execution required")
    require(manifest["macro_contract"]["physical_plan"] == plan and sha(project / "manifest.json") == record["manifest_sha256"],
            "retained diagnostic macro plan/manifest mismatch")
    budget = frozen["budget"]
    require(budget["exclusive_eda_lane_cpus"] == 6 and budget["parallel_magic_jobs"] == 1 and
            budget["threads_per_job"] == 1 and budget["cases"] == 4 and
            budget["per_case_timeout_seconds"] in (300, 480) and
            frozen["timeout_seconds"] == 4 * budget["per_case_timeout_seconds"] + 60,
            "unsupported retained diagnostic budget")
    command = frozen["command"]
    require(command.count("--entrypoint") == 1 and command.count("-B") == 1, "unsupported diagnostic command")
    entry = command.index("--entrypoint")
    image = record["environment"]["execution_binding"]["image_identity"]["image_id"]
    require(command[entry:entry + 5] == ["--entrypoint", "python3", image, "-I", "-B"] and
            command[-3:] == ["inside", "--output", str(diagnostic)] and "--pull=never" in command,
            "retained diagnostic image/output mismatch")
    runner = Path(command[entry + 5])
    require(runner.name == "run_magic_obstruction_diagnostic.py" and
            runner.parent.name == "sram_physical" and runner.parent.parent.name == "test",
            "unsupported retained diagnostic runner")
    revised_checker = runner.parents[2] / "flow/hardcaml_asic_flow/physical_runtime.py"
    hashes = frozen["input_sha256"]
    require(str(revised_checker) in hashes and not revised_checker.is_relative_to(run),
            "diagnostic checker provenance missing")
    config = load(flow / "resolved.json")
    step, = flow.glob("*-magic-drc")
    routed = Path(load(step / "state_in.json")["def"])
    names = {m["module"] for m in plan["macros"]}
    require(len(names) == 1, "unique retained macro module required")
    name, = names
    require(len(config["MACROS"][name]["lef"]) == 1, "exact retained macro LEF required")
    required = [run / "run.json", run / "results.json", run / "preflight.json", project / "manifest.json",
                project / "physical-runtime.py", project / plan["plan_file"], project / plan["pdn_tcl"],
                flow / "resolved.json", flow / "final/metrics.json", step / "_env.tcl", step / "state_in.json",
                step / "reports/drc.magic.rpt", routed, Path(config["MAGICRC"]),
                flow / "final/gds" / (manifest["top_module"] + ".gds"),
                flow / "final/nl" / (manifest["top_module"] + ".nl.v")]
    required += list(flow.glob("*-openroad-generatepdn/macro-power-geometry.json"))
    require(len(list(flow.glob("*-openroad-generatepdn/macro-power-geometry.json"))) == 1,
            "unique retained power geometry required")
    required += [Path(p) for p in [*config["MACROS"][name]["lef"], *config["TECH_LEFS"].values(), *config["CELL_LEFS"]]]
    for ref in record["environment"]["references"]:
        require(hashes.get(ref["path"]) == ref["sha256"] and sha(ref["path"]) == ref["sha256"],
                "retained diagnostic source reference mismatch")
        required.append(Path(ref["path"]))
    require(revised_checker not in required, "diagnostic checker cannot exempt a physical input")
    require(all(str(p) in hashes for p in required), "retained diagnostic frozen input missing")
    require(all(sha(p) == digest for p, digest in hashes.items() if Path(p) != revised_checker),
            "retained diagnostic frozen input changed")
    evidence, result = load(directory / "evidence.json"), load(diagnostic / "result.json")
    require(result == dict(evidence, original_run_status=record["status"], run_id=record["run_id"],
                           build_identity=record["build_identity"], raw_report_sha256=sha(step / "reports/drc.magic.rpt")),
            "retained diagnostic result/run/raw report mismatch")
    paths = [diagnostic / name for name in ("plan.json", "execution.json", "result.json")]
    return {str(p): sha(p) for p in paths}


def retained_obstruction_controls(flow, plan, findings, directory):
    """Re-read raw diagnostic controls; derived LEFs/DEFs must be exact copies.

    This is only the Magic portion of checking. Real-GDS, contacts, electrical
    metrics and executed LVS are independently required by check().
    """
    flow, directory = Path(flow).resolve(), Path(directory).resolve()
    identity_artifacts = retained_obstruction_identity(flow, plan, directory)
    from librelane.common import get_script_dir
    scripts = Path(get_script_dir())
    body = (scripts / "magic/drc.tcl").read_text()
    marker = "set report_dir $::env(STEP_DIR)/reports"
    require(body.count(marker) == 1, "unsupported retained Magic diagnostic script")
    evidence = load(directory / "evidence.json")
    require(evidence["status"] == "diagnostic_only" and evidence["physical_acceptance"] is False and
            evidence["exact_layer_control_relationships"] == "pass", "incomplete diagnostic control evidence")
    config = load(flow / "resolved.json")
    source = Path(config["MACROS"][plan["macros"][0]["module"]]["lef"][0])
    require(config.get("MAGIC_DRC_USE_GDS") is False and config.get("MAGIC_DRC_MAGLEFS") is None,
            "unsupported retained Magic reader scope")
    step, = flow.glob("*-magic-drc")
    routed = Path(load(step / "state_in.json")["def"])
    require(evidence["source_lef_sha256"] == sha(source) and evidence["routed_def_sha256"] == sha(routed),
            "retained controls differ from physical inputs")
    cases = {"stock": [], "without-Metal2": ["Metal2"], "without-Metal3": ["Metal3"],
             "without-combined": ["Metal2", "Metal3"]}
    require(set(evidence["controls"]) == set(cases), "four retained layer controls required")
    observed, artifacts = {}, {**identity_artifacts, str(directory / "evidence.json"): sha(directory / "evidence.json")}
    for label, layers in cases.items():
        folder = directory / label
        record = evidence["controls"][label]
        require(load(folder / "observation.json") == record, "retained per-case observation mismatch")
        lef, copied, script, report = (folder / p for p in ("diagnostic.lef", "diagnostic.def", "counterfactual.tcl", "reports/drc.magic.rpt"))
        expected = without_wide_obstructions(source.read_text(), layers) if layers else source.read_text()
        require(lef.read_text() == expected and record["removed_wide_rectangles"] == layers,
                "retained diagnostic changed pins/narrow OBS/power or wrong wide rectangle")
        require(sha(copied) == sha(routed) == record["routed_def_sha256"], "retained diagnostic DEF changed")
        for path, key in ((lef, "diagnostic_lef_sha256"), (script, "script_sha256"), (report, "report_sha256")):
            require(sha(path) == record[key], "retained diagnostic artifact changed: " + str(path))
        expected_script = "drc off\ncrashbackups disable\nlocking disable\nsource {" + str(step / "_env.tcl") + "}\n"
        expected_script += "set ::env(CURRENT_DEF) {" + str(copied) + "}\n"
        expected_script += "set ::env(MACRO_LEFS) {" + str(lef) + "}\nset ::env(SCRIPTS_DIR) {" + str(scripts) + "}\n"
        expected_script += "source $::env(SCRIPTS_DIR)/magic/common/read.tcl\nread_tech_lef\nread_pdk_lef\nread_macro_lef\nread_extra_lef\nread_pad_lef\nread_def\n"
        expected_script += "set ::env(STEP_DIR) {" + str(folder) + "}\nset ::env(DESIGN_NAME) {" + config["DESIGN_NAME"] + "}\n"
        expected_script += body[body.index(marker):] + "\nquit -noprompt\n"
        require(script.read_text() == expected_script, "retained control reader/deck script differs from pinned machinery")
        observed[label] = magic_findings(report)
        require(len(observed[label]) == record["raw_count"], "retained diagnostic count mismatch")
        for path in (lef, copied, script, report, folder / "magic.log", folder / "observation.json"):
            artifacts[str(path)] = sha(path)
    result = validate_obstruction_controls(findings, observed, plan, source_lef_sha256=sha(source))
    require(all(sha(p) == digest for p, digest in artifacts.items()), "retained diagnostic changed during validation")
    result.update(source_lef_sha256=sha(source), routed_def_sha256=sha(routed), control_artifacts=artifacts)
    return result


def obstruction_contact_attribution(findings, plan, geometry, layout, db):
    """Geometric attribution only. XY coincidence with Metal4 is not a short."""
    matched = wide_obstruction_geometry(findings, plan, reviewed_projections=True)
    units = Decimal(geometry["dbu_per_um"])
    via_layer = layout.find_layer(49, 0)  # pinned layers_def.drc: Via3 drawing
    vias = db.Region() if via_layer is None else db.Region(layout.top_cell().begin_shapes_rec(via_layer))
    result = []
    for finding, match in zip(findings, matched):
        _, box = finding
        macro = next(m for m in geometry["macros"] if m["instance"] == match["instance"])
        contacts = []
        for contact in macro["contacts"]:
            for key in ("neck_rect_dbu", "landing_rect_dbu"):
                rect = [Decimal(v) / units for v in contact[key]]
                overlap = (max(box[0], rect[0]), max(box[1], rect[1]), min(box[2], rect[2]), min(box[3], rect[3]))
                if overlap[0] <= overlap[2] and overlap[1] <= overlap[3]:
                    coordinates = [v / Decimal(str(layout.dbu)) for v in overlap]
                    require(all(v == int(v) for v in coordinates), "contact intersection not on GDS DBU")
                    region = db.Region(db.Box(*map(int, coordinates)))
                    contacts.append({"pin": contact["pin"], "net": contact["net"], "contact_layer": "Metal4",
                                     "region": key, "intersection_um": [str(v) for v in overlap],
                                     "relation": "area_overlap" if overlap[0] < overlap[2] and overlap[1] < overlap[3] else "boundary_touch",
                                     "via3_area_present": not (vias & region).is_empty()})
        result.append({**match, "contacts": contacts, "interpretation": "XY_only_not_electrical_connectivity"})
    return result


def disposition_magic(flow, plan, geometry_path, expected_count, *, baseline_dir=None):
    reports = list(flow.glob("*-magic-drc/reports/drc.magic.rpt"))
    require(len(reports) == 1, "missing raw Magic DRC report")
    findings = magic_findings(reports[0])
    require(len(findings) == expected_count, "Magic metric/raw report disagreement")
    if not findings:
        return {"status": "zero_findings", "raw_count": 0, "raw_report_sha256": sha(reports[0])}
    # The raw production DRC is DEF/LEF. Establish its intrinsic LEF diagnostics
    # independently, without changing the PDK, macro, or emitted project inputs.
    from librelane.common import get_script_dir
    config = load(flow / "resolved.json")
    name = plan["macros"][0]["module"]
    baseline = Path(baseline_dir) if baseline_dir is not None else flow / "magic-stock-abstract-baseline"
    require(not any(c in str(baseline) for c in "{}\n\r"), "unsafe baseline output path")
    baseline.mkdir()
    (baseline / "reports").mkdir()
    tech_lefs = set(config["TECH_LEFS"].values())
    require(len(tech_lefs) == 1, "unsupported baseline technology LEF selection")
    lefs = [*tech_lefs, *config["CELL_LEFS"], *config["MACROS"][name]["lef"]]
    require(all("{" not in p and "}" not in p and "\n" not in p for p in lefs), "unsafe baseline LEF path")
    body = (Path(get_script_dir()) / "magic/drc.tcl").read_text()
    marker = "set report_dir $::env(STEP_DIR)/reports"
    require(body.count(marker) == 1, "unsupported pinned Magic diagnostic script")
    script = "drc off\ncrashbackups disable\nlocking disable\n" + "\n".join("lef read {" + p + "}" for p in lefs)
    script += "\nset ::env(STEP_DIR) {" + str(baseline) + "}\nset ::env(DESIGN_NAME) {" + name + "}\n"
    script += body[body.index(marker):] + "\nquit -noprompt\n"
    script_path = baseline / "baseline.tcl"
    script_path.write_text(script)
    with (baseline / "magic.log").open("w") as log:
        result = subprocess.run(["magic", "-dnull", "-noconsole", "-rcfile", config["MAGICRC"], str(script_path)],
                                cwd=baseline, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode == 0, "stock abstract Magic baseline failed")
    baseline_report = baseline / "reports/drc.magic.rpt"
    try:
        disposition = classify_magic(findings, magic_findings(baseline_report), plan, load(geometry_path))
    except ValueError:
        disposition = wide_obstruction_counterfactual(flow, plan, findings, baseline / "wide-obstruction-diagnostic")
    disposition.update(raw_report_sha256=sha(reports[0]), baseline_report_sha256=sha(baseline_report),
                       baseline_script_sha256=sha(script_path), baseline_input_hashes={p: sha(p) for p in lefs})
    write(baseline / "disposition.json", disposition)
    return disposition


def check(project, pdk_root, *, evidence_output=None, magic_control_dir=None):
    manifest, plan = validate_plan(project)
    project, pdk_root = Path(project), Path(pdk_root)
    flow = project / "runs/asic"
    output = flow if evidence_output is None else Path(evidence_output)
    if evidence_output is not None:
        require(not output.resolve().is_relative_to(project.resolve()), "independent evidence output must be outside original project")
        require(output.is_dir() and not (output / "physical-checks.json").exists(), "fresh independent evidence output required")
    require(magic_control_dir is None or evidence_output is not None, "retained controls require separate evidence record")
    if magic_control_dir is not None:
        require(not output.resolve().is_relative_to(project.resolve().parent),
                "retained controls require output outside original run")
    top = manifest["top_module"]
    gds = flow / "final/gds" / (top + ".gds")
    gds_digest = sha(gds)
    metrics = load(flow / "final/metrics.json")
    resolved = load(flow / "resolved.json")
    for key in ("RUN_KLAYOUT_DRC", "RUN_MAGIC_DRC", "RUN_LVS", "RUN_KLAYOUT_STREAMOUT", "ERROR_ON_KLAYOUT_DRC",
                "ERROR_ON_ILLEGAL_OVERLAPS", "ERROR_ON_LVS_ERROR", "ERROR_ON_PDN_VIOLATIONS",
                "ERROR_ON_DISCONNECTED_PINS", "ERROR_ON_TR_DRC"):
        require(resolved.get(key) is True, "effective physical checker disabled/missing: " + key)
    require(resolved.get("PRIMARY_GDSII_STREAMOUT_TOOL") == "klayout" and resolved.get("MAGIC_DRC_USE_GDS") is False,
            "effective real-GDS/abstract diagnostic scope mismatch")
    require(resolved.get("MAGIC_EXT_USE_GDS") is False, "unexpected device extraction scope")
    require(resolved.get("STA_THREADS") == 1 and resolved.get("OPENROAD_THREADS") == 6 and
            resolved.get("KLAYOUT_DRC_THREADS") == 6 and resolved.get("KLAYOUT_XOR_THREADS") == 6, "effective worker budget mismatch")
    require(resolved.get("MAX_SLEW_VIOLATION_CORNERS") == ["*"] and resolved.get("MAX_CAP_VIOLATION_CORNERS") == ["*"],
            "all-corner electrical checker selection required")
    effective_deck_options(resolved.get("KLAYOUT_DRC_OPTIONS", {}), manifest["flow_configuration"]["KLAYOUT_DRC_OPTIONS"])
    import klayout.db as db
    layout = db.Layout()
    layout.read(str(gds))
    require([c.name for c in layout.top_cells()] == [top], "assembled GDS top mismatch")
    macro_names = {m["module"] for m in manifest["macro_contract"]["macros"]}
    for name in macro_names:
        cell = layout.cell(name)
        require(cell is not None and not cell.bbox().empty() and not cell.is_empty(), "real macro geometry missing: " + name)
        require(any(not cell.begin_shapes_rec(layer).at_end() for layer in layout.layer_indexes()),
                "macro has no real shapes: " + name)
    verify_stock_geometry(layout, manifest, pdk_root, db)
    geometry = geometry_evidence(flow, plan, layout, db)
    drc_steps = sorted(flow.glob("*-klayout-drc"))
    require(len(drc_steps) == 1, "exactly one KLayout DRC execution required")
    step = drc_steps[0]
    state = load(step / "state_in.json")
    checked_gds = Path(state["gds"])
    require(sha(checked_gds) == gds_digest, "DRC input differs from final real GDS")
    captured_log, deck_log = deck_logs(project, flow, checked_gds)
    report = step / "reports/drc.klayout.lyrdb"
    root = ET.parse(report).getroot()
    require(root.tag == "report-database", "invalid KLayout report database")
    require(root.find("items") is not None and len(root.find("items")) == 0, "KLayout real-GDS violations")
    json_report = step / "reports/drc.klayout.json"
    require(finite_count(load(json_report), "total") == 0, "KLayout JSON report violations")
    require(finite_count(metrics, "klayout__drc_error__count") == 0, "KLayout metric violations")
    # A warning-only upstream checker is never sufficient for physical acceptance.
    required_zero = ["route__drc_errors", "magic__illegal_overlap__count", "design__lvs_error__count",
                     "design__power_grid_violation__count", "design__critical_disconnected_pin__count", "antenna__violating__nets", "antenna__violating__pins"]
    for key in required_zero:
        require(finite_count(metrics, key) == 0, "physical/electrical violations: " + key)
    for base in ("design__max_slew_violation__count", "design__max_cap_violation__count"):
        for corner in manifest["flow_configuration"]["STA_CORNERS"]:
            key = base + "__corner:" + corner
            require(finite_count(metrics, key) == 0, "electrical violations: " + key)
    # Keep raw Magic counts. No unreviewed category or bounding-box-only waiver:
    # a new supply bridge can introduce a real short inside the macro footprint.
    magic_count = finite_count(metrics, "magic__drc_error__count")
    if magic_control_dir is None:
        magic_disposition = disposition_magic(flow, plan, geometry, magic_count)
    else:
        raw_magic, = flow.glob("*-magic-drc/reports/drc.magic.rpt")
        findings = magic_findings(raw_magic)
        require(len(findings) == magic_count, "Magic metric/raw report disagreement")
        magic_disposition = retained_obstruction_controls(flow, plan, findings, magic_control_dir)
        magic_disposition["raw_report_sha256"] = sha(raw_magic)
        magic_disposition["contact_attribution"] = obstruction_contact_attribution(findings, plan, load(geometry), layout, db)
    lvs = list(flow.glob("*-netgen-lvs/lvs_script.lvs"))
    require(len(lvs) == 1, "missing executed LVS script")
    cdl, powered, derived, derived_powered = validate_lvs_reader(lvs[0].parent, manifest, pdk_root)
    lvs_log = lvs[0].parent / "netgen-lvs.log"
    retained_log = output / "physical-checker-artifacts/drc-klayout.log"
    retained_log.parent.mkdir(exist_ok=True)
    retained_log.write_bytes(deck_log.read_bytes())
    require(sha(gds) == gds_digest and sha(checked_gds) == gds_digest, "real GDS changed during physical evidence validation")
    evidence = {"schema_version": 1, "checking_profile": plan["checking_profile"],
                "lvs_scope": plan["lvs_scope"], "status": "pass", "gds_sha256": gds_digest,
                "drc_report_sha256": sha(report), "lvs_script_sha256": sha(lvs[0]),
                "schematic_cdl_sha256": sha(cdl), "magic_raw_count": magic_count,
                "schematic_cdl_reader": "cmos5l_cdl_names_v1", "macro_boundary_pin_count": len(stock_lvs_ports()),
                "power_geometry_sha256": sha(geometry), "final_power_geometry": "retained_in_real_gds",
                "magic_disposition": magic_disposition, "internal_device_lvs": "not_verified",
                "resource_limits": {"workers": 6, "sta_workers": 1}}
    raw = [geometry, report, json_report, captured_log, step / "state_in.json", flow / "resolved.json", lvs[0], lvs_log,
           lvs[0].parent / "lvs-reader.json", derived, derived_powered, powered,
           flow / "final/nl" / (top + ".nl.v"), flow / "final/lef" / (top + ".lef")]
    require(all(p.is_file() for p in raw), "required physical raw artifact missing")
    raw += list(flow.glob("*-magic-drc/reports/*"))
    raw += list(flow.glob("*-magic-drc/_env.tcl"))
    raw += list(flow.glob("*-magic-drc/state_in.json"))
    raw += [Path(load(path)["def"]) for path in flow.glob("*-magic-drc/state_in.json")]
    raw += list(flow.glob("*-magic-spiceextraction/feedback*"))
    raw += list(flow.glob("*-netgen-lvs/reports/*"))
    raw += list((flow / "magic-stock-abstract-baseline").rglob("*"))
    extraction = list(flow.glob("*-magic-spiceextraction/feedback.txt"))
    require(len(extraction) == 1, "raw Magic extraction feedback required")
    evidence["artifacts"] = {str(p.relative_to(flow)): sha(p) for p in raw if p.is_file()}
    if evidence_output is None:
        evidence["artifacts"][str(retained_log.relative_to(flow))] = sha(retained_log)
    else:
        evidence.update(record_kind="independent_physical_recheck_not_provider_acceptance",
                        provider_acceptance=False, output_artifacts={str(retained_log.relative_to(output)): sha(retained_log)})
    write(output / "physical-checks.json", evidence)
    return evidence


def reviewed_timing_cell(name, module):
    """Only bridge the reviewed undriven specify reference wires.

    The four supplied reset-flop functional bodies are structurally identical
    across drive strengths. Compare the entire non-timing body, not a name prefix
    or merely the presence of three delayed_* strings. Other cells keep their
    supplied structural logic and may not hide procedural/delayed behavior.
    """
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", module, flags=re.S)
    specify_count = len(re.findall(r"\bspecify\b", clean))
    require(specify_count == len(re.findall(r"\bendspecify\b", clean)) and specify_count <= 1,
            "unsupported specify structure: " + name)
    functional = re.sub(r"\bspecify\b.*?\bendspecify\b", "", clean, flags=re.S)
    delayed = sorted(set(re.findall(r"\bdelayed_(\w+)\b", functional)))
    flops = {"sg13cmos5l_dfrbpq_1", "sg13cmos5l_dfrbpq_2", "sg13cmos5l_dfrbp_1", "sg13cmos5l_dfrbp_2"}
    if name in flops:
        dual = name in {"sg13cmos5l_dfrbp_1", "sg13cmos5l_dfrbp_2"}
        ports = "Q, Q_N, CLK, D, RESET_B" if dual else "Q, CLK, D, RESET_B"
        wires = "wire int_fwire_IQ, int_fwire_IQN, int_fwire_r; wire xcr_0;" if dual else "wire int_fwire_IQ, int_fwire_r, xcr_0;"
        expected = f"""module {name} ({ports});
          output {"Q, Q_N" if dual else "Q"}; input CLK, D, RESET_B;
          reg notifier; wire delayed_D, delayed_RESET_B, delayed_CLK;
          {wires}
          not (int_fwire_r, delayed_RESET_B); buf (xcr_0, 0);
          ihp_dff_r (int_fwire_IQ, notifier, delayed_CLK, delayed_D, int_fwire_r, xcr_0);
          buf (Q, int_fwire_IQ);
          {"not (int_fwire_IQN, int_fwire_IQ); buf (Q_N, int_fwire_IQN);" if dual else ""}
          endmodule"""
        normalize = lambda value: re.sub(r"\s+", "", value)
        require(normalize(functional) == normalize(expected), "unreviewed reset-flop functional body: " + name)
        require(delayed == ["CLK", "D", "RESET_B"], "unreviewed delayed reference pins: " + name)
    else:
        require(not delayed and not re.search(r"\b(?:reg|always|initial|notifier|ihp_\w+)\b|[$#`\"]", functional),
                "unreviewed nonstructural/delayed-reference cell: " + name)
    # Strip only the original specify region; preserve the supplied function text.
    # Current qualified module bodies contain no string literals or preprocessor
    # directives; comment words cannot impersonate timing delimiters.
    mask = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group()), module, flags=re.S)
    regions = list(re.finditer(r"\bspecify\b.*?\bendspecify\b", mask, re.S))
    require(len(regions) == specify_count, "unbalanced specify regions: " + name)
    body = module
    for region in reversed(regions):
        body = body[:region.start()] + body[region.end():]
    bypass = "\n".join("assign delayed_" + pin + " = " + pin + ";" for pin in delayed)
    return body.replace("endmodule", bypass + "\nendmodule"), delayed


def replay_models(project, pdk_root, netlist, output):
    """Derive only reviewed zero-delay cells; never rewrite the netlist or PDK."""
    project, pdk_root, output = Path(project), Path(pdk_root), Path(output)
    manifest = load(project / "manifest.json")
    timing = "timing_physical_qualification" in manifest and "macro_contract" not in manifest
    if timing:
        expected = dict(schema_version=1, resolution_policy="scalar-q-driver-cmos5l-physical-v2",
            analysis_corners="all_configured", analysis_stages=["synthesized_netlist", "post_pnr"],
            sequential_inventory="exact_declared_stages", max_buffer_hops=8,
            endpoint_map="::hc_pt7::checked_endpoints", trace_inventory="::hc_pt7::checked_traces")
        require(manifest["timing_physical_qualification"] == expected and
                manifest.get("timing_intent", {}).get("resolution_policy") == expected["resolution_policy"] and
                manifest.get("operation") == "Hardening" and manifest.get("target", {}).get("technology") == "ihp-sg13cmos5l",
                "unsupported macro-free timing replay profile")
        require(Path(netlist).resolve() == (project / "runs/asic/final/nl" / (manifest["top_module"] + ".nl.v")).resolve(),
                "timing replay requires the unchanged final implementation netlist")
        contract = {"models": []}
    else:
        contract = manifest["macro_contract"]["implementation_simulation"]
        require(contract["defines"] == ["FUNCTIONAL"], "functional SRAM model contract")
    pdk = pdk_root / "ihp-sg13cmos5l"
    sources = []
    for ref in contract["models"]:
        require(ref.startswith("pdk_dir::") and ".." not in Path(ref[9:]).parts, "model path")
        path = (pdk / ref[9:]).resolve(strict=True)
        require(path.is_relative_to(pdk_root.resolve()), "model escapes PDK")
        sources.append(path)
    references = load(project.parent / "preflight.json")["references"]
    recorded = {r.get("reference"): r["sha256"] for r in references}
    for ref, path in zip(contract["models"], sources):
        require(recorded.get(ref) == sha(path), "run-bound macro model changed: " + ref)
    vendor = pdk / "libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v"
    udp = vendor.with_name("sg13cmos5l_udp.v")
    if timing:
        require(all(path.resolve().is_relative_to(pdk_root.resolve()) for path in (vendor, udp)), "timing model source escapes pinned PDK")
        require(all(path.is_file() and path.stat().st_size <= 32 * 1024 * 1024 for path in
                    (vendor, udp, Path(netlist), project / "manifest.json", project.parent / "preflight.json")),
                "timing replay source exceeds32MiB or is missing")
    recorded_paths = {str(Path(r["path"]).resolve()): r["sha256"] for r in references if "path" in r}
    for path in (vendor, udp):
        require(recorded_paths.get(str(path.resolve())) == sha(path), "run-bound standard-cell model changed: " + str(path))
    before = {str(path): sha(path) for path in [project / "manifest.json", project.parent / "preflight.json", Path(netlist), vendor, udp] + sources}
    snapshots = {}
    if timing:
        snapshots = {path: Path(path).read_bytes() for path in before}
        require(all(hashlib.sha256(data).hexdigest() == before[path] for path, data in snapshots.items()),
                "timing replay source changed while snapshotting")
        require(json.loads(snapshots[str(project / "manifest.json")]) == manifest and
                json.loads(snapshots[str(project.parent / "preflight.json")])["references"] == references,
                "timing replay source metadata changed while snapshotting")
        require(all(hashlib.sha256(snapshots[str(path)]).hexdigest() == recorded_paths.get(str(path.resolve()))
                    for path in (vendor, udp)), "timing replay snapshot differs from run-bound supplied source")
        require(not any((output / name).exists() for name in ("stdcell-functional.v", "stdcell-vendor.v", "replay-models.json", "model-0.v")),
                "fresh timing replay output required")
    rtl = snapshots[str(Path(netlist))].decode("utf-8") if timing else Path(netlist).read_text()
    selection_rtl = re.sub(r"/\*.*?\*/|//[^\n]*", "", rtl, flags=re.S) if timing else rtl
    used = sorted(set(re.findall(r"\b(sg13cmos5l_\w+)\s+(?:\\\S+|\w+)\s*\(", selection_rtl)))
    require(used, "no mapped standard cells found")
    require(not timing or len(used) <= 4096, "timing replay used-cell bound exceeded")
    text = snapshots[str(vendor)].decode("utf-8") if timing else vendor.read_text()
    preamble = text[:text.index("// type:")] if "// type:" in text else ""
    transformed = [preamble, "`timescale 1ns/1ps\n// Reviewed zero-delay functional view; not SDF/timing evidence.\n"]
    adaptations = {}
    module_sources = {}
    for name in used:
        matches = re.findall(r"\bmodule\s+" + re.escape(name) + r"\s*\(.*?\bendmodule", text, re.S)
        require(len(matches) == 1, "unknown/nonunique vendor cell: " + name)
        if timing:
            body, delayed = reviewed_timing_cell(name, matches[0])
            transformed.append(body)
            adaptations[name] = delayed
            module_sources[name] = hashlib.sha256(matches[0].encode()).hexdigest()
            continue
        body = re.sub(r"\bspecify\b.*?\bendspecify\b", "", matches[0], flags=re.S)
        delayed = sorted(set(re.findall(r"\bdelayed_(\w+)\b", body)))
        if delayed:
            require(name == "sg13cmos5l_dfrbpq_1" and set(delayed) == {"CLK", "D", "RESET_B"},
                    "unreviewed delayed-reference cell: " + name)
        bypass = "\n".join("assign delayed_" + pin + " = " + pin + ";" for pin in delayed)
        transformed.append(body.replace("endmodule", bypass + "\nendmodule"))
        adaptations[name] = delayed
    functional = output / "stdcell-functional.v"
    functional.write_text("\n".join(transformed))
    retained = []
    for i, source in enumerate(sources + [udp]):
        target = output / ("model-" + str(i) + ".v")
        target.write_bytes(snapshots[str(source)] if timing else source.read_bytes())
        retained.append(str(target))
    (output / "stdcell-vendor.v").write_bytes(snapshots[str(vendor)] if timing else vendor.read_bytes())
    inventory = {"scope": "zero_delay_gate_functional", "models": retained + [str(functional)],
          "vendor_sha256": before[str(vendor)] if timing else sha(vendor), "functional_sha256": sha(functional), "adaptations": adaptations,
          "source_hashes": {str(p): before[str(p)] if timing else sha(p) for p in sources + [udp, vendor]},
          "retained_hashes": {Path(p).name: sha(p) for p in retained + [str(functional), str(output / "stdcell-vendor.v")]}}
    if timing:
        inventory.update(schema_version=2, profile="cmos5l_physical_timing_used_cells_v1",
            claim="zero_delay_digital_functional_only_not_vendor_timing_or_sdf_conformance",
            manifest_sha256=before[str(project / "manifest.json")], netlist_sha256=before[str(Path(netlist))],
            preflight_sha256=before[str(project.parent / "preflight.json")], vendor_module_sha256=module_sources,
            defines=["FUNCTIONAL", "SIM"])
        require(all(sha(path) == digest for path, digest in before.items()), "timing replay supplied source changed during adaptation")
    write(output / "replay-models.json", inventory)


def prepare_tt(work):
    """Adapt a run-local pinned precheck, preserving the original and patch receipt."""
    path = Path(work) / "precheck/precheck.py"
    original = path.read_text()
    needle = 'f"{PDK_ROOT}/{PDK_NAME}/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",\n'
    require(original.count(needle) == 1, "unsupported pinned TT CMOS5L invocation")
    replacement = needle + '        extra_vars={"threads": "6", "run_mode": "deep", "no_recommended": "false", "log": f"{REPORTS_PATH}/cmos5l-deck.log"},\n'
    path.write_text(original.replace(needle, replacement))
    write(Path(work).parent / "tt-adaptation.json", {"original_sha256": hashlib.sha256(original.encode()).hexdigest(),
          "adapted_sha256": sha(path), "profile": "cmos5l_real_gds_v1", "workers": 6})


def audit(project, pdk_root, output):
    """Read-only failed-run diagnostics, fresh output; never confer acceptance."""
    project, pdk_root, output = Path(project), Path(pdk_root), Path(output)
    output.mkdir()  # fresh output only; originals are never edited or relabeled
    manifest, plan = validate_plan(project)
    flow = project / "runs/asic"
    metrics, resolved = load(flow / "final/metrics.json"), load(flow / "resolved.json")
    report = {"schema_version": 1, "scope": "diagnostic_only_not_acceptance", "source_project": str(project),
              "checker_sha256": sha(__file__),
              "source_manifest_sha256": sha(project / "manifest.json"), "source_metrics_sha256": sha(flow / "final/metrics.json"),
              "checks": {}, "observed_metrics": metrics}

    def attempt(name, fn):
        try:
            value = fn()
            report["checks"][name] = {"status": "pass", "result": value}
        except Exception as error:
            report["checks"][name] = {"status": "fail", "error": str(error)}
        write(output / "audit.json", report)

    import klayout.db as db
    layout = db.Layout()
    gds = flow / "final/gds" / (manifest["top_module"] + ".gds")
    layout.read(str(gds))
    attempt("stock_geometry", lambda: verify_stock_geometry(layout, manifest, pdk_root, db))
    geometry = next(flow.glob("*-openroad-generatepdn/macro-power-geometry.json"))
    attempt("final_power_geometry", lambda: str(geometry_evidence(flow, plan, layout, db)))
    attempt("effective_deck_options", lambda: effective_deck_options(resolved["KLAYOUT_DRC_OPTIONS"], manifest["flow_configuration"]["KLAYOUT_DRC_OPTIONS"]))

    def drc():
        step = next(flow.glob("*-klayout-drc"))
        checked = Path(load(step / "state_in.json")["gds"])
        require(sha(checked) == sha(gds), "checked/final GDS mismatch")
        xml = ET.parse(step / "reports/drc.klayout.lyrdb").getroot()
        require(xml.tag == "report-database" and xml.find("items") is not None and len(xml.find("items")) == 0, "real-GDS DRC violations/report missing")
        require(finite_count(load(step / "reports/drc.klayout.json"), "total") == 0 and finite_count(metrics, "klayout__drc_error__count") == 0, "DRC report/metric disagreement")
        logs = deck_logs(project, flow, checked)
        return {"gds_sha256": sha(gds), "captured_log": str(logs[0]), "deck_log": str(logs[1])}

    attempt("real_gds_drc", drc)
    attempt("magic_disposition", lambda: disposition_magic(flow, plan, geometry, finite_count(metrics, "magic__drc_error__count"), baseline_dir=output / "magic-baseline"))
    lvs = next(flow.glob("*-netgen-lvs"))
    attempt("lvs_result", lambda: validate_lvs_result(lvs, manifest))
    attempt("lvs_reader_receipt", lambda: [str(p) for p in validate_lvs_reader(lvs, manifest, pdk_root)])
    def models():
        destination = output / "replay-models"
        destination.mkdir()
        replay_models(project, pdk_root, flow / "final/nl" / (manifest["top_module"] + ".nl.v"), destination)
        inventory = load(destination / "replay-models.json")
        return {"scope": inventory["scope"], "used_cell_types": sorted(inventory["adaptations"])}
    attempt("final_netlist_model_derivation", models)
    keys = ["route__drc_errors", "magic__illegal_overlap__count", "design__lvs_error__count", "design__power_grid_violation__count",
            "design__critical_disconnected_pin__count", "antenna__violating__nets", "antenna__violating__pins"]
    keys += [base + "__corner:" + corner for base in ("design__max_slew_violation__count", "design__max_cap_violation__count")
             for corner in manifest["flow_configuration"]["STA_CORNERS"]]
    for key in keys:
        def zero(key=key):
            value = finite_count(metrics, key)
            require(value == 0, "nonzero violation count: " + str(value))
            return value
        attempt(key, zero)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("check", "models", "tt", "audit"))
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    {"check": check, "models": replay_models, "tt": prepare_tt, "audit": audit}[args.operation](*args.paths)


if __name__ == "__main__":
    main()
