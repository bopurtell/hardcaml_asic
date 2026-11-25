"""Exact-image launch and bounded cancellation fixtures: no real EDA or Docker."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
from hardcaml_asic_flow import execution_transport as transport, coarse_flow_oracle
import test_coarse_flow as fixtures


def docker_binding(rootless=False):
    return {"schema_version": 1, "execution_mode": "dockerized",
            "interpreter": "python3", "interpreter_flags": ["-I", "-B"],
            "image_identity": {"declared_ref": "ghcr.io/librelane/librelane:1.2.3",
                "image_id": "sha256:" + "1" * 64, "repo_digests": ["repo@sha256:" + "2" * 64],
                "binding": "local-image-id", "platform": {"os": "linux", "architecture": "amd64"}},
            "engine_identity": {"name": "docker", "executable": "/checked/docker",
                "rootless": rootless, "server_version": "27.fixture"}}


class TransportTest(unittest.TestCase):
    def test_command_binds_id_engine_flags_paths_and_rootless_user(self):
        for rootless in (True, False):
            binding = docker_binding(rootless)
            argv = transport.docker_command(binding, Path("/scratch/project"), Path("/scratch/pdk"),
                                            name=transport.owned_name(), pdk="fixture")
            argv += [*binding["interpreter_flags"], "-m", "librelane", "--help"]
            self.assertEqual(argv[0], "/checked/docker")
            self.assertIn("--pull=never", argv)
            self.assertEqual("--user" in argv, not rootless)
            self.assertEqual(argv[-6:], ["sha256:" + "1" * 64, "-I", "-B", "-m", "librelane", "--help"])
            self.assertIn("/scratch/project:/scratch/project:rw", argv)
            self.assertIn("/scratch/pdk:/scratch/pdk:ro", argv)
            self.assertNotIn(binding["image_identity"]["declared_ref"], argv)
            self.assertNotIn("--dockerized", argv)
            self.assertEqual([arg for arg in argv if arg.startswith("/")],
                             ["/checked/docker", "/scratch/project:/scratch/project:rw",
                              "/scratch/pdk:/scratch/pdk:ro", "/scratch/project"])
        binding = docker_binding()
        binding["interpreter_flags"] = ["-B"]
        report = {"runtime_contract": {"execution_mode": "dockerized", "observations": {"container": binding}}}
        self.assertEqual(transport.from_preflight(report, native=False)["interpreter_flags"], ["-B"])

    def test_missing_or_malformed_bindings_fail_closed(self):
        for field in ("image_identity", "engine_identity", "interpreter_flags", "interpreter"):
            binding = docker_binding()
            del binding[field]
            with self.subTest(field=field), self.assertRaises(transport.BindingError):
                transport.validate(binding)
        binding = docker_binding()
        binding["engine_identity"]["rootless"] = None
        with self.assertRaises(transport.BindingError):
            transport.validate(binding)
        with self.assertRaises(transport.BindingError):
            transport.from_preflight({"ready": True}, native=False)

    def test_observation_is_copied_and_retagging_cannot_redirect(self):
        observed = docker_binding()
        report = {"runtime_contract": {"execution_mode": "dockerized", "observations": {
            "container": observed}}}
        binding = transport.from_preflight(report, native=False)
        observed["image_identity"]["image_id"] = "sha256:" + "9" * 64
        with mock.patch.object(transport.observation, "output", return_value="sha256:" + "1" * 64) as probe:
            transport.verify_image(binding)
        self.assertEqual(probe.call_args.args[0][-1], "sha256:" + "1" * 64)
        with mock.patch.object(transport.observation, "output", return_value="sha256:" + "9" * 64):
            with self.assertRaises(transport.BindingError):
                transport.verify_image(binding)

    def test_engine_change_fails(self):
        with mock.patch.object(transport.observation, "output", return_value=json.dumps({
            "SecurityOptions": ["rootless"], "ServerVersion": "27.fixture"})):
            with self.assertRaises(transport.BindingError):
                transport.verify_engine(docker_binding())

    def test_ambient_runtime_overrides_are_removed(self):
        keys = ("PYTHONPATH", "PYTHONHOME", "LIBRELANE_IMAGE_OVERRIDE",
                "LIBRELANE_CONTAINER_ENGINE", "OPENLANE_CONTAINER_ENGINE", "_MOUNT_HOST_LIBRELANE")
        with mock.patch.dict(os.environ, dict.fromkeys(keys, "evil")):
            self.assertFalse(set(keys) & transport.environment().keys())
            self.assertEqual(os.environ["LIBRELANE_IMAGE_OVERRIDE"], "evil")

    def test_cleanup_is_bounded_and_targets_only_owned_name(self):
        binding = docker_binding()
        name = transport.owned_name()
        with mock.patch.object(transport.observation, "output") as output:
            transport.cleanup(binding, name)
        output.assert_called_once_with(["/checked/docker", "rm", "-f", name], timeout=5)
        with self.assertRaises(transport.BindingError):
            transport.cleanup(binding, "somebody-elses-container")
        with mock.patch.object(transport.observation.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("docker rm", 5)):
            with self.assertRaises(transport.observation.ObservationError):
                transport.cleanup(binding, name)


class BoundRunTest(fixtures.RunnerTest):
    def ready(self):
        report = super().ready()
        if not self.args.native:
            report["execution_binding"] = docker_binding()
            report["runtime_contract"] = {"execution_mode": "dockerized", "observations": {
                "container": docker_binding()}}
        return report

    def test_new_docker_run_uses_binding_for_legacy_and_new_policy(self):
        self.args.native = False
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.ready()), \
             mock.patch.object(transport, "verify_engine", return_value={}), mock.patch.object(transport, "verify_image"), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as logged:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual(outcome.exit_code, 0)
        argv = logged.call_args.args[0]
        self.assertEqual(argv[0], "/checked/docker")
        self.assertIn("sha256:" + "1" * 64, argv)
        self.assertNotIn("--dockerized", argv)
        self.assertIsNotNone(logged.call_args.kwargs["cleanup"])
        self.assertEqual(coarse_flow_oracle.read_json(outcome.run_record)["execution_binding"], docker_binding())

    def test_native_exact_literal_flags_and_sanitized_env(self):
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.ready()), \
             mock.patch.dict(os.environ, {"PYTHONPATH": "evil"}), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as logged:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(logged.call_args.args[0][:5], [str(self.args.python), "-I", "-B", "-m", "librelane"])
        self.assertNotIn("PYTHONPATH", logged.call_args.args[2])

    def test_missing_binding_records_failure_without_launch(self):
        report = self.ready()
        del report["execution_binding"]
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=report), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as logged:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual(outcome.status, "failed")
        logged.assert_not_called()

    def test_changed_binding_cannot_disagree_with_saved_observation(self):
        report = self.ready()
        report["execution_binding"]["interpreter"] = "/other/python"
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=report), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as logged:
            outcome = coarse_flow_oracle.run(self.args)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("differs from preflight observations", coarse_flow_oracle.read_json(outcome.run_record)["error"])
        logged.assert_not_called()

    def test_interruption_cleanup_failure_preserves_signal_and_escalates(self):
        process = mock.Mock(pid=12345)
        process.wait.side_effect = [coarse_flow_oracle.FlowInterrupted(signal.SIGTERM),
                                    subprocess.TimeoutExpired("client", 5), -9]
        record = {"commands": []}
        cleanup = mock.Mock(side_effect=RuntimeError("daemon unavailable"))
        with mock.patch.object(coarse_flow_oracle.subprocess, "Popen", return_value=process), \
             mock.patch.object(coarse_flow_oracle.os, "killpg") as kill:
            with self.assertRaises(coarse_flow_oracle.FlowInterrupted) as error:
                coarse_flow_oracle.run_logged(["client"], self.root, {}, self.root / "log", record,
                                  self.root / "record.json", cleanup=cleanup)
        self.assertEqual(error.exception.signum, signal.SIGTERM)
        self.assertEqual(record["container_cleanup"][0]["status"], "failed")
        self.assertIn(mock.call(12345, signal.SIGKILL), kill.call_args_list)
        self.assertEqual(process.wait.call_args_list[1:], [mock.call(timeout=5), mock.call(timeout=5)])
        self.assertEqual(cleanup.call_count, 2)

    def test_failed_launch_still_cleans_owned_container(self):
        record = {"commands": []}
        cleanup = mock.Mock()
        with mock.patch.object(coarse_flow_oracle.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaises(OSError):
                coarse_flow_oracle.run_logged(["client"], self.root, {}, self.root / "log", record,
                                  self.root / "record.json", cleanup=cleanup)
        cleanup.assert_called_once()
        self.assertEqual(record["container_cleanup"][0]["status"], "removed-or-absent")

    def test_cleanup_failure_prevents_success(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(coarse_flow_oracle.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                coarse_flow_oracle.run_logged(["client"], self.root, {}, self.root / "log", {"commands": []},
                    self.root / "record.json", cleanup=mock.Mock(side_effect=RuntimeError("timeout")))

    def test_real_client_and_term_ignoring_descendant_are_bounded(self):
        # A local fixture process group, not a container: both ignore TERM.
        child_pid = self.root / "descendant.pid"
        code = ("import signal,subprocess,sys,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
                f"Path({str(child_pid)!r}).write_text(str(child.pid)); time.sleep(60)")
        cleanup = mock.Mock()
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            coarse_flow_oracle.run_logged([sys.executable, "-I", "-B", "-c", code], self.root, {},
                self.root / "log", {"commands": []}, self.root / "record.json",
                cleanup=cleanup, timeout=0.3)
        self.assertLess(time.monotonic() - started, 12)
        self.assertEqual(cleanup.call_count, 2)
        pid = int(child_pid.read_text())
        # Linux may leave a reparented zombie briefly; it is no longer executing.
        for _ in range(40):
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split()[2]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            time.sleep(0.025)
        else:
            self.fail("TERM-ignoring descendant remains executing after escalation")


class BoundPostcheckTest(fixtures.PostcheckTest):
    def test_exact_image_and_engine_are_verified_before_simulator(self):
        record = coarse_flow_oracle.read_json(self.run_dir / "run.json")
        record["execution_binding"] = docker_binding(rootless=True)
        coarse_flow_oracle.save(self.run_dir / "run.json", record)
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes[:2]), \
             mock.patch.object(transport, "verify_engine", return_value={"name": "docker"}) as engine, \
             mock.patch.object(transport, "verify_image", return_value="sha256:" + "1" * 64) as image, \
             mock.patch.object(transport, "cleanup"), \
             mock.patch.object(coarse_flow_oracle.runtime_observation, "output", return_value="Icarus 12"), \
             mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0) as logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.exit_code, 0)
        engine.assert_called_once()
        image.assert_called_once()
        compile_command = logged.call_args_list[1].args[0]
        self.assertEqual(compile_command[0], "/checked/docker")
        self.assertIn("sha256:" + "1" * 64, compile_command)
        self.assertNotIn("--user", compile_command)
        tools = coarse_flow_oracle.read_json(outcome.check_record)["tools"]
        self.assertEqual(tools["execution_binding"], record["execution_binding"])

    def test_new_missing_binding_cannot_use_legacy_tag_fallback(self):
        record = coarse_flow_oracle.read_json(self.run_dir / "run.json")
        record["execution_binding_required"] = True
        coarse_flow_oracle.save(self.run_dir / "run.json", record)
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes[:2]), \
             mock.patch.object(coarse_flow_oracle, "run_logged") as logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.status, "failed")
        logged.assert_not_called()

    def test_image_verification_failure_prevents_simulator_invocation(self):
        record = coarse_flow_oracle.read_json(self.run_dir / "run.json")
        record["execution_binding"] = docker_binding()
        coarse_flow_oracle.save(self.run_dir / "run.json", record)
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes[:2]), \
             mock.patch.object(transport, "verify_engine", return_value={}), \
             mock.patch.object(transport, "verify_image", side_effect=transport.BindingError("ID mismatch")), \
             mock.patch.object(coarse_flow_oracle.runtime_observation, "output") as simulator, \
             mock.patch.object(coarse_flow_oracle, "run_logged") as logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.status, "failed")
        simulator.assert_not_called()
        logged.assert_not_called()

    def test_new_native_record_cannot_select_a_mutable_simulator_tag(self):
        record = coarse_flow_oracle.read_json(self.run_dir / "run.json")
        record["execution_binding"] = {"schema_version": 1, "execution_mode": "native",
            "interpreter": "/selected/python", "interpreter_flags": ["-I", "-B"]}
        coarse_flow_oracle.save(self.run_dir / "run.json", record)
        with mock.patch.object(coarse_flow_oracle, "command_output", side_effect=self.probes[:2]), \
             mock.patch.object(coarse_flow_oracle.runtime_observation, "observe_container") as probe, \
             mock.patch.object(coarse_flow_oracle, "run_logged") as logged:
            outcome = coarse_flow_oracle.check(self.args)
        self.assertEqual(outcome.status, "failed")
        probe.assert_not_called()
        logged.assert_not_called()


class BoundCollectorTest(fixtures.CollectorTest):
    def test_container_implementation_version_overrides_host_launcher(self):
        record = coarse_flow_oracle.read_json(self.root / "run.json")
        record["execution_mode"] = "dockerized"
        record["environment"]["actual"]["librelane"] = "host-launcher"
        record["environment"]["runtime_contract"] = {"observations": {
            "container": {"librelane_version": "container-implementation"}}}
        coarse_flow_oracle.save(self.root / "run.json", record)
        self.assertEqual(coarse_flow_oracle.collect(self.root)["tool"]["version"], "container-implementation")


if __name__ == "__main__":
    unittest.main()
