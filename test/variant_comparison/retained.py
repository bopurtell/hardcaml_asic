"""Opt-in public retained-source acceptance. Reads existing evidence; no EDA.

Supply the original named execution paths (named relocation is unsupported), and
the existing all-corner synthesis/full coarse run directories. No stores/providers
are created or selected. This is a consumer assertion script, not a report parser.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def snapshot(roots):
    files = set()
    for root in roots:
        files.update(p for p in root.rglob('*') if p.is_file())
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size,
                     p.stat().st_mtime_ns) for p in files}


def main():
    p = argparse.ArgumentParser()
    for key in ['consumer', 'cli', 'area', 'delay', 'synthesis', 'physical']:
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--check-declared-inputs', action='store_true')
    a = p.parse_args()
    env = dict(os.environ, PATH='/no-tools', PYTHONPATH='/no-catalog', PYTHONDONTWRITEBYTECODE='1')
    roots = [a.area.parent, a.delay.parent, a.synthesis, a.physical]
    before = snapshot(roots)

    def compare(axis, kind, left, right):
        api = [a.consumer.resolve(), axis, kind, left.resolve(), kind, right.resolve()]
        cli = [a.cli.resolve(), 'compare', '--axis', axis, '--left-kind', kind,
               '--left', left.resolve(), '--right-kind', kind, '--right', right.resolve(), '--json']
        values = []
        for args in [api, cli]:
            result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                                    env=env, timeout=120)
            assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
            assert len(result.stdout.encode()) <= 1024 * 1024
            values.append(json.loads(result.stdout))
        assert values[0] == values[1], 'API/CLI parity'
        return values[0]

    named = compare('flow_configuration', 'execution', a.area, a.delay)
    for key in ['attempt_id', 'bundle_identity', 'publications']:
        assert named['left']['identities'][key] != named['right']['identities'][key], key
    left = named['left']['outputs']['mapped_netlist']
    right = named['right']['outputs']['mapped_netlist']
    assert left['sha256'] == right['sha256'] and left['path'] != right['path']
    assert all(m['right_minus_left'] is None for m in named['metrics'])
    assert named['left']['hardware_metadata_status'] == 'unavailable'
    physical = compare('descriptive', 'coarse_run', a.physical, a.physical)
    native = [m for m in physical['metrics'] if m['metric_id'].startswith('native/')]
    assert len(native) == 18
    assert all(m['comparison'] == 'comparable' and m['right_minus_left'] == 0 for m in native)
    outcomes = physical['left']['outcomes']['timing']
    assert outcomes['all_corner_timing_goal'] == 'pass'
    assert outcomes['exhaustive_coverage'] == 'unavailable'
    assert len(outcomes['corners']) == 3
    assert all(c['electrical_goal'] == 'pass' for c in outcomes['corners'])
    cross = compare('descriptive', 'coarse_run', a.synthesis, a.physical)
    native = [m for m in cross['metrics'] if m['metric_id'].startswith('native/')]
    assert len(native) == 18
    assert all(m['comparison'] == 'confounded' and m['right_minus_left'] is None for m in native)
    assert all('stage: differs' in m['reasons'] and 'rc: differs' in m['reasons'] for m in native)
    if a.check_declared_inputs:
        with tempfile.TemporaryDirectory(prefix='variant-physical-input-') as tmp:
            copied = Path(tmp) / 'run'
            shutil.copytree(a.physical, copied, symlinks=True)
            original = compare('descriptive', 'coarse_run', copied, copied)
            native_copy = [m for m in original['metrics'] if m['metric_id'].startswith('native/')]
            assert len(native_copy) == 18 and all(m['comparison'] == 'comparable' for m in native_copy)
            manifest = json.loads((copied / 'project/manifest.json').read_text())
            member = copied / 'project' / manifest['source_inputs'][0]['copy']
            data = member.read_bytes()
            for action in ['corrupt', 'missing']:
                if action == 'corrupt':
                    member.write_bytes(data + b'\ncorrupted declared source\n')
                else:
                    member.unlink()
                for args in [[a.consumer.resolve(), 'descriptive', 'coarse_run', copied, 'coarse_run', copied],
                             [a.cli.resolve(), 'compare', '--axis', 'descriptive', '--left-kind', 'coarse_run',
                              '--left', copied, '--right-kind', 'coarse_run', '--right', copied, '--json']]:
                    r = subprocess.run(list(map(str, args)), capture_output=True, text=True, env=env, timeout=120)
                    assert r.returncode == 2 and not r.stdout and r.stderr, (action, r.returncode, r.stdout, r.stderr)
    assert before == snapshot(roots), 'retained source bytes/sizes/mtimes changed'
    print(json.dumps(dict(status='passed', preserved_files=len(before), named_metrics=len(named['metrics']),
                          physical_comparable_native_metrics=18, cross_stage_confounded_native_metrics=18,
                          corrupted_missing_physical_declared_sources_rejected=a.check_declared_inputs,
                          comparison_digests=[j['comparison_digest'] for j in [named, physical, cross]])))


if __name__ == '__main__':
    main()
