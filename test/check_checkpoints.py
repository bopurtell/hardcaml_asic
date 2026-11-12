"""Actual Python validate versus native OCaml; retained fixtures are read-only."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.dont_write_bytecode = True

def verify(consumer, inspector, retained_roots=()):
    from hardcaml_asic_flow import checkpoint as old, execution_plan as plans, presentation
    counts = [0, 0]
    env = dict(os.environ, PATH='/unusable', PYTHONPATH='/unusable')
    def api(**q):
        p = subprocess.run([str(consumer)], input=json.dumps(q) + '\n', text=True,
                           capture_output=True, check=True, env=env, timeout=30)
        return json.loads(p.stdout)
    def compare(path, compatibility=None, **kw):
        compatibility = old.read(path / 'manifest.json')['compatibility'] if compatibility is None else compatibility
        expected = None
        try:
            expected = old.validate(path, compatibility=compatibility,
                **{('_location' if k == 'location' else k): v for k, v in kw.items()})
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass
        actual = api(op='validate', path=str(path), compatibility=compatibility, **kw)
        if expected is None:
            assert 'error' in actual, (path, kw, actual)
            counts[1] += 1
        else:
            assert actual == dict(validation='host_only', runtime_closure_validated=False,
                                  compatibility_source='expected', manifest=expected), (path, kw, actual)
            counts[0] += 1
        return expected
    def inspect(path, *args, expected=None):
        p = subprocess.run([str(inspector), '--json', str(path), *args], text=True,
                           capture_output=True, env=env, timeout=30)
        value = json.loads(p.stdout)
        if expected is None:
            assert p.returncode == 2 and 'error' in value and p.stderr
            assert value['validation'] == 'host_only' and 'manifest' not in value
        else:
            assert p.returncode == 0 and not p.stderr, p.stderr
            assert value == dict(validation='host_only', runtime_closure_validated=False,
                compatibility_source='expected' if '--compatibility' in args else 'recorded', manifest=expected)
            lines = []
            restart_to = args[args.index('--restart-to') + 1] if '--restart-to' in args else None
            presentation.checkpoint(value, path=path, restart_to=restart_to, output=lines.append)
            human = subprocess.run([str(inspector), str(path), *args], text=True,
                                   capture_output=True, env=env, check=True)
            assert human.stdout == '\n'.join(lines) + '\n', (path, human.stdout, lines)
    retained_count = 0
    for root in map(Path, retained_roots):
        manifests = sorted(root.glob('**/checkpoints/*/manifest.json'))
        assert manifests, f'retained fixtures missing: {root}'
        for file in manifests:
            path, before = file.parent, old.digest(file)
            manifest = old.read(file)
            assert api(op='read', path=str(path))['manifest'] == manifest
            expected = compare(path)
            assert expected is not None, (path, 'retained host validation failed')
            inspect(path, expected=expected)
            for target in plans.BOUNDARIES:
                compare(path, restart_to=target)
            compare(path, restart=True)
            changed = copy.deepcopy(manifest['compatibility'])
            changed['bridge_sha256'] = '0' * 64
            compare(path, compatibility=changed)
            assert old.digest(file) == before
            retained_count += 1
    with tempfile.TemporaryDirectory(prefix='checkpoint-cases-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        path = root / 'checkpoint with spaces'
        (path / 'views').mkdir(parents=True)
        host = root / 'host collateral'
        host.write_text('retained input')
        for name in ['nl', 'json_h', 'odb', 'def']:
            (path / 'views' / name).write_bytes(name.encode())
        compatibility = {'target': 'fixture', 'bridge_sha256': '1' * 64,
                         'binding': {'image_identity': {'image_id': plans.TESTED_IMAGE}}}
        closure = {str(host): old.file_record(host),
                   '/nix/store/fixture-only/collateral': {'bytes': 100, 'sha256': 'f' * 64}}
        (path / 'closure.json').write_text(old.canonical(closure))
        for name in ['resolved.json', 'segment.json']:
            (path / name).write_text('{}')
        state = {name: str(path / 'views' / name) for name in ['nl', 'json_h', 'odb', 'def']}
        state['corners'] = {'tt': [str(path / 'views' / 'nl'), None]}
        state['metrics'] = {'infinite': float('inf'), 'negative': -float('inf'), 'nan': float('nan'),
                            'quoted': 'Infinity -Infinity NaN \\"', 'nested': [float('inf')]}
        original_state = json.dumps(state, indent=2).encode()
        (path / 'state.json').write_bytes(original_state)
        def make(schema=2, boundary='synthesis'):
            targets = plans.RESTART_TARGETS[boundary] if schema == 2 else (
                ['floorplan', 'placement'] if boundary == 'synthesis' else [])
            m = dict(schema_version=schema, location=str(path), boundary=boundary,
                restart_supported=bool(targets), compatibility=copy.deepcopy(compatibility),
                files={str(p.relative_to(path)): old.file_record(p) for p in path.rglob('*')
                       if p.is_file() and p.name != 'manifest.json'}, external=copy.deepcopy(closure),
                metrics={'area': {'availability': 'available', 'value': 1.0},
                         'timing': {'availability': 'unavailable', 'reason': 'not observed'}}, acceptance='not_evaluated')
            if schema == 2:
                m.update(restart_targets=list(targets), tested_image_id=plans.TESTED_IMAGE, closure_scope='path_bound')
            return m
        def write(m, sign=True):
            m = copy.deepcopy(m)
            if sign:
                m.pop('identity', None)
                m['identity'] = old.identity(m)
            (path / 'manifest.json').write_text(old.canonical(m))
            return m
        for schema, boundaries in [(1, ['synthesis', 'floorplan', 'placement']), (2, plans.BOUNDARIES)]:
            for boundary in boundaries:
                m = write(make(schema, boundary))
                assert compare(path) == m
                assert api(op='parse', path=str(path), manifest=m)['manifest'] == m
                inspect(path, expected=m)
                compare(path, restart=True)
                for target in plans.BOUNDARIES:
                    compare(path, restart_to=target)
        base = make()
        mutations = [('schema_version', v) for v in [0, 3, True, 1.0, '2', None]] + [
            ('location', str(root / 'relocated')), ('boundary', 'unknown'),
            ('restart_supported', False), ('restart_supported', 1), ('restart_targets', ['placement']),
            ('tested_image_id', 'sha256:wrong'), ('closure_scope', 'relocatable'), ('acceptance', 'accepted'),
            ('files', []), ('files', {}), ('external', {}), ('external', {'relative': old.file_record(host)}),
            ('metrics', []), ('metrics', {'x': {}}),
            ('metrics', {'x': {'availability': 'available', 'value': True}}),
            ('metrics', {'x': {'availability': 'available', 'value': '1'}})]
        for key, value in mutations:
            m = copy.deepcopy(base)
            m[key] = value
            write(m)
            assert compare(path) is None, (key, value)
            inspect(path)
        for name in ['../outside', '/absolute', 'views/../nl', 'state.json']:
            for record in [{'sha256': 'A' * 64, 'bytes': 1}, {'sha256': 'f' * 64, 'bytes': True},
                           {'sha256': 'f' * 64, 'bytes': -1}, {'sha256': 'f' * 64, 'bytes': 1.0},
                           {'sha256': 'f' * 64, 'bytes': 1, 'extra': 0}]:
                m = copy.deepcopy(base)
                m['files'][name] = record
                write(m)
                assert compare(path) is None
        for field in list(base):
            m = copy.deepcopy(base)
            del m[field]
            write(m)
            assert compare(path, compatibility=compatibility) is None
        m = dict(base, future=1)
        write(m)
        assert compare(path) is None
        for mutate in [lambda m: m.update(identity='0' * 64), lambda m: m['metrics']['area'].update(value=2)]:
            m = write(base)
            mutate(m)
            write(m, sign=False)
            assert compare(path) is None
        write(base)
        assert compare(path, compatibility={'wrong': True}) is None
        # Actual compatibility uses Python value equality, not canonical bytes.
        m = copy.deepcopy(base)
        m['compatibility']['numeric_evidence'] = [1, False, 0.0, 2**70]
        write(m)
        expected_compatibility = copy.deepcopy(m['compatibility'])
        expected_compatibility['numeric_evidence'] = [1.0, 0, -0.0, float(2**70)]
        assert compare(path, compatibility=expected_compatibility) is not None
        expected_compatibility['numeric_evidence'][-1] = float(2**70) + 2**20
        assert compare(path, compatibility=expected_compatibility) is None
        write(base)
        expected_file = root / 'expected.json'
        expected_file.write_text(old.canonical(compatibility))
        inspect(path, '--compatibility', str(expected_file), '--restart-to', 'placement', expected=old.read(path / 'manifest.json'))
        expected_file.write_text('{"wrong":true}')
        inspect(path, '--compatibility', str(expected_file))
        m = copy.deepcopy(base)
        m['compatibility']['binding']['image_identity']['image_id'] = 'sha256:wrong'
        write(m)
        assert compare(path) is None
        write(base)
        saved = (path / 'closure.json').read_bytes()
        (path / 'closure.json').write_text('{}')
        write(make())
        assert compare(path, check_external=False) is None
        (path / 'closure.json').write_bytes(saved)
        write(make())
        host.write_text('changed')
        assert compare(path) is None
        assert compare(path, check_external=False) is not None
        host.write_text('retained input')
        write(base)
        view = path / 'views' / 'nl'
        saved = view.read_bytes()
        view.write_bytes(saved + b'changed')
        assert compare(path) is None
        view.unlink()
        assert compare(path) is None
        view.write_bytes(saved)
        extra = path / 'unlisted'
        extra.write_text('extra')
        assert compare(path) is None
        extra.unlink()
        view.unlink()
        view.symlink_to(host)
        assert compare(path) is None
        view.unlink()
        view.write_bytes(saved)
        # Closed inventory rejects dangling/directory/manifest symlinks too;
        # Python's older is_file walk can ignore these. Test that delta explicitly.
        for link in [path / 'dangling', path / 'alias']:
            link.symlink_to(root / 'absent' if link.name == 'dangling' else path / 'views')
            assert 'error' in api(op='validate', path=str(path), compatibility=compatibility)
            link.unlink()
        manifest_bytes = (path / 'manifest.json').read_bytes()
        (root / 'manifest').write_bytes(manifest_bytes)
        (path / 'manifest.json').unlink()
        (path / 'manifest.json').symlink_to(root / 'manifest')
        assert 'error' in api(op='validate', path=str(path), compatibility=compatibility)
        (path / 'manifest.json').unlink()
        (path / 'manifest.json').write_bytes(manifest_bytes)
        real_host = root / 'real-host'
        host.rename(real_host)
        host.symlink_to(real_host)
        assert compare(path) is not None
        host.unlink()
        real_host.rename(host)
        candidate = root / 'private-candidate'
        shutil.copytree(path, candidate)
        assert compare(candidate) is None
        assert compare(candidate, location=str(path)) is not None
        for update in [dict(nl=None), dict(corners={'tt': 1}), dict(nl='relative'),
                       dict(nl=str(root / 'outside')), dict(nl=str(path / 'views' / 'unlisted')),
                       dict(metrics=[]), dict(corners=[float('inf')])]:
            (path / 'state.json').write_text(json.dumps(dict(state, **update)))
            write(make())
            assert compare(path) is None
        (path / 'state.json').write_bytes(original_state)
        write(make())
        assert compare(path) is not None
        assert (path / 'state.json').read_bytes() == original_state
        for replacement in ['1e999', '-1e999']:
            native = json.dumps(dict(state, metrics={})).replace('"metrics": {}',
                '"metrics": {"native":' + replacement + '}')
            (path / 'state.json').write_text(native)
            write(make())
            assert compare(path) is not None
        normalized = copy.deepcopy(state)
        normalized['nl'] = str(path) + '/views//./nl'
        (path / 'state.json').write_text(json.dumps(normalized))
        write(make())
        assert compare(path) is not None
        (path / 'state.json').write_bytes(original_state)
        write(make())
        valid = (path / 'manifest.json').read_text()
        for raw in [valid[:-1] + ',"schema_version":2}', valid + ' trailing',
                    valid.replace('"value":1.0', '"value":Infinity'),
                    valid.replace('"value":1.0', '"value":1e999')]:
            (path / 'manifest.json').write_text(raw)
            assert compare(path, compatibility=compatibility) is None
        write(make())
        human = subprocess.run([str(inspector), str(path), '--restart-to', 'floorplan'],
                               text=True, capture_output=True, env=env, check=True)
        for text in ['host_only', 'recorded', 'runtime validated', 'path_bound',
                     'image-only dependencies', 'not fresh boundary measurements',
                     'not_evaluated', 'no execution performed', 'not established']:
            assert text in human.stdout, (text, human.stdout)
        help_result = subprocess.run([str(inspector), '--help'], text=True, capture_output=True, env=env)
        assert help_result.returncode == 0 and 'host validation' in help_result.stdout
        for args in [[], [''], ['--unknown'], [str(path), '--restart-to', 'bogus'], [str(root / 'missing')]]:
            result = subprocess.run([str(inspector), '--json', *args], text=True, capture_output=True, env=env)
            assert result.returncode == 2 and 'error' in json.loads(result.stdout)
    print(f'checkpoint: {counts[0]} accepted / {counts[1]} rejected Python validation parity cases; '
          f'{retained_count} retained real checkpoints; API, CLI, opaque state and strict symlink checks passed')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--consumer', type=Path)
    p.add_argument('--inspector', type=Path)
    p.add_argument('--installed', action='store_true')
    p.add_argument('--retained-root', type=Path, action='append', default=[],
                   help='also validate every checkpoint in this retained real experiment; missing fixtures fail')
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if not args.installed:
        sys.path.insert(0, str(repo / 'flow'))
        verify(args.consumer.resolve(), args.inspector.resolve(), args.retained_root)
        return
    with tempfile.TemporaryDirectory(prefix='installed-checkpoint-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        prefix, consumer = root / 'prefix', root / 'consumer'
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer.mkdir()
        shutil.copyfile(repo / 'test/checkpoint_consumer.ml', consumer / 'checkpoint_consumer.ml')
        (consumer / 'dune-project').write_text('(lang dune 3.17)\n(name installed_checkpoint)\n')
        (consumer / 'dune').write_text('(executable (name checkpoint_consumer) (libraries hardcaml_asic.execution))\n')
        env = dict(os.environ, OCAMLPATH=str(prefix / 'lib') + ':' + os.environ.get('OCAMLPATH', ''))
        subprocess.run(['dune', 'build', '-j20', 'checkpoint_consumer.exe'], cwd=consumer, env=env, check=True)
        script = root / 'check_checkpoints.py'
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
            f'ns=runpy.run_path({str(script)!r},run_name="checkpoint_test")\n'
            f'ns["verify"](Path({str(consumer / "_build/default/checkpoint_consumer.exe")!r}),Path({str(prefix / "bin/hardcaml-asic-experimental-checkpoint-inspect")!r}),{[str(p.resolve()) for p in args.retained_root]!r})\n')
        subprocess.run([sys.executable, '-I', '-B', str(launcher)], cwd=root, check=True)

if __name__ == '__main__':
    main()
