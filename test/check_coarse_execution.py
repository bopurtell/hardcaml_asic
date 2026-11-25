"""Current Python aggregate oracle against installed typed native orchestration."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock


def suite(consumer, package):
    sys.path.insert(0, str(package.parent))
    from hardcaml_asic_flow import cli, coarse_flow_oracle, report, runner
    count = 0
    for mode in ('none', 'archive-reject', 'run-failed', 'run-interrupted',
                 'run-failed-collect', 'run-interrupted-collect', 'fail-run', 'fail-postcheck', 'fail-collect',
                 'fail-report', 'fail-archive', 'malformed-report', 'acceptance-fails',
                 'retained-error', 'retained-interrupted',
                 *('int-'+s for s in ('run', 'postcheck', 'collect', 'report', 'archive')),
                 *('term-'+s for s in ('run', 'postcheck', 'collect', 'report', 'archive'))):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as tmp:
            root = Path(tmp)
            source = root/'source'; flow = source/'project/runs/asic'
            (flow/'06-yosys-synthesis/reports').mkdir(parents=True)
            (source/'project/manifest.json').write_text('{}')
            (flow/'06-yosys-synthesis/reports/stat.json').write_text(json.dumps({
                'design': {'area': 10, 'num_cells': 2, 'num_cells_by_type': {}}}))
            (flow/'06-yosys-synthesis/state_out.json').write_text(json.dumps({'metrics': {
                'design__instance_unmapped__count': 0, 'synthesis__check_error__count': 0,
                'design__inferred_latch__count': 0}}))
            full = mode.endswith('postcheck')
            status = 'failed' if mode.startswith('run-failed') else 'interrupted' if mode.startswith('run-interrupted') else 'completed'
            record = dict(run_id='a'*32, build_identity='fixture', status=status,
                          requested_stage='full' if full else 'synthesis',
                          completed_stage='full' if full else 'synthesis', bundle=str(root/'bundle'),
                          outputs={'flow': str(flow)}, environment={})
            if mode == 'acceptance-fails':
                (flow/'06-yosys-synthesis/reports/stat.json').write_text('{}')
            (source/'run.json').write_text(json.dumps(record))
            (root/'record.json').write_text(json.dumps(record))
            actual = subprocess.run([str(consumer), str(root), mode], text=True, capture_output=True)
            run_dir=root/'runs/19700101T000000000000Z_aaaaaaaa'
            archive=None if mode=='none' else root/'archive'
            def boundary(name):
                if mode=='fail-'+name or mode in ('run-failed-collect','run-interrupted-collect') and name=='collect':
                    raise ValueError(name+' fixture failure')
                if mode in ('int-'+name, 'term-'+name):
                    raise coarse_flow_oracle.FlowInterrupted(2 if mode.startswith('int') else 15)
            class Operations:
                def run(self, args):
                    # Native callback-boundary interruption retains allocation;
                    # the physical run service returns that saved outcome.
                    if mode=='fail-run': boundary('run')
                    return coarse_flow_oracle.RunOutcome('a'*32, run_dir, run_dir/'run.json', status,
                        1 if status=='failed' else 130 if status=='interrupted' else 0)
                def postcheck(self, args): boundary('postcheck'); raise ValueError('postcheck fixture failure')
                def collect(self, args): boundary('collect'); return 0
                def report(self, args):
                    boundary('report')
                    return 2 if mode=='malformed-report' else report.report(source, coarse_flow_oracle.collect(source), 'fixture', output=lambda _: None)
                def archive(self, args): boundary('archive'); return 2
            args=SimpleNamespace(stage='full' if full else 'synthesis', archive=archive, force=False,
                                 json=True, verbose=False)
            stdout, stderr=io.StringIO(),io.StringIO()
            ticks=iter(i*.125 for i in range(100))
            original=runner.execute
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr),mock.patch.object(
                    runner,'execute',side_effect=lambda *a,**kw: original(*a,**kw,clock=lambda: next(ticks))):
                try: code=cli.execute_sequence(args,Operations())
                except (ValueError, coarse_flow_oracle.FlowInterrupted): code=2
            if mode=='fail-run':
                assert actual.returncode==2 and not actual.stdout and not run_dir.exists()
                continue
            value=json.loads(actual.stdout)
            if mode.startswith('retained-'):
                assert value['exit_code']==(143 if mode=='retained-interrupted' else 2)
                assert [s['operation'] for s in value['steps']]==['run','collect']
                assert value['steps'][1]['best_effort'] and (run_dir/'results.json').is_file()
            elif mode in ('int-run','term-run'):
                assert value['exit_code']==(130 if mode.startswith('int') else 143)
                assert [s['operation'] for s in value['steps']]==['run','collect']
            else:
                expected=json.loads(stdout.getvalue())
                assert value==expected,(mode,value,expected,actual.stderr)
                assert actual.returncode==code
            if value['exit_code'] or archive is None:
                assert not (root/'called-archive').exists() or mode in ('archive-reject','fail-archive','int-archive','term-archive')
            assert not (run_dir/'journal.jsonl').exists()
            count+=1
    print(f'{count} native/current Python aggregate lifecycle differentials and cancellation boundaries')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--consumer',required=True);p.add_argument('--package',default='flow/hardcaml_asic_flow')
    a=p.parse_args();suite(Path(a.consumer).resolve(),Path(a.package).resolve())
