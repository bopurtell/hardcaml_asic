"""Public pair/codec/scope/arithmetic negatives; no tools, builds or EDA."""
import argparse
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture as coarse_fixture
from check_stage_report import fixture as checkpoint_fixture, execution


def write(p, value):
    p.write_text(json.dumps(value, separators=(',', ':')))


def rehash(p, transform):
    j = json.loads(p.read_text())
    j.pop('identity')
    transform(j)
    identity = hashlib.sha256(json.dumps(j, separators=(',', ':')).encode()).hexdigest()
    write(p, dict(identity=identity, **j))


def snapshot(root):
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--consumer', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    a = parser.parse_args()
    consumer, cli = a.consumer.resolve(), a.cli.resolve()
    env = dict(os.environ, PATH='/no-external-tools', PYTHONPATH='/no-current-catalog',
               PYTHONDONTWRITEBYTECODE='1')
    checks = 0

    def call(args, code=0):
        nonlocal checks
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True, env=env, timeout=60)
        assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
        checks += 1
        if code == 2:
            assert not result.stdout and result.stderr
            return result.stderr
        return json.loads(result.stdout)

    with tempfile.TemporaryDirectory(prefix='comparison-') as tmp:
        capability = call([cli, '--version', '--json'])['cli_contract']['capabilities']['variant_comparison1']
        assert capability['read_only'] and capability['schema_version'] == 1
        assert capability['max_response_bytes'] == 1024 * 1024
        root = Path(tmp)
        coarse = root / 'coarse'
        coarse.mkdir()
        coarse_fixture(coarse)
        stage = root / 'stage'
        stage.mkdir()
        ref = checkpoint_fixture(stage)
        exe = stage / 'execution-attempt.json'
        write(exe, execution([ref]))
        bundle = coarse / 'project'
        targets = dict(bundle=bundle, coarse_run=coarse, execution=exe)
        original = snapshot(root)
        for lk, lp in targets.items():
            for rk, rp in targets.items():
                api = call([consumer, 'descriptive', lk, lp, rk, rp])
                public = call([cli, 'compare', '--left-kind', lk, '--left', lp,
                               '--right-kind', rk, '--right', rp, '--axis', 'descriptive', '--json'])
                assert api == public
                assert api['left']['kind'] == lk and api['right']['kind'] == rk
                assert api['schema_version'] == 1 and not api['truncated']
                assert api['workload']['status'] == 'unavailable'
                assert len(json.dumps(api).encode()) <= 1024 * 1024
                assert 'winner' not in api
                for metric in api['metrics']:
                    assert metric['right_minus_left'] is None, 'missing controls must withhold fixture deltas'
        assert original == snapshot(root), 'public/API queries must preserve all source bytes/mtimes'
        for axis in ['hardware', 'resource_policy', 'flow_configuration']:
            j = call([consumer, axis, 'bundle', bundle, 'execution', exe])
            assert j['axis'] == axis
        for args in [[], ['--axis', 'invalid'], ['--left-kind', 'latest'],
                     ['--query', 'metrics'], ['--axis', 'hardware', 'extra'],
                     ['--left', str(bundle), '--left', str(bundle)]]:
            call([cli, 'compare', *args, '--json'], 2)
        call([consumer, 'descriptive', 'finalization', bundle, 'bundle', bundle], 2)
        call([consumer, 'descriptive', 'execution', stage, 'bundle', bundle], 2)

        # Coherently rehash the outer manifest: optional metadata is still strict.
        valid = call([consumer, 'variant'])
        manifest = bundle / 'manifest.json'
        baseline = manifest.read_bytes()
        rehash(manifest, lambda j: j.update(hardware_variant=valid))
        good = call([consumer, 'hardware', 'bundle', bundle, 'bundle', bundle])
        assert good['left']['hardware_variant'] == valid
        assert good['left']['hardware_metadata_status'] == 'recorded'
        for mutation in [lambda j: None, lambda j: {}, lambda j: dict(j, identity='0' * 64),
                         lambda j: dict(j, assignments={}), lambda j: dict(j, assignments={'depth': 257}),
                         lambda j: dict(j, assignments={'depth': 256.0}), lambda j: dict(j, extra=True),
                         lambda j: dict(j, schema_version=99)]:
            manifest.write_bytes(baseline)
            rehash(manifest, lambda j: j.update(hardware_variant=mutation(copy.deepcopy(valid))))
            call([consumer, 'hardware', 'bundle', bundle, 'bundle', bundle], 2)
        manifest.write_bytes(baseline)
        old = call([consumer, 'hardware', 'bundle', bundle, 'bundle', bundle])
        assert old['left']['hardware_metadata_status'] == 'unavailable'

        # Member bytes remain independently authoritative even with a valid
        # hardware declaration and coherently rehashed manifest identity.
        member = bundle / 'input.txt'
        member.write_text('recorded input bytes')
        rehash(manifest, lambda j: j.update(hardware_variant=valid,
               files=[dict(path='input.txt', sha256=hashlib.sha256(member.read_bytes()).hexdigest())]))
        call([consumer, 'hardware', 'bundle', bundle, 'bundle', bundle])
        member.write_text('changed input bytes')
        call([consumer, 'hardware', 'bundle', bundle, 'bundle', bundle], 2)
        manifest.write_bytes(baseline)
        member.unlink()

        empty = root / 'empty'
        missing = root / 'historical-missing'
        empty.mkdir()
        missing.mkdir()
        (empty / 'manifest.json').write_bytes(baseline)
        (missing / 'manifest.json').write_bytes(baseline)
        rehash(empty / 'manifest.json', lambda j: (j.update(resources=[]), j.pop('macro_contract')))
        rehash(missing / 'manifest.json', lambda j: (j.pop('resources'), j.pop('macro_contract')))
        inventories = call([consumer, 'descriptive', 'bundle', empty, 'bundle', missing])
        assert inventories['left']['inventory_status'] == 'available'
        assert inventories['left']['resources'] == []
        assert inventories['right']['inventory_status'] == 'unavailable'

        # A coarse run must accept the optional checked metadata through all its
        # existing resource/report replay gates, with its manifest binding updated.
        run_file = coarse / 'run.json'
        run_baseline = run_file.read_bytes()
        rehash(manifest, lambda j: j.update(hardware_variant=valid))
        run = json.loads(run_baseline)
        run.update(build_identity=json.loads(manifest.read_text())['identity'],
                   manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        write(run_file, run)
        updated = call([consumer, 'hardware', 'coarse_run', coarse, 'bundle', bundle])
        assert updated['left']['hardware_variant'] == valid
        rehash(manifest, lambda j: j.update(hardware_variant=dict(valid, identity='0' * 64)))
        run.update(build_identity=json.loads(manifest.read_text())['identity'],
                   manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        write(run_file, run)
        call([consumer, 'hardware', 'coarse_run', coarse, 'bundle', bundle], 2)
        manifest.write_bytes(baseline)
        run_file.write_bytes(run_baseline)

        # Stored assertions cannot be silently replaced by fresh reconciliation
        # when their claimed observations no longer match their source replay.
        stored = call([consumer, 'collect', coarse])
        results_file = coarse / 'results.json'
        write(results_file, stored)
        call([consumer, 'descriptive', 'coarse_run', coarse, 'bundle', bundle])
        stored['resource_evidence']['observations'][0]['measurements']['macro_count']['value'] = 77
        write(results_file, stored)
        call([consumer, 'descriptive', 'coarse_run', coarse, 'bundle', bundle], 2)
        results_file.unlink()

        # Changed source hash and crossed execution/checkpoint provenance reject.
        broken = json.loads(exe.read_text())
        broken['artifacts'][0]['identity'] = '0' * 64
        original_execution = exe.read_bytes()
        write(exe, broken)
        call([consumer, 'descriptive', 'execution', exe, 'bundle', bundle], 2)
        exe.write_bytes(original_execution)
        record = coarse / 'run.json'
        original_record = record.read_bytes()
        j = json.loads(original_record)
        j['build_identity'] = 'wrong-bundle'
        write(record, j)
        call([consumer, 'descriptive', 'coarse_run', coarse, 'bundle', bundle], 2)
        record.write_bytes(original_record)

        # A deterministic generation race: opening the right metadata triggers a
        # changed valid left manifest after left capture, before final replay.
        other = root / 'right'
        other.mkdir()
        other_manifest = other / 'manifest.json'
        other_manifest.write_bytes(baseline)
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK)
        assert fd >= 0
        assert libc.inotify_add_watch(fd, os.fsencode(other_manifest), 0x20) >= 0  # IN_OPEN
        mutated = threading.Event()

        def mutate_after_right_open():
            assert select.select([fd], [], [], 10)[0]
            os.read(fd, 65536)
            rehash(manifest, lambda j: j.update(hardware_variant=valid))
            mutated.set()

        worker = threading.Thread(target=mutate_after_right_open)
        worker.start()
        error = call([consumer, 'hardware', 'bundle', bundle, 'bundle', other], 2)
        worker.join()
        os.close(fd)
        assert mutated.is_set() and 'generation' in error, error
        manifest.write_bytes(baseline)

        # Registry policy seam: all controls are supplied as already verified facts.
        controls = {key: 'same-recorded-fact' for key in ['source', 'target', 'libraries',
                    'environment', 'timing_environment', 'rc', 'hardware', 'resource_policy',
                    'resource_contracts', 'flow_configuration']}
        m = dict(metric_id='native/typ/hold_slack', name='hold_slack', value=-0.25,
                 classification='observed', quantity='real', unit='ns', stage='post_pnr',
                 definition='native_worst_hold', scope={'corner': 'typ', 'coverage': 'whole_design'},
                 controls=controls, sources={'log': 'verified-ref'})
        metric_file = root / 'metric.json'

        def compare(left, right, axis='descriptive'):
            write(metric_file, dict(left=left, right=right, axis=axis))
            return call([consumer, 'metric', metric_file])

        right = dict(m, value=0.125)
        assert compare(m, right)['right_minus_left'] == 0.375
        assert compare(right, m)['right_minus_left'] == -0.375
        for field, value in [('stage', 'synthesized_netlist'), ('unit', 'ps'),
                             ('definition', 'sampled_hold'), ('scope', {'corner': 'slow'})]:
            result = compare(m, dict(right, **{field: value}))
            assert result['comparison'] == 'confounded' and result['right_minus_left'] is None
        for field in controls:
            changed = copy.deepcopy(right)
            changed['controls'][field] = 'different-recorded-fact'
            result = compare(m, changed)
            assert result['comparison'] == 'confounded', (field, result)
            changed['controls'][field] = None
            assert compare(m, changed)['comparison'] == 'unavailable'
        for axis, field in [('hardware', 'hardware'), ('resource_policy', 'resource_policy'),
                            ('flow_configuration', 'flow_configuration')]:
            changed = copy.deepcopy(right)
            changed['controls'][field] = 'intended-change'
            assert compare(m, changed, axis)['comparison'] == 'comparable'
            changed['controls']['libraries'] = 'uncontrolled-change'
            assert compare(m, changed, axis)['comparison'] == 'confounded'
        for classification in ['estimate', 'unknown']:
            result = compare(dict(m, classification=classification), dict(right, classification=classification))
            assert result['comparison'] == 'unavailable' and result['right_minus_left'] is None
        for value in [float('nan'), float('inf'), -float('inf'), None, True]:
            assert compare(m, dict(right, value=value))['right_minus_left'] is None
        count = dict(m, name='macro_count', quantity='integer', value=10**100, unit='count', classification='derived')
        assert compare(count, dict(count, value=10**100 + 7))['right_minus_left'] == 7
        assert compare(count, dict(count, value=10**100 - 7))['right_minus_left'] == -7
        assert compare(count, dict(count, value=float(2**53)))['right_minus_left'] is None
        assert compare(m, dict(right, value=2))['right_minus_left'] is None, 'mixed arithmetic must not silently round'

        # Whole-response bound: valid finite source manifests can still be too
        # large for the comparison envelope. This is an explicit error, not truncation.
        huge = root / 'huge'
        huge.mkdir()
        huge_manifest = huge / 'manifest.json'
        huge_manifest.write_bytes(baseline)
        rehash(huge_manifest, lambda j: j.update(flow_configuration={'recorded': 'x' * (1024 * 1024)}))
        assert '1MiB' in call([consumer, 'descriptive', 'bundle', huge, 'bundle', huge], 2)
    print(f'variant comparison: {checks} public/API/scope/negative checks passed')


if __name__ == '__main__':
    main()
