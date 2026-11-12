"""Named-flow v1 production CLI/bridge fixture, no provider Python imports or EDA.

Build @install @all first. Only disposable fixture repositories are modified.
"""
import copy
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from reference_harness import hardlink_prefix, thin_prefix

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='store_true', help='exercise built native helpers directly; no installed tree')
    parser.add_argument('--publication-races-only', action='store_true')
    cli_args = parser.parse_args()
    source_mode = cli_args.source
    with tempfile.TemporaryDirectory(dir="/tmp/opencode", prefix="namedflow-") as tmp:
        root = Path(tmp)
        prefix = root / "installed"
        if not source_mode:
            hardlink_prefix(REPO / "_build/install/default", prefix)
            thin_prefix(prefix)
        cli = prefix / "bin/hardcaml-asic-flow"
        exporter = root / "exporter"
        shutil.copyfile(REPO / "_build/default/test/plan_contract/consumer.exe", exporter)
        exporter.chmod(0o700)
        prepared_consumer = root / "prepared-consumer"
        shutil.copyfile(REPO / "_build/default/test/stage_preparation_consumer.exe", prepared_consumer)
        prepared_consumer.chmod(0o700)
        fixtures = root / "fixtures"
        fixtures.mkdir()
        for name in ("stage_preparation_engine.py", "segment_service_engine.py",
                     "segment_service_fixture.py", "bridge_protocol_fixture.py"):
            shutil.copyfile(REPO / "test" / name, fixtures / name)
        source = root / "source"
        source.mkdir()
        for name in ("design.ml", "catalog.ml"):
            (source / name).write_text("(* frozen fixture *)\n")
        git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                       GIT_AUTHOR_EMAIL="fixture@test.invalid", GIT_COMMITTER_EMAIL="fixture@test.invalid",
                       GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="commit.gpgSign", GIT_CONFIG_VALUE_0="false",
                       GIT_CONFIG_KEY_1="core.hooksPath", GIT_CONFIG_VALUE_1=os.devnull)
        def native_argv(argv):
            argv = list(map(str, argv))
            if source_mode:
                # Keep actual native execution, plan, report and observation readers;
                # only replace installed public dispatch with explicit source helpers.
                offset = argv.index(str(cli)) if str(cli) in argv else None
                if offset is not None:
                    command, *rest = argv[offset + 1:]
                    helper = {'finalize': 'finalization', 'observation': 'observation_read'}.get(command, command)
                    if command == 'stages':
                        bundle, *rest = rest
                        # Public dispatch resolves path arguments before delegation.
                        # Preserve kernel symlink/.. traversal in this source adapter too.
                        workspace_index = rest.index('--workspace') + 1
                        workspace = Path(rest[workspace_index]).resolve()
                        rest[workspace_index] = str(workspace)
                        rest = ['--bundle', bundle, '--resources', str(REPO / 'flow/hardcaml_asic_flow'),
                                '--evidence-root', str(workspace.parent / 'stage-preflight'), *rest]
                    elif command == 'report':
                        target, *rest = rest
                        rest = ['--target', target, *rest]
                    elif command == 'finalize':
                        target, *rest = rest
                        rest = ['--execution', target, *['--store' if x == '--operation-store' else x for x in rest]]
                    argv = [*argv[:offset], str(REPO / f'_build/default/bin/experimental_{helper}.exe'), *rest]
                elif argv[0] == str(prefix / 'bin/hardcaml-asic-experimental-plan-inspect'):
                    argv[0] = str(REPO / '_build/default/bin/experimental_plan_inspect.exe')
            return argv
        def run(argv, env=None, **kw):
            return subprocess.run(native_argv(argv), cwd=root, env=env, text=True,
                                  capture_output=True, timeout=60, **kw)
        for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(source), *args], env=git_env, check=True)
        exports = {}
        for profile in ("area", "delay"):
            p = run([exporter, source, "native-fixture", profile, root / "bundles", "signoff"], env=git_env)
            assert p.returncode == 0, p.stderr
            exports[profile] = json.loads(p.stdout)
        assert exports["area"]["plan"]["bundle_identity"] != exports["delay"]["plan"]["bundle_identity"]
        race_root = root / 'publication-races'
        race_root.mkdir()
        race_plan = root / 'race-plan.json'
        race_plan.write_text(json.dumps(exports['area']['plan']))
        race_exe = REPO / '_build/default/test/named_observation_race.exe'
        if not source_mode:
            # Independently link the race fixture against the public installed API.
            consumer = root / 'race-consumer'
            consumer.mkdir()
            shutil.copyfile(REPO / 'test/named_observation_race.ml', consumer / 'named_observation_race.ml')
            (consumer / 'dune-project').write_text('(lang dune 3.17)\n')
            (consumer / 'dune').write_text('(executable (name named_observation_race) (libraries hardcaml_asic.execution unix))\n')
            subprocess.run(['dune', 'build', '-j20', 'named_observation_race.exe'], cwd=consumer,
                           env=dict(os.environ, OCAMLPATH=str(prefix / 'lib')), check=True)
            race_exe = consumer / '_build/default/named_observation_race.exe'
        race = run([race_exe,
                    race_plan, exports['area']['directory'], race_root])
        assert race.returncode == 0, (race.stdout, race.stderr)
        print(race.stdout.strip(), flush=True)
        if cli_args.publication_races_only:
            return
        top = json.loads(exports["area"]["plan"]["bundle_manifest"])["top_module"]
        inventory_mutations = [
            "bad-source", "bad-copy", "duplicate-source", "missing-source", "duplicate-file",
            "bad-hash", "unsafe-file", "file-descendant",
            "declared:synthesis_sources", "declared:simulation_sources",
            "config:VERILOG_FILES", "config:PNR_SDC_FILE", "config:SIGNOFF_SDC_FILE",
            "extra:SYNTH_EXTRA_MAPPING_FILE", "extra:VERILOG_INCLUDE_DIRS", "extra:PDN_CFG",
            "extra:FP_PDN_CFG", "extra:meta", "extra:SYNTH_STRATEGY",
            "expression:dir::*.v", "expression:ref::$DESIGN_DIR/unchecked.v",
            "expression:exec::unchecked", "expression:expr::1+1",
            "omit:src/config.json", f"omit:src/{top}.v", "omit:constraints/top.sdc", "omit:inputs/design.ml",
        ]
        for mutation in inventory_mutations:
            p = run([exporter, source, "native-fixture", "area", root / "bundles", "signoff", mutation], env=git_env)
            assert p.returncode == 0, (mutation, p.stderr)
            exports[mutation] = json.loads(p.stdout)
        discovery = run([prefix / "bin/hardcaml-asic-experimental-plan-inspect"],
                        input=json.dumps({"schema_version": 4, "stages": ["synthesis", "floorplan", "placement", "cts", "routing", "timing", "signoff"], "initial_boundary": None}))
        assert discovery.returncode == 0, discovery.stderr
        discovery = json.loads(discovery.stdout)
        image = discovery["operation_sequence"][0]["backend"]["tested_image_id"] if "tested_image_id" in discovery["operation_sequence"][0]["backend"] else None
        # Same checked native capability used by every fixture runtime.
        if image is None:
            def find_image(j):
                if isinstance(j, dict):
                    if "tested_image_id" in j:
                        return j["tested_image_id"]
                    for v in j.values():
                        found = find_image(v)
                        if found:
                            return found
                elif isinstance(j, list):
                    for v in j:
                        found = find_image(v)
                        if found:
                            return found
            image = find_image(discovery)
        assert image
        serial = 0
        def case(profile="area", mode="success"):
            nonlocal serial
            serial += 1
            folder = root / f"case-{serial}"
            folder.mkdir()
            data = copy.deepcopy(exports[profile])
            bundle = folder / "bundle"
            shutil.copytree(data["directory"], bundle)
            planfile = folder / "plan.json"
            planfile.write_text(json.dumps(data["plan"]))
            manifest = json.loads((bundle / "manifest.json").read_text())
            for ref in manifest["target"]["external_references"]:
                base = folder / "tt" if ref["source"] == "Support_tools" else folder / "pdk" / manifest["target"]["technology"]
                path = base / ref["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("floorplan fixture" if ref["source"] == "Support_tools" else "technology fixture")
            (folder / "classic-inventory.json").write_text(json.dumps([o["backend"] for o in discovery["operation_sequence"]]))
            (folder / "classic-settings.json").write_text(json.dumps({o["backend"]["implementation"]: o["settings"] for o in discovery["operation_sequence"] if o["settings"]}))
            tools = folder / "bin"
            tools.mkdir()
            for name in ("python", "docker", "git"):
                shutil.copyfile(fixtures / "stage_preparation_engine.py", tools / name)
                (tools / name).chmod(0o700)
            shutil.copyfile(fixtures / "segment_service_engine.py", tools / "segment_service_engine.py")
            env = dict(os.environ, PATH=str(tools), PYTHONPATH="/denied", SERVICE_ENGINE_ROOT=str(folder),
                       SERVICE_ENGINE_FIXTURE=str(fixtures / "segment_service_fixture.py"),
                       PREPARATION_IMAGE=image, PREPARATION_REVISION="a" * 40, PREPARATION_MODE=mode,
                       BRIDGE_FIXTURE_FINAL_VIEWS="1", BRIDGE_FIXTURE_BUNDLE_CONFIG="1", ASSERT_JOURNAL_BEFORE_PROBE="1")
            argv = [cli, "stages", bundle, "--methodology-plan", planfile,
                    "--expected-plan-identity", data["plan"]["plan_identity"], "--support-tools", folder / "tt",
                    "--pdk-root", folder / "pdk", "--python", tools / "python", "--workspace", folder / "workspace",
                    "--checkpoint-store", folder / "workspace/checkpoints", "--json"]
            return folder, data, planfile, argv, env
        def summary(folder):
            return next((folder / "workspace").glob("execution-*.json"))
        def report(path, good=True):
            p = run([cli, "report", path, "--json"], env=dict(os.environ, PATH="/denied", PYTHONPATH="/denied"))
            assert (p.returncode == 0) == good, (p.stdout, p.stderr)
            return p
        def inspect(attempt, good=True):
            p = run([cli, 'observation', 'inspect', attempt, '--json'],
                    env=dict(os.environ, PATH='/denied', PYTHONPATH='/denied'))
            assert p.returncode == (0 if good else 2), (p.stdout, p.stderr)
            return json.loads(p.stdout)
        # Public prepare -> mutate -> execute seam. There is no backend launch,
        # constructed-flow marker or checkpoint when frozen staged bytes differ.
        for hook, relative in [('after_prepare', p) for p in
                               ('src/config.json', f'src/{top}.v', 'constraints/top.sdc', 'inputs/design.ml')] + [
                                   ('after_invoke', 'src/config.json'), ('after_invoke', f'src/{top}.v')]:
            folder, data, planfile, argv, env = case()
            env.pop('ASSERT_JOURNAL_BEFORE_PROBE')
            changed = 'changed after prepare'
            if relative == 'src/config.json':
                config = json.loads(data['plan']['configuration_bytes'])
                config['SYNTH_STRATEGY'] = 'DELAY 1'
                changed = json.dumps(config)
            request = dict(bundle=str(folder / 'bundle'), support_tools=str(folder / 'tt'),
                           pdk_root=str(folder / 'pdk'), python=str(folder / 'bin/python'),
                           docker=str(folder / 'bin/docker'), git=str(folder / 'bin/git'),
                            resources=str(REPO / 'flow/hardcaml_asic_flow' if source_mode else prefix / 'lib/hardcaml_asic/flow/hardcaml_asic_flow'),
                           workspace=str(folder / 'workspace'), checkpoint_store=str(folder / 'workspace/checkpoints'),
                           evidence_root=str(folder / 'stage-preflight'), environment=[f'{k}={v}' for k,v in env.items()],
                           checkpoint=None, plan=data['plan']['methodology']['recipe'],
                           methodology=json.dumps(data['plan']))
            request[hook] = dict(path=relative, bytes=changed)
            request_path = folder / 'request.json'
            request_path.write_text(json.dumps(request))
            p = run([prepared_consumer, request_path], env=env)
            assert p.returncode != 0, (relative, p.stdout, p.stderr)
            execution = json.loads(p.stdout)
            assert execution['status'] == 'failed' and not execution.get('artifacts', [])
            assert len(list((folder / 'workspace').glob('*-transport.json'))) == (0 if hook == 'after_prepare' else 1)
            assert (folder / 'workspace/project/src/constructed').exists() == (hook == 'after_invoke')
            assert not list((folder / 'workspace/checkpoints').iterdir())
            diagnostics = json.loads(next((folder / 'workspace').glob('diagnostics-*.json')).read_text())
            assert 'methodology' in json.dumps(diagnostics)
        for relative, boundary in (('src/config.json', 'synthesis'), (f'src/{top}.v', 'floorplan')):
            folder, data, planfile, argv, env = case()
            env.update(NAMED_INPUT_MUTATION=relative, NAMED_MUTATE_BOUNDARY=boundary)
            p = run(argv, env=env)
            assert p.returncode != 0, (p.stdout, p.stderr)
            execution = json.loads(summary(folder).read_text())
            assert execution['status'] == 'failed'
            assert len(execution.get('artifacts', [])) == (0 if boundary == 'synthesis' else 1)
            assert any('methodology' in (o['error'] or '') for o in execution['occurrences'])
        folder, data, planfile, argv, env = case()
        env['NAMED_BETWEEN_STAGES'] = f'src/{top}.v'
        p = run(argv, env=env)
        assert p.returncode != 0, (p.stdout, p.stderr)
        execution = json.loads(summary(folder).read_text())
        assert len(execution['artifacts']) == 1 and execution['status'] == 'failed'
        assert len(list((folder / 'workspace').glob('*-transport.json'))) == 1
        for key, value in (('SYNTH_STRATEGY', 'DELAY 1'), ('SYNTH_STRATEGY', 'AREA 9'),
                           ('SYNTH_STRATEGY', True), ('SYNTH_STRATEGY', None),
                           ('SYNTH_ABC_BUFFERING', True), ('SYNTH_ABC_BUFFERING', 'false'),
                           ('SYNTH_ABC_BUFFERING', 0), ('SYNTH_ABC_BUFFERING', None)):
            folder, data, planfile, argv, env = case()
            env.update(BRIDGE_FIXTURE_WRONG_PARAMETER=key, BRIDGE_FIXTURE_WRONG_VALUE=json.dumps(value))
            p = run(argv, env=env)
            assert p.returncode != 0, (key, value, p.stdout, p.stderr)
            execution = json.loads(summary(folder).read_text())
            assert execution['status'] == 'failed' and not execution.get('artifacts', [])
            assert 'methodology resolved parameter mismatch: ' + key in json.dumps(execution)
            assert (folder / 'workspace/project/src/constructed').exists()
            assert not list((folder / 'workspace/checkpoints').iterdir())
        for key in ('SYNTH_STRATEGY', 'SYNTH_ABC_BUFFERING'):
            folder, data, planfile, argv, env = case()
            env['BRIDGE_FIXTURE_MISSING_PARAMETER'] = key
            p = run(argv, env=env)
            assert p.returncode != 0, (key, p.stdout, p.stderr)
            execution = json.loads(summary(folder).read_text())
            assert not execution.get('artifacts', []) and execution['status'] == 'failed'
            assert 'methodology resolved parameter mismatch: ' + key in json.dumps(execution)
        successes = []
        for profile, strategy in (("area", "AREA 0"), ("delay", "DELAY 1")):
            folder, data, planfile, argv, env = case(profile)
            if profile == 'area':
                (folder/'nested').mkdir()
                (folder/'alias').symlink_to(folder/'nested', target_is_directory=True)
                argv[argv.index('--workspace')+1] = str(folder/'alias/../workspace')
            trace = folder / "native-trace"
            p = run(["/usr/bin/strace", "-f", "-e", "trace=file,process", "-o", trace, *argv], env=env)
            assert p.returncode == 0, (p.stdout, p.stderr)
            lines = trace.read_text().splitlines()
            if not source_mode:
                assert not any(str(REPO) + "/" in line for line in lines), "installed execution opened checkout files"
            assert not any("hardcaml_asic_flow/" + name + ".py" in line for line in lines
                           for name in ("stages", "runner", "flow_observation", "result_store", "cli", "identity")), "retired provider Python opened"
            allocation, execution = map(json.loads, p.stdout.splitlines())
            assert execution.pop('execution_file') == str(summary(folder))
            assert execution == json.loads(summary(folder).read_text())
            ref = execution["methodology"]
            assert allocation["methodology"]["state"] == "pending"
            assert allocation["methodology"]["snapshot_path"] == ref["snapshot_path"]
            assert ref["attempt_id"] == execution["attempt_id"] == allocation["stream_id"]
            assert ref["expected_plan_identity"] == ref["plan_identity"] == data["plan"]["plan_identity"]
            snapshot = Path(ref["snapshot_path"])
            assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == ref["snapshot_sha256"]
            allocated = json.loads((Path(allocation["attempt"]) / "000000000001.json").read_text())
            assert allocated["data"]["request"]["methodology_launch"]["snapshot_path"] == str(snapshot)
            config = json.loads((folder / "workspace/project/src/config.json").read_text())
            assert config["SYNTH_STRATEGY"] == strategy and config["SYNTH_ABC_BUFFERING"] is False
            for artifact in execution["artifacts"]:
                resolved = json.loads((Path(artifact["path"]) / "resolved.json").read_text())
                assert resolved["SYNTH_STRATEGY"] == strategy and resolved["SYNTH_ABC_BUFFERING"] is False
            receipt = json.loads(next((folder / "stage-preflight").glob("operations/*/operation.json")).read_text())
            assert receipt["status"] == "ready" and receipt["methodology"] == ref
            assert receipt["request"]["methodology"] == ref and Path(receipt["request"]["workspace"]).resolve() == folder / 'workspace'
            assert receipt["request"]["timeout_seconds"] == 600
            before = json.loads(report(summary(folder)).stdout)
            method = before['methodology']
            assert method['state'] == 'recorded'
            assert method['profile']['id'] == profile
            assert method['plan_identity'] == data['plan']['plan_identity'] == ref['expected_plan_identity']
            assert method['methodology_hash'] == data['plan']['methodology_hash']
            assert method['effective_parameters'] == data['plan']['methodology']['effective_parameters']
            observed = inspect(allocation['attempt'])
            assert observed['execution_file'] == str(summary(folder))
            assert observed['methodology'] == method
            assert observed['execution_request'] == receipt['request']
            preflight_path = Path(allocation['attempt']) / 'preflight.json'
            preflight_bytes = preflight_path.read_bytes()
            altered_preflight = json.loads(preflight_bytes)
            altered_preflight['methodology']['plan_identity'] = '0' * 64
            preflight_path.write_text(json.dumps(altered_preflight))
            inspect(allocation['attempt'], False)
            preflight_path.write_bytes(preflight_bytes)
            human = run([cli, 'report', summary(folder)]).stdout
            assert 'METHODOLOGY' in human and profile in human and strategy in human
            assert human.index('METHODOLOGY') < human.index('CHECKPOINT')
            verbose = run([cli, 'report', summary(folder), '--verbose']).stdout
            assert 'provenance:' in verbose and 'frozen methodology:' in verbose
            updates_before = run([cli, 'observation', 'read-updates', allocation['attempt'], '--stream-id', allocation['stream_id'], '--json']).stdout
            planfile.write_text('changed profile definition and source plan')
            (source / 'catalog.ml').write_text('(* changed profile definition *)')
            assert json.loads(report(summary(folder)).stdout) == before
            planfile.unlink()
            (source / 'catalog.ml').unlink()
            assert json.loads(report(summary(folder)).stdout) == before
            assert inspect(allocation['attempt']) == observed
            assert run([cli, 'observation', 'read-updates', allocation['attempt'], '--stream-id', allocation['stream_id'], '--json']).stdout == updates_before
            (source / 'catalog.ml').write_text('(* frozen fixture *)\n')
            execution_path = summary(folder)
            saved_execution = execution_path.read_text()
            execution_path.write_text('')
            inspect(allocation['attempt'], False)  # a completed attempt cannot downgrade to pending
            execution_path.write_text('{malformed')
            inspect(allocation['attempt'], False)
            execution_path.write_text(saved_execution)
            for key in ("attempt_id", "bundle_identity", "plan_identity", "snapshot_sha256"):
                altered = copy.deepcopy(execution)
                altered["methodology"][key] = "0" * 64
                execution_path.write_text(json.dumps(altered))
                report(execution_path, False)
                inspect(allocation['attempt'], False)
            execution_path.write_text(saved_execution)
            # Fully rehash wrong backend-resolved output and its checkpoint ref:
            # historical validation must still compare against the named plan.
            artifact = execution['artifacts'][0]
            checkpoint = Path(artifact['path'])
            resolved_path = checkpoint / 'resolved.json'
            manifest_path = checkpoint / 'manifest.json'
            saved_resolved, saved_manifest = resolved_path.read_bytes(), manifest_path.read_bytes()
            wrong = json.loads(saved_resolved)
            wrong['SYNTH_STRATEGY'] = 'DELAY 4'
            wrong_bytes = json.dumps(wrong).encode()
            resolved_path.chmod(0o600)
            manifest_path.chmod(0o600)
            resolved_path.write_bytes(wrong_bytes)
            manifest = json.loads(saved_manifest)
            manifest['files']['resolved.json'] = dict(sha256=hashlib.sha256(wrong_bytes).hexdigest(), bytes=len(wrong_bytes))
            del manifest['identity']
            identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            manifest['identity'] = identity
            manifest_path.write_text(json.dumps(manifest))
            altered = copy.deepcopy(execution)
            altered['artifacts'][0]['identity'] = identity
            execution_path.write_text(json.dumps(altered))
            rejected = report(execution_path, False)
            assert 'methodology resolved parameter mismatch' in rejected.stderr, rejected.stderr
            # Journal keeps its original publication identity and must reject too.
            inspect(allocation['attempt'], False)
            resolved_path.write_bytes(saved_resolved)
            manifest_path.write_bytes(saved_manifest)
            execution_path.write_text(saved_execution)
            saved = snapshot.read_bytes()
            snapshot.chmod(0o600)
            snapshot.write_bytes(saved + b" ")
            report(summary(folder), False)
            inspect(allocation['attempt'], False)
            snapshot.write_bytes(saved)
            snapshot.unlink()
            report(summary(folder), False)
            inspect(allocation['attempt'], False)
            snapshot.write_bytes(saved)
            successes.append((folder, execution))
        # Full-flow finalization's selection accepts and validates the additive ref.
        folder, execution = successes[0]
        p = run([cli, "finalize", summary(folder), "--operation-store", folder / "finalizations", "--json"],
                env=dict(os.environ, PATH=str(folder / "bin"), PYTHONPATH="/denied",
                         SERVICE_ENGINE_ROOT=str(folder), SERVICE_ENGINE_FIXTURE=str(fixtures / "segment_service_fixture.py"),
                         PREPARATION_IMAGE=image, PREPARATION_REVISION="a" * 40, BRIDGE_FIXTURE_BUNDLE_CONFIG="1"))
        assert p.returncode == 0, (p.stdout, p.stderr)
        # Required snapshot loss also blocks finalization before its first probe.
        snapshot = Path(execution["methodology"]["snapshot_path"])
        saved = snapshot.read_bytes()
        snapshot.unlink()
        events_before = (folder / "preparation-events.jsonl").read_bytes()
        p = run([cli, "finalize", summary(folder), "--operation-store", folder / "missing-sidecar-finalization", "--json"])
        assert p.returncode != 0, p.stdout
        assert (folder / "preparation-events.jsonl").read_bytes() == events_before
        snapshot.write_bytes(saved)
        # Observation opt-out still publishes the required snapshot and reference.
        folder, data, planfile, argv, env = case()
        env.pop("ASSERT_JOURNAL_BEFORE_PROBE")
        p = run([*argv, "--no-observation"], env=env)
        assert p.returncode == 0, (p.stdout, p.stderr)
        execution = json.loads(p.stdout)
        ref = execution["methodology"]
        assert Path(ref["snapshot_path"]).parent.parent.name == "operations"
        receipt = json.loads(Path(ref["snapshot_path"]).with_name("operation.json").read_text())
        assert receipt["request"]["methodology"] == ref
        assert not (folder / "observations").exists()
        planfile.write_text("historical source plan was edited")
        report(summary(folder))
        def reject(mutate):
            folder, data, planfile, argv, env = case()
            mutate(folder, data, planfile, argv)
            p = run(argv, env=env)
            assert p.returncode == 2, (p.stdout, p.stderr)
            assert not (folder / "preparation-events.jsonl").exists(), p.stderr
            assert not (folder / "workspace").exists()
        reject(lambda f, d, p, a: a.__setitem__(a.index("--expected-plan-identity") + 1, "0" * 64))
        reject(lambda f, d, p, a: (f / "bundle/src/config.json").write_text("{}"))
        reject(lambda f, d, p, a: (f / "bundle/inputs/design.ml").write_text("changed"))
        for path in (f"src/{top}.v", "constraints/top.sdc"):
            reject(lambda f, d, p, a, path=path: (f / "bundle" / path).write_text("changed executable input"))
        def external_symlink(f, d, p, a):
            path = f / "bundle" / "src" / f"{top}.v"
            outside = f / "outside.v"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        reject(external_symlink)
        reject(lambda f, d, p, a: shutil.copyfile(Path(exports["delay"]["directory"]) / "manifest.json", f / "bundle/manifest.json"))
        for extra in (("--checkpoint", "missing"), ("--endpoint", "synthesis"), ("--plan", "missing")):
            reject(lambda f, d, p, a, extra=extra: a.extend(extra))
        reject(lambda f, d, p, a: a.__delitem__(slice(a.index("--expected-plan-identity"), a.index("--expected-plan-identity") + 2)))
        for mutation in inventory_mutations:
            folder, data, planfile, argv, env = case(mutation)
            (folder / "bundle/manifest.json").write_text(data["plan"]["bundle_manifest"])
            # Omitted executable bytes remain present and can be changed; the
            # attack also supplies its recomputed expected plan hash.
            if mutation.startswith("omit:"):
                (folder / "bundle" / mutation.removeprefix("omit:")).write_text("changed omitted bytes")
            p = run(argv, env=env)
            assert p.returncode == 2, (mutation, p.stdout, p.stderr)
            assert not (folder / "preparation-events.jsonl").exists(), mutation
            assert not (folder / "workspace").exists(), mutation
        for field in ("stages", "key", "backend", "sourcehash"):
            def corrupt(f, d, p, a, field=field):
                plan = d["plan"]
                if field == "stages":
                    plan["methodology"]["recipe"]["stages"] = ["routing"]
                elif field == "key":
                    plan["methodology"]["project_assignments"][0]["assignment"]["key"] = "EXECUTABLE"
                elif field == "backend":
                    plan["methodology"]["translation"]["backend_recipe"] = "arbitrary"
                else:
                    manifest = json.loads(plan["bundle_manifest"])
                    manifest["source_inputs"][0]["sha256"] = "0" * 64
                    plan["bundle_manifest"] = json.dumps(manifest, separators=(",", ":"))
                p.write_text(json.dumps(plan))
            reject(corrupt)
        # Failure after preparation and cancellation retain the same explicit ref.
        for mode in ("exit17", "timeout"):
            folder, data, planfile, argv, env = case()
            env["SERVICE_FIXTURE_MODE"] = mode
            if mode == "exit17":
                p = run(argv, env=env)
                assert p.returncode == 17, (p.stdout, p.stderr)
            else:
                process = subprocess.Popen(native_argv(argv), cwd=root, env=env, text=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                allocation = json.loads(process.stdout.readline())
                deadline = time.monotonic() + 15
                while not list((folder / "workspace/project").glob("*-request.json")):
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=20)
                assert process.returncode == 143, (stdout, stderr)
            execution = json.loads(summary(folder).read_text())
            assert execution["methodology"]["plan_identity"] == data["plan"]["plan_identity"]
            assert execution["status"] == ("failed" if mode == "exit17" else "interrupted")
            report(summary(folder))
            attempt = Path(execution['methodology']['snapshot_path']).parent
            observed = inspect(attempt)
            assert observed['execution_file'] == str(summary(folder))
            assert observed['methodology']['plan_identity'] == data['plan']['plan_identity']
            assert observed['execution_request']['methodology'] == execution['methodology']
        folder, data, planfile, argv, env = case(mode="host_mismatch")
        p = run(argv, env=env)
        assert p.returncode == 2, p.stderr
        terminal = json.loads(p.stdout.splitlines()[-1])
        assert terminal["methodology"]["state"] == "checked"
        assert Path(terminal["methodology"]["reference"]["snapshot_path"]).is_file()
        assert not (folder / "workspace").exists()
        attempt = Path(terminal['methodology']['reference']['snapshot_path']).parent
        observed = inspect(attempt)
        assert observed['execution_file'] is None and terminal['execution_file'] is None
        assert observed['methodology']['state'] == 'recorded'
        assert observed['methodology']['plan_identity'] == data['plan']['plan_identity']
        assert observed['execution_request']['methodology'] == terminal['methodology']['reference']
        assert observed['execution_request']['workspace'] == str(folder / 'workspace')
        snapshot = Path(terminal['methodology']['reference']['snapshot_path'])
        snapshot.unlink()
        inspect(attempt, False)
        print(f"namedflow1 ({'source native helpers' if source_mode else 'installed'}): two-profile schema-4 seven-stage bridge execution; frozen report/inspection identity and provenance; source/profile deletion stability; required snapshot/reference negatives; unchanged read-updates; prepared-input and resolved-value vetoes; finalization and failed/interrupted/preflight request evidence passed")


if __name__ == "__main__":
    main()
