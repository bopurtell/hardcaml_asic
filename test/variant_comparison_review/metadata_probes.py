"""Strict optional metadata through the real public archive inspection reader."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'test'))
from check_resource_evidence import fixture


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--consumer', type=Path, required=True)
    ap.add_argument('--review-api', type=Path, required=True)
    ap.add_argument('--cli', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    valid = json.loads(subprocess.check_output([str(a.consumer.resolve()), 'variant'], env=env))
    receipts = []
    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='variant-metadata-review-') as tmp:
        root = Path(tmp)
        fixture(root)
        p = root/'project/manifest.json'
        original = json.loads(p.read_text())
        # All new metadata examples start from the exact public library codec.
        # The outer identity is correctly computed over the last-wins semantic
        # payload, so rejection must come from duplicate syntax admission.
        def bind(raw):
            semantic = json.loads(raw)
            unsigned = {k: v for k, v in semantic.items() if k != 'identity'}
            identity = hashlib.sha256(json.dumps(unsigned, separators=(',', ':')).encode()).hexdigest()
            raw = raw.replace('"identity":"' + original['identity'] + '"', '"identity":"' + identity + '"', 1)
            p.write_text(raw)
            record = json.loads((root/'run.json').read_text())
            record.update(build_identity=identity, manifest_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            (root/'run.json').write_text(json.dumps(record, separators=(',', ':')))
        def call(label):
            for api, cmd in [('archive', [str(a.review_api.resolve()), 'archive-inspect', str(root)]),
                             ('compare', [str(a.cli.resolve()), 'compare', '--left-kind', 'bundle', '--left', str(root/'project'),
                                '--right-kind', 'bundle', '--right', str(root/'project'), '--axis', 'hardware', '--json'])]:
                r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
                receipts.append(dict(case=label, api=api, code=r.returncode, stderr=r.stderr,
                    stdout_bytes=len(r.stdout), manifest_sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
        old_raw = json.dumps(original, separators=(',', ':'))
        bind(old_raw)
        call('legacy-absence')
        base = old_raw[:-1] + ',"hardware_variant":' + json.dumps(valid, separators=(',', ':')) + '}'
        bind(base)
        call('valid-hardware-metadata')
        bind(old_raw[:-1] + ',"hardware_variant":null,"hardware_variant":' + json.dumps(valid, separators=(',', ':')) + '}')
        call('duplicate-optional-field')
        nested = json.dumps(valid, separators=(',', ':')).replace('"depth":256', '"depth":128,"depth":256')
        bind(old_raw[:-1] + ',"hardware_variant":' + nested + '}')
        call('duplicate-assignment-field')
        bind(old_raw[:-1] + ',"hardware_variant":null}')
        call('present-null')
    a.output.write_text(json.dumps(receipts, indent=2)+'\n')
    print(json.dumps(receipts, indent=2))


if __name__ == '__main__':
    main()
