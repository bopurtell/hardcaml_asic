"""Synthetic public comparison tests; no EDA or simulation claim."""
import argparse
import copy
import hashlib
import json
import os
import select
from pathlib import Path
import subprocess
import tempfile


def raw(j):
    return json.dumps(j, separators=(',', ':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(p, j):
    p.write_bytes(raw(j))


def ref(p):
    return dict(path=p.name, sha256=sha(p.read_bytes()), bytes=p.stat().st_size)


def fixture(root, variant, model):
    root.mkdir()
    bundle = root / 'bundle'
    bundle.mkdir()
    rtl = ('module top; /* ' + model + ' */ endmodule\n').encode()
    (bundle / 'top.v').write_bytes(rtl)
    manifest = dict(schema_version=1, adapter='LibreLane', project='test', top_module='top',
                    resources=[], files=[dict(path='top.v', sha256=sha(rtl))],
                    synthesis_sources=['top.v'], simulation_sources=['top.v'], source_inputs=[],
                    hardware_variant=variant)
    manifest = dict(identity=sha(raw(manifest)), **manifest)
    write(bundle / 'manifest.json', manifest)
    artifacts = []
    for role, data in [('rtl', rtl), ('model', model.encode()), ('testbench', b'tb'), ('tool', b'tool-v1')]:
        p = root / (role + '.txt')
        p.write_bytes(data)
        artifacts.append(dict(role=role, source=ref(p)))
    ops = [dict(enable=True, write=True, address=255, data=123),
           dict(enable=True, write=False, address=255, data=0),
           dict(enable=False, write=False, address=0, data=0)]
    expected = [dict(before=None, after=None), dict(before=None, after=123), dict(before=123, after=123)]
    workload = dict(protocol='memory-1rw-latency1-v1', seed=0, depth=256, width=16,
                    window=dict(start=0, stop=3), operations=ops, expected=expected)
    workload['identity'] = sha(json.dumps(workload, sort_keys=True, separators=(',', ':')).encode())
    write(root / 'stimulus.json', dict(operations=ops))
    write(root / 'trace.json', dict(rows=[dict(cycle=i, input=op, **expected[i]) for i, op in enumerate(ops)]))
    sim = dict(contract='hardcaml-asic.workload-simulation', schema_version=1, attempt_id=model,
               status='completed', bundle_identity=manifest['identity'], hardware_variant_identity=variant['identity'],
               workload_identity=workload['identity'], stimulus=ref(root / 'stimulus.json'), trace=ref(root / 'trace.json'),
               rtl_mode='implementation', artifacts=artifacts, adapter='memory-1rw-latency1-v1',
               clock='posedge-single-clock-v1', model='memory-interface-v1', measurement='memory-1rw-window-v1')
    write(root / 'simulation.json', sim)
    result = dict(contract='hardcaml-asic.workload-result', schema_version=1, bundle=str(bundle), workload=workload,
                  simulation=ref(root / 'simulation.json'), stimulus=sim['stimulus'], trace=sim['trace'],
                  counters=dict(cycles=3, reads=1, writes=1, operations=2, defined_observations=3, read_latency=1))
    write(root / 'result.json', result)
    return bundle, root / 'result.json', sim, result


def main():
    ap = argparse.ArgumentParser()
    for key in ['consumer', 'cli', 'variant', 'fence']:
        ap.add_argument('--' + key, type=Path, required=True)
    a = ap.parse_args()
    consumer, cli, variant = [getattr(a, k).resolve() for k in ['consumer', 'cli', 'variant']]
    checks = 0

    def call(args, error=False):
        nonlocal checks
        p = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1'))
        checks += 1
        assert p.returncode == (2 if error else 0), (args, p.returncode, p.stdout, p.stderr)
        return p.stderr if error else json.loads(p.stdout)

    v = call([variant, 'variant'])
    with tempfile.TemporaryDirectory(prefix='pt8-comparison-') as tmp:
        root = Path(tmp)
        l, lw, ls, lr = fixture(root / 'left', v, 'flops')
        r, rw, rs, rr = fixture(root / 'right', v, 'sram')

        def compare(left_workload=lw, right_workload=rw, error=False):
            return call([consumer, l, r, left_workload, right_workload], error)

        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        good = compare()
        assert good['left']['workload']['binding']['bundle'] == str(l.resolve(strict=True))
        assert good['right']['workload']['binding']['bundle'] == str(r.resolve(strict=True))
        assert good['left']['workload']['binding']['bundle_identity'] == ls['bundle_identity']
        assert good['right']['workload']['binding']['bundle_identity'] == rs['bundle_identity']
        assert original == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original}
        rows = {m['metric_id']: m for m in good['metrics'] if m['metric_id'].startswith('workload/')}
        assert len(rows) == 7 and all(m['comparison'] == 'comparable' for m in rows.values())
        assert rows['workload/cycles']['right_minus_left'] == 0
        assert rows['workload/cycles']['left']['controls_digest'] in good['left']['metric_contexts']
        assert rows['workload/throughput']['left']['numerator'] == 2
        assert rows['workload/throughput']['left']['denominator'] == 3
        assert rows['workload/throughput']['right_minus_left_ratio'] == dict(numerator=0, denominator=1)
        assert good['left']['identities']['attempt_id'] is None
        assert good['left']['workload']['binding']['simulation_attempt_id'] == 'flops'
        assert good['left']['workload']['provenance']['artifacts'] != good['right']['workload']['provenance']['artifacts']
        cli_args = [cli, 'compare', '--left-kind', 'bundle', '--left', l, '--right-kind', 'bundle', '--right', r,
                    '--axis', 'resource_policy', '--left-workload', lw, '--right-workload', rw, '--json']
        assert call(cli_args) == good
        call(cli_args + ['--left-workload', lw], True)
        assert 'does not match target' in compare(rw, lw, True)
        old = compare('-', '-')
        assert old['workload']['status'] == 'unavailable' and 'workload' not in old['left']
        one = compare(lw, '-')
        assert all(m['comparison'] == 'unavailable' for m in one['metrics'] if m['metric_id'].startswith('workload/'))
        # Rebound false counters must still replay, not trust checksums/counters.
        for key in rr['counters']:
            bad = copy.deepcopy(rr)
            bad['counters'][key] += 1
            write(rw, bad)
            assert 'stored counters differ' in compare(error=True)
        write(rw, rr)
        for key in ['bundle_identity', 'hardware_variant_identity', 'workload_identity']:
            bad_sim = dict(rs, **{key: '0' * 64})
            write(rw.parent / 'simulation.json', bad_sim)
            write(rw, dict(rr, simulation=ref(rw.parent / 'simulation.json')))
            assert 'mismatch' in compare(error=True)
        # A fully coherent different logical window is valid retained evidence,
        # but must not compare just because cycle counts are well formed.
        windowed = copy.deepcopy(rr)
        windowed['workload']['window']['start'] = 1
        unsigned = {k: v for k, v in windowed['workload'].items() if k != 'identity'}
        windowed['workload']['identity'] = sha(json.dumps(unsigned, sort_keys=True, separators=(',', ':')).encode())
        windowed['counters'].update(cycles=2, writes=0, operations=1)
        write(rw.parent / 'simulation.json', dict(rs, workload_identity=windowed['workload']['identity']))
        windowed['simulation'] = ref(rw.parent / 'simulation.json')
        write(rw, windowed)
        assert all(m['comparison'] == 'confounded' and m['right_minus_left'] is None
                   for m in compare()['metrics'] if m['metric_id'].startswith('workload/'))
        write(rw.parent / 'simulation.json', rs)
        write(rw, rr)
        # Rebound different functional semantics confound without comparing model bytes.
        rs['model'] = 'other-interface-v1'
        write(rw.parent / 'simulation.json', rs)
        changed = dict(rr, simulation=ref(rw.parent / 'simulation.json'))
        write(rw, changed)
        assert all(m['comparison'] == 'confounded' and m['right_minus_left'] is None
                   for m in compare()['metrics'] if m['metric_id'].startswith('workload/'))
        rs['model'] = 'memory-interface-v1'
        write(rw.parent / 'simulation.json', rs)
        write(rw, rr)
        # Changed actual raw sources reject stale refs; coherent rebindings stay distinct.
        model = rw.parent / 'model.txt'
        model.write_bytes(b'changed concrete SRAM model')
        assert 'source reference mismatch' in compare(error=True)
        rs['artifacts'][1]['source'] = ref(model)
        write(rw.parent / 'simulation.json', rs)
        write(rw, dict(rr, simulation=ref(rw.parent / 'simulation.json')))
        fresh = compare()
        assert fresh['comparison_digest'] != good['comparison_digest']
        assert fresh['right']['source_digest'] != good['right']['source_digest']
        assert all(m['comparison'] == 'comparable' for m in fresh['metrics'] if m['metric_id'].startswith('workload/'))
        # Exact rational subtraction, with unreduced source ratios retained.
        throughput = good['left']['workload']['metrics'][-1]
        other = dict(throughput, value=0.75, numerator=3, denominator=4)
        ratio = call([consumer, 'metric', json.dumps(throughput), json.dumps(other)])
        assert ratio['right_minus_left_ratio'] == dict(numerator=1, denominator=12)
        # Whole-generation recheck after opposite-side capture: a formatting-only
        # result change has valid semantics and no outer ref to fail accidentally.
        for name in ['result.json', 'simulation.json', 'stimulus.json', 'trace.json', 'model.txt', 'tool.txt', 'testbench.txt', 'rtl.txt']:
            source = lw.parent / name
            original = source.read_bytes()
            notify_read, notify_write = os.pipe()
            release_read, release_write = os.pipe()
            env = dict(os.environ, PATH='/no-tools', LD_PRELOAD=str(a.fence.resolve()),
                       PT8_NOTIFY=str(notify_write), PT8_RELEASE=str(release_read))
            proc = subprocess.Popen(list(map(str, [consumer, l, r, lw, rw])), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    pass_fds=(notify_write, release_read))
            try:
                assert select.select([notify_read], [], [], 10)[0], 'reader never reached right capture'
                assert os.read(notify_read, 1) == b'R'
                source.write_bytes(original + b'\n')
                os.write(release_write, b'R')
                stdout, stderr = proc.communicate(timeout=30)
                checks += 1
                assert proc.returncode == 2 and not stdout, (proc.returncode, stdout, stderr)
                assert b'stale workload' in stderr or b'source reference mismatch' in stderr, stderr
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for fd in [notify_read, notify_write, release_read, release_write]:
                    os.close(fd)
                source.write_bytes(original)
        # Family policy seam exercises otherwise unsupported metadata mismatches.
        metric = good['left']['workload']['metrics'][0]
        for key in ['stage', 'definition', 'scope', 'unit']:
            bad = dict(metric, **{key: 'different'})
            m = call([consumer, 'metric', json.dumps(metric), json.dumps(bad)])
            assert m['comparison'] == 'confounded' and m['right_minus_left'] is None
        for key in ['workload', 'adapter', 'clock', 'model', 'measurement', 'rtl_mode', 'tool']:
            bad = copy.deepcopy(metric)
            bad['controls'][key] = ['different-model-set'] if key == 'model' else 'different'
            m = call([consumer, 'metric', json.dumps(metric), json.dumps(bad)])
            assert m['comparison'] == 'confounded' and m['right_minus_left'] is None
            del bad['controls'][key]
            m = call([consumer, 'metric', json.dumps(metric), json.dumps(bad)])
            assert m['comparison'] == 'unavailable' and m['right_minus_left'] is None
        corner = dict(metric, scope=dict(metric['scope'], corner='slow'))
        other_corner = dict(metric, scope=dict(metric['scope'], corner='fast'))
        m = call([consumer, 'metric', json.dumps(corner), json.dumps(other_corner)])
        assert m['comparison'] == 'confounded' and m['right_minus_left'] is None
        # Readable failed simulation retains outcome and withholds every delta.
        rs['status'] = 'failed'
        write(rw.parent / 'simulation.json', rs)
        write(rw, dict(rr, simulation=ref(rw.parent / 'simulation.json')))
        failed = compare()
        assert failed['right']['workload']['outcome']['status'] == 'failed'
        assert all(m['comparison'] == 'unavailable' and m['right_minus_left'] is None
                   for m in failed['metrics'] if m['metric_id'].startswith('workload/'))
    print(f'{checks} workload comparison API/CLI checks passed')


if __name__ == '__main__':
    main()
