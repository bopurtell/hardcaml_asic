#!/usr/bin/env python3
"""Small Dune stand-in; never runs a real build or install."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


if sys.argv[1:] == ['--grandchild']:
    def stop(signum, _frame):
        Path(os.environ['FAKE_SIGNAL']).write_text(str(signum))
        if os.environ['FAKE_MODE'] != 'stubborn':
            sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    Path(os.environ['FAKE_READY']).write_text(json.dumps({
        'leader': os.getppid(), 'child': os.getpid(), 'group': os.getpgrp(),
    }))
    while True:
        time.sleep(0.05)

with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(),
                          'workers': os.environ.get('HARDCAML_ASIC_TEST_WORKERS'),
                          'switch': os.environ.get('OPAMSWITCH')}) + '\n')
print('fake Dune stdout: ' + sys.argv[1], flush=True)
print('fake Dune stderr: ' + sys.argv[1], file=sys.stderr, flush=True)
mode = os.environ.get('FAKE_MODE', 'success')
if mode in ('tree', 'stubborn', 'orphan-failure'):
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    child = subprocess.Popen([sys.executable, __file__, '--grandchild'])
    while not Path(os.environ['FAKE_READY']).exists():
        time.sleep(0.01)
    if mode == 'orphan-failure':
        sys.exit(23)
    child.wait()
elif mode == 'failure' and sys.argv[1] == os.environ.get('FAKE_FAIL_STAGE', 'runtest'):
    sys.exit(23)
elif mode == 'slow':
    time.sleep(0.35)
