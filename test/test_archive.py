"""Archive selection, immutable inputs, and restoration; needs no EDA tools."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


sys.dont_write_bytecode = True
# The modules under test are the ones Dune installs: `flow/hardcaml_asic_flow/`
# is put on the path and imported as a regular package, so these suites cover the
# installed implementation rather than a checkout-only copy of it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
from hardcaml_asic_flow import archive  # noqa: E402

FLOW = "project/runs/asic"
TOP = "tt_um_asic_observable"


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "original-bundle"
        self.run = self.root / "run"
        self.output = self.root / "evidence"

        self.bundle_files = {
            "constraints/top.sdc": "create_clock -period 10 clk\n",
            "info.yaml": "project: fixture\n",
            "inputs/lib/design.ml": "let design = \"fixture\"\n",
            "simulation/fixture.v": "module fixture; endmodule\n",
            "src/config.json": "{}\n",
            "src/fixture.v": "module fixture; endmodule\n",
        }
        for relative, contents in self.bundle_files.items():
            self.put_at(self.bundle, relative, contents)
        self.manifest_value = self.make_manifest()
        self.write_manifests()
        shutil.copytree(self.bundle, self.run / "project")

        self.put("execution.log", "log\n")
        self.put("preflight.json", json.dumps({"ready": True}))
        self.put(f"{FLOW}/final/metrics.csv", "route__drc_errors,0\n")
        self.put(f"{FLOW}/final/metrics.json", "{}\n")
        self.put(f"{FLOW}/final/sdc/{TOP}.sdc", "sdc\n")
        self.put(f"{FLOW}/06-yosys-synthesis/reports/stat.rpt", "stat\n")
        self.put(f"{FLOW}/55-openroad-stapostpnr/summary.rpt", "summary\n")
        self.gds = self.put(f"{FLOW}/final/gds/{TOP}.gds", "gds-bytes\n")
        self.netlist = self.put(f"{FLOW}/final/nl/{TOP}.nl.v", "netlist\n")
        self.write_run("completed")

    def make_manifest(self):
        files = [{"path": relative, "sha256": digest(self.bundle / relative)}
                 for relative in sorted(self.bundle_files)]
        source_hash = digest(self.bundle / "inputs/lib/design.ml")
        return {
            "identity": "build-1",
            "schema_version": 1,
            "adapter": "LibreLane",
            "top_module": TOP,
            "source_inputs": [{
                "path": "lib/design.ml",
                "copy": "inputs/lib/design.ml",
                "sha256": source_hash,
                "git_status": " M lib/design.ml",
            }],
            "files": files,
            "synthesis_sources": ["src/fixture.v"],
            "simulation_sources": ["simulation/fixture.v"],
        }

    def write_manifests(self):
        data = json.dumps(self.manifest_value, indent=2) + "\n"
        self.put_at(self.bundle, "manifest.json", data)
        staged = self.run / "project"
        if staged.exists():
            self.put_at(staged, "manifest.json", data)

    def put_at(self, root, relative, text):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def put(self, relative, text):
        return self.put_at(self.run, relative, text)

    def write_run(self, status):
        manifest = self.run / "project/manifest.json"
        self.put("run.json", json.dumps({
            "run_id": "attempt-1",
            "build_identity": "build-1",
            "status": status,
            "requested_stage": "full",
            "completed_stage": "full" if status == "completed" else None,
            "bundle": str(self.bundle),
            "manifest_sha256": digest(manifest),
            "outputs": {"flow": FLOW, "project": "project"},
        }))
        self.put("results.json", "{}\n")

    def update_manifest(self, change, roots=None):
        change(self.manifest_value)
        data = json.dumps(self.manifest_value, indent=2) + "\n"
        for root in roots or (self.bundle, self.run / "project"):
            self.put_at(root, "manifest.json", data)
        self.write_run("completed")

    def write_check(self, name="check-1", gds=None):
        check = self.run / "checks" / name
        self.put_at(check, "tt/precheck/reports/results.md", "rows\n")
        self.put_at(check, "postcheck.log", "postcheck\n")
        self.put_at(check, "gate.out", "compiled simulator, not evidence\n")
        testbench = self.put_at(check, "testbench.v", "testbench\n")
        self.put_at(check, "postcheck.json", json.dumps({
            "checks": {"precheck": "pass", "gate_level": "pass"},
            "inputs": {
                "gds_sha256": gds or digest(self.gds),
                "netlist_sha256": digest(self.netlist),
                "testbench_sha256": digest(testbench),
            },
        }))
        return check

    def test_full_archive_preserves_complete_bundle(self):
        self.write_check()
        tarball, selected, omitted = archive.archive(self.run, self.output, False)
        names = {name for _, name in selected}
        self.assertIn(f"{FLOW}/final/gds/{TOP}.gds", names)
        self.assertIn("checks/check-1/tt/precheck/reports/results.md", names)
        self.assertNotIn("checks/check-1/gate.out", names)
        self.assertEqual(omitted, [])
        with tarfile.open(tarball) as stream:
            self.assertEqual(sorted(stream.getnames()), sorted(names))
        with tarfile.open(self.output / "input-bundle.tar.gz") as stream:
            bundle_names = set(stream.getnames())
        self.assertEqual(bundle_names, {"bundle/manifest.json"} | {
            f"bundle/{name}" for name in self.bundle_files})
        record = json.loads((self.output / "archive.json").read_text())
        self.assertEqual(record["input_bundle"]["source"], "original")
        self.assertEqual(record["input_bundle"]["sha256"],
                         digest(self.output / "input-bundle.tar.gz"))
        self.assertEqual(record["input_bundle"]["manifest_files_verified"], 6)
        for name in ("manifest.json", "run.json", "results.json", "postcheck.json",
                     "input-bundle.sha256"):
            self.assertTrue((self.output / name).is_file(), name)

    def test_synthesis_only_archive_still_preserves_complete_bundle(self):
        for path in (self.gds, self.netlist):
            path.unlink()
            path.parent.rmdir()
        (self.run / "results.json").unlink()
        _, selected, omitted = archive.archive(self.run, self.output, False)
        self.assertIn(f"{FLOW}/final/gds", omitted)
        self.assertIn(f"{FLOW}/final/nl", omitted)
        self.assertIn("results.json", omitted)
        self.assertTrue(selected)
        with tarfile.open(self.output / "input-bundle.tar.gz") as stream:
            self.assertIn("bundle/src/fixture.v", stream.getnames())
            self.assertIn("bundle/simulation/fixture.v", stream.getnames())
            self.assertIn("bundle/inputs/lib/design.ml", stream.getnames())

    def test_round_trip_needs_only_evidence_directory(self):
        expected_manifest = (self.bundle / "manifest.json").read_bytes()
        archive.archive(self.run, self.output, False)
        shutil.rmtree(self.bundle)
        shutil.rmtree(self.run)

        restored = self.root / "restored"
        restored.mkdir()
        with tarfile.open(self.output / "input-bundle.tar.gz") as stream:
            stream.extractall(restored, filter="data")
        manifest_path = restored / "bundle/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        run = json.loads((self.output / "run.json").read_text())
        checksum = (self.output / "input-bundle.sha256").read_text().split()[0]
        self.assertEqual(checksum, digest(self.output / "input-bundle.tar.gz"))
        self.assertEqual(manifest_path.read_bytes(), expected_manifest)
        self.assertEqual(digest(manifest_path), run["manifest_sha256"])
        self.assertEqual(manifest["identity"], run["build_identity"])
        for entry in manifest["files"]:
            self.assertEqual(digest(restored / "bundle" / entry["path"]),
                             entry["sha256"])

    def test_verified_staged_fallback(self):
        shutil.rmtree(self.bundle)
        archive.archive(self.run, self.output, False)
        record = json.loads((self.output / "archive.json").read_text())
        self.assertEqual(record["input_bundle"]["source"], "staged")

    def test_missing_original_and_incomplete_staged_copy_fails(self):
        shutil.rmtree(self.bundle)
        (self.run / "project/src/fixture.v").unlink()
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("no complete valid immutable bundle", str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_tampered_manifest_file_fails(self):
        for root in (self.bundle, self.run / "project"):
            (root / "src/fixture.v").write_text("tampered\n")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("changed bundle file", str(raised.exception))

    def test_manifest_and_source_metadata_linkage_are_verified(self):
        self.update_manifest(lambda value: value["source_inputs"][0].update(
            sha256="0" * 64))
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("source input metadata disagrees", str(raised.exception))

    def test_build_identity_mismatch_fails(self):
        self.update_manifest(lambda value: value.update(identity="different"))
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("identity", str(raised.exception))

    def test_unsafe_manifest_path_fails(self):
        self.update_manifest(lambda value: value["files"].append(
            {"path": "src/../escape", "sha256": "0" * 64}))
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("unsafe bundle path", str(raised.exception))

    def test_duplicate_manifest_path_fails(self):
        self.update_manifest(lambda value: value["files"].append(value["files"][0].copy()))
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("duplicate bundle path", str(raised.exception))

    def test_conflicting_manifest_paths_fail(self):
        self.update_manifest(lambda value: value["files"].append(
            {"path": "src", "sha256": "0" * 64}))
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("conflicting bundle paths", str(raised.exception))

    def test_staged_manifest_mismatch_fails_even_with_original(self):
        (self.run / "project/manifest.json").write_text("{}\n")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("staged manifest", str(raised.exception))

    def test_symlink_escape_fails(self):
        shutil.rmtree(self.bundle)
        target = self.root / "outside.v"
        target.write_text(self.bundle_files["src/fixture.v"])
        staged = self.run / "project/src/fixture.v"
        staged.unlink()
        staged.symlink_to(target)
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("symlink", str(raised.exception))

    def test_failed_force_preserves_existing_archive_and_readme(self):
        archive.archive(self.run, self.output, False)
        old_archive = (self.output / "archive.json").read_bytes()
        old_bundle = (self.output / "input-bundle.tar.gz").read_bytes()
        (self.output / "README.md").write_text("human account\n")
        for root in (self.bundle, self.run / "project"):
            (root / "src/fixture.v").write_text("tampered\n")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError):
            archive.archive(self.run, self.output, True)
        self.assertEqual((self.output / "archive.json").read_bytes(), old_archive)
        self.assertEqual((self.output / "input-bundle.tar.gz").read_bytes(), old_bundle)
        self.assertEqual((self.output / "README.md").read_text(), "human account\n")

    def test_force_rearchive_preserves_readme_and_is_deterministic(self):
        archive.archive(self.run, self.output, False)
        first = digest(self.output / "input-bundle.tar.gz")
        (self.output / "README.md").write_text("human account\n")
        (self.output / "postcheck-stale.json").write_text("stale\n")
        archive.archive(self.run, self.output, True)
        self.assertEqual(digest(self.output / "input-bundle.tar.gz"), first)
        self.assertEqual((self.output / "README.md").read_text(), "human account\n")
        self.assertFalse((self.output / "postcheck-stale.json").exists())

    def test_write_failure_does_not_publish_partial_replacement(self):
        archive.archive(self.run, self.output, False)
        old_archive = (self.output / "archive.json").read_bytes()
        with mock.patch.object(archive, "write_input_bundle",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                archive.archive(self.run, self.output, True)
        self.assertEqual((self.output / "archive.json").read_bytes(), old_archive)

    def test_populated_output_requires_force_and_two_checks_stay_distinct(self):
        self.write_check("check-1")
        self.write_check("check-2")
        self.output.mkdir()
        (self.output / "README.md").write_text("human account\n")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError):
            archive.archive(self.run, self.output, False)
        archive.archive(self.run, self.output, True)
        self.assertTrue((self.output / "postcheck-check-1.json").is_file())
        self.assertTrue((self.output / "postcheck-check-2.json").is_file())
        self.assertFalse((self.output / "postcheck.json").exists())
        self.assertEqual((self.output / "README.md").read_text(), "human account\n")

    def test_list_describes_complete_input_preservation(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["hardcaml-asic-flow", str(self.run), "--list"]):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(archive.main(), 0)
        self.assertIn("input-bundle: bundle/src/fixture.v", stdout.getvalue())
        self.assertIn("input-bundle: bundle/simulation/fixture.v", stdout.getvalue())
        self.assertIn("input-bundle: bundle/inputs/lib/design.ml", stdout.getvalue())
        self.assertIn("complete input-bundle files from original", stderr.getvalue())

    def test_list_rejects_unfinished_or_corrupted_runs(self):
        self.write_run("failed")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError):
            archive.list_archive(self.run)
        self.write_run("completed")
        (self.run / "project/manifest.json").write_text("{}\n")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError):
            archive.list_archive(self.run)

    def test_gds_hash_mismatch_and_unfinished_run_fail(self):
        self.write_check(gds="0" * 64)
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError):
            archive.archive(self.run, self.output, False)
        self.assertFalse(self.output.exists())
        shutil.rmtree(self.run / "checks")
        self.write_run("failed")
        with self.assertRaises(archive.coarse_flow_oracle.PrerequisiteError) as raised:
            archive.archive(self.run, self.output, False)
        self.assertIn("not completed", str(raised.exception))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
