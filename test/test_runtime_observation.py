"""Synthetic runtime metadata and daemon lifecycle; never launch a physical flow."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
from hardcaml_asic_flow import runtime_contract as rc, runtime_observation as ro, phase4

ROOT = Path(__file__).resolve().parents[1]
HOST = {"python_version": "3.11.17", "librelane_version": "3.1.0.dev3",
        "requires_python": ">=3.10", "metadata_error": None,
        "tcl": {"ready": True, "version": "8.6.16", "error": None}}
CONTAINER = {k: v for k, v in HOST.items() if k != "tcl"}
CONTAINER["python_version"] = "3.13.13"
IMAGE_ID = "sha256:" + "1" * 64
IMAGE = [{"Id": IMAGE_ID, "RepoDigests": ["official@sha256:" + "2" * 64],
          "Os": "linux", "Architecture": "amd64"}]


def policy():
    return rc.policy_from_lock(ro.read_lock(ROOT / "toolchain.lock"))


class ObservationTest(unittest.TestCase):
    def engine(self, values):
        return mock.patch.object(ro, "output", side_effect=values)

    def test_host_literal_path_isolated_bounded_sanitized_child(self):
        poisoned = {k: "bad" for k in ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY",
                    "TCLLIBPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "GCONV_PATH", "GLIBC_TUNABLES")}
        with mock.patch.dict(os.environ, poisoned), mock.patch.object(ro.subprocess, "run", return_value=
                mock.Mock(returncode=0, stdout=json.dumps(HOST))) as run:
            before = os.environ.copy()
            value = ro.observe_host(Path("venv alias/bin/python"))
            self.assertEqual(os.environ, before)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["venv alias/bin/python", "-I", "-B", "-c"])
        self.assertEqual(value["interpreter"], argv[0])
        self.assertEqual(run.call_args.kwargs["timeout"], ro.TIMEOUT)
        self.assertFalse(set(poisoned) & run.call_args.kwargs["env"].keys())

    def test_image_id_binding_is_distinct_from_digest_and_cleanup(self):
        with mock.patch.object(ro.shutil, "which", return_value="/prefix/docker"), self.engine([
                '{"ServerVersion":"29","SecurityOptions":["name=rootless"]}', json.dumps(IMAGE),
                json.dumps(CONTAINER), ""]) as output:
            value = ro.observe_container("official:tag")
        argv = output.call_args_list[2].args[0]
        self.assertEqual(argv[:3], ["/prefix/docker", "run", "--rm"])
        for flag in ("--pull=never", "--read-only"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--entrypoint") + 1:],
                         ["python3", IMAGE_ID, "-I", "-B", "-c", ro.CONTAINER_CODE])
        self.assertFalse(any(x in argv for x in ("-v", "--mount", "-e", "--env", "--env-file")))
        name = argv[argv.index("--name") + 1]
        self.assertTrue(name.startswith("hardcaml-asic-metadata-"))
        self.assertEqual(output.call_args_list[3].args[0], ["/prefix/docker", "rm", "-f", name])
        self.assertEqual(output.call_args_list[3].kwargs["timeout"], ro.CLEANUP_TIMEOUT)
        self.assertEqual(value["image_identity"]["image_id"], IMAGE_ID)
        self.assertNotEqual(value["image_identity"]["repo_digests"][0].split("@")[-1], IMAGE_ID)
        self.assertTrue(value["engine_identity"]["rootless"])

    def test_image_packaging_fallback_records_same_mode_for_next_launch(self):
        missing = dict(CONTAINER, librelane_version=None, requires_python=None, metadata_error="not found")
        with mock.patch.object(ro.shutil, "which", return_value="/docker"), self.engine([
                "{}", json.dumps(IMAGE), json.dumps(missing), "", json.dumps(CONTAINER), ""]) as output:
            value = ro.observe_container("official:tag")
        self.assertEqual(value["interpreter_flags"], ["-B"])
        self.assertEqual(value["packaging_path"], "image-owned-environment")
        argv = output.call_args_list[4].args[0]
        self.assertEqual(argv[argv.index(IMAGE_ID) + 1:][:2], ["-B", "-c"])
        first = output.call_args_list[2].args[0]
        self.assertNotEqual(first[first.index("--name") + 1], argv[argv.index("--name") + 1])

    def test_probe_errors_cleanup_only_owned_container(self):
        failures = (ro.ObservationError("timeout"), "bad JSON", '{"x": NaN}', '{"x":1e999}',
                    '{}', json.dumps(dict(CONTAINER, requires_python=None)))
        for failure in failures:
            with self.subTest(failure=failure), mock.patch.object(ro.shutil, "which", return_value="/docker"), \
                 self.engine(["{}", json.dumps(IMAGE), failure, ""]) as output:
                with self.assertRaises(ro.ObservationError):
                    ro.observe_container("official:tag")
                self.assertEqual(len(output.call_args_list), 4)
                self.assertEqual(output.call_args_list[-1].args[0][1:3], ["rm", "-f"])

    def test_inspection_failure_never_runs_or_pulls(self):
        for inspection in (ro.ObservationError("missing image"), "[]",
                           json.dumps([dict(IMAGE[0], Id="repo@sha256:bad")]),
                           json.dumps([dict(IMAGE[0], Os=None)])):
            with mock.patch.object(ro.shutil, "which", return_value="/docker"), \
                 self.engine(["{}", inspection]) as output:
                with self.assertRaises(ro.ObservationError):
                    ro.observe_container("official:tag")
                self.assertEqual(len(output.call_args_list), 2)

    def test_timeout_interruption_cleanup_and_cleanup_failure_are_bounded(self):
        for error in (subprocess.TimeoutExpired(["docker", "run"], ro.TIMEOUT), KeyboardInterrupt()):
            calls = []
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                if argv[1] == "info":
                    return mock.Mock(returncode=0, stdout="{}")
                if argv[1] == "image":
                    return mock.Mock(returncode=0, stdout=json.dumps(IMAGE))
                if argv[1] == "run":
                    raise error
                return mock.Mock(returncode=0, stdout="")
            with mock.patch.object(ro.shutil, "which", return_value="/docker"), \
                 mock.patch.object(ro.subprocess, "run", side_effect=run):
                with self.assertRaises((ro.ObservationError, KeyboardInterrupt)):
                    ro.observe_container("official:tag")
            self.assertEqual(calls[-1][0][1:3], ["rm", "-f"])
            self.assertEqual(calls[-1][1]["timeout"], ro.CLEANUP_TIMEOUT)
            self.assertTrue(all(call[1]["timeout"] <= ro.TIMEOUT for call in calls))
        with mock.patch.object(ro.shutil, "which", return_value="/docker"), self.engine([
                "{}", json.dumps(IMAGE), json.dumps(CONTAINER), ro.ObservationError("cleanup timeout")]):
            with self.assertRaisesRegex(ro.ObservationError, "cleanup timeout"):
                ro.observe_container("official:tag")

    def test_timeout_and_nonzero_client_become_observation_errors(self):
        for failure in (subprocess.TimeoutExpired(["docker"], 20), OSError("missing")):
            with mock.patch.object(ro.subprocess, "run", side_effect=failure):
                with self.assertRaises(ro.ObservationError):
                    ro.output(["docker"])
        with mock.patch.object(ro.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="failed")):
            with self.assertRaisesRegex(ro.ObservationError, "failed"):
                ro.output(["docker"])

    def test_real_synthetic_venv_tcl_and_metadata_without_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            venv = Path(temporary) / "venv"
            subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(venv)], check=True)
            site = next((venv / "lib").glob("python*/site-packages"))
            dist = site / "librelane-3.1.0.dev3.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: librelane\nVersion: 3.1.0.dev3\nRequires-Python: >=3.10\n")
            (site / "tkinter.py").write_text("raise ImportError('broken Tcl fixture')\n")
            (site / "fixture_tcl.pth").write_text(
                f"import sys; sys.path.insert(0, {str(site)!r})\n")
            alias = Path(temporary) / "alias"
            alias.symlink_to(venv, target_is_directory=True)
            observed = ro.observe_host(alias / "bin/python")
            self.assertEqual(observed["interpreter"], str(alias / "bin/python"))
            self.assertEqual(observed["librelane_version"], HOST["librelane_version"])
            self.assertFalse(observed["tcl"]["ready"])
            self.assertIn("broken Tcl fixture", observed["tcl"]["error"])
            self.assertFalse(list(venv.rglob("*.pyc")))


class IntegrationTest(unittest.TestCase):
    def test_shared_policy_cases_and_container_subset(self):
        explicit = policy()
        legacy = rc.policy_from_requested({"python": "3.11", "librelane": "3.1.0.dev3"})
        self.assertFalse(ro.evaluate_observations(explicit, HOST, CONTAINER)["issues"])
        self.assertTrue(ro.evaluate_observations(legacy, HOST, CONTAINER)["issues"])
        self.assertFalse(ro.evaluate_observations(legacy, HOST, CONTAINER, allow_python_mismatch=True)["issues"])
        self.assertTrue(ro.evaluate_observations(legacy, HOST,
            dict(CONTAINER, librelane_version="9.9.9"), allow_python_mismatch=True)["issues"])
        self.assertTrue(ro.evaluate_observations(legacy, HOST,
            dict(CONTAINER, python_version="malformed"), allow_python_mismatch=True)["issues"])
        cases = [("host", "python_version", "3.12.1"), ("host", "librelane_version", "9.9.9"),
                 ("container", "python_version", "3.15.0"), ("container", "librelane_version", "9.9.9"),
                 ("container", "requires_python", None), ("container", "requires_python", ">=3.11")]
        for role, field, value in cases:
            host, container = copy.deepcopy(HOST), copy.deepcopy(CONTAINER)
            (host if role == "host" else container)[field] = value
            result = ro.evaluate_observations(explicit, host, container, allow_python_mismatch=True)
            self.assertTrue(result["issues"])
            self.assertFalse(result["waivers"])
        host = copy.deepcopy(HOST)
        host["tcl"]["ready"] = False
        self.assertTrue(ro.evaluate_observations(explicit, host, native=True)["issues"])
        self.assertFalse(ro.evaluate_observations(explicit, HOST, native=True)["issues"])
        full = ro.evaluate_observations(explicit, HOST, CONTAINER)
        subset = ro.evaluate_container(explicit, CONTAINER)
        self.assertEqual(subset["comparisons"], [c for c in full["comparisons"] if c["role"] == "container"])

    def preflight(self, requested, *, native=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text(json.dumps({
                "schema_version": 1, "adapter": "LibreLane", "files": [],
                "requested_tools": dict(requested, support_tools_revision="rev", pdk_revision="rev"),
                "target": {"external_references": [], "technology": "fixture"}}))
            with mock.patch.object(phase4, "command_output", return_value="rev") as commands, \
                 mock.patch.object(ro, "observe_host", return_value=dict(HOST,
                    interpreter=str(root / "venv/bin/python"), interpreter_flags=["-I", "-B"])) as host, \
                 mock.patch.object(ro, "observe_container", return_value=dict(CONTAINER,
                    interpreter="python3", interpreter_flags=["-I", "-B"],
                    engine_identity={"name": "docker", "executable": "/checked/docker",
                                     "server_version": "fixture", "rootless": False},
                    image_identity={"image_id": IMAGE_ID, "declared_ref": "ghcr.io/librelane/librelane:3.1.0.dev3",
                                    "binding": "local-image-id", "repo_digests": [],
                                    "platform": {"os": "linux", "architecture": "amd64"}})) as container:
                report = phase4.preflight(root, root, root, root / "venv/bin/python", native=native)
            return report, commands, host, container

    def test_preflight_policy_bundle_authority_legacy_new_native_and_fail_closed(self):
        requested = {"python": "3.11", "librelane": "3.1.0.dev3"}
        legacy, *_ = self.preflight(requested)
        self.assertFalse(legacy["ready"])
        p = policy()
        requested["runtime_policy"] = {k: p[k] for k in ("schema_version", "host", "implementation")}
        new, *_ = self.preflight(requested)
        self.assertTrue(new["ready"], new["issues"])
        self.assertEqual(new["actual"]["container_librelane"], "3.1.0.dev3")
        self.assertEqual(new["runtime_contract"]["authority"], "bundle.requested_tools")
        native, _, _, container = self.preflight(requested, native=True)
        self.assertTrue(native["ready"])
        container.assert_not_called()
        requested["runtime_policy"]["schema_version"] = 99
        failed, commands, host, container = self.preflight(requested)
        self.assertFalse(failed["ready"])
        commands.assert_not_called()
        host.assert_not_called()
        container.assert_not_called()


if __name__ == "__main__":
    unittest.main()
