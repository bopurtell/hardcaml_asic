"""Real Python acceptance/rendering differentials; no physical tools."""
import argparse
import contextlib
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from resource_evidence_compat import historical_human, collection_expected

RETAINED = Path('/tmp/opencode/native-final-integration/finalizations/20261014T040711301534Z_913453bd')


def suite(cli, consumer, package, fixtures, public=None):
    sys.path.insert(0, str(package.parent))
    sys.path.insert(0, str(fixtures.parent))
    from hardcaml_asic_flow import report, coarse_flow_oracle, cli as public_cli, ocaml_backend
    import test_report
    test_report.PACKAGE_ROOT = Path(os.environ.get('FLOW_TEST_REFERENCE_ROOT', str(package.parent)))
    count = 0

    def call(run, *args):
        return subprocess.run([str(cli), '--run-dir', str(run), *args],
                              text=True, capture_output=True, env=dict(os.environ, PATH='/no-eda-tools'))

    def snapshot(run):
        return {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in run.rglob('*') if p.is_file()}

    def compare(run, results, context, source='fixture'):
        nonlocal count
        output=[]
        request=dict(context=context, results=results, run_dir=str(run))
        try:
            status=report.report(run, results, source, context=context, output=output.append)
        except (TypeError,ValueError,AttributeError,OverflowError):
            actual=subprocess.run([str(consumer)],input=json.dumps(request)+'\n',text=True,capture_output=True)
            assert actual.returncode==2,(actual.stdout,actual.stderr)
            count+=1
            return 2,''
        human='\n'.join(output)+'\n'
        actual=subprocess.run([str(consumer)],input=json.dumps(request)+'\n',text=True,capture_output=True)
        assert actual.returncode==0, (actual.stdout,actual.stderr)
        value=json.loads(actual.stdout)
        assert value['exit_code']==status and value['accepted']==(status==0), value
        expected_problems=output[output.index('PROBLEMS')+1:] if 'PROBLEMS' in output else []
        assert value['problems']==[p[4:] for p in expected_problems], (value,expected_problems)
        assert historical_human(value['human'])==human.replace('   source '+source+'\n','   source fixture\n'), '\n'.join(
            __import__('difflib').unified_diff(human.splitlines(),value['human'].splitlines(),fromfile='Python',tofile='OCaml'))
        count+=1
        return status,human

    # All original report fixtures now ALSO compare native helper output byte for byte.
    original=test_report.reporter
    def differential_reporter(*args):
        expected=original(*args)
        if args and args[0]!='--runs':
            actual=call(Path(args[0]), *args[1:])
            assert (actual.returncode,historical_human(actual.stdout),historical_human(actual.stderr))==(expected.returncode,expected.stdout,expected.stderr), (args,expected,actual)
        return expected
    with mock.patch.object(test_report,'reporter',side_effect=differential_reporter):
        result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(test_report.ReportTest))
        assert result.wasSuccessful()

    with tempfile.TemporaryDirectory(prefix='report-',dir='/tmp/opencode') as tmp:
        root=Path(tmp); run=root/'run Ω with spaces'; run.mkdir()
        flow=run/'project/runs/asic'; (flow/'final').mkdir(parents=True)
        metrics=flow/'final/metrics.csv'
        context=dict(run_id='attempt-1',build_identity='build-1',bundle='/bundle Ω',status='completed',
                     requested_stage='full',completed_stage='full',outputs={'flow':'project/runs/asic'})
        def persist(results, record=context):
            (run/'run.json').write_text(json.dumps(record))
            (run/'results.json').write_text(json.dumps(results))
        baseline=copy.deepcopy(test_report.FULL_RESULTS)
        with metrics.open('w',newline='') as stream:
            csv.writer(stream).writerows([
                ['timing__setup__ws__corner:Ω,"quoted', '18.425'],
                ['timing__setup__ws__corner:Ω,"quoted', '18.426'],
                ['timing__hold__ws__corner:Ω,"quoted', '1e39'],
                ['timing__hold_vio__count__corner:Ω,"quoted', '0'],
                ['timing__setup__tns__corner:unused', '-3'],
                ['timing__setup__ws__corner:negative', '-.005'],
                ['timing__setup__ws__corner:nonfinite', 'NaN'],
                ['timing__hold__ws__corner:nonfinite', '-Infinity'],
                ['timing__hold__ws__corner:underflow', '-1e-999'],
                ['timing__setup__ws__corner:long空間空間空間空間空間空間空間', '1.2345'],
                ['timing__setup__ws__corner:invalid', '0x1p2'],
                ['timing__setup__ws__corner:new\nline', '4'],
                ['timing__setup__ws__corner:trailing\n', '5'],
                ['ignored','a','b'], [], ['timing__setup__ws__corner:underscore','1_0']])
        for name in ['z-last','a-first']:
            p=run/'checks'/name/'tt/precheck/reports/results.md'; p.parent.mkdir(parents=True)
            p.write_text('| Check | Result |\n|---|---|\n| Pin Ω check | OK |\n| Display only | FAIL |\n| skip | --- |\n| | |\n|\u00a0Unicode padded\u2003|\u3000OK\u202f|\n')
        p=flow/'37-resizertimingpostcts/tool.log'; p.parent.mkdir()
        p.write_text('+ repair_timing -verbose -hold\r\n[INFO RSZ-0033] No hold violations found.\n  Hold violation: fixture\nRSZ-0098 repaired\nunrelated\n')
        (flow/'resizer-not-a-directory').write_text('ignored')
        sdc=run/'project/constraints/top.sdc'; sdc.parent.mkdir()
        sdc.write_text('create_clock -period 10 [get_ports clk]\r\n# Ω display\n')
        compare(run,baseline,context)

        values=[None,False,True,0,1,-1,2.5, -0.0, 9007199254740993, 10**100,
                float('nan'),float('inf'),float('-inf'),'0','',[],{},'bad Ω\nquoted']
        for section,names in [('metrics',['mapped_area','mapped_cells','setup_slack','hold_slack']),
                              ('synthesis_checks',['unmapped_instances','synthesis_errors','inferred_latches'])]:
            for name in names:
                for raw in values:
                    results=copy.deepcopy(baseline); results[section][name]['value']=raw
                    compare(run,results,context)
                for raw in values:
                    results=copy.deepcopy(baseline); results[section][name]['unavailable_reason']=raw
                    compare(run,results,context)
        # Missing/malformed policy containers and status/scope/identity contradictions.
        for key in ['metrics','synthesis_checks','checks','postchecks','errors','timing_unconstrained_modes']:
            for raw in values:
                results=copy.deepcopy(baseline); results[key]=raw
                compare(run,results,context)
            results=copy.deepcopy(baseline); results.pop(key); compare(run,results,context)
        for key in ['run_id','build_identity','process_status','requested_stage','completed_stage']:
            for raw in values+['full','synthesis','completed','failed','interrupted','routing']:
                results=copy.deepcopy(baseline); results[key]=raw
                compare(run,results,context)
        for mode in [[],['hold'],['setup','hold'],[''],[False]]:
            results=copy.deepcopy(baseline); results['timing_unconstrained_modes']=mode
            compare(run,results,context)
        for entry in [None,False,{},dict(status='failed',checks={}),dict(status='completed',checks=[]),
                      dict(status='completed',checks={'precheck':False,'gate_level':''})]:
            results=copy.deepcopy(baseline); results['postchecks']=[entry,baseline['postchecks'][0]]
            compare(run,results,context)
        for corner in values:
            results=copy.deepcopy(baseline); results['metrics']['hold_slack']['corner']=corner
            compare(run,results,context)
        # Half-even fixed formatting, subnormals, large finite integers, signed zero.
        rng=random.Random(818)
        for raw in [2.675,1.2345,-0.0005,1e30,1e-30,5e-324,1.7976931348623157e308]+[rng.uniform(-1000,1000) for _ in range(40)]:
            results=copy.deepcopy(baseline); results['metrics']['mapped_area']['value']=raw
            compare(run,results,context)
        for raw in [10**100,9007199254740993,True,{},['Ω'],None,'quote\'"\\\n\r\t\x00\x1f', 'Ω 😀']:
            results=copy.deepcopy(baseline); record=dict(context,run_id=raw)
            results['run_id']=raw
            compare(run,results,record)
        for a,b in [(1,True),(1,1.0),(9007199254740993,9007199254740992.0),
                    (10**100,float(10**100)),(2**100,float(2**100))]:
            results=copy.deepcopy(baseline); record=dict(context,run_id=a)
            results['run_id']=b; compare(run,results,record)
        for errors in [[None,False,{'raw':float('nan')},['Ω',10**100]],['line\nwith "quotes"']]:
            results=copy.deepcopy(baseline); results['errors']=errors; compare(run,results,context)
        for stage in ['synthesis','full',None,'routing']:
            results=test_report.synthesis_results(); record=dict(context,requested_stage=stage,completed_stage=stage)
            results.update(requested_stage=stage,completed_stage=stage)
            compare(run,results,record)

        # Stored evidence wins even when raw collection would fail; JSON is normalized,
        # supplementary failing Markdown/CSV does not change an accepted policy verdict.
        baseline['opaque_raw']={'nonfinite':float('inf'),'huge':10**100}
        persist(baseline)
        (run/'project/manifest.json').write_text('{bad')
        # PT.5 cannot silently trust a malformed available expectation manifest.
        assert call(run,'--json').returncode==2
        (run/'project/manifest.json').write_text('{}')
        before=snapshot(run)
        status,human=compare(run,baseline,context,source='results.json')
        actual=call(run,'--json')
        assert actual.returncode==0 and historical_human(actual.stderr)==human
        assert actual.stdout==json.dumps(baseline,indent=2,sort_keys=True)+'\n'
        assert snapshot(run)==before
        for token in ['NaN','Infinity','-Infinity','1e999','-1e999']:
            raw=json.dumps(baseline).replace('"opaque_raw": {','"opaque_raw": {"overflow": '+token+',')
            (run/'results.json').write_text(raw)
            actual=call(run,'--json')
            assert actual.returncode==0 and actual.stdout==json.dumps(json.loads(raw),indent=2,sort_keys=True)+'\n'
        persist(baseline)
        assert call(run,'--diagnostics-only').stdout==call(run,'--diagnostics-only').stderr==''
        verbose=call(run,'--diagnostics-only','--verbose')
        assert verbose.returncode==0 and historical_human(verbose.stderr).startswith(human) and 'acceptance exit 0' in verbose.stderr
        # Actual in-memory acquisition compared with actual Python collector/report.
        (run/'project/manifest.json').write_text('{}')
        (run/'results.json').unlink()
        expected=collection_expected(coarse_flow_oracle.collect(run))
        status,human=compare(run,expected,context,source='collected in memory')
        before=snapshot(run); actual=call(run,'--json')
        assert actual.returncode==status and historical_human(actual.stderr)==human,(actual.stderr,human)
        assert actual.stdout==json.dumps(expected,indent=2,sort_keys=True)+'\n'
        assert snapshot(run)==before and not (run/'results.json').exists()

        synth=flow/'06-yosys-synthesis'; (synth/'reports').mkdir(parents=True)
        (synth/'reports/stat.json').write_text('{"design":{"area":1.25,"num_cells":3}}')
        (synth/'state_out.json').write_text(json.dumps({'metrics':{
            'design__instance_unmapped__count':0,'synthesis__check_error__count':0,'design__inferred_latch__count':0}}))
        record=dict(context,requested_stage='synthesis',completed_stage='synthesis')
        (run/'run.json').write_text(json.dumps(record))
        metrics.write_text('timing__setup__ws,0\ntiming__hold__ws,0\n')
        expected=collection_expected(coarse_flow_oracle.collect(run))
        status,human=compare(run,expected,record,source='collected in memory')
        assert status==0
        before=snapshot(run); actual=call(run,'--json')
        assert actual.returncode==0 and historical_human(actual.stderr)==human
        assert actual.stdout==json.dumps(expected,indent=2,sort_keys=True)+'\n'
        assert before==snapshot(run) and not (run/'results.json').exists()

        # Public routing and callback preserve exits 0/1/2 without fallback policy.
        env=dict(os.environ,PYTHONPATH=str(package.parent))
        command=[str(public),'report',str(run),'--json'] if public else [sys.executable,'-B','-m','hardcaml_asic_flow','report',str(run),'--json']
        args=SimpleNamespace(run_dir=run,json=True,diagnostics_only=False,verbose=False)
        for code in [0,1,2]:
            results=copy.deepcopy(baseline)
            if code==1: results['metrics']['mapped_cells']['unavailable_reason']=False
            persist(results)
            if code==2: (run/'results.json').write_text('{bad')
            expected=call(run,'--json')
            actual=subprocess.run(command,text=True,capture_output=True,env=env)
            assert (actual.returncode,actual.stdout,actual.stderr)==(expected.returncode,expected.stdout,expected.stderr)
            assert actual.returncode==code
            out=io.StringIO(); err=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                assert public_cli.Operations().report(args)==code
            assert (out.getvalue(),err.getvalue())==(expected.stdout,expected.stderr)
        with mock.patch.object(ocaml_backend,'binary',side_effect=OSError('missing authoritative report')):
            with contextlib.redirect_stderr(io.StringIO()):
                assert public_cli.main(['report',str(run)])==2
        if public:
            held=cli.with_name(cli.name+'.held'); cli.rename(held)
            try:
                actual=subprocess.run(command,text=True,capture_output=True,env=env)
                assert actual.returncode==2 and 'OCaml backend is missing' in actual.stderr
            finally: held.rename(cli)
        assert subprocess.run([str(cli)],capture_output=True).returncode==2
        assert subprocess.run(command[:1]+(['report'] if public else ['-B','-m','hardcaml_asic_flow','report']),env=env,capture_output=True).returncode==2
        for malformed in ['null','[]','false','1','"text"','{bad']:
            (run/'results.json').write_text(malformed)
            actual=call(run,'--json'); assert actual.returncode==2 and not actual.stdout
        # Very large integer conversion is a malformed-service exit, not Infinity.
        results=copy.deepcopy(baseline); results['metrics']['mapped_cells']['value']=10**400
        persist(results); assert call(run).returncode==2

        if RETAINED.is_dir():
            before=snapshot(RETAINED)
            receipt=json.loads((RETAINED/'operation.json').read_text()); record=receipt['evidence_context']
            results=json.loads((RETAINED/'results.json').read_text())
            status,_=compare(RETAINED,results,record)
            assert status==0
            ctx=root/'context.json'; ctx.write_text(json.dumps(record))
            actual=call(RETAINED,'--context',str(ctx),'--json')
            assert actual.returncode==0,(actual.stdout,actual.stderr)
            assert actual.stdout==json.dumps(results,indent=2,sort_keys=True)+'\n'
            assert before==snapshot(RETAINED)
            print('retained accepted finalization: read-only native acceptance/render comparison passed')
    print(f'report: {count} actual Python policy/human byte differentials; original fixtures, stored/in-memory, public/callback/read-only checks passed')


def main():
    parser=argparse.ArgumentParser()
    for name in ['cli','consumer','package','fixtures','public']: parser.add_argument('--'+name,type=Path)
    parser.add_argument('--installed',action='store_true')
    args=parser.parse_args(); repo=Path(__file__).resolve().parents[1]
    if args.installed:
        with tempfile.TemporaryDirectory(prefix='installed-report-',dir='/tmp/opencode') as tmp:
            root=Path(tmp); prefix=root/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(repo, prefix)
            driver=root/'check_report.py'; shutil.copyfile(__file__,driver)
            fixtures=root/'test_report.py'; shutil.copyfile(repo/'test/test_report.py',fixtures)
            consumer=root/'consumer'; consumer.mkdir()
            (consumer/'dune-project').write_text('(lang dune 3.0)\n')
            (consumer/'dune').write_text('(executable (name main) (libraries hardcaml_asic.execution))\n')
            shutil.copyfile(repo/'test/report_consumer.ml',consumer/'main.ml')
            subprocess.run(['dune','build','-j20','main.exe'],cwd=consumer,env=dict(os.environ,OCAMLPATH=str(prefix/'lib')),check=True)
            launcher=root/'launch.py'
            launcher.write_text('import sys,runpy\n'+f'checkout={str(repo)!r}\n'+
                'def audit(event,args):\n'
                ' if event=="open" and isinstance(args[0],str) and args[0].startswith(checkout+"/"):\n'
                '  raise AssertionError("checkout read: "+args[0])\n'
                'sys.addaudithook(audit)\nfrom reference_harness import activate\nactivate()\n'+f'runpy.run_path({str(driver)!r},run_name="__main__")\n')
            subprocess.run([sys.executable,'-B',str(launcher),'--cli',str(prefix/'bin/hardcaml-asic-experimental-report'),
                '--consumer',str(consumer/'_build/default/main.exe'),'--package',str(prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow'),
                '--fixtures',str(fixtures),'--public',str(prefix/'bin/hardcaml-asic-flow')],cwd=root,check=True)
    else:
        suite((args.cli or repo/'_build/default/bin/experimental_report.exe').resolve(),
              (args.consumer or repo/'_build/default/test/report_consumer.exe').resolve(),
              (args.package or repo/'flow/hardcaml_asic_flow').resolve(),
              (args.fixtures or repo/'test/test_report.py').resolve(),args.public)


if __name__=='__main__': main()
