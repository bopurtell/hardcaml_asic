"""Public native version metadata and raw-environ negatives; no EDA or Docker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

VARIABLE = 'HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT'
CAPABILITY = dict(schema_version=1, profile='synthesis-total-tasks-v1', total_container_tasks=6,
                  fresh_only=True, acceptance=False, opt_in=VARIABLE + '=6')
ENVELOPE = dict(schema_version=1, profile='synthesis-total-tasks-v1', total_container_tasks=6,
                configured_executor_max_workers=6, reduced_usable_executor_concurrency=True)
RAW_EXEC = r'''
import ctypes, os, sys
binary = os.fsencode(sys.argv[1])
entries = [os.fsencode(k+'='+v) for k,v in os.environ.items()
           if k != 'HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT']
entries += [os.fsencode(s) for s in sys.argv[2:]]
argv = (ctypes.c_char_p * 4)(binary, b'--version', b'--json', None)
env = (ctypes.c_char_p * (len(entries)+1))(*entries, None)
libc = ctypes.CDLL(None, use_errno=True)
libc.execve(binary, argv, env)
raise OSError(ctypes.get_errno(), 'execve failed')
'''


def verify(binary, evidence=None):
    binary = Path(binary).resolve(strict=True)
    if evidence is not None:
        evidence.mkdir()
    base = dict(os.environ, PATH='/no-tools', PYTHONPATH='/no-python')
    base.pop(VARIABLE, None)
    cases = []

    def call(name, env, entries=None, args=('--version', '--json')):
        argv = ([str(binary), *args] if entries is None else
                [sys.executable, '-B', '-c', RAW_EXEC, str(binary), *entries])
        p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
        cases.append(dict(name=name, argv=argv, opt_in=env.get(VARIABLE), raw_entries=entries,
                          exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr))
        return p

    for name, env, active in [('default', base, False), ('inactive-empty-environment', {}, False),
                             ('inactive-neighbor', dict(base, **{VARIABLE + '_OTHER': '6'}), False),
                             ('active', dict(base, **{VARIABLE: '6'}), True)]:
        for args in (('--version', '--json'), ('--json', '--version')):
            p = call(name, env, args=args)
            assert p.returncode == 0, (name, p.stderr)
            value = json.loads(p.stdout)
            capability = value['cli_contract']['capabilities']['synthesis_collection1']
            assert json.dumps(capability, sort_keys=True) == json.dumps(CAPABILITY, sort_keys=True)
            assert value['execution_envelope'] == (ENVELOPE if active else None), value

    for value in ('', '0', '2', '20', '06', '6 ', ' 6', 'max', 'false', '6,6', '6\n'):
        p = call('invalid-value-' + repr(value), dict(base, **{VARIABLE: value}))
        assert p.returncode == 2 and not p.stdout and VARIABLE in p.stderr, p
    for entries in ([VARIABLE + '=6', VARIABLE + '=6'], [VARIABLE + '=6', VARIABLE + '=20'],
                    [VARIABLE + '=20', VARIABLE + '=6'], [VARIABLE]):
        p = call('invalid-raw-environment', base, entries=entries)
        assert p.returncode == 2 and not p.stdout and VARIABLE in p.stderr, (entries, p)
    p = call('invalid-human-version', dict(base, **{VARIABLE: '20'}), args=('--version',))
    assert p.returncode == 2 and not p.stdout and VARIABLE in p.stderr
    if evidence is not None:
        (evidence / 'metadata-controls.json').write_text(json.dumps(dict(
            passed=True, eda_launches=0, binary=str(binary),
            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), cases=cases), indent=2) + '\n')
    print(f'synthesis collection public metadata: {len(cases)} default/inactive/active and malformed/duplicate controls passed')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cli', type=Path, required=True)
    p.add_argument('--evidence', type=Path)
    args = p.parse_args()
    verify(args.cli, args.evidence)
