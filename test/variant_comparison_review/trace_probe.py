"""Trace the actual native public reader, distinguishing helper dispatch from EDA."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cli', type=Path, required=True)
    ap.add_argument('--physical', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='variant-trace-review-') as tmp:
        log = Path(tmp)/'strace.log'
        cmd = ['/usr/bin/strace', '-f', '-qq', '-s', '2048', '-e', 'trace=%file,process,network',
               '-o', str(log), str(a.cli.resolve()), 'compare', '--left-kind', 'coarse_run',
               '--left', str(a.physical.resolve()), '--right-kind', 'coarse_run',
               '--right', str(a.physical.resolve()), '--axis', 'descriptive', '--json']
        r = subprocess.run(cmd, env=dict(os.environ, PATH='/no-tools', PYTHONDONTWRITEBYTECODE='1'),
                           text=True, capture_output=True, timeout=120)
        lines = log.read_text().splitlines()
        execs = [l for l in lines if 'execve(' in l or 'execveat(' in l]
        writes = [l for l in lines if any(flag in l for flag in ['O_WRONLY', 'O_RDWR', 'O_CREAT', 'O_TRUNC', 'O_APPEND'])
                  or re.search(r'\b(unlink|rename|mkdir|rmdir|symlink|link|truncate|chmod|chown|utime|utimes|utimensat)\(', l)]
        network = [l for l in lines if re.search(r'\b(socket|connect|bind|listen|sendto|sendmsg)\(', l)]
        assert r.returncode == 0, r.stderr
        assert not writes and not network, (writes, network)
        assert len(execs) == 2, execs
        assert 'experimental-compare' in execs[1] or 'experimental_compare' in execs[1], execs
        receipt = dict(status='passed', syscalls=len(lines), execs=execs, mutation_syscalls=writes,
                       network_syscalls=network, response_bytes=len(r.stdout.encode()),
                       comparison_digest=json.loads(r.stdout)['comparison_digest'])
        a.output.write_text(json.dumps(receipt, indent=2)+'\n')
        print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
