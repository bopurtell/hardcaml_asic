"""Pure regression fixtures: no builds, EDA, containers or installed libraries."""
from decimal import Decimal as D
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("physical_runtime", ROOT / "flow/hardcaml_asic_flow/physical_runtime.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
spec = importlib.util.spec_from_file_location("magic_diagnostic", Path(__file__).with_name("run_magic_obstruction_diagnostic.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def marker(rule, box):
    return rule, tuple(map(D, box.split()))


# Exact retained PT3c markers; no inferred/rounded bounding boxes.
M2 = [marker(rt.M2_OBS_RULE, b) for b in (
    "39.400 480.520 39.460 495.920", "39.400 597.805 39.460 608.785")]
M3 = [marker(rt.M3_OBS_RULE, b) for b in (
    "40.000 599.240 221.660 599.380", "242.980 599.240 276.800 599.380",
    "55.100 479.540 70.940 479.740", "86.300 479.540 129.260 479.740",
    "198.710 479.540 240.380 479.740", "246.820 479.540 261.185 479.740",
    "262.240 479.540 276.800 479.740")]
PLAN = {"plan_file": "physical/macro-plan.json", "pdn_tcl": "physical/pdn.tcl",
        "macros": [{"module": "SRAM", "instance": "program", "location_um": [40, 480], "halo_um": 2}]}
LEF = ("  PIN signal\n    PORT\n      LAYER Metal2 ;\n      RECT 1 0 2 0.26 ;\n    END\n  END signal\n"
       "  PIN VDD!\n    PORT\n      LAYER Metal4 ;\n      RECT 0 0 2 100 ;\n    END\n  END VDD!\n"
       "  OBS\n    LAYER Metal2 ;\n      RECT 0.1 1 0.3 118.7 ;\n"
       "    LAYER Metal2 SPACING 0.21 ;\n      RECT 0 0.52 236.8 118.78 ;\n"
       "    LAYER Metal3 ;\n      RECT 0 0 236.8 118.78 ;\n"
       "    LAYER Metal4 ;\n      RECT 0 0 4 118.78 ;\n  END\n")


def bind_fixture(root, flow, lef, routed, diagnostic):
    """Independent metadata closure around fixture controls, without EDA."""
    project, run = flow.parents[1], flow.parents[2]
    step = flow / "63-magic-drc"
    step.mkdir(parents=True, exist_ok=True)
    required = []
    for path, text in ((run / "results.json", "{}"), (run / "preflight.json", "{}"),
                       (project / "physical-runtime.py", "original checker"),
                       (project / PLAN["plan_file"], json.dumps(PLAN)),
                       (project / PLAN["pdn_tcl"], "original power geometry script"),
                       (flow / "final/gds/top.gds", "original assembled GDS"),
                       (flow / "final/nl/top.nl.v", "original netlist"),
                       (flow / "final/metrics.json", "{}"),
                       (flow / "21-openroad-generatepdn/macro-power-geometry.json", "{}"),
                       (step / "reports/drc.magic.rpt", "original raw Magic report"),
                       (root / "tech.lef", "tech LEF"), (root / "cells.lef", "cell LEF"),
                       (root / "pinned.magicrc", "pinned Magic reader")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        required.append(path)
    config = rt.load(flow / "resolved.json")
    config.update(TECH_LEFS={"nom": str(root / "tech.lef")}, CELL_LEFS=[str(root / "cells.lef")],
                  MAGICRC=str(root / "pinned.magicrc"))
    rt.write(flow / "resolved.json", config)
    manifest = {"top_module": "top", "macro_contract": {"physical_plan": PLAN}}
    rt.write(project / "manifest.json", manifest)
    image = "sha256:" + "a" * 64
    record = {"run_id": "run-a", "build_identity": "bundle-a", "status": "failed",
              "manifest_sha256": rt.sha(project / "manifest.json"), "environment": {
                  "execution_binding": {"image_identity": {"image_id": image}},
                  "references": [{"path": str(lef), "sha256": rt.sha(lef)}]}}
    rt.write(run / "run.json", record)
    required += [run / "run.json", project / "manifest.json", flow / "resolved.json", step / "_env.tcl",
                 step / "state_in.json", lef, routed, runner.RUNTIME, Path(runner.__file__)]
    frozen = {"schema_version": 1, "run": str(run), "run_id": "run-a", "build_identity": "bundle-a",
              "original_status": "failed", "physical_acceptance": False,
              "input_sha256": {str(p): rt.sha(p) for p in required}, "timeout_seconds": 1980,
              "budget": {"exclusive_eda_lane_cpus": 6, "parallel_magic_jobs": 1, "threads_per_job": 1,
                         "cases": 4, "per_case_timeout_seconds": 480},
              "command": ["docker", "run", "--pull=never", "--entrypoint", "python3", image, "-I", "-B",
                          str(Path(runner.__file__)), "inside", "--output", str(diagnostic)]}
    rt.write(diagnostic / "plan.json", frozen)
    rt.write(diagnostic / "execution.json", {"status": "completed", "exit_code": 0, "physical_acceptance": False})
    rt.write(diagnostic / "result.json", dict(rt.load(diagnostic / "controls/evidence.json"),
             original_run_status="failed", run_id="run-a", build_identity="bundle-a",
             raw_report_sha256=rt.sha(step / "reports/drc.magic.rpt")))


class Diagnostic(unittest.TestCase):
    def controls(self):
        return {"stock": M2 + M3, "without-Metal2": M3, "without-Metal3": M2, "without-combined": []}

    def test_short_overlap_and_boundary_equality_remain_rejected_for_acceptance(self):
        self.assertEqual(D("598.78") - M2[1][1][1], D("0.975"))
        for finding in [M2[1], M3[0], M3[1], M3[-1]]:
            with self.assertRaisesRegex(ValueError, "unique stock obstruction"):
                rt.classify_wide_obstructions([finding], [], PLAN)
        rt.validate_obstruction_control_markers(M2 + M3, self.controls())
        with self.assertRaises(ValueError):
            rt.validate_obstruction_controls(M2 + M3, self.controls(), PLAN)

    def test_exact_multiset_controls_reject_loss_shift_extra_duplicate_and_unknown(self):
        for label, value in (("stock", M2 + M3[:-1]), ("stock", M2 + M3 + [M3[0]]),
                             ("without-Metal2", []), ("without-Metal3", M2 + [M3[0]]),
                             ("without-combined", [M2[0]]),
                             ("stock", [("unknown", M2[0][1])])):
            observed = self.controls()
            observed[label] = value
            with self.assertRaises(ValueError):
                rt.validate_obstruction_control_markers(M2 + M3, observed)
        shifted = list(M3[0][1])
        shifted[0] += D("0.005")
        observed = self.controls()
        observed["stock"] = M2 + [(M3[0][0], tuple(shifted))] + M3[1:]
        with self.assertRaises(ValueError):
            rt.validate_obstruction_control_markers(M2 + M3, observed)

    def test_reviewed_extension_requires_exact_geometry_identity_hash_and_all_controls(self):
        plan = copy.deepcopy(PLAN)
        plan["macros"][0]["module"] = rt.PT3C_MACRO
        def accept(findings, controls, p=plan, digest=rt.PT3C_STOCK_LEF_SHA256):
            return rt.validate_obstruction_controls(findings, controls, p, source_lef_sha256=digest)
        result = accept(M2 + M3, self.controls())
        self.assertEqual(result["accepted_count"], 9)
        self.assertEqual(sum(f["coverage"] == "pt3c_exact_edge_projection_v1" for f in result["findings"]), 4)
        for digest in (None, "wrong"):
            with self.assertRaises(ValueError):
                accept(M2 + M3, self.controls(), digest=digest)
        with self.assertRaises(ValueError):
            accept(M2 + M3, self.controls(), p=PLAN)
        with self.assertRaises(ValueError):
            rt.classify_wide_obstructions(M2 + M3, [], plan, source_lef_sha256=rt.PT3C_STOCK_LEF_SHA256)
        for bad_box in ("39.400 597.800 39.460 608.785", "39.400 597.805 39.460 608.790",
                        "39.405 597.805 39.460 608.785", "39.400 597.805 39.465 608.785",
                        "39.400 598.780 39.460 608.785",  # zero overlap
                        "39.400 599.000 39.460 608.785",  # beyond obstruction end
                        "39.990 597.805 40.050 608.785"):  # crosses macro boundary
            finding = marker(rt.M2_OBS_RULE, bad_box)
            controls = {"stock": [finding], "without-Metal2": []}
            with self.assertRaises(ValueError):
                accept([finding], controls)
        for original in (M3[0], M3[1], M3[-1]):
            for box in ((original[1][0] - D("0.005"), *original[1][1:]),
                        (*original[1][:2], original[1][2] + D("0.005"), original[1][3]),
                        (*original[1][:3], original[1][3] + D("0.005"))):
                finding = (original[0], box)
                # Keep at least one boundary endpoint; these are not old strict-interior cases.
                with self.assertRaises(ValueError):
                    accept([finding], {"stock": [finding], "without-Metal3": []})
        for changed in ({"without-Metal2": []}, {"stock": M2 + M3[:-1]}, {"without-combined": M3}):
            with self.assertRaises(ValueError):
                accept(M2 + M3, dict(self.controls(), **changed))
        duplicate = M2 + [M2[1]] + M3
        with self.assertRaisesRegex(ValueError, "duplicate reviewed"):
            accept(duplicate, {"stock": duplicate, "without-Metal2": M3,
                               "without-Metal3": M2 + [M2[1]], "without-combined": []})
        overlapping = copy.deepcopy(plan)
        overlapping["macros"].append(dict(overlapping["macros"][0], instance="other"))
        with self.assertRaisesRegex(ValueError, "unique stock"):
            accept(M2 + M3, self.controls(), p=overlapping)
        observed = self.controls()
        del observed["without-Metal3"]
        with self.assertRaises(ValueError):
            rt.validate_obstruction_control_markers(M2 + M3, observed)

    def test_removal_is_exact_and_preserves_pins_power_narrow_obs(self):
        for layers in (("Metal2",), ("Metal3",), ("Metal2", "Metal3")):
            expected = LEF
            for layer in layers:
                rect = "      RECT 0 " + ("0.52" if layer == "Metal2" else "0") + " 236.8 118.78 ;\n"
                expected = expected.replace(rect, "")
                if layer == "Metal2":
                    expected = expected.replace("    LAYER Metal2 SPACING 0.21 ;\n", "")
                if layer == "Metal3":
                    expected = expected.replace("    LAYER Metal3 ;\n", "")
            self.assertEqual(rt.without_wide_obstructions(LEF, layers), expected)

    def test_prepare_is_tool_free_pins_image_and_read_only_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, out = root / "original", root / "diagnostic"
            run.mkdir()
            image = "sha256:" + "a" * 64
            record = {"status": "failed", "run_id": "original", "build_identity": "bundle",
                      "environment": {"execution_binding": {"image_identity": {"image_id": image}}}}
            with patch.object(runner, "WORKSPACE", root), patch.object(runner, "inputs", return_value=(record, PLAN, {})), \
                 patch.object(runner.subprocess, "run", side_effect=AssertionError("no tools during preparation")), patch("builtins.print"):
                runner.prepare(run, out, rt)
                receipt = rt.load(out / "plan.json")
                command = receipt["command"]
                self.assertIn(image, command)
                self.assertIn(f"{root}:{root}:ro", command)
                self.assertIn(f"{out}:{out}:rw", command)
                self.assertIn("--pull=never", command)
                self.assertIn("--cpus=6", command)
                self.assertEqual(receipt["budget"]["parallel_magic_jobs"], 1)
                runner.prepare(run, root / "larger-budget", rt, case_timeout=480)
                larger = rt.load(root / "larger-budget/plan.json")
                self.assertEqual(larger["timeout_seconds"], 1980)
                self.assertEqual(larger["budget"]["per_case_timeout_seconds"], 480)
                with self.assertRaises(ValueError):
                    runner.prepare(run, out, rt)
                with self.assertRaises(ValueError):
                    runner.prepare(run, run / "must-not-write", rt)

    def test_retained_identity_rejects_cross_run_stale_gds_def_lef_and_missing_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = root / "run/project/runs/asic"
            step = flow / "63-magic-drc"
            step.mkdir(parents=True)
            lef, routed = root / "stock.lef", root / "routed.def"
            lef.write_text(LEF)
            routed.write_text("original routed DEF")
            (step / "_env.tcl").write_text("original environment")
            rt.write(step / "state_in.json", {"def": str(routed)})
            rt.write(flow / "resolved.json", {"MACROS": {"SRAM": {"lef": [str(lef)]}}})
            diagnostic = root / "diagnostic"
            (diagnostic / "controls").mkdir(parents=True)
            rt.write(diagnostic / "controls/evidence.json", {"status": "diagnostic_only"})
            bind_fixture(root, flow, lef, routed, diagnostic)
            self.assertEqual(len(rt.retained_obstruction_identity(flow, PLAN, diagnostic / "controls")), 3)
            # Preserve the expected hashes while changing the real inputs.
            for path in (routed, lef, flow / "final/gds/top.gds"):
                with self.subTest(substitution=path.name):
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed")
                    with self.assertRaisesRegex(ValueError, "source reference mismatch|frozen input changed"):
                        rt.retained_obstruction_identity(flow, PLAN, diagnostic / "controls")
                    path.write_bytes(original)
            frozen_path = diagnostic / "plan.json"
            saved = rt.load(frozen_path)
            for mutation in (lambda p: p.update(run_id="other-run"),
                             lambda p: p.update(build_identity="other-bundle"),
                             lambda p: p.update(run=str(root / "different-run")),
                             lambda p: p["input_sha256"].pop(str(flow / "final/gds/top.gds")),
                             lambda p: p["command"].__setitem__(p["command"].index("-I") - 1, "sha256:" + "b" * 64)):
                changed = copy.deepcopy(saved)
                mutation(changed)
                rt.write(frozen_path, changed)
                with self.assertRaises(ValueError):
                    rt.retained_obstruction_identity(flow, PLAN, diagnostic / "controls")
            rt.write(frozen_path, saved)
            result_path = diagnostic / "result.json"
            result = rt.load(result_path)
            rt.write(result_path, dict(result, run_id="other-run"))
            with self.assertRaisesRegex(ValueError, "result/run/raw report mismatch"):
                rt.retained_obstruction_identity(flow, PLAN, diagnostic / "controls")
            rt.write(result_path, result)
            rt.write(diagnostic / "execution.json", {"status": "failed", "exit_code": 137, "physical_acceptance": False})
            with self.assertRaisesRegex(ValueError, "completed diagnostic"):
                rt.retained_obstruction_identity(flow, PLAN, diagnostic / "controls")

    def test_evidence_path_serial_copies_retains_observations_without_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = root / "run/project/runs/asic"
            step = flow / "63-magic-drc"
            step.mkdir(parents=True)
            scripts = root / "scripts"
            (scripts / "magic").mkdir(parents=True)
            (scripts / "magic/drc.tcl").write_text("set report_dir $::env(STEP_DIR)/reports\n# pinned body\n")
            lef, routed = root / "stock.lef", root / "routed.def"
            lef.write_text(LEF)
            routed.write_bytes(b"exact routes pins narrowOBS and power preserved\n")
            (step / "_env.tcl").write_text("# original environment\n")
            rt.write(step / "state_in.json", {"def": str(routed)})
            rt.write(flow / "resolved.json", {"MAGIC_DRC_USE_GDS": False, "MAGIC_DRC_MAGLEFS": None,
                     "MACROS": {"SRAM": {"lef": [str(lef)]}}, "DESIGN_NAME": "top", "MAGICRC": "pinned.magicrc"})
            common = types.ModuleType("librelane.common")
            common.get_script_dir = lambda: scripts
            launches = []

            def run(command, **kwargs):
                folder = kwargs["cwd"]
                launches.append(folder.name)
                self.assertEqual((folder / "diagnostic.def").read_bytes(), routed.read_bytes())
                self.assertIn("set ::env(CURRENT_DEF) {" + str(folder / "diagnostic.def") + "}", Path(command[-1]).read_text())
                self.assertEqual(kwargs["env"]["OMP_NUM_THREADS"], "1")
                self.assertEqual(kwargs["timeout"], 300)
                (folder / "reports/drc.magic.rpt").write_text("raw report fixture")
                return types.SimpleNamespace(returncode=0)

            def findings(path):
                return self.controls()[path.parent.parent.name]

            (root / "diagnostic").mkdir()
            with patch.dict(sys.modules, {"librelane.common": common}), patch.object(rt.subprocess, "run", side_effect=run), \
                 patch.object(rt, "magic_findings", side_effect=findings):
                result = rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "diagnostic/controls", evidence_only=True)
                self.assertEqual(launches, list(self.controls()))
                self.assertFalse(result["physical_acceptance"])
                self.assertEqual(result["exact_layer_control_relationships"], "pass")
                self.assertFalse((root / "diagnostic/controls/disposition.json").exists())
                self.assertEqual(lef.read_text(), LEF)
                self.assertEqual(result["diagnostic_limits"]["parallel_magic_jobs"], 1)
                bind_fixture(root, flow, lef, routed, root / "diagnostic")
                with patch.object(rt, "PT3C_MACRO", "SRAM"), patch.object(rt, "PT3C_STOCK_LEF_SHA256", rt.sha(lef)):
                    retained = rt.retained_obstruction_controls(flow, PLAN, M2 + M3, root / "diagnostic/controls")
                    self.assertEqual(retained["accepted_count"], 9)
                    # Even rehashing an injected script cannot change the pinned reader/deck.
                    script = root / "diagnostic/controls/stock/counterfactual.tcl"
                    old = script.read_text()
                    script.write_text(old + "# unreviewed reader change\n")
                    evidence_path = root / "diagnostic/controls/evidence.json"
                    saved = evidence_path.read_text()
                    changed = rt.load(evidence_path)
                    changed["controls"]["stock"]["script_sha256"] = rt.sha(script)
                    rt.write(evidence_path, changed)
                    result_path = root / "diagnostic/result.json"
                    saved_result = result_path.read_text()
                    changed_result = rt.load(result_path)
                    changed_result["controls"] = changed["controls"]
                    rt.write(result_path, changed_result)
                    observation_path = root / "diagnostic/controls/stock/observation.json"
                    saved_observation = observation_path.read_text()
                    rt.write(observation_path, changed["controls"]["stock"])
                    with self.assertRaisesRegex(ValueError, "reader/deck script"):
                        rt.retained_obstruction_controls(flow, PLAN, M2 + M3, root / "diagnostic/controls")
                    script.write_text(old)
                    evidence_path.write_text(saved)
                    result_path.write_text(saved_result)
                    observation_path.write_text(saved_observation)
                    # Self-consistent hashes cannot legitimize losing a pin.
                    derived = root / "diagnostic/controls/stock/diagnostic.lef"
                    saved_lef = derived.read_text()
                    derived.write_text(saved_lef.replace("RECT 1 0 2 0.26", "RECT 1 0 2 0.25"))
                    changed = rt.load(evidence_path)
                    changed["controls"]["stock"]["diagnostic_lef_sha256"] = rt.sha(derived)
                    rt.write(evidence_path, changed)
                    changed_result = rt.load(result_path)
                    changed_result["controls"] = changed["controls"]
                    rt.write(result_path, changed_result)
                    rt.write(observation_path, changed["controls"]["stock"])
                    with self.assertRaisesRegex(ValueError, "changed pins/narrow OBS/power"):
                        rt.retained_obstruction_controls(flow, PLAN, M2 + M3, root / "diagnostic/controls")
                    derived.write_text(saved_lef)
                    evidence_path.write_text(saved)
                    result_path.write_text(saved_result)
                    observation_path.write_text(saved_observation)
                    copied = root / "diagnostic/controls/stock/diagnostic.def"
                    copied.write_text("changed route")
                    with self.assertRaisesRegex(ValueError, "DEF changed"):
                        rt.retained_obstruction_controls(flow, PLAN, M2 + M3, root / "diagnostic/controls")
                    copied.write_bytes(routed.read_bytes())
                    # Exercise the actual coarse fallback, not an injected
                    # disposition: independent baseline -> fresh four controls.
                    geometry = flow / "21-openroad-generatepdn/macro-power-geometry.json"
                    rt.write(geometry, {"dbu_per_um": 1000, "macros": [{"instance": "program", "contacts": []}]})
                    baseline = root / "coarse-baseline"
                    timeouts = []
                    def coarse_run(command, **kwargs):
                        folder = kwargs["cwd"]
                        timeouts.append(kwargs["timeout"])
                        self.assertEqual(kwargs["timeout"], 180 if folder == baseline else 300)
                        (folder / "reports/drc.magic.rpt").write_text("independent raw report")
                        return types.SimpleNamespace(returncode=0)
                    def coarse_findings(path):
                        if path == step / "reports/drc.magic.rpt":
                            return M2 + M3
                        if path.parent.parent == baseline:
                            return []  # cannot intrinsically disposition any routed marker
                        return self.controls()[path.parent.parent.name]
                    with patch.object(rt.subprocess, "run", side_effect=coarse_run), \
                         patch.object(rt, "magic_findings", side_effect=coarse_findings):
                        coarse = rt.disposition_magic(flow, PLAN, geometry, 9, baseline_dir=baseline)
                    self.assertEqual(coarse["accepted_count"], 9)
                    self.assertEqual(sorted(timeouts), [180, 300, 300, 300, 300])
                    self.assertTrue((baseline / "wide-obstruction-diagnostic/disposition.json").is_file())
                    with self.assertRaisesRegex(ValueError, "unsupported Magic diagnostic timeout"):
                        rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "production480",
                                                          tool_timeout_seconds=480)
                    self.assertFalse((root / "production480").exists())
                with self.assertRaises(ValueError):
                    rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "acceptance")
                self.assertFalse((root / "acceptance").exists())
                def disproved(path):
                    return M3 if path.parent.parent.name == "without-combined" else findings(path)
                with patch.object(rt, "magic_findings", side_effect=disproved):
                    negative = rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "disproved", evidence_only=True)
                self.assertEqual(negative["exact_layer_control_relationships"], "fail")
                self.assertEqual(negative["controls"]["without-combined"]["raw_count"], 7)
                self.assertTrue((root / "disproved/evidence.json").exists())
                self.assertFalse(negative["physical_acceptance"])
                launches.clear()
                def timed_out(command, **kwargs):
                    launches.append(kwargs["cwd"].name)
                    raise rt.subprocess.TimeoutExpired(command, kwargs["timeout"])
                with patch.object(rt.subprocess, "run", side_effect=timed_out):
                    with self.assertRaises(rt.subprocess.TimeoutExpired):
                        rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "timeout", evidence_only=True)
                self.assertEqual(launches, ["stock"])
                failure = rt.load(root / "timeout/evidence.json")
                self.assertEqual(failure["status"], "diagnostic_inconclusive")
                self.assertEqual(failure["failed_case"], "stock")
                self.assertEqual(failure["error_type"], "TimeoutExpired")
                self.assertFalse(failure["physical_acceptance"])
                with patch.object(rt.subprocess, "run", side_effect=timed_out):
                    with self.assertRaises(rt.subprocess.TimeoutExpired):
                        rt.wide_obstruction_counterfactual(flow, PLAN, M2 + M3, root / "timeout480", evidence_only=True,
                                                          tool_timeout_seconds=480)
                self.assertEqual(rt.load(root / "timeout480/evidence.json")["diagnostic_limits"]["tool_timeout_seconds"], 480)


if __name__ == "__main__":
    unittest.main()
