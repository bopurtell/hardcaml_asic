"""Isolated SRAM probe runner acceptance: real snapshot I/O, fake tool execution only."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "test/fixtures/sram_flow_probe"


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob("*") if p.is_file()}


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="SRAM probe runner space '")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.here = self.root / "experiments/sram_flow_feasibility"
        self.here.mkdir(parents=True)
        spec = importlib.util.spec_from_file_location("sram_probe_under_test", SOURCE / "run_probe.py")
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        self.runner.ROOT, self.runner.HERE = self.root, self.here
        for name in self.runner.AUTHORED:
            shutil.copyfile(SOURCE / name, self.here / name)
        self.config = json.loads((self.here / "config.json").read_text())
        template = self.root / "floorplan.def"
        template.write_text("floorplan fixture\n")
        self.config["FP_DEF_TEMPLATE"] = str(template)
        (self.here / "config.json").write_text(json.dumps(self.config))
        for macro in self.config["MACROS"].values():
            for kind in ("lef", "gds", "lib", "spice"):
                values = macro[kind]
                if isinstance(values, dict):
                    values = [v for group in values.values() for v in group]
                for view in values:
                    self.file(".toolchain/pdk/ihp-sg13cmos5l/" + view.removeprefix("pdk_dir::"))
        for name in (".venv", ".venv-precheck"):
            self.file(f".toolchain/{name}/bin/python")
        self.file(".toolchain/tt-sram-s3-cfa06bae/precheck/default.nix")
        self.file(".toolchain/tt-sram-s3-cfa06bae/precheck/precheck.py")
        self.file(".toolchain/tt-sram-s3-cfa06bae/tech/fixture")
        self.view = self.file("retained/routed.gds", "retained GDS bytes")
        self.state = {"gds": str(self.view), "nl": str(self.view),
                      "spef": {"nom_*": str(self.view)},
                      "metrics": {"ignored_path": "/not/a/view"}}
        for _, state in self.runner.STATES.values():
            path = self.here / "runs/attempt-005-full" / state
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.state))
        self.calls = []
        self.exit_codes = [0]
        self.make_gds = False

    def file(self, name, text="fixture\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def execute(self, command, cwd, env, log):
        self.calls.append((command, cwd, env))
        log.write_text("fake tool execution\n")
        if command[0] == "nix-shell":
            # Real KLayout cannot save reports without this upstream prerequisite.
            # Deliberately do not mkdir here: runner setup must create it first.
            (cwd / "reports/fake-result.xml").write_text("<report/>\n")
        if self.make_gds and len(self.calls) == 1:
            path = self.output / "inputs/runs/case/final/gds/tt_um_sram_probe.gds"
            path.parent.mkdir(parents=True)
            path.write_text("new GDS")
        return self.exit_codes.pop(0)

    @property
    def output(self):
        return self.here / "runs/pt0-case"

    def invoke(self, *args):
        with patch.object(sys, "argv", ["run_probe.py", *args]), \
             patch.object(self.runner, "run_logged", side_effect=self.execute), \
             patch.object(self.runner.subprocess, "check_output", return_value="fixture-revision\n"), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return self.runner.main()
            except SystemExit as error:
                return error.code

    def run_mode(self, mode="sta", *extra):
        return self.invoke(mode, "--tag", "case", "--jobs", "4", *extra)

    def test_invalid_arguments_never_create_output_or_launch(self):
        before = tree(self.root)
        for argv in ([], ["sta"], ["unknown"],
                     ["sta", "--tag", "../escape", "--jobs", "4"],
                     ["sta", "--tag", "a" * 81, "--jobs", "4"],
                     *[["sta", "--tag", "case", "--jobs", jobs] for jobs in ("0", "-1", "21", "1.5")],
                      ["sta", "--tag", "case", "--jobs", "4", "--precheck"],
                     ["precheck-only", "--tag", "case", "--jobs", "4"],
                     ["corrected-full", "--tag", "case", "--jobs", "4", "--retained-run", "somewhere"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.invoke(*argv), 2)
        self.assertEqual(self.calls, [])
        self.assertEqual(tree(self.root), before)

    def test_dry_run_all_modes_is_read_only(self):
        self.prepare_archive()
        before = tree(self.root)
        for mode in ("sta", "magic-gds", "corrected-full", "power-only"):
            self.assertEqual(self.run_mode(mode, "--dry-run"), 0)
        self.assertEqual(tree(self.root), before)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_missing_retained_view_fails_before_reservation(self):
        self.view.unlink()
        self.assertEqual(self.run_mode(), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_malformed_retained_state_has_clean_input_error(self):
        # Real CLI error boundary; malformed input must never reach EDA.
        state = self.here / "runs/attempt-005-full" / self.runner.STATES["sta"][1]
        state.write_text("[]\n")
        result = self.runner.subprocess.run(
            [sys.executable, "-B", str(self.here / "run_probe.py"), "sta",
             "--tag", "case", "--jobs", "4", "--dry-run"],
            capture_output=True, text=True, timeout=5)
        self.assertFalse(self.output.exists())
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_empty_retained_state_rejected_before_launch(self):
        state = self.here / "runs/attempt-005-full" / self.runner.STATES["sta"][1]
        state.write_text("{}\n")
        code = self.run_mode()
        self.assertEqual((code, len(self.calls), self.output.exists()), (2, 0, False))

    def test_mode_required_views_and_shapes_rejected_before_launch(self):
        cases = {
            "sta": [None, 1, "state", {}, {"nl": str(self.view)},
                    {"nl": str(self.view), "spef": {}},
                    {"nl": str(self.view), "spef": str(self.view)},
                    {"nl": [], "spef": {"nom_*": str(self.view)}},
                    {"nl": str(self.view), "spef": {"nom_*": []}},
                    {"nl": str(self.view), "spef": {"nom_*": None}}],
            "magic-gds": [[], {}, {"gds": None}, {"gds": []}, {"gds": ""}],
        }
        for mode, states in cases.items():
            state_path = self.here / "runs/attempt-005-full" / self.runner.STATES[mode][1]
            for state in states:
                with self.subTest(mode=mode, state=state):
                    state_path.write_text(json.dumps(state))
                    self.assertEqual(self.run_mode(mode), 2)
                    self.assertFalse(self.output.exists())
                    self.assertEqual(self.calls, [])

    def test_minimal_sta_and_optional_view_shapes_are_supported(self):
        state = {"nl": str(self.view), "spef": {"nom_*": [str(self.view)]},
                 "odb": None, "sdf": {"nom_typ_1p20V_25C": str(self.view)},
                 "lib": {"nom_*": [str(self.view)]}}
        state_path = self.here / "runs/attempt-005-full" / self.runner.STATES["sta"][1]
        state_path.write_text(json.dumps(state))
        self.assertEqual(self.run_mode("sta"), 0)

    def test_magic_requires_only_gds(self):
        state_path = self.here / "runs/attempt-005-full" / self.runner.STATES["magic-gds"][1]
        state_path.write_text(json.dumps({"gds": str(self.view)}))
        self.assertEqual(self.run_mode("magic-gds"), 0)

    def test_explicit_tt_root_is_copied_and_recorded(self):
        tt = self.root / "current tt"
        self.file("current tt/precheck/default.nix", "current nix")
        self.file("current tt/precheck/precheck.py", "current precheck")
        self.file("current tt/tech/fixture", "current tech")
        self.assertEqual(self.run_mode("corrected-full", "--precheck", "--tt-root", str(tt)), 2)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["precheck_tt_root"], str(tt))
        self.assertEqual((self.output / "tt/precheck/precheck.py").read_text(), "current precheck")

    def test_tt_root_requires_precheck_and_complete_checkout(self):
        self.assertEqual(self.run_mode("sta", "--tt-root", str(self.root)), 2)
        self.assertEqual(self.run_mode("corrected-full", "--precheck", "--tt-root", str(self.root)), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_failure_preserves_inputs_and_reserves_identity(self):
        before = tree(self.root)
        self.exit_codes = [17]
        self.assertEqual(self.run_mode(), 17)
        self.assertEqual(json.loads((self.output / "status.json").read_text()), {"flow_exit": 17})
        current = tree(self.root)
        self.assertEqual({k: current[k] for k in before}, before)
        self.assertEqual(self.run_mode(), 2)
        self.assertEqual(tree(self.root), current)
        self.assertEqual(len(self.calls), 1)
        inputs = self.output / "inputs"
        self.assertEqual(json.loads((inputs / "state.json").read_text()), self.state)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["retained_view_sha256"],
                         {str(self.view): hashlib.sha256(self.view.read_bytes()).hexdigest()})
        for name, digest in manifest["input_sha256"].items():
            self.assertEqual(hashlib.sha256((inputs / name).read_bytes()).hexdigest(), digest)
            self.assertEqual((inputs / name).stat().st_mode & 0o222, 0)

    def test_modes_and_explicit_thread_caps(self):
        self.assertEqual(self.run_mode("magic-gds"), 0)
        command, cwd, env = self.calls[0]
        self.assertEqual(cwd, self.root)
        self.assertEqual(command[command.index("--jobs") + 1], "4")
        for setting in ("OPENROAD_THREADS=4", "STA_THREADS=1", "KLAYOUT_DRC_THREADS=4", "KLAYOUT_XOR_THREADS=4"):
            self.assertIn(setting, command)
        self.assertEqual(command[command.index("--only") + 1], "Magic.DRC")
        self.assertEqual(env["OMP_NUM_THREADS"], "4")
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(env["MKL_NUM_THREADS"], "1")
        config = json.loads((self.output / "inputs/config.json").read_text())
        self.assertIs(config["MAGIC_DRC_USE_GDS"], True)
        self.assertEqual(config["FP_DEF_TEMPLATE"], str(self.output / "inputs/floorplan.def"))

    def prepare_archive(self):
        historical = dict(self.config)
        historical["MACROS"] = json.loads(json.dumps(self.config["MACROS"]))
        historical["MACROS"][self.runner.MACRO]["lib"] = {"*": [
            self.config["MACROS"][self.runner.MACRO]["lib"]["nom_typ_1p20V_25C"][0]]}
        payload = json.dumps(historical).encode()
        self.runner.ARCHIVE = self.root / "historical.tar.gz"
        with tarfile.open(self.runner.ARCHIVE, "w:gz") as archive:
            member = tarfile.TarInfo("experiments/sram_flow_feasibility/config.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        self.runner.ARCHIVE_HASH = self.runner.digest(self.runner.ARCHIVE)

    def test_power_only_uses_verified_historical_corners_and_current_pdn(self):
        self.prepare_archive()
        self.assertEqual(self.run_mode("power-only"), 0)
        config = json.loads((self.output / "inputs/config.json").read_text())
        self.assertEqual(list(config["MACROS"][self.runner.MACRO]["lib"]), ["*"])
        self.assertEqual((self.output / "inputs/pdn_cfg.tcl").read_bytes(), (SOURCE / "pdn_cfg.tcl").read_bytes())
        self.assertNotIn("--only", self.calls[0][0])

    def test_archive_hash_mismatch_fails_before_launch(self):
        self.prepare_archive()
        self.runner.ARCHIVE_HASH = "0" * 64
        self.assertEqual(self.run_mode("power-only"), 2)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_real_subprocess_failure_is_logged_and_returned(self):
        log = self.root / "child.log"
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.runner.run_logged(
                [sys.executable, "-B", "-c",
                 "import os,sys; print(os.environ['PT0_TEST']); print('stderr marker', file=sys.stderr); sys.exit(7)"],
                self.root, dict(os.environ, PT0_TEST="stdout marker"), log)
        self.assertEqual(code, 7)
        self.assertIn("stdout marker", log.read_text())
        self.assertIn("stderr marker", log.read_text())

    def test_failed_flow_without_gds_skips_precheck(self):
        self.exit_codes = [19]
        self.assertEqual(self.run_mode("corrected-full", "--precheck"), 19)
        status = json.loads((self.output / "status.json").read_text())
        self.assertEqual(status, {"flow_exit": 19, "precheck_exit": 2, "precheck_skipped": "no final GDS"})
        self.assertEqual(len(self.calls), 1)

    def test_success_without_gds_is_not_precheck_success(self):
        self.assertEqual(self.run_mode("corrected-full", "--precheck"), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads((self.output / "status.json").read_text())["precheck_exit"], 2)

    def test_precheck_still_runs_on_available_gds_and_preserves_flow_failure(self):
        self.make_gds = True
        self.exit_codes = [19, 23]
        self.assertEqual(self.run_mode("corrected-full", "--precheck"), 19)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(json.loads((self.output / "status.json").read_text()),
                         {"flow_exit": 19, "precheck_exit": 23})

    def test_precheck_failure_propagates_after_successful_flow(self):
        self.make_gds = True
        self.exit_codes = [0, 23]
        before = tree(self.root)
        self.assertEqual(self.run_mode("corrected-full", "--precheck"), 23)
        self.assertEqual(json.loads((self.output / "status.json").read_text()), {"flow_exit": 0, "precheck_exit": 23})
        current = tree(self.root)
        self.assertEqual({k: current[k] for k in before}, before)
        self.assertEqual(self.calls[1][1], self.output / "tt/precheck")
        # Execute the generated launcher against a harmless argv recorder, proving
        # quoting/path preservation and the actual threads variable (not thr).
        recorder = self.file("fake klayout", "#!/bin/sh\nexec /usr/bin/python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' \"$@\"\n")
        recorder.chmod(0o755)
        result = self.runner.subprocess.run(
            [str(self.output / "bin/klayout"), "-r", "deck with spaces", "-rd", "thr=1"],
            env=dict(os.environ, PT0_REAL_KLAYOUT=str(recorder)), capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(json.loads(result.stdout), ["-r", "deck with spaces", "-rd", "thr=1", "-rd", "threads=4"])

    def test_precheck_creates_clean_reports_before_launch(self):
        old_report = self.file(".toolchain/tt-sram-s3-cfa06bae/precheck/reports/old.xml", "preserve")
        self.make_gds = True
        self.exit_codes = [0, 0]
        self.assertEqual(self.run_mode("corrected-full", "--precheck"), 0)
        reports = self.output / "tt/precheck/reports"
        self.assertEqual([p.name for p in reports.iterdir()], ["fake-result.xml"])
        self.assertEqual(old_report.read_text(), "preserve")

    def prepare_precheck_run(self):
        retained = self.root / "retained"
        self.file("retained/final/gds/tt_um_sram_probe.gds", "retained final GDS")
        self.file("retained/final/lef/tt_um_sram_probe.lef", "retained final LEF")
        self.file("retained/info.yaml", "retained project metadata")
        return retained

    def test_precheck_only_snapshots_retained_inputs_without_flow(self):
        retained = self.prepare_precheck_run()
        before = tree(self.root)
        self.assertEqual(self.run_mode("precheck-only", "--retained-run", str(retained)), 0)
        self.assertEqual(len(self.calls), 1)
        command, cwd, _ = self.calls[0]
        self.assertEqual(command[0], "nix-shell")
        precheck_argv = shlex.split(command[-1].splitlines()[-1])
        self.assertEqual(precheck_argv[precheck_argv.index("--gds") + 1],
                         str(self.output / "inputs/gds/tt_um_sram_probe.gds"))
        self.assertTrue((cwd / "reports/fake-result.xml").is_file())
        self.assertFalse((self.output / "flow.log").exists())
        self.assertEqual(json.loads((self.output / "status.json").read_text()), {"precheck_exit": 0})
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertIsNone(manifest["command"])
        self.assertEqual(manifest["geometry"], "retained")
        self.assertEqual(manifest["precheck_retained_run"], str(retained))
        self.assertEqual((self.output / "inputs/info.yaml").read_text(), "retained project metadata")
        for name, digest in manifest["input_sha256"].items():
            path = self.output / "inputs" / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertEqual(path.stat().st_mode & 0o222, 0)
        current = tree(self.root)
        self.assertEqual({k: current[k] for k in before}, before)
        self.assertEqual(self.run_mode("precheck-only", "--retained-run", str(retained)), 2)
        self.assertEqual(tree(self.root), current)

    def test_precheck_only_dry_run_and_tt_override(self):
        retained = self.prepare_precheck_run()
        tt = self.root / "current tt"
        self.file("current tt/precheck/default.nix", "current nix")
        self.file("current tt/precheck/precheck.py", "current precheck")
        self.file("current tt/tech/fixture", "current tech")
        before = tree(self.root)
        args = ("precheck-only", "--retained-run", str(retained), "--tt-root", str(tt))
        self.assertEqual(self.run_mode(*args, "--dry-run"), 0)
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.calls, [])
        self.exit_codes = [23]
        self.assertEqual(self.run_mode(*args), 23)
        self.assertEqual((self.output / "tt/precheck/precheck.py").read_text(), "current precheck")
        self.assertEqual(json.loads((self.output / "status.json").read_text()), {"precheck_exit": 23})

    def test_precheck_only_missing_inputs_fail_before_reservation(self):
        retained = self.prepare_precheck_run()
        for name in ("info.yaml", "final/gds/tt_um_sram_probe.gds", "final/lef/tt_um_sram_probe.lef"):
            with self.subTest(name=name):
                path = retained / name
                content = path.read_bytes()
                path.unlink()
                self.assertEqual(self.run_mode("precheck-only", "--retained-run", str(retained)), 2)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.calls, [])
                path.write_bytes(content)


if __name__ == "__main__":
    unittest.main()
