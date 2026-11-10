#!/usr/bin/env python3
"""Coordinator's independent retained-example audit (no simulation/tool launch)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--example', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    root = a.example.resolve()
    evidence = root / 'acceptance/pt8-workloads'
    receipt = load(evidence / 'final-reviewed-receipt.json')
    api = root / '_build_pt8_workloads/default/workloads/workload_public.exe'
    assert sha(api) == receipt['binaries'][str(api)]
    for name, values in receipt['provider_sources'].items():
        assert sha(root.parent / 'hardcaml_asic' / name) == values['captured_sha256']
    assert receipt['reader_provider_generation'] != receipt['simulation_producer_generation']
    checks = {}
    paths = {}
    for label in ('flops', 'exact', 'ignored-workload', 'flops-ignored'):
        d = evidence / 'simulation-04' / label
        result = load(d / 'result.json')
        sim = load(d / 'simulation.json')
        w = result['workload']
        trace = load(d / 'trace.json')['rows']
        for ref in [result[k] for k in ('simulation', 'stimulus', 'trace')] + [x['source'] for x in sim['artifacts']]:
            assert sha(d / ref['path']) == ref['sha256']
            assert (d / ref['path']).stat().st_size == ref['bytes']
        raw = (d / 'execute/stdout.log').read_text().splitlines()
        raw = [line.split()[1:] for line in raw if line.startswith('TRACE ')]
        assert len(raw) == len(trace) == len(w['operations']) == 1307
        steps = re.findall(r"step\((\d+), 1'b([01]), 1'b([01]), 8'd(\d+), 8'd(\d+)\);", (d / 'testbench.v').read_text())
        assert len(steps) == len(trace)
        memory, output = {}, None
        counters = dict(cycles=0, reads=0, writes=0, operations=0,
                        defined_observations=0, read_latency=None)
        mismatches = 0
        addresses = set()
        for i, (op, row, words, step) in enumerate(zip(w['operations'], trace, raw, steps)):
            assert row['cycle'] == i == int(words[0]) == int(step[0])
            assert row['input'] == op
            assert (bool(int(step[1])), bool(int(step[2])), int(step[3])) == (op['enable'], op['write'], op['address'])
            assert int(step[4]) == (0 if label == 'ignored-workload' else op['data'] & 255)
            for phase, word in zip(('before', 'after'), words[1:]):
                assert row[phase] == (None if re.search('[xz]', word.lower()) else int(word, 16))
            before = output
            if op['enable']:
                if op['write']:
                    memory[op['address']] = op['data']
                    output = None
                else:
                    output = memory.get(op['address'])
                    addresses.add(op['address'])
            assert w['expected'][i] == dict(before=before, after=output)
            mismatches += sum(expected is not None and row[phase] != expected
                              for phase, expected in (('before', before), ('after', output)))
            if w['window']['start'] <= i < w['window']['stop']:
                counters['cycles'] += 1
                counters['defined_observations'] += (before is not None) + (output is not None)
                if op['enable']:
                    counters['operations'] += 1
                    counters['writes' if op['write'] else 'reads'] += 1
                    if not op['write'] and output is not None:
                        counters['read_latency'] = 1
        assert len(addresses) == 256
        assert counters == result['counters'] == dict(cycles=1307, reads=525, writes=262,
            operations=787, defined_observations=2083, read_latency=1)
        assert (mismatches == 0) == (label in ('flops', 'exact'))
        compile_record = load(d / 'compile/command.json')
        execute_record = load(d / 'execute/command.json')
        assert compile_record['exit_code'] == execute_record['exit_code'] == 0
        assert sha(d / 'execute/stdout.log') == execute_record['stdout_sha256']
        assert '-DFUNCTIONAL' in compile_record['argv']
        manifest = load(Path(result['bundle']) / 'manifest.json')
        for source in manifest['synthesis_sources']:
            assert sha(Path(result['bundle']) / source) == sha(d / Path(source).name)
        if label == 'exact':
            rtl = (d / 'tt_um_pt8_workload.v').read_text()
            assert 'hc_resource_7_program' in rtl
            for name, model in receipt['stock_vendor_models'].items():
                local = d / Path(name).name
                assert sha(local) == sha(Path(name)) == model['sha256']
                assert str(local) in compile_record['argv']
        query = subprocess.run([str(api), 'inspect', str(d / 'result.json')],
                               capture_output=True, timeout=180)
        assert query.returncode == 0, query.stderr
        projection = json.loads(query.stdout)
        assert projection['outcome']['mismatched_observations'] == mismatches
        (a.output / f'{label}-inspection.json').write_bytes(query.stdout)
        checks[label] = dict(counters=counters, mismatched_observations=mismatches,
                             raw_stdout_sha256=sha(d / 'execute/stdout.log'))
        paths[label] = d / 'result.json'
    left, right = (load(paths[k])['bundle'] for k in ('flops', 'exact'))
    query = subprocess.run([str(api), 'compare', left, right, str(paths['flops']), str(paths['exact'])],
                           capture_output=True, timeout=180)
    assert query.returncode == 0, query.stderr
    comparison = json.loads(query.stdout)
    metrics = [m for m in comparison['metrics'] if m['metric_id'].startswith('workload/')]
    assert len(metrics) == 7
    assert all(m['comparison'] == 'comparable' and m['right_minus_left'] == 0 for m in metrics)
    (a.output / 'comparison.json').write_bytes(query.stdout)
    (a.output / 'review.json').write_text(json.dumps(dict(pass_=True, reviewer='coordinator',
        script_sha256=sha(Path(__file__)), checks=checks,
        receipt_sha256=sha(evidence / 'final-reviewed-receipt.json'),
        comparison_digest=comparison['comparison_digest'],
        new_simulations=0, eda=0), indent=2) + '\n')
    print('Independent raw-trace/bench/oracle/artifact/public-reader audit passed: four executions, seven zero deltas.')


if __name__ == '__main__':
    main()
