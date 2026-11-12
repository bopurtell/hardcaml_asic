"""Physical evidence policy fixtures. No EDA or vendor tools are launched."""
import copy
from decimal import Decimal as D
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import types

spec = importlib.util.spec_from_file_location("physical_runtime", sys.argv.pop(1))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
report_consumer = str(Path(sys.argv.pop(1)).resolve())
collect_consumer = str(Path(sys.argv.pop(1)).resolve())


class PhysicalPolicy(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_cdl_reader_is_injective_naming_only_and_preserves_other_logic(self):
        source = "* stock A<0> comment preserved\n.SUBCKT SRAM A<0> VDD! VSS!\nMN0 A<0> VDD! VSS! VSS! nmos w=300n l=130n\n.ENDS\n"
        adapted, changes = runtime.normalize_cdl(source)
        self.assertIn("* stock A<0> comment preserved", adapted)
        self.assertIn("MN0 A[0] VDD VSS VSS nmos w=300n l=130n", adapted)
        self.assertEqual(changes["A<0> -> A[0]"], 2)
        for bad in (".SUBCKT SRAM A<0> A[0]\n.ENDS\n", ".SUBCKT SRAM VDD! VDD\n.ENDS\n", ".SUBCKT SRAM VDD! vdd\n.ENDS\n"):
            with self.assertRaisesRegex(ValueError, "noninjective"):
                runtime.normalize_cdl(bad)
        macro = {"module": "SRAM", "instance": "ram"}
        original = r"SRAM ram (.\VDD! (power), .\VDDARRAY! (array), .\VSS! (ground), .A_ADDR({n7,n6,n5,n4,n3,n2,n1,n0}));" + "\nordinary_cell other (.VDD(power),.A(data));"
        view, _ = runtime.normalize_powered_verilog(original, [macro])
        self.assertIn(".VDDARRAY(array)", view)
        self.assertIn(".A_ADDR({n7,n6,n5,n4,n3,n2,n1,n0})", view)
        self.assertTrue(view.endswith("ordinary_cell other (.VDD(power),.A(data));"))
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            runtime.normalize_powered_verilog(original.replace(r".\VDD! (power),", ""), [macro])

    def test_lvs_match_line_cannot_hide_missing_macro_or_pin_mismatches(self):
        name = "RM_IHPSG13_1P_256x16_c2_bm_bist"
        manifest = {"macro_contract": {"macros": [{"module": name}]}}
        pins = sorted(runtime.stock_lvs_ports())
        stats = [{"pins": [pins, pins]},
                 {"name": ["top", "top"], "devices": [[[name, 1], ["buffer", 2]], [[name, 1], ["buffer", 2]]],
                  "nets": [322, 322], "pins": [["clk"], ["clk"]]}]
        runtime.validate_lvs_statistics(stats, manifest)
        for mutation in (lambda s: s[-1].update(devices=[[["buffer", 2]], [["buffer", 2]]]),
                         lambda s: s[0].update(pins=[["A[0]"], ["proxyA[0]"]]),
                         lambda s: s[0].update(pins=[["(no matching pin)"], ["A[0]"]]),
                         lambda s: s.pop(0),
                         lambda s: s[-1].update(badnets=[{"actual": "short"}]),
                         lambda s: s[-1].update(nets=[322, 321])):
            bad = copy.deepcopy(stats)
            mutation(bad)
            with self.assertRaises(ValueError):
                runtime.validate_lvs_statistics(bad, manifest)

    def test_obstruction_counterfactual_is_diagnostic_and_marker_specific(self):
        lef = "PIN signal\n  LAYER Metal3 ;\nEND signal\n  OBS\n    LAYER Metal3 ;\n      RECT 0 0 236.8 118.78 ;\n    LAYER Metal4 ;\n      RECT 0 0 4 118.78 ;\n  END\n"
        derived = runtime.without_m3_obstruction(lef)
        self.assertIn("PIN signal\n  LAYER Metal3 ;", derived)
        self.assertIn("    LAYER Metal4 ;\n      RECT 0 0 4 118.78 ;", derived)
        with self.assertRaises(ValueError):
            runtime.without_m3_obstruction(lef.replace("236.8", "100"))
        plan = {"macros": [{"instance": "ram", "location_um": [40.0, 81.0], "halo_um": 2.0}]}
        marker = (runtime.M3_OBS_RULE, tuple(map(D, ["93.700", "80.540", "142.390", "80.740"])))
        self.assertEqual(runtime.classify_m3_obstruction([marker], [], plan)["raw_count"], 1)
        for findings, remaining in (([("different rule", marker[1])], []),
                                    ([(marker[0], tuple(map(D, [90, 82, 140, 83])))], []),
                                    ([marker], [marker])):
            with self.assertRaises(ValueError):
                runtime.classify_m3_obstruction(findings, remaining, plan)

    def test_m2_m3_wide_obstruction_controls_preserve_narrow_obs_and_pin_views(self):
        lef = ("PIN data\n  LAYER Metal2 ;\n  RECT 1 0 2 0.26 ;\nEND data\n"
               "  OBS\n    LAYER Metal2 ;\n      RECT 0.1 1 0.3 118.7 ;\n"
               "    LAYER Metal2 SPACING 0.21 ;\n      RECT 0 0.52 236.8 118.78 ;\n"
               "      RECT 4 0 9 118.78 ;\n    LAYER Metal3 ;\n      RECT 0 0 236.8 118.78 ;\n"
               "    LAYER Metal4 ;\n      RECT 0 0 4 118.78 ;\n  END\n")
        m2 = runtime.without_wide_obstructions(lef, ["Metal2"])
        self.assertEqual(m2, lef.replace("      RECT 0 0.52 236.8 118.78 ;\n", ""))
        both = runtime.without_wide_obstructions(lef, ["Metal2", "Metal3"])
        self.assertIn("PIN data\n  LAYER Metal2 ;", both)
        self.assertIn("RECT 0.1 1 0.3 118.7", both)
        self.assertIn("RECT 4 0 9 118.78", both)
        self.assertIn("LAYER Metal4", both)
        self.assertNotIn("RECT 0 0.52 236.8 118.78", both)
        for bad in (lef.replace("0 0.52 236.8", "0 0.53 236.8"), lef.replace("      RECT 0 0.52 236.8 118.78 ;", "      RECT 0 0.52 236.8 118.78 ;\n      RECT 0 0.52 236.8 118.78 ;")):
            with self.assertRaises(ValueError):
                runtime.without_wide_obstructions(bad, ["Metal2"])

    def test_m2_sideband_projection_and_layer_controls_reject_integration_errors(self):
        plan = {"macros": [{"instance": "ram", "location_um": [40.0, 81.0], "halo_um": 2.0}]}
        m2 = [(runtime.M2_OBS_RULE, tuple(map(D, box))) for box in
              (["39.400", "81.520", "39.460", "202.295"], ["277.340", "81.520", "277.400", "209.785"])]
        m3 = [(runtime.M3_OBS_RULE, tuple(map(D, ["137.860", "80.540", "157.690", "80.740"])))]
        findings = m2 + m3
        controls = {"stock": findings, "without-Metal2": m3, "without-Metal3": m2, "without-combined": []}
        # The alternate rendering must be exact, not a broad rule-name match.
        controls["stock"] = [(rule.replace("< 120", "< 0.6um"), box) for rule, box in findings]
        result = runtime.validate_obstruction_controls(findings, controls, plan)
        self.assertEqual(result["raw_count"], 3)
        self.assertEqual(result["accepted_count"], 3)
        for changed in ({"without-Metal2": []}, {"without-combined": m3}, {"stock": m2}):
            with self.assertRaises(ValueError):
                runtime.validate_obstruction_controls(findings, dict(controls, **changed), plan)
        shifted = (m2[0][0], (m2[0][1][0] + D("0.005"), *m2[0][1][1:]))
        with self.assertRaisesRegex(ValueError, "does not reproduce"):
            runtime.validate_obstruction_controls(findings, dict(controls, stock=[shifted, *findings[1:]]), plan)
        missing = dict(controls)
        del missing["without-Metal3"]
        with self.assertRaisesRegex(ValueError, "complete layer-specific"):
            runtime.validate_obstruction_controls(findings, missing, plan)
        for box in (["40.100", "81.520", "40.160", "202.295"],  # inside SRAM
                    ["39.990", "81.520", "40.050", "202.295"],  # crosses boundary
                    ["39.300", "81.520", "39.360", "202.295"],  # outside 0.6um influence
                    ["277.340", "81.520", "277.400", "209.790"]):  # exceeds rule projection
            with self.assertRaises(ValueError):
                runtime.classify_wide_obstructions([(runtime.M2_OBS_RULE, tuple(map(D, box)))], [], plan)
        for rule in ("Metal2 short (M2.f)", runtime.M2_OBS_RULE.replace("120", "121"), "Illegal overlap"):
            with self.assertRaises(ValueError):
                runtime.classify_wide_obstructions([(rule, m2[0][1])], [], plan)

    def test_deck_false_string_coercion_is_exact_and_coverage_is_logged(self):
        declared = {"no_feol": "false", "log": "drc-klayout.log", "threads": 6}
        for value in (0, False, "false"):
            runtime.effective_deck_options(dict(declared, no_feol=value), declared)
        for value in (1, True, "true", "0"):
            with self.assertRaises(ValueError):
                runtime.effective_deck_options(dict(declared, no_feol=value), declared)

    def test_final_public_lef_must_match_selected_power_geometry(self):
        text = "PIN VPWR\n USE POWER ;\n PORT\n LAYER Metal4 ;\n RECT 10.000 3.560 12.100 707.080 ;\n END\nEND VPWR\n"
        pins = [{"net": "VPWR", "rect_dbu": [10000, 3560, 12100, 707080]}]
        runtime.validate_public_lef(text, pins, 1000)
        for bad in (text.replace("Metal4", "Metal3"), text.replace("10.000", "10.005"), text.replace("USE POWER", "USE SIGNAL")):
            with self.assertRaises(ValueError):
                runtime.validate_public_lef(bad, pins, 1000)

    def test_exact_baseline_is_not_blanket_waiver(self):
        plan = {"macros": [{"instance": "ram", "location_um": [40.0, 81.0]}]}
        geometry = {"dbu_per_um": 1000, "macros": [{"instance": "ram", "contacts": [
            {"minimum_clearance_dbu": 600, "neck_rect_dbu": [50000, 81000, 52000, 83000],
             "landing_rect_dbu": [50000, 80000, 52000, 82000]}]}]}
        baseline = [("exact stock abstract rule", tuple(map(D, [20, 30, 21, 31])))]
        finding = [(baseline[0][0], tuple(map(D, [60, 111, 61, 112])))]
        disposition = runtime.classify_magic(finding, baseline, plan, geometry)
        self.assertEqual(disposition["accepted_count"], 1)
        for bad in ([('different rule', finding[0][1])],
                    [(finding[0][0], tuple(map(D, [61, 111, 62, 112])))],
                    finding * 2):
            with self.assertRaisesRegex(ValueError, "unclassified/integration-sensitive"):
                runtime.classify_magic(bad, baseline, plan, geometry)
        # A baseline match at a bridge or spanning a macro edge is still rejected.
        for local, absolute in (([10, 1, 11, 2], [50, 82, 51, 83]),
                                ([0, 30, 1, 31], [40, 111, 41, 112])):
            with self.assertRaisesRegex(ValueError, "unclassified/integration-sensitive"):
                runtime.classify_magic([("rule", tuple(map(D, absolute)))],
                                       [("rule", tuple(map(D, local)))], plan, geometry)

    def test_missing_nonfinite_fractional_negative_metrics_fail(self):
        for value in (None, True, "0", float("nan"), float("inf"), -1, 0.5):
            with self.assertRaises(ValueError):
                runtime.finite_count({"metric": value}, "metric")
        self.assertEqual(runtime.finite_count({"metric": 0}, "metric"), 0)

    def test_actual_native_acceptance_requires_electrical_and_replay(self):
        metric = lambda value: {"value": value, "corner": "nom_typ_1p20V_25C", "unavailable_reason": None}
        context = {"run_id": "physical", "build_identity": "bundle", "status": "completed",
                   "requested_stage": "full", "completed_stage": "full", "physical_checks_sha256": "recorded"}
        results = {"run_id": "physical", "build_identity": "bundle", "process_status": "completed",
                   "requested_stage": "full", "completed_stage": "full", "errors": [],
                   "timing_goal": "pass", "timing_unconstrained_modes": [],
                   "physical_profile": "cmos5l_real_gds_v1", "physical_evidence": {
                       "status": "pass", "lvs_scope": "abstract_macro_boundary_with_schematic_cdl"},
                   "metrics": {"mapped_area": metric(100), "mapped_cells": metric(10),
                               "setup_slack": metric(1), "hold_slack": metric(1)},
                   "synthesis_checks": {k: metric(0) for k in ("unmapped_instances", "synthesis_errors", "inferred_latches")},
                   "checks": {k: {"status": "pass"} for k in ("drc", "lvs", "antenna", "max_slew", "max_cap")},
                   "postchecks": [{"status": "completed", "checks": {"precheck": "pass", "gate_level": "pass"}}]}

        def accepted(value):
            result = subprocess.run([report_consumer], input=json.dumps({"context": context, "results": value}),
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)["accepted"]

        self.assertTrue(accepted(results))
        for key in ("max_slew", "max_cap"):
            for status in ("fail", "unknown"):
                bad = copy.deepcopy(results)
                bad["checks"][key]["status"] = status
                self.assertFalse(accepted(bad))
            bad = copy.deepcopy(results)
            del bad["checks"][key]
            self.assertFalse(accepted(bad))
        for key in ("physical_profile", "physical_evidence", "postchecks"):
            bad = copy.deepcopy(results)
            del bad[key]
            self.assertFalse(accepted(bad))
        bad = copy.deepcopy(results)
        bad["postchecks"][0]["checks"]["gate_level"] = "fail"
        self.assertFalse(accepted(bad))

    def test_collection_retains_magic_counts_and_checks_disposition_integrity(self):
        run = self.root / "run"
        project = run / "project"
        flow = project / "runs/asic"
        (flow / "final/gds").mkdir(parents=True)
        gds = flow / "final/gds/top.gds"
        gds.write_text("assembled GDS fixture")
        manifest = {"top_module": "top", "macro_contract": {"execution": "fresh_full"},
                    "flow_configuration": {"STA_CORNERS": ["nom_typ_1p20V_25C"]}}
        manifest_path = project / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        helper = project / "physical-runtime.py"
        helper.write_text("recorded checker fixture")
        raw = flow / "raw/magic.rpt"
        raw.parent.mkdir()
        raw.write_text("five original abstract findings")
        evidence = {"status": "pass", "gds_sha256": runtime.sha(gds),
                    "artifacts": {"raw/magic.rpt": runtime.sha(raw)},
                    "magic_raw_count": 5, "magic_disposition": {"status": "dispositioned_stock_abstract",
                    "accepted_count": 5, "unclassified_count": 0}}
        evidence_path = run / "physical-checks.json"
        evidence_path.write_text(json.dumps(evidence))
        context = {"run_id": "run", "build_identity": "bundle", "status": "completed", "requested_stage": "full",
                   "completed_stage": "full", "outputs": {"flow": "project/runs/asic"},
                   "manifest_sha256": runtime.sha(manifest_path), "physical_checker_sha256": runtime.sha(helper),
                   "physical_checks_sha256": runtime.sha(evidence_path)}
        (run / "run.json").write_text(json.dumps(context))
        rows = {"route__drc_errors": 0, "klayout__drc_error__count": 0, "magic__drc_error__count": 5,
                "magic__illegal_overlap__count": 0, "design__lvs_error__count": 0,
                "antenna__violating__nets": 0, "antenna__violating__pins": 0,
                "design__max_slew_violation__count__corner:nom_typ_1p20V_25C": 0,
                "design__max_cap_violation__count__corner:nom_typ_1p20V_25C": 0}
        metrics = flow / "final/metrics.csv"
        metrics.write_text("\n".join(f"{k},{v}" for k, v in rows.items()))

        def collect():
            result = subprocess.run([collect_consumer], input=json.dumps({"run_dir": str(run)}),
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)

        result = collect()
        self.assertEqual(result["checks"]["drc"]["status"], "pass")
        self.assertEqual(result["checks"]["drc"]["raw"]["magic__drc_error__count"], "5")
        self.assertEqual(result["physical_evidence"]["magic_raw_count"], 5)
        self.assertEqual(result["checks"]["max_slew"]["status"], "pass")
        raw.write_text("changed after physical checking")
        result = collect()
        self.assertTrue(any("physical evidence changed" in e for e in result["errors"]))
        self.assertEqual(result["checks"]["drc"]["status"], "fail")
        raw.write_text("five original abstract findings")
        rows["design__max_slew_violation__count__corner:nom_typ_1p20V_25C"] = 1
        metrics.write_text("\n".join(f"{k},{v}" for k, v in rows.items()))
        self.assertEqual(collect()["checks"]["max_slew"]["status"], "fail")

    def test_real_report_gds_cdl_evidence_seams_and_negatives(self):
        project, pdk = self.root / "run/project", self.root / "pdk"
        flow = project / "runs/asic"
        plan = {"schema_version": 1, "strategy": "cmos5l_m4_edge_access_v1", "plan_file": "physical/macro-plan.json",
                "pdn_tcl": "physical/pdn.tcl", "checking_profile": "cmos5l_real_gds_v1",
                "lvs_scope": "abstract_macro_boundary_with_schematic_cdl", "macros": []}
        name = "RM_IHPSG13_1P_256x16_c2_bm_bist"
        ref = "pdk_dir::libs.ref/sram.cdl"
        cdl = pdk / "ihp-sg13cmos5l/libs.ref/sram.cdl"
        cdl.parent.mkdir(parents=True)
        cdl.write_text(f".SUBCKT {name} A_ADDR<0> VDD! VDDARRAY! VSS!\n.ENDS\n")

        def save(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))

        save(project / plan["plan_file"], plan)
        (project / plan["pdn_tcl"]).write_text("# canonical generator fixture")
        config = {"EXTRA_SPICE_MODELS": [ref], "STA_CORNERS": ["nom_typ_1p20V_25C"], "KLAYOUT_DRC_OPTIONS": {"threads": 6}}
        manifest = {"top_module": "top", "flow_configuration": config, "macro_contract": {
            "physical_plan": plan, "macros": [{"module": name, "instance": "ram"}]}, "files": [
                {"path": key, "sha256": runtime.sha(project / key)} for key in (plan["plan_file"], plan["pdn_tcl"])]}
        save(project / "manifest.json", manifest)
        gds = flow / "final/gds/top.gds"
        gds.parent.mkdir(parents=True)
        gds.write_bytes(b"assembled real-GDS fixture")
        (flow / "final/nl").mkdir()
        (flow / "final/nl/top.nl.v").write_text("final mapped gates")
        (flow / "final/lef").mkdir()
        (flow / "final/lef/top.lef").write_text("final LEF retained fixture")
        drc = flow / "58-klayout-drc"
        save(drc / "state_in.json", {"gds": str(gds)})
        report = drc / "reports/drc.klayout.lyrdb"
        report.parent.mkdir()
        report.write_text("<report-database><items/></report-database>")
        save(drc / "reports/drc.klayout.json", {"total": 0})
        log_text = "Starting running IHP-SG13CMOS5L KLayout DRC runset on " + str(gds) + "\n"
        log_text += "\n".join(s + " enabled: true" for s in ("FEOL", "BEOL", "OFFGRID", "ANGLE", "PIN", "FORBIDDEN", "RECOMMENDED"))
        log_text += "\nPreCheck DRC enabled: false\n"
        (drc / "klayout-drc.log").write_text(log_text)
        (project / "drc-klayout.log").write_text(log_text)
        lvs = flow / "62-netgen-lvs"
        lvs.mkdir()
        script = lvs / "lvs_script.lvs"
        powered = flow / "source.pnl.v"
        powered.write_text(name + r" ram (.\VDD! (VPWR), .\VDDARRAY! (VPWR), .\VSS! (VGND));")
        derived, _ = runtime.prepare_lvs_inputs(project, cdl, powered, lvs)
        script.write_text(f"readnet spice {derived} $circuit2\nlvs -blackbox -json\n")
        (lvs / "netgen-lvs.log").write_text(f"Reading SPICE netlist file '{derived}'...")
        extraction = flow / "60-magic-spiceextraction/feedback.txt"
        extraction.parent.mkdir()
        extraction.write_text("")
        raw_magic = flow / "57-magic-drc/reports/drc.magic.rpt"
        raw_magic.parent.mkdir(parents=True)
        raw_magic.write_text("raw findings retained")
        geometry = flow / "12-openroad-generatepdn/macro-power-geometry.json"
        save(geometry, {"schema_version": 1})
        resolved = {key: True for key in ("RUN_KLAYOUT_DRC", "RUN_MAGIC_DRC", "RUN_LVS", "RUN_KLAYOUT_STREAMOUT",
                    "ERROR_ON_KLAYOUT_DRC", "ERROR_ON_ILLEGAL_OVERLAPS", "ERROR_ON_LVS_ERROR", "ERROR_ON_PDN_VIOLATIONS",
                    "ERROR_ON_DISCONNECTED_PINS", "ERROR_ON_TR_DRC")}
        resolved.update(PRIMARY_GDSII_STREAMOUT_TOOL="klayout", MAGIC_DRC_USE_GDS=False, MAGIC_EXT_USE_GDS=False,
                        STA_THREADS=1, OPENROAD_THREADS=6, KLAYOUT_DRC_THREADS=6, KLAYOUT_XOR_THREADS=6,
                        MAX_SLEW_VIOLATION_CORNERS=["*"], MAX_CAP_VIOLATION_CORNERS=["*"],
                        KLAYOUT_DRC_OPTIONS=config["KLAYOUT_DRC_OPTIONS"])
        save(flow / "resolved.json", resolved)
        metrics = {key: 0 for key in ("klayout__drc_error__count", "route__drc_errors", "magic__illegal_overlap__count",
                   "design__lvs_error__count", "design__power_grid_violation__count", "design__critical_disconnected_pin__count",
                   "antenna__violating__nets", "antenna__violating__pins", "magic__drc_error__count",
                   "design__max_slew_violation__count__corner:nom_typ_1p20V_25C",
                   "design__max_cap_violation__count__corner:nom_typ_1p20V_25C")}
        save(flow / "final/metrics.json", metrics)
        # Only layout API is simulated; the report/hash/CDL/electrical validators
        # execute against real independent fixture files.
        cell = types.SimpleNamespace(name="top", bbox=lambda: types.SimpleNamespace(empty=lambda: False),
                                     is_empty=lambda: False, begin_shapes_rec=lambda _: types.SimpleNamespace(at_end=lambda: False))
        layout = types.SimpleNamespace(read=lambda _: None, top_cells=lambda: [cell], cell=lambda _: cell,
                                       layer_indexes=lambda: [1])
        db = types.ModuleType("klayout.db")
        db.Layout = lambda: layout
        klayout = types.ModuleType("klayout")
        klayout.db = db
        with patch.dict(sys.modules, {"klayout": klayout, "klayout.db": db}), \
             patch.object(runtime, "geometry_evidence", return_value=geometry), \
             patch.object(runtime, "verify_stock_geometry"), \
             patch.object(runtime, "validate_lvs_result"), \
             patch.object(runtime, "magic_findings", return_value=[]):
            evidence = runtime.check(project, pdk)
            self.assertEqual(evidence["status"], "pass")
            self.assertEqual(evidence["internal_device_lvs"], "not_verified")
            self.assertIn(str(raw_magic.relative_to(flow)), evidence["artifacts"])
            independent = self.root / "independent-recheck"
            independent.mkdir()
            frozen = {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            with patch.object(runtime, "retained_obstruction_controls", return_value={"status": "fixture_disposition"}), \
                 patch.object(runtime, "obstruction_contact_attribution", return_value=[]):
                separate = runtime.check(project, pdk, evidence_output=independent, magic_control_dir=self.root / "controls")
            self.assertFalse(separate["provider_acceptance"])
            self.assertEqual(separate["record_kind"], "independent_physical_recheck_not_provider_acceptance")
            self.assertTrue((independent / "physical-checks.json").is_file())
            self.assertEqual(frozen, {str(p): p.read_bytes() for p in project.rglob("*") if p.is_file()})
            with self.assertRaisesRegex(ValueError, "outside original project"):
                runtime.check(project, pdk, evidence_output=flow)
            sibling = project.parent / "would-mutate-original-run"
            sibling.mkdir()
            with self.assertRaisesRegex(ValueError, "outside original run"):
                runtime.check(project, pdk, evidence_output=sibling, magic_control_dir=self.root / "controls")
            for key in ("design__lvs_error__count", "magic__illegal_overlap__count",
                        "design__max_slew_violation__count__corner:nom_typ_1p20V_25C",
                        "design__max_cap_violation__count__corner:nom_typ_1p20V_25C"):
                bad = dict(metrics, **{key: 1})
                save(flow / "final/metrics.json", bad)
                with self.assertRaises(ValueError):
                    runtime.check(project, pdk)
                del bad[key]
                save(flow / "final/metrics.json", bad)
                with self.assertRaises(ValueError):
                    runtime.check(project, pdk)
            save(flow / "final/metrics.json", metrics)
            other = flow / "different.gds"
            other.write_bytes(b"different assembled layout")
            save(drc / "state_in.json", {"gds": str(other)})
            with self.assertRaisesRegex(ValueError, "differs from final"):
                runtime.check(project, pdk)
            save(drc / "state_in.json", {"gds": str(gds)})
            script.write_text("lvs -blackbox -json\n")
            with self.assertRaisesRegex(ValueError, "did not consume"):
                runtime.check(project, pdk)

    def test_final_used_cell_models_and_mutation_fail_closed(self):
        project, pdk, output = [self.root / name for name in ("project", "pdk", "check")]
        for path in (project, pdk, output):
            path.mkdir()
        tech = pdk / "ihp-sg13cmos5l"
        models = tech / "libs.ref/sram"
        cells = tech / "libs.ref/sg13cmos5l_stdcell/verilog"
        models.mkdir(parents=True)
        cells.mkdir(parents=True)
        refs = ["pdk_dir::libs.ref/sram/macro.v", "pdk_dir::libs.ref/sram/core.v"]
        for name in ("macro.v", "core.v"):
            (models / name).write_text("// stock functional source\n")
        (project / "manifest.json").write_text(json.dumps({"macro_contract": {
            "implementation_simulation": {"defines": ["FUNCTIONAL"], "models": refs}}}))
        (self.root / "preflight.json").write_text(json.dumps({"references": [
            {"reference": ref, "sha256": runtime.sha(tech / ref[9:])} for ref in refs]}))
        (cells / "sg13cmos5l_udp.v").write_text("// UDP retained exactly\n")
        vendor = cells / "sg13cmos5l_stdcell.v"
        vendor.write_text('''module sg13cmos5l_buf_1 (input A, output X);
assign X=A;
specify (A=>X)=1; endspecify
endmodule
module sg13cmos5l_dfrbpq_1 (input CLK,D,RESET_B, output Q);
wire delayed_CLK, delayed_D, delayed_RESET_B;
vendor_udp u(Q, delayed_CLK, delayed_D, delayed_RESET_B);
endmodule
module sg13cmos5l_unknown_1 (input A, output Q);
wire delayed_A;
assign Q=delayed_A;
endmodule
''')
        preflight = json.loads((self.root / "preflight.json").read_text())
        preflight["references"] += [{"path": str(path.resolve()), "sha256": runtime.sha(path)}
                                     for path in (vendor, cells / "sg13cmos5l_udp.v")]
        (self.root / "preflight.json").write_text(json.dumps(preflight))
        netlist = self.root / "final.nl.v"
        netlist.write_text("sg13cmos5l_buf_1 resized_buffer(.A(a),.X(x));\nsg13cmos5l_dfrbpq_1 staging(.Q(q));\n")
        runtime.replay_models(project, pdk, netlist, output)
        functional = (output / "stdcell-functional.v").read_text()
        self.assertIn("assign delayed_CLK = CLK;", functional)
        self.assertIn("vendor_udp u(Q, delayed_CLK, delayed_D, delayed_RESET_B);", functional)
        self.assertNotIn("specify", functional)
        self.assertNotIn("module sg13cmos5l_unknown_1", functional)
        inventory = json.loads((output / "replay-models.json").read_text())
        self.assertEqual(inventory["scope"], "zero_delay_gate_functional")
        self.assertEqual(len(inventory["models"]), 4)
        netlist.write_text("sg13cmos5l_unknown_1 changed_after_route(.Q(q));")
        with self.assertRaisesRegex(ValueError, "unreviewed delayed-reference"):
            runtime.replay_models(project, pdk, netlist, output)
        (models / "macro.v").write_text("changed after preflight")
        with self.assertRaisesRegex(ValueError, "run-bound macro model changed"):
            runtime.replay_models(project, pdk, netlist, output)

    def test_tt_adapter_exact_pinned_seam_and_thread_option(self):
        work = self.root / "tt"
        (work / "precheck").mkdir(parents=True)
        path = work / "precheck/precheck.py"
        original = 'f"{PDK_ROOT}/{PDK_NAME}/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",\n'
        path.write_text(original)
        runtime.prepare_tt(work)
        self.assertIn('"threads": "6"', path.read_text())
        self.assertIn('"no_recommended": "false"', path.read_text())
        receipt = json.loads((self.root / "tt-adaptation.json").read_text())
        self.assertNotEqual(receipt["original_sha256"], receipt["adapted_sha256"])
        path.write_text("unexpected pinned invocation")
        with self.assertRaisesRegex(ValueError, "unsupported pinned TT"):
            runtime.prepare_tt(work)


unittest.main()
