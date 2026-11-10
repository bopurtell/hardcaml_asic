"""Independent final-fence closure-membership characterization; no production edits."""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'workload_comparison'))
from check import fixture, sha, write
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture as coarse_fixture


def main():
    ap = argparse.ArgumentParser()
    for key in ['probe', 'variant', 'fence']:
        ap.add_argument('--' + key, type=Path, required=True)
    a = ap.parse_args()
    probe, variant, fence = (getattr(a, k).resolve() for k in ['probe', 'variant', 'fence'])
    v = json.loads(subprocess.check_output([str(variant), 'variant']))
    with tempfile.TemporaryDirectory(prefix='pt8-discovery-review-') as tmp:
        root = Path(tmp)
        left, lw, ls, _ = fixture(root / 'left', v, 'flops')
        right, rw, _, _ = fixture(root / 'right', v, 'sram')
        coarse = root / 'coarse'
        coarse.mkdir()
        coarse_fixture(coarse)
        (coarse / 'project/top.v').write_bytes((left / 'top.v').read_bytes())
        (coarse / 'project/manifest.json').write_bytes((left / 'manifest.json').read_bytes())
        record_path = coarse / 'run.json'
        record = json.loads(record_path.read_bytes())
        record.update(build_identity=ls['bundle_identity'], manifest_sha256=sha((left / 'manifest.json').read_bytes()))
        write(record_path, record)
        command = list(map(str, [probe, 'coarse_run', coarse, 'bundle', right, lw, rw]))

        def clean():
            p = subprocess.run(command, capture_output=True, text=True, timeout=60)
            assert p.returncode == 0, p.stderr
            return json.loads(p.stdout)

        baseline = clean()
        assert baseline['left']['outcomes']['postchecks'] == []
        receipts = []
        for preexisting_directory in [False, True]:
            checks = coarse / 'checks'
            if preexisting_directory:
                checks.mkdir()
            nr, nw = os.pipe()
            rr, rwfd = os.pipe()
            env = dict(os.environ, LD_PRELOAD=str(fence), PT8_OPEN_COUNT='5',
                       PT8_NOTIFY=str(nw), PT8_RELEASE=str(rr))
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, pass_fds=(nw, rr))
            try:
                assert select.select([nr], [], [], 20)[0], 'late workload boundary not reached'
                assert os.read(nr, 1) == b'R'
                new_check = checks / 'new-failed-postcheck'
                new_check.mkdir(parents=True)
                write(new_check / 'postcheck.json', dict(status='failed', checks=dict(precheck='fail', gate_level='fail')))
                os.write(rwfd, b'R')
                stdout, stderr = p.communicate(timeout=60)
                assert p.returncode == 0, (p.returncode, stderr.decode())
                captured = json.loads(stdout)
                current = clean()
                assert captured['left']['outcomes']['postchecks'] == []
                assert current['left']['outcomes']['postchecks'][0]['status'] == 'failed'
                assert captured['comparison_digest'] != current['comparison_digest']
                receipts.append(dict(preexisting_checks_directory=preexisting_directory,
                                     mutation_open=5, exit_code=p.returncode,
                                     returned_postchecks=captured['left']['outcomes']['postchecks'],
                                     current_postchecks=current['left']['outcomes']['postchecks'],
                                     stale_digest=captured['comparison_digest'], current_digest=current['comparison_digest']))
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                for fd in [nr, nw, rr, rwfd]:
                    os.close(fd)
                shutil.rmtree(checks)
        print(json.dumps(dict(finding='F2_unfenced_discovery_membership', receipts=receipts), indent=2))


if __name__ == '__main__':
    main()
