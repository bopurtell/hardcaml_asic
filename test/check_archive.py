"""Actual OUR Python fixtures, native service differential and publication IO tests."""
import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
if os.environ.get("FLOW_TEST_REFERENCE_ROOT"):
    checkout = Path(os.environ["ARCHIVE_DENY_CHECKOUT"]).resolve()
    def deny_checkout(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve().is_relative_to(checkout):
                raise RuntimeError("checkout read denied: " + str(args[0]))
    sys.addaudithook(deny_checkout)
    from reference_harness import activate
    activate()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
import test_archive as fixtures
from hardcaml_asic_flow import archive as oracle, cli, ocaml_backend


def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def tree(p):
    return {str(x.relative_to(p)): ("link", os.readlink(x)) if x.is_symlink()
            else ("dir",) if x.is_dir() else ("file", sha(x))
            for x in p.rglob("*")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--installed-prefix")
    parser.add_argument("--retained-run")
    parser.add_argument("--smoke-output")
    args = parser.parse_args()
    helper = str(Path(args.cli).resolve())
    consumer = str(Path(args.consumer).resolve())
    if args.retained_run:
        run = Path(args.retained_run).resolve()
        record = json.loads((run / "run.json").read_text())
        original = Path(record["bundle"]).expanduser().resolve()
        before = tree(run)
        original_before = tree(original) if original.is_dir() else None
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            selected, omitted = oracle.list_archive(run)
        result = subprocess.run([helper, "--run-dir", str(run), "--list"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (result.stdout, result.stderr) == (stdout.getvalue(), stderr.getvalue())
        assert tree(run) == before
        assert original_before is None or tree(original) == original_before
        assert args.smoke_output
        output = Path(args.smoke_output).resolve()
        assert not output.exists(), "smoke must use a NEW disposable archive"
        result = subprocess.run([helper, "--run-dir", str(run), "--output", str(output), "--json"],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        archived = json.loads((output / "archive.json").read_text())
        assert archived["members"] == sorted(n for _, n in selected)
        assert archived["input_bundle"]["sha256"] == sha(output / "input-bundle.tar.gz")
        with tarfile.open(output / "input-bundle.tar.gz") as stream:
            for member in stream.getmembers():
                relative = member.name.removeprefix("bundle/")
                expected = record["manifest_sha256"] if relative == "manifest.json" else next(
                    x["sha256"] for x in json.loads((output / "manifest.json").read_text())["files"] if x["path"] == relative)
                h = hashlib.sha256()
                with stream.extractfile(member) as source:
                    for block in iter(lambda: source.read(65536), b""):
                        h.update(block)
                assert h.hexdigest() == expected
        assert tree(run) == before
        assert original_before is None or tree(original) == original_before
        print(f"retained run unchanged; read-only Python/native list parity: {len(selected)} reports, {len(omitted)} selection omissions")
        print(f"NEW archive {output}: reports {archived['archive_bytes']} bytes, input bundle {archived['input_bundle']['archive_bytes']} bytes")
        return
    with tempfile.TemporaryDirectory(prefix="native-archive-") as tmp:
        root = Path(tmp)
        if args.installed_prefix:
            prefix = Path(args.installed_prefix).resolve()
            source = root / "archive_consumer.ml"
            shutil.copyfile(Path(__file__).with_name("archive_consumer.ml"), source)
            consumer = str(root / "consumer")
            subprocess.run(["ocamlfind", "ocamlopt", "-package", "hardcaml_asic.execution", "-linkpkg",
                            "-o", consumer, str(source)], cwd=root,
                           env=dict(os.environ, OCAMLPATH=str(prefix / "lib")), check=True)
            # Execute fixtures and the differential oracle outside checkout, using
            # only installed modules; audit denies accidental provider reads.
            drivers = root / "drivers"
            drivers.mkdir()
            for name in ("check_archive.py", "test_archive.py"):
                shutil.copyfile(Path(__file__).with_name(name), drivers / name)
            from reference_harness import copy_reference, thin_prefix
            thin_prefix(prefix)
            reference = copy_reference(Path(__file__).resolve().parents[1] / "flow", root / "reference",
                                       prefix / "lib/hardcaml_asic/flow")
            shutil.copyfile(Path(__file__).with_name("reference_harness.py"), drivers / "reference_harness.py")
            env = dict(os.environ, PYTHONPATH=str(prefix / "lib/hardcaml_asic/flow"),
                        FLOW_TEST_REFERENCE_ROOT=str(reference),
                        FLOW_TEST_RUNTIME_ROOT=str(prefix / "lib/hardcaml_asic/flow"),
                       ARCHIVE_DENY_CHECKOUT=str(Path(__file__).resolve().parents[1]))
            subprocess.run([sys.executable, "-B", str(drivers / "check_archive.py"),
                            "--cli", str(prefix / "bin/hardcaml-asic-experimental-archive"),
                            "--consumer", consumer], cwd=root, env=env, check=True)
            fixture = fixtures.ArchiveTest()
            fixture.setUp()
            try:
                result = subprocess.run([str(prefix / "bin/hardcaml-asic-flow"), "archive",
                                         str(fixture.run), "--list"], cwd=root, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                assert "input-bundle: bundle/src/fixture.v" in result.stdout
                result = subprocess.run([str(prefix / "bin/hardcaml-asic-flow"), "archive",
                                         str(fixture.run), "--output", str(fixture.output), "--json"],
                                        cwd=root, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["archive"] == str(fixture.output)
            finally:
                fixture.doCleanups()
            print("installed independent archive consumer, outside-checkout fixtures and launcher passed")
            return

        denied = os.environ.get("ARCHIVE_DENY_CHECKOUT")
        if denied:
            def audit(event, values):
                if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
                    path = os.fsdecode(values[0])
                    if path.startswith(denied + "/"):
                        raise AssertionError("checkout read: " + path)
            sys.addaudithook(audit)

        def call(run, output=None, *, force=False, listing=False, extra=(), ok=True):
            cmd = [helper, "--run-dir", str(run)]
            if output is not None:
                cmd += ["--output", str(output)]
            if force:
                cmd += ["--force"]
            if listing:
                cmd += ["--list"]
            result = subprocess.run(cmd + list(extra), capture_output=True, text=True)
            assert (result.returncode == 0) == ok, (cmd, result.returncode, result.stdout, result.stderr)
            if not ok:
                assert result.returncode == 2
            return result

        def native_archive(run, output, force):
            result = subprocess.run(
                [helper, "--run-dir", str(run), "--output", str(output)] + (["--force"] if force else []),
                capture_output=True, text=True)
            if result.returncode:
                raise oracle.coarse_flow_oracle.PrerequisiteError(result.stderr)
            record = json.loads((output / "archive.json").read_text())
            # Public metadata sorts names; fixture expectations only use sets.
            return output / "reports.tar.gz", [(run / n, n) for n in record["members"]], record["omitted"]

        def native_list(run, output=print, diagnostics=None):
            result = subprocess.run([helper, "--run-dir", str(run), "--list"], capture_output=True, text=True)
            if result.returncode:
                raise oracle.coarse_flow_oracle.PrerequisiteError(result.stderr)
            for line in result.stdout.splitlines():
                output(line)
            (diagnostics or (lambda s: print(s, file=sys.stderr)))(result.stderr.rstrip("\n"))

        # Run the original fixtures unchanged against the actual native boundary.
        # The one Python-function monkeypatch test has a separate native IO failure
        # equivalent below; CLI main is replaced only to invoke the real helper.
        real_archive, real_list = oracle.archive, oracle.list_archive
        def native_main():
            run = Path(sys.argv[1])
            result = call(run, listing=True)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        suite = unittest.TestSuite()
        for name in unittest.defaultTestLoader.getTestCaseNames(fixtures.ArchiveTest):
            if name != "test_write_failure_does_not_publish_partial_replacement":
                suite.addTest(fixtures.ArchiveTest(name))
        with mock.patch.object(oracle, "archive", native_archive), mock.patch.object(oracle, "list_archive", native_list), \
                mock.patch.object(oracle, "main", native_main):
            result = unittest.TextTestRunner(verbosity=1).run(suite)
        assert result.wasSuccessful()

        count = 0
        def fixture():
            f = fixtures.ArchiveTest()
            f.setUp()
            return f

        def differential(change=lambda f: None):
            nonlocal count
            f = fixture()
            try:
                change(f)
                before = tree(f.run)
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    selected, omitted = real_list(f.run)
                result = call(f.run, listing=True)
                assert (result.stdout, result.stderr) == (stdout.getvalue(), stderr.getvalue())
                inspected = subprocess.run([consumer, "inspect", str(f.run)], capture_output=True, text=True, check=True)
                typed = json.loads(inspected.stdout)
                assert typed["members"] == [n for _, n in selected]
                assert typed["omitted"] == omitted
                pyout = f.root / "python"
                real_archive(f.run, pyout, False)
                result = call(f.run, f.output, extra=("--json", "--verbose"))
                assert json.loads(result.stdout) == {"archive": str(f.output), "reports": str(f.output / "reports.tar.gz")}
                a = json.loads((pyout / "archive.json").read_text())
                b = json.loads((f.output / "archive.json").read_text())
                for record, output in ((a, pyout), (b, f.output)):
                    assert record["archive_bytes"] == (output / "reports.tar.gz").stat().st_size
                    assert record["input_bundle"]["archive_bytes"] == (output / "input-bundle.tar.gz").stat().st_size
                    assert record["input_bundle"]["sha256"] == sha(output / "input-bundle.tar.gz")
                    assert (output / "input-bundle.sha256").read_text() == sha(output / "input-bundle.tar.gz") + "  input-bundle.tar.gz\n"
                    record.pop("created_at")
                    record.pop("archive_bytes")  # report gzip/header representation differs
                    record["input_bundle"].pop("archive_bytes")
                    record["input_bundle"].pop("sha256")  # new codec representation is independently verified
                assert a == b, (a, b)
                with tarfile.open(pyout / "reports.tar.gz") as p, tarfile.open(f.output / "reports.tar.gz") as n:
                    assert p.getnames() == n.getnames()
                    for pm, nm in zip(p.getmembers(), n.getmembers()):
                        assert p.extractfile(pm).read() == n.extractfile(nm).read()
                        assert (pm.mode, pm.uid, pm.gid, pm.mtime, pm.uname, pm.gname) == \
                               (nm.mode, nm.uid, nm.gid, nm.mtime, nm.uname, nm.gname)
                with tarfile.open(f.output / "input-bundle.tar.gz") as stream:
                    assert stream.getnames() == typed["input_members"]
                    for member in stream.getmembers():
                        assert member.isfile() and (member.mode, member.uid, member.gid, member.mtime) == (0o644, 0, 0, 0)
                        path = f.run / "project" / member.name.removeprefix("bundle/")
                        assert stream.extractfile(member).read() == path.read_bytes()
                for name in b["records"]:
                    assert (f.output / name).read_bytes() == (pyout / name).read_bytes()
                assert tree(f.run) == before
                count += 1
            finally:
                f.doCleanups()

        differential()
        differential(lambda f: f.write_check())
        differential(lambda f: (f.write_check("a"), f.write_check("z")))
        differential(lambda f: shutil.rmtree(f.bundle))
        differential(lambda f: (f.bundle / "src/fixture.v").write_text("changed original"))
        differential(lambda f: ((f.run / "results.json").unlink(), (f.run / "preflight.json").unlink(),
                                 f.gds.unlink(), f.netlist.unlink()))
        differential(lambda f: f.put("results.json", '{"unknown": NaN, "huge": ' + "9" * 200 + '}\n'))
        def moved_flow(f):
            new = f.run / "relocated"
            shutil.move(f.run / fixtures.FLOW, new)
            value = json.loads((f.run / "run.json").read_text())
            value["outputs"]["flow"] = "relocated"
            f.put("run.json", json.dumps(value))
        differential(moved_flow)
        def alias_original(f):
            alias = f.root / "alias"
            alias.symlink_to(f.bundle, target_is_directory=True)
            value = json.loads((f.run / "run.json").read_text())
            value["bundle"] = str(alias)
            f.put("run.json", json.dumps(value))
        differential(alias_original)
        def expanded_original(f):
            value = json.loads((f.run / "run.json").read_text())
            value["bundle"] = "~/original-bundle"
            f.put("run.json", json.dumps(value))
            os.environ["HOME"] = str(f.root)
        with mock.patch.dict(os.environ):
            differential(expanded_original)
        def absolute_overrides(f):
            value = json.loads((f.run / "run.json").read_text())
            value["outputs"] = {"flow": str(f.run / fixtures.FLOW), "project": str(f.run / "project")}
            f.put("run.json", json.dumps(value))
        differential(absolute_overrides)
        def absent_pinned(f):
            f.write_check()
            f.gds.unlink()
            f.netlist.unlink()
            (f.run / "checks/check-1/testbench.v").unlink()
        differential(absent_pinned)
        def null_expected(f):
            check = f.write_check()
            value = json.loads((check / "postcheck.json").read_text())
            value["inputs"]["gds_sha256"] = None
            (check / "postcheck.json").write_text(json.dumps(value))
            f.gds.write_text("changed bytes with no recorded expected hash\n")
        differential(null_expected)
        def empty_dirs(f):
            shutil.rmtree(f.run / fixtures.FLOW / "55-openroad-stapostpnr")
            (f.run / fixtures.FLOW / "55-openroad-stapostpnr").mkdir()
            f.write_check()
            (f.run / "checks/check-1/tt/precheck/reports/results.md").unlink()
        differential(empty_dirs)
        def raw_bytes(f):
            for path in (f.bundle / "manifest.json", f.run / "project/manifest.json"):
                path.write_bytes((json.dumps(f.manifest_value, separators=(",", ":")) + "\r\n").encode())
            f.write_run("completed")
            f.put("preflight.json", "not even JSON: raw evidence\r\n")
        differential(raw_bytes)
        def nested_metadata(f):
            p = f.put(f"{fixtures.FLOW}/06-yosys-synthesis/reports/nested/λ-long.txt", "report\n")
            p.chmod(0o640)
            os.utime(p, (1234567890.25, 1234567890.25))
        differential(nested_metadata)

        # Inventory and linkage negatives use real fixtures; both roots carry the
        # exact same byte-pinned malformed manifest, so fallback cannot mask it.
        changes = [
            lambda m: m.update(adapter="other"), lambda m: m.update(schema_version=2),
            lambda m: m["files"][0].update(sha256="A" * 64),
            lambda m: m["files"][0].update(sha256="0" * 63),
            lambda m: m["files"][0].update(path="/absolute"),
            lambda m: m["files"][0].update(path="a\\b"),
            lambda m: m["files"][0].update(path="a//b"),
            lambda m: m["files"][0].update(path="a/./b"),
            lambda m: m["files"].append({"path": "manifest.json", "sha256": "0" * 64}),
            lambda m: m["synthesis_sources"].append(m["synthesis_sources"][0]),
            lambda m: m["simulation_sources"].append("missing"),
            lambda m: m["source_inputs"].clear(),
            lambda m: m["source_inputs"].append(copy.deepcopy(m["source_inputs"][0])),
            lambda m: m["source_inputs"][0].update(copy="inputs/wrong"),
        ]
        for change in changes:
            f = fixture()
            try:
                f.update_manifest(change)
                with unittest.TestCase().assertRaises((oracle.coarse_flow_oracle.PrerequisiteError, KeyError, ValueError)):
                    real_list(f.run)
                call(f.run, listing=True, ok=False)
                call(f.run, f.output, ok=False)
                assert not f.output.exists()
            finally:
                f.doCleanups()

        f = fixture()
        try:
            # Never combine two incomplete roots.
            (f.bundle / "src/fixture.v").unlink()
            (f.run / "project/simulation/fixture.v").unlink()
            call(f.run, listing=True, ok=False)
        finally:
            f.doCleanups()

        for relative in ("project/manifest.json", "project/src", fixtures.FLOW + "/final/gds", "checks/link"):
            f = fixture()
            try:
                if relative == "checks/link":
                    source = f.write_check("link")
                else:
                    source = f.run / relative
                target = f.root / "outside"
                source.rename(target)
                source.symlink_to(target, target_is_directory=target.is_dir())
                # Original bundle can rescue staged file corruption, but it must
                # never rescue a linked staged manifest or a report selection link.
                call(f.run, listing=True, ok=False)
            finally:
                f.doCleanups()

        f = fixture()
        try:
            f.write_check()
            call(f.run, f.output)
            (f.output / "README.md").write_text("human\n")
            (f.output / "unrelated").mkdir()
            (f.output / "unrelated/link").symlink_to("../../external")
            (f.output / "postcheck-stale.json").mkdir()
            (f.output / "postcheck-stale.json/data").write_text("owned directory")
            (f.output / "run.json").unlink()
            (f.output / "run.json").mkdir()
            call(f.run, f.output, force=True)
            assert (f.output / "run.json").is_file()
            assert not (f.output / "postcheck-stale.json").exists()
            assert os.readlink(f.output / "unrelated/link") == "../../external"
            previous = tree(f.output)
            result = subprocess.run([consumer, "rollback", str(f.run), str(f.output), "true", "/usr/bin/gzip", str(root)],
                                    capture_output=True, text=True)
            assert result.returncode == 2 and "injected second rename failure" in result.stderr
            assert tree(f.output) == previous
            assert not list(f.root.glob(".evidence.archive*"))

            bad = root / "bad-gzip"
            bad.write_text("#!/bin/sh\nexit 7\n")
            bad.chmod(0o755)
            call(f.run, f.output, force=True, extra=("--gzip", str(bad)), ok=False)
            assert tree(f.output) == previous
            assert not list(f.root.glob(".evidence.archive*"))

            mutate = root / "mutate-gzip"
            mutate.write_text("#!/bin/sh\nprintf changed > '" + str(f.bundle / "src/fixture.v") + "'\nexec /usr/bin/gzip \"$@\"\n")
            mutate.chmod(0o755)
            call(f.run, f.output, force=True, extra=("--gzip", str(mutate)), ok=False)
            assert tree(f.output) == previous
            assert not list(f.root.glob(".evidence.archive*"))
        finally:
            f.doCleanups()

        f = fixture()
        try:
            # A writer outside the service lock creates a README while packaging.
            # The publication-time force check must retain it and refuse clobber.
            collide = root / "collision-gzip"
            collide.write_text("#!/bin/sh\nmkdir -p '" + str(f.output) + "'\nprintf human > '" +
                               str(f.output / "README.md") + "'\nexec /usr/bin/gzip \"$@\"\n")
            collide.chmod(0o755)
            call(f.run, f.output, extra=("--gzip", str(collide)), ok=False)
            assert tree(f.output) == {"README.md": ("file", hashlib.sha256(b"human").hexdigest())}
            assert not list(f.root.glob(".evidence.archive*"))
        finally:
            f.doCleanups()

        f = fixture()
        try:
            # Caller run/output aliases resolve just as Path.resolve does; an
            # existing empty output does not require --force.
            f.output.mkdir()
            run_alias, out_alias = f.root / "run-alias", f.root / "out-alias"
            run_alias.symlink_to(f.run, target_is_directory=True)
            out_alias.symlink_to(f.output, target_is_directory=True)
            call(run_alias, listing=True)
            result = call(run_alias, out_alias, extra=("--json",))
            assert json.loads(result.stdout)["archive"] == str(f.output)
            assert out_alias.is_symlink()
            first_hash = sha(f.output / "input-bundle.tar.gz")
            for p in (f.bundle / "src/fixture.v", f.bundle / "manifest.json"):
                p.chmod(0o600)
                os.utime(p, (1234567890, 1234567890))
            call(f.run, f.output, force=True)
            assert sha(f.output / "input-bundle.tar.gz") == first_hash
        finally:
            f.doCleanups()

        # Slow compression provides an observable lock boundary. A second service
        # publisher fails immediately, then the first publishes an intact tree.
        f = fixture()
        try:
            slow = root / "slow-gzip"
            slow.write_text("#!/bin/sh\nsleep 0.3\nexec /usr/bin/gzip \"$@\"\n")
            slow.chmod(0o755)
            cmd = [helper, "--run-dir", str(f.run), "--output", str(f.output), "--gzip", str(slow)]
            first = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            import time
            deadline = time.monotonic() + 10
            while not (f.root / ".evidence.archive-lock").exists():
                assert first.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            call(f.run, f.output, ok=False)
            out, err = first.communicate(timeout=30)
            assert first.returncode == 0, (out, err)
            call(f.run, f.output, ok=False)
            assert not list(f.root.glob(".evidence.archive*"))
        finally:
            f.doCleanups()

        # Native compatibility callback and authoritative public routing.
        f = fixture()
        try:
            with mock.patch.object(ocaml_backend, "binary", return_value=Path(helper)):
                options = argparse.Namespace(run_dir=f.run, output=f.output, force=False, list=False,
                                             json=True, quiet=True, verbose=False)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    assert cli.Operations().archive(options) == 0
                    assert cli.Operations().archive(options) == 2
            call(f.run, listing=True, extra=("--json",), ok=False)
            call(f.run, ok=False)
            with mock.patch.object(ocaml_backend, "binary", side_effect=OSError("missing native archive")):
                with unittest.TestCase().assertRaises(OSError):
                    cli.Operations().archive(options)
        finally:
            f.doCleanups()
        print(f"archive: {count} actual Python differentials, native original fixtures, inventory/link/IO/mutation/rollback/concurrency cases passed")


if __name__ == "__main__":
    main()
