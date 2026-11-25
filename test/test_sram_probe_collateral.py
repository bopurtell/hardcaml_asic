"""SRAM probe collateral checks. No EDA; real PDK/LibreLane checks skip if absent.

Run: python3 -B -m unittest discover -s test -p 'test_sram_probe_*.py' -v
"""

from decimal import Decimal
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "test/fixtures/sram_flow_probe"
CONFIG = json.loads((PROBE / "config.json").read_text())
MACRO = "RM_IHPSG13_1P_256x16_c2_bm_bist"
PDK = ROOT / ".toolchain/pdk/ihp-sg13cmos5l"
EXPECTED = {
    "nom_fast_1p32V_m40C": ("fast_1p32V_m55C", "1.32", "-55"),
    "nom_slow_1p08V_125C": ("slow_1p08V_125C", "1.08", "125"),
    "nom_typ_1p20V_25C": ("typ_1p20V_25C", "1.20", "25"),
}


def local_path(view):
    return PDK / view.removeprefix("pdk_dir::")


def lef_rects(text):
    """Read layer-tagged RECTs, retaining exact integer DBU coordinates."""
    layer = None
    result = []
    for statement in text.split(";"):
        match = re.search(r"\bLAYER\s+(\w+)\s*$", statement)
        if match:
            layer = match[1]
        match = re.search(r"\bRECT\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", statement)
        if match:
            result.append((layer, tuple(int(Decimal(v) * 1000) for v in match.groups())))
    return result


def intersection(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def authored_bridges():
    return [(net, tuple(map(int, coords.split()))) for net, coords in re.findall(
        r"odb::dbSBox_create \$(vpwr|vgnd)_swire \$metal4 ([\d ]+) STRIPE",
        (PROBE / "pdn_cfg.tcl").read_text())]


def grid_rects(text):
    """Read long vertical Metal4 SPECIALNET stripes; exclude short hand bridges.

    This oracle is deliberately limited to the retained grid's explicit, even-width
    two-point paths. It does not interpret ordinary signal routing or vias.
    """
    body = text.split("SPECIALNETS ", 1)[1].split("END SPECIALNETS", 1)[0]
    result = {}
    for name, net in re.findall(r"^\s*- (\w+)\b(.*?);", body, re.M | re.S):
        rects = []
        for values in re.findall(
                r"(?:\+ ROUTED|NEW) Metal4 (\d+) \+ SHAPE STRIPE"
                r"\s+\( (\d+) (\d+) \) \( (\d+) (\d+) \)", net):
            width, x1, y1, x2, y2 = map(int, values)
            if x1 == x2 and abs(y2-y1) > 10000:
                if width % 2:
                    raise AssertionError("oracle requires even DBU stripe widths")
                rects.append((x1-width//2, min(y1, y2), x1+width//2, max(y1, y2)))
        result[name.lower()] = rects
    return result


class GridClearanceTest(unittest.TestCase):
    # Local rectangles independently transcribed from power-01's routed DEF,
    # 44-openroad-detailedrouting/tt_um_sram_probe.def, SPECIALNETS. DBU=1 nm.
    GRID = {
        "vpwr": [(111830, 3560, 113930, 80520), (111830, 200260, 113930, 707080),
                 (61830, 3560, 63930, 80520)],
        "vgnd": [(115930, 3560, 118030, 80520), (115930, 200260, 118030, 707080),
                 (65930, 3560, 68030, 80520)],
    }

    def check_access(self, net, bridge, grid):
        # Minimum M4 width=0.20 um; avoid a sub-minimum overlap to the same-net
        # grid. This is a geometry check, not an IR/EM adequacy claim.
        self.assertGreaterEqual(min(bridge[2]-bridge[0], bridge[3]-bridge[1]), 200)
        self.assertGreaterEqual((bridge[2]-bridge[0]) * (bridge[3]-bridge[1]), 144000)
        contacts = [intersection(bridge, stripe) for stripe in grid[net]]
        self.assertTrue(any(w >= 200 and h >= 200 for w, h in contacts), contacts)
        opposite = "vgnd" if net == "vpwr" else "vpwr"
        for stripe in grid[opposite]:
            w, h = intersection(bridge, stripe)
            dx, dy = max(0, -w), max(0, -h)
            # Conservatively require the stronger M4.e spacing even at corners:
            # 0.24 um for wide lines with >1 um parallel run (M4.b is 0.21).
            self.assertGreaterEqual(dx*dx + dy*dy, 240*240,
                                    ("opposite-net clearance", net, bridge, stripe))

    def test_authored_bridges_have_grid_access_and_clearance(self):
        for net, bridge in authored_bridges():
            with self.subTest(net=net, bridge=bridge):
                self.check_access(net, bridge, self.GRID)

    def test_oracle_rejects_power01_right_edge(self):
        for bridge in ((113700, 79520, 115800, 81000),
                       (113700, 197660, 115800, 201310)):
            with self.subTest(bridge=bridge), self.assertRaises(AssertionError):
                self.check_access("vpwr", bridge, self.GRID)

    def test_real_retained_grid_oracle(self):
        path = PROBE / "runs/pt0-power-01/inputs/runs/power-01/44-openroad-detailedrouting/tt_um_sram_probe.def"
        if not path.is_file():
            self.skipTest("retained power-01 routed DEF unavailable; representative oracle still runs")
        text = path.read_text()
        self.assertRegex(text, r"UNITS\s+DISTANCE\s+MICRONS\s+1000\s*;")
        grid = grid_rects(text)
        # Verify independently transcribed local geometry against the real grid.
        for net, rects in self.GRID.items():
            for rect in rects:
                self.assertIn(rect, grid[net])
        for net, bridge in authored_bridges():
            self.check_access(net, bridge, grid)
        for bridge in ((113700, 79520, 115800, 81000),
                       (113700, 197660, 115800, 201310)):
            with self.assertRaises(AssertionError):
                self.check_access("vpwr", bridge, grid)

    def test_real_metal4_width_and_spacing_rules(self):
        path = PDK / "libs.ref/sg13cmos5l_stdcell/lef/sg13cmos5l_tech.lef"
        if not path.is_file():
            self.skipTest("PDK technology LEF unavailable")
        layer = path.read_text().split("LAYER Metal4\n", 1)[1].split("END Metal4", 1)[0]
        self.assertRegex(layer, r"WIDTH\s+0\.20\s*;")
        self.assertRegex(layer, r"AREA\s+0\.144\s*;")
        self.assertRegex(layer, r"WIDTH 0\.39\s+0\.21\s+0\.24\s+0\.24")


class CornerTest(unittest.TestCase):
    def test_exact_single_match_and_no_fallback(self):
        views = CONFIG["MACROS"][MACRO]["lib"]
        self.assertEqual(set(views), set(EXPECTED))
        for corner, (suffix, _, _) in EXPECTED.items():
            # LibreLane collects ALL matching entries, not first/most-specific.
            matches = [v for pattern, values in views.items()
                       if fnmatch.fnmatchcase(corner, pattern) for v in values]
            self.assertEqual(matches, [f"pdk_dir::libs.ref/sg13cmos5l_sram/lib/{MACRO}_{suffix}.lib"])
        for unknown in ("nom_fast_1p32V_m55C", "nom_typ_1p20V_85C", "unknown"):
            self.assertFalse(any(fnmatch.fnmatchcase(unknown, p) for p in views))

    def test_real_liberty_operating_conditions(self):
        for corner, (_, voltage, temperature) in EXPECTED.items():
            path = local_path(CONFIG["MACROS"][MACRO]["lib"][corner][0])
            if not path.is_file():
                self.skipTest(f"real PDK Liberty unavailable: {path}")
            text = path.read_text()
            self.assertRegex(text, rf"cell\s*\(\s*{MACRO}\s*\)")
            for key, expected in (("nom_voltage", voltage), ("nom_temperature", temperature)):
                actual = re.search(rf"\b{key}\s*:\s*([-\d.]+)", text)
                self.assertIsNotNone(actual, key)
                self.assertEqual(Decimal(actual[1]), Decimal(expected))

    def test_installed_librelane_collects_all_matches(self):
        python = ROOT / ".toolchain/.venv/bin/python"
        if not python.is_file():
            self.skipTest("installed LibreLane interpreter unavailable")
        script = '''import json, sys
from librelane.common import Toolbox
views = json.loads(sys.argv[1])
toolbox = Toolbox(sys.argv[2])
result = {c: toolbox.filter_views({}, views, c) for c in views}
duplicate = dict(views, **{"*": ["fallback.lib"]})
result["with_fallback"] = toolbox.filter_views({}, duplicate, "nom_fast_1p32V_m40C")
print(json.dumps(result))
'''
        views = CONFIG["MACROS"][MACRO]["lib"]
        with tempfile.TemporaryDirectory(prefix="sram-probe-corners-") as tmp:
            result = subprocess.run([str(python), "-B", "-c", script, json.dumps(views), tmp],
                                    text=True, capture_output=True, timeout=30, check=True)
        loaded = json.loads(result.stdout)
        self.assertEqual({k: loaded[k] for k in views}, views)
        self.assertEqual(loaded["with_fallback"], views["nom_fast_1p32V_m40C"] + ["fallback.lib"])


class GeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lef = local_path(CONFIG["MACROS"][MACRO]["lef"][0])
        if not lef.is_file():
            raise unittest.SkipTest(f"real macro LEF unavailable: {lef}")
        text = lef.read_text()
        if not re.search(r"\bORIGIN\s+0\s+0\s*;", text):
            raise AssertionError("geometry oracle requires zero LEF origin")
        instance = CONFIG["MACROS"][MACRO]["instances"]["memory.sram"]
        if instance["orientation"] != "R0":
            raise AssertionError("geometry oracle requires R0 placement")
        x, y = (int(Decimal(str(v)) * 1000) for v in instance["location"])

        def placed(body):
            return [(a+x, b+y, c+x, d+y) for layer, (a, b, c, d) in lef_rects(body)
                    if layer == "Metal4"]

        cls.pins = {name: placed(body) for name, body in
                    re.findall(r"\bPIN\s+(\S+)\s*\n(.*?)\n\s*END\s+\1", text, re.S)}
        cls.obstructions = placed(text.split("\n  OBS", 1)[1])
        cls.bridges = authored_bridges()

    def check_bridge(self, bridge, pin, minimum_contact):
        contacts = [intersection(bridge, rect) for rect in self.pins[pin]]
        # Edge abutment is intentional at the bottom; a corner touch is not contact.
        self.assertTrue(any(w >= minimum_contact and h >= 0 for w, h in contacts), contacts)
        for obs in self.obstructions:
            w, h = intersection(bridge, obs)
            self.assertFalse(w > 0 and h > 0, ("obstruction overlap", bridge, obs))
        other_pins = ("VSS!",) if pin != "VSS!" else ("VDD!", "VDDARRAY!")
        for other in other_pins:
            for rect in self.pins[other]:
                w, h = intersection(bridge, rect)
                self.assertFalse(w > 0 and h >= 0, ("opposite-net contact", bridge, rect))

    def test_bridges_contact_real_pins_without_obstruction_or_short(self):
        self.assertEqual([net for net, _ in self.bridges], ["vpwr", "vpwr", "vgnd"])
        for (_, bridge), pin, contact in zip(self.bridges, ("VDD!", "VDDARRAY!", "VSS!"), (1950, 1950, 1530)):
            with self.subTest(pin=pin):
                self.check_bridge(bridge, pin, contact)

    def test_oracle_rejects_original_upper_bridge(self):
        with self.assertRaises(AssertionError):
            self.check_bridge((111830, 197660, 113930, 201310), "VDDARRAY!", 2100)

    def test_floorplan_uses_geometry_oracle_dbu_scale(self):
        template = Path(CONFIG["FP_DEF_TEMPLATE"])
        if not template.is_file():
            self.skipTest("TT floorplan template unavailable")
        self.assertRegex(template.read_text(), r"UNITS\s+DISTANCE\s+MICRONS\s+1000\s*;")


if __name__ == "__main__":
    unittest.main()
