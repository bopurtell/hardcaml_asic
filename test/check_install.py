#!/usr/bin/env python3
"""Installer contract checks using only Python and fake Dune processes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / 'scripts/install.py'


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='asic install ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dune = self.root / 'fake dune'
        shutil.copyfile(Path(__file__).with_name('install_fake_dune.py'), self.dune)
        self.dune.chmod(0o755)
        self.env = dict(os.environ, FAKE_LOG=str(self.root / 'calls'),
                        FAKE_READY=str(self.root / 'ready'),
                        FAKE_SIGNAL=str(self.root / 'signal'), OPAMSWITCH='existing-switch')
        self.env.pop('HARDCAML_ASIC_TEST_WORKERS', None)
        self.command = [sys.executable, '-B', str(INSTALLER), '--prefix',
                        str(self.root / 'install prefix'), '--dune', str(self.dune)]

    def calls(self):
        path = self.root / 'calls'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def invoke(self, *extra):
        return subprocess.run(self.command + list(extra), env=self.env, cwd=self.root,
                              text=True, capture_output=True, timeout=15)

    def test_cli_output_and_existing_environment(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        calls = self.calls()
        self.assertEqual([call['argv'][0] for call in calls], ['build', 'runtest', 'install'])
        for call in calls:
            self.assertEqual(call['cwd'], str(INSTALLER.parent.parent))
            self.assertEqual(call['workers'], '4')
            self.assertEqual(call['switch'], 'existing-switch')
            self.assertIn('--root', call['argv'])
            self.assertIn('short', call['argv'])
            for stream in ('stdout', 'stderr'):
                self.assertIn(f'fake Dune {stream}: {call["argv"][0]}', result.stderr)
        self.assertIn('-j4', calls[0]['argv'])
        self.assertIn('--only-packages', calls[0]['argv'])
        self.assertIn('@install', calls[0]['argv'])
        self.assertIn('--no-buffer', calls[1]['argv'])
        self.assertIn('-j1', calls[1]['argv'])
        self.assertEqual(calls[2]['argv'][-3:],
                         ['--prefix', str(self.root / 'install prefix'), 'hardcaml_asic'])
        self.assertEqual(result.stderr.count('[ASIC DONE]'), 3)
        self.assertIn('[ASIC COMPLETE]', result.stderr)
        self.assertIn('elapsed)', result.stderr)
        self.assertNotIn('\x1b', result.stderr)
        self.assertFalse((self.root / 'install prefix').exists())

    def test_verbose_and_worker_override(self):
        self.env['HARDCAML_ASIC_TEST_WORKERS'] = '3'
        result = self.invoke('--verbose')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('result-store workers: 3', result.stderr)
        self.assertIn('+ ', result.stderr)
        for call in self.calls():
            self.assertIn('verbose', call['argv'])
            self.assertEqual(call['workers'], '3')

    def test_stage_failures_stop_pipeline(self):
        self.env['FAKE_MODE'] = 'failure'
        for index, stage in enumerate(('build', 'runtest', 'install')):
            with self.subTest(stage=stage):
                (self.root / 'calls').unlink(missing_ok=True)
                self.env['FAKE_FAIL_STAGE'] = stage
                result = self.invoke()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(len(self.calls()), index + 1)
                self.assertIn('[ASIC FAILED]', result.stderr)
                self.assertIn('23', result.stderr)
                self.assertNotIn('[ASIC COMPLETE]', result.stderr)

    def test_spawn_failure(self):
        self.dune.unlink()
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn('[ASIC FAILED] 1/3 Build', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_prefix_is_required(self):
        result = subprocess.run(self.command[:3], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_live_heartbeat(self):
        # Shorten only the reporting interval; exercise the real run/wait loop.
        self.env['FAKE_MODE'] = 'slow'
        loader = ('import runpy,sys; m=runpy.run_path(sys.argv.pop(1)); '
                  'm["main"].__globals__["HEARTBEAT_SECONDS"]=0.1; '
                  'sys.exit(m["main"]())')
        command = [sys.executable, '-B', '-c', loader, str(INSTALLER), *self.command[3:]]
        with subprocess.Popen(command, env=self.env, cwd=self.root, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            # Reading the flushed heartbeat before exit proves this isn't just
            # an elapsed-time summary printed at the end of a captured command.
            import select
            deadline = time.monotonic() + 5
            lines = []
            while time.monotonic() < deadline:
                ready, _, _ = select.select([process.stderr], [], [], 1)
                if ready:
                    line = process.stderr.readline()
                    lines.append(line)
                    if '[ASIC WORKING]' in line:
                        self.assertIsNone(process.poll())
                        break
            else:
                self.fail('No live heartbeat: ' + ''.join(lines))
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(stdout, '')
            self.assertEqual(process.returncode, 0, stderr)

    def assert_stopped(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            # Orphans can briefly remain zombies until the host init reaps them.
            stat = Path(f'/proc/{pid}/stat')
            try:
                if stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    return
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        self.fail(f'Process {pid} survived installer cleanup')

    def tree_case(self, mode, signum=None, parent_group=False):
        self.env['FAKE_MODE'] = mode
        command = self.command
        if parent_group:
            # Model a caller sharing a group with the captured installer. The
            # caller survives group cancellation long enough to collect output.
            wrapper = ('import signal,subprocess,sys; '
                       'signal.signal(signal.SIGINT,lambda *_:None); '
                       'signal.signal(signal.SIGTERM,lambda *_:None); '
                       'sys.exit(subprocess.call(sys.argv[1:]))')
            command = [sys.executable, '-c', wrapper, *command]
        process = subprocess.Popen(command, env=self.env, cwd=self.root,
                                   start_new_session=parent_group, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pids = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    pids = json.loads((self.root / 'ready').read_text())
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(0.01)
            self.assertIsNotNone(pids, 'Fake Dune tree did not start')
            if signum is not None:
                if parent_group:
                    os.killpg(process.pid, signum)
                else:
                    os.kill(process.pid, signum)
                    # A second cancellation must not interrupt cleanup.
                    time.sleep(0.15)
                    if process.poll() is None:
                        os.kill(process.pid, signum)
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(stdout, '')
            self.assertEqual(process.returncode, 128 + signum if signum else 1, stderr)
            self.assertEqual(len(self.calls()), 1)
            self.assertNotIn('[ASIC COMPLETE]', stderr)
            self.assertIn('[ASIC CANCELLED]' if signum else '[ASIC FAILED]', stderr)
            self.assert_stopped(pids['leader'])
            self.assert_stopped(pids['child'])
            forwarded = int((self.root / 'signal').read_text())
            self.assertEqual(forwarded, signum or signal.SIGTERM)
        finally:
            if pids:
                try:
                    os.killpg(pids['group'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_sigint_cleans_grandchild(self):
        self.tree_case('tree', signal.SIGINT)

    def test_sigterm_kills_stubborn_grandchild_after_leader_exit(self):
        self.tree_case('stubborn', signal.SIGTERM)

    def test_captured_parent_group_cancellation(self):
        self.tree_case('tree', signal.SIGTERM, parent_group=True)

    def test_failed_leader_cleans_orphan(self):
        self.tree_case('orphan-failure')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, default=INSTALLER)
    args, rest = parser.parse_known_args()
    INSTALLER = args.installer.resolve()
    unittest.main(argv=[sys.argv[0], *rest])
