"""Real filesystem and old Python reader/new OCaml writer storage checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
TIME = 1760000000.123456
IDS = [f"{n:08x}" + "12344abc8def123456789abc" for n in range(32)]

def verify(consumer, workers=16):
    from hardcaml_asic_flow import result_store as old, phase4
    def run(**request):
        completed = subprocess.run([str(consumer)], input=json.dumps(request) + "\n",
                                   text=True, capture_output=True, check=True, timeout=15)
        return json.loads(completed.stdout)
    def allocation(root, ids=IDS, attempts=8, **kw):
        return run(op="allocate", path=str(root), time=TIME, ids=ids, attempts=attempts, **kw)
    def save(path, value):
        return run(op="save", path=str(path), value=value)
    with tempfile.TemporaryDirectory(prefix="result-store-cases-", dir="/tmp/opencode") as temp:
        root = Path(temp)
        pyroot, mlroot = root / "python", root / "ocaml"
        def old_allocate(ids):
            values = iter(ids)
            return old.allocate(pyroot, clock=lambda: datetime.fromtimestamp(TIME, timezone.utc),
                                id_factory=lambda: next(values))
        py = old_allocate(IDS)
        ml = allocation(mlroot)
        assert ml["identity"] == py.identity and ml["created_at"] == py.created_at
        assert Path(ml["directory"]).name == py.directory.name
        for n, instant in enumerate((0, -0.000001, 1760000000, 1760000000.999999)):
            stamp = datetime.fromtimestamp(instant, timezone.utc)
            timed = run(op="allocate", path=str(root / f"clock-{n}"), time=instant, ids=IDS)
            assert timed["created_at"] == stamp.isoformat()
            assert Path(timed["directory"]).name == stamp.strftime("%Y%m%dT%H%M%S%fZ") + "_" + IDS[0][:8]
        sentinel = Path(ml["directory"]) / "retained"
        sentinel.write_text("unfamiliar run")
        ml2 = allocation(mlroot)
        py2 = old_allocate(IDS)
        assert Path(ml2["directory"]).name == py2.directory.name
        assert sentinel.read_text() == "unfamiliar run"
        before = sorted(p.name for p in mlroot.iterdir())
        assert "error" in allocation(mlroot, ids=[IDS[0]] * 3, attempts=3)
        assert before == sorted(p.name for p in mlroot.iterdir())
        # Full UUID is retained, but only first eight digits determine collisions.
        assert "error" in allocation(mlroot, ids=[IDS[0][:8] + "0" * 24], attempts=1)
        for bad, attempts in [("invalid", 1), (IDS[0], 0)]:
            assert "error" in allocation(root / "invalid", ids=[bad], attempts=attempts)
        for n, spelling in enumerate((str(uuid.UUID(IDS[0])), "{" + str(uuid.UUID(IDS[0])) + "}",
                                      "urn:uuid:" + str(uuid.UUID(IDS[0])), IDS[0].upper())):
            assert allocation(root / f"uuid-{n}", ids=[spelling])["identity"] == uuid.UUID(spelling).hex
        assert "error" in allocation(root / "bad-uuid", ids=[IDS[0][:4] + "{" + IDS[0][4:] + "}"])
        blocker = root / "file-root"
        blocker.write_text("retained")
        assert "error" in allocation(blocker / "child")
        assert blocker.read_text() == "retained"
        for kind in ("file", "link"):
            collision_root = root / ("collision-" + kind)
            collision_root.mkdir()
            collision = collision_root / py.directory.name
            if kind == "file":
                collision.write_text("unfamiliar file")
            else:
                collision.symlink_to(root / "unfamiliar-referent")
            assert "error" in allocation(collision_root, ids=[IDS[0]], attempts=1)
            if kind == "link":
                assert collision.is_symlink()
            else:
                assert collision.read_text() == "unfamiliar file"
        alias = root / "alias"
        alias.symlink_to(mlroot, target_is_directory=True)
        linked = allocation(alias / "new")
        assert Path(linked["directory"]).parent == mlroot / "new"
        normalized = allocation(root / "absent" / ".." / "normalized")
        assert Path(normalized["directory"]).parent == root / "normalized"
        assert not (root / "absent").exists()
        dangling = root / "dangling"
        dangling.symlink_to(root / "real-new")
        through_link = allocation(dangling)
        assert Path(through_link["directory"]).parent == root / "real-new"
        loop = root / "loop"
        loop.symlink_to(loop)
        assert "error" in allocation(loop)
        # Keep all 16 allocations; smaller pools still exercise concurrent contention.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            allocations = list(pool.map(lambda _: allocation(root / "parallel", attempts=32), range(16)))
        directories = [a["directory"] for a in allocations]
        assert len(set(directories)) == 16
        for a in allocations:
            assert Path(a["directory"]).name.endswith(a["identity"][:8])
        randoms = [run(op="random", path=str(root / "random")) for _ in range(3)]
        assert len({a["identity"] for a in randoms}) == 3
        assert all(uuid.UUID(a["identity"]).version == 4 for a in randoms)
        # Exact pretty bytes on the finite supported record domain, not identity bytes.
        records = [None, {}, [], {"unicode": "é𝄞\n", "floats": [1.0, -0.0, 5e-324, 1e20],
                    "huge": 10**150, "nested": {"b": [True, None], "a": {}},
                    "future_backend_field": {"opaque_reference": "state.json"}}]
        target = root / "record.json"
        for record in records:
            assert save(target, record) is True
            assert target.read_bytes() == (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
            assert phase4.read_json(target) == record  # actual provider reader
            assert run(op="read", path=str(target)) == record
            old.save_json(target, record)
            assert run(op="read", path=str(target)) == record  # old writer/new reader
            assert target.stat().st_mode & 0o777 == 0o600
        for raw in ['{"x":1,"x":2}', 'NaN', 'Infinity', '{"x":1e999}', '{"x":"\\ud800"}', '{} trailing']:
            target.write_text(raw)
            assert "error" in run(op="read", path=str(target))
        opaque = root / "state.json"
        opaque.write_bytes(b'{"backend_metric":Infinity}\n')
        original = opaque.read_bytes()
        assert "error" in run(op="read", path=str(opaque))
        assert opaque.read_bytes() == original
        # Rename failure cleans temporary files and preserves the destination.
        directory = root / "directory.json"
        directory.mkdir()
        (directory / "retained").write_text("owned elsewhere")
        assert "error" in save(directory, {"new": True})
        assert (directory / "retained").read_text() == "owned elsewhere"
        assert not list(root.glob(".directory.json.*"))
        assert "error" in save(root / "missing" / "record.json", {})
        assert not (root / "missing").exists()
        # Destination symlink is replaced; backend referent remains opaque.
        target.unlink()
        target.symlink_to(opaque)
        assert save(target, {"ready": False}) is True
        assert not target.is_symlink() and opaque.read_bytes() == original
        assert run(op="validate", path=str(target)) == str(target)
        assert "error" in run(op="validate", path=str(directory))
        assert "error" in run(op="validate", path=str(root / "missing" / "output"))
        # Unix access validation and unwritable parent failure (non-root execution).
        locked = root / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            if os.geteuid() != 0:
                assert "error" in save(locked / "file", {})
                assert "error" in run(op="validate", path=str(locked / "file"))
                failure = allocation(locked, ids=[IDS[0]], attempts=8)
                assert "error" in failure and "ids exhausted" not in failure["error"]
        finally:
            locked.chmod(0o700)
        # Old-or-new visibility during two competing writers, no partial JSON.
        a, b = {"writer": "A", "payload": "a" * 65536}, {"writer": "B", "payload": "b" * 65536}
        save(target, a)
        writers = [subprocess.Popen([str(consumer)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True) for _ in range(2)]
        for p, value in zip(writers, [a, b]):
            p.stdin.write(json.dumps(dict(op="write_many", path=str(target), value=value)) + "\n")
            p.stdin.close()
        reads = 0
        while any(p.poll() is None for p in writers):
            assert json.loads(target.read_text()) in (a, b)
            reads += 1
        for p in writers:
            assert json.loads(p.stdout.read()) is True and p.wait() == 0
        assert reads > 0 and not list(root.glob(".record.json.*"))
        # Receipt shapes and diagnostics compare to the real Python constructors.
        bundle = root / "bundle"
        provenance = {"status": "stale", "future": {"tool": "not observed"}}
        pending = run(op="pending", bundle=str(bundle), provenance=provenance)
        assert pending == old.diagnostic(bundle, "pending", "preflight pending; tool probes have not completed", provenance=provenance)
        begun = run(op="begin", path=str(root / "results"), bundle=str(bundle), time=TIME, ids=IDS)
        receipt_path, report_path = Path(begun["receipt_path"]), Path(begun["report_path"])
        receipt = begun["receipt"]
        assert phase4.read_json(receipt_path) == receipt
        assert not report_path.exists()  # caller must write pending before its probe
        assert receipt == {"schema_version": 1, "record_type": "preflight_operation", "operation_id": IDS[0],
            "bundle": str(bundle), "started_at": py.created_at, "ended_at": None, "status": "pending",
            "report": "preflight.json", "exit_code": None}
        assert save(report_path, pending) is True
        for ready in (True, False):
            result = {"ready": ready, "issues": ["anything", 7], "additive": {"raw": "retained"}}
            classified = run(op="classify", value=result)
            assert classified == {"status": "ready" if ready else "failed", "exit_code": 0 if ready else 1, "result": result}
            save(report_path, result)
            assert phase4.read_json(report_path) == result
        for malformed in ({}, {"ready": 1, "issues": []}, {"ready": True, "issues": {}}, []):
            assert "error" in run(op="classify", value=malformed)
        receipt["future"] = {"retained": True}
        final = run(op="finish", value=receipt, ended_at=py.created_at, error="publication failed")
        assert final == dict(receipt, ended_at=py.created_at, status="failed", exit_code=1, error="publication failed")
        # A failure after allocation retains the reserved attempt rather than
        # silently deleting it/retrying or entering an existing run.
        failed = run(op="begin_fail", path=str(root / "failed-begin"), bundle=str(bundle), time=TIME, ids=IDS)
        assert "error" in failed
        retained = list((root / "failed-begin" / "operations").iterdir())
        assert len(retained) == 1 and not list(retained[0].iterdir())
        bad_bundle = run(op="begin", path=str(root / "invalid-bundle"), bundle="bad\u0000bundle", time=TIME, ids=IDS)
        assert "error" in bad_bundle and not (root / "invalid-bundle").exists()
    print(f"result-store: allocation/collision/concurrency ({workers} workers, 16 allocations)/failure/atomic visibility and Python record parity passed")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", type=Path)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(2, 17), default=16)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if not args.installed:
        sys.path.insert(0, str(repo / "flow"))
        verify(args.consumer.resolve(), args.workers)
        return
    with tempfile.TemporaryDirectory(prefix="installed-result-store-", dir="/tmp/opencode") as temporary:
        root = Path(temporary)
        prefix, consumer = root / "prefix", root / "consumer"
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer.mkdir()
        shutil.copyfile(repo / "test/result_store_consumer.ml", consumer / "result_store_consumer.ml")
        (consumer / "dune-project").write_text("(lang dune 3.17)\n(name installed_storage_consumer)\n")
        (consumer / "dune").write_text("(executable (name result_store_consumer) (libraries hardcaml_asic.execution unix))\n")
        env = dict(os.environ)
        env["OCAMLPATH"] = str(prefix / "lib") + (":" + env["OCAMLPATH"] if env.get("OCAMLPATH") else "")
        subprocess.run(["dune", "build", "-j20", "result_store_consumer.exe"], cwd=consumer, env=env, check=True)
        sys.path.insert(0, str(prefix / "lib/hardcaml_asic/flow"))
        previous_cwd = Path.cwd()
        os.chdir(root)
        auditing = True
        def audit(event, values):
            if auditing and event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
                if Path(os.fsdecode(values[0])).resolve().is_relative_to(repo):
                    raise AssertionError("checkout read in installed storage test")
        sys.addaudithook(audit)
        try:
            verify(consumer / "_build/default/result_store_consumer.exe", args.workers)
        finally:
            auditing = False
            os.chdir(previous_cwd)
        print("fresh installed storage consumer passed with Python checkout reads denied")

if __name__ == "__main__":
    main()
