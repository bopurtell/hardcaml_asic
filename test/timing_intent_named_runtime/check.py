"""Production named preparation/lifecycle/transport/helper with fake EDA boundary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

EMITTER, CONSUMER, PLAN, HELPER = map(lambda x: Path(x).resolve(), sys.argv[1:])
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(Path(p).read_text())


def write(p, j):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(j) + "\n")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_snapshot(root):
    return {str(p.relative_to(root)): ("link", os.readlink(p)) if p.is_symlink() else
            ("directory",) if p.is_dir() else ("file", sha(p)) for p in root.rglob("*")}


with tempfile.TemporaryDirectory(prefix="named-timing-runtime-", dir="/tmp/opencode") as temp:
    tmp = Path(temp)
    bundle = tmp / "bundle"
    emitted = subprocess.run([EMITTER, bundle], text=True, capture_output=True)
    assert emitted.returncode == 0, emitted.stderr
    methodology = json.loads(emitted.stdout)
    manifest = read(bundle / "manifest.json")
    physical_bundle = tmp / "physical-bundle"
    physical_emitted = subprocess.run([EMITTER, physical_bundle, "signoff"], text=True, capture_output=True)
    assert physical_emitted.returncode == 0, physical_emitted.stderr
    physical_methodology = json.loads(physical_emitted.stdout)
    discovery = subprocess.run([PLAN], input=json.dumps(methodology["methodology"]["recipe"]),
                               text=True, capture_output=True, check=True)
    # Discover the complete inventory through the public domain projection.
    full = read(bundle / "manifest.json")
    discovery = subprocess.run([PLAN], input=json.dumps(dict(schema_version=4,
        stages=["synthesis", "floorplan", "placement", "cts", "routing", "timing", "signoff"], initial_boundary=None)),
        text=True, capture_output=True)
    assert discovery.returncode == 0, discovery.stderr
    operations = json.loads(discovery.stdout)["operation_sequence"]
    image = "sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a"
    for mode, expected in [("good", 0), ("later_failure", 1), ("unexecuted", 1), ("timeout", 124),
                           ("cancel", 130), ("cleanup", 1), ("publication_failure", 2), ("wrong_state", 1),
                           ("postverify", 2), ("later_failure_cleanup", 1), ("cancel_cleanup", 130),
                           ("failure_obstruction", 1), ("cancel_obstruction", 130), ("timeout_obstruction", 124),
                           ("malformed_obstruction", 1), ("large_closure", 0),
                           ("unsupported", 2), ("cancel_preflight", 143), ("cancel_initial", 143),
                           ("validation_failure", 2), ("probe_failure", 17),
                           ("root_equal", 2), ("root_inside", 2), ("root_alias", 2), ("root_ancestor", 2),
                           ("pdk_aliases", 0), ("alias_before", 1), ("alias_after", 1),
                           ("alias_post_runtime", 2), ("metadata_alias", 1)]:
        root = tmp / mode
        root.mkdir()
        write(root / "classic-inventory.json", [o["backend"] for o in operations])
        write(root / "classic-settings.json", {o["backend"]["implementation"]: o["settings"] for o in operations if o["settings"]})
        for ref in manifest["target"]["external_references"]:
            base = root / "tt" if ref["source"] == "Support_tools" else root / "pdk" / manifest["target"]["technology"]
            p = base / ref["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("floorplan fixture" if ref["source"] == "Support_tools" else "fixture")
        (root / "pdk").mkdir(exist_ok=True)
        tools = root / "bin"
        tools.mkdir()
        for name in ("python", "git", "stage_preparation_engine.py"):
            shutil.copyfile(HERE.parent / "stage_preparation_engine.py" if name == "stage_preparation_engine.py" else HERE / "engine.py", tools / name)
            (tools / name).chmod(0o700)
        shutil.copyfile(HERE / "engine.py", tools / "docker")
        (tools / "docker").chmod(0o700)
        env = dict(os.environ, SERVICE_ENGINE_ROOT=str(root), PREPARATION_IMAGE=image,
                   PREPARATION_REVISION="a" * 40, NAMED_RUNTIME_MODE=mode, PYTHONDONTWRITEBYTECODE="1")
        if mode == "probe_failure":
            env["PREPARATION_MODE"] = "probe_failure"
        if mode == "large_closure":
            collateral = root / "pdk-closure"
            collateral.mkdir()
            for i in range(1485):
                (collateral / f"pdk-{i:04d}.dat").write_bytes(b"PDK fixture\n")
            with (collateral / "native.gds").open("wb") as stream:
                stream.truncate(67_485_696)
        if mode == "pdk_aliases" or mode.startswith("alias_"):
            shared=root / "pdk/ihp-sg13g2/shared"
            shared.mkdir(parents=True)
            target=shared / "cells.lib"
            target.write_text('library (fixture) { time_unit : "1ns"; }\n')
            declared=root / "pdk/ihp-sg13cmos5l/aliases"
            declared.mkdir(parents=True)
            alias_paths=[]
            for i in range(1061 if mode == "pdk_aliases" else 1):
                p=declared / f"alias-{i}.lib"
                p.symlink_to(target)
                alias_paths.append(str(p))
            directory_alias=declared / "directory-alias"
            directory_alias.symlink_to(shared, target_is_directory=True)
            alias_paths.append(str(directory_alias / "cells.lib"))
            canonical_paths=[str(target)]
            if mode == "pdk_aliases":
                for i in range(422):
                    p=shared / f"canonical-{i}.dat"
                    p.write_text("required canonical collateral\n")
                    canonical_paths.append(str(p))
                gds=shared / "native.gds"
                with gds.open("wb") as stream:
                    stream.truncate(67_485_696)
                canonical_paths.append(str(gds))
            write(root / "alias-paths.json", alias_paths)
            write(root / "declared-pdk-paths.json", alias_paths + canonical_paths)
        cfg = dict(bundle=str(bundle), methodology=json.dumps(methodology), mode=mode,
            support_tools=str(root / "tt"), pdk_root=str(root / "pdk"), python=str(tools / "python"),
            docker=str(tools / "docker"), git=str(tools / "git"), resources=str(HELPER.parent),
            workspace=str(root / "workspace"), evidence_root=str(root / "evidence"))
        if mode == "unsupported":
            cfg.update(bundle=str(physical_bundle), methodology=json.dumps(physical_methodology))
        if mode in ("root_equal", "root_alias"):
            (root / "workspace").mkdir()
            (root / "workspace/sentinel").write_text("existing user workspace\n")
        if mode == "root_equal":
            cfg["evidence_root"] = cfg["workspace"]
        elif mode == "root_inside":
            cfg["evidence_root"] = str(root / "workspace/forbidden-metadata")
        elif mode == "root_alias":
            (root / "metadata-link").symlink_to(root / "workspace/forbidden-metadata")
            cfg["evidence_root"] = str(root / "metadata-link")
        elif mode == "root_ancestor":
            cfg["evidence_root"] = str(root)
        write(root / "config.json", cfg)
        before_paths = tree_snapshot(root) if mode.startswith("root_") else None
        result = subprocess.run([CONSUMER, root / "config.json"], text=True, capture_output=True, env=env, timeout=90)
        logs = {str(p.relative_to(root)): p.read_text() for p in (root / "workspace").rglob("*.log")}
        logs.update({str(p.relative_to(root)): p.read_text() for p in (root / "evidence").rglob("*.stderr")})
        assert bool(result.returncode) == bool(expected), (mode, result.returncode, result.stdout, result.stderr, logs)
        if mode.startswith("root_"):
            assert result.returncode == 2 and "evidence root must be separate" in result.stderr
            assert tree_snapshot(root) == before_paths, (mode, tree_snapshot(root), before_paths)
            assert not (root / "cancel-polled").exists()
            assert not (root / "early-expectation-observed.json").exists()
            if mode in ("root_inside", "root_ancestor"):
                assert not (root / "workspace").exists()
            continue
        operation = read(next((root / "evidence/operations").glob("*/operation.json")))
        initial = operation["timing_intent"]
        snapshot = operation["timing_intent_snapshot"]
        assert read(snapshot["path"]) == initial and sha(Path(snapshot["path"])) == snapshot["sha256"]
        assert Path(snapshot["path"]).stat().st_mode & 0o777 == 0o400
        report = read(next((root / "evidence/operations").glob("*/preflight.json")))
        assert report["timing_intent"] == initial and report["timing_intent_snapshot"] == snapshot
        if mode in ("unsupported", "cancel_preflight", "cancel_initial", "validation_failure", "probe_failure"):
            assert result.returncode == expected, (mode, result.stderr)
            assert not (root / "runtime-argv.jsonl").exists()
            assert not (root / "workspace").exists()
            if mode == "unsupported":
                assert "only schema4 named synthesis endpoint" in result.stderr
                assert not (root / "preparation-events.jsonl").exists()
            assert operation["status"] == report["status"] == ("interrupted" if mode.startswith("cancel") else "failed")
            assert operation["exit_code"] == report["exit_code"] == expected
            assert report["signal"] == (15 if mode.startswith("cancel") else None)
            if mode in ("validation_failure", "cancel_initial"):
                assert not (root / "early-expectation-observed.json").exists()
            continue
        execution_path = next((root / "workspace").glob("execution-*.json"))
        execution = read(execution_path)
        assert execution["timing_intent"] == initial == read(root / "early-expectation-observed.json")
        assert len(execution["artifacts"]) == 1, (mode, result.stdout, result.stderr)
        assert (root / "validation-observed").exists()
        successful=mode in ("good", "large_closure", "pdk_aliases")
        source_invalid=mode in ("wrong_state", "alias_before", "alias_after", "alias_post_runtime", "metadata_alias")
        assert execution["occurrences"][0]["status"] == ("completed" if successful else "interrupted" if mode in ("cancel", "cancel_cleanup", "cancel_obstruction") else "failed")
        checkpoint = Path(execution["artifacts"][0]["path"])
        before = read(root / "checkpoint-before.json")
        assert all(sha(Path(p)) == h for p, h in before.items()), mode
        directory = Path(execution["timing_intent"]["directory"])
        obstructed = mode in ("publication_failure", "failure_obstruction", "cancel_obstruction", "timeout_obstruction", "malformed_obstruction")
        if not obstructed and not source_invalid:
            companion = read(directory / "analysis.json")
            assert companion["status"] == ("completed" if successful else "failed")
            if mode in ("cancel", "cancel_cleanup", "timeout"):
                assert result.returncode == expected, (mode, result.returncode)
                assert companion["failure"]["exit_code"] == expected
            if mode in ("cleanup", "later_failure_cleanup", "cancel_cleanup"):
                assert companion["failure"]["secondary"]
        if source_invalid:
            # The fixture modified the immutable request, so a truthful failed
            # companion cannot republish it under its original request hash.
            assert not (directory / "analysis.json").exists()
            diagnostics = read(next((root / "workspace").glob("diagnostics-*.json")))
            assert diagnostics["primary"]["secondary"]
            if mode.startswith("alias_"):
                assert result.returncode == expected
                if mode == "alias_post_runtime":
                    assert "alias resolution differs" in execution["occurrences"][0]["error"]
                else:
                    assert "alias resolution differs" in (directory / "runtime.log").read_text()
        if mode in ("failure_obstruction", "cancel_obstruction", "timeout_obstruction", "malformed_obstruction"):
            assert result.returncode == expected
            diagnostics = read(next((root / "workspace").glob("diagnostics-*.json")))
            assert diagnostics["primary"]["exit_code"] == expected
            assert any("companion publication failed" in s and "obstructed" in s for s in diagnostics["primary"]["secondary"])
            if mode == "malformed_obstruction":
                assert (directory / "analysis.json").read_text() == "not a valid companion\n"
            else:
                assert (directory / "analysis.json").is_dir()
        if successful:
            assert (root / "workspace/actual-state-input").read_text() == str(checkpoint / "state.json")
            runtime = read(directory / "runtime-result.json")
            assert runtime["requested_corners"] == runtime["executed_corners"] == ["typ"]
            assert runtime["synthesis"]["state"]["path"] == str((checkpoint / "state.json").relative_to(root / "workspace"))
            assert not (root / "workspace/run.json").exists()
        if mode == "pdk_aliases":
            publication=read(directory / "publication.json")
            rows=publication["external_aliases"]
            declared=read(root / "declared-pdk-paths.json")
            assert len(rows)==1062
            assert {row["path"] for row in rows} == set(read(root / "alias-paths.json"))
            assert all(row["root"]==str(root / "pdk") and row["target"]==str(root / "pdk/ihp-sg13g2/shared/cells.lib") for row in rows)
            assert set(declared).issubset({r["path"] for r in publication["sources"]})
            assert set(declared).issubset(read(checkpoint / "manifest.json")["external"])
            assert all(row["sha256"]==sha(Path(row["path"])) and row["bytes"]==Path(row["path"]).stat().st_size for row in rows)
        inspected = subprocess.run([CONSUMER, "inspect", execution_path], text=True, capture_output=True, timeout=30)
        assert (inspected.returncode == 0) == (not obstructed and not source_invalid), (mode, inspected.stdout, inspected.stderr)
        if inspected.returncode == 0:
            view = json.loads(inspected.stdout)
            assert view["status"] == ("completed" if successful else "failed")
            assert view["verdict"] == "unavailable"
            if mode == "pdk_aliases":
                assert set(read(root / "declared-pdk-paths.json")).issubset({r["path"] for r in view["sources"]})
        launches = [json.loads(line) for line in (root / "runtime-argv.jsonl").read_text().splitlines()]
        assert len(launches) == 3, (mode, launches)  # synthesis, publication validation, analysis
        probe_runs = [args for name, args in map(json.loads, (root / "preparation-events.jsonl").read_text().splitlines())
                      if args and args[0] == "run"]
        assert probe_runs and all("--cpus=6" in args for args in probe_runs)
    print("30 named runtime cases pass: no-write root rejection; valid early expectation; full 1486-declared/1062-alias PDK proof and GDS with native alias-library copies; before/after/post-runtime retarget and metadata-link rejection; no EDA")
