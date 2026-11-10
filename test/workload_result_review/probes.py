"""Independent adversarial review. Disposable synthetic evidence, no EDA.

Does not import A's fixtures or tests. Expected admissions explicitly separate
wire correctness from semantic-use/authentication limitations.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import tempfile


def encode(j, canonical=False):
    return json.dumps(j, sort_keys=canonical, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(p, j):
    p.write_bytes(encode(j))


def ref(p):
    data = p.read_bytes()
    return dict(path=p.name, sha256=sha(data), bytes=len(data))


def counters(rows, window, expected):
    selected = [r for r in rows if window['start'] <= r['cycle'] < window['stop']]
    reads = sum(r['input']['enable'] and not r['input']['write'] for r in selected)
    writes = sum(r['input']['enable'] and r['input']['write'] for r in selected)
    defined = sum(expected[r['cycle']][k] is not None for r in selected for k in ['before', 'after'])
    latency = any(r['input']['enable'] and not r['input']['write'] and
                  expected[r['cycle']]['after'] is not None for r in selected)
    return dict(cycles=len(selected), reads=reads, writes=writes, operations=reads+writes,
                defined_observations=defined, read_latency=1 if latency else None)


def fixture(root, variant):
    bundle = root / 'bundle'
    bundle.mkdir()
    files = []
    for name, data in [('top.v', b'module top; /* synthetic implementation */ endmodule\n'),
                       ('behavioral.v', b'module top; /* synthetic behavioral */ endmodule\n')]:
        (bundle / name).write_bytes(data)
        files.append(dict(path=name, sha256=sha(data)))
    manifest = dict(schema_version=1, adapter='LibreLane', project='independent-review', top_module='top',
                    resources=[], files=files, synthesis_sources=['top.v'], simulation_sources=['behavioral.v'],
                    source_inputs=[], hardware_variant=variant)
    manifest = dict(identity=sha(encode(manifest)), **manifest)
    write(bundle / 'manifest.json', manifest)
    artifacts = []
    for role, data in [('rtl', (bundle / 'top.v').read_bytes()), ('model', b'review-functional-model'),
                       ('testbench', b'review-testbench'), ('tool', b'review-tool-settings')]:
        p = root / (role + '.txt')
        p.write_bytes(data)
        artifacts.append(dict(role=role, source=ref(p)))
    def op(address, data=0, write_=False, enable=True):
        return dict(enable=enable, write=write_, address=address, data=data)
    ops = [op(255)]
    for address in [0, 127, 128, 255]:
        ops.extend([op(address, 257 * address ^ 0xa55a, True), op(address)])
    ops.extend([op(255, 1, True, False), op(255), op(0), op(128), op(127), op(0, enable=False)])
    memory, output, expected, rows = {}, None, [], []
    for i, operation in enumerate(ops):
        before = output
        if operation['enable']:
            if operation['write']:
                memory[operation['address']] = operation['data']
                output = None
            else:
                output = memory.get(operation['address'])
        expected.append(dict(before=before, after=output))
        rows.append(dict(cycle=i, input=operation, **expected[-1]))
    workload = dict(protocol='memory-1rw-latency1-v1', seed=4294967295, depth=256, width=16,
                    window=dict(start=2, stop=len(ops)-1), operations=ops, expected=expected)
    workload['identity'] = sha(encode(workload, True))
    write(root / 'stimulus.json', dict(operations=ops))
    write(root / 'trace.json', dict(rows=rows))
    sim = dict(contract='hardcaml-asic.workload-simulation', schema_version=1, attempt_id='independent-001',
               status='completed', bundle_identity=manifest['identity'], hardware_variant_identity=variant['identity'],
               workload_identity=workload['identity'], stimulus=ref(root / 'stimulus.json'), trace=ref(root / 'trace.json'),
               rtl_mode='implementation', artifacts=artifacts, adapter='memory-1rw-latency1-v1',
               clock='posedge-single-clock-v1', model='review-functional-interface-v1', measurement='memory-1rw-window-v1')
    result = dict(contract='hardcaml-asic.workload-result', schema_version=1, bundle=str(bundle), workload=workload,
                  stimulus=sim['stimulus'], trace=sim['trace'], counters=counters(rows, workload['window'], expected))
    return manifest, sim, result, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', type=Path, required=True)
    ap.add_argument('--fence', type=Path, required=True)
    a = ap.parse_args()
    api, fence = a.api.resolve(), a.fence.resolve()
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    receipts = []

    def call(path, digest=None):
        args = [str(api), str(path)] + ([digest] if digest else [])
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)

    v = json.loads(subprocess.check_output([str(api), 'variant', '256'], env=env))
    v128 = json.loads(subprocess.check_output([str(api), 'variant', '128'], env=env))
    with tempfile.TemporaryDirectory(prefix='pt8-a-independent-') as tmp:
        root = Path(tmp)
        manifest, sim, result, rows = fixture(root, v)
        path = root / 'result.json'

        def save(s=None, r=None, trace=None):
            s, r = copy.deepcopy(sim if s is None else s), copy.deepcopy(result if r is None else r)
            if trace is not None:
                write(root / 'trace.json', dict(rows=trace))
            r['trace'] = s['trace'] = ref(root / 'trace.json')
            write(root / 'simulation.json', s)
            r['simulation'] = ref(root / 'simulation.json')
            write(path, r)

        def probe(name, code=2, error=None, outcome=None, digest=None, selected=None):
            p = call(selected or path, digest)
            assert p.returncode == code, (name, p.returncode, p.stdout, p.stderr)
            j = json.loads(p.stdout) if code == 0 else None
            if error:
                assert error in p.stderr, (name, p.stderr)
            if outcome:
                assert j['outcome']['functional'] == outcome, (name, j['outcome'])
                if outcome != 'pass':
                    assert all(m['value'] is None for m in j['metrics'])
            receipts.append(dict(probe=name, exit=p.returncode, result=j['outcome'] if outcome else p.stderr.strip() if code else 'admitted'))
            return j

        save()
        good = probe('baseline_nonzero_half_open_window', 0, outcome='pass')
        assert good['outcome']['recomputed_counters'] == result['counters']
        throughput = good['metrics'][-1]
        assert throughput['numerator'] == result['counters']['operations']
        assert throughput['denominator'] == result['counters']['cycles']
        initial = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}

        def restore():
            for p, data in initial.items():
                if not stat.S_ISREG(p.lstat().st_mode):
                    p.unlink()
                p.write_bytes(data)

        for key in ['bundle_identity', 'hardware_variant_identity', 'workload_identity']:
            save(s=dict(sim, **{key: '0' * 64}))
            probe('wrong_simulation_' + key, error='mismatch')
        save(s=dict(sim, attempt_id=''))
        probe('empty_attempt', error='identifier')
        save(s=dict(sim, attempt_id='a' * 129))
        probe('overlong_attempt', error='identifier')
        save(s=dict(sim, attempt_id='another-attempt'))
        relabeled = probe('rebound_attempt_is_declaration', 0, outcome='pass')
        assert relabeled['binding']['simulation_attempt_id'] == 'another-attempt'
        assert relabeled['source_digest'] != good['source_digest']
        for key in result['counters']:
            bad = copy.deepcopy(result)
            bad['counters'][key] = 2 if key == 'read_latency' else bad['counters'][key] + 1
            save(r=bad)
            probe('coherent_false_counter_' + key, error='stored counters differ')
        for key, value in [('depth', 128), ('width', 8), ('seed', 4294967296), ('protocol', 'memory-other')]:
            bad = copy.deepcopy(result)
            bad['workload'][key] = value
            bad['workload']['identity'] = sha(encode({k: x for k, x in bad['workload'].items() if k != 'identity'}, True))
            save(s=dict(sim, workload_identity=bad['workload']['identity']), r=bad)
            probe('rebound_unsupported_' + key)
        for key in ['clock', 'adapter', 'measurement']:
            save(s=dict(sim, **{key: 'ignored-setting'}))
            probe('ignored_semantic_control_' + key, error='unsupported')
        bad = copy.deepcopy(result)
        bad['workload']['expected'][2]['after'] ^= 1
        bad['workload']['identity'] = sha(encode({k: x for k, x in bad['workload'].items() if k != 'identity'}, True))
        save(s=dict(sim, workload_identity=bad['workload']['identity']), r=bad)
        probe('coherent_false_expected_over_unchanged_trace', error='oracle')
        for name, cycle, key in [('wrong_after', 2, 'after'), ('wrong_before', 3, 'before'),
                                 ('wrong_before_outside_window', len(rows)-1, 'before')]:
            changed = copy.deepcopy(rows)
            changed[cycle][key] ^= 1
            save(trace=changed)
            probe(name, 0, outcome='fail')
        changed = copy.deepcopy(rows)
        for i in range(1, len(rows)):
            changed[i]['after'] = rows[i-1]['after']
        save(trace=changed)
        probe('one_cycle_late_output', 0, outcome='fail')
        changed = copy.deepcopy(rows)
        changed[1]['after'] = 65535
        save(trace=changed)
        probe('unspecified_write_output_not_fabricated', 0, outcome='pass')
        changed = copy.deepcopy(rows)
        changed[9]['after'] = 1  # disabled write must hold, not clobber memory/output
        save(trace=changed)
        probe('disabled_write_does_not_update_or_output', 0, outcome='fail')
        for name, changed in [('missing_prefix_cycle', rows[1:]), ('duplicate_cycle', rows[:2]+rows[1:]),
                              ('trace_suffix', rows+[rows[-1]])]:
            save(trace=changed)
            probe(name)
        save(trace=rows[:-1])
        probe('completed_cannot_hide_tail_outside_window', error='incomplete trace')
        for status in ['failed', 'partial', 'interrupted']:
            for count in [0, 4, len(rows)]:
                prefix = rows[:count]
                r = dict(result, counters=counters(prefix, result['workload']['window'], result['workload']['expected']))
                save(s=dict(sim, status=status), r=r, trace=prefix)
                j = probe(f'{status}_prefix_{count}', 0, outcome='unavailable')
                assert j['outcome']['status'] == status and j['outcome']['performance'] == 'unavailable'
        restore()
        for name, window, operations, observations in [
                ('defined_hold_without_enabled_read', dict(start=9, stop=10), 0, 2),
                ('undefined_initial_read', dict(start=0, stop=1), 1, 0)]:
            r = copy.deepcopy(result)
            r['workload']['window'] = window
            unsigned = {k: v for k, v in r['workload'].items() if k != 'identity'}
            r['workload']['identity'] = sha(encode(unsigned, True))
            r['counters'] = counters(rows, window, r['workload']['expected'])
            save(s=dict(sim, workload_identity=r['workload']['identity']), r=r)
            j = probe(name, 0, outcome='pass')
            assert j['outcome']['recomputed_counters']['read_latency'] is None
            assert j['outcome']['recomputed_counters']['operations'] == operations
            assert j['outcome']['recomputed_counters']['defined_observations'] == observations
        restore()
        for role in ['rtl', 'model', 'testbench', 'tool']:
            artifacts = [x for x in sim['artifacts'] if x['role'] != role]
            pad = root / ('pad-for-' + role)
            pad.write_bytes(b'padding keeps artifact count valid without the required role')
            artifacts.append(dict(role='model' if role != 'model' else 'tool', source=ref(pad)))
            save(s=dict(sim, artifacts=artifacts))
            probe('missing_role_' + role, error='missing artifact role ' + role)
        save(s=dict(sim, artifacts=sim['artifacts']+[sim['artifacts'][0]]))
        probe('duplicate_rtl_artifact', error='duplicate artifact')
        save(s=dict(sim, rtl_mode='behavioral'))
        probe('behavioral_mode_with_implementation_bytes', error='RTL artifacts differ')
        (root / 'rtl.txt').write_bytes((root / 'bundle/behavioral.v').read_bytes())
        behavioral = copy.deepcopy(sim)
        behavioral['rtl_mode'] = 'behavioral'
        behavioral['artifacts'][0]['source'] = ref(root / 'rtl.txt')
        save(s=behavioral)
        j = probe('behavioral_mode_with_exact_selected_bytes', 0, outcome='pass')
        assert j['controls']['rtl_mode'] == 'behavioral'
        restore()
        save(s=dict(sim, artifacts=sim['artifacts'] * 9))
        probe('artifact_count_36', error='artifact count bounds')
        restore()
        (root / 'model.txt').write_bytes(b'x' * (8*1024*1024+1))
        large = copy.deepcopy(sim)
        large['artifacts'][1]['source'] = ref(root / 'model.txt')
        save(s=large)
        probe('artifact_8mib_plus_one', error='integer bounds')
        restore()
        large = copy.deepcopy(sim)
        for i in range(4):
            p = root / f'large-model-{i}'
            p.write_bytes(b'x' * (8*1024*1024))
            large['artifacts'].append(dict(role='model', source=ref(p)))
        save(s=large)
        probe('combined_sources_over_32mib', error='aggregate source budget')
        restore()
        # Deliberately ignored hardware parameter is not detectable from hashes:
        # rebind a valid depth128 declaration while retaining depth256 trace/RTL.
        rebound_manifest = dict(manifest, hardware_variant=v128)
        rebound_manifest.pop('identity')
        rebound_manifest = dict(identity=sha(encode(rebound_manifest)), **rebound_manifest)
        write(root / 'bundle/manifest.json', rebound_manifest)
        save(s=dict(sim, bundle_identity=rebound_manifest['identity'], hardware_variant_identity=v128['identity']))
        probe('ignored_variant_depth_is_semantic_use_limit', 0, outcome='pass')
        restore()
        # Raw JSON, not decoded/re-encoded authority; bounded and strict.
        baseline = path.read_bytes()
        mutations = [('escaped_duplicate', baseline.replace(b'"schema_version":1', b'"schema_version":1,"schema_versi\\u006fn":1', 1)),
                     ('float_integer', baseline.replace(b'"schema_version":1', b'"schema_version":1.0', 1)),
                     ('nan', baseline.replace(b'"schema_version":1', b'"schema_version":NaN', 1)),
                     ('exponent_overflow', baseline.replace(b'"schema_version":1', b'"schema_version":1e9999', 1)),
                     ('invalid_utf8', baseline.replace(b'completed', b'\xff')),
                     ('deep_nesting', b'[' * 258 + b'0' + b']' * 258),
                     ('raw_1mib_plus_one', b' ' * (1024*1024+1)),
                     ('truncated_json', baseline[:-1])]
        # result has no status field: inject invalid UTF8 into a present string.
        mutations[4] = ('invalid_utf8', baseline.replace(b'memory-1rw-latency1-v1', b'\xff', 1))
        for name, data in mutations:
            path.write_bytes(data)
            probe(name)
        restore()
        for count in [0, 4097]:
            r = copy.deepcopy(result)
            r['workload']['operations'] = [dict(enable=False, write=False, address=0, data=0)] * count
            r['workload']['expected'] = [dict(before=None, after=None)] * count
            r['workload']['identity'] = sha(encode({k: v for k, v in r['workload'].items() if k != 'identity'}, True))
            save(s=dict(sim, workload_identity=r['workload']['identity']), r=r)
            probe('operation_count_' + str(count), error='operation count bounds')
        restore()
        for name in ['simulation', 'stimulus', 'trace']:
            member = root / (name + '.json')
            member.write_bytes(member.read_bytes() + b' ' * (1024*1024))
            if name == 'simulation':
                write(path, dict(result, simulation=ref(member)))
            elif name == 'trace':
                save()
            else:
                save(s=dict(sim, stimulus=ref(member)), r=dict(result, stimulus=ref(member)))
            probe('raw_' + name + '_over_1mib', error='byte bound exceeded')
            restore()
        for member_path in ['../stimulus.json', '/stimulus.json', './stimulus.json', 'a//b', 'a\\b', 'a\x00b', 'a' * 513]:
            changed_ref = dict(result['stimulus'], path=member_path)
            save(s=dict(sim, stimulus=changed_ref), r=dict(result, stimulus=changed_ref))
            probe('unsafe_path_' + repr(member_path[:40]), error='unsafe member path')
        restore()
        (root / 'linked').symlink_to(root, target_is_directory=True)
        changed_ref = dict(result['stimulus'], path='linked/stimulus.json')
        save(s=dict(sim, stimulus=changed_ref), r=dict(result, stimulus=changed_ref))
        probe('reference_symlink_ancestor', error='retained_open')
        restore()
        for kind in ['leaf_symlink', 'fifo', 'directory']:
            target = root / 'stimulus.json'
            original = target.read_bytes()
            target.unlink()
            if kind == 'leaf_symlink':
                (root / 'outside').write_bytes(original)
                target.symlink_to(root / 'outside')
            elif kind == 'fifo':
                os.mkfifo(target)
            else:
                target.mkdir()
            probe('reference_' + kind)
            if kind == 'directory':
                p = subprocess.run([str(api), 'descriptor-probe', str(path), '64'],
                                   env=env, capture_output=True, text=True, timeout=15)
                assert p.returncode == 0, p.stderr
                leak = json.loads(p.stdout)
                assert leak['errors'] == 64
                receipts.append(dict(probe='directory_rejection_descriptor_growth', exit=0, result=leak))
            if kind == 'directory':
                target.rmdir()
            else:
                target.unlink()
            target.write_bytes(original)
        # Probe explicit root and bundle admission boundary separately from refs.
        alias = root / 'selected-parent-link'
        alias.symlink_to(root, target_is_directory=True)
        probe('selected_parent_symlink_is_canonicalized', 0, selected=alias / 'result.json', outcome='pass')
        target = root / 'bundle/top.v'
        data = target.read_bytes()
        target.unlink()
        (root / 'bundle/actual.v').write_bytes(data)
        target.symlink_to('actual.v')
        probe('inherited_bundle_internal_symlink_admitted', 0, outcome='pass')
        target.unlink()
        target.write_bytes(data)
        # Recheck refuses a coherent replacement of an artifact generation.
        (root / 'model.txt').write_bytes(b'new coherent concrete model bytes')
        rebound = copy.deepcopy(sim)
        rebound['artifacts'][1]['source'] = ref(root / 'model.txt')
        save(s=rebound)
        probe('coherent_source_replacement_recheck', digest=good['source_digest'], error='stale workload')
        restore()

        def race(name, member, nth, when, mutate, error=None):
            nr, nw = os.pipe()
            rr, rw = os.pipe()
            race_env = dict(env, LD_PRELOAD=str(fence), REVIEW_MEMBER=member, REVIEW_NTH=str(nth),
                            REVIEW_WHEN=when, REVIEW_NOTIFY=str(nw), REVIEW_RELEASE=str(rr))
            p = subprocess.Popen([str(api), str(path)], env=race_env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, pass_fds=(nw, rr))
            try:
                assert select.select([nr], [], [], 10)[0], (name, 'no rendezvous')
                assert os.read(nr, 1) == b'R'
                mutate()
                os.write(rw, b'R')
                stdout, stderr = p.communicate(timeout=15)
                assert p.returncode == 2 and not stdout, (name, p.returncode, stdout, stderr)
                if error:
                    assert error.encode() in stderr, (name, stderr)
                receipts.append(dict(probe=name, exit=p.returncode, result=stderr.decode().strip()))
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                for fd in [nr, nw, rr, rw]:
                    os.close(fd)
                restore()

        def replace_leaf():
            target = root / 'trace.json'
            (root / 'replacement').write_bytes(target.read_bytes())
            (root / 'replacement').replace(target)

        race('opened_descriptor_replaced_identical_bytes', 'trace.json', 1, 'after', replace_leaf, 'changed during read')

        def swap_fifo():
            target = root / 'trace.json'
            target.unlink()
            os.mkfifo(target)

        race('open_time_fifo_replacement', 'trace.json', 1, 'before', swap_fifo, 'regular file')

        for name in ['result', 'simulation', 'stimulus', 'trace', 'model', 'tool', 'testbench']:
            def coherent_replacement(name=name):
                member = root / (name + ('.txt' if name in ['model', 'tool', 'testbench'] else '.json'))
                member.write_bytes(member.read_bytes() + b'\n')
                if name == 'result':
                    return
                changed_sim, changed_result = copy.deepcopy(sim), copy.deepcopy(result)
                if name in ['stimulus', 'trace']:
                    changed_sim[name] = changed_result[name] = ref(member)
                elif name != 'simulation':
                    for artifact in changed_sim['artifacts']:
                        if artifact['role'] == name:
                            artifact['source'] = ref(member)
                if name != 'simulation':
                    write(root / 'simulation.json', changed_sim)
                changed_result['simulation'] = ref(root / 'simulation.json')
                write(path, changed_result)

            race('between_replays_coherent_' + name, 'result.json', 2, 'before',
                 coherent_replacement, 'source generation changed')

        def coherent_rtl_replacement():
            rtl = (root / 'rtl.txt').read_bytes() + b'// generation change\n'
            (root / 'rtl.txt').write_bytes(rtl)
            (root / 'bundle/top.v').write_bytes(rtl)
            m = copy.deepcopy(manifest)
            m.pop('identity')
            m['files'][0]['sha256'] = sha(rtl)
            m = dict(identity=sha(encode(m)), **m)
            write(root / 'bundle/manifest.json', m)
            s = copy.deepcopy(sim)
            s['bundle_identity'] = m['identity']
            s['artifacts'][0]['source'] = ref(root / 'rtl.txt')
            save(s=s)

        race('between_replays_coherent_rtl_and_bundle', 'result.json', 2, 'before',
             coherent_rtl_replacement, 'source generation changed')

        def raw_manifest_replacement():
            p = root / 'bundle/manifest.json'
            p.write_bytes(p.read_bytes() + b'\n')

        race('between_replays_bundle_raw_manifest_only', 'result.json', 2, 'before',
             raw_manifest_replacement, 'source generation changed')

        print(json.dumps(dict(checks=len(receipts))))
        for receipt in receipts:
            print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
