"""The packaging contract: one source, package-relative data, honest identity.

These are source-tree tests. They check the properties that make the installed
command possible -- no checkout-relative runtime discovery, one maintained lock,
a version that matches `dune-project`, a `--version` that promises nothing P6.5
has not delivered -- without needing a prefix. The fresh-prefix evidence that an
installation actually behaves this way is `test/test_installed_prefix.py`, which
builds and installs and so is deliberately not part of `dune runtest`.

No PDK, container, EDA tool, network access or provisioning is involved.
"""

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO / "flow"
sys.path.insert(0, str(PACKAGE_ROOT))
from hardcaml_asic_flow import archive, cli, identity, managed_python, paths, coarse_flow_oracle, probe, report, runtime_contract, runtime_observation, execution_transport, result_store  # noqa: E402
from hardcaml_asic_flow import backend_protocol  # noqa: E402


class LayoutTest(unittest.TestCase):
    def test_every_installed_module_resolves_inside_the_package(self):
        # A module that still loaded itself from `scripts/` would pass its own
        # unit tests in a checkout and then be missing from a prefix.
        for module in (backend_protocol, managed_python, paths, probe, runtime_contract, runtime_observation):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    Path(module.__file__).resolve().parent, paths.PACKAGE_DIR)
                self.assertTrue(module.__name__.startswith("hardcaml_asic_flow."))

    def test_paths_is_the_only_module_that_derives_a_runtime_path(self):
        # Loading a sibling by file path and walking up to a repository root are
        # exactly what P6.4 removes. `paths` is the single place allowed to turn
        # a location into paths, and it derives every one of them from this
        # package's own __file__ -- so there is one thing to audit, not six.
        for module in (archive, cli, identity, managed_python, coarse_flow_oracle, report, runtime_contract, runtime_observation, execution_transport, result_store):
            source = Path(module.__file__).read_text()
            for construct in ("__file__", "spec_from_file_location",
                              "module_from_spec", "parents["):
                with self.subTest(module=module.__name__, construct=construct):
                    self.assertNotIn(construct, source)
        self.assertIn("Path(__file__).resolve().parent",
                      Path(paths.__file__).read_text())

    def test_dune_installs_exactly_the_package_files(self):
        stanzas = (PACKAGE_ROOT / "dune").read_text()
        runtime = {"__init__", "backend_protocol", "librelane_bridge", "stage_metadata_probe",
                   "probe", "runtime_observation", "runtime_contract", "managed_python", "paths",
                   "macro_flow", "physical_runtime", "timing_intent_runtime", "timing_physical_runtime"}
        installed = set(re.findall(r"\(hardcaml_asic_flow/(\w+)\.py as flow/hardcaml_asic_flow/\w+\.py\)", stanzas))
        self.assertEqual(installed, runtime)
        data = set(re.findall(r"as flow/hardcaml_asic_flow/data/([^\s)]+)", stanzas))
        self.assertEqual(data, {"toolchain.sh", "toolchain.lock", "managed-python.json", "version.txt"})
        native = re.sub(r";[^\n]*", "", (REPO / "bin/dune").read_text())
        self.assertRegex(native, r"\(executable\s+\(name flow_cli\)(?:(?!\(executable)[\s\S])*?\(public_name hardcaml-asic-flow\)")
        self.assertIn("(hardcaml_asic_flow/data/managed-python.json as "
                      "flow/hardcaml_asic_flow/data/managed-python.json)", stanzas)


class RuntimeDataTest(unittest.TestCase):
    def test_the_installed_lock_is_the_repository_lock(self):
        # One maintained source. check_bundle.py asserts the repository lock
        # against the emitted manifest, so a second handwritten copy under data/
        # could drift past that check; a symlink to it cannot.
        maintained = REPO / "toolchain.lock"
        self.assertEqual(paths.TOOLCHAIN_LOCK.read_text(), maintained.read_text())
        # data/ holds the provisioner, the lock and the generated version file,
        # and no second lock to edit by mistake.
        self.assertEqual(
            sorted(path.name for path in paths.DATA_DIR.iterdir()
                   if path.name.endswith(".lock")),
            ["toolchain.lock"])
        if (REPO / ".git").exists():
            # A real checkout. In a Dune build tree or an installed prefix the
            # same bytes arrive as a regular file, which is the point of the
            # symlink: nothing downstream has to know it was one.
            self.assertTrue(paths.TOOLCHAIN_LOCK.is_symlink())
            self.assertEqual(paths.TOOLCHAIN_LOCK.resolve(), maintained.resolve())

    def test_missing_runtime_data_names_the_prefix_and_searches_nowhere(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = Path(temporary) / "data/toolchain.lock"
            with self.assertRaises(paths.MissingRuntimeData) as raised:
                paths.require(absent, "the toolchain lock")
        message = str(raised.exception)
        self.assertIn(str(absent), message)
        self.assertIn("no source checkout is searched", message)

    def test_provision_runs_the_packaged_script_against_the_packaged_lock(self):
        captured = {}

        def fake_run(command, env=None, check=False):
            captured["command"] = command
            captured["env"] = env
            return mock.Mock(returncode=0)

        args = mock.Mock(toolchain=Path("/scratch/toolchain"), check=True, python=None,
                         offline=False, no_container=True)
        with mock.patch.object(cli.subprocess, "run", fake_run):
            self.assertEqual(cli.Operations().provision(args), 0)
        command = captured["command"]
        self.assertEqual(command[0], "bash")
        self.assertEqual(Path(command[1]), paths.PROVISIONER)
        self.assertEqual(command[2], "--lock-file")
        self.assertEqual(Path(command[3]), paths.TOOLCHAIN_LOCK)
        self.assertEqual(command[4:], ["--host-python", sys.executable, "--check", "--no-container"])
        self.assertEqual(captured["env"]["TOOLCHAIN"], "/scratch/toolchain")

    def test_provision_exposes_no_public_way_to_swap_the_pins(self):
        # --lock-file is internal plumbing. If it reached the public parser a
        # caller could provision against pins the bundle will then reject.
        parser = cli.build_parser()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["provision", "--lock-file", "/tmp/other.lock"])
        self.assertEqual(raised.exception.code, 2)
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["provision", "--host-python", sys.executable])
        self.assertEqual(raised.exception.code, 2)

    def test_the_provisioner_reads_the_lock_beside_it_by_default(self):
        script = (paths.DATA_DIR / "toolchain.sh").read_text()
        self.assertIn('data_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)', script)
        self.assertIn('lock_file="$data_dir/toolchain.lock"', script)
        self.assertNotIn("repo_root", script)


class IdentityTest(unittest.TestCase):
    def test_declared_version_matches_dune_project(self):
        # dune-project is the authority; the constant is a mirror so an
        # -I-isolated process needs no build metadata. This is the drift check.
        declared = re.search(r"^\(version (\S+)\)$",
                             (REPO / "dune-project").read_text(), re.MULTILINE)
        self.assertIsNotNone(declared)
        self.assertEqual(identity.DECLARED_VERSION, declared.group(1))

    def test_generated_version_file_wins_over_the_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "version.txt"
            generated.write_text("9.9.9\n")
            with mock.patch.object(identity, "VERSION_FILE", generated):
                self.assertEqual(identity.package_version(),
                                 ("9.9.9", "installed-package-metadata"))
                self.assertEqual(identity.describe()["package_version"], "9.9.9")

    def test_source_tree_falls_back_to_the_mirror_and_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(identity, "VERSION_FILE",
                                   Path(temporary) / "absent.txt"):
                self.assertEqual(
                    identity.package_version(),
                    (identity.DECLARED_VERSION, "source-tree-declaration"))

    def test_version_claims_no_revision_state_or_library_match(self):
        described = identity.describe()
        self.assertFalse(described["source"]["stamped"])
        self.assertIsNone(described["source"]["revision"])
        self.assertIsNone(described["source"]["inventory_digest"])
        self.assertIsNone(described["source"]["library_artifact_match"])
        self.assertEqual(described["source"]["state"], "unknown")
        self.assertIn("P6.5", described["source"]["note"])

    def test_version_is_json_and_exits_zero_while_unstamped(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            self.assertEqual(cli.main(["--version", "--json"]), 0)
        described = json.loads(stdout.getvalue())
        self.assertEqual(described["tool"], "hardcaml-asic-flow")
        self.assertEqual(described["supported_record_schemas"], [1])
        self.assertEqual(described["cli_contract"]["capabilities"]["stage_observation_schemas"],
                         {"write": 2, "read": [1, 2]})
        self.assertTrue(described["cli_contract"]["capabilities"]["producer_event_timestamps"])
        contract = described["cli_contract"]
        self.assertEqual(contract["version"], 4)
        self.assertEqual(contract["default_output"], "human")
        self.assertEqual(contract["json_option"], "--json")
        self.assertEqual(contract["results_defaults"]["root"], "<caller-cwd>/_flow_results")
        self.assertTrue(contract["capabilities"]["collect_json_writes_results"])
        self.assertFalse(contract["capabilities"]["provision_json"])
        self.assertFalse(contract["capabilities"]["archive_json_list"])
        self.assertEqual(described["module_dir"], str(paths.PACKAGE_DIR))

    def test_version_and_help_dispatch_no_operation(self):
        class Refuses:
            def __getattr__(self, name):
                raise AssertionError(f"--version must not reach {name}")

        for argv in ([], ["--help"], ["--version"], ["--version", "--json"]):
            with self.subTest(argv=argv):
                with mock.patch("sys.stdout", io.StringIO()):
                    try:
                        code = cli.main(argv, operations=Refuses())
                    except SystemExit as exit_code:
                        code = exit_code.code
                self.assertEqual(code, 0)


class LauncherTest(unittest.TestCase):
    LAUNCHER = PACKAGE_ROOT / "hardcaml-asic-flow"

    def test_launcher_routes_to_native_owner(self):
        script = self.LAUNCHER.read_text()
        # The source adapter invokes the native owner directly.
        self.assertIn('exec "$binary" "$@"', script)
        self.assertIn("bin/flow_cli.exe", script)
        self.assertTrue(os.access(self.LAUNCHER, os.X_OK))

    def test_launcher_diagnoses_a_missing_install_without_falling_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            (prefix / "bin").mkdir()
            copy = prefix / "bin/hardcaml-asic-flow"
            copy.write_bytes(self.LAUNCHER.read_bytes())
            copy.chmod(0o755)
            completed = subprocess.run([str(copy), "--version"], text=True,
                                       capture_output=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("missing native CLI", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_development_adapter_diagnoses_missing_build_at_unrelated_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "renamed-flow"
            link.symlink_to(self.LAUNCHER)
            completed = subprocess.run([str(link), "--version"], text=True,
                                       capture_output=True, check=False)
        # This development-only shell adapter cannot discover a build from an
        # unrelated symlink directory. Installed native symlink discovery is
        # exercised by the installed-prefix suite.
        self.assertEqual(completed.returncode, 2)
        self.assertIn("missing native CLI", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
