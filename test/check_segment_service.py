"""Actual OCaml service/transport/supervisor/runner/publisher integration.

Native APIs and engine are disposable fixtures; the transported bridge, protocol,
and all OCaml execution/publication code are real. No physical tools are run.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def verify(consumer, service, package, root, fixture, engine):
    sys.path.insert(0, str(package))
    from hardcaml_asic_flow import checkpoint as cp, backend_protocol as protocol, execution_plan as ep
    count = 0
    runtime_file = next(p for p in Path("/nix/store").iterdir() if p.is_file())
    serial = 0

    def prepared(mode="success", image_closure=False):
        nonlocal serial
        serial += 1
        case = root / f"case-{serial}"
        workspace = case / "workspace with spaces"
        project = workspace / "project"
        store = workspace / "checkpoints"
        pdk = case / "pdk"
        (project / "src").mkdir(parents=True)
        (project / "bridge").mkdir()
        (project / "candidates").mkdir()
        store.mkdir()
        (pdk / "fixture").mkdir(parents=True)
        (pdk / "fixture/tech.lef").write_text("fixture collateral")
        (project / "src/config.json").write_text('{"CLOCK_PERIOD":10}')
        (project / "manifest.json").write_text(json.dumps({"files": [], "target": {
            "technology": "fixture", "external_references": []}}))
        for name in ("librelane_bridge.py", "backend_protocol.py"):
            shutil.copyfile(package / "hardcaml_asic_flow" / name, project / "bridge" / name)
        binding = {"schema_version": 1, "execution_mode": "dockerized", "interpreter": "python3", "engine_identity": {
            "name": "docker", "executable": str(engine), "rootless": False, "server_version": "fixture"},
            "image_identity": {"binding": "local-image-id", "declared_ref": "fixture", "image_id": ep.TESTED_IMAGE,
                               "repo_digests": [], "platform": {"os": "linux", "architecture": "amd64"}},
            "interpreter_flags": ["-I", "-B"]}
        compat = {"bundle_identity": "a" * 64, "target": {"technology": "fixture"},
                  "requested_tools": {}, "binding": copy.deepcopy(binding),
                  "bridge_sha256": cp.digest(project / "bridge/librelane_bridge.py"),
                  "project": str(project), "pdk_root": str(pdk)}
        env = dict(os.environ, SERVICE_FIXTURE_MODE=mode, SERVICE_ENGINE_ROOT=str(case), AMBIENT_SECRET="do-not-persist-fixture-secret",
                   SERVICE_ENGINE_FIXTURE=str(fixture))
        env.pop("BRIDGE_FIXTURE_MODE", None)
        env.pop("BRIDGE_FIXTURE_RUNTIME_FILE", None)
        if image_closure:
            env["BRIDGE_FIXTURE_RUNTIME_FILE"] = str(runtime_file)
        config = {"workspace": str(workspace), "project": str(project), "pdk_root": str(pdk),
                  "checkpoint_store": str(store), "compatibility": compat, "binding": binding,
                  "interpreter": sys.executable, "environment": [k + "=" + v for k, v in env.items()],
                  "timeout": 2, "initial_checkpoint": None, "attempt_id": f"fixture{serial}",
                  "plan": ep.resolve_stages("synthesis").as_dict(),
                  "summary_path": str(workspace / "execution.json"),
                  "diagnostics_path": str(workspace / "diagnostics.json")}
        return config, case

    def call(config, case, callback_mode="success", production=False):
        path = case / "prepared.json"
        data = copy.deepcopy(config)
        if production:
            data.pop("interpreter")
        path.write_text(json.dumps(data))
        result = subprocess.run([str(service), str(path)] if production else
                                [str(consumer), str(path), str(fixture), callback_mode],
                                cwd=case, capture_output=True, text=True, timeout=45)
        if production and not result.stdout:
            return {"create_error": result.stderr}, result
        assert result.stdout, (callback_mode, result.stderr)
        return json.loads(result.stdout), result

    def run_case(mode="success", callback="success", image=False, success=False, code=None, mutate=None):
        nonlocal count
        config, case = prepared(mode, image)
        if mutate:
            mutate(config)
        out, process = call(config, case, callback)
        assert process.returncode == 0, process.stderr
        if "create_error" in out:
            assert not success, out
            assert not list(Path(config["project"]).glob("*-request.json")), out
        else:
            summary = out["summary"]
            assert (summary["exit_code"] == 0) == success, (mode, callback, out)
            if code is not None:
                assert summary["exit_code"] == code, out
            if success:
                assert len(summary["artifacts"]) == 1, out
                cp.validate(summary["artifacts"][0]["path"], compatibility=config["compatibility"])
            else:
                assert not summary.get("artifacts"), out
                assert not list(Path(config["checkpoint_store"]).iterdir()), out
            assert json.loads(Path(config["summary_path"]).read_text()) == summary
            references = out["diagnostics"]["transport_records"]
            for reference in references:
                launch = json.loads(Path(reference["launch"]).read_text())
                req = protocol.loads(Path(launch["request_path"]).read_text())
                assert launch["request_id"] == req["request_id"]
                assert launch["request_sha256"] == protocol.identity(req)
                assert launch["argv"][-1] == launch["request_path"]
                assert launch["host_uid"] == os.getuid() and launch["host_gid"] == os.getgid()
                assert "do-not-persist-fixture-secret" not in Path(reference["launch"]).read_text()
                terminal = Path(reference["terminal"])
                if mode.startswith("transport_record_failure"):
                    assert terminal.is_dir() and not success
                    assert "transport evidence recording failed" in str(out["diagnostics"])
                else:
                    evidence = json.loads(terminal.read_text())
                    assert evidence["request_id"] == req["request_id"]
                    assert evidence["started_at_unix"] <= evidence["ended_at_unix"]
                    if mode == "exit17":
                        assert evidence["process"]["outcome"] == "exited" and evidence["process"]["code"] == 17
                        assert not evidence["transport_succeeded"]
                    if mode == "timeout":
                        assert evidence["process"]["outcome"] == "timed_out"
                    if mode == "cancel":
                        assert evidence["process"]["outcome"] == "cancelled"
        count += 1
        return config, case, out

    good, case, out = run_case(success=True)
    published = Path(out["summary"]["artifacts"][0]["path"])
    assert "Infinity" in (published / "state.json").read_text()
    report = json.loads((published / "segment.json").read_text())
    assert report["metrics"]["backend_nonfinite"] == {"availability": "unavailable", "reason": "nonfinite_or_nonnumeric", "raw": "inf"}
    assert report["metrics"]["finite"] == {"availability": "available", "value": 3.5}
    assert out["events"] == ["started:implementation.synthesis", "published:implementation.synthesis", "finished:implementation.synthesis"], out
    requests = list(Path(good["project"]).glob("*-request.json"))
    assert len(requests) == 1
    req = protocol.loads(requests[0].read_text())
    assert re.fullmatch("synthesis-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", req["tag"])
    protocol.read_reply(req)
    assert len(out["diagnostics"]["transport_records"]) == 1
    run_case("transport_record_failure")
    _, _, failed_record = run_case("transport_record_failure_exit17", code=17)
    assert failed_record["diagnostics"]["primary"]["secondary"]
    _, _, interrupted_record = run_case("transport_record_failure_cancel", code=143)
    assert interrupted_record["summary"]["status"] == "interrupted"
    assert interrupted_record["diagnostics"]["primary"]["secondary"]
    _, _, failed_spawn = run_case(mutate=lambda c: c.update(interpreter=str(root / "missing-interpreter")))
    terminal = Path(failed_spawn["diagnostics"]["transport_records"][0]["terminal"])
    assert json.loads(terminal.read_text())["process"]["outcome"] == "failed"
    config, case, failed_launch = run_case(callback="launch_record_failure")
    assert not list(Path(config["project"]).glob("*-reply.json"))
    assert failed_launch["diagnostics"]["transport_records"] == []

    for mode in ("backend_failure", "backend_signal", "exit17", "signaled_after_reply", "missing_reply", "malformed_reply",
                 "wrong_id", "wrong_request_hash", "wrong_version", "failed_reply", "artifact_hash", "artifact_bytes",
                 "reply_extra", "mutate_artifact", "report_extra", "report_steps", "report_metric_bool",
                 "report_metric_extra", "report_nonfinite", "report_source", "report_boundary"):
        run_case(mode, code=17 if mode == "exit17" else None)
    run_case("timeout", code=124)
    run_case("sigpipe_after_reply", code=141)
    _, _, cancelled = run_case("cancel", code=143)
    assert cancelled["summary"]["status"] == "interrupted", cancelled
    for callback in ("candidate_mutation", "candidate_forged_reply", "bridge_mutation", "closure_mutation", "callback_exception", "publication_permission"):
        run_case(callback=callback)
    for resource in ("librelane_bridge.py", "backend_protocol.py"):
        run_case(mutate=lambda c: (Path(c["project"]) / "bridge" / resource).write_text("tampered"))
    run_case(mutate=lambda c: c["compatibility"].update(bridge_sha256="b" * 64))
    run_case(mutate=lambda c: c["compatibility"]["binding"]["image_identity"].update(image_id="sha256:" + "0" * 64))
    run_case(mutate=lambda c: c["compatibility"].update(project="/wrong"))
    run_case(mutate=lambda c: c["compatibility"].update(extra=True))
    run_case(mutate=lambda c: c.update(checkpoint_store=c["project"]))
    run_case(mutate=lambda c: c.update(timeout=0))
    run_case(mutate=lambda c: c["environment"].extend(["DUPLICATE=1", "DUPLICATE=2"]))
    run_case(mutate=lambda c: c.update(workspace=c["workspace"] + "/."))
    run_case(mutate=lambda c: c.update(initial_checkpoint=str(published)))
    def symlink_candidates(c):
        path = Path(c["project"]) / "candidates"
        path.rmdir()
        path.symlink_to(Path(c["checkpoint_store"]), target_is_directory=True)
    run_case(mutate=symlink_candidates)
    run_case(mutate=lambda c: c.update(plan=ep.resolve("synthesis").as_dict()))
    run_case(mutate=lambda c: c.update(plan=ep.resolve_stages("placement", resume=True).as_dict()))

    # Real selected-runtime callback executes the bridge's validation operation.
    image_config, image_case, image_out = run_case(image=True, success=True)
    validation_requests = [protocol.loads(p.read_text()) for p in Path(image_config["project"]).glob("validate-*-request.json")]
    assert len(validation_requests) == 1
    validation = validation_requests[0]
    assert re.fullmatch("validate-synthesis-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", validation["tag"])
    validation_reply = protocol.validate_reply(protocol.loads(Path(validation["reply_path"]).read_text()), validation)
    assert validation_reply["outcome"] == "validated"
    for name, evidence in validation_reply["artifacts"].items():
        actual = cp.file_record(Path(image_out["summary"]["artifacts"][0]["path"]) / name)
        assert actual == {k: evidence[k] for k in ("sha256", "bytes")}
    assert not Path(validation["candidate"]).exists()  # private staging promoted
    for mode in ("validate_mismatch", "validate_exit17", "validate_missing_reply", "validate_wrong_id", "validate_timeout", "validate_cancel", "validate_manifest_mutation", "validate_artifact_mutation"):
        run_case(mode, image=True, code={"validate_exit17": 17, "validate_timeout": 124, "validate_cancel": 143}.get(mode))

    # Full fixture-backed schema-4 seven-boundary runner, with every source coming from actual
    # preceding publication and both initial/continuation bridge checks.
    config, case = prepared()
    config["plan"] = ep.resolve_stages("signoff").as_dict()
    full, _ = call(config, case)
    assert full["summary"]["exit_code"] == 0, full
    assert [a["boundary"] for a in full["summary"]["artifacts"]] == list(ep.BOUNDARIES)
    previous = None
    for path in sorted(Path(config["project"]).glob("*-request.json"), key=lambda p: list(ep.BOUNDARIES).index(protocol.loads(p.read_text())["boundary"])):
        request = protocol.loads(path.read_text())
        assert request["checkpoint"] == previous, request
        protocol.read_reply(request)
        artifact = next(a for a in full["summary"]["artifacts"] if a["boundary"] == request["boundary"])
        cp.validate(artifact["path"], compatibility=config["compatibility"])
        previous = artifact["path"]
    count += 1

    # Historical recipes remain parseable evidence, but cannot execute on this bridge.
    for schema in (2, 3, 4):
        resumed = copy.deepcopy(good)
        resumed["initial_checkpoint"] = str(published)
        resumed["plan"] = ep.StagePlan("placement", resume=True, schema_version=schema,
            stages=() if schema == 2 else ("floorplan", "placement"),
            initial_boundary=None if schema == 2 else "synthesis").as_dict()
        result, _ = call(resumed, case=Path(good["workspace"]).parent)
        if schema == 4:
            assert result["summary"]["exit_code"] == 0, result
            assert [a["boundary"] for a in result["summary"]["artifacts"]] == ["floorplan", "placement"]
        else:
            assert result["summary"]["exit_code"] != 0 and not result["events"], result
            # Runner omits the artifacts key when validation fails before any
            # publication. Also require that no backend call was launched.
            assert not result["summary"].get("artifacts"), result
            assert not result["diagnostics"]["transport_records"], result
        count += 1
    resumed = copy.deepcopy(good)
    resumed["initial_checkpoint"] = str(published)
    resumed["plan"] = ep.resolve_sequence(["cts"], initial_boundary="placement").as_dict()
    bad_source, _ = call(resumed, Path(good["workspace"]).parent)
    assert bad_source["summary"]["exit_code"] != 0 and not bad_source["events"]
    count += 1
    resumed["plan"] = ep.resolve_stages("floorplan", resume=True).as_dict()
    bad_initial, _ = call(resumed, Path(good["workspace"]).parent, "initial_mutation")
    assert bad_initial["summary"]["exit_code"] != 0 and not bad_initial["events"]
    count += 1

    # A promoted artifact survives downstream callback/hook failures.
    config, case = prepared()
    hooked, _ = call(config, case, "published_hook_failure")
    assert hooked["summary"]["exit_code"] != 0 and len(hooked["summary"]["artifacts"]) == 1
    cp.validate(hooked["summary"]["artifacts"][0]["path"], compatibility=config["compatibility"])
    count += 1

    # Installed public executable exercises production Docker command construction
    # and actual supervisor/cleanup against a fixture engine on prepared paths.
    config, case = prepared(image_closure=True)
    summary, process = call(config, case, production=True)
    assert process.returncode == 0 and summary["exit_code"] == 0, (process.stderr, summary)
    cp.validate(summary["artifacts"][0]["path"], compatibility=config["compatibility"])
    events = [json.loads(line) for line in (case / "engine-events.jsonl").read_text().splitlines()]
    launches = [e for e in events if e[0] == "run"]
    assert len(launches) == 2 and all(ep.TESTED_IMAGE in launch for launch in launches)
    names = {launch[launch.index("--name") + 1] for launch in launches}
    assert len(names) == 2
    assert {e[2] for e in events if e[:2] == ["rm", "-f"]} == names
    assert json.loads(Path(config["summary_path"]).read_text()) == summary
    assert json.loads(Path(config["diagnostics_path"]).read_text())["primary"] is None
    records = json.loads(Path(config["diagnostics_path"]).read_text())["transport_records"]
    assert len(records) == 2
    for paths in records:
        launch = json.loads(Path(paths["launch"]).read_text())
        terminal = json.loads(Path(paths["terminal"]).read_text())
        assert launch["argv"][1:] in launches
        assert launch["container_name"] in names
        assert launch["binding"]["engine_identity"] == config["binding"]["engine_identity"]
        assert launch["resources"]["cpus"] == 6
        assert terminal["transport_succeeded"] and terminal["cleanup"]["error"] is None
        assert len(terminal["cleanup"]["attempts"]) == 1
    retained = Path(config["summary_path"]).read_bytes()
    _, retry = call(config, case, production=True)
    assert retry.returncode == 2 and Path(config["summary_path"]).read_bytes() == retained
    count += 1
    config, case = prepared(image_closure=True)
    config['environment'].append('HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=6')
    out, process = call(config, case, production=True)
    assert process.returncode == 0, (out, process.stderr)
    records = json.loads(Path(config['diagnostics_path']).read_text())['transport_records']
    assert len(records) == 2, (out, records)  # execution AND publication validation
    for paths in records:
        launch = json.loads(Path(paths['launch']).read_text())
        assert '--pids-limit=6' in launch['argv'] and '--cpus=6' in launch['argv']
        assert launch['execution_envelope']['total_container_tasks'] == 6
    count += 1
    for native, mismatch, cleanup, nonzero in ((True, False, False, False), (False, True, False, False),
                                               (False, False, True, True), (False, False, True, False)):
        config, case = prepared("exit17" if nonzero else "success")
        if native:
            config["binding"] = {"schema_version": 1, "execution_mode": "native", "interpreter": sys.executable,
                                 "interpreter_flags": ["-I", "-B"]}
        if mismatch:
            config["binding"]["engine_identity"]["server_version"] = "changed"
        if cleanup:
            config["environment"].append("SERVICE_ENGINE_CLEANUP_FAIL=1")
        output, process = call(config, case, production=True)
        assert process.returncode != 0 and not list(Path(config["checkpoint_store"]).iterdir()), output
        if cleanup:
            assert process.returncode == (17 if nonzero else 2), (output, process.stderr)
            diagnostics = json.loads(Path(config["diagnostics_path"]).read_text())
            assert diagnostics["primary"]["secondary"], diagnostics
            record = diagnostics["transport_records"][0]
            evidence = json.loads(Path(record["terminal"]).read_text())
            assert not evidence["transport_succeeded"] and evidence["cleanup"]["error"]
            assert evidence["process"]["code"] == (17 if nonzero else 0)
        count += 1
    print(f"OCaml segment service: {count} fixture-backed execution/publication checks passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", type=Path)
    parser.add_argument("--service", type=Path)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ocaml-segment-service-", dir="/tmp/opencode") as tmp:
        root = Path(tmp)
        if not args.installed:
            engine = root / "engine"
            shutil.copyfile(Path(__file__).with_name("segment_service_engine.py"), engine)
            engine.chmod(0o700)
            verify(args.consumer.resolve(), args.service.resolve(), repo / "flow", root,
                   Path(__file__).with_name("segment_service_fixture.py"), engine)
            return
        prefix = root / "prefix"
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer = root / "consumer"
        consumer.mkdir()
        shutil.copyfile(repo / "test/segment_service_consumer.ml", consumer / "segment_service_consumer.ml")
        (consumer / "dune-project").write_text("(lang dune 3.17)\n(name installed_segment_service_consumer)\n")
        (consumer / "dune").write_text("(executable (name segment_service_consumer) (libraries hardcaml_asic.execution unix))\n")
        env = dict(os.environ)
        env["OCAMLPATH"] = str(prefix / "lib") + (":" + env["OCAMLPATH"] if env.get("OCAMLPATH") else "")
        subprocess.run(["dune", "build", "-j20", "segment_service_consumer.exe"], cwd=consumer, env=env, check=True)
        for name in ("segment_service_fixture.py", "bridge_protocol_fixture.py", "segment_service_engine.py"):
            shutil.copyfile(repo / "test" / name, root / name)
        engine = root / "segment_service_engine.py"
        engine.chmod(0o700)
        auditing = True
        def audit(event, values):
            if auditing and event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(values[0])).resolve()
                if path.is_relative_to(repo):
                    raise AssertionError("checkout read in installed service check: " + str(path))
        sys.addaudithook(audit)
        try:
            verify(consumer / "_build/default/segment_service_consumer.exe",
                   prefix / "bin/hardcaml-asic-experimental-segment-execute", prefix / "lib/hardcaml_asic/flow",
                   root, root / "segment_service_fixture.py", engine)
        finally:
            auditing = False
        print("fresh installed service and independent API consumer passed outside checkout")


if __name__ == "__main__":
    main()
