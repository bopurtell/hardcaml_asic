"""Independent narrow R2/R3/R4 true controls and actual-open replacement fences."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'test'))
from check_resource_evidence import fixture


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, value):
    p.write_text(json.dumps(value, separators=(',', ':'))+'\n')


def main():
    ap = argparse.ArgumentParser()
    for name in ['consumer', 'cli', 'shim', 'output']:
        ap.add_argument('--'+name, type=Path, required=True)
    a = ap.parse_args()
    consumer, cli = a.consumer.resolve(), a.cli.resolve()
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    receipts = []

    def invoke(name, cmd, code=0):
        r = subprocess.run(list(map(str, cmd)), env=env, text=True, capture_output=True, timeout=10)
        assert r.returncode == code, (name, r.returncode, r.stderr)
        value = json.loads(r.stdout) if r.stdout else None
        receipts.append(dict(case=name, code=r.returncode, stderr=r.stderr))
        return value

    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='variant-correction-controls-') as tmp:
        root = Path(tmp)
        run = root/'run'
        run.mkdir()
        fixture(run)
        results = run/'results.json'
        stored = invoke('actual-collection', [consumer, 'collect', run])
        write(results, stored)
        cmd = [cli, 'compare', '--left-kind', 'coarse_run', '--left', run, '--right-kind',
               'coarse_run', '--right', run, '--axis', 'descriptive', '--json']
        baseline = invoke('stored-baseline', cmd)
        refs = baseline['left']['consulted_metadata']
        for path in [run/'run.json', results, run/'project/manifest.json']:
            ref = next(r for r in refs if r['path'] == str(path))
            assert ref['sha256'] == sha(path) and ref['bytes'] == path.stat().st_size
        original_hash = sha(results)
        results.write_text(json.dumps(stored, indent=4)+'\n')
        changed = invoke('stable-raw-reformat', cmd)
        assert baseline['metrics'] == changed['metrics']
        assert baseline['comparison_digest'] != changed['comparison_digest']
        receipts[-1].update(old_results_sha256=original_hash, new_results_sha256=sha(results),
            all_three_original_metadata_refs_checked=True, unchanged_metrics=True)

        # Not an inotify scheduling guess: replace immediately before/after the
        # real leaf open. The former disproves a stat-only admission fix; the
        # latter checks that a pinned old descriptor cannot mask path replacement.
        for fence in ['before', 'after']:
            write(results, stored)
            notify_r, notify_w = os.pipe()
            release_r, release_w = os.pipe()
            injected = dict(env, LD_PRELOAD=str(a.shim.resolve()), VARIANT_REVIEW_FENCE=fence,
                            VARIANT_REVIEW_NOTIFY_FD=str(notify_w), VARIANT_REVIEW_RELEASE_FD=str(release_r))
            p = subprocess.Popen(list(map(str, cmd)), env=injected, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 pass_fds=(notify_w, release_r))
            try:
                assert select.select([notify_r], [], [], 10)[0], 'actual open fence was not reached'
                assert os.read(notify_r, 1) == b'R'
                results.unlink()
                os.mkfifo(results)
                os.write(release_w, b'G')
                out, err = p.communicate(timeout=5)
                assert p.returncode == 2 and not out, (fence, p.returncode, out, err)
                receipts.append(dict(case='actual-open-'+fence+'-fifo-replacement', code=2,
                    actual_fence_reached=True, timed_out=False, stderr=err))
            finally:
                if p.poll() is None:
                    p.kill(); p.communicate()
                for fd in [notify_r, notify_w, release_r, release_w]:
                    os.close(fd)
                results.unlink()

        # Archive's original and fallback staged roles, strict optional metadata
        # and genuine legacy last-wins semantics use the real public service.
        original = root/'original-bundle'
        shutil.copytree(run/'project', original)
        record = json.loads((run/'run.json').read_text())
        record['bundle'] = str(original)
        write(run/'run.json', record)
        r = invoke('archive-valid-original-role', [consumer, 'archive-inspect', run])
        assert r['bundle_source'] == 'original'
        shutil.rmtree(original)
        r = invoke('archive-valid-staged-fallback', [consumer, 'archive-inspect', run])
        assert r['bundle_source'] == 'staged'
        manifest = run/'project/manifest.json'
        old = json.loads(manifest.read_text())
        valid = invoke('public-variant', [consumer, 'variant'])
        def bind(value, extra_raw=''):
            unsigned = {k:v for k,v in value.items() if k != 'identity'}
            identity = hashlib.sha256(json.dumps(unsigned, separators=(',', ':')).encode()).hexdigest()
            raw = json.dumps(dict(identity=identity, **unsigned), separators=(',', ':'))
            if extra_raw:
                raw = '{'+extra_raw+raw[1:]
            manifest.write_text(raw)
            rec = json.loads((run/'run.json').read_text())
            rec.update(build_identity=identity, manifest_sha256=sha(manifest))
            write(run/'run.json', rec)
        bind(old, '"project":"legacy-old",')
        invoke('archive-absent-legacy-duplicate-semantics', [consumer, 'archive-inspect', run])
        bound = dict(old, hardware_variant=valid)
        bind(bound)
        invoke('archive-valid-optional-staged', [consumer, 'archive-inspect', run])
        for key, other in [('contract','other'), ('design_id','other'), ('schema_id','other'),
                           ('schema_version',99), ('identity','bad')]:
            # Insert a losing duplicate, leaving every normalized payload/digest
            # correctly bound. Strict original-byte admission must still reject.
            bind(bound)
            raw = manifest.read_text()
            marker = '"hardware_variant":{'
            raw = raw.replace(marker, marker+json.dumps(key)+':'+json.dumps(other)+',', 1)
            manifest.write_text(raw)
            rec = json.loads((run/'run.json').read_text())
            rec['manifest_sha256'] = sha(manifest)
            write(run/'run.json', rec)
            invoke('archive-duplicate-hardware-'+key, [consumer, 'archive-inspect', run], 2)

    a.output.write_text(json.dumps(receipts, indent=2)+'\n')
    print(json.dumps(receipts, indent=2))


if __name__ == '__main__':
    main()
