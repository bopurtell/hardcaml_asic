"""Original provider fixtures, actual native subprocesses and installed consumers."""
import argparse
import copy
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
import unittest
from unittest.mock import patch
import venv

RETAINED=Path('/tmp/opencode/native-final-integration/finalizations/20261014T040711301534Z_913453bd')
FAKE=r'''#!PYTHON
import json,os,pathlib,signal,sys,time
sys.path.insert(0,PACKAGE)
from hardcaml_asic_flow import backend_protocol as protocol
root=pathlib.Path(os.environ['FIXTURE']);mode=json.loads((root/'mode.json').read_text());a=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(a)+'\n')
def block(phase):
 if mode.get('block')==phase:
  (root/'blocked').write_text(str(os.getpid()));time.sleep(60)
if a[0]=='info':
 block('probe');print(json.dumps(dict(SecurityOptions=[],ServerVersion='changed' if mode.get('engine') else 'fixture')));sys.exit(0)
if a[:2]==['image','inspect']:
 image='changed' if mode.get('image') else IMAGE
 print(image if '--format' in a else json.dumps([{'Id':image}]));sys.exit(0)
if a[0]=='rm':
 block('cleanup')
 if mode.get('cleanup_fail'):sys.exit(9)
 (root/'containers'/a[-1]).unlink(missing_ok=True);sys.exit(0)
strict=os.environ.get('HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT')=='6'
assert a[0]=='run' and ('--cpus=6' if strict else '--cpus=20') in a and '--pull=never' in a and IMAGE in a
assert ('--pids-limit=6' in a)==strict
(root/'containers').mkdir(exist_ok=True);(root/'containers'/a[a.index('--name')+1]).write_text('owned')
if 'iverilog' in a:
 if '-V' in a:print('Icarus fixture');sys.exit(0)
 block('compile');pathlib.Path(a[a.index('-o')+1]).write_text('compiled');sys.exit(mode.get('gate',0))
if 'vvp' in a:
 block('replay')
 if mode.get('selection_changed'):
  p=root/'execution.json';v=json.loads(p.read_text());v['artifacts']=[];p.write_text(json.dumps(v))
 sys.exit(0)
request=protocol.validate_request(protocol.loads(pathlib.Path(a[-1]).read_text()))
assert request['operation']=='validate_publication' and request['checkpoint'] is None
block('runtime')
case=mode.get('case','valid')
if case=='interrupt':os.kill(os.getppid(),signal.SIGTERM);time.sleep(60)
if case=='nonzero':sys.exit(9)
if case=='missing':sys.exit(0)
reply=protocol.reply(request,error='fixture failure' if case=='failed' else None)
if case=='wrong_request':reply['request_sha256']='0'*64
if case=='wrong_version':reply['protocol_version']=2
if case=='changed_bytes':reply['artifacts']['closure.json']['sha256']='0'*64
pathlib.Path(request['reply_path']).write_text(protocol.canonical(reply))
if mode.get('mutate'):
 p=pathlib.Path(mode['mutate']);p.write_text(p.read_text()+' ')
'''


def suite(cli,consumer,package,fixtures,public=None):
    sys.path.insert(0,str(package.parent));sys.path.insert(0,str(fixtures))
    from hardcaml_asic_flow import finalization,phase4,checkpoint,backend_protocol,report,presentation,cli as public_cli,ocaml_backend
    import test_finalization
    count=0
    original_setup=test_finalization.FinalizationTest.setUp
    original_invoke=test_finalization.FinalizationTest.invoke
    original_publication=test_finalization.FinalizationTest.publication
    original_teardown=test_finalization.FinalizationTest.tearDown
    tmp_factory=tempfile.TemporaryDirectory
    def teardown(f):
        for p in f.root.rglob('*'):
            if p.is_symlink():p.unlink()
        original_teardown(f)
    def publication(f,boundary,metrics=None):
        if f._testMethodName=='test_runtime_validation_requires_correlated_v1_reply_and_artifact_bytes':
            return original_publication(f,boundary,metrics)
        if not (f.project/'bridge').exists():
            (f.project/'bridge').mkdir()
            for name in ('librelane_bridge.py','backend_protocol.py'):
                shutil.copy2(package/name,f.project/'bridge'/name)
                (f.project/'bridge'/name).chmod(0o644)
            f.compat['bridge_sha256']=checkpoint.digest(f.project/'bridge/librelane_bridge.py')
            f.binding['engine_identity']['executable']=str(f.root/'bin/docker')
        return original_publication(f,boundary,metrics)
    def setup(f):
        with patch.object(test_finalization.tempfile,'TemporaryDirectory',side_effect=lambda:tmp_factory(prefix="final ' Ω ")):
            original_setup(f)
        if f._testMethodName=='test_runtime_validation_requires_correlated_v1_reply_and_artifact_bytes':return
        bin=f.root/'bin';bin.mkdir()
        script=FAKE.replace('PYTHON',sys.executable,1).replace('PACKAGE',repr(str(package.parent))).replace('IMAGE',repr(f.binding['image_identity']['image_id']))
        (bin/'docker').write_text(script);(bin/'docker').chmod(0o755)
        (bin/'nix-shell').write_text('#!'+sys.executable+'\nimport os,sys,json\nfrom pathlib import Path\n'
            'mode=json.loads((Path(os.environ["FIXTURE"])/"mode.json").read_text())\n'
            'if sys.argv[-1]=="klayout -v":print("KLayout 0.28.17")\n'
            'elif "importlib.metadata" in sys.argv[-1]: print("0.28.17")\n'
            'else:sys.exit(mode.get("precheck",0))\n')
        (bin/'nix-shell').chmod(0o755)
        (bin/'env').symlink_to(shutil.which('env'))
        for name in ('dirname','readlink'):(bin/name).symlink_to(shutil.which(name))
        (bin/'python3').symlink_to(sys.executable)
        f.native_env=dict(os.environ,PATH=str(bin),FIXTURE=str(f.root))
        (f.root/'mode.json').write_text('{}');(f.root/'calls.jsonl').write_text('')
        # Actual lexical venv for native metadata probes.
        f.python.unlink();v=f.root/'venv';venv.EnvBuilder(with_pip=False).create(v)
        site=next((v/'lib').glob('python*/site-packages'));dist=site/'klayout-0.28.17.dist-info';dist.mkdir()
        (dist/'METADATA').write_text('Name: klayout\nVersion: 0.28.17\n')
        f.python=v/'bin/python'
        (f.support/'precheck/default.nix').write_text('let pkgs = import (fetchTarball "https://example.invalid/pinned.tar.gz") {}; in pkgs.mkShell {}')
        (f.support/'precheck/precheck.py').write_text('')
    def native(f,accept=False,mode=None,extra=(),public_call=False):
        (f.root/'mode.json').write_text(json.dumps(mode or {}))
        phase4.save(f.support/'precheck/tool-versions.json',{'klayout':'0.28.17'})
        if public_call:
            base=[str(public)] if public else [sys.executable,'-B','-m','hardcaml_asic_flow']
            base+=['finalize',str(f.execution),'--operation-store',str(f.root/'native'),'--json']
            env=dict(f.native_env,PYTHONPATH=str(package.parent))
        else:
            base=[str(cli),'--execution',str(f.execution),'--store',str(f.root/'native'),'--json'];env=f.native_env
        if accept:base+=['--accept','--support-tools',str(f.support),'--precheck-python',str(f.python),
                        '--testbench',str(f.bench),'--testbench-top','replay','--replay-trace',str(f.trace)]
        r=subprocess.run([*base,*extra],env=env,text=True,capture_output=True,timeout=30)
        lines=[json.loads(x) for x in r.stdout.splitlines()]
        assert lines,(r.returncode,r.stderr)
        if public_call:assert lines[0]['event']=='allocated',r
        value=lines[-1];directory=Path(value.pop('attempt'))
        assert r.returncode==value['exit_code'] and finalization.inspect(directory)==value,(r,value)
        assert not (directory/'run.json').exists()
        markers=list((f.root/'containers').glob('*'))
        if (mode or {}).get('cleanup_fail'):
            assert markers and value['status']=='failed'
            for p in markers:p.unlink()
        else:assert not markers,(mode,r.stderr)
        return directory,value
    def invoke(f,**kwargs):
        nonlocal count
        before=f.execution.read_bytes()
        expected_dir,expected=original_invoke(f,**kwargs)
        mode={}
        changed=before!=f.execution.read_bytes()
        if changed:
            f.execution.write_bytes(before);mode['selection_changed']=True
        if kwargs.get('runtime_error'):
            mode['case']='interrupt' if isinstance(kwargs['runtime_error'],result_store.FlowInterrupted) else 'nonzero'
        mode.update(precheck=kwargs.get('precheck',0),gate=kwargs.get('gate',0))
        actual_dir,actual=native(f,accept=kwargs.get('accept',False) or kwargs.get('public_cli',False),mode=mode)
        for key in ('status','acceptance','external_postcheck','exit_code'):
            assert actual[key]==expected[key],(key,actual[key],expected[key],actual.get('error'))
        if expected['results'] is not None:
            from resource_evidence_compat import collection_expected, historical_human
            exp=collection_expected(phase4.read_json(expected_dir/'results.json'));act=phase4.read_json(actual_dir/'results.json')
            def normalize(v):
                return json.loads(json.dumps(v,ensure_ascii=False).replace(str(actual_dir),str(expected_dir)).replace(actual['attempt_id'],expected['attempt_id']))
            for key in ('metrics','synthesis_checks','checks','provenance'):
                assert normalize(act[key])==exp[key],(key,act[key],exp[key])
            context=normalize(actual['evidence_context'])
            assert context.pop('manifest_sha256')==hashlib.sha256((actual_dir/'project/manifest.json').read_bytes()).hexdigest()
            assert context==expected['evidence_context']
            if kwargs.get('accept') or kwargs.get('public_cli'):
                output=[];code=report.report(actual_dir,act,'segmented mapped evidence',context=actual['evidence_context'],output=output.append)
                assert code==actual['exit_code'] and historical_human((actual_dir/'acceptance.txt').read_text())=='\n'.join(output)+'\n'
        if before==f.execution.read_bytes(): assert before==f.execution.read_bytes()
        phase4.save(f.support/'precheck/tool-versions.json',{'klayout':'fixture'})
        count+=1
        return expected_dir,expected
    from hardcaml_asic_flow import result_store
    with patch.object(test_finalization.FinalizationTest,'setUp',setup),patch.object(test_finalization.FinalizationTest,'tearDown',teardown),patch.object(test_finalization.FinalizationTest,'publication',publication),patch.object(test_finalization.FinalizationTest,'invoke',invoke):
        result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(test_finalization.FinalizationTest))
        assert result.wasSuccessful()

    with patch.object(test_finalization.FinalizationTest,'publication',publication):
        f=test_finalization.FinalizationTest();setup(f)
        try:
            original=copy.deepcopy(f.value)
            # Failures are allocated and persisted before any runtime launch.
            for value in ({},dict(original,exit_code=True),dict(original,occurrences=[]),dict(original,artifacts=[]),
                          dict(original,plan=test_finalization.plans.resolve_stages('signoff',resume=True,initial_boundary='synthesis').as_dict())):
                phase4.save(f.execution,value);_,receipt=native(f);assert receipt['status']=='failed' and receipt['results'] is None
            phase4.save(f.execution,original)
            for path in ('relative','/no/such/checkpoint',original['artifacts'][0]['path']+'\x00'):
                value=copy.deepcopy(original);value['artifacts'][0]['path']=path;phase4.save(f.execution,value)
                _,receipt=native(f);assert receipt['status']=='failed' and receipt['results'] is None
            phase4.save(f.execution,original)
            first=Path(original['artifacts'][0]['path']);manifest=first/'manifest.json';manifest.chmod(0o644);saved=manifest.read_bytes()
            legacy=json.loads(saved);legacy['schema_version']=1
            for key in ('restart_targets','tested_image_id','closure_scope'):del legacy[key]
            legacy['identity']=checkpoint.identity({k:v for k,v in legacy.items() if k!='identity'})
            manifest.write_text(json.dumps(legacy));value=copy.deepcopy(original);value['artifacts'][0]['identity']=legacy['identity'];phase4.save(f.execution,value)
            _,receipt=native(f);assert receipt['status']=='failed' and 'schema 2' in receipt['error'],receipt['error']
            manifest.write_bytes(saved);phase4.save(f.execution,original)
            manifests={Path(item['path'])/'manifest.json':(Path(item['path'])/'manifest.json').read_bytes() for item in original['artifacts']}
            bad=copy.deepcopy(f.binding);bad['engine_identity']['executable']='relative'
            for binding in (None,dict(f.binding,schema_version=True),bad,{'schema_version':1,'execution_mode':'native','interpreter':sys.executable,'interpreter_flags':['-I','-B']}):
                value=copy.deepcopy(original)
                for artifact in value['artifacts']:
                    p=Path(artifact['path'])/'manifest.json';p.chmod(0o644);m=json.loads(manifests[p]);m['compatibility']['binding']=binding
                    m['identity']=checkpoint.identity({k:v for k,v in m.items() if k!='identity'});p.write_text(json.dumps(m));artifact['identity']=m['identity']
                phase4.save(f.execution,value);_,receipt=native(f);assert receipt['status']=='failed' and receipt['results'] is None
            for p,data in manifests.items():p.write_bytes(data)
            phase4.save(f.execution,original)
            _,receipt=native(f,extra=['--store',str(f.root/'colon:store')]);assert receipt['status']=='failed' and receipt['results'] is None
            for case in ('missing','wrong_request','wrong_version','failed','changed_bytes','nonzero','interrupt'):
                _,receipt=native(f,mode={'case':case});assert receipt['status']==('interrupted' if case=='interrupt' else 'failed')
                assert receipt['results'] is None
            for mode in ({'engine':True},{'image':True},{'cleanup_fail':True},{'mutate':str(f.execution)}):
                _,receipt=native(f,mode=mode);assert receipt['status']=='failed' and receipt['results'] is None
                phase4.save(f.execution,original)
            # Byte-pinned resources, including bridge control changes, fail before launch.
            for name in ('librelane_bridge.py','backend_protocol.py'):
                p=f.project/'bridge'/name;before=p.read_bytes();p.write_bytes(before+b'\n')
                _,receipt=native(f);assert receipt['status']=='failed';p.write_bytes(before)
            directory,receipt=native(f,public_call=True)
            r=subprocess.run([str(consumer),'run',str(f.execution),str(f.root/'consumer')],env=f.native_env,text=True,capture_output=True)
            assert r.returncode==0 and json.loads(r.stdout)['status']=='completed',r.stderr
            if public:
                accepted,verdict=native(f,accept=True,public_call=True);assert verdict['acceptance']=='pass'
            # Historical snapshot parity and read-only inspection with unusable PATH.
            lines=[];presentation.finalization(receipt,path=directory,output=lines.append)
            snap=(directory/'operation.json').read_bytes()
            r=subprocess.run([str(cli),'--inspect','--directory',str(directory),'--public-cli4'],env=dict(os.environ,PATH='/no-tools'),text=True,capture_output=True)
            assert r.returncode==0 and r.stdout=='\n'.join(lines)+'\n',(r.stdout,lines)
            assert (directory/'operation.json').read_bytes()==snap
            inspector=cli.with_name('hardcaml-asic-experimental-finalization-inspect' if public else 'experimental_finalization_inspect.exe')
            r=subprocess.run([str(inspector),str(directory),'--json'],env=dict(os.environ,PATH='/no-tools',PYTHONPATH='/no-python'),text=True,capture_output=True)
            assert r.returncode==0 and json.loads(r.stdout)==receipt
            base=[str(public)] if public else [sys.executable,'-B','-m','hardcaml_asic_flow']
            calls=(f.root/'calls.jsonl').read_bytes()
            for arguments in (['--json','finalization-inspect',str(directory)],['finalization-inspect',str(directory)]):
                r=subprocess.run([*base,*arguments],env=dict(f.native_env,PYTHONPATH=str(package.parent)),text=True,capture_output=True)
                assert r.returncode==0 and (json.loads(r.stdout)==receipt if '--json' in arguments else r.stdout=='\n'.join(lines)+'\n'),r.stderr
            assert calls==(f.root/'calls.jsonl').read_bytes() and (directory/'operation.json').read_bytes()==snap
            (f.root/'mode.json').write_text('{}')
            r=subprocess.run([str(cli),'--execution',str(f.execution),'--store',str(f.root/'human'),'--public-cli4'],env=f.native_env,text=True,capture_output=True)
            assert r.returncode==0,r.stderr
            human_dir=next((f.root/'human').iterdir());human_receipt=finalization.inspect(human_dir);human=[]
            presentation.allocation(dict(event='allocated',attempt=str(human_dir),attempt_id=human_receipt['attempt_id']),output=human.append)
            presentation.finalization(human_receipt,path=human_dir,output=human.append)
            assert r.stdout=='\n'.join(human)+'\n'
            # Schema/lifecycle validation compared with the actual historical reader.
            for value in (dict(receipt,acceptance='pass'),dict(receipt,schema_version=True),dict(receipt,schema_version=1.0),
                          dict(receipt,exit_code=True),dict(receipt,ended_at=None),dict(receipt,extra='unknown')):
                phase4.save(directory/'operation.json',value)
                try:finalization.inspect(directory)
                except ValueError:pass
                else:raise AssertionError(value)
                r=subprocess.run([str(consumer),'inspect',str(directory)],capture_output=True)
                assert r.returncode==2
            (directory/'operation.json').write_bytes(snap)
            historical=copy.deepcopy(receipt);historical['binding']={'historical':'unsupported current runtime'}
            historical['selected_checkpoints'][0]['path']='/not-present-historical-checkpoint'
            phase4.save(directory/'operation.json',historical)
            r=subprocess.run([str(consumer),'inspect',str(directory)],text=True,capture_output=True)
            assert r.returncode==0 and json.loads(r.stdout)==historical
            (directory/'operation.json').write_bytes(snap)
            for value in (dict(receipt,status='failed',exit_code=10**100),dict(receipt,status='running',ended_at=None,**{})):
                if value['status']=='running':del value['exit_code']
                phase4.save(directory/'operation.json',value);expected=finalization.inspect(directory)
                r=subprocess.run([str(consumer),'inspect',str(directory)],text=True,capture_output=True)
                assert r.returncode==0 and json.loads(r.stdout)==expected
            (directory/'operation.json').write_bytes(snap)
            # Cancellation at real probe, bridge and owned-container cleanup boundaries.
            for phase in ('probe','runtime','cleanup'):
                for signum in (signal.SIGINT,signal.SIGTERM):
                    (f.root/'blocked').unlink(missing_ok=True);(f.root/'mode.json').write_text(json.dumps({'block':phase}))
                    p=subprocess.Popen([str(cli),'--execution',str(f.execution),'--store',str(f.root/'signals'),'--json'],env=f.native_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    deadline=time.monotonic()+10
                    while not (f.root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                    assert (f.root/'blocked').exists(),p.communicate()
                    p.send_signal(signum)
                    if phase=='cleanup': (f.root/'mode.json').write_text('{}')
                    stdout,stderr=p.communicate(timeout=15);v=json.loads(stdout)
                    assert p.returncode==128+signum and v['status']=='interrupted',(stdout,stderr)
            for phase in ('compile','replay'):
                (f.root/'blocked').unlink(missing_ok=True);(f.root/'mode.json').write_text(json.dumps({'block':phase}))
                phase4.save(f.support/'precheck/tool-versions.json',{'klayout':'0.28.17'})
                p=subprocess.Popen([str(cli),'--execution',str(f.execution),'--store',str(f.root/'signals'),'--json','--accept',
                    '--support-tools',str(f.support),'--precheck-python',str(f.python),'--testbench',str(f.bench),'--testbench-top','replay'],
                    env=f.native_env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                deadline=time.monotonic()+15
                while not (f.root/'blocked').exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                assert (f.root/'blocked').exists(),p.communicate()
                p.send_signal(signal.SIGTERM);stdout,stderr=p.communicate(timeout=15);v=json.loads(stdout)
                assert p.returncode==143 and v['status']=='interrupted' and v['external_postcheck']=='interrupted' and v['acceptance']=='not_evaluated',(stdout,stderr)
            # Missing installed helper is authoritative, no Python fallback.
            with patch.object(ocaml_backend,'binary',side_effect=OSError('missing native helper')):
                assert public_cli.main(['finalize',str(f.execution),'--operation-store',str(f.root/'none')])==2
        finally:teardown(f)
    # The opt-in route collects a genuine synthetic synthesis checkpoint without
    # physical views, and exercises actual OCaml transport/publication/collector.
    with patch.object(test_finalization.FinalizationTest,'publication',publication):
        f=test_finalization.FinalizationTest();setup(f)
        try:
            f.native_env['HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT']='6'
            value=copy.deepcopy(f.value)
            value.update(plan=dict(schema_version=4,stages=['synthesis'],initial_boundary=None),
                         artifacts=value['artifacts'][:1],occurrences=value['occurrences'][:1])
            phase4.save(f.execution,value)
            checkpoint_dir=Path(value['artifacts'][0]['path'])
            # Published checkpoint bytes remain immutable; absent mapped physical
            # outputs below prove the synthesis-only collection scope.
            (f.root/'mode.json').write_text('{}')
            result=subprocess.run([str(cli),'--execution',str(f.execution),'--store',str(f.root/'strict'), '--json'],
                                  env=f.native_env,text=True,capture_output=True,timeout=30)
            receipt=json.loads(result.stdout)
            assert result.returncode==0, (result.stdout,result.stderr)
            directory=Path(receipt['attempt'])
            assert receipt['execution_envelope']['total_container_tasks']==6
            assert receipt['acceptance']=='not_evaluated' and receipt['external_postcheck']=='not_evaluated'
            assert receipt['evidence_context']['completed_stage']=='synthesis'
            assert not (directory/'evidence/signoff-state.json').exists()
            assert not (directory/'evidence/final/gds').exists() and not (directory/'evidence/final/lef').exists()
            original_netlist=Path(json.loads((checkpoint_dir/'state.json').read_text())['nl'])
            assert (directory/'evidence/final/nl/fixture_top.nl.v').read_bytes()==original_netlist.read_bytes()
            assert '--pids-limit=6' in receipt['commands'][0] and '--cpus=6' in receipt['commands'][0]
            collected=json.loads((directory/'results.json').read_text())
            for group in ('metrics','synthesis_checks','checks'):
                assert all(item.get('origin_boundary')=='synthesis' for item in collected[group].values())
            result=subprocess.run([str(cli),'--execution',str(f.execution),'--store',str(f.root/'strict-accept'), '--json','--accept'],
                                  env=f.native_env,text=True,capture_output=True,timeout=30)
            assert result.returncode!=0 and 'does not support --accept' in result.stdout
            phase4.save(f.execution,f.value)
            result=subprocess.run([str(cli),'--execution',str(f.execution),'--store',str(f.root/'strict-physical'), '--json'],
                                  env=f.native_env,text=True,capture_output=True,timeout=30)
            assert result.returncode!=0 and 'complete fresh recipe' in result.stdout
        finally:teardown(f)
    if RETAINED.exists():
        def snapshot(root):return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        before=snapshot(RETAINED);receipt=finalization.inspect(RETAINED);execution=Path(receipt['execution']['path'])
        source_before=hashlib.sha256(execution.read_bytes()).hexdigest()
        assert subprocess.run([str(consumer),'select',str(execution)],capture_output=True).returncode==0
        r=subprocess.run([str(consumer),'normalize',str(RETAINED)],text=True,capture_output=True);assert r.returncode==0,r.stderr
        from resource_evidence_compat import collection_expected
        actual=json.loads(r.stdout);expected=collection_expected(phase4.collect(RETAINED,context=receipt['evidence_context'],synthesis_state=RETAINED/'evidence/synthesis-state.json'))
        assert actual['results']==expected and actual['exit_code']==report.report(RETAINED,expected,'fixture',context=receipt['evidence_context'],output=lambda _:None)==0
        assert snapshot(RETAINED)==before and hashlib.sha256(execution.read_bytes()).hexdigest()==source_before
        print('retained accepted finalization and source execution: read-only selection/normalization/policy parity')
    print(f'finalization: {count} original fixture invocation differentials; actual protocol, runtime, cleanup, cancellation, public and immutable inspection checks passed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--installed',action='store_true');p.add_argument('--real-smoke',action='store_true');p.add_argument('--cli',type=Path);p.add_argument('--consumer',type=Path)
    p.add_argument('--prefix',type=Path);p.add_argument('--fixtures',type=Path);p.add_argument('--deny');a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    if a.real_smoke:
        sys.path.insert(0,str(root/'flow'))
        from hardcaml_asic_flow import finalization,phase4,checkpoint,report
        receipt=finalization.inspect(RETAINED);execution=Path(receipt['execution']['path'])
        value,compatibility,pubs=finalization.selected(execution)
        inputs={execution,*(RETAINED.rglob('*'))}
        for directory,manifest in pubs:
            inputs.add(directory/'manifest.json')
            inputs.update(directory/p for p in manifest['files'])
            inputs.update(Path(p) for p in manifest['external'])
        before={str(p):checkpoint.file_record(p) for p in inputs if p.is_file()}
        r=subprocess.run([str((a.cli or root/'_build/default/bin/experimental_finalization.exe').resolve()),'--execution',str(execution),
            '--store','/tmp/opencode/native-finalization-native-smoke','--public-cli4','--json'],text=True,capture_output=True,timeout=180)
        assert r.returncode==0,(r.stdout,r.stderr)
        actual=json.loads(r.stdout.splitlines()[-1]);directory=Path(actual.pop('attempt'))
        assert actual['status']=='completed' and actual['acceptance']=='not_evaluated' and not (directory/'run.json').exists()
        from resource_evidence_compat import collection_expected
        expected=collection_expected(phase4.collect(directory,context=actual['evidence_context'],synthesis_state=directory/'evidence/synthesis-state.json'))
        expected['provenance']={'contract':finalization.CONTRACT,'operation':str(directory/'operation.json'),
            'selected_evidence':actual['selected_evidence'],'adapter':actual['adapter']}
        for group in ('metrics','synthesis_checks','checks'):
            for item in expected[group].values():item['origin_boundary']='synthesis' if item['stage']=='Yosys.Synthesis' else 'signoff'
        assert phase4.read_json(directory/'results.json')==expected
        consumer=(a.consumer or root/'_build/default/test/finalization_consumer.exe').resolve()
        normalized=subprocess.run([str(consumer),'normalize',str(directory)],text=True,capture_output=True);assert normalized.returncode==0,normalized.stderr
        assert json.loads(normalized.stdout)['exit_code']==report.report(directory,expected,'segmented mapped evidence',context=actual['evidence_context'],output=lambda _:None)==1
        import csv,io
        stream=io.StringIO(newline='');csv.writer(stream).writerows(sorted(phase4.read_json(directory/'evidence/signoff-state.json')['metrics'].items()))
        assert (directory/'evidence/final/metrics.csv').read_bytes()==stream.getvalue().encode()
        assert {p:checkpoint.file_record(p) for p in before}==before
        print('real exact-image closure validation + native collection, Python bytes/normalization/policy parity:',directory)
    elif a.prefix:
        def audit(event,args):
            if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and str(args[0]).startswith(a.deny+'/'):raise PermissionError('checkout reads forbidden')
        sys.addaudithook(audit)
        from reference_harness import activate
        activate()
        suite(a.prefix/'bin/hardcaml-asic-experimental-finalization',a.consumer,a.prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow',a.fixtures,a.prefix/'bin/hardcaml-asic-flow')
    elif a.installed:
        with tempfile.TemporaryDirectory(prefix='finalization-installed-',dir='/tmp/opencode') as tmp:
            tmp=Path(tmp);prefix=tmp/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(root, prefix)
            work=tmp/'consumer';work.mkdir();shutil.copy2(root/'test/finalization_consumer.ml',work/'finalization_consumer.ml')
            (work/'dune-project').write_text('(lang dune 3.17)\n');(work/'dune').write_text('(executable (name finalization_consumer) (libraries hardcaml_asic.execution unix))\n')
            env=dict(os.environ,OCAMLPATH=str(prefix/'lib')+':'+os.environ.get('OCAMLPATH',''))
            subprocess.run(['dune','build','-j20','finalization_consumer.exe'],cwd=work,env=env,check=True)
            for name in ('check_finalization.py','test_finalization.py'):shutil.copy2(root/'test'/name,tmp/name)
            # Retained evidence refers to source paths: compare separately in source mode.
            subprocess.run([sys.executable,'-B',str(tmp/'check_finalization.py'),'--prefix',str(prefix),'--fixtures',str(tmp),'--consumer',str(work/'_build/default/finalization_consumer.exe'),'--deny',str(root)],cwd=tmp,env=env,check=True)
    else:suite((a.cli or root/'_build/default/bin/experimental_finalization.exe').resolve(),(a.consumer or root/'_build/default/test/finalization_consumer.exe').resolve(),root/'flow/hardcaml_asic_flow',Path(__file__).resolve().parent)

if __name__=='__main__':main()
