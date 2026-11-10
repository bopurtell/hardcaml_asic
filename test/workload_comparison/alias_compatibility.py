"""Opt-in diagnostic: does the final fence preserve admitted flow aliases?

No retained evidence edits. Uses a disposable source fixture and public services.
Prints characterization receipts, not an acceptance claim for a failing case.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--consumer', type=Path, required=True)
    a = ap.parse_args()
    consumer = a.consumer.resolve()
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    receipts = []

    def call(name, args):
        p = subprocess.run(list(map(str, [consumer, *args])), env=env,
                           capture_output=True, text=True, timeout=30)
        receipts.append(dict(case=name, exit=p.returncode, error=p.stderr.strip(), stdout_bytes=len(p.stdout)))
        return p

    with tempfile.TemporaryDirectory(prefix='pt8-B-alias-') as tmp:
        root = Path(tmp)
        flow, _, _ = fixture(root)
        command = ['targets', 'coarse_run', root, 'coarse_run', root, '-', '-']
        assert call('canonical_comparison_control', command).returncode == 0
        actual = flow.with_name('asic-real')
        flow.rename(actual)
        flow.symlink_to(actual.name, target_is_directory=True)
        assert call('aliased_flow_public_report', ['report', root]).returncode == 0
        result = call('aliased_flow_comparison', command)
        print(json.dumps(dict(compatible=result.returncode == 0, receipts=receipts), indent=2))


if __name__ == '__main__':
    main()
