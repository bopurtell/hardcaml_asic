"""Actual external subprocess postcheck contract/differentials; no physical EDA."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import venv

FAKE = r'''#!INTERPRETER
import ctypes, json, os, pathlib, resource, signal, subprocess, sys, time
root=pathlib.Path(os.environ['FIXTURE'])
mode=json.loads((root/'mode.json').read_text())
a=sys.argv[1:]; name=pathlib.Path(sys.argv[0]).name
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps([name,a])+'\n')
if os.environ.get('OMP_NUM_THREADS')=='20': assert len(os.sched_getaffinity(0))<=20
def block(phase):
    if mode.get('kill')==phase:
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        assert ctypes.CDLL(None).prctl(4,0,0,0,0)==0  # PR_SET_DUMPABLE: no core-handler pipe
        os.kill(os.getpid(),mode['signal'])
    if mode.get('block')==phase:
        (root/'blocked').write_text(str(os.getpid()))
        time.sleep(60)
if name=='nix-shell':
    assert os.environ['NIX_PATH']=='nixpkgs=https://example.invalid/pinned.tar.gz'
    block('nix')
    if a[-1]=='klayout -v': print('KLayout '+mode.get('klayout','0.28.17')); sys.exit(0)
    sys.exit(subprocess.call(['/bin/sh','-c',a[-1]]))
if name=='docker':
    if a[0]=='info':
        print(json.dumps(dict(SecurityOptions=[],ServerVersion=mode.get('engine','29'),ID='daemon',Name='fixture'))); sys.exit(0)
    if a[:2]==['image','inspect']:
        print('sha256:'+('b' if mode.get('image_mismatch') else 'a')*64); sys.exit(0)
    if a[0]=='rm':
        block('cleanup')
        if mode.get('cleanup_fail'): print('fixture removal failure',file=sys.stderr); sys.exit(9)
        (root/'containers'/a[-1]).unlink(missing_ok=True)
        sys.exit(0)
    assert a[0]=='run' and '--pull=never' in a
    assert a[a.index('--name')+1].startswith('hardcaml-asic-execution-')
    (root/'containers').mkdir(exist_ok=True)
    (root/'containers'/a[a.index('--name')+1]).write_text('owned fixture container')
    if os.environ.get('OMP_NUM_THREADS')=='20': assert '--cpus=20' in a
    assert 'sha256:'+'a'*64 in a or 'ghcr.io/librelane/librelane:1.2.3' in a
    if 'iverilog' in a:
        if '-V' in a: block('probe'); print('Icarus 12\nextra version line'); sys.exit(0)
        block('compile')
        assert a[a.index('-s')+1]=='memory_tb'
        pathlib.Path(a[a.index('-o')+1]).write_text('compiled')
        print('compiled stdout'); print('compiled stderr',file=sys.stderr)
        sys.exit(mode.get('compile_exit',0))
    if 'vvp' in a:
        block('replay')
        for x in a:
            if x.startswith('+trace='): assert pathlib.Path(x[7:]).read_text()=='trace\n'
        print('replay stdout'); print('replay stderr',file=sys.stderr)
        os.write(1,b'ordered stdout 1\n'); os.write(2,b'ordered stderr 2\n'); os.write(1,b'ordered stdout 3\n')
        sys.exit(mode.get('replay_exit',0))
raise AssertionError((name,a))
'''

PRECHECK = '''import ctypes, json, os, pathlib, resource, signal, sys, time
root=pathlib.Path(os.environ['FIXTURE'])
mode=json.loads((root/'mode.json').read_text())
assert 'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ
if os.environ.get('OMP_NUM_THREADS')=='20': assert len(os.sched_getaffinity(0))<=20
assert sys.prefix!=sys.base_prefix
pathlib.Path('reports').mkdir(exist_ok=True)
pathlib.Path('reports/report.md').write_text('fixture report')
print('precheck stdout'); print('precheck stderr',file=sys.stderr)
if mode.get('kill')=='precheck':
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    assert ctypes.CDLL(None).prctl(4,0,0,0,0)==0
    os.kill(os.getpid(),mode['signal'])
if mode.get('block')=='precheck':
    (root/'blocked').write_text(str(os.getpid())); time.sleep(60)
sys.exit(mode.get('precheck_exit',0))
'''


def suite(cli, consumer, package, public):
    sys.path.insert(0,str(package.parent))
    from hardcaml_asic_flow import coarse_flow_oracle, ocaml_backend, cli as public_cli
    with tempfile.TemporaryDirectory(prefix="postcheck ' Ω ") as tmp:
        root=Path(tmp); tools=root/'tools'; pre=tools/'precheck'; pre.mkdir(parents=True)
        (tools/'tech').mkdir()
        (pre/'default.nix').write_text('let pkgs = import (fetchTarball "https://example.invalid/pinned.tar.gz") {}; in pkgs.mkShell {}')
        (pre/'tool-versions.json').write_text(json.dumps({'klayout':'0.28.17'}))
        (pre/'precheck.py').write_text(PRECHECK)
        bin=root/'bin'; bin.mkdir()
        for name in ('docker','nix-shell'):
            p=bin/name; p.write_text(FAKE.replace('INTERPRETER',sys.executable)); p.chmod(0o755)
        (bin/'env').symlink_to(shutil.which('env'))
        for name in ('dirname','readlink'):
            (bin/name).symlink_to(shutil.which(name))
        (bin/'python3').symlink_to(sys.executable)
        v=root/'precheck venv'; venv.EnvBuilder(with_pip=False).create(v)
        site=next((v/'lib').glob('python*/site-packages'))
        dist=site/'klayout-0.28.17.dist-info'; dist.mkdir(); metadata=dist/'METADATA'
        metadata.write_text('Name: klayout\nVersion: 0.28.17\n')
        alias=root/"alias ' python"; alias.symlink_to(v,target_is_directory=True)
        python=alias/'bin/../bin/python'
        tb=root/'tb.v'; tb.write_text('module memory_tb; endmodule\n')
        trace=root/'trace.txt'; trace.write_text('trace\n')
        pdk=root/'pdk'
        for relative in ('sg13cmos5l_io/verilog/sg13cmos5l_io.v','sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v','sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v'):
            p=pdk/'ihp-sg13cmos5l/libs.ref'/relative; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('// model\n')
        run=root/'run'; final=run/'project/runs/asic/final'
        (final/'gds').mkdir(parents=True); (final/'nl').mkdir()
        (final/'gds/top.gds').write_text('gds\n'); (final/'nl/top.nl.v').write_text('netlist\n')
        (run/'project/manifest.json').write_text('{"top_module":"top"}')
        binding=dict(schema_version=1,execution_mode='dockerized',interpreter='python3',interpreter_flags=['-I','-B'],
            engine_identity=dict(name='docker',executable=str(bin/'docker'),rootless=False,server_version='29',daemon_id='daemon',name_on_server='fixture'),
            image_identity=dict(binding='local-image-id',declared_ref='ghcr.io/librelane/librelane:1.2.3',image_id='sha256:'+'a'*64,repo_digests=[],platform=dict(os='linux',architecture='amd64')))
        context=dict(run_id='attempt',build_identity='build',status='completed',completed_stage='full',outputs={'flow':'project/runs/asic'},environment={'requested':{'librelane':'1.2.3'}})
        env=dict(os.environ,PATH=str(bin),FIXTURE=str(root),PYTHONPATH='/poison')
        args=SimpleNamespace(run_dir=run,support_tools=tools,pdk_root=pdk,precheck_python=python,testbench=tb,testbench_top='memory_tb')
        base=[str(cli),'--run-dir',str(run),'--support-tools',str(tools),'--pdk-root',str(pdk),'--precheck-python',str(python),'--testbench',str(tb),'--testbench-top','memory_tb','--json']
        def prepare(mode, bound=True):
            (root/'mode.json').write_text(json.dumps(mode)); (root/'calls.jsonl').write_text('')
            record=json.loads(json.dumps(context))
            if bound:
                record.update(execution_binding_required=True,execution_binding=json.loads(json.dumps(binding)))
                record['environment']['execution_binding']=json.loads(json.dumps(binding))
            (run/'run.json').write_text(json.dumps(record))
            return record
        def invoke(mode={}, bound=True, extra=()):
            prepare(mode,bound)
            r=subprocess.run([*base,*extra],env=env,capture_output=True,text=True,timeout=25)
            assert r.stdout,(r.returncode,r.stderr)
            o=json.loads(r.stdout); record=json.loads(Path(o['check_record']).read_text())
            assert r.returncode==o['exit_code'] and o['status']==record['status'],(o,record,r.stderr)
            assert record['ended_at'] and record['schema_version']==1
            assert len(o['check_id'])==32 and Path(o['check_dir']).name==o['check_id']
            return r,o,record
        count=0
        for bound in (False,True):
            for mode in ({},{'precheck_exit':3},{'compile_exit':4},{'replay_exit':5},{'klayout':'0.29.1'}):
                r,o,record=invoke(mode,bound)
                old=os.environ.copy()
                try:
                    os.environ.clear(); os.environ.update(env)
                    expected=coarse_flow_oracle.check(args)
                finally:
                    os.environ.clear(); os.environ.update(old)
                legacy=json.loads(expected.check_record.read_text())
                for k in ('schema_version','run_id','build_identity','status','checks','inputs','tools','reports','error'):
                    assert record.get(k)==legacy.get(k),(mode,bound,k,record.get(k),legacy.get(k),record.get('error'))
                assert o['exit_code']==expected.exit_code
                if record['commands']:
                    log=(Path(o['check_dir'])/'postcheck.log').read_text()
                    assert 'precheck stdout' in log and 'precheck stderr' in log
                    assert str(python) in record['commands'][0][-1].replace("'\\''", "'")
                count+=1
        # A child signal is a failed prerequisite/tool, never caller cancellation.
        for phase in ('nix','precheck','probe','compile','replay','cleanup'):
            for sig in (signal.SIGKILL,signal.SIGSEGV):
                mode={'kill':phase,'signal':sig}
                r,o,record=invoke(mode)
                assert r.returncode==1 and record['status']=='failed',(phase,sig,o,record)
                assert 'interrupted_by' not in record and 'SIGTERM' not in record.get('error','')
                if phase=='nix':
                    assert record['process_results'][0]['outcome']=='signaled'
                    assert record['commands']==[] and len(record['processes'])==1
                    old=os.environ.copy()
                    try:
                        os.environ.clear(); os.environ.update(env)
                        expected=coarse_flow_oracle.check(args)
                    finally:
                        os.environ.clear(); os.environ.update(old)
                    legacy=json.loads(expected.check_record.read_text())
                    assert legacy['status']==record['status'] and expected.exit_code==o['exit_code']
                    count+=1
                if phase=='precheck': assert record['checks']['precheck']=='fail' and record['checks']['gate_level']=='pass'
                if phase=='compile': assert record['checks']['gate_level']=='fail'
                if phase=='cleanup':
                    assert all(x['status']=='failed' for x in record['container_cleanup'])
                    killed=[x for x in record['container_cleanup'] if x['outcome']=='signaled']
                    assert killed and all(x['code_or_ocaml_signal'] is not None for x in killed),record['container_cleanup']
                    for marker in (root/'containers').iterdir(): marker.unlink()
        # Actual absolute flow paths and nested symlink aliases, including .. .
        flow=context['outputs']['flow']
        flow_alias=root/'flow alias'; flow_alias.symlink_to(run/'project',target_is_directory=True)
        nested_alias=root/'nested alias'; nested_alias.symlink_to(flow_alias,target_is_directory=True)
        for path in (str(final.parent),str(nested_alias/'runs/../runs/asic')):
            context['outputs']['flow']=path
            r,o,record=invoke()
            assert r.returncode==0,(path,r.stderr,record)
            old=os.environ.copy()
            try:
                os.environ.clear(); os.environ.update(env)
                expected=coarse_flow_oracle.check(args)
            finally:
                os.environ.clear(); os.environ.update(old)
            legacy=json.loads(expected.check_record.read_text())
            assert expected.exit_code==0 and record['inputs']==legacy['inputs']
            count+=1
        context['outputs']['flow']=flow
        r,o,record=invoke(extra=('--replay-trace',str(trace)))
        assert r.returncode==0 and record['inputs']['replay_trace_sha256']==hashlib.sha256(trace.read_bytes()).hexdigest()
        assert (Path(o['check_dir'])/'replay-trace.txt').read_bytes()==trace.read_bytes()
        assert 'ordered stdout 1\nordered stderr 2\nordered stdout 3\n' in (Path(o['check_dir'])/'postcheck.log').read_text()
        assert record['resource_limits']['tool_timeout_seconds'] is None
        assert record['resource_limits']['host_cpu_affinity_max']==20
        assert record['inputs']['gds_sha256']==hashlib.sha256((final/'gds/top.gds').read_bytes()).hexdigest()
        assert record['inputs']['netlist_sha256']==hashlib.sha256((final/'nl/top.nl.v').read_bytes()).hexdigest()
        assert record['inputs']['testbench_sha256']==hashlib.sha256(tb.read_bytes()).hexdigest()
        for process in record['processes']:
            assert (Path(o['check_dir'])/process['stdout']).is_file()
            assert (Path(o['check_dir'])/process['stderr']).is_file()
        helper_alias=root/'helper alias'; helper_alias.symlink_to(cli)
        prepare({})
        r=subprocess.run([str(helper_alias),*base[1:]],env=env,capture_output=True,text=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        for mode in ({'image_mismatch':True},{'engine':'changed'},{'cleanup_fail':True}):
            r,o,record=invoke(mode)
            assert r.returncode==1 and record['commands']==[] and record.get('error'),record
            if mode.get('cleanup_fail'):
                assert list((root/'containers').iterdir())
                assert all(x['status']=='failed' for x in record['container_cleanup'])
                for marker in (root/'containers').iterdir(): marker.unlink()
        metadata.write_text('Name: klayout\nVersion: 0.29.1\n')
        r,o,record=invoke(); assert r.returncode==1 and record['tools']['precheck_python_klayout']=='0.29.1'
        metadata.write_text('Name: klayout\nVersion: 0.28.17\n')
        # New records cannot silently use historical fallback (including null binding).
        for change in ('missing','null','native','saved'):
            rec=prepare({})
            if change=='missing': del rec['execution_binding']
            elif change=='null': rec['execution_binding']=None
            elif change=='native': rec['execution_binding']=dict(schema_version=1,execution_mode='native',interpreter=str(python),interpreter_flags=['-I','-B'])
            else: rec['environment']['execution_binding']['engine_identity']['server_version']='other'; rec['execution_binding']=binding
            (run/'run.json').write_text(json.dumps(rec))
            r=subprocess.run(base,env=env,capture_output=True,text=True); o=json.loads(r.stdout)
            record=json.loads(Path(o['check_record']).read_text()); assert r.returncode==1 and not record['commands'],record
        # Cancellation at host/probe/replay/cleanup boundaries must stop unrelated launches.
        for phase in ('nix','precheck','probe','compile','replay','cleanup'):
            for sig in (signal.SIGINT,signal.SIGTERM):
                prepare({'block':phase}); (root/'blocked').unlink(missing_ok=True)
                p=subprocess.Popen(base,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                deadline=time.monotonic()+10
                while not (root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline: time.sleep(.01)
                if not (root/'blocked').exists():
                    stdout,stderr=p.communicate(); failed=json.loads(stdout)
                    raise AssertionError((phase,stderr,json.loads(Path(failed['check_record']).read_text())))
                if phase=='precheck':
                    active=max((run/'checks').glob('*/postcheck.json'),key=lambda x:x.stat().st_mtime_ns)
                    assert 'precheck stderr' in (active.parent/'postcheck.log').read_text()
                p.send_signal(sig); stdout,stderr=p.communicate(timeout=15)
                o=json.loads(stdout); record=json.loads(Path(o['check_record']).read_text())
                assert p.returncode==128+sig and record['status']=='interrupted',(phase,o,record,stderr)
                calls=[json.loads(x) for x in (root/'calls.jsonl').read_text().splitlines()]
                if phase in ('probe','cleanup'): assert not record['commands']
                if phase=='precheck': assert len(record['commands'])==1
                if phase=='compile': assert not any('vvp' in a for _,a in calls)
                if phase in ('probe','compile','replay','cleanup'): assert record.get('container_cleanup')
                pid=int((root/'blocked').read_text())
                stat=Path('/proc')/str(pid)/'stat'
                assert not stat.exists() or stat.read_text().split(') ')[1][0]=='Z',(phase,pid)
                if phase!='cleanup': assert not list((root/'containers').iterdir())
                else:
                    assert all(x['status']=='failed' for x in record['container_cleanup'])
                    for marker in (root/'containers').iterdir(): marker.unlink()
        r,o,record=invoke({'block':'nix'},extra=('--timeout','0.15'))
        assert r.returncode==1 and 'timed out' in record['error']
        # In-memory finalization context and replay through a separately linked API consumer.
        rec=prepare({}); rec['evidence_origin']={'boundary':'signoff'}
        ctx=root/'context.json'; ctx.write_text(json.dumps(rec)); (run/'run.json').unlink()
        r=subprocess.run([str(consumer),str(run),str(tools),str(pdk),str(python),str(tb),str(ctx),str(trace)],env=env,text=True,capture_output=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        o=json.loads(r.stdout); record=json.loads(Path(o['check_record']).read_text())
        assert record['evidence_origin']==rec['evidence_origin'] and not (run/'run.json').exists()
        old=os.environ.copy()
        try:
            os.environ.clear(); os.environ.update(env)
            expected=coarse_flow_oracle.check(args,context=rec,replay_trace=trace)
        finally:
            os.environ.clear(); os.environ.update(old)
        legacy=json.loads(expected.check_record.read_text())
        for key in ('schema_version','run_id','build_identity','status','checks','inputs','tools','reports','evidence_origin'):
            assert record[key]==legacy[key],(key,record[key],legacy[key])
        count+=1
        prepare({})
        public_args=['postcheck',str(run),'--support-tools',str(tools),'--pdk-root',str(pdk),'--precheck-python',str(python),'--testbench',str(tb),'--testbench-top','memory_tb','--json']
        public_command=([sys.executable,'-I','-B','-c',
            f'import os,sys; sys.path.insert(0,{str(Path(__file__).parent)!r}); sys.path.insert(0,sys.argv.pop(1)); from reference_harness import activate; activate() if os.environ.get("FLOW_TEST_REFERENCE_ROOT") else None; from hardcaml_asic_flow.cli import main; sys.exit(main())',os.environ.get('FLOW_TEST_REFERENCE_ROOT',str(package.parent))]
            if public is None else [str(public)])
        r=subprocess.run([*public_command,*public_args],env=env,capture_output=True,text=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        assert json.loads(r.stdout)['status']=='completed'
        # Public optional replay/timeout are forwarded and recorded, including aliases.
        r=subprocess.run([*public_command,*public_args,'--replay-trace',str(trace),'--timeout','30'],env=env,capture_output=True,text=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        record=json.loads(Path(json.loads(r.stdout)['check_record']).read_text())
        assert record['resource_limits']['tool_timeout_seconds']==30
        assert 'replay_trace_sha256' in record['inputs']
        old=os.environ.copy()
        try:
            os.environ.clear(); os.environ.update(env)
            outcome=public_cli.Operations().postcheck(args)
            assert isinstance(outcome,coarse_flow_oracle.CheckOutcome) and outcome.exit_code==0
            original=ocaml_backend.binary
            ocaml_backend.binary=lambda _: (_ for _ in ()).throw(OSError('missing helper'))
            try:
                try: public_cli.Operations().postcheck(args)
                except OSError: pass
                else: raise AssertionError('fallback')
            finally: ocaml_backend.binary=original
        finally:
            os.environ.clear(); os.environ.update(old)
        prepare({}); before=set((run/'checks').iterdir()); tb.unlink()
        r=subprocess.run(base,env=env,capture_output=True,text=True)
        assert r.returncode==2 and set((run/'checks').iterdir())==before
        print(f'postcheck: {count} actual Python persisted-field/outcome differentials; versions, replay, caps, bindings, cancellation, cleanup, API/public callbacks passed')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--installed',action='store_true')
    parser.add_argument('--cli',type=Path); parser.add_argument('--consumer',type=Path); a=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if a.installed:
        with tempfile.TemporaryDirectory(prefix='postcheck-installed-',dir='/tmp/opencode') as tmp:
            tmp=Path(tmp); prefix=tmp/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(root, prefix)
            work=tmp/'consumer'; work.mkdir()
            shutil.copy2(root/'test/postcheck_consumer.ml',work/'postcheck_consumer.ml')
            (work/'dune-project').write_text('(lang dune 3.17)\n')
            (work/'dune').write_text('(executable (name postcheck_consumer) (libraries hardcaml_asic.execution unix))\n')
            env=dict(os.environ,OCAMLPATH=str(prefix/'lib')+':'+os.environ.get('OCAMLPATH',''))
            subprocess.run(['dune','build','-j20','postcheck_consumer.exe'],cwd=work,env=env,check=True)
            driver=tmp/'check_postcheck.py'; shutil.copy2(__file__,driver)
            subprocess.run([sys.executable,'-B',str(driver),'--prefix',str(prefix),'--consumer',str(work/'_build/default/postcheck_consumer.exe'),'--deny',str(root)],cwd=tmp,env=env,check=True)
    else:
        suite((a.cli or root/'_build/default/bin/experimental_postcheck.exe').resolve(),
              (a.consumer or root/'_build/default/test/postcheck_consumer.exe').resolve(),
              root/'flow/hardcaml_asic_flow',None)


if __name__=='__main__':
    if '--prefix' in sys.argv:
        p=argparse.ArgumentParser(); p.add_argument('--prefix',type=Path); p.add_argument('--consumer',type=Path); p.add_argument('--deny'); a=p.parse_args()
        def audit(event,args):
            if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and str(args[0]).startswith(a.deny+'/'):
                raise PermissionError('checkout reads forbidden')
        sys.addaudithook(audit)
        from reference_harness import activate
        activate()
        suite(a.prefix/'bin/hardcaml-asic-experimental-postcheck',a.consumer,a.prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow',a.prefix/'bin/hardcaml-asic-flow')
    else: main()
