"""Actual Python journal writer/reducer/query differential; historical IO is read-only."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from reference_harness import observation_description

sys.dont_write_bytecode = True
RETAINED = [Path('/tmp/opencode/backend-v1-integration/results/observations'),
            Path('/home/wayne/devel/jane/hardcaml_asic_example/_flow_results/observations')]


def verify(cli, consumer, flow, root, historical=False):
    sys.path.insert(0, str(flow))
    from hardcaml_asic_flow import flow_observation as py, execution_plan as plans
    counts = dict(accepted=0, rejected=0, queries=0, incremental=0, historical=0)
    env = dict(os.environ, PATH='/unusable', PYTHONPATH='/unusable')

    def query(*args, expected=None, code=None):
        r = subprocess.run([str(cli), *map(str, args)], cwd=root, env=env,
                           capture_output=True, text=True, timeout=10)
        value = json.loads(r.stdout)
        if args[0] == 'describe' and expected is not None:
            expected = observation_description(expected)
        if code:
            assert r.returncode == 2 and value['error']['code'] == code, (args, r, value)
        else:
            assert r.returncode == 0 and not r.stderr and value == expected, (args, r, expected)
            assert r.stdout == py.canonical(expected) + '\n'
        counts['queries'] += 1
        return value

    class Driver:
        def __enter__(self):
            self.p = subprocess.Popen([str(consumer)], cwd=root, env=env, text=True,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return self

        def call(self, **q):
            self.p.stdin.write(json.dumps(q, allow_nan=False) + '\n')
            self.p.stdin.flush()
            line = self.p.stdout.readline()
            assert line, (q, self.p.poll(), self.p.stderr.read())
            return json.loads(line)

        def __exit__(self, *args):
            self.p.stdin.close()
            self.p.wait(timeout=10)
            stderr = self.p.stderr.read()
            assert self.p.returncode == 0 and not stderr, stderr

    def sign(value):
        value['sha256'] = py.digest({k: v for k, v in value.items() if k != 'sha256'})
        return value

    class Adapter:
        runtime_record = None
        def validate(self, plan):
            pass
        def invoke(self, action, **kwargs):
            return SimpleNamespace(status='completed', exit_code=0)
        def publish(self, action, outcome):
            boundary = action.key.removeprefix('implementation.')
            return dict(path=str(root / 'recorded-checkpoints' / boundary), identity='a' * 64,
                        boundary=boundary, **{k:v for k,v in plans.boundary_capabilities()[boundary].items()
                                              if k not in ('endpoint', 'successor')})

    query('describe', expected=py.describe())
    help_result = subprocess.run([str(cli), '--help'], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0 and 'read-updates' in help_result.stdout and not help_result.stderr
    empty = root / 'empty'
    empty.mkdir()
    query('inspect', empty, code='INVALID_RECORD')
    query('inspect', root / 'missing', code='INVALID_RECORD')
    writers = []
    fixtures = []
    # Actual production Writer/Runner, with only backend operations replaced.
    for i, plan in enumerate([*(plans.resolve_stages(s) for s in plans.STAGE_ORDER),
                              plans.resolve_stages('signoff', resume=True),
                              plans.resolve_sequence(['placement', 'cts'], initial_boundary='floorplan')]):
        w = py.Writer(root / 'journals', workspace=root / f'work-{i}',
                      checkpoint_store=root / 'cp', request={'fixture': 'λ😀', 'number': 1.25})
        execution = w.run(lambda: plan, Adapter)
        assert execution.status == 'completed' and len(execution.artifacts) == len(plan.actions)
        w.close()
        writers.append(w)
        fixtures.append(py.load(w.directory)[1])
        if i == 6:
            final = py.inspect(w.directory)['final_results']
            assert final['physical_completion'] == 'completed' and final['acceptance'] == 'not_evaluated'
            assert final['external_postcheck'] == 'not_evaluated' and len(final['checkpoint_refs']) == 7
        query('inspect', w.directory, expected=py.inspect(w.directory))
        for after in range(w.sequence + 1):
            query('read-updates', w.directory, '--stream-id', w.stream_id, '--after', after,
                  expected=py.read_updates(w.directory, stream_id=w.stream_id, after=after))
    # Legacy fixtures are converted only in a new disposable directory.
    legacy = root / 'schema 1 journal'
    legacy.mkdir()
    previous = None
    for e in fixtures[-1]:
        e = copy.deepcopy(e)
        e.pop('recorded_at')
        e.pop('elapsed_seconds')
        e.update(schema_version=1, previous_sha256=previous)
        sign(e)
        previous = e['sha256']
        (legacy / f"{e['sequence']:012d}.json").write_text(py.canonical(e))
    query('inspect', legacy, expected=py.inspect(legacy))
    query('read-updates', legacy, '--stream-id', fixtures[-1][0]['stream_id'], '--after', 2,
          expected=py.read_updates(legacy, stream_id=fixtures[-1][0]['stream_id'], after=2))
    fixtures.append(py.load(legacy)[1])

    with Driver() as d:
        def check(events):
            d.call(op='reset')
            state = stream = previous = schema = elapsed = None
            for seq, value in enumerate(events, 1):
                try:
                    state, stream = py.validate_record(value, sequence=seq, stream=stream,
                        previous=previous, state=state, schema_version=schema, previous_elapsed=elapsed)
                    previous, schema, elapsed = value['sha256'], value['schema_version'], value.get('elapsed_seconds')
                except (ValueError, TypeError, KeyError, OverflowError):
                    expected = None
                else:
                    expected = py.observation_snapshot(state, seq, None, schema_version=schema)
                actual = d.call(op='append', record=value, active=None)
                if expected is None:
                    assert actual['error']['code'] == 'INVALID_RECORD', (value, actual)
                    counts['rejected'] += 1
                    return
                assert actual == {'ok': expected}, (value, actual, expected)
            counts['accepted'] += 1
            for after in (0, len(events)):
                expected = dict(contract=py.CONTRACT, schema_version=schema, stream_id=stream,
                                after=after, cursor=len(events), events=events[after:])
                assert d.call(op='updates', stream=stream, after=after) == {'ok': expected}

        for events in fixtures:
            # Every prefix is a fact snapshot, not implicit terminal success.
            for length in range(1, len(events) + 1):
                check(events[:length])
        for index in range(1, len(fixtures[-1])):
            # Legacy timestamps are absent, never ignored or synthesized.
            value = copy.deepcopy(fixtures[-1][index])
            value['recorded_at'] = '2026-01-01T00:00:00.000000Z'
            check(fixtures[-1][:index] + [sign(value)])
        base = fixtures[1]
        # A terminal synthesis publication and a physical SRAM publication have
        # narrower capabilities than a generic synthesis checkpoint. Re-sign the
        # event so we exercise lifecycle semantics, not merely hash rejection.
        index = next(i for i, event in enumerate(base) if event["event"] == "published")
        for supported, targets, counter in (
                (False, [], "accepted"), (True, ["floorplan"], "accepted"),
                (True, [], "rejected"), (False, ["floorplan"], "rejected"),
                (True, ["placement"], "rejected")):
            event = copy.deepcopy(base[index])
            event["data"]["artifact"].update(restart_supported=supported, restart_targets=targets)
            before = counts[counter]
            check(base[:index] + [sign(event)])
            assert counts[counter] == before + 1
        replacements = [None, True, False, 0, -1, 1.0, '', 'wrong', [], {}]
        # Re-sign each malformed candidate so facts are checked beyond hashing.
        for index, record in enumerate(base):
            for field in record:
                for replacement in replacements:
                    value = copy.deepcopy(record)
                    value[field] = replacement
                    check(base[:index] + [sign(value)])
                value = copy.deepcopy(record)
                value.pop(field)
                if field != 'sha256':
                    sign(value)
                check(base[:index] + [value])
            for field in record['data']:
                for replacement in replacements:
                    value = copy.deepcopy(record)
                    value['data'][field] = replacement
                    check(base[:index] + [sign(value)])
            for nested in ('occurrence', 'artifact'):
                if isinstance(record['data'].get(nested), dict):
                    for field in record['data'][nested]:
                        for replacement in replacements:
                            value = copy.deepcopy(record)
                            value['data'][nested][field] = replacement
                            check(base[:index] + [sign(value)])
                        value = copy.deepcopy(record)
                        value['data'][nested].pop(field)
                        check(base[:index] + [sign(value)])
            value = copy.deepcopy(record)
            value['unknown'] = None
            check(base[:index] + [sign(value)])
            value = copy.deepcopy(record)
            value['sha256'] = '0' * 64
            check(base[:index] + [value])
        for stamp in ('0001-01-01T00:00:00.000000Z', '2024-02-29T23:59:59.999999Z',
                      '0000-01-01T00:00:00.000000Z', '2026-02-29T00:00:00.000000Z',
                      '2026-02-30T00:00:00.000000Z', '2026-01-01T24:00:00.000000Z',
                      '2026-01-01T00:00:60.000000Z', '2026-01-01T00:00:00.000000+00:00',
                      '2026-01-01T00:00:00.000000Z\n'):
            check([sign(dict(base[0], recorded_at=stamp))])
        for elapsed in (-1, -0.0, 0, 1e-300, 1e300, 10**400):
            check([sign(dict(base[0], elapsed_seconds=elapsed))])
        for index in (1, len(base)-1):
            check(base[:index] + [sign(dict(base[index], elapsed_seconds=0))])
            mixed = {k:v for k,v in dict(base[index], schema_version=1).items()
                     if k not in ('recorded_at', 'elapsed_seconds')}
            check(base[:index] + [sign(mixed)])
        # Wall time may regress; elapsed may be equal; neither affects ordering.
        chain = copy.deepcopy(base)
        previous = None
        for i,e in enumerate(chain):
            e.update(recorded_at=f'2026-01-01T00:00:{59-i:02d}.000000Z', elapsed_seconds=1,
                     previous_sha256=previous)
            sign(e)
            previous = e['sha256']
        check(chain)
        # Preflight failure before planning, arbitrary integer code, retained
        # publication without finish, and failed outer outcome after physical success.
        for status, code in [('failed', 2), ('interrupted', 143), ('failed', 10**100)]:
            e = copy.deepcopy(base[1])
            e.update(event='outcome', data=dict(status=status, exit_code=code, error='fixture'))
            check(base[:1] + [sign(e)])
        e = copy.deepcopy(fixtures[5][-1])
        e['data'] = dict(status='failed', exit_code=2, error='terminal save failure')
        check(fixtures[5][:-1] + [sign(e)])
        assert d.call(op='state')['ok']['outcome']['status'] == 'failed'
        check(base + [sign(dict(base[-1], sequence=len(base)+1, previous_sha256=base[-1]['sha256']))])
        # Real nonterminal failures and interruptions preserve later unreached
        # actions, and cannot be substituted for publication/earned completion.
        for status, code in [('failed', 9), ('interrupted', 143)]:
            e = copy.deepcopy(base[5])
            e['previous_sha256'] = base[3]['sha256']
            e['sequence'] = 5
            e['data']['occurrence'].update(status=status, exit_code=code, error='fixture')
            e['data']['artifact'] = None
            sign(e)
            tail = copy.deepcopy(base[-1])
            tail.update(sequence=6, previous_sha256=e['sha256'],
                        data=dict(status=status, exit_code=code, error=None))
            check(base[:4] + [e, sign(tail)])
        # The second action cannot start before the first finishes.
        e = copy.deepcopy(base[6])
        e.update(sequence=5, previous_sha256=base[3]['sha256'])
        check(base[:4] + [sign(e)])
        # A second publication of the same boundary is corruption.
        e = copy.deepcopy(base[4])
        e.update(sequence=6, previous_sha256=base[4]['sha256'])
        check(base[:5] + [sign(e)])

    w = py.Writer(root / 'live', workspace=root / 'live-work', checkpoint_store=root / 'cp', request={})
    try:
        query('inspect', w.directory, expected=py.inspect(w.directory))
        assert py.inspect(w.directory)['writer_active'] is True
        query('read-updates', w.directory, '--stream-id', 'wrong', code='RESET_REQUIRED')
        for after in ('-1', '2', 'true', '1.0', '999999999999999999999999999999999999'):
            query('read-updates', w.directory, '--stream-id', w.stream_id, '--after', after,
                  code='RESET_REQUIRED')
        query('read-updates', w.directory, code='RESET_REQUIRED')
        with Driver() as d:
            for stream, after in [(None, 0), (w.stream_id, None), ('wrong', 0), (w.stream_id, -1)]:
                assert d.call(op='open', directory=str(w.directory), stream=stream, after=after)['error']['code'] == 'RESET_REQUIRED'
            assert 'ok' in d.call(op='open', directory=str(w.directory), stream=w.stream_id, after=1)
            pyreader = py.IncrementalReader(w.directory, stream_id=w.stream_id, after=1)
            def poll():
                expected = pyreader.poll()
                assert d.call(op='poll') == {'ok': dict(updates=expected[0], status=expected[1])}
                counts['incremental'] += 1
            for _ in range(5):
                poll()
            (w.directory / '.000000000002.json.tmp').write_text('{')
            poll()
            w.append('planned', {'plan': plans.resolve_stages('floorplan').as_dict()})
            poll()
            w.append('prepared', {'runtime_record': None})
            poll()
            w.close()
            poll()
            assert py.inspect(w.directory)['observation_status'] == 'writer_lost_or_unknown'
        (w.directory / 'writer.lock').unlink()
        query('inspect', w.directory, expected=py.inspect(w.directory))
        assert py.inspect(w.directory)['writer_active'] is None
    finally:
        w.close()

    # Filesystem errors: temporary names ignored; committed malformed records,
    # nonregular entries, gaps and altered/deleted/replaced prefixes fail closed.
    source = writers[0].directory
    for mode in ('changed', 'deleted', 'replaced', 'symlink', 'directory', 'gap', 'badname',
                 'truncated', 'duplicate', 'overflow', 'nonfinite', 'invalid_utf8'):
        directory = root / ('corrupt-' + mode)
        shutil.copytree(source, directory)
        before = {p.name:p.read_bytes() for p in directory.iterdir()}
        with Driver() as d:
            assert 'ok' in d.call(op='open', directory=str(directory), stream=None, after=None)
            target = directory / '000000000001.json'
            if mode == 'changed':
                target.write_bytes(target.read_bytes() + b' ')
            elif mode == 'deleted':
                target.unlink()
            elif mode == 'replaced':
                tmp = directory / '.replacement'
                tmp.write_bytes(target.read_bytes())
                tmp.replace(target)
            elif mode in ('symlink', 'directory'):
                target.unlink()
                target.symlink_to(source / target.name) if mode == 'symlink' else target.mkdir()
            else:
                tail = directory / f'{len(fixtures[0])+1:012d}.json'
                if mode == 'gap':
                    tail = directory / f'{len(fixtures[0])+2:012d}.json'
                elif mode == 'badname':
                    tail = directory / '9-bad.json'
                raw = {'truncated': b'{', 'duplicate': b'{"x":1,"x":2}', 'overflow': b'{"x":1e999}',
                       'nonfinite': b'{"x":NaN}', 'invalid_utf8': b'"\xff"'}.get(mode, b'{}')
                tail.write_bytes(raw)
            if mode != 'replaced':
                # Replacing identical bytes is a warm-reader error; a new cold
                # reader legitimately accepts those bytes as a fresh prefix.
                query('inspect', directory, code='INVALID_RECORD') if mode != 'changed' else query('inspect', directory, expected=py.inspect(directory))
            assert d.call(op='poll')['error']['code'] == 'INVALID_RECORD'
            # A failed reader remains poisoned even after the caller repairs its
            # own disposable fixture; it must not silently continue/recover.
            if target.is_dir():
                target.rmdir()
            elif target.is_symlink():
                target.unlink()
            for p in directory.glob('*.json'):
                if p.name not in before:
                    p.unlink()
            for name, raw in before.items():
                (directory / name).write_bytes(raw)
            assert d.call(op='poll')['error']['code'] == 'INVALID_RECORD'
            counts['incremental'] += 1
        query('inspect', directory, expected=py.inspect(directory))
    # Verify content work at the syscall boundary: open/read once at attachment,
    # metadata-only idle polls, one additional record open after actual append.
    # This is a test-only tracing process, not a live-watch command.
    strace = shutil.which('strace')
    if strace:
        trace = root / 'reader-syscalls.log'
        live = py.Writer(root / 'traced', workspace=root / 'trace-work', checkpoint_store=root / 'cp', request={})
        p = subprocess.Popen([strace, '-f', '-e', 'trace=openat', '-o', str(trace), str(consumer)],
                             cwd=root, env=env, text=True, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def traced(**q):
            p.stdin.write(json.dumps(q) + '\n')
            p.stdin.flush()
            assert 'ok' in json.loads(p.stdout.readline())
        try:
            traced(op='open', directory=str(live.directory), stream=None, after=None)
            for _ in range(5):
                traced(op='poll')
            live.append('outcome', dict(status='failed', exit_code=2, error=None))
            live.close()
            traced(op='poll')
            traced(op='poll')
            p.stdin.close()
            assert p.wait(timeout=10) == 0, p.stderr.read()
            lines = trace.read_text().splitlines()
            for seq in (1, 2):
                name = str(live.directory / f'{seq:012d}.json')
                assert sum(name in line for line in lines) == 1, (name, lines)
            counts['incremental'] += 1
        finally:
            live.close()
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)
    for raw in ('{', '{"x":1,"x":2}', '{"x":1e999}', '{"x":NaN}'):
        directory = root / 'torn'
        directory.mkdir(exist_ok=True)
        (directory / '000000000001.json').write_text(raw)
        query('inspect', directory, code='INVALID_RECORD')
    if historical:
        for retained in RETAINED:
            if retained.is_relative_to('/tmp/opencode') and not retained.exists():
                print(f'SKIP optional retained observations: {retained}')
                continue
            files = sorted(retained.glob('**/observations/*/000000000001.json'))
            if retained.name == 'observations':
                files = sorted(retained.glob('*/000000000001.json'))
            assert files, f'missing retained observations: {retained}'
            for first in files:
                directory = first.parent
                before = {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
                expected = py.inspect(directory)
                query('inspect', directory, expected=expected)
                for after in (0, expected['cursor']//2, expected['cursor']):
                    query('read-updates', directory, '--stream-id', expected['stream_id'], '--after', after,
                          expected=py.read_updates(directory, stream_id=expected['stream_id'], after=after))
                assert before == {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
                counts['historical'] += 1
    print('observation reader/reducer:', counts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli', type=Path, default=Path('_build/default/bin/experimental_observation_read.exe'))
    parser.add_argument('--consumer', type=Path, default=Path('_build/default/test/observation_consumer.exe'))
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--historical', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='observation-reader-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        if not args.installed:
            verify(args.cli.resolve(), args.consumer.resolve(), repo / 'flow', root, args.historical)
            return
        prefix = root / 'prefix'
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer = root / 'consumer'
        consumer.mkdir()
        shutil.copyfile(repo / 'test/observation_consumer.ml', consumer / 'observation_consumer.ml')
        (consumer / 'dune-project').write_text('(lang dune 3.17)\n(name installed_observation_consumer)\n')
        (consumer / 'dune').write_text('(executable (name observation_consumer) (libraries hardcaml_asic.execution))\n')
        env = dict(os.environ)
        env['OCAMLPATH'] = str(prefix / 'lib') + (':' + env['OCAMLPATH'] if env.get('OCAMLPATH') else '')
        subprocess.run(['dune', 'build', '-j20', 'observation_consumer.exe'], cwd=consumer, env=env, check=True)
        auditing = True
        def audit(event, values):
            if not auditing:
                return
            if event == 'open' and isinstance(values[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(values[0])).resolve()
                if path.is_relative_to(repo):
                    raise AssertionError(f'checkout read in installed observation test: {path}')
            if event == 'import' and (values[0].startswith('librelane') or values[0].endswith('librelane_bridge')):
                raise AssertionError(f'backend import: {values[0]}')
        sys.addaudithook(audit)
        try:
            verify(prefix / 'bin/hardcaml-asic-experimental-observation-read',
                   consumer / '_build/default/observation_consumer.exe',
                   prefix / 'lib/hardcaml_asic/flow', root, args.historical)
        finally:
            auditing = False
        print('installed observation API/command passed outside checkout with checkout opens denied')


if __name__ == '__main__':
    main()
