"""Independent variant comparison review: real readers, disposable source-rebound copies, no EDA.

Does not edit provider/consumer source or retained originals. Public comparisons
run with tool discovery denied. Receipts retain failures rather than treating
the implementation's current behavior as the specification.
"""
import argparse
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / 'test'))
from check_resource_evidence import fixture


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, separators=(',', ':')) + '\n')


def rebind(root, transform):
    path = root / 'project/manifest.json'
    manifest = json.loads(path.read_text())
    manifest.pop('identity')
    transform(manifest)
    identity = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest()
    write(path, dict(identity=identity, **manifest))
    run = json.loads((root / 'run.json').read_text())
    run.update(build_identity=identity, manifest_sha256=sha(path))
    write(root / 'run.json', run)


def snapshot(root):
    return {str(p.relative_to(root)): (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cli', type=Path, required=True)
    ap.add_argument('--consumer', type=Path, required=True)
    ap.add_argument('--physical', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    cli, consumer = a.cli.resolve(), a.consumer.resolve()
    env = dict(os.environ, PATH='/no-tools', PYTHONPATH='/no-catalog', PYTHONDONTWRITEBYTECODE='1')
    receipts = dict(cli_sha256=sha(cli), consumer_sha256=sha(consumer), cases=[])

    def call(name, kind, left, right=None, axis='descriptive'):
        args = [str(cli), 'compare', '--left-kind', kind, '--left', str(left),
                '--right-kind', kind, '--right', str(right or left), '--axis', axis, '--json']
        start = time.monotonic()
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
        case = dict(name=name, code=result.returncode, stderr=result.stderr,
                    seconds=round(time.monotonic()-start, 3), stdout_bytes=len(result.stdout.encode()))
        value = json.loads(result.stdout) if result.stdout else None
        if value:
            case.update(digest=value['comparison_digest'], metrics=[dict(
                id=m['metric_id'], state=m['comparison'], delta=m['right_minus_left'], reasons=m['reasons'])
                for m in value['metrics']])
            case['environment'] = value['left']['controls']['environment']
            case['record_sha256'] = value['left']['identities']['record_sha256']
        receipts['cases'].append(case)
        return case, value

    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='variant-comparison-review-') as temp:
        root = Path(temp)
        run = root / 'coarse'
        run.mkdir()
        fixture(run)
        copied = run / 'project/inputs/config.ml'
        copied.parent.mkdir()
        copied.write_text('let depth = 256\n')
        rebind(run, lambda m: m.update(files=[dict(path='inputs/config.ml', sha256=sha(copied))],
            source_inputs=[dict(path='config.ml', copy='inputs/config.ml', sha256=sha(copied), git_status='')]))
        before = snapshot(run)
        assert call('coarse.valid-bound-input', 'coarse_run', run)[0]['code'] == 0
        assert before == snapshot(run)
        copied.write_text('let depth = 128\n')
        call('coarse.corrupted-declared-input', 'coarse_run', run)
        call('bundle.corrupted-declared-input-control', 'bundle', run/'project')
        copied.unlink()
        call('coarse.missing-declared-input', 'coarse_run', run)
        call('bundle.missing-declared-input-control', 'bundle', run/'project')
        copied.write_text('let depth = 256\n')

        # Report_service's fallback reads outside Resource_evidence's bounded,
        # contained reader. Do not let a FIFO test hang: kill after two seconds.
        fifo = run / 'results.json'
        os.mkfifo(fifo)
        cmd = [str(cli), 'compare', '--left-kind', 'coarse_run', '--left', str(run),
               '--right-kind', 'coarse_run', '--right', str(run), '--axis', 'descriptive', '--json']
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=2)
            receipts['cases'].append(dict(name='coarse.results-fifo-admission', code=result.returncode,
                stderr=result.stderr, timed_out=False))
        except subprocess.TimeoutExpired:
            receipts['cases'].append(dict(name='coarse.results-fifo-admission', timed_out=True))
        fifo.unlink()

        # Stored report bytes are consulted but are not in the resource refs.
        # Change only their raw generation when the opposite target is opened.
        collected = subprocess.run([str(consumer), 'collect', str(run)], env=env,
            capture_output=True, text=True, check=True, timeout=20)
        results = run / 'results.json'
        results.write_text(collected.stdout)
        right = root / 'right'
        right.mkdir()
        fixture(right)
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK)
        assert fd >= 0
        assert libc.inotify_add_watch(fd, os.fsencode(right/'project/manifest.json'), 0x20) >= 0
        changed = threading.Event()
        raw_before = sha(results)
        def change_raw():
            if select.select([fd], [], [], 10)[0]:
                os.read(fd, 65536)
                results.write_text(json.dumps(json.loads(results.read_text()), indent=4) + '\n')
                changed.set()
        worker = threading.Thread(target=change_raw)
        worker.start()
        case, _ = call('coarse.raw-results-generation-race', 'coarse_run', run, right)
        worker.join()
        os.close(fd)
        case.update(mutation_completed=changed.is_set(), old_results_sha256=raw_before,
                    new_results_sha256=sha(results))
        assert changed.is_set() and sha(results) != raw_before

        if a.physical:
            original = a.physical.resolve()
            preserved = snapshot(original)
            physical = root / 'physical'
            shutil.copytree(original, physical, symlinks=True)
            # Retained coarse timing supports historical execution-root relocation;
            # all original native process/closure records remain untouched.
            call('physical.relocated-baseline', 'coarse_run', physical)
            manifest = json.loads((physical/'project/manifest.json').read_text())
            input_ref = manifest['source_inputs'][0]
            member = physical/'project'/input_ref['copy']
            original_member = member.read_bytes()
            member.write_bytes(original_member + b'\n(* independent review changed declared bytes *)\n')
            case, _ = call('physical.corrupted-declared-input', 'coarse_run', physical)
            case['changed_member'] = input_ref['copy']
            call('physical.bundle-corruption-control', 'bundle', physical/'project')
            member.unlink()
            case, _ = call('physical.missing-declared-input', 'coarse_run', physical)
            case['missing_member'] = input_ref['copy']
            member.write_bytes(original_member)
            run_path = physical/'run.json'
            original_record = run_path.read_bytes()
            record = json.loads(original_record)
            record['environment'] = dict(status='unknown', ready=False)
            write(run_path, record)
            case, _ = call('physical.unknown-environment-object', 'coarse_run', physical)
            # Actual validated binding still exists here: report explicitly rather
            # than assert this alone proves an unknown native executable/image.
            case['authoritative_execution_binding_still_present'] = bool(record.get('execution_binding'))
            run_path.write_bytes(original_record)
            # Missing advertised linked source must fail, not become unavailable.
            companion = physical/'timing-intent/analysis.json'
            backup = companion.read_bytes()
            companion.unlink()
            call('physical.missing-required-companion', 'coarse_run', physical)
            companion.write_bytes(backup)
            assert snapshot(original) == preserved
            receipts['original_physical_files_preserved'] = len(preserved)

    a.output.write_text(json.dumps(receipts, indent=2) + '\n')
    print(json.dumps([dict(name=c['name'], code=c.get('code'), timed_out=c.get('timed_out', False),
        comparable=sum(m['state']=='comparable' for m in c.get('metrics', []))) for c in receipts['cases']], indent=2))


if __name__ == '__main__':
    main()
