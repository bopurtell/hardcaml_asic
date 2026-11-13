#!/usr/bin/env python3
"""Build, test and install ASIC into an explicit prefix using an existing switch.

Consumers own source/version selection and environment activation. All progress
and child output goes to stderr, including when captured by a downstream caller.
"""
import argparse
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time


HEARTBEAT_SECONDS = 15
CANCEL_GRACE_SECONDS = 5
POLL_SECONDS = 0.1


class Cancelled(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(signal.Signals(signum).name)


class Cancellation:
    def __init__(self):
        self.signum = None

    def request(self, signum, _frame):
        # Do not raise asynchronously: a signal can arrive while Popen is
        # creating the child, before we have its process-group ID for cleanup.
        if self.signum is None:
            self.signum = signum

    def check(self):
        if self.signum is not None:
            raise Cancelled(self.signum)


def progress(message):
    print(message, file=sys.stderr, flush=True)


def signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        return False


def stop_group(process, signum=signal.SIGTERM):
    """Stop descendants too, even if the group leader has already exited."""
    signal_group(process, signum)
    deadline = time.monotonic() + CANCEL_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()  # Reap the leader, but do not mistake its exit for cleanup.
        if not signal_group(process, 0):
            break
        time.sleep(POLL_SECONDS)
    else:
        signal_group(process, signal.SIGKILL)
    process.wait()


def run(label, argv, root, env, cancellation, verbose=False):
    cancellation.check()
    started = time.monotonic()
    progress(f'[ASIC START] {label}')
    argv = list(map(str, argv))
    if verbose:
        progress(f'+ {shlex.join(argv)}')
    process = None
    try:
        # Electrum may run us in its own process group with captured streams.
        # A separate session lets us clean up Dune's entire tree without
        # signalling Electrum (or other commands in its process group).
        process = subprocess.Popen(argv, cwd=root, env=env, start_new_session=True,
                                   stdout=sys.stderr, stderr=sys.stderr)
        heartbeat = started + HEARTBEAT_SECONDS
        while True:
            cancellation.check()
            try:
                code = process.wait(timeout=POLL_SECONDS)
                cancellation.check()
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now >= heartbeat:
                    progress(f'[ASIC WORKING] {label} ({now - started:.1f}s elapsed)')
                    heartbeat = now + HEARTBEAT_SECONDS
        if code:
            raise subprocess.CalledProcessError(code, argv)
    except BaseException as error:
        if process is not None:
            signum = error.signum if isinstance(error, Cancelled) else signal.SIGTERM
            stop_group(process, signum)
        status = 'CANCELLED' if isinstance(error, (Cancelled, KeyboardInterrupt)) else 'FAILED'
        progress(f'[ASIC {status}] {label} ({time.monotonic() - started:.1f}s elapsed): {error}')
        raise
    progress(f'[ASIC DONE] {label} ({time.monotonic() - started:.1f}s elapsed)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', required=True, type=Path)
    parser.add_argument('--dune', default='dune')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prefix = args.prefix.resolve()
    env = dict(os.environ)
    # This controls only check_result_store.py, not the rest of the test suite.
    env.setdefault('HARDCAML_ASIC_TEST_WORKERS', '4')
    display = 'verbose' if args.verbose else 'short'
    cancellation = Cancellation()
    previous = {sig: signal.signal(sig, cancellation.request)
                for sig in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()

    def dune(command, *options):
        return [args.dune, command, '--root', root, '--display', display, *options]

    stages = [
        ('1/3 Build hardcaml_asic (Dune: 4 jobs)',
         dune('build', '-j4', '--only-packages', 'hardcaml_asic', '@install')),
        ('2/3 Test hardcaml_asic (Dune: 1 job; result-store workers: '
         + env['HARDCAML_ASIC_TEST_WORKERS'] + ')',
         dune('runtest', '--no-buffer', '-j1')),
        ('3/3 Install hardcaml_asic',
         dune('install', '--prefix', prefix, 'hardcaml_asic')),
    ]
    try:
        progress(f'[ASIC INSTALL] prefix: {prefix}')
        for label, argv in stages:
            run(label, argv, root, env, cancellation, args.verbose)
        cancellation.check()
        progress(f'[ASIC COMPLETE] Installed to {prefix} ({time.monotonic() - started:.1f}s elapsed)')
        return 0
    except Cancelled as error:
        progress(f'[ASIC CANCELLED] Installation stopped by {error}')
        return 128 + error.signum
    except (OSError, subprocess.CalledProcessError) as error:
        progress(f'[ASIC FAILED] Installation failed: {error}')
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())
