"""Actual Python collector differentials and installed/public bounded checks."""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import random
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from resource_evidence_compat import collection_expected

RETAINED = Path('/tmp/opencode/native-final-integration/finalizations/20261014T040711301534Z_913453bd')


def suite(cli, consumer, package, fixtures, public=None, retained=RETAINED):
    sys.path.insert(0, str(package.parent))
    sys.path.insert(0, str(fixtures.parent))
    from hardcaml_asic_flow import coarse_flow_oracle, cli as public_cli, ocaml_backend, report
    import test_coarse_flow
    original = coarse_flow_oracle.collect
    count = 0

    def invoke(request):
        result = subprocess.run([str(consumer)], input=json.dumps(request)+'\n',
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, (request, result.stdout, result.stderr)
        return result.stdout

    def compare(run, *, context=None, synthesis_state=None):
        nonlocal count
        expected = collection_expected(original(run, context=context, synthesis_state=synthesis_state))
        actual = invoke(dict(run_dir=str(run), context=context,
                             synthesis_state=str(synthesis_state) if synthesis_state else None))
        expected_bytes = json.dumps(expected, indent=2, sort_keys=True)+'\n'
        assert actual == expected_bytes, '\n'.join(__import__('difflib').unified_diff(
            expected_bytes.splitlines(), actual.splitlines(), fromfile='Python', tofile='OCaml'))
        count += 1
        return expected

    # Retained coarse_flow_oracle fixtures execute unchanged, substituting only a differential
    # observer around the REAL Python implementation.
    with mock.patch.object(coarse_flow_oracle, 'collect', side_effect=compare):
        tests = unittest.defaultTestLoader.loadTestsFromTestCase(test_coarse_flow.CollectorTest)
        result = unittest.TextTestRunner().run(tests)
        assert result.wasSuccessful()

    with tempfile.TemporaryDirectory(prefix='collect-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        run = root/'run with 空間'; flow = run/'project/runs/asic'
        flow.mkdir(parents=True)
        record = dict(run_id='explicit-attempt', build_identity='build', status='failed',
                      requested_stage='full', completed_stage=None,
                      outputs={'flow': 'project/runs/asic'}, execution_mode='dockerized',
                      environment={'actual': {'librelane': 'host'}, 'runtime_contract': {
                          'observations': {'container': {'librelane_version': 'container'}}}})
        (run/'run.json').write_text(json.dumps(record))
        manifest = run/'project/manifest.json'
        manifest.write_text(json.dumps({'target': {'corner': 'nominal Ω'}}))
        stat = flow/'06-yosys-synthesis/reports/stat.json'; stat.parent.mkdir(parents=True)
        state = flow/'06-yosys-synthesis/state_out.json'
        metrics = flow/'final/metrics.csv'; metrics.parent.mkdir(parents=True)

        def write_csv(rows):
            with metrics.open('w', newline='') as stream:
                csv.writer(stream).writerows(rows)

        for raw in [None, True, False, 0, -1, 2.5, 9007199254740993,
                    9007199254740995, 10**100, 1.7976931348623157e308,
                    float('nan'), float('inf'), float('-inf'), '', '1.5', 'bad Ω', {}, []]:
            stat.write_text(json.dumps({'creator': 'Yosys Ω', 'design': {'area': raw,
                'sequential_area': raw, 'num_cells': raw, 'num_cells_by_type': {'布尔': True, 'huge': 10**100}}}))
            state.write_text(json.dumps({'metrics': {'design__instance_unmapped__count': raw,
                'synthesis__check_error__count': raw, 'design__inferred_latch__count': raw}}))
            write_csv([['timing__setup__ws__corner:slow,"Ω\nline', '0.2'],
                       ['timing__setup__ws', '-0.5'], ['timing__hold__ws__corner:fast', '1e39'],
                       ['design__instance__area__stdcell', '3.14'], ['design__instance__utilization__stdcell', '.5'],
                       ['route__drc_errors', '0'], ['magic__drc_error__count', '0'],
                       ['design__lvs_error__count', '0'], ['antenna__violating__nets', '0'],
                       ['antenna__violating__pins', '0']])
            compare(run)
        for token in ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999']:
            stat.write_text('{"design":{"area":'+token+',"num_cells":1}}')
            state.write_text('{"metrics":{"design__instance_unmapped__count":'+token+'}}')
            write_csv([['timing__setup__ws__corner:bad', token], ['timing__hold__ws', token]])
            compare(run)
        for contents in ['', '\n', 'a,b,c\r\n', '"x,y",2\r\n', '"timing__setup__ws__corner:Ω",.25\r\n',
                         'timing__setup__ws,0.1\ntiming__setup__ws,-0.2\n',
                         '"timing__setup__ws",0.1\n"timing__hold__ws",0.2\n',
                         '"unterminated,field', 'a,"quoted"tail\n']:
            metrics.write_text(contents, newline='')
            compare(run)
        for contents in ['{}', '{"design":[]}', '{"design":null}', '{bad', '{"design":{}}',
                         '{"design":{"area":1,"area":2}}', '[]', 'null', 'false', '1.0', '2', '"text"']:
            stat.write_text(contents)
            compare(run)
        for contents in ['{}', '[]', 'null', 'false', '1.0', '2', '"text"', '{bad']:
            state.write_text(contents)
            compare(run)
        state.write_text('{"metrics":{}}')
        rng = random.Random(714)
        for bits in [53, 54, 55, 100, 500, 1000]:
            for _ in range(4):
                value = rng.getrandbits(bits)
                for signed in (value, -value):
                    stat.write_text(json.dumps({'design':{'area':signed,'num_cells':signed}}))
                    compare(run)
        for raw in ['inf','+Infinity','-inf','nan','NAN','NaN','1_0','1__0','_1','1_',
                    '1.0_5','1e+2','  1.25  ','0x1p2','-0.0','1e-999','1.7976931348623159e308']:
            write_csv([['timing__setup__ws',raw],['timing__hold__ws','0']])
            compare(run)
        # Timing all-evidence worst-corner policy and synthesis-state tie behavior.
        segmented = flow/'synthesis-state.json'
        segmented.write_text('{"metrics":{"design__instance__area":12,"design__instance__count":2}}')
        for rows in [[], [['timing__setup__ws','-.2'],['timing__setup__ws__corner:real','-.2'],['timing__hold__ws','0']],
                     [['timing__setup__ws','-.3'],['timing__setup__ws__corner:real','-.2'],['timing__hold__ws','0']],
                     [['timing__setup__ws','1e30'],['timing__hold__ws','-1e30']],
                     [['timing__setup__ws','bad'],['timing__setup__ws__corner:real','0.2'],['timing__hold__ws','0']]]:
            write_csv(rows)
            compare(run, context=record, synthesis_state=segmented)
        # Only the documented raw artifacts, not logs or unrelated files.
        for relative in ['08-sta/reports/a.rpt', '08-sta/reports/a.json', '08-sta/reports/raw.log',
                         'final/gds/top.gds', 'final/nl/top.v', 'final/metrics.json']:
            p = flow/relative; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('opaque raw bytes')
        for name in ['z-last', 'a-first', 'Ω-middle']:
            p = run/'checks'/name/'postcheck.json'; p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'check_id':name, 'status':'failed', 'reason':False, 'empty':'',
                                     'raw':float('inf'), 'count':10**100}))
        expected = compare(run)
        assert expected['postchecks'][0]['check_id'] == 'a-first'
        # Context is in-memory, not required to match run.json, or require it.
        (run/'run.json').unlink()
        context = dict(record, run_id='context-only')
        compare(run, context=context, synthesis_state=segmented)
        (run/'run.json').write_text(json.dumps(record))

        # Typed metric constructor preserves false/empty optional reasons and bools.
        for value in [None, False, True, 1, 1.0, float('nan'), 10**100]:
            for reason in [None, False, '', 'absent']:
                actual=invoke(dict(operation='metric', value=value, reason=reason))
                expected=coarse_flow_oracle.metric(value,'count','fixture','fixture','fixture',reason=reason)
                assert actual == json.dumps(expected, indent=2, sort_keys=True)+'\n'
        for raw in ['{"n":NaN,"i":Infinity,"m":-Infinity,"e":1e999,"b":true,"h":'+str(10**100)+'}',
                    '{"dup":1,"dup":2}', '{"x":"Ω\\nquoted\\\""}', '[1,1.0,-0.0]']:
            assert invoke(dict(operation='decode', raw=raw)) == json.dumps(json.loads(raw),indent=2,sort_keys=True)+'\n'

        # Required bad records fail before replacing an existing result.
        destination = run/'results.json'; destination.write_text('prior result')
        def call(*args):
            return subprocess.run([str(cli),'--run-dir',str(run),*args],text=True,capture_output=True)
        for bad in [run/'checks/a-first/postcheck.json', manifest, run/'run.json']:
            old=bad.read_bytes(); bad.write_text('{bad')
            result=call('--json'); assert result.returncode==2 and not result.stdout
            assert destination.read_text()=='prior result'
            bad.write_bytes(old)
        # Absent differs from malformed, finite counts remain reporter decisions.
        stat.write_text('{"design":{"num_cells":2.5}}')
        state.write_text('{"metrics":{}}')
        write_csv([['timing__setup__ws','0'],['timing__hold__ws','0']])
        result=compare(run)
        assert result['metrics']['mapped_cells']['value']==2.5
        assert not report.numeric(result['metrics']['mapped_cells']['value'],count=True)
        assert report.report(run,result,'native collected',output=lambda *_:None)!=0
        # Atomic destination replacement including symlink itself; no other writes.
        def snapshot():
            return {str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file() and p!=destination}
        before=snapshot()
        target=root/'symlink target'; target.write_text('untouched')
        destination.unlink(); destination.symlink_to(target)
        out=call('--json'); assert out.returncode==0,(out.stdout,out.stderr)
        assert not destination.is_symlink() and target.read_text()=='untouched'
        assert before==snapshot()
        assert json.loads(out.stdout)==result and destination.read_text()==out.stdout
        assert not list(run.glob('.results.json.*'))
        destination.unlink()
        out=call('--stdout'); assert out.returncode==0 and not destination.exists()
        assert json.loads(out.stdout)==result
        out=call('--output',str(root/'missing parent/results.json'))
        assert out.returncode==2 and not destination.exists()
        out=subprocess.run([str(cli),'--run-dir',str(root),'--stdout'],text=True,capture_output=True,
                           env=dict(os.environ,PATH='/no-tools'))
        assert out.returncode==2 and not (root/'results.json').exists()
        # Real public route and compatibility callback, including missing helper.
        command=[str(public),'collect',str(run),'--json'] if public else [sys.executable,'-B','-m','hardcaml_asic_flow','collect',str(run),'--json']
        env=dict(os.environ,PYTHONPATH=str(package.parent))
        out=subprocess.run(command,env=env,text=True,capture_output=True)
        assert out.returncode==0,(out.stdout,out.stderr)
        assert json.loads(out.stdout)==result
        if public:
            held=cli.with_name(cli.name+'.held'); cli.rename(held)
            try:
                out=subprocess.run(command,env=env,text=True,capture_output=True)
                assert out.returncode==2 and 'OCaml backend is missing' in out.stderr
            finally:
                held.rename(cli)
        args=SimpleNamespace(run_dir=run,output=destination,stdout=False,json=False,quiet=True,verbose=False)
        assert public_cli.Operations().collect(args)==0
        with mock.patch.object(ocaml_backend,'binary',side_effect=OSError('missing authoritative helper')):
            with contextlib.redirect_stderr(io.StringIO()):
                assert public_cli.main(['collect',str(run),'--stdout'])==2

        # Python's very large integer conversion raises, not silently Infinity.
        stat.write_text('{"design":{"area":'+str(10**400)+'}}')
        with unittest.TestCase().assertRaises(OverflowError): original(run)
        assert call('--stdout').returncode==2

        if retained.is_dir():
            # Read-only retained official state with Infinity and accepted finalization.
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in retained.rglob('*') if p.is_file()}
            receipt=json.loads((retained/'operation.json').read_text())
            context=receipt['evidence_context']; synth=retained/'evidence/synthesis-state.json'
            compare(retained,context=context,synthesis_state=synth)
            assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in retained.rglob('*') if p.is_file()}
            disposable=root/'accepted copy'; shutil.copytree(retained,disposable)
            # Written context is a test input only; production finalization owns it.
            context_file=root/'context.json'; context_file.write_text(json.dumps(context))
            out=subprocess.run([str(cli),'--run-dir',str(disposable),'--context',str(context_file),
                '--synthesis-state',str(disposable/'evidence/synthesis-state.json'),'--json'],text=True,capture_output=True)
            assert out.returncode==0,(out.stdout,out.stderr)
            expected=collection_expected(original(disposable,context=context,synthesis_state=disposable/'evidence/synthesis-state.json'))
            assert json.loads(out.stdout)==expected
            assert report.report(disposable,expected,'native collected',context=context,output=lambda *_:None)==0
            print('retained accepted finalization: read-only differential and disposable-copy acceptance passed')
        else:
            print('retained accepted finalization unavailable:',retained)
    print(f'collect: {count} exact Python result/byte differentials; typed codec/metric, public/callback and atomic checks passed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cli',type=Path)
    parser.add_argument('--consumer',type=Path)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--fixtures',type=Path)
    parser.add_argument('--public',type=Path)
    parser.add_argument('--installed',action='store_true')
    args=parser.parse_args(); repo=Path(__file__).resolve().parents[1]
    if args.installed:
        with tempfile.TemporaryDirectory(prefix='installed-collect-',dir='/tmp/opencode') as tmp:
            root=Path(tmp); prefix=root/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(repo, prefix)
            driver=root/'check_collect.py'; shutil.copyfile(__file__,driver)
            fixtures=root/'test_coarse_flow.py'; shutil.copyfile(repo/'test/test_coarse_flow.py',fixtures)
            consumer=root/'consumer'; consumer.mkdir()
            (consumer/'dune-project').write_text('(lang dune 3.0)\n')
            (consumer/'dune').write_text('(executable (name main) (libraries hardcaml_asic.execution))\n')
            shutil.copyfile(repo/'test/collect_consumer.ml',consumer/'main.ml')
            subprocess.run(['dune','build','-j20','main.exe'],cwd=consumer,
                env=dict(os.environ,OCAMLPATH=str(prefix/'lib')),check=True)
            launcher=root/'launch.py'
            launcher.write_text('import sys,runpy\n'+f'checkout={str(repo)!r}\n'+
                'def audit(event,args):\n'
                ' if event=="open" and isinstance(args[0],str) and args[0].startswith(checkout+"/"):\n'
                '  raise AssertionError("checkout read: "+args[0])\n'
                'sys.addaudithook(audit)\nfrom reference_harness import activate\nactivate()\n'+f'runpy.run_path({str(driver)!r},run_name="__main__")\n')
            subprocess.run([sys.executable,'-B',str(launcher),'--cli',str(prefix/'bin/hardcaml-asic-experimental-collect'),
                '--consumer',str(consumer/'_build/default/main.exe'),
                '--package',str(prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow'),
                '--fixtures',str(fixtures),'--public',str(prefix/'bin/hardcaml-asic-flow')],cwd=root,check=True)
    else:
        suite((args.cli or repo/'_build/default/bin/experimental_collect.exe').resolve(),
              (args.consumer or repo/'_build/default/test/collect_consumer.exe').resolve(),
              (args.package or repo/'flow/hardcaml_asic_flow').resolve(),
              (args.fixtures or repo/'test/test_coarse_flow.py').resolve(),args.public)


if __name__=='__main__': main()
