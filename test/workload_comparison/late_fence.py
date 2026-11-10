"""B regression for independent F1: mutate AFTER the relevant deep replay.

The independent workload_comparison_review probes remain untouched. This adds
rejection assertions for their exact open7/open5 phases and further source types.
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile

from check import fixture, sha, write
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture as coarse_fixture
from check_stage_report import fixture as checkpoint_fixture, execution


def main():
    ap = argparse.ArgumentParser()
    for key in ['consumer', 'variant', 'fence']:
        ap.add_argument('--' + key, type=Path, required=True)
    a = ap.parse_args()
    consumer, variant, fence = [getattr(a, k).resolve() for k in ['consumer', 'variant', 'fence']]
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    v = json.loads(subprocess.check_output([str(variant), 'variant'], env=env))
    checks = 0

    def run(command):
        nonlocal checks
        p = subprocess.run(list(map(str, command)), env=env, capture_output=True, timeout=30)
        assert p.returncode == 0, (p.stdout, p.stderr)
        checks += 1
        return json.loads(p.stdout)

    def late_action(command, count, mutate, label, expected):
        nonlocal checks
        nr, nw = os.pipe()
        rr, rw = os.pipe()
        process_env = dict(env, LD_PRELOAD=str(fence), PT8_OPEN_COUNT=str(count),
                           PT8_NOTIFY=str(nw), PT8_RELEASE=str(rr))
        p = subprocess.Popen(list(map(str, command)), env=process_env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, pass_fds=(nw, rr))
        try:
            assert select.select([nr], [], [], 15)[0], 'late boundary not reached'
            assert os.read(nr, 1) == b'R'
            mutate()
            os.write(rw, b'R')
            stdout, stderr = p.communicate(timeout=30)
            assert p.returncode == 2 and not stdout and expected in stderr, (label, p.returncode, stdout, stderr)
            checks += 1
            print(json.dumps(dict(case=label, open=count, exit=p.returncode, error=stderr.decode().strip())))
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            for fd in [nr, nw, rr, rw]:
                os.close(fd)

    def late_change(command, count, source, append=True):
        nonlocal checks
        original = source.read_bytes()
        nr, nw = os.pipe()
        rr, rw = os.pipe()
        process_env = dict(env, LD_PRELOAD=str(fence), PT8_OPEN_COUNT=str(count),
                           PT8_NOTIFY=str(nw), PT8_RELEASE=str(rr))
        p = subprocess.Popen(list(map(str, command)), env=process_env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, pass_fds=(nw, rr))
        try:
            assert select.select([nr], [], [], 15)[0], 'deep replay boundary not reached'
            assert os.read(nr, 1) == b'R'
            changed = original + b'\n' if append else bytes([original[0] ^ 1]) + original[1:]
            source.write_bytes(changed)
            os.write(rw, b'R')
            stdout, stderr = p.communicate(timeout=30)
            assert p.returncode == 2 and not stdout, (source, p.returncode, stdout, stderr)
            assert (b'final raw source' in stderr or b'byte bound exceeded' in stderr), (source, stderr)
            checks += 1
            print(json.dumps(dict(source=str(source.relative_to(root)), open=count,
                                  before_sha256=sha(original), changed_sha256=sha(changed),
                                  exit=p.returncode, error=stderr.decode().strip())))
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            for fd in [nr, nw, rr, rw]:
                os.close(fd)
            source.write_bytes(original)

    with tempfile.TemporaryDirectory(prefix='pt8-B-final-fence-') as tmp:
        root = Path(tmp)
        l, lw, ls, lr = fixture(root / 'left', v, 'flops')
        r, rw, rs, rr = fixture(root / 'right', v, 'sram')
        command = [consumer, l, r, lw, rw]
        run(command)
        # Opens1/2 left snapshot,3/4 right snapshot,5/6 left recheck,7/8 right
        # recheck, exactly as in A's independent original reproduction.
        for name in ['result.json', 'simulation.json', 'stimulus.json', 'trace.json',
                     'rtl.txt', 'model.txt', 'testbench.txt', 'tool.txt']:
            late_change(command, 7, lw.parent / name)
        late_change(command, 7, lw, append=False)
        late_change(command, 7, l / 'top.v')
        late_change(command, 7, l / 'manifest.json')
        coarse = root / 'coarse'
        coarse.mkdir()
        coarse_fixture(coarse)
        (coarse / 'project/top.v').write_bytes((l / 'top.v').read_bytes())
        (coarse / 'project/manifest.json').write_bytes((l / 'manifest.json').read_bytes())
        run_path = coarse / 'run.json'
        record = json.loads(run_path.read_bytes())
        record.update(build_identity=ls['bundle_identity'], manifest_sha256=sha((l / 'manifest.json').read_bytes()))
        write(run_path, record)
        joined = [consumer, 'targets', 'coarse_run', coarse, 'bundle', r, lw, rw]
        run(joined)
        late_change(joined, 5, run_path)
        stored = json.loads(subprocess.check_output([str(consumer), 'report', str(coarse)], env=env))
        write(coarse / 'results.json', stored)
        run(joined)
        late_change(joined, 7, coarse / 'results.json')
        # The workload's bundle is a different retained copy from coarse/project.
        late_change(joined, 7, l / 'top.v')
        (coarse / 'results.json').unlink()
        checks_root = coarse / 'checks'
        for initial in ['absent', 'empty', 'child-without-postcheck']:
            if initial != 'absent':
                checks_root.mkdir()
            if initial == 'child-without-postcheck':
                (checks_root / 'selected-child').mkdir()
            run(joined)

            def add_postcheck():
                child = checks_root / ('selected-child' if initial == 'child-without-postcheck' else 'new-failed-postcheck')
                child.mkdir(parents=True, exist_ok=True)
                write(child / 'postcheck.json', dict(status='failed', checks=dict(precheck='fail', gate_level='fail')))

            late_action(joined, 5, add_postcheck, 'postcheck-discovery-' + initial, b'postcheck discovery generation changed')
            current = run(joined)
            assert current['left']['outcomes']['postchecks'][0]['status'] == 'failed'
            shutil.rmtree(checks_root)
        late_action(joined, 5, checks_root.mkdir, 'initially-absent-empty-checks-created', b'postcheck discovery generation changed')
        checks_root.rmdir()
        (checks_root / 'existing').mkdir(parents=True)
        write(checks_root / 'existing/postcheck.json', dict(status='failed', checks=dict(precheck='fail', gate_level='fail')))
        run(joined)
        late_action(joined, 5, lambda: shutil.rmtree(checks_root), 'existing-postcheck-directory-removed', b'postcheck discovery generation changed')

        # The old readers admit this internal flow alias. Pin its canonical
        # resolution, including when a late retarget has byte-identical sources.
        flow = coarse / record['outputs']['flow']
        actual = flow.with_name('asic-real')
        alternate = flow.with_name('asic-alternate')
        flow.rename(actual)
        shutil.copytree(actual, alternate)
        flow.symlink_to(actual.name, target_is_directory=True)
        run(joined)

        def retarget_flow():
            flow.unlink()
            flow.symlink_to(alternate.name, target_is_directory=True)

        late_action(joined, 5, retarget_flow, 'flow-alias-retarget-identical-bytes', b'final raw source alias changed')
        checks_root.mkdir()
        for i in range(8193):
            (checks_root / str(i)).touch()
        limited = subprocess.run(list(map(str, joined)), env=env, capture_output=True, timeout=30)
        assert limited.returncode == 2 and not limited.stdout and b'check directory count exceeds 8192' in limited.stderr
        checks += 1
        shutil.rmtree(checks_root)
        stage = root / 'stage'
        stage.mkdir()
        checkpoint = checkpoint_fixture(stage)
        selected = stage / 'execution.json'
        write(selected, execution([checkpoint]))
        # Only the right workload is attached: its final replay starts at open3.
        stage_command = [consumer, 'targets', 'execution', selected, 'bundle', r, '-', rw]
        run(stage_command)
        for source in [selected, stage / 'synthesis/manifest.json', stage / 'synthesis/segment.json',
                       stage / 'synthesis/state.json', stage / 'synthesis/views/nl', stage / 'host']:
            late_change(stage_command, 3, source)
    print(f'late final raw fence: {checks} baseline/rejection checks passed')


if __name__ == '__main__':
    main()
