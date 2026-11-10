"""Independent flow/discovery alias compatibility, retarget and enumeration bounds."""
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
    receipts = []
    with tempfile.TemporaryDirectory(prefix='pt8-independent-alias-') as tmp:
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

        def query(error=None):
            p = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert p.returncode == (2 if error else 0), (p.returncode, p.stdout, p.stderr)
            if error:
                assert error in p.stderr and not p.stdout, p.stderr
                return p.stderr.strip()
            result = json.loads(p.stdout)
            assert all(m['comparison'] == 'comparable' for m in result['metrics'] if m['metric_id'].startswith('workload/'))
            return result

        query()

        def late_retarget(alias, target, error):
            nr, nw = os.pipe()
            rr, rwfd = os.pipe()
            env = dict(os.environ, LD_PRELOAD=str(fence), PT8_OPEN_COUNT='5', PT8_NOTIFY=str(nw), PT8_RELEASE=str(rr))
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, pass_fds=(nw, rr))
            try:
                assert select.select([nr], [], [], 20)[0]
                assert os.read(nr, 1) == b'R'
                alias.unlink()
                alias.symlink_to(target.name, target_is_directory=True)
                os.write(rwfd, b'R')
                stdout, stderr = p.communicate(timeout=30)
                assert p.returncode == 2 and not stdout and error.encode() in stderr, (p.returncode, stdout, stderr)
                receipts.append(dict(probe='identical-byte-alias-retarget', alias=alias.name, exit=2, error=stderr.decode().strip()))
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                for fd in [nr, nw, rr, rwfd]:
                    os.close(fd)

        flow = coarse / record['outputs']['flow']
        actual, alternate = flow.with_name('real-flow'), flow.with_name('alternate-flow')
        flow.rename(actual)
        shutil.copytree(actual, alternate)
        flow.symlink_to(actual.name, target_is_directory=True)
        query()
        receipts.append(dict(probe='stable-flow-alias', result='passed'))
        late_retarget(flow, alternate, 'final raw source alias changed')
        query()
        checks = coarse / 'checks'
        actual_checks, alternate_checks = coarse / 'checks-real', coarse / 'checks-alternate'
        actual_checks.mkdir()
        alternate_checks.mkdir()
        checks.symlink_to(actual_checks.name, target_is_directory=True)
        query()
        receipts.append(dict(probe='stable-checks-alias', result='passed'))
        late_retarget(checks, alternate_checks, 'postcheck discovery generation changed')
        query()
        checks.unlink()
        # opendir must reject a FIFO instead of waiting for a writer; child FIFOs
        # are only metadata probes, never opened as streams.
        os.mkfifo(checks)
        query()
        receipts.append(dict(probe='checks-fifo', result='nonblocking historical no-postchecks semantics'))
        checks.unlink()
        checks.mkdir()
        os.mkfifo(checks / 'child-fifo')
        error = query('Not a directory')
        receipts.append(dict(probe='checks-child-fifo', result='nonblocking rejection', error=error))
        (checks / 'child-fifo').unlink()
        for i in range(4096):
            (checks / str(i)).mkdir()
        query()
        receipts.append(dict(probe='4096-discovery-members', result='passed'))
        for i in range(4096, 8193):
            (checks / str(i)).mkdir()
        error = query('check directory count exceeds 8192')
        receipts.append(dict(probe='8193-discovery-members', exit=2, error=error))
    print(json.dumps(dict(receipts=receipts), indent=2))


if __name__ == '__main__':
    main()
