#!/usr/bin/env python3
"""Strict public CLI parity against an independently linked public API consumer."""
import argparse
import json
from pathlib import Path
import subprocess
import re


def check(cli, api, results, output):
    output.mkdir(parents=True, exist_ok=False)
    count = 0
    def command(args, expected=0):
        nonlocal count
        count += 1
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=60)
        (output / f'{count:02d}.stdout').write_bytes(result.stdout)
        (output / f'{count:02d}.stderr').write_bytes(result.stderr)
        (output / f'{count:02d}.json').write_text(json.dumps({
            'argv': list(map(str, args)), 'exit_code': result.returncode}) + '\n')
        assert result.returncode == expected, (args, result.returncode, result.stderr)
        if expected == 2:
            assert not result.stdout, ('invalid command polluted stdout', args)
        return result.stdout
    identity = json.loads(command([cli, '--version', '--json']))
    cap = identity['cli_contract']['capabilities']['workload_inspection1']
    assert cap['read_only'] and cap['schema_version'] == 1 and cap['max_response_bytes'] == 1048576
    for path in results:
        expected = json.loads(command([api, 'inspect', path]))
        for args in ([cli, 'workload-inspect', path, '--json'],
                     [cli, '--json', 'workload-inspect', path],
                     [cli, 'workload-inspect', '--json', path],
                     [cli, 'workload-inspect', '--json', '--', path]):
            data = command(args)
            assert len(data) <= 1048576 and json.loads(data) == expected
    path = results[0]
    for args in ([], ['', '--json'], [path, path, '--json'], [path, '--result', path],
                 [path, '--seed', '34097'], [path, '--json=true'],
                 [path, '--axis', 'hardware'], ['--unknown'], [output, '--json'],
                 [output / 'missing.json', '--json']):
        command([cli, 'workload-inspect', *args], 2)
    invalid = output / 'invalid.json'
    invalid.write_text('{"contract":"unknown"}\n')
    command([cli, 'workload-inspect', invalid, '--json'], 2)
    # Verify the advertised read-only operation at the syscall boundary. One
    # foreground PID exec-replaces itself with the public-service thin helper.
    trace = output / 'readonly.strace'
    command(['strace', '-f', '-s', '4096', '-e', 'trace=process,file,network',
        '-o', trace, '--', cli, 'workload-inspect', path, '--json'])
    lines = trace.read_text().splitlines()
    assert not [line for line in lines if re.search(r'\b(fork|vfork|clone|clone3|socket|socketpair|connect|bind|listen)\(', line)]
    assert not [line for line in lines if re.search(r'\b(open|openat|openat2)\(.*\bO_(WRONLY|RDWR|CREAT|TRUNC)\b', line)
                or re.search(r'\b(mkdir|unlink|rename|symlink|link|truncate|chmod|utime)\(', line)]
    execs = [line for line in lines if 'execve(' in line]
    assert len(execs) == 2 and len({line.split()[0] for line in execs}) == 1
    assert str(cli) in execs[0] and 'hardcaml-asic-experimental-workload-inspect' in execs[1]
    (output / 'assertions.json').write_text(json.dumps({'pass': True,
        'commands': count, 'api_cli_parity': True, 'strict_cli': True, 'read_only_syscalls': True,
        'results': list(map(str, results))}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--api', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('results', type=Path, nargs='+')
    args = parser.parse_args()
    check(args.cli, args.api, args.results, args.output)
