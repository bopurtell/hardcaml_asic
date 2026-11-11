"""Binding/argv differential fixtures and real bounded fake-engine race checks."""
import argparse
import copy
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

sys.dont_write_bytecode = True

def binding(engine="/checked/docker", rootless=False, flags=None):
    return {"schema_version": 1, "execution_mode": "dockerized", "interpreter": "python3",
            "interpreter_flags": flags or ["-I", "-B"],
            "image_identity": {"binding": "local-image-id", "declared_ref": "mutable:tag",
                               "image_id": "sha256:" + "1" * 64, "repo_digests": ["repo@sha256:" + "2" * 64],
                               "platform": {"os": "linux", "architecture": "amd64"}},
            "engine_identity": {"name": "docker", "executable": str(engine), "rootless": rootless,
                                "server_version": "29.fixture"}}

def verify(consumer, fixture, root, transport):
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    name = "hardcaml-asic-execution-" + "a" * 32
    env = ["PRESERVED=a=b; $(no shell)", "PATH=/unusable", "PYTHONPATH=evil", "PYTHONHOME=evil",
           "TCL_LIBRARY=evil", "TK_LIBRARY=evil", "TCLLIBPATH=evil", "GCONV_PATH=evil",
           "GLIBC_TUNABLES=evil", "LD_PRELOAD=evil", "LD_CUSTOM=evil", "DYLD_CUSTOM=evil",
           "LIBRELANE_IMAGE_OVERRIDE=evil", "LIBRELANE_CONTAINER_ENGINE=evil",
           "OPENLANE_CONTAINER_ENGINE=evil", "_MOUNT_HOST_LIBRELANE=evil"]
    base = {"op": "command", "binding": binding(), "name": name, "project": "/scratch/project space",
            "pdk_root": "/scratch/pdk space", "workdir": "/scratch/project space/sub",
            "entrypoint": "python3", "pdk": "fixture", "environment": env,
            "interpreter": True, "cwd": str(root), "arguments": ["-c", "a b", "", "$(touch forbidden)", "λ"],
            "timeout": 0.12, "cleanup_timeout": 0.45}

    def call(request, interrupt=None, ready=None):
        p = subprocess.Popen([str(consumer)], cwd=root, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            p.stdin.write((json.dumps(request) + "\n").encode())
            p.stdin.close()
            p.stdin = None
            if interrupt is not None:
                deadline = time.monotonic() + 4
                while not ready.exists():
                    assert p.poll() is None
                    assert time.monotonic() < deadline
                    time.sleep(0.005)
                os.kill(p.pid, interrupt)
            out, err = p.communicate(timeout=6)
            assert p.returncode == 0, (out, err)
            return json.loads(out)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()

    accepted = 0
    # Opt-in total-task cap is independent from the preserved default quota/env.
    for cap in (1, 6, 20):
        result = call(dict(base, pids_limit=cap))
        assert result['argv'].count('--pids-limit=' + str(cap)) == 1
        assert result['argv'].index('--pids-limit=' + str(cap)) < result['argv'].index(base['binding']['image_identity']['image_id'])
        assert '--cpus=20' in result['argv']
    for cap in (0, -1, 21, True, '6'):
        assert 'error' in call(dict(base, pids_limit=cap))
    for cap in (None, 6):
        argv = call(dict(base, op='metadata-command', pids_limit=cap))['argv']
        assert ('--pids-limit=6' in argv) == (cap == 6)
        assert '--cpus=6' in argv and '--read-only' in argv and '-v' not in argv
        assert argv[argv.index('--network') + 1] == 'none'
    synthesis = dict(schema_version=4, stages=['synthesis'], initial_boundary=None)
    profile = 'HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=6'
    assert call(dict(op='envelope', environment=[profile], plan=synthesis)) == dict(requested=True, limit=6)
    physical = dict(schema_version=4, stages=['synthesis', 'floorplan'], initial_boundary=None)
    assert call(dict(op='envelope', environment=[], plan=physical)) == dict(requested=False, limit=None)
    for envelope_env, plan in (([profile], physical), ([profile, profile], synthesis),
                      (['HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=20'], synthesis),
                      ([profile], dict(schema_version=4, stages=['floorplan'], initial_boundary='synthesis'))):
        assert 'error' in call(dict(op='envelope', environment=envelope_env, plan=plan))
    def bounded(argv):
        argv = list(argv)
        argv.insert(argv.index("--name") + 2, "--cpus=20")
        index = argv.index("--entrypoint")
        argv[index:index] = ["-e", "_OPENLANE_MAX_CORES=20", "-e", "OPENROAD_THREADS=20", "-e", "OMP_NUM_THREADS=20"]
        return argv
    for rootless in (False, True):
        for flags in (["-I", "-B"], ["-B"]):
            for entrypoint, interpreter in (("python3", True), ("iverilog", False)):
                for extras in (False, True):
                    request = copy.deepcopy(base)
                    request["binding"] = binding(rootless=rootless, flags=flags)
                    request["entrypoint"], request["interpreter"] = entrypoint, interpreter
                    if extras:
                        request["binding"]["engine_identity"].update(daemon_id="daemon", name_on_server=None)
                        request["binding"]["image_identity"]["platform"]["variant"] = "v8"
                    result = call(request)
                    transport.validate(request["binding"])
                    expected = transport.docker_command(request["binding"], request["project"], request["pdk_root"],
                                name=name, workdir=request["workdir"], entrypoint=entrypoint, pdk=request["pdk"])
                    expected = bounded(expected) + (flags if interpreter else []) + request["arguments"]
                    assert result["argv"] == expected, result
                    assert result["binding"] == request["binding"]
                    with mock.patch.dict(
                            os.environ, dict(e.split("=", 1) for e in env), clear=True):
                        expected_env = transport.environment()
                    assert dict(e.split("=", 1) for e in result["environment"]) == expected_env
                    assert "mutable:tag" not in result["argv"]
                    accepted += 1
                    request["workdir"] = request["pdk"] = None
                    result = call(request)
                    expected = transport.docker_command(request["binding"], request["project"], request["pdk_root"],
                                                        name=name, entrypoint=entrypoint)
                    expected = bounded(expected) + (flags if interpreter else []) + request["arguments"]
                    assert result["argv"] == expected
                    accepted += 1
    native = {"schema_version": 1, "execution_mode": "native", "interpreter": "/lexical/venv/bin/python",
              "interpreter_flags": ["-I", "-B"]}
    request = dict(base, binding=native)
    result = call(request)
    assert result["argv"] == [native["interpreter"], "-I", "-B"] + base["arguments"]
    assert result["binding"] == native
    accepted += 1

    # Mutations rejected by both validators, plus stricter public closed-schema
    # and NUL/Unicode checks deliberately beyond Python validate's loose objects.
    rejected = 0
    for section in (None, "image_identity", "engine_identity"):
        source = binding()
        target = source if section is None else source[section]
        for key in target:
            for value in (None, True, 1.0, [], {}, ""):
                bad = copy.deepcopy(source)
                (bad if section is None else bad[section])[key] = value
                try:
                    transport.validate(bad)
                except (ValueError, TypeError, AttributeError):
                    assert "error" in call(dict(base, op="binding", binding=bad)), bad
                    rejected += 1
    for path in ((), ("engine_identity",), ("image_identity",), ("image_identity", "platform")):
        bad = binding()
        target = bad
        for key in path:
            target = target[key]
        target["unknown"] = "extra"
        assert "error" in call(dict(base, op="binding", binding=bad))
        rejected += 1
    for bad in (dict(native, interpreter="relative"), dict(native, interpreter_flags=["-B"]),
                dict(native, schema_version=True), dict(native, interpreter="/nul\0path")):
        assert "error" in call(dict(base, op="binding", binding=bad))
        rejected += 1
    for changes in ({"name": "other"}, {"project": "relative"}, {"pdk_root": "/pdk:redirect"},
                    {"workdir": "relative"}):
        assert "error" in call(dict(base, **changes))
        rejected += 1
    for raw in ('{"schema_version":1,"schema_version":1}', 'NaN', '{} trailing',
                '{"interpreter":"\\ud800"}', '{"schema_version":1.0}'):
        assert "error" in call(dict(base, op="parse", raw=raw))
        rejected += 1
    result = call(dict(base, op="parse", raw=json.dumps(binding())))
    assert result["binding"] == binding()
    accepted += 1

    serial = 0
    def real(mode, interrupt=None, missing=False, collision=False, missing_cwd=False, op="run"):
        nonlocal serial
        serial += 1
        cwd = root / f"real {serial}"
        cwd.mkdir()
        engine = cwd / "selected engine"
        shutil.copyfile(fixture, engine)
        engine.chmod(0o700)
        if collision:
            (cwd / "stdout.log").write_text("retained evidence")
        request = dict(base, op=op, binding=binding(engine if not missing else cwd / "missing"),
                       cwd=str(cwd), project=str(cwd), workdir=str(cwd), pdk_root=str(cwd / "pdk"),
                       timeout=0.12 if mode == "race" else 2,
                       environment=env + ["FIXTURE_ROOT=" + str(cwd), "FIXTURE_MODE=" + mode])
        if missing_cwd:
            request["cwd"] = str(cwd / "missing cwd")
        if mode in ("normal", "absent") and not (missing or collision or missing_cwd) and op == "run":
            request["cleanup_timeout"] = 5
        started = time.monotonic()
        try:
            result = call(request, interrupt, cwd / "ready")
            assert time.monotonic() - started < 3
            assert result["reuse_rejected"]
            assert result["handlers_restored"]
            attempts = result["cleanup"]["attempts"]
            assert attempts
            assert len(attempts) < 15
            events = [json.loads(line) for line in (cwd / "events").read_text().splitlines()] if (cwd / "events").exists() else []
            own = result["argv"][result["argv"].index("--name") + 1]
            assert own != name and own.startswith("hardcaml-asic-execution-")
            assert all(e["name"] == own for e in events)
            assert all(e["argv"] == ["rm", "-f", own] for e in events if e["kind"] == "rm")
            if mode in ("race", "race-nonzero") and not collision:
                kinds = [e["kind"] for e in events]
                assert kinds.index("absent") < kinds.index("created") < kinds.index("removed"), kinds
                assert result["outcome"] == ("timeout" if mode == "race" else "exit:17")
                assert result["cleanup"]["error"] is None and not result["succeeded"]
            if missing:
                assert result["outcome"].startswith("failed:") and result["cleanup"]["error"]
            elif collision or missing_cwd:
                assert result["outcome"].startswith("failed:")
                if collision:
                    assert (cwd / "stdout.log").read_text() == "retained evidence"
                assert result["cleanup"]["error"] is None, result
            elif interrupt or mode.startswith("cleanup-sig") or op == "cancel":
                assert result["outcome"].startswith("cancel:") and not result["succeeded"]
                assert result["cleanup"]["error"] is None
            elif op == "cancel-failure":
                assert result["outcome"].startswith("failed:") and not result["succeeded"]
                assert result["cleanup"]["error"] is None
            elif mode.startswith("rmfail") or mode == "rmhang":
                assert result["outcome"] == ("exit:17" if mode == "rmfail-primary" else "exit:0")
                assert result["cleanup"]["error"] and not result["succeeded"]
                if mode == "rmhang":
                    assert attempts[-1]["outcome"] == "timeout"
            elif mode == "nonzero":
                assert result["outcome"] == "exit:17" and not result["succeeded"]
            elif mode == "signaled":
                assert result["outcome"].startswith("signal:") and not result["succeeded"]
            elif mode not in ("race", "race-nonzero"):
                assert result["succeeded"] and result["cleanup"]["error"] is None, (mode, result)
            if mode in ("normal", "absent") and not (missing or collision or missing_cwd) and op == "run":
                assert len(attempts) == 1, result
                assert time.monotonic() - started < 1.5, "completed cleanup exhausted retry window"
            if mode == "transient":
                assert len(attempts) == 2, result
            if events:
                observed = events[0]
                assert observed["cwd"] == (str(cwd) if observed["kind"] == "run" else "/")
                assert observed["environment"]["PRESERVED"] == "a=b; $(no shell)"
                assert "PYTHONPATH" not in observed["environment"]
                if observed["kind"] == "run":
                    assert observed["argv"] == result["argv"][1:]
            return result
        finally:
            # Failure containment for fake daemon resources, including deliberate
            # cleanup failures. Only this disposable test root is enumerated.
            for path in cwd.glob("hardcaml-asic-execution-*"):
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    time.sleep(0.01)
            else:
                raise AssertionError("fixture daemon survived cleanup")
    for mode in ("normal", "absent", "race", "race-nonzero", "nonzero", "signaled", "transient", "rmfail", "rmfail-primary", "rmhang",
                 "cleanup-sigint", "cleanup-sigterm"):
        real(mode)
    real("normal", missing=True)
    real("normal", collision=True)
    real("normal", missing_cwd=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        real("interrupt", interrupt=sig)
    real("normal", op="cancel")
    real("normal", op="cancel-failure")
    # Lexical venv path must not become its realpath, or venv package selection
    # changes. Run a genuine isolated Python command through native transport.
    venv = root / "lexical venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    cwd = root / "native"
    cwd.mkdir()
    native["interpreter"] = str(venv / "bin/python")
    request = dict(base, op="run", binding=native, cwd=str(cwd), timeout=2,
                   arguments=["-c", "import sys,json,os;print(json.dumps([sys.prefix,sys.argv[1:],dict(os.environ)]))",
                              "a b", "", "$(touch forbidden)", "λ"])
    result = call(request)
    assert result["succeeded"] and result["cleanup"] is None
    prefix, argv, actual_env = json.loads((cwd / "stdout.log").read_text())
    assert prefix == str(venv) and argv == request["arguments"][2:]
    assert "PYTHONPATH" not in actual_env and not (cwd / "forbidden").exists()
    print(f"OCaml transport: {accepted} differential binding/command cases, {rejected} negatives; {serial} real engine cases and lexical venv passed")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", type=Path)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="execution-transport-", dir="/tmp/opencode") as tmp:
        root = Path(tmp)
        fixture = Path(__file__).with_name("fake_docker_engine.py").resolve()
        if not args.installed:
            sys.path.insert(0, str(repo / "flow"))
            from hardcaml_asic_flow import execution_transport
            verify(args.consumer.resolve(), fixture, root, execution_transport)
            return
        prefix = root / "prefix"
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer = root / "consumer"
        consumer.mkdir()
        shutil.copyfile(repo / "test/execution_transport_consumer.ml", consumer / "execution_transport_consumer.ml")
        (consumer / "dune-project").write_text("(lang dune 3.17)\n(name installed_transport_consumer)\n")
        (consumer / "dune").write_text("(executable (name execution_transport_consumer) (libraries hardcaml_asic.execution unix))\n")
        env = dict(os.environ)
        env["OCAMLPATH"] = str(prefix / "lib") + (":" + env["OCAMLPATH"] if env.get("OCAMLPATH") else "")
        subprocess.run(["dune", "build", "-j20", "execution_transport_consumer.exe"], cwd=consumer, env=env, check=True)
        copied_fixture = root / "fake_docker_engine.py"
        shutil.copyfile(fixture, copied_fixture)
        sys.path.insert(0, str(prefix / "lib/hardcaml_asic/flow"))
        auditing = True
        def audit(event, values):
            if auditing and event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(values[0])).resolve()
                if path.is_relative_to(repo):
                    raise AssertionError("checkout read in installed transport test: " + str(path))
        sys.addaudithook(audit)
        try:
            from hardcaml_asic_flow import execution_transport
            verify(consumer / "_build/default/execution_transport_consumer.exe", copied_fixture, root, execution_transport)
        finally:
            auditing = False
        print("fresh installed transport consumer passed outside checkout with checkout opens denied")

if __name__ == "__main__":
    main()
