"""Real publisher processes, actual old/new readers, no physical EDA."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
sys.dont_write_bytecode = True


def verify(consumer, reader):
    from hardcaml_asic_flow import checkpoint as old, execution_plan as plans
    env = dict(os.environ, PATH='/unusable', PYTHONPATH='/unusable')
    checks = 0
    with tempfile.TemporaryDirectory(prefix='publisher-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        store, candidate = root / 'store with spaces', root / 'candidate'
        store.mkdir()
        candidate.mkdir()
        host, source = root / 'host', root / 'netlist'
        host.write_text('collateral')
        source.write_bytes(b'native view\0' * 10000)
        compatibility = {'bridge_sha256': '1' * 64,
                         'binding': {'image_identity': {'image_id': plans.TESTED_IMAGE}}}
        metrics_raw = ('{"inf":Infinity,"neg":-Infinity,"nan":NaN,"overflow":1e999,'
                       '"zero":-0.0,"one":1.0,"big":123456789012345678901234567890,'
                       '"precise":0.123456789012345678901234567890,"text":' + json.dumps('Infinity " NaN') +
                       ',"nested":[Infinity,{"x":null}]}')
        state = dict(nl=str(source), json_h=str(source), odb=str(source), def_=str(source),
                     lib={'tt': [str(source), None]}, optional=None)
        state['def'] = state.pop('def_')
        state_bytes = json.dumps(state)[:-1] + ',"metrics":' + metrics_raw + '}\n'
        # Deliberate raw native metrics, not public identity data.
        json.loads(state_bytes)

        def prepare(image=True, boundary='synthesis'):
            host.write_text('collateral')
            source.write_bytes(b'native view\0' * 10000)
            (candidate / 'state.json').write_text(state_bytes)
            closure = {str(host): old.file_record(host)}
            if image:
                closure['/nix/store/image-only/input'] = {'bytes': 42, 'sha256': 'f' * 64}
            (candidate / 'closure.json').write_text(old.canonical(closure))
            (candidate / 'resolved.json').write_text('{"resolved":"unchanged"}\n')
            (candidate / 'segment.json').write_text(old.canonical(dict(status='completed', boundary=boundary,
                bridge_sha256=compatibility['bridge_sha256'], metrics={'area': {'availability': 'available', 'value': 1.0}})))

        def query(name, mode='ok', **extra):
            return dict(candidate=str(candidate), store=str(store), compatibility=copy.deepcopy(compatibility),
                        boundary='synthesis', name=name, mode=mode, host=str(host), source=str(source), **extra)

        def api(q, executable=consumer):
            p = subprocess.run([str(executable)], input=json.dumps(q) + '\n', text=True,
                               capture_output=True, check=True, env=env, timeout=30)
            return json.loads(p.stdout)

        def reject(q, expected_kind=None):
            nonlocal checks
            result = api(q)
            assert 'error' in result or 'exception' in result, result
            if expected_kind:
                assert result.get('kind') == expected_kind, result
            if '\0' not in q['name']:
                assert not (store / ('.publishing-' + q['name'])).exists(), result
            checks += 1
            return result

        for boundary in plans.BOUNDARIES:
            prepare(boundary=boundary)
            q = query('success-' + boundary)
            q['boundary'] = boundary
            before = {p.name: p.read_bytes() for p in candidate.iterdir()}
            result = api(q)
            assert result['called'] and result['runtime'] == 'validated', result
            path = Path(result['path'])
            manifest = old.validate(path, compatibility=compatibility)
            assert manifest == result['manifest'] and manifest['schema_version'] == 2
            assert result['summary'] == dict(path=str(path), **{k: manifest[k] for k in
                ['identity', 'boundary', 'restart_supported', 'restart_targets', 'closure_scope', 'tested_image_id', 'acceptance']})
            new = api(dict(op='validate', path=str(path), compatibility=compatibility), reader)
            assert new['manifest'] == manifest
            native = (path / 'state.json').read_text()
            assert metrics_raw in native, native
            assert json.loads(native)['metrics']['zero'] == 0.0
            assert len(list((path / 'views').iterdir())) == 5
            for p in path.rglob('*'):
                assert p.stat().st_mode & 0o777 == (0o555 if p.is_dir() else 0o444)
            assert path.stat().st_mode & 0o777 == 0o555
            assert before == {p.name: p.read_bytes() for p in candidate.iterdir()}
            checks += 1

        prepare(image=False)
        result = api(query('host-only', 'raise'))
        assert result['runtime'] == 'not_required' and not result['called'], result
        checks += 1
        # Actual previous production writer -> fresh linked/installed OCaml reader.
        prepare()
        legacy = old.publish(candidate, store, compatibility=compatibility, boundary='synthesis',
                             validate_runtime=lambda staging: None)
        legacy_path = Path(legacy['path'])
        legacy_manifest = old.validate(legacy_path, compatibility=compatibility)
        assert api(dict(op='validate', path=str(legacy_path), compatibility=compatibility), reader)['manifest'] == legacy_manifest
        checks += 1
        for mode in ['missing', 'fail', 'interrupt', 'raise', 'break', 'mutate', 'metadata', 'source', 'host',
                     'candidate', 'extra', 'empty-dir', 'symlink', 'resign']:
            prepare()
            result = reject(query('bad-' + mode, mode), {'fail': 'runtime_failed', 'interrupt': 'interrupted'}.get(mode))
            assert not (store / ('synthesis-bad-' + mode)).exists(), result
            if mode == 'raise':
                assert result['exception'] == 'Sys_error("callback exception retained")'
            if mode == 'break':
                assert result['exception'].endswith('Sys.Break'), result

        prepare()
        q = query('cleanup-failure', 'cleanup-fail')
        r = reject(q, 'cleanup_failed')
        assert 'runtime failed explicitly' in r['error'] and 'cleanup failed' in r['error'], r
        assert (store / '.publishing-cleanup-failure.retained/state.json').is_file()
        assert not (store / 'synthesis-cleanup-failure').exists()

        for mode in ['collision', 'file-collision', 'link-collision']:
            prepare()
            q = query('bad-' + mode, mode)
            reject(q, 'publication_failed')
            p = store / ('synthesis-' + q['name'])
            if mode == 'collision':
                assert p.is_dir() and not list(p.iterdir())
            elif mode == 'file-collision':
                assert p.read_text() == 'user-owned collision'
            else:
                assert p.is_symlink() and p.resolve() == host and host.read_text() == 'collateral'

        for name in ['occupied-file', 'occupied-dir', 'occupied-link']:
            prepare()
            p = store / ('.publishing-' + name)
            if name.endswith('file'):
                p.write_text('owned by user')
            elif name.endswith('dir'):
                p.mkdir()
                (p / 'evidence').write_text('owned by user')
            else:
                p.symlink_to('/does-not-exist')
            before = p.lstat()
            result = api(query(name))
            assert 'error' in result and p.lstat() == before and not result['called'], result
            checks += 1

        # Twelve independently executing publishers contend for the same names.
        prepare()
        q = query('concurrent', 'pause')
        processes = [subprocess.Popen([str(consumer)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, env=env) for _ in range(12)]
        for p in processes:
            p.stdin.write(json.dumps(q) + '\n')
            p.stdin.close()
        results = []
        for p in processes:
            p.wait(timeout=30)
            assert p.returncode == 0, p.stderr.read()
            results.append(json.loads(p.stdout.read()))
        assert sum('path' in r for r in results) == 1, results
        old.validate(store / 'synthesis-concurrent', compatibility=compatibility)
        assert not (store / '.publishing-concurrent').exists()
        checks += 1

        # Mutate a large regular view while streaming/hash passes are running.
        prepare(image=False)
        with source.open('wb') as f:
            for _ in range(64):
                f.write(b'A' * (1024 * 1024))
        stop = threading.Event()
        writes = [0]
        def mutate_copy():
            with source.open('r+b', buffering=0) as f:
                while not stop.is_set():
                    f.seek(0)
                    f.write(bytes([65 + writes[0] % 2]) * 65536)
                    writes[0] += 1
        thread = threading.Thread(target=mutate_copy)
        thread.start()
        try:
            result = reject(query('streaming-mutation'))
            assert writes[0] > 10 and 'changed' in result['error'], result
            assert not (store / 'synthesis-streaming-mutation').exists()
        finally:
            stop.set()
            thread.join()

        # Structural/public/outer consistency negatives fail before readiness.
        for i, (file, value) in enumerate([
            ('segment.json', dict(status='failed', boundary='synthesis', bridge_sha256='1' * 64, metrics={})),
            ('segment.json', dict(status='completed', boundary='placement', bridge_sha256='1' * 64, metrics={})),
            ('segment.json', dict(status='completed', boundary='synthesis', bridge_sha256='2' * 64, metrics={})),
            ('segment.json', dict(status='completed', boundary='synthesis', bridge_sha256='1' * 64,
                                  metrics={'x': {'availability': 'available', 'value': True}})),
            ('closure.json', {}), ('closure.json', {'relative': old.file_record(host)}),
            ('state.json', {'nl': str(source), 'json_h': str(source), 'metrics': []}),
            ('state.json', {'nl': str(source), 'json_h': str(source), 'metrics': {}, 'bad': float('inf')}),
            ('state.json', {'metrics': {}}),
            ('state.json', {'nl': str(root / 'missing-view'), 'json_h': str(source), 'metrics': {}}),
            ('segment.json', dict(status='completed', boundary='synthesis', bridge_sha256='1' * 64,
                                  metrics={'x': {'availability': 'available', 'value': float('inf')}})),
        ]):
            prepare()
            (candidate / file).write_text(json.dumps(value))
            reject(query('invalid-' + str(i)))
        prepare()
        q = query('wrong-image')
        q['compatibility']['binding']['image_identity']['image_id'] = 'sha256:wrong'
        reject(q)
        for name in ['', '.', '..', 'a/b', 'nul\0suffix']:
            prepare()
            reject(query(name))
        prepare()
        (candidate / 'resolved.json').unlink()
        reject(query('missing-metadata'))
        prepare()
        q = query('missing-store')
        q['store'] = str(root / 'missing-store')
        reject(q)
        assert not (root / 'missing-store').exists()
        assert not list(store.glob('.publishing-invalid-*'))
        # Existing schema-1 state/manifest is not an input restart mechanism.
        prepare()
        (candidate / 'manifest.json').write_text('{"schema_version":1}')
        r = reject(query('no-schema1-upgrade'))
        assert 'not a checkpoint/restart input' in r['error']
        assert (candidate / 'manifest.json').read_text() == '{"schema_version":1}'
    print(f'checkpoint publisher: {checks} checks; all seven boundaries, native metric bytes, old/new readers, '
          'explicit callback failures/interruption, mutation vetoes and 12 concurrent no-clobber publishers passed')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--consumer', type=Path)
    p.add_argument('--reader', type=Path)
    p.add_argument('--installed', action='store_true')
    p.add_argument('--native-python', type=Path)
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if not args.installed:
        sys.path.insert(0, str(repo / 'flow'))
        verify(args.consumer.resolve(), args.reader.resolve())
        if args.native_python:
            subprocess.run([str(args.native_python.absolute()), '-B', str(repo / 'test/check_native_publisher_state.py'),
                            str(args.consumer.resolve())], check=True)
        return
    with tempfile.TemporaryDirectory(prefix='installed-publisher-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        prefix, consumer = root / 'prefix', root / 'consumer'
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer.mkdir()
        for name in ['checkpoint_consumer.ml', 'checkpoint_publisher_consumer.ml']:
            shutil.copyfile(repo / 'test' / name, consumer / name)
        (consumer / 'dune-project').write_text('(lang dune 3.17)\n(name installed_publisher)\n')
        (consumer / 'dune').write_text(''.join(f'(executable (name {name}) (modules {name}) (libraries hardcaml_asic.execution))\n'
            for name in ['checkpoint_consumer', 'checkpoint_publisher_consumer']))
        env = dict(os.environ, OCAMLPATH=str(prefix / 'lib') + ':' + os.environ.get('OCAMLPATH', ''))
        subprocess.run(['dune', 'build', '-j20'], cwd=consumer, env=env, check=True)
        script = root / 'check_checkpoint_publisher.py'
        shutil.copyfile(Path(__file__), script)
        launcher = root / 'launch.py'
        launcher.write_text('import sys,runpy\nfrom pathlib import Path\n'
            f'repo=Path({str(repo)!r})\n'
            'def audit(event,args):\n'
            ' if event=="open" and isinstance(args[0],(str,bytes)):\n'
            '  if Path(args[0]).resolve().is_relative_to(repo): raise RuntimeError("checkout read denied")\n'
            ' if event=="import" and (args[0].startswith("librelane") or "librelane_bridge" in args[0]): raise RuntimeError("backend import denied")\n'
            'sys.addaudithook(audit)\n'
            f'sys.path.insert(0,{str(root)!r})\nfrom reference_harness import activate\nactivate()\n'
            f'ns=runpy.run_path({str(script)!r},run_name="publisher_test")\n'
            f'ns["verify"](Path({str(consumer / "_build/default/checkpoint_publisher_consumer.exe")!r}),Path({str(consumer / "_build/default/checkpoint_consumer.exe")!r}))\n')
        subprocess.run([sys.executable, '-I', '-B', str(launcher)], cwd=root, check=True)
        if args.native_python:
            native_test = root / 'check_native_publisher_state.py'
            shutil.copyfile(repo / 'test/check_native_publisher_state.py', native_test)
            subprocess.run([str(args.native_python.absolute()), '-B', str(native_test),
                            str(consumer / '_build/default/checkpoint_publisher_consumer.exe')], cwd=root, check=True)


if __name__ == '__main__':
    main()
