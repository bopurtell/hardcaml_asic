"""Independent B cross-review. Passing reproduction records a known fence gap;
it does NOT mean the production gap has been fixed. No production/test edits."""
import argparse
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'workload_comparison'))
from check import fixture, ref, sha, write
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_resource_evidence import fixture as coarse_fixture


def main():
    ap = argparse.ArgumentParser()
    for key in ['probe', 'variant', 'fence']:
        ap.add_argument('--' + key, type=Path, required=True)
    a = ap.parse_args()
    probe, variant, fence = (getattr(a, k).resolve() for k in ['probe', 'variant', 'fence'])
    checks = 0
    receipts = {}

    def call(args, error=None):
        nonlocal checks
        p = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=60)
        checks += 1
        assert p.returncode == (2 if error else 0), (p.returncode, p.stdout, p.stderr, error)
        if error:
            assert error in p.stderr, (error, p.stderr)
            return p.stderr.strip()
        return json.loads(p.stdout)

    v = call([variant, 'variant'])
    with tempfile.TemporaryDirectory(prefix='pt8-independent-B-') as tmp:
        root = Path(tmp)
        l, lw, ls, lr = fixture(root / 'left', v, 'flops')
        r, rw, rs, rr = fixture(root / 'right', v, 'sram')
        command = [probe, 'bundle', l, 'bundle', r, lw, rw]
        good = call(command)
        metric = good['left']['workload']['metrics'][0]
        # Exercise all controlling families rather than merely changing values.
        for key in ['clock', 'model', 'adapter', 'measurement', 'workload', 'tool', 'rtl_mode']:
            changed = copy.deepcopy(metric)
            changed['controls'][key] = ['different-semantic-set'] if key == 'model' else 'different'
            row = call([probe, 'metric', json.dumps(metric), json.dumps(changed)])
            assert row['comparison'] == 'confounded' and row['right_minus_left'] is None
            changed['controls'][key] = None
            row = call([probe, 'metric', json.dumps(metric), json.dumps(changed)])
            assert row['comparison'] == 'unavailable'
        for key in ['stage', 'scope', 'definition', 'unit', 'quantity', 'classification']:
            changed = dict(metric, **{key: 'different'})
            row = call([probe, 'metric', json.dumps(metric), json.dumps(changed)])
            assert row['comparison'] != 'comparable' and row['right_minus_left'] is None
        changed = copy.deepcopy(metric)
        changed['controls'].update(libraries=['unrelated-library'], rc='not-a-physical-metric', environment='other')
        assert call([probe, 'metric', json.dumps(metric), json.dumps(changed)])['comparison'] == 'comparable'
        # Rational subtraction: negative, unreduced, very large exact quantities.
        throughput = good['left']['workload']['metrics'][-1]
        for numerator, denominator, expected in [(1, 3, (-1, 3)), (4, 6, (0, 1)),
                                                  (0, 12345, (-2, 3)),
                                                  (10**50, 3 * 10**50, (-1, 3))]:
            changed = dict(throughput, value=numerator / denominator, numerator=numerator, denominator=denominator)
            row = call([probe, 'metric', json.dumps(throughput), json.dumps(changed)])
            assert row['comparison'] == 'comparable'
            assert row['right_minus_left_ratio'] == dict(zip(['numerator', 'denominator'], expected))
        for denominator in [0, -1, None]:
            changed = dict(throughput, denominator=denominator)
            row = call([probe, 'metric', json.dumps(throughput), json.dumps(changed)])
            assert row['comparison'] == 'unavailable' and row['right_minus_left_ratio'] is None
        # Concrete model sets are NOT semantic control sets. An extra, retained
        # concrete model is allowed under equal declared model semantics.
        model = rw.parent / 'additional-model.v'
        model.write_bytes(b'extra concrete model fixture')
        changed = copy.deepcopy(rs)
        changed['artifacts'].append(dict(role='model', source=ref(model)))
        write(rw.parent / 'simulation.json', changed)
        write(rw, dict(rr, simulation=ref(rw.parent / 'simulation.json')))
        concrete = call(command)
        assert all(m['comparison'] == 'comparable' for m in concrete['metrics'] if m['metric_id'].startswith('workload/'))
        receipts['concrete_model_set'] = 'different bytes/member count accepted under equal semantic declaration (documented trust boundary)'
        write(rw.parent / 'simulation.json', rs)
        write(rw, rr)
        # Coarse-run linkage is honestly bundle-only; no run attempt promotion.
        coarse = root / 'coarse'
        coarse.mkdir()
        coarse_fixture(coarse)
        (coarse / 'project' / 'top.v').write_bytes((l / 'top.v').read_bytes())
        (coarse / 'project' / 'manifest.json').write_bytes((l / 'manifest.json').read_bytes())
        run_path = coarse / 'run.json'
        run = json.loads(run_path.read_bytes())
        run.update(build_identity=ls['bundle_identity'], manifest_sha256=sha((l / 'manifest.json').read_bytes()))
        write(run_path, run)
        coarse_command = [probe, 'coarse_run', coarse, 'bundle', r, lw, rw]
        joined = call(coarse_command)
        assert joined['left']['identities']['run_id'] == 'fixture'
        assert joined['left']['workload']['binding']['simulation_attempt_id'] == 'flops'
        assert joined['left']['workload_association'] == 'verified_bundle_and_hardware_only_not_execution_attempt'
        receipts['coarse_association'] = 'separate simulation and coarse-run identities retained; only bundle/hardware join asserted'

        def race(command, count, source):
            nonlocal checks
            original = source.read_bytes()
            nr, nw = os.pipe()
            rrfd, rwfd = os.pipe()
            env = dict(os.environ, LD_PRELOAD=str(fence), PT8_OPEN_COUNT=str(count),
                       PT8_NOTIFY=str(nw), PT8_RELEASE=str(rrfd))
            proc = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, pass_fds=(nw, rrfd))
            try:
                assert select.select([nr], [], [], 20)[0], 'read boundary not reached'
                assert os.read(nr, 1) == b'R'
                source.write_bytes(original + b'\n')
                os.write(rwfd, b'R')
                stdout, stderr = proc.communicate(timeout=60)
                checks += 1
                return proc.returncode, json.loads(stdout) if stdout else None, stderr.decode(), sha(source.read_bytes())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                for fd in [nr, nw, rrfd, rwfd]:
                    os.close(fd)
                source.write_bytes(original)

        code, _, error, _ = race(command, 3, lw)
        assert code == 2 and 'stale workload' in error
        receipts['early_mutation_control'] = 'correctly rejected at first opposite-side capture'
        code, result, error, current_sha = race(command, 7, lw)
        assert code == 0, (code, error)
        returned_sha = next(s['sha256'] for s in result['left']['workload']['provenance']['sources'] if s['path'] == str(lw))
        assert returned_sha != current_sha
        assert all(m['comparison'] == 'comparable' for m in result['metrics'] if m['metric_id'].startswith('workload/'))
        receipts['F1_late_workload_generation'] = dict(exit_code=code, returned_sha256=returned_sha,
                                                      current_sha256=current_sha, delta_state='comparable')
        # Stronger cross-domain case: target metadata changes while the new
        # workload validation loop runs, after the target's final recapture.
        code, result, error, current_sha = race(coarse_command, 5, run_path)
        assert code == 0, (code, error)
        returned_sha = result['left']['identities']['record_sha256']
        assert returned_sha != current_sha
        receipts['F1_target_during_workload_fence'] = dict(exit_code=code, returned_sha256=returned_sha,
                                                          current_sha256=current_sha)
        # Oversized pair: individually bounded/valid workloads, but response
        # duplication of source refs must still hit the complete 1MiB gate.
        long = root
        for i in range(5):
            long = long / (str(i) + 'p' * 170)
            long.mkdir()
        bl, wl, sl, rl = fixture(long / 'left', v, 'flops')
        br, wr, sr, rr_long = fixture(long / 'right', v, 'sram')
        for result_path, simulation, record in [(wl, sl, rl), (wr, sr, rr_long)]:
            directory = result_path.parent / ('q' * 180) / ('r' * 180)
            directory.mkdir(parents=True)
            for i in range(28):
                p = directory / ('model-' + str(i))
                p.write_bytes(b'bounded-model')
                source = ref(p)
                source['path'] = str(p.relative_to(result_path.parent))
                simulation['artifacts'].append(dict(role='model', source=source))
            write(result_path.parent / 'simulation.json', simulation)
            write(result_path, dict(record, simulation=ref(result_path.parent / 'simulation.json')))
            standalone = call([probe, 'workload', result_path])
            assert len(json.dumps(standalone, separators=(',', ':')).encode()) < 1024**2
        receipts['whole_response_gate'] = call([probe, 'bundle', bl, 'bundle', br, wl, wr], error='whole JSON exceeds 1MiB')
    print(json.dumps(dict(checks=checks, receipts=receipts), indent=2))


if __name__ == '__main__':
    main()
