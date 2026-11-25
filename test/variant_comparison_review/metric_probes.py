"""Rebind native metric sources in disposable real-record copies; no tool runs.

Rehashed fixtures test the reader, not physical truth or authenticated execution.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, j):
    p.write_text(json.dumps(j, separators=(',', ':')) + '\n')


def rebind(root):
    # Dependency order: inner audit -> selected audits/progress -> outer closure.
    for name in ['timing-intent/corners/nom_typ_1p20V_25C/audit.json',
                 'timing-intent/input-selection.json', 'timing-intent/runtime-result.json',
                 'timing-intent/corner-progress.json', 'timing-intent/analysis.json']:
        p = root/name
        j = json.loads(p.read_text())
        def refs(node):
            if isinstance(node, dict):
                if 'path' in node and 'sha256' in node:
                    rel = Path(node['path'])
                    candidate = p.parent/rel if p.name == 'audit.json' else root/rel
                    if candidate.is_file():
                        node['sha256'] = sha(candidate)
                for v in node.values():
                    refs(v)
            elif isinstance(node, list):
                for v in node:
                    refs(v)
        refs(j)
        write(p, j)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--physical', type=Path, required=True)
    ap.add_argument('--cli', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    env = dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1')
    receipts = []
    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='variant-metric-review-') as tmp:
        root = Path(tmp)
        left, right = root/'left', root/'right'
        original = a.physical.resolve()
        before = {str(p): (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in original.rglob('*') if p.is_file()}
        shutil.copytree(original, left, symlinks=True)
        shutil.copytree(original, right, symlinks=True)
        state = right/'timing-intent/corners/state_out.json'
        log = right/'timing-intent/corners/nom_typ_1p20V_25C/sta.log'
        state_original, log_original = state.read_bytes(), log.read_bytes()
        corner = 'nom_typ_1p20V_25C'
        def metric(name, value):
            j = json.loads(state.read_text())
            key = name + '__corner:' + corner
            j['metrics'][key] = value
            write(state, j)
            raw = log.read_text()
            raw, count = re.subn(r'(%OL_METRIC_[IF] ' + re.escape(key) + r') [^\r\n]+',
                                lambda m: m[1] + ' ' + str(value), raw)
            assert count == 1
            log.write_text(raw)
            rebind(right)
        def call(name, axis='descriptive'):
            cmd = [str(a.cli.resolve()), 'compare', '--left-kind', 'coarse_run', '--left', str(left),
                   '--right-kind', 'coarse_run', '--right', str(right), '--axis', axis, '--json']
            r = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=120)
            receipt = dict(case=name, code=r.returncode, stderr=r.stderr)
            if r.stdout:
                j = json.loads(r.stdout)
                receipt.update(digest=j['comparison_digest'], left_generation=j['left']['source_digest'],
                    right_generation=j['right']['source_digest'],
                    left_run_id=j['left']['identities']['run_id'],right_run_id=j['right']['identities']['run_id'],
                    native=[m for m in j['metrics'] if m['metric_id'].startswith('native/')])
            receipts.append(receipt)
            return receipt
        metric('timing__hold__ws', -0.25)
        metric('timing__hold_vio__count', 7)
        good = call('coherently-rebound-signed-slack-and-count')
        if good['code'] == 0:
            hold = next(m for m in good['native'] if m['metric_id'] == 'native/'+corner+'/hold_slack')
            count = next(m for m in good['native'] if m['metric_id'] == 'native/'+corner+'/hold_violations')
            assert hold['comparison'] == 'comparable' and hold['right_minus_left'] < 0
            assert count['comparison'] == 'comparable' and count['right_minus_left'] == 7
        metric('timing__hold_vio__count', 2**53+1)
        large = call('source-count-outside-exact-native-domain')
        if large['code'] == 0:
            m = next(m for m in large['native'] if m['metric_id'] == 'native/'+corner+'/hold_violations')
            assert m['right_minus_left'] is None and m['comparison'] == 'unavailable'
        # Native exact runtime binding is mandatory independently of any labels.
        record = json.loads((right/'run.json').read_text())
        record.pop('execution_binding', None)
        record['environment'] = dict(status='unknown', ready=False)
        write(right/'run.json', record)
        missing = call('unknown-environment-and-missing-authoritative-binding')
        assert missing['code'] == 2
        assert before == {str(p): (sha(p), p.stat().st_size, p.stat().st_mtime_ns)
                          for p in original.rglob('*') if p.is_file()}
    a.output.write_text(json.dumps(receipts, indent=2)+'\n')
    print(json.dumps([dict(case=r['case'], code=r['code'], stderr=r['stderr'],
        native_metrics=len(r.get('native', []))) for r in receipts], indent=2))


if __name__ == '__main__':
    main()
