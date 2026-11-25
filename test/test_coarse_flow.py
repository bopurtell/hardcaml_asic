"""Runner and collection fixtures require no PDK or licensed tools."""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import venv


sys.dont_write_bytecode = True
# The modules under test are the ones Dune installs: `flow/hardcaml_asic_flow/`
# is put on the path and imported as a regular package, so these suites cover the
# installed implementation rather than a checkout-only copy of it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
from hardcaml_asic_flow import coarse_flow_oracle  # noqa: E402


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "src").mkdir()
        (self.bundle / "src/config.json").write_text("{}\n")
        self.manifest = {
            "schema_version": 1,
            "adapter": "LibreLane",
            "identity": "build-1",
            "top_module": "fixture_top",
            "files": [],
            "requested_tools": {"librelane": "1.2.3", "python": "3.11"},
            "target": {"technology": "ihp-sg13cmos5l", "external_references": []},
        }
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest))
        self.args = SimpleNamespace(
            bundle=self.bundle,
            runs=self.root / "runs",
            stage="synthesis",
            support_tools=self.root / "tools",
            pdk_root=self.root / "pdk",
            python=self.root / "venv/bin/python",
            allow_python_mismatch=False,
            native=True,
        )

    def ready(self):
        return {
            "ready": True,
            "issues": [],
            "waivers": [],
            "requested": self.manifest["requested_tools"],
            "actual": {"python": "3.11.9", "librelane": "1.2.3"},
            "execution_binding": {"schema_version": 1, "execution_mode": "native",
                "interpreter": str(self.args.python), "interpreter_flags": ["-I", "-B"]},
            "runtime_contract": {"execution_mode": "native", "observations": {
                "host": {"interpreter": str(self.args.python), "interpreter_flags": ["-I", "-B"]}}},
        }

    def test_structured_outcome_and_actual_preflight_are_persisted(self):
        decision = self.ready()
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=decision), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0):
            outcome = coarse_flow_oracle.run(self.args)
        self.assertIsInstance(outcome, coarse_flow_oracle.RunOutcome)
        self.assertTrue(outcome.run_dir.is_absolute())
        self.assertTrue(outcome.run_record.is_absolute())
        self.assertEqual(outcome.run_record, outcome.run_dir / "run.json")
        self.assertEqual((outcome.status, outcome.exit_code), ("completed", 0))
        record = coarse_flow_oracle.read_json(outcome.run_record)
        self.assertEqual(coarse_flow_oracle.read_json(outcome.run_dir / "preflight.json"), decision)
        self.assertEqual(record["environment"], decision)
        self.assertRegex(outcome.run_dir.name, r"^\d{8}T\d{12}Z_[0-9a-f]{8}$")
        self.assertEqual(len(outcome.run_id), 32)
        self.assertEqual(record["run_id"], outcome.run_id)
        self.assertNotEqual(outcome.run_id, outcome.run_dir.name)

    def test_run_pending_preflight_exists_before_probe_and_retries_are_unique(self):
        def probe(*_args):
            pending = [p for p in self.args.runs.glob("*/preflight.json")
                       if coarse_flow_oracle.read_json(p).get("diagnostic", {}).get("status") == "pending"]
            self.assertEqual(len(pending), 1)
            record = coarse_flow_oracle.read_json(pending[0].parent / "run.json")
            self.assertEqual(record["status"], "starting")
            self.assertEqual(record["commands"], [])
            self.assertFalse(coarse_flow_oracle.read_json(pending[0])["ready"])
            return self.ready()
        with mock.patch.object(coarse_flow_oracle, "preflight", side_effect=probe), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0):
            first, second = coarse_flow_oracle.run(self.args), coarse_flow_oracle.run(self.args)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_preflight_failures_and_interruptions_are_durable_and_never_execute(self):
        cases = ((RuntimeError("probe failed"), "failed", 1),
                 (coarse_flow_oracle.FlowInterrupted(signal.SIGINT), "interrupted", 130),
                 (coarse_flow_oracle.FlowInterrupted(signal.SIGTERM), "interrupted", 143),
                 (KeyboardInterrupt(), "interrupted", 130))
        for error, status, code in cases:
            with self.subTest(code=code), \
                 mock.patch.object(coarse_flow_oracle, "preflight", side_effect=error), \
                 mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
                outcome = coarse_flow_oracle.run(self.args)
                self.assertEqual((outcome.status, outcome.exit_code), (status, code))
                report = coarse_flow_oracle.read_json(outcome.run_dir / "preflight.json")
                self.assertFalse(report["ready"])
                self.assertEqual(report["diagnostic"]["status"], status)
                self.assertEqual(coarse_flow_oracle.read_json(outcome.run_record)["environment"], report)
                self.assertFalse((outcome.run_dir / "project").exists())
                execute.assert_not_called()

    def test_readiness_failure_keeps_exact_payload_without_execute(self):
        decision = {"ready": False, "issues": ["missing tool"], "actual": {}}
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=decision), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual((outcome.status, outcome.exit_code), ("failed", 1))
        self.assertEqual(coarse_flow_oracle.read_json(outcome.run_dir / "preflight.json"), decision)
        self.assertEqual(coarse_flow_oracle.read_json(outcome.run_record)["environment"], decision)
        execute.assert_not_called()

    def test_destination_allocation_failure_never_probes(self):
        self.args.runs.write_text("owner file")
        with mock.patch.object(coarse_flow_oracle, "preflight") as probe, \
             mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
            with self.assertRaises(OSError):
                coarse_flow_oracle.run(self.args)
        probe.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(self.args.runs.read_text(), "owner file")

    def test_real_preflight_signal_is_scoped_and_durable(self):
        before = signal.getsignal(signal.SIGTERM)
        def probe(*_args):
            os.kill(os.getpid(), signal.SIGTERM)
        with mock.patch.object(coarse_flow_oracle, "preflight", side_effect=probe), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual((outcome.status, outcome.exit_code), ("interrupted", 143))
        self.assertFalse(coarse_flow_oracle.read_json(outcome.run_dir / "preflight.json")["ready"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        execute.assert_not_called()

    def test_missing_manifest_fails_before_allocation(self):
        (self.bundle / "manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            coarse_flow_oracle.run(self.args)
        self.assertFalse(self.args.runs.exists())

    def test_interrupted_manifest_read_does_not_invent_run(self):
        with mock.patch.object(coarse_flow_oracle, "read_json", side_effect=coarse_flow_oracle.FlowInterrupted(signal.SIGINT)), \
             mock.patch.object(coarse_flow_oracle, "preflight") as probe:
            with self.assertRaises(coarse_flow_oracle.FlowInterrupted):
                coarse_flow_oracle.run(self.args)
        self.assertFalse(self.args.runs.exists())
        probe.assert_not_called()

    def test_pending_publication_failure_keeps_failed_run_without_probes(self):
        original_save = coarse_flow_oracle.save
        def save(path, value):
            if path.name == "preflight.json":
                raise PermissionError("report unavailable")
            original_save(path, value)
        with mock.patch.object(coarse_flow_oracle, "save", side_effect=save), \
             mock.patch.object(coarse_flow_oracle, "preflight") as probe, \
             mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual((outcome.status, outcome.exit_code), ("failed", 1))
        self.assertIn("report unavailable", coarse_flow_oracle.read_json(outcome.run_record)["error"])
        probe.assert_not_called()
        execute.assert_not_called()

    def test_terminal_publication_failure_keeps_prior_diagnostic_and_failed_run(self):
        original_save = coarse_flow_oracle.save
        def save(path, value):
            if path.name == "preflight.json" and value.get("ready"):
                with mock.patch.object(coarse_flow_oracle.os, "replace", side_effect=OSError("disk full")):
                    original_save(path, value)
            else:
                original_save(path, value)
        with mock.patch.object(coarse_flow_oracle, "save", side_effect=save), \
             mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.ready()), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as execute:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual((outcome.status, outcome.exit_code), ("failed", 1))
        self.assertEqual(coarse_flow_oracle.read_json(outcome.run_dir / "preflight.json")["diagnostic"]["status"], "pending")
        self.assertIn("disk full", coarse_flow_oracle.read_json(outcome.run_record)["error"])
        execute.assert_not_called()

    def test_preflight_preserves_real_collateral_indexes_and_directory_metadata(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Bundle test", GIT_COMMITTER_NAME="Bundle test",
                   GIT_AUTHOR_EMAIL="bundle@test.invalid", GIT_COMMITTER_EMAIL="bundle@test.invalid",
                   GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="commit.gpgSign",
                   GIT_CONFIG_VALUE_0="false", GIT_CONFIG_KEY_1="core.hooksPath",
                   GIT_CONFIG_VALUE_1=os.devnull, GIT_CONFIG_KEY_2="init.templateDir",
                   GIT_CONFIG_VALUE_2="")
        repo = self.root / "collateral"
        repo.mkdir()
        reference = repo / "reference.txt"
        reference.write_text("fixture collateral\n")
        for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "baseline")):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                           env=env, text=True).strip()
        # Unchanged content with stale index stat data forces ordinary status to
        # refresh its index, making this a real write-regression test.
        info = reference.stat()
        os.utime(reference, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        self.manifest["requested_tools"].update(support_tools_revision=revision,
                                                pdk_revision=revision)
        self.manifest["target"]["external_references"] = [
            {"source": "Support_tools", "path": "reference.txt", "role": "fixture"}]
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest))

        def state():
            index = repo / ".git/index"
            return (index.read_bytes(), index.stat().st_mode, index.stat().st_mtime_ns,
                    (repo / ".git").stat().st_mtime_ns)

        before = state()
        host = {"python_version": "3.11.9", "librelane_version": "1.2.3",
                "interpreter": str(self.args.python), "interpreter_flags": ["-I", "-B"],
                "tcl": {"ready": True}}
        with mock.patch.dict(os.environ, {**env, "GIT_OPTIONAL_LOCKS": "1"}), \
             mock.patch.object(coarse_flow_oracle.runtime_observation, "observe_host", return_value=host):
            report = coarse_flow_oracle.preflight(self.bundle, repo, repo, self.args.python, native=True)
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["ready"])
        self.assertEqual(state(), before)

    def test_failed_and_interrupted_attempts_keep_machine_readable_identity(self):
        cases = (
            (RuntimeError("flow failed"), "failed", 1, None),
            (coarse_flow_oracle.FlowInterrupted(signal.SIGINT), "interrupted", 130, "SIGINT"),
            (coarse_flow_oracle.FlowInterrupted(signal.SIGTERM), "interrupted", 143, "SIGTERM"),
        )
        for error, status, code, interrupted_by in cases:
            with self.subTest(status=status, code=code):
                with mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.ready()), \
                     mock.patch.object(coarse_flow_oracle, "run_logged", side_effect=error):
                    outcome = coarse_flow_oracle.run(self.args)
                self.assertEqual((outcome.status, outcome.exit_code), (status, code))
                record = coarse_flow_oracle.read_json(outcome.run_record)
                self.assertEqual(record["status"], status)
                if interrupted_by:
                    self.assertEqual(record["interrupted_by"], interrupted_by)

    def test_standalone_adapter_prints_only_the_run_record_path(self):
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.ready()), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(coarse_flow_oracle.execute(self.args), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(Path(lines[0]).is_file())

    def test_run_logged_terminates_and_waits_for_child_group(self):
        process = mock.MagicMock()
        process.pid = 4321
        process.wait.side_effect = [coarse_flow_oracle.FlowInterrupted(signal.SIGTERM), 143]
        process.__enter__.return_value = process
        process.__exit__.return_value = False
        record = {"commands": []}
        record_path = self.root / "record.json"
        with mock.patch.object(coarse_flow_oracle.subprocess, "Popen", return_value=process), \
             mock.patch.object(coarse_flow_oracle.os, "killpg") as killpg:
            with self.assertRaises(coarse_flow_oracle.FlowInterrupted):
                coarse_flow_oracle.run_logged(["tool"], self.root, os.environ.copy(),
                                  self.root / "log", record, record_path)
        self.assertEqual(killpg.call_args_list, [mock.call(4321, signal.SIGTERM),
                                               mock.call(4321, signal.SIGKILL)])
        self.assertEqual(process.wait.call_count, 2)

    def test_atomic_save_preserves_existing_record_on_publication_failure(self):
        path = self.root / "results.json"
        path.write_text("old evidence\n")
        with mock.patch.object(coarse_flow_oracle.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                coarse_flow_oracle.save(path, {"new": "partial"})
        self.assertEqual(path.read_text(), "old evidence\n")
        self.assertEqual(list(self.root.glob(".results.json.*")), [])


class PostcheckTest(unittest.TestCase):
    def setUp(self):
        cleanup = mock.patch.object(coarse_flow_oracle.execution_transport, "cleanup")
        cleanup.start()
        self.addCleanup(cleanup.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "run"
        flow = self.run_dir / "project/runs/asic/final"
        (flow / "gds").mkdir(parents=True)
        (flow / "nl").mkdir()
        (flow / "gds/fixture_top.gds").write_text("gds\n")
        (flow / "nl/fixture_top.nl.v").write_text("module fixture_top; endmodule\n")
        (self.run_dir / "project/manifest.json").write_text(json.dumps({
            "top_module": "fixture_top",
        }))
        (self.run_dir / "run.json").write_text(json.dumps({
            "run_id": "attempt-1",
            "build_identity": "build-1",
            "status": "completed",
            "completed_stage": "full",
            "outputs": {"flow": "project/runs/asic"},
            "environment": {"requested": {"librelane": "1.2.3"}},
        }))
        self.support = self.root / "support"
        (self.support / "precheck").mkdir(parents=True)
        # The shape that matters is the pinned fetchTarball: postcheck reads the
        # nixpkgs revision out of this file rather than out of a channel.
        self.nixpkgs = "https://github.com/NixOS/nixpkgs/archive/fixture-revision.tar.gz"
        (self.support / "precheck/default.nix").write_text(
            'let pkgs = import (fetchTarball "%s") {}; in pkgs.mkShell {}\n'
            % self.nixpkgs)
        (self.support / "precheck/tool-versions.json").write_text(json.dumps({
            "klayout": "0.28.17",
        }))
        (self.support / "tech").mkdir()
        self.pdk = self.root / "pdk"
        cells = self.pdk / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog"
        cells.mkdir(parents=True)
        (cells / "sg13cmos5l_udp.v").write_text("\n")
        (cells / "sg13cmos5l_stdcell.v").write_text("\n")
        io_model = self.pdk / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog"
        io_model.mkdir(parents=True)
        (io_model / "sg13cmos5l_io.v").write_text("\n")
        self.testbench = self.root / "custom_tb.v"
        self.testbench.write_text("module memory_tb; endmodule\n")
        self.precheck_python = self.root / "precheck-python"
        self.precheck_python.write_text("\n")
        self.args = SimpleNamespace(
            run_dir=self.run_dir,
            support_tools=self.support,
            pdk_root=self.pdk,
            precheck_python=self.precheck_python,
            testbench=self.testbench,
            testbench_top="memory_tb",
        )
        self.probes = ["KLayout 0.28.17", "0.28.17", "image-id", "Icarus 12"]

    def test_missing_input_fails_before_check_allocation(self):
        self.testbench.unlink()
        with self.assertRaises(coarse_flow_oracle.PrerequisiteError):
            coarse_flow_oracle.check(self.args)
        self.assertFalse((self.run_dir / "checks").exists())

    def test_explicit_non_example_top_is_forwarded_and_recorded(self):
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as run_logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.exit_code, 0)
        compile_command = run_logged.call_args_list[1].args[0]
        self.assertEqual(compile_command[compile_command.index("-s") + 1], "memory_tb")
        self.assertEqual(compile_command[0], "docker")
        self.assertIn("ghcr.io/librelane/librelane:1.2.3", compile_command)
        self.assertIn("--pull=never", compile_command)
        self.assertIsNotNone(run_logged.call_args_list[1].kwargs["cleanup"])
        record_path = next((self.run_dir / "checks").glob("*/postcheck.json"))
        self.assertEqual(coarse_flow_oracle.read_json(record_path)["inputs"]["testbench_top"],
                         "memory_tb")

    def test_real_precheck_venv_survives_quoted_shell_command(self):
        environment = self.root / "precheck with spaces"
        venv.EnvBuilder(symlinks=True, with_pip=False).create(environment)
        alias = self.root / "precheck alias"
        alias.symlink_to(environment, target_is_directory=True)
        python = alias / "bin/../bin/python"
        self.assertTrue(python.is_symlink())
        self.args.precheck_python = python
        (self.support / "precheck/precheck.py").write_text(
            "import json,sys; print(json.dumps([sys.prefix, sys.base_prefix]))\n")
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes) as probes, \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(probes.call_args_list[1].args[0][0], str(python))
        argv, cwd, env = logged.call_args_list[0].args[:3]
        command = argv[-1]
        self.assertIn(shlex.quote(str(python)), command)
        prefix, base = json.loads(subprocess.check_output(
            ["/bin/sh", "-c", command], cwd=cwd, env=env, text=True))
        self.assertEqual(Path(prefix).resolve(), environment)
        self.assertNotEqual(prefix, base)

    def test_nixpkgs_pin_comes_from_the_pinned_collateral_or_is_absent(self):
        pinned = self.support / "precheck/default.nix"
        self.assertEqual(coarse_flow_oracle.nixpkgs_pin(pinned), f"nixpkgs={self.nixpkgs}")
        # No pin in the file means no search path: an empty NIX_PATH would be a
        # worse answer than nix's own diagnostic, so the caller leaves it alone.
        unpinned = self.root / "unpinned.nix"
        unpinned.write_text("{ pkgs ? import <nixpkgs> {} }: pkgs.mkShell {}\n")
        self.assertIsNone(coarse_flow_oracle.nixpkgs_pin(unpinned))

    def test_precheck_shell_environment_carries_the_pinned_nixpkgs(self):
        """Both nix-shell invocations see <nixpkgs> from the copied default.nix."""
        probes = []

        def record(argv, cwd=None, env=None):
            probes.append((argv, env))
            return self.probes[len(probes) - 1]

        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=record), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as run_logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.exit_code, 0)
        klayout_argv, klayout_env = probes[0]
        self.assertEqual(klayout_argv[0], "nix-shell")
        self.assertEqual(klayout_env["NIX_PATH"], f"nixpkgs={self.nixpkgs}")
        logged_nix_shell = [call for call in run_logged.call_args_list
                            if call.args[0][0] == "nix-shell"]
        self.assertEqual(len(logged_nix_shell), 1)
        self.assertEqual(logged_nix_shell[0].args[2]["NIX_PATH"],
                         f"nixpkgs={self.nixpkgs}")

    def test_an_unpinned_precheck_shell_leaves_nix_path_alone(self):
        (self.support / "precheck/default.nix").write_text(
            "{ pkgs ? import <nixpkgs> {} }: pkgs.mkShell {}\n")
        seen = []

        def record(argv, cwd=None, env=None):
            seen.append(env)
            return self.probes[len(seen) - 1]

        # A NIX_PATH the caller already exported is not this test's subject; the
        # claim is only that an unpinned shell adds none.
        environment = {name: value for name, value in os.environ.items()
                       if name != "NIX_PATH"}
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=record), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0), \
             mock.patch.dict(coarse_flow_oracle.os.environ, environment, clear=True):
            coarse_flow_oracle.check(self.args)
        self.assertNotIn("NIX_PATH", seen[0])

    def test_interrupted_postcheck_is_finalized_with_signal_status(self):
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes), \
             mock.patch.object(coarse_flow_oracle, "run_logged",
                               side_effect=coarse_flow_oracle.FlowInterrupted(signal.SIGTERM)):
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.exit_code, 143)
        record_path = next((self.run_dir / "checks").glob("*/postcheck.json"))
        record = coarse_flow_oracle.read_json(record_path)
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(record["interrupted_by"], "SIGTERM")
        self.assertIsNotNone(record["ended_at"])


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "project/runs/asic").mkdir(parents=True)
        (self.root / "run.json").write_text(json.dumps({
            "run_id": "attempt-1", "build_identity": "build-1", "status": "completed",
            "requested_stage": "full", "completed_stage": "full",
            "outputs": {"flow": "project/runs/asic"},
            "environment": {"actual": {"librelane": "3.1.0.dev3"}},
        }))

    def put(self, relative, text):
        path = self.root / "project/runs/asic" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_complete(self):
        self.put("06-yosys-synthesis/reports/stat.json", json.dumps({
            "design": {"area": 123.5, "sequential_area": 40,
                       "num_cells": 15, "num_cells_by_type": {"sg13cmos5l_dfrbpq_1": 5}}
        }))
        self.put("final/metrics.csv", "timing__setup__ws__corner:slow,0.3\n"
                 "timing__hold__ws__corner:fast,0.1\nroute__drc_errors,0\n"
                 "design__lvs_error__count,0\nantenna__violating__nets,0\n"
                 "antenna__violating__pins,0\n")
        self.put("06-yosys-synthesis/state_out.json", json.dumps({
            "metrics": {"design__instance_unmapped__count": 0,
                        "synthesis__check_error__count": 0,
                        "design__inferred_latch__count": 0}}))
        result = coarse_flow_oracle.collect(self.root)
        self.assertEqual(result["metrics"]["mapped_area"]["value"], 123.5)
        self.assertEqual(result["metrics"]["setup_slack"]["corner"], "slow")
        self.assertEqual(result["timing_goal"], "pass")
        self.assertEqual(result["checks"]["lvs"]["status"], "pass")
        self.assertEqual(result["mapped_cell_types"]["sg13cmos5l_dfrbpq_1"], 5)
        self.assertEqual(result["synthesis_checks"]["unmapped_instances"]["value"], 0)

    def test_partial_and_absent(self):
        self.put("06-yosys-synthesis/reports/stat.json", json.dumps({
            "design": {"area": 12, "num_cells": 2}}))
        partial = coarse_flow_oracle.collect(self.root)
        self.assertEqual(partial["metrics"]["mapped_area"]["value"], 12)
        self.assertIsNone(partial["metrics"]["setup_slack"]["value"])
        self.assertEqual(partial["timing_goal"], "unknown")
        self.assertEqual(partial["checks"]["drc"]["status"], "unknown")
        (self.root / "project/runs/asic/06-yosys-synthesis/reports/stat.json").unlink()
        absent = coarse_flow_oracle.collect(self.root)
        self.assertIn("absent", absent["metrics"]["mapped_area"]["unavailable_reason"])

    def test_unconstrained_hold_sentinel(self):
        self.put("final/metrics.csv", "timing__setup__ws__corner:slow,18.0\n"
                 "timing__hold__ws__corner:slow,1.0000000433293989E+39\n")
        result = coarse_flow_oracle.collect(self.root)
        hold = result["metrics"]["hold_slack"]
        self.assertIsNone(hold["value"])
        self.assertEqual(hold["unavailable_reason"], "no constrained timing paths reported")
        self.assertEqual(result["timing_unconstrained_modes"], ["hold"])
        self.assertEqual(result["timing_goal"], "pass")
        self.put("final/metrics.csv", "timing__setup__ws__corner:slow,-0.5\n"
                 "timing__hold__ws__corner:slow,1.0000000433293989E+39\n")
        self.assertEqual(coarse_flow_oracle.collect(self.root)["timing_goal"], "fail")

    def test_malformed_reports_and_failed_process(self):
        self.put("06-yosys-synthesis/reports/stat.json", "{bad")
        self.put("final/metrics.csv", "timing__setup__ws,not-a-number\ninvalid-row\n")
        record = json.loads((self.root / "run.json").read_text())
        record["status"] = "failed"
        (self.root / "run.json").write_text(json.dumps(record))
        result = coarse_flow_oracle.collect(self.root)
        self.assertEqual(result["process_status"], "failed")
        self.assertIsNone(result["metrics"]["mapped_area"]["value"])
        self.assertEqual(len(result["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
