"""Provider replay/admission tests. Synthetic retained traces, no EDA/simulator."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def raw(j, canonical=False):
    return json.dumps(j, sort_keys=canonical, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def identity(j):
    return hashlib.sha256(raw(j, True)).hexdigest()


def write(p, j):
    p.write_bytes(raw(j))


def ref(p):
    data = p.read_bytes()
    return dict(path=p.name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--consumer', type=Path, required=True)
    consumer = parser.parse_args().consumer.resolve()
    checks = 0

    def invoke(path, error=None, digest=None):
        nonlocal checks
        args = [str(consumer), str(path)] + ([digest] if digest else [])
        p = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1'))
        checks += 1
        assert p.returncode == (2 if error else 0), (p.returncode, p.stdout, p.stderr, error)
        if error:
            assert error in p.stderr, (error, p.stderr)
            return
        return json.loads(p.stdout)

    with tempfile.TemporaryDirectory(prefix='pt8-workload-') as tmp:
        root = Path(tmp)
        bundle = root / 'bundle'
        bundle.mkdir()
        variant = invoke('variant')
        rtl = b'module top; endmodule\n'
        (bundle / 'top.v').write_bytes(rtl)
        manifest = dict(schema_version=1, adapter='LibreLane', project='test', top_module='top',
                        resources=[], files=[dict(path='top.v', sha256=hashlib.sha256(rtl).hexdigest())],
                        synthesis_sources=['top.v'], simulation_sources=['top.v'], source_inputs=[],
                        hardware_variant=variant)
        manifest = dict(identity=hashlib.sha256(raw(manifest)).hexdigest(), **manifest)
        write(bundle / 'manifest.json', manifest)
        artifacts = []
        for role, data in [('rtl', rtl), ('model', b'functional-memory-v1'),
                           ('testbench', b'before/after edge testbench'), ('tool', b'fixture-not-real-execution')]:
            p = root / (role + '.txt')
            p.write_bytes(data)
            artifacts.append(dict(role=role, source=ref(p)))
        # All addresses, meaningful patterns, read-after-write, disabled writes,
        # stalls and undefined initial/write outputs. A nonzero sample start.
        ops = [dict(enable=True, write=False, address=255, data=0)]
        for address in range(256):
            ops += [dict(enable=True, write=True, address=address, data=(address * 257) ^ 0x5a5a),
                    dict(enable=True, write=False, address=address, data=0),
                    dict(enable=False, write=True, address=address, data=0),
                    dict(enable=True, write=False, address=address, data=65535)]
        memory, output, expected, rows = {}, None, [], []
        counts = dict(cycles=0, reads=0, writes=0, defined_observations=0, read_latency=1, operations=0)
        for cycle, op in enumerate(ops):
            before = output
            if op['enable']:
                if op['write']:
                    memory[op['address']] = op['data']
                    output = None
                else:
                    output = memory.get(op['address'])
            expected.append(dict(before=before, after=output))
            rows.append(dict(cycle=cycle, input=op, before=before, after=output))
            if cycle >= 1:
                counts['cycles'] += 1
                counts['reads'] += int(op['enable'] and not op['write'])
                counts['writes'] += int(op['enable'] and op['write'])
                counts['defined_observations'] += int(before is not None) + int(output is not None)
        counts['operations'] = counts['reads'] + counts['writes']
        workload = dict(protocol='memory-1rw-latency1-v1', seed=42, depth=256, width=16,
                        window=dict(start=1, stop=len(ops)), operations=ops, expected=expected)
        workload['identity'] = identity(workload)
        write(root / 'stimulus.json', dict(operations=ops))
        write(root / 'trace.json', dict(rows=rows))
        sim = dict(contract='hardcaml-asic.workload-simulation', schema_version=1,
                   attempt_id='test-attempt-001', status='completed', bundle_identity=manifest['identity'],
                   hardware_variant_identity=variant['identity'], workload_identity=workload['identity'],
                   stimulus=ref(root / 'stimulus.json'), trace=ref(root / 'trace.json'),
                   rtl_mode='implementation', artifacts=artifacts, adapter='memory-1rw-latency1-v1',
                   clock='posedge-single-clock-v1', model='functional-memory-v1', measurement='memory-1rw-window-v1')
        result = dict(contract='hardcaml-asic.workload-result', schema_version=1, bundle=str(bundle),
                      workload=workload, stimulus=sim['stimulus'], trace=sim['trace'], counters=counts)

        def save(s=None, r=None, trace=None):
            s, r = copy.deepcopy(sim if s is None else s), copy.deepcopy(result if r is None else r)
            if trace is not None:
                write(root / 'trace.json', trace)
            r['trace'] = s['trace'] = ref(root / 'trace.json')
            write(root / 'simulation.json', s)
            r['simulation'] = ref(root / 'simulation.json')
            write(root / 'result.json', r)
            return root / 'result.json'

        path = save()
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        good = invoke(path)
        assert good['binding']['bundle'] == str(bundle.resolve(strict=True))
        assert good['binding']['bundle_identity'] == manifest['identity']
        assert good['outcome']['functional'] == 'pass'
        assert good['outcome']['recomputed_counters'] == counts
        metrics = {m['name']: m for m in good['metrics']}
        assert metrics['throughput']['numerator'] == 768
        assert metrics['throughput']['denominator'] == 1024
        assert metrics['throughput']['value'] == 0.75
        assert metrics['read_latency']['value'] == 1
        assert 'firmware_size' not in metrics and 'fetch_count' not in metrics
        assert original == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original}
        invoke(path, digest=good['source_digest'])
        # The selected published root may be a link, but the additive locator
        # names the actual verified canonical root, not the alias spelling.
        published = root / 'published-bundle'
        published.symlink_to(bundle.name, target_is_directory=True)
        aliased = invoke(save(r=dict(result, bundle=str(published))))
        assert aliased['binding']['bundle'] == str(bundle.resolve(strict=True))
        assert aliased['binding']['bundle_identity'] == good['binding']['bundle_identity']
        save()
        # Coherent false counters: all outer references/checksums freshly rebound,
        # same trace and logical workload, not merely a stale-hash rejection.
        for key in counts:
            bad = copy.deepcopy(result)
            bad['counters'][key] += 1
            invoke(save(r=bad), 'stored counters differ')
        bad = copy.deepcopy(result)
        bad['counters']['reads'] += 1
        bad['counters']['operations'] += 1
        invoke(save(r=bad), 'stored counters differ')
        for key in ['bundle_identity', 'hardware_variant_identity', 'workload_identity']:
            bad = dict(sim, **{key: '0' * 64})
            invoke(save(s=bad), 'mismatch')
        bad = dict(sim, clock='negedge')
        invoke(save(s=bad), 'unsupported clock')
        for key, value in [('adapter', 'labels-do-not-validate-behavior'),
                           ('measurement', 'other-window-definition'), ('rtl_mode', 'unknown')]:
            invoke(save(s=dict(sim, **{key: value})), 'unsupported')
        # Rebound artifact inventory cannot quietly omit required concrete roles.
        bad = dict(sim, artifacts=artifacts + [artifacts[0]])
        invoke(save(s=bad), 'duplicate artifact')
        bad = dict(sim, artifacts=[artifacts[0], artifacts[1], artifacts[2], artifacts[2]])
        invoke(save(s=bad), 'missing artifact role tool')
        bad = copy.deepcopy(result)
        bad['workload']['expected'][2]['after'] ^= 1
        unsigned = {k: v for k, v in bad['workload'].items() if k != 'identity'}
        bad['workload']['identity'] = identity(unsigned)
        rebound = dict(sim, workload_identity=bad['workload']['identity'])
        invoke(save(s=rebound, r=bad), 'independent memory oracle')
        bad = copy.deepcopy(result)
        bad['workload']['window']['start'] = 2
        bad['workload']['identity'] = identity({k: v for k, v in bad['workload'].items() if k != 'identity'})
        rebound = dict(sim, workload_identity=bad['workload']['identity'])
        invoke(save(s=rebound, r=bad), 'stored counters differ')
        # Changed stimulus, with every reference rebound, still disagrees with
        # the logical workload rather than just failing a checksum gate.
        changed_ops = copy.deepcopy(ops)
        changed_ops[1]['address'] = 127
        write(root / 'stimulus.json', dict(operations=changed_ops))
        bad = dict(result, stimulus=ref(root / 'stimulus.json'))
        rebound = dict(sim, stimulus=bad['stimulus'])
        invoke(save(s=rebound, r=bad), 'stimulus differs')
        write(root / 'stimulus.json', dict(operations=ops))
        bad = copy.deepcopy(rows)
        bad[2]['input']['data'] = 1
        invoke(save(trace=dict(rows=bad)), 'trace cycle/input differs')
        bad = copy.deepcopy(rows)
        bad[2]['cycle'] = 1
        invoke(save(trace=dict(rows=bad)), 'trace cycle/input differs')
        invoke(save(trace=dict(rows=rows + [rows[-1]])), 'trace suffix')
        # Wrong observed behavior remains genuine readable failed evidence;
        # no performance values escape, including when the process completed.
        bad = copy.deepcopy(rows)
        bad[2]['after'] ^= 1
        failure = invoke(save(trace=dict(rows=bad)))
        assert failure['outcome']['functional'] == 'fail'
        assert all(m['value'] is None for m in failure['metrics'])
        bad = copy.deepcopy(rows)
        bad[3]['before'] ^= 1
        failure = invoke(save(trace=dict(rows=bad)))
        assert failure['outcome']['functional'] == 'fail', 'before-edge latency/hold evidence matters'
        # Unspecified write output may be a concrete vendor value; never count
        # it as a verified defined observation or compare it against invented zero.
        bad = copy.deepcopy(rows)
        bad[1]['after'] = 31415
        assert invoke(save(trace=dict(rows=bad)))['outcome']['functional'] == 'pass'
        bad[1]['after'] = 65536
        invoke(save(trace=dict(rows=bad)), 'integer bounds')
        invoke(save(trace=dict(rows=rows[:-1])), 'incomplete trace')
        partial_counts = dict(cycles=0, reads=0, writes=0, defined_observations=0, read_latency=None, operations=0)
        for status in ['failed', 'partial', 'interrupted']:
            partial = invoke(save(s=dict(sim, status=status), r=dict(result, counters=partial_counts), trace=dict(rows=rows[:1])))
            assert partial['outcome']['status'] == status
            assert partial['outcome']['performance'] == 'unavailable'
            assert all(m['value'] is None for m in partial['metrics'])
        # A complete trace does not turn a producer-recorded failure into pass.
        failed = invoke(save(s=dict(sim, status='failed'), trace=dict(rows=rows)))
        assert failed['outcome']['functional'] == 'unavailable'
        assert failed['outcome']['performance'] == 'unavailable'
        save(trace=dict(rows=rows))
        # Stale captured generation, even a semantically harmless raw whitespace change.
        good = invoke(path)
        (root / 'result.json').write_bytes((root / 'result.json').read_bytes() + b'\n')
        invoke(path, 'stale workload', digest=good['source_digest'])
        save()
        baseline = path.read_bytes()
        for suffix, error in [(b' trailing', 'malformed'), (b' ', None)]:
            path.write_bytes(baseline + suffix)
            if error:
                invoke(path, 'JSON')
            else:
                invoke(path)
        path.write_bytes(baseline.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1))
        invoke(path, 'duplicate')
        path.write_bytes(baseline.replace(b'"schema_version":1', b'"schema_version":NaN', 1))
        invoke(path, 'JSON')
        path.write_bytes(baseline.replace(b'"schema_version":1', b'"schema_version":1.0', 1))
        invoke(path, 'unsupported contract/version')
        path.write_bytes(baseline.replace(b'"seed":42', b'"seed":42,"s\\u0065ed":42', 1))
        invoke(path, 'duplicate')
        path.write_bytes(baseline.replace(b'"seed":42', b'"seed":1e9999', 1))
        invoke(path, 'nonfinite')
        path.write_bytes(baseline)
        # No leaf or ancestor symlink admission, FIFO blocking or excessive allocation.
        target = root / 'trace.json'
        original_trace = target.read_bytes()
        target.unlink()
        os.mkfifo(target)
        invoke(path, 'regular file')
        target.unlink()
        outside = root / 'outside.json'
        outside.write_bytes(original_trace)
        target.symlink_to(outside)
        invoke(path, 'retained_open')
        target.unlink()
        target.write_bytes(b' ' * (1024 * 1024 + 1))
        invoke(path, 'byte bound')
        target.write_bytes(original_trace)
        # A symlinked member ancestor also fails admission (not just a leaf).
        (root / 'linked').symlink_to(root, target_is_directory=True)
        bad = copy.deepcopy(result)
        bad['stimulus'] = dict(sim['stimulus'], path='linked/stimulus.json')
        invoke(save(r=bad), 'retained_open')
        bad = copy.deepcopy(result)
        bad['stimulus'] = dict(sim['stimulus'], path='../stimulus.json')
        invoke(save(r=bad), 'unsafe member')
        save()
        (root / 'rtl.txt').write_bytes(b'wrong RTL')
        invoke(path, 'source reference mismatch')
        bad = copy.deepcopy(sim)
        bad['artifacts'][0]['source'] = ref(root / 'rtl.txt')
        invoke(save(s=bad), 'RTL artifacts differ')
    print(f'workload_result: {checks} public API checks passed')


if __name__ == '__main__':
    main()
