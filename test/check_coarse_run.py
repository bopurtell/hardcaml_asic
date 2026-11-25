"""Actual subprocess coarse lifecycle differentials; fake external runtimes only."""
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
import contextlib
import io
import re
import venv
from types import SimpleNamespace
from check_preflight import FAKE
from check_postcheck import PRECHECK

RUN = r'''
if '-m' in a and a[a.index('-m')+1] == 'librelane':
    project = pathlib.Path.cwd()
    attempt = project.parent
    record = json.loads((attempt/'run.json').read_text())
    assert record['status'] == 'running' and record['commands'][-1][1:] == a
    assert json.loads((attempt/'preflight.json').read_text())['ready']
    assert (project/'tt/collateral').read_text() == 'collateral'
    assert (project/'src/config.json').read_text() == '{}\n'
    assert a[-1] == 'src/config.json' and '--force-run-dir' in a
    if pathlib.Path(sys.argv[0]).name == 'docker':
        assert '--pull=never' in a and '-v' in a
        if os.environ.get('OPENROAD_THREADS') == '20': assert '--cpus=20' in a
        assert 'sha256:'+'a'*64 in a and 'ghcr.io/librelane/librelane:3.1.0.dev3' not in a
    if mode.get('run_block'):
        (root/'blocked').write_text(str(os.getpid()))
        time.sleep(60)
    if mode.get('mutate'): (project/'manifest.json').write_text('{}')
    if mode.get('mutate_source'): (pathlib.Path(record['bundle'])/'manifest.json').write_text('{}')
    if mode.get('cleanup_fail'): (root/'executed').write_text('yes')
    if mode.get('accepted'):
        reports = project/'runs/asic/06-yosys-synthesis/reports'
        reports.mkdir(parents=True,exist_ok=True)
        (reports/'stat.json').write_text(json.dumps(dict(design=dict(area=10,num_cells=2,num_cells_by_type={}))))
        (reports.parent/'state_out.json').write_text(json.dumps(dict(metrics={
            'design__instance_unmapped__count':0,'synthesis__check_error__count':0,'design__inferred_latch__count':0})))
    if mode.get('full_evidence'):
        final=project/'runs/asic/final'
        for name in ('gds/top.gds','nl/top.nl.v'):
            p=final/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('fixture\n')
    if mode.get('terminal_publication_fail'): attempt.chmod(0o500)
    print('external LibreLane output',flush=True)
    sys.exit(mode.get('run_exit',0))
'''

CHECK = r'''
name=pathlib.Path(sys.argv[0]).name
if name=='nix-shell':
    if a[-1]=='klayout -v': print('KLayout 0.28.17'); sys.exit(0)
    import subprocess
    sys.exit(subprocess.call(['/bin/sh','-c',a[-1]]))
if name=='docker' and ('iverilog' in a or 'vvp' in a):
    phase='probe' if '-V' in a else 'compile' if 'iverilog' in a else 'replay'
    if mode.get('check_block')==phase:
        (root/'blocked').write_text(str(os.getpid())); time.sleep(60)
    if phase=='probe': print('Icarus 12'); sys.exit(0)
    if phase=='compile': pathlib.Path(a[a.index('-o')+1]).write_text('compiled')
    sys.exit(mode.get(phase+'_exit',0))
'''


def suite(cli, package, public, consumer=None):
    sys.path.insert(0, str(package.parent))
    from hardcaml_asic_flow import coarse_flow_oracle, cli as public_cli, execution_plan, ocaml_backend, runtime_contract
    with tempfile.TemporaryDirectory(prefix='coarse run ') as tmp:
        root = Path(tmp); bin = root/'bin'; bin.mkdir()
        (bin/'dirname').symlink_to(shutil.which('dirname'))
        (bin/'python3').symlink_to(sys.executable)
        fake = FAKE.replace("if pathlib.Path(sys.argv[0]).name == 'docker':", RUN + CHECK + "\nif pathlib.Path(sys.argv[0]).name == 'docker':", 1)
        fake = fake.replace("if a[:2] == ['image','inspect']:",
            "if a[:2] == ['image','inspect'] and '--format' in a:\n        print('sha256:'+'a'*64); sys.exit(0)\n    if a[:2] == ['image','inspect']:")
        fake = fake.replace("if a[:2] == ['image','inspect']:",
            "if a[:2] == ['image','inspect'] and mode.get('verify_mismatch') and any(x.startswith('sha256:') for x in a):\n        print(json.dumps([dict(Id='sha256:'+'b'*64, RepoDigests=[], Os='linux', Architecture='amd64')])); sys.exit(0)\n    if a[:2] == ['image','inspect']:")
        fake = fake.replace("if a[0] == 'rm': sys.exit(0)",
            "if a[0] == 'rm':\n        if mode.get('cleanup_fail') and (root/'executed').exists(): print('fixture cleanup failure',file=sys.stderr); sys.exit(9)\n        sys.exit(0)")
        for name in ('git','python','docker'):
            p = bin/name; p.write_text(fake.replace('INTERPRETER',sys.executable)); p.chmod(0o755)
        bundle = root/'bundle'; (bundle/'src').mkdir(parents=True)
        (bundle/'src/config.json').write_text('{}\n')
        tools = root/'tools'; tools.mkdir(); (tools/'collateral').write_text('collateral')
        pdk = root/'pdk'; pdk.mkdir()
        manifest = dict(schema_version=1,adapter='LibreLane',identity='build-fixture',top_module='top',
            requested_tools=dict(python='3.11',librelane='3.1.0.dev3',support_tools_revision='revision',pdk_revision='revision'),
            files=[dict(path='src/config.json',sha256=coarse_flow_oracle.digest(bundle/'src/config.json'))],
            target=dict(technology='tech',external_references=[dict(source='Support_tools',role='test',path='collateral')]))
        policy=runtime_contract.policy_from_requested(manifest['requested_tools'])
        policy['implementation']['dockerized'].update(policy='official-librelane-image',
            python=dict(policy='distribution-supported',requires_python='>=3.10',supported_minors=list(runtime_contract.SUPPORTED_MINORS)))
        manifest['requested_tools']['runtime_policy']={k:policy[k] for k in ('schema_version','host','implementation')}
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        env = dict(os.environ,PATH=str(bin),FIXTURE=str(root))
        base = [str(cli),'--bundle',str(bundle),'--support-tools',str(tools),'--pdk-root',str(pdk),
            '--python',str(bin/'python'),'--resources',str(package),'--runs',str(root/'runs'),'--json']
        def invoke(mode, native=True, stage='synthesis', extra=()):
            (root/'mode.json').write_text(json.dumps(mode))
            return subprocess.run([*base,'--stage',stage,*(['--native'] if native else []),*extra],
                env=env,text=True,capture_output=True,timeout=20)
        count = 0
        for native,stage in ((True,'synthesis'),(False,'synthesis'),(False,'full')):
            for mode in ({},{'run_exit':7},{'dirty':True},{'host_python':'3.12.1'},{'mutate':True}):
                r = invoke(mode,native,stage); outcome = json.loads(r.stdout)
                record = json.loads(Path(outcome['run_record']).read_text())
                assert r.returncode == outcome['exit_code'], (r.stderr,record)
                assert record['environment'] == json.loads((Path(outcome['run_dir'])/'preflight.json').read_text())
                old = os.environ.copy()
                try:
                    os.environ.clear(); os.environ.update(env)
                    expected = coarse_flow_oracle.run(SimpleNamespace(bundle=bundle,runs=root/'legacy',stage=stage,
                        support_tools=tools,pdk_root=pdk,python=bin/'python',native=native,allow_python_mismatch=False))
                finally:
                    os.environ.clear(); os.environ.update(old)
                legacy = coarse_flow_oracle.read_json(expected.run_record)
                # Names, clocks and attempt paths are intentionally fresh. All other
                # persisted fields are the actual Python producer's contract.
                for key in ('schema_version','execution_binding_required','build_identity','bundle','manifest_sha256',
                            'requested_stage','completed_stage','status','logs','outputs','environment','execution_binding',
                            'execution_mode','execution_engine','error'):
                    assert record.get(key)==legacy.get(key),(native,mode,key,record.get(key),legacy.get(key),record.get('error'),r.stderr,
                        (expected.run_dir/'execution.log').read_text() if (expected.run_dir/'execution.log').exists() else legacy.get('error'))
                assert outcome['status']==expected.status and outcome['exit_code']==expected.exit_code
                if record['commands']:
                    assert len(record['commands'])==1
                    log = (Path(outcome['run_dir'])/'execution.log').read_text()
                    assert '$ ' in log and 'external LibreLane output' in log
                if not native and record['commands']:
                    assert '--cpus=20' in record['commands'][0]
                    assert record['container_cleanup'][-1]['status']=='removed-or-absent'
                # Failed runs are retained and accepted by the existing collector.
                coarse_flow_oracle.collect(Path(outcome['run_dir']))
                count += 1
        # Explicit checked coarse plans and lexical interpreter aliases.
        plan = root/'plan.json'; plan.write_text(json.dumps(execution_plan.resolve('synthesis').as_dict()))
        r = invoke({},extra=('--plan',str(plan)))
        assert r.returncode==0,(r.stdout,r.stderr)
        alias = bin/'alias'; alias.symlink_to(bin/'python')
        alias_command = base.copy(); alias_command[alias_command.index('--python')+1]=str(bin/'../bin/alias')
        (root/'mode.json').write_text('{}')
        r = subprocess.run([*alias_command,'--native'],env=env,capture_output=True,text=True)
        o=json.loads(r.stdout); record=json.loads(Path(o['run_record']).read_text())
        assert record['execution_binding']['interpreter']==str(bin/'../bin/alias')
        # Pre-allocation errors do not create an attempt.
        before=set((root/'runs').iterdir())
        assert invoke({},True,'full').returncode==2
        original=(bundle/'manifest.json').read_text(); (bundle/'manifest.json').write_text('bad')
        assert invoke({}).returncode==2
        (bundle/'manifest.json').write_text(original)
        assert set((root/'runs').iterdir())==before
        r=invoke({'mutate_source':True})
        o=json.loads(r.stdout); record=json.loads(Path(o['run_record']).read_text())
        assert r.returncode==1 and record['completed_stage'] is None and record['error']=='build manifest changed during execution'
        (bundle/'manifest.json').write_text(original)
        r=invoke({'cleanup_fail':True},False)
        o=json.loads(r.stdout); record=json.loads(Path(o['run_record']).read_text())
        assert r.returncode==1 and record['completed_stage'] is None
        assert record['container_cleanup'] and all(x['status']=='failed' for x in record['container_cleanup'])
        assert 'cleanup' in record['error']
        r=invoke({'verify_mismatch':True},False)
        o=json.loads(r.stdout); record=json.loads(Path(o['run_record']).read_text())
        assert r.returncode==1 and record['commands']==[] and 'mismatch' in record['error']
        for target in ('host','container'):
            for sig in (signal.SIGINT,signal.SIGTERM):
                before=(root/'calls').read_text()
                r=invoke({'storage_signal':target,'signal':sig},False)
                o=json.loads(r.stdout); record=json.loads(Path(o['run_record']).read_text())
                assert r.returncode==128+sig and record['status']=='interrupted',(o,record,r.stderr)
                assert not record['commands'] and record['completed_stage'] is None
                assert 'evidence write failed' in '; '.join(record['environment']['issues']),record
                calls=[json.loads(x)[1] for x in (root/'calls').read_text()[len(before):].splitlines()]
                assert len([a for a in calls if a[0]=='run'])==(target=='container'),calls
        for signum in (signal.SIGINT,signal.SIGTERM):
            (root/'blocked').unlink(missing_ok=True)
            (root/'mode.json').write_text(json.dumps({'block':True}))
            p=subprocess.Popen([*base,'--native'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+15
            while not (root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
            assert (root/'blocked').exists()
            attempt=max((root/'runs').iterdir(),key=lambda x:x.name)
            assert json.loads((attempt/'preflight.json').read_text())['diagnostic']['status']=='pending'
            assert json.loads((attempt/'run.json').read_text())['status']=='starting'
            p.send_signal(signum); stdout,stderr=p.communicate(timeout=15)
            o=json.loads(stdout); record=json.loads(Path(o['run_record']).read_text())
            assert p.returncode==o['exit_code']==128+signum,(o,stderr,record)
            assert record['status']=='interrupted' and record['environment']==json.loads((attempt/'preflight.json').read_text())
        for native in (True,False):
            for signum in (signal.SIGINT,signal.SIGTERM):
                (root/'blocked').unlink(missing_ok=True)
                (root/'mode.json').write_text(json.dumps({'run_block':True}))
                p=subprocess.Popen([*base,*(['--native'] if native else [])],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                deadline=time.monotonic()+15
                while not (root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                if not (root/'blocked').exists():
                    stdout,stderr=p.communicate(timeout=5)
                    failed=json.loads(stdout)
                    raise AssertionError((native,signum,p.returncode,stdout,stderr,Path(failed['run_record']).read_text()))
                p.send_signal(signum); stdout,stderr=p.communicate(timeout=15)
                o=json.loads(stdout); record=json.loads(Path(o['run_record']).read_text())
                assert p.returncode==o['exit_code']==128+signum,(o,stderr)
                assert record['status']=='interrupted' and record['interrupted_by']==signal.Signals(signum).name
                assert record['completed_stage'] is None
                if not native: assert record['container_cleanup'][-1]['status']=='removed-or-absent'
        (root/'mode.json').write_text('{}')
        public_args=['run',str(bundle),'--support-tools',str(tools),'--pdk-root',str(pdk),
            '--python',str(bin/'python'),'--runs',str(root/'public'),'--native','--stage','synthesis','--json']
        command = [str(public)] if package.parents[2].name=='lib' else [sys.executable,'-B','-c',
            f'import os,sys; sys.path.insert(0,{str(Path(__file__).parent)!r}); sys.path.insert(0,{os.environ.get("FLOW_TEST_REFERENCE_ROOT", str(package.parent))!r}); from reference_harness import activate; activate() if os.environ.get("FLOW_TEST_REFERENCE_ROOT") else None; from hardcaml_asic_flow.cli import main; sys.exit(main())']
        r=subprocess.run([*command,*public_args],env=env,text=True,capture_output=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        assert json.loads(r.stdout)['status']=='completed'
        execute_args=public_args.copy(); execute_args[0]='execute'
        r=subprocess.run([*command,*execute_args],env=env,text=True,capture_output=True,timeout=20)
        result=json.loads(r.stdout)
        assert r.returncode==1 and result['status']=='rejected',(r.stdout,r.stderr)
        assert json.loads(Path(result['run_record']).read_text())['status']=='completed'
        assert (Path(result['run_dir'])/'results.json').is_file()
        # Actual current Python aggregate with injected native service fixtures,
        # compared to authoritative native aggregate over real external children.
        manifest.update(synthesis_sources=[],simulation_sources=[],source_inputs=[])
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        for mode, retain in (({},False),({'accepted':True},False),({'accepted':True},True),
                             ({'run_exit':7},True),({'dirty':True},False),
                             ({'accepted':True,'mutate':True},True),({'bad_json':True},True),
                             ({'accepted':True,'archive_reject':True},True)):
            (root/'mode.json').write_text(json.dumps(mode))
            destination=root/'aggregate-archive'
            if destination.exists(): shutil.rmtree(destination)
            if mode.get('archive_reject'):
                destination.mkdir();(destination/'README').write_text('user entry')
            more=['--archive',str(destination)] if retain else []
            r=subprocess.run([*command,*execute_args,*more],env=env,text=True,capture_output=True,timeout=30)
            actual=json.loads(r.stdout)
            if destination.exists(): shutil.rmtree(destination)
            if mode.get('archive_reject'):
                destination.mkdir();(destination/'README').write_text('user entry')
            args=SimpleNamespace(bundle=bundle,runs=root/'oracle',stage='synthesis',support_tools=tools,
                pdk_root=pdk,python=bin/'python',native=True,allow_python_mismatch=False,
                archive=destination if retain else None,force=False,json=True,verbose=False)
            stdout,stderr=io.StringIO(),io.StringIO(); old=os.environ.copy()
            try:
                os.environ.clear();os.environ.update(env)
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    code=public_cli.execute_sequence(args,public_cli.Operations())
            finally: os.environ.clear();os.environ.update(old)
            expected=json.loads(stdout.getvalue())
            def normalized(value):
                value=dict(value)
                for key in ('run_id','run_dir','run_record'): value.pop(key)
                value['steps']=[{k:v for k,v in s.items() if k!='duration_seconds'} for s in value['steps']]
                return value
            assert normalized(actual)==normalized(expected),(mode,actual,expected,r.stderr,stderr.getvalue())
            assert r.returncode==code
            def human(text, value):
                text=text.replace(value['run_dir'],'RUN').replace(value['run_id'],'ID')
                return re.sub(r'\(\d+\.\d{3}s\)','(TIME)',text)
            assert human(r.stderr,actual)==human(stderr.getvalue(),expected),(mode,r.stderr,stderr.getvalue())
            if retain and mode.get('accepted') and not mode.get('mutate') and not mode.get('archive_reject'):
                assert actual.get('archive')==str(destination) and (destination/'archive.json').is_file(),(actual,r.stderr)
            else: assert 'archive' not in actual
            assert not (Path(actual['run_dir'])/'journal.jsonl').exists()
        (root/'mode.json').write_text(json.dumps({'terminal_publication_fail':True}))
        r=subprocess.run([*command,*execute_args],env=env,text=True,capture_output=True,timeout=30)
        value=json.loads(r.stdout);run_dir=Path(value['run_dir']);run_dir.chmod(0o700)
        assert r.returncode==value['exit_code']==2 and value['status']=='failed',(value,r.stderr)
        assert [s['operation'] for s in value['steps']]==['run','collect']
        assert value['steps'][1]['best_effort']
        assert json.loads((run_dir/'run.json').read_text())['status']=='running'
        for accepted, json_mode, verbose in ((True,False,False),(False,False,False),
                                            (True,True,True),(False,False,True)):
            (root/'mode.json').write_text(json.dumps({'accepted':accepted}))
            call=execute_args.copy()
            if not json_mode: call.remove('--json')
            if verbose: call.append('--verbose')
            r=subprocess.run([*command,*call],env=env,text=True,capture_output=True,timeout=30)
            args=SimpleNamespace(bundle=bundle,runs=root/'oracle',stage='synthesis',support_tools=tools,
                pdk_root=pdk,python=bin/'python',native=True,allow_python_mismatch=False,
                archive=None,force=False,json=json_mode,verbose=verbose)
            stdout,stderr=io.StringIO(),io.StringIO();old=os.environ.copy()
            try:
                os.environ.clear();os.environ.update(env)
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    code=public_cli.execute_sequence(args,public_cli.Operations())
            finally: os.environ.clear();os.environ.update(old)
            assert code==r.returncode
            def receipt(out):
                if json_mode: return json.loads(out)
                return {'run_id':re.search(r'^run id: (.+)$',out,re.M)[1],
                        'run_dir':re.search(r'^run: (/.+)$',out,re.M)[1]}
            actual,expected=receipt(r.stdout),receipt(stdout.getvalue())
            def display(text,value):
                text=human(text,value)
                return re.sub(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+(?:\+00:00|Z)','DATE',text)
            if not json_mode:
                assert display(r.stdout,actual)==display(stdout.getvalue(),expected),(r.stdout,stdout.getvalue())
            assert display(r.stderr,actual)==display(stderr.getvalue(),expected),(r.stderr,stderr.getvalue())
        for signum in (signal.SIGINT,signal.SIGTERM):
            (root/'mode.json').write_text(json.dumps({'run_block':True}))
            (root/'blocked').unlink(missing_ok=True)
            p=subprocess.Popen([*command,*execute_args],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            deadline=time.monotonic()+20
            while not (root/'blocked').exists() and time.monotonic()<deadline: time.sleep(.02)
            assert (root/'blocked').exists()
            p.send_signal(signum); stdout,stderr=p.communicate(timeout=15)
            value=json.loads(stdout)
            assert p.returncode==value['exit_code']==128+signum,(value,stderr)
            assert [s['operation'] for s in value['steps']]==['run','collect']
            assert value['steps'][1]['best_effort'] and (Path(value['run_dir'])/'results.json').is_file()
        # Full aggregate uses the actual native TT/replay service and tool children.
        pre=tools/'precheck';pre.mkdir();(tools/'tech').mkdir()
        (pre/'default.nix').write_text('let pkgs = import (fetchTarball "https://example.invalid/pinned.tar.gz") {}; in pkgs.mkShell {}')
        (pre/'tool-versions.json').write_text('{"klayout":"0.28.17"}')
        (pre/'precheck.py').write_text(PRECHECK.replace("mode.get('block')", "mode.get('check_block')"))
        v=root/'venv';venv.EnvBuilder(with_pip=False).create(v)
        site=next((v/'lib').glob('python*/site-packages'));dist=site/'klayout-0.28.17.dist-info';dist.mkdir()
        (dist/'METADATA').write_text('Name: klayout\nVersion: 0.28.17\n')
        p=bin/'nix-shell';p.write_text(fake.replace('INTERPRETER',sys.executable));p.chmod(0o755)
        (bin/'env').symlink_to(shutil.which('env'))
        for relative in ('sg13cmos5l_io/verilog/sg13cmos5l_io.v','sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v','sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v'):
            p=pdk/'ihp-sg13cmos5l/libs.ref'/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('// fixture\n')
        tb=root/'tb.v';tb.write_text('module memory_tb; endmodule\n')
        full_args=[x for x in execute_args if x!='--native'];full_args[full_args.index('synthesis')]='full'
        full_args+=['--precheck-python',str(v/'bin/python'),'--testbench',str(tb),'--testbench-top','memory_tb']
        for mode in ({},{'precheck_exit':3},{'compile_exit':4},{'replay_exit':5}):
            (root/'mode.json').write_text(json.dumps(dict(mode,full_evidence=True,accepted=True)))
            r=subprocess.run([*command,*full_args],env=env,text=True,capture_output=True,timeout=35)
            value=json.loads(r.stdout)
            assert [s['operation'] for s in value['steps']]==(['run','postcheck','collect'] if mode else ['run','postcheck','collect','report']),(value,r.stderr)
            assert value['status']==('failed' if mode else 'rejected'),(value,r.stderr)
            checks=list((Path(value['run_dir'])/'checks').glob('*/postcheck.json'))
            assert len(checks)==1 and json.loads(checks[0].read_text())['status']==('failed' if mode else 'completed')
            args=SimpleNamespace(bundle=bundle,runs=root/'oracle',stage='full',support_tools=tools,
                pdk_root=pdk,python=bin/'python',native=False,allow_python_mismatch=False,
                precheck_python=v/'bin/python',testbench=tb,testbench_top='memory_tb',
                archive=None,force=False,json=True,verbose=False)
            stdout,stderr=io.StringIO(),io.StringIO();old=os.environ.copy()
            try:
                os.environ.clear();os.environ.update(env)
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    code=public_cli.execute_sequence(args,public_cli.Operations())
            finally: os.environ.clear();os.environ.update(old)
            expected=json.loads(stdout.getvalue())
            assert normalized(value)==normalized(expected) and r.returncode==code,(value,expected)
        for phase in ('probe','precheck','compile','replay'):
            for signum in (signal.SIGINT,signal.SIGTERM):
                mode=dict(full_evidence=True,accepted=True)
                mode['check_block']=phase
                (root/'mode.json').write_text(json.dumps(mode));(root/'blocked').unlink(missing_ok=True)
                p=subprocess.Popen([*command,*full_args],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                deadline=time.monotonic()+60
                while not (root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                if not (root/'blocked').exists():
                    p.terminate();stdout,stderr=p.communicate(timeout=15)
                    raise AssertionError((phase,stdout,stderr))
                p.send_signal(signum);stdout,stderr=p.communicate(timeout=15);value=json.loads(stdout)
                assert p.returncode==value['exit_code']==128+signum,(phase,value,stderr)
                assert [s['operation'] for s in value['steps']]==['run','postcheck','collect'],(phase,value,stderr)
                assert value['steps'][-1]['best_effort']
        (root/'mode.json').write_text('{}')
        if consumer:
            r=subprocess.run([str(consumer),str(bundle),str(tools),str(pdk),str(bin/'python'),str(package),str(root/'consumer')],
                env=env,text=True,capture_output=True,timeout=20)
            assert r.returncode==0 and json.loads(r.stdout)['status']=='completed',(r.stdout,r.stderr)
        if package.parents[2].name=='lib':
            # Mutate only the disposable installed fixture, never the source helper.
            missing=cli.with_name(cli.name+'.held'); cli.rename(missing)
            try:
                r=subprocess.run([*command,*public_args],env=env,text=True,capture_output=True)
                assert r.returncode==2 and 'OCaml backend is missing' in r.stderr,(r.stdout,r.stderr)
            finally: missing.rename(cli)
            execute_helper=cli.with_name('hardcaml-asic-experimental-execute')
            held=execute_helper.with_name(execute_helper.name+'.held');execute_helper.rename(held)
            try:
                r=subprocess.run([*command,*execute_args],env=env,text=True,capture_output=True)
                assert r.returncode==2 and not r.stdout and 'OCaml backend is missing' in r.stderr
            finally: held.rename(execute_helper)
        # Operations callback returns precisely the retained Python RunOutcome type.
        old=os.environ.copy()
        try:
            os.environ.clear(); os.environ.update(env)
            o=public_cli.Operations().run(SimpleNamespace(bundle=bundle,runs=root/'callback',stage='synthesis',
                support_tools=tools,pdk_root=pdk,python=bin/'python',native=True))
            assert isinstance(o,coarse_flow_oracle.RunOutcome) and o.as_dict()['status']=='completed'
        finally: os.environ.clear(); os.environ.update(old)
        # Authoritative resolution: missing selected helper fails, never coarse_flow_oracle fallback.
        original_binary=ocaml_backend.binary
        try:
            ocaml_backend.binary=lambda _: (_ for _ in ()).throw(OSError('missing authoritative helper'))
            try: public_cli.Operations().run(SimpleNamespace())
            except OSError: pass
            else: raise AssertionError('fallback')
        finally: ocaml_backend.binary=original_binary
        print(f'coarse run: {count} Python record/outcome differentials; aggregate 8 receipts, retained publication failure, 4 human/JSON/verbose presentations, 4 full postchecks, 10 external cancellation cases; plans, bindings and public routing passed')


def main():
    p=argparse.ArgumentParser(); p.add_argument('--installed',action='store_true')
    p.add_argument('--installed-hardlink',action='store_true',help='space-bounded independent prefix from Dune install staging')
    p.add_argument('--cli',type=Path); p.add_argument('--package',type=Path); p.add_argument('--public',type=Path); p.add_argument('--consumer',type=Path)
    a=p.parse_args(); repo=Path(__file__).resolve().parents[1]
    if a.installed or a.installed_hardlink:
        with tempfile.TemporaryDirectory(dir='/tmp/opencode',prefix='installed-coarse-') as tmp:
            root=Path(tmp); prefix=root/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(repo, prefix)
            aggregate=root/'aggregate';aggregate.mkdir()
            (aggregate/'dune-project').write_text('(lang dune 3.0)\n')
            (aggregate/'dune').write_text('(executable (name main) (libraries hardcaml_asic.execution unix))\n')
            shutil.copyfile(repo/'test/coarse_execution_consumer.ml',aggregate/'main.ml')
            subprocess.run(['dune','build','-j20','main.exe'],cwd=aggregate,env=dict(os.environ,OCAMLPATH=str(prefix/'lib')),check=True)
            shutil.copyfile(repo/'test/check_coarse_execution.py',root/'check_coarse_execution.py')
            for name in ('check_coarse_run.py','check_preflight.py','check_postcheck.py'): shutil.copyfile(repo/'test'/name,root/name)
            launcher=root/'launch.py'; launcher.write_text('import sys,runpy\n'+f'checkout={str(repo)!r}\n'+
                'def audit(event,args):\n if event=="open" and isinstance(args[0],str) and args[0].startswith(checkout+"/"):\n  raise AssertionError("checkout read: "+args[0])\n'+
                'sys.addaudithook(audit)\nfrom reference_harness import activate\nactivate()\n'+f'runpy.run_path({str(root/"check_coarse_run.py")!r},run_name="__main__")\n')
            aggregate_launcher=root/'aggregate-launch.py'
            aggregate_launcher.write_text(launcher.read_text().replace('check_coarse_run.py','check_coarse_execution.py'))
            subprocess.run([sys.executable,'-B',str(aggregate_launcher),
                '--consumer',str(aggregate/'_build/default/main.exe'),
                '--package',str(prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow')],cwd=root,check=True)
            shutil.rmtree(aggregate)
            subprocess.run([sys.executable,'-B',str(launcher),'--cli',str(prefix/'bin/hardcaml-asic-experimental-run'),
                '--package',str(prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow'),'--public',str(prefix/'bin/hardcaml-asic-flow')],cwd=root,check=True)
    else: suite((a.cli or repo/'_build/default/bin/experimental_run.exe').resolve(),
        (a.package or repo/'flow/hardcaml_asic_flow').resolve(),(a.public or repo/'flow/hardcaml-asic-flow').resolve(),a.consumer)


if __name__=='__main__': main()
