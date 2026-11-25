"""Durable B corrections for independent review R1-R4; no A fixture changes.

All mutations are disposable fixtures. Services run with tool lookup denied; their
queries may neither rewrite source files nor turn genuine failed evidence into a
query error. Native descriptor admission is exercised through the actual report
and comparison services, including open-time replacement, not only a precheck.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, j):
    p.write_text(json.dumps(j, separators=(',', ':')) + '\n')


def rebind(root, transform):
    p = root / 'project/manifest.json'
    j = json.loads(p.read_text())
    j.pop('identity')
    transform(j)
    identity = hashlib.sha256(json.dumps(j, separators=(',', ':')).encode()).hexdigest()
    write(p, dict(identity=identity, **j))
    run = root / 'run.json'
    j = json.loads(run.read_text())
    j.update(build_identity=identity, manifest_sha256=sha(p))
    write(run, j)


def bound_fixture(root):
    root.mkdir()
    fixture(root)
    member = root / 'project/inputs/config.ml'
    member.parent.mkdir()
    member.write_text('let depth = 256\n')
    rebind(root, lambda j: j.update(files=[dict(path='inputs/config.ml', sha256=sha(member))],
           source_inputs=[dict(path='config.ml', copy='inputs/config.ml', sha256=sha(member), git_status='')]))
    return member


def snapshot(root):
    return {str(p): (sha(p), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--consumer', type=Path, required=True)
    ap.add_argument('--cli', type=Path, required=True)
    a = ap.parse_args()
    consumer, cli = a.consumer.resolve(), a.cli.resolve()
    env = dict(os.environ, PATH='/no-tools', PYTHONPATH='/no-catalog', PYTHONDONTWRITEBYTECODE='1')
    checks = 0

    def call(args, code=0):
        nonlocal checks
        p = subprocess.run(list(map(str, args)), env=env, capture_output=True, text=True, timeout=5)
        assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
        checks += 1
        if code == 2:
            assert not p.stdout and p.stderr
            return p.stderr
        return json.loads(p.stdout)

    def compare(left, right=None, code=0):
        args = [cli, 'compare', '--left-kind', 'coarse_run', '--left', left,
                '--right-kind', 'coarse_run', '--right', right or left, '--axis', 'descriptive', '--json']
        return call(args, code)

    def on_open(watched, mutate, run):
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK)
        assert fd >= 0
        assert libc.inotify_add_watch(fd, os.fsencode(watched), 0x20) >= 0
        done = threading.Event()

        def worker():
            if select.select([fd], [], [], 10)[0]:
                os.read(fd, 65536)
                mutate()
                done.set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            result = run()
        finally:
            thread.join()
            os.close(fd)
        assert done.is_set(), 'race mutation must actually execute'
        return result

    with tempfile.TemporaryDirectory(prefix='variant-admission-') as temp:
        root = Path(temp)
        left, right = root / 'left', root / 'right'
        member = bound_fixture(left)
        bound_fixture(right)
        preserved = snapshot(root)
        baseline = compare(left)
        assert baseline['left']['verified_bundle_sources']
        assert compare(left)['comparison_digest'] == baseline['comparison_digest']
        assert snapshot(root) == preserved, 'queries are read-only'

        # R1: corrupt/missing declared bytes reject, not unchanged digest/delta.
        original_member = member.read_bytes()
        member.write_bytes(original_member + b'changed\n')
        compare(left, code=2)
        member.unlink()
        compare(left, code=2)
        member.write_bytes(original_member)
        assert compare(left)['comparison_digest'] == baseline['comparison_digest']
        manifest = left / 'project/manifest.json'
        manifest_raw, run_raw = manifest.read_bytes(), (left / 'run.json').read_bytes()
        for transform in [lambda j: j['source_inputs'][0].update(sha256='0' * 64),
                          lambda j: j['source_inputs'][0].update(copy='inputs/wrong.ml'),
                          lambda j: j['source_inputs'].append(j['source_inputs'][0].copy())]:
            rebind(left, transform)
            compare(left, code=2)
            manifest.write_bytes(manifest_raw)
            (left / 'run.json').write_bytes(run_raw)

        # R2: actual Report_service + comparison, not only a guarded pre-read.
        results = left / 'results.json'
        for kind in ['fifo', 'symlink', 'directory', 'oversize']:
            if kind == 'fifo':
                os.mkfifo(results)
            elif kind == 'symlink':
                donor = root / 'donor-results.json'
                write(donor, {})
                results.symlink_to(donor)
            elif kind == 'directory':
                results.mkdir()
            else:
                with results.open('wb') as out:
                    out.truncate(32 * 1024 * 1024 + 1)
            call([consumer, 'report', left], 2)
            compare(left, code=2)
            if kind == 'directory':
                results.rmdir()
            else:
                results.unlink()
        # The same descriptor reader rejects a symlink ancestor, as well as leaf.
        (root / 'alias').symlink_to(left, target_is_directory=True)
        call([consumer, 'read-regular', root / 'alias/run.json'], 2)

        stored = call([consumer, 'collect', left])
        write(results, stored)
        stable = compare(left)
        raw_hash = sha(results)
        assert any(r['path'].endswith('/results.json') and r['sha256'] == raw_hash
                   for r in stable['left']['consulted_metadata'])
        assert compare(left)['comparison_digest'] == stable['comparison_digest']
        # R3: a stable formatting-only rewrite has a distinct raw generation.
        results.write_text(json.dumps(stored, indent=4) + '\n')
        assert sha(results) != raw_hash
        changed = compare(left)
        assert changed['comparison_digest'] != stable['comparison_digest']
        assert changed['metrics'] == stable['metrics']
        write(results, stored)
        raw_hash = sha(results)
        error = on_open(right / 'project/manifest.json',
                        lambda: results.write_text(json.dumps(stored, indent=4) + '\n'),
                        lambda: compare(left, right, code=2))
        assert sha(results) != raw_hash and 'generation' in error, error
        # R2 TOCTOU: replace results with a FIFO at its actual open, after any
        # earlier path admission. A descriptor-safe read/replay rejects promptly.
        write(results, stored)

        def replace_fifo():
            results.unlink()
            os.mkfifo(results)

        on_open(results, replace_fifo, lambda: compare(left, code=2))
        results.unlink()
        # A real failed retention observation is readable, not a corruption error.
        mapped = left / 'project/runs/asic/06-yosys-synthesis/top.nl.v.json'
        j = json.loads(mapped.read_text())
        j['modules']['top']['cells'].pop('hc_resource_6_memory')
        write(mapped, j)
        failed = compare(left)
        assert failed['left']['outcomes']['resource_retention'] == 'fail'
        assert failed['left']['outcomes']['acceptance'] == 'fail'
        assert not call([consumer, 'report', left])['accepted']

        # R4: exact raw duplicate admission through Archive_service.inspect.
        archive = root / 'archive'
        archive.mkdir()
        fixture(archive)
        p = archive / 'project/manifest.json'
        old = json.loads(p.read_text())
        original_identity = old['identity']
        old_raw = json.dumps(old, separators=(',', ':'))
        variant = call([consumer, 'variant'])
        variant_raw = json.dumps(variant, separators=(',', ':'))

        def bind_raw(raw):
            semantic = json.loads(raw)
            unsigned = {k: v for k, v in semantic.items() if k != 'identity'}
            identity = hashlib.sha256(json.dumps(unsigned, separators=(',', ':')).encode()).hexdigest()
            raw = raw.replace('"identity":"' + original_identity + '"', '"identity":"' + identity + '"', 1)
            p.write_text(raw)
            run = json.loads((archive / 'run.json').read_text())
            run.update(build_identity=identity, manifest_sha256=sha(p))
            write(archive / 'run.json', run)

        cases = [('legacy-absence', old_raw, 0),
                 ('valid', old_raw[:-1] + ',"hardware_variant":' + variant_raw + '}', 0),
                 ('null', old_raw[:-1] + ',"hardware_variant":null}', 2),
                 ('duplicate-top', old_raw[:-1] + ',"hardware_variant":null,"hardware_variant":' + variant_raw + '}', 2),
                 ('escaped-top', old_raw[:-1] + ',"\\u0068ardware_variant":null,"hardware_variant":' + variant_raw + '}', 2),
                 ('duplicate-assignment', old_raw[:-1] + ',"hardware_variant":' +
                  variant_raw.replace('"depth":256', '"depth":128,"depth":256') + '}', 2),
                 ('escaped-assignment', old_raw[:-1] + ',"hardware_variant":' +
                  variant_raw.replace('"depth":256', '"\\u0064epth":128,"depth":256') + '}', 2),
                 ('duplicate-descriptor', old_raw[:-1] + ',"hardware_variant":' +
                  variant_raw.replace('"minimum":128', '"minimum":127,"minimum":128') + '}', 2)]
        for label, raw, code in cases:
            bind_raw(raw)
            call([consumer, 'archive-inspect', archive], code)
            call([cli, 'compare', '--axis', 'hardware', '--left-kind', 'bundle', '--left', archive / 'project',
                  '--right-kind', 'bundle', '--right', archive / 'project', '--json'], code)
        bind_raw(old_raw)
        # Historical schema1 genuinely failed evidence is still archivable.
        net = archive / 'project/runs/asic/06-yosys-synthesis/top.nl.v.json'
        j = json.loads(net.read_text())
        j['modules']['top']['cells'].pop('hc_resource_6_memory')
        write(net, j)
        call([consumer, 'archive-inspect', archive])
    print(f'Variant comparison review corrections: {checks} real-service admission/generation/archive checks passed')


if __name__ == '__main__':
    main()
