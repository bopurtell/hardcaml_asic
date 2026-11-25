"""Subprocess standalone preflight differential; synthetic runtimes, no EDA."""
import argparse
import copy
import json
from itertools import product
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import venv


FAKE = r'''#!INTERPRETER
import json, os, pathlib, signal, sys, time
a = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE'])
mode = json.loads((root/'mode.json').read_text())
with (root/'calls').open('a') as f: f.write(json.dumps([sys.argv[0],a])+'\n')
if mode.get('block') and pathlib.Path(sys.argv[0]).name == 'python':
    (root/'blocked').write_text(str(os.getpid()))
    time.sleep(60)
if pathlib.Path(sys.argv[0]).name == 'git':
    assert os.environ['GIT_OPTIONAL_LOCKS'] == '0'
    print('revision' if 'rev-parse' in a else (' M collateral' if mode.get('dirty') else ''))
    sys.exit(0)
assert not any(k in os.environ for k in ('PYTHONHOME','TCL_LIBRARY','LD_PRELOAD'))
if pathlib.Path(sys.argv[0]).name == 'docker':
    if a[0] == 'info':
        print(json.dumps(dict(ServerVersion='29.0', SecurityOptions=['rootless'], ID='daemon', Name='fixture')))
        sys.exit(0)
    if a[:2] == ['image','inspect']:
        print(json.dumps([dict(Id='sha256:'+'a'*64, RepoDigests=[], Os='linux', Architecture='amd64')]))
        sys.exit(0)
    if a[0] == 'rm': sys.exit(0)
    if mode.get('block_container'):
        (root/'blocked').write_text(str(os.getpid()))
        time.sleep(60)
    assert '--pull=never' in a
    if not mode.get('stage_probe'):
        assert '--read-only' in a and '--network' in a
        assert '-v' not in a and '--mount' not in a
    host = False
else: host = True
if mode.get('storage_signal') and host == (mode['storage_signal']=='host'):
    output = pathlib.Path(os.readlink('/proc/self/fd/1'))
    if mode.get('storage_readonly'):
        output.parent.chmod(0o500)
    targets = [] if mode.get('storage_readonly') else mode.get('storage_paths', [str(output.with_suffix('.json'))])
    for target in targets:
        evidence = pathlib.Path(target)
        if not evidence.is_absolute(): evidence = output.parent/evidence
        evidence.unlink(); evidence.mkdir()
        (evidence/'user-owned').write_text('retain me')
    if mode.get('signal'):
        os.kill(os.getppid(), mode['signal'])
        time.sleep(60)
code = a[a.index('-c')+1]
value = dict(python_version=mode.get('host_python','3.11.9') if host else '3.13.1',
             librelane_version=mode.get('librelane','3.1.0.dev3'), requires_python='>=3.10', metadata_error=None)
if mode.get('fallback') and not host and '-I' in a:
    value.update(librelane_version=None, requires_python=None, metadata_error='isolated metadata unavailable')
if host: value['tcl'] = dict(ready=not mode.get('tcl_fail'), version='8.6', error=None)
if mode.get('bad_json'): print('not json'); sys.exit(0)
if mode.get('nonzero'): print('fake runtime failure',file=sys.stderr); sys.exit(7)
if 'stage_runtime_metadata' in code:
    value = dict(protocol_version=1,kind='stage_runtime_metadata',role='host' if host else 'container',metadata=value)
print(json.dumps(value))
'''


def suite(cli, package, public, consumer=None):
    sys.path.insert(0, str(package.parent))
    from hardcaml_asic_flow import coarse_flow_oracle, runtime_contract, result_store
    with tempfile.TemporaryDirectory(prefix='preflight differential ') as tmp:
        root = Path(tmp)
        bin = root/'bin'
        bin.mkdir()
        for name in ('git', 'python', 'docker'):
            p = bin/name
            p.write_text(FAKE.replace('INTERPRETER', sys.executable))
            p.chmod(0o755)
        bundle = root/'bundle'
        bundle.mkdir()
        tools, pdk = root/'tools', root/'pdk'
        tools.mkdir(); pdk.mkdir()
        (bundle/'data').write_text('data')
        (tools/'collateral').write_text('collateral')
        requested = dict(python='3.11', librelane='3.1.0.dev3', support_tools_revision='revision', pdk_revision='revision')
        def official_policy():
            policy=runtime_contract.policy_from_requested(requested)
            policy['implementation']['dockerized'].update(policy='official-librelane-image',
                python=dict(policy='distribution-supported',requires_python='>=3.10',supported_minors=list(runtime_contract.SUPPORTED_MINORS)))
            return {k:policy[k] for k in ('schema_version','host','implementation')}
        manifest = dict(schema_version=1, adapter='LibreLane', requested_tools=requested,
            files=[dict(path='data',sha256=coarse_flow_oracle.digest(bundle/'data'))],
            target=dict(technology='tech', external_references=[dict(source='Support_tools',role='test',path='collateral')]))
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        env = dict(os.environ, PATH=str(bin), FIXTURE=str(root))
        base = [str(cli),'--bundle',str(bundle),'--support-tools',str(tools),'--pdk-root',str(pdk),
                '--python',str(bin/'python'),'--resources',str(package),'--results-root',str(root/'results'),'--json']
        checks = 0
        def invoke(mode, native, output=None, command=None):
            (root/'mode.json').write_text(json.dumps(mode))
            cmd = command or base
            return subprocess.run([*cmd, *(['--native'] if native else []),
                *(['--output',str(output)] if output else [])],env=env,capture_output=True,text=True,timeout=15)
        for explicit_policy,native in product((False,True),(True,False)):
            manifest['requested_tools']=copy.deepcopy(requested)
            if explicit_policy: manifest['requested_tools']['runtime_policy']=official_policy()
            (bundle/'manifest.json').write_text(json.dumps(manifest))
            for mode in ({}, {'dirty':True}, {'tcl_fail':True}, {'host_python':'3.12.1'},
                         {'librelane':'other'}, {'fallback':True}):
                result = invoke(mode,native)
                actual = json.loads(result.stdout)
                old = os.environ.copy()
                try:
                    os.environ.clear(); os.environ.update(env)
                    expected = coarse_flow_oracle.preflight(bundle,tools,pdk,bin/'python',native=native)
                finally:
                    os.environ.clear(); os.environ.update(old)
                assert actual == expected, (mode,native,actual,expected,result.stderr)
                assert result.returncode == (0 if expected['ready'] else 1), result
                report = Path(result.stderr.split('preflight: report ')[1].splitlines()[0])
                assert json.loads(report.read_text()) == actual
                receipt = json.loads((report.parent/'operation.json').read_text())
                assert report == report.parent/receipt['report']
                assert receipt['exit_code'] == result.returncode and receipt['ended_at']
                checks += 1
        manifest['requested_tools']=requested
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        # Malformed bundle policy returns before any external probe.
        broken = copy.deepcopy(manifest)
        broken['requested_tools']['runtime_policy'] = {}
        (bundle/'manifest.json').write_text(json.dumps(broken))
        before = (root/'calls').read_text()
        failed = invoke({},True)
        assert failed.returncode == 1 and 'runtime policy:' in json.loads(failed.stdout)['issues'][0]
        assert (root/'calls').read_text() == before
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        # Explicit output replaces only that entry; invalid destinations do no probing.
        explicit = root/'explicit.json'
        explicit.write_text('old')
        count = len(list((root/'results/operations').iterdir()))
        r = invoke({},True,explicit)
        assert r.returncode == 0 and json.loads(explicit.read_text()) == json.loads(r.stdout)
        assert len(list((root/'results/operations').iterdir())) == count
        before = (root/'calls').read_text()
        for output in (root/'absent/report.json',tools):
            r = invoke({},True,output)
            assert r.returncode == 2 and not r.stdout
        assert (root/'calls').read_text() == before
        assert not (root/'absent').exists()
        for mode in ({'bad_json':True},{'nonzero':True}):
            r = invoke(mode,False)
            report = json.loads(r.stdout)
            assert r.returncode == 1 and not report['ready']
            assert 'requested' in report and report['runtime_contract']['comparisons']
        # Real importlib/Tcl in a lexical venv (symlinked interpreter), without
        # importing LibreLane APIs. The installed distribution is metadata only.
        virtual = root/'real venv'
        venv.EnvBuilder(with_pip=False,symlinks=True).create(virtual)
        site = virtual/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        dist = site/'librelane-3.1.0.dev3.dist-info'
        dist.mkdir()
        (dist/'METADATA').write_text('Metadata-Version: 2.1\nName: librelane\nVersion: 3.1.0.dev3\nRequires-Python: >=3.10\n')
        literal = str(virtual)+'/bin/../bin/python'
        real_base = base.copy()
        real_base[real_base.index('--python')+1] = literal
        (root/'mode.json').write_text('{}')
        r = invoke({},True,command=real_base)
        actual = json.loads(r.stdout)
        old = os.environ.copy()
        try:
            os.environ.clear(); os.environ.update(env)
            expected = coarse_flow_oracle.preflight(bundle,tools,pdk,Path(literal),native=True)
        finally:
            os.environ.clear(); os.environ.update(old)
        assert actual==expected,(actual,expected,r.stderr)
        assert actual['python']==literal and actual['execution_binding']['interpreter']==literal
        assert actual['actual']['librelane']=='3.1.0.dev3'
        # Missing manifests still allocate and publish a terminal diagnostic.
        (bundle/'manifest.json').unlink()
        r = invoke({},True)
        assert r.returncode == 1 and json.loads(r.stdout)['diagnostic']['status'] == 'failed'
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        # Accumulation: changed file, collateral and Tcl failures all survive.
        (bundle/'data').write_text('changed')
        r = invoke({'dirty':True,'tcl_fail':True},True)
        issues = json.loads(r.stdout)['issues']
        assert any('bundle file' in x for x in issues) and any('modified test' in x for x in issues) and any('tcl_ready' in x for x in issues)
        (bundle/'data').write_text('data')
        # Public parsing/defaults and production delegation, no fallback.
        command = [sys.executable,'-B','-c',
                   f'import os,sys; sys.path.insert(0,{str(Path(__file__).parent)!r}); sys.path.insert(0,{os.environ.get("FLOW_TEST_REFERENCE_ROOT", str(package.parent))!r}); from reference_harness import activate; activate() if os.environ.get("FLOW_TEST_REFERENCE_ROOT") else None; from hardcaml_asic_flow.cli import main; sys.exit(main())',
                   'preflight',str(bundle),'--toolchain',str(root),
                   '--python',str(bin/'python'),'--support-tools',str(tools),'--pdk-root',str(pdk),
                   '--results-root',str(root/'results'),'--json']
        r = invoke({},True,command=command)
        assert r.returncode == 0, (r.stdout,r.stderr)
        assert json.loads(r.stdout)['execution_binding']['interpreter'] == str(bin/'python')
        missing_command=command.copy()
        missing_command[3]=missing_command[3].replace('sys.exit(main())',
            'from hardcaml_asic_flow import ocaml_backend; '
            'ocaml_backend.binary=lambda name: (_ for _ in ()).throw(OSError("missing native sentinel")); sys.exit(main())')
        before=(root/'calls').read_text()
        r=invoke({},True,command=missing_command)
        assert r.returncode==2 and 'missing native sentinel' in r.stderr and not r.stdout
        assert (root/'calls').read_text()==before
        if public.parent.name=='bin':
            # Exercise the actual installed shell launcher too, from unrelated cwd.
            shell_env=dict(env,PATH=str(bin)+os.pathsep+os.environ['PATH'])
            (root/'mode.json').write_text('{}')
            r=subprocess.run([str(public),*command[4:],'--native'],cwd=root,env=shell_env,capture_output=True,text=True,timeout=15)
            assert r.returncode==0,(r.stdout,r.stderr)
            assert json.loads(r.stdout)['ready']
        # Simultaneous cancellation and terminal evidence EISDIR: the process
        # outcome stays primary, storage diagnostics survive, no later probes.
        for target in ('host','container'):
            for sig in (signal.SIGINT,signal.SIGTERM):
                for explicit_output in (None,explicit):
                    before=(root/'calls').read_text()
                    public_probe=([str(public),*command[4:]] if public.parent.name=='bin' else command)
                    r=invoke({'storage_signal':target,'signal':sig},False,explicit_output,command=public_probe)
                    report=json.loads(r.stdout)
                    assert r.returncode==128+sig,(target,r.returncode,r.stdout,r.stderr)
                    assert report['diagnostic']['interrupted_by']==sig.name
                    assert any('evidence write failed' in x for x in report['issues']),report
                    calls=[json.loads(x) for x in (root/'calls').read_text()[len(before):].splitlines()]
                    docker_calls=[a for name,a in calls if Path(name).name=='docker']
                    if target=='host': assert not docker_calls,docker_calls
                    else:
                        assert len([a for a in docker_calls if a[0]=='run'])==1,docker_calls
                        assert docker_calls[-1][:2]==['rm','-f'],docker_calls
        for nonzero in (False,True):
            r=invoke({'storage_signal':'host','nonzero':nonzero},True)
            report=json.loads(r.stdout)
            assert r.returncode==1 and not report['ready'] and 'execution_binding' not in report,report
            assert any('evidence write failed' in x for x in report['issues']),report
            if nonzero: assert any('process exited nonzero' in x for x in report['issues']),report
        # Outer terminal report/receipt publication must retain the process
        # result too. Corruption happens after pending allocation, before signal.
        public_probe=([str(public),*command[4:]] if public.parent.name=='bin' else command)
        routes=[base,public_probe]
        if consumer:
            routes.append([str(consumer),'--run',str(bundle),str(tools),str(pdk),
                str(bin/'python'),str(package),str(root/'results')])
        for route_index,route in enumerate(routes):
            typed=consumer and route[0]==str(consumer)
            for native,target in ((True,'host'),(False,'host'),(False,'container')):
                for sig in (signal.SIGINT,signal.SIGTERM):
                    for corruption in ('report','receipt','both','explicit'):
                        output=root/f'terminal-explicit-{route_index}-{native}-{target}-{sig}.json' if corruption=='explicit' else None
                        if output: output.write_text('old')
                        paths=([str(output)] if output else
                            [name for kind,name in (('report','preflight.json'),('receipt','operation.json'))
                             if corruption in (kind,'both')])
                        before=(root/'calls').read_text()
                        old_ops=set((root/'results/operations').iterdir())
                        mode=dict(storage_signal=target,storage_paths=paths,signal=sig)
                        if typed:
                            (root/'mode.json').write_text(json.dumps(mode))
                            r=subprocess.run([*route,'native' if native else 'docker',
                                *([str(output)] if output else [])],env=env,capture_output=True,text=True,timeout=15)
                        else: r=invoke(mode,native,output,command=route)
                        report=json.loads(r.stdout)
                        assert r.returncode==128+sig,(route,corruption,r.returncode,r.stdout,r.stderr)
                        assert report['diagnostic']['interrupted_by']==sig.name and not report['ready']
                        assert any('terminal publication failed' in x for x in report['issues']),report
                        if output:
                            directory=output.parent
                            assert set((root/'results/operations').iterdir())==old_ops
                        else:
                            new_ops=set((root/'results/operations').iterdir())-old_ops
                            assert len(new_ops)==1
                            directory=new_ops.pop()
                        for path in paths:
                            p=Path(path) if Path(path).is_absolute() else directory/path
                            assert p.is_dir() and list(p.iterdir())==[p/'user-owned']
                            assert (p/'user-owned').read_text()=='retain me'
                        report_ok=corruption=='receipt'
                        receipt_ok=corruption=='report'
                        if report_ok: assert json.loads((directory/'preflight.json').read_text())==report
                        if receipt_ok:
                            receipt=json.loads((directory/'operation.json').read_text())
                            assert receipt['status']=='interrupted' and receipt['exit_code']==128+sig
                            assert 'terminal publication failed' in receipt['error']
                        if typed:
                            assert f'report={str(report_ok).lower()} receipt={str(receipt_ok).lower()}' in r.stderr,r.stderr
                        else:
                            assert ('preflight: unpublished report ' in r.stderr)==(not report_ok),r.stderr
                        calls=[json.loads(x) for x in (root/'calls').read_text()[len(before):].splitlines()]
                        docker_calls=[a for name,a in calls if Path(name).name=='docker']
                        if target=='host': assert not docker_calls,docker_calls
                        else:
                            assert len([a for a in docker_calls if a[0]=='run'])==1,docker_calls
                            assert docker_calls[-1][:2]==['rm','-f'],docker_calls
        # Storage alone is exit 2; an existing readiness failure remains exit 1.
        for paths in (['preflight.json'],['operation.json'],['preflight.json','operation.json']):
            for failure in ({},{'tcl_fail':True},{'nonzero':True}):
                r=invoke(dict(storage_signal='host',storage_paths=paths,**failure),True)
                report=json.loads(r.stdout)
                assert r.returncode==(1 if failure else 2) and not report['ready'],(r.stdout,r.stderr)
                assert any('terminal publication failed' in x for x in report['issues'])
                if failure.get('tcl_fail'): assert any('tcl_ready' in x for x in report['issues'])
                if failure.get('nonzero'): assert any('process exited nonzero' in x for x in report['issues'])
        # A non-writable operation root leaves actual pending bytes, not a
        # fabricated failed receipt. Text output must label both unpublished.
        for sig in (None,signal.SIGINT,signal.SIGTERM):
            old_ops=set((root/'results/operations').iterdir())
            text_base=[x for x in base if x!='--json']
            r=invoke(dict(storage_signal='host',storage_readonly=True,signal=sig),True,command=text_base)
            directory=(set((root/'results/operations').iterdir())-old_ops).pop()
            try:
                # Without signal the probe-evidence write already failed, so
                # its readiness failure (1) precedes outer storage failures.
                assert r.returncode==(128+sig if sig else 1),(r.stdout,r.stderr)
                assert 'unpublished report: ' in r.stdout and 'unpublished operation: ' in r.stdout,r.stdout
                receipt=json.loads((directory/'operation.json').read_text())
                assert receipt['status']=='pending' and receipt['exit_code'] is None and receipt['ended_at'] is None,receipt
                assert json.loads((directory/'preflight.json').read_text())['diagnostic']['status']=='pending'
            finally: directory.chmod(0o700)
        # Root allocation/write failure remains infrastructure exit 2, no tools.
        blocked_root=root/'blocked-results'; blocked_root.write_text('user owned')
        blocked_base=base.copy(); blocked_base[blocked_base.index('--results-root')+1]=str(blocked_root)
        before=(root/'calls').read_text()
        r=invoke({},True,command=blocked_base)
        assert r.returncode==2 and not r.stdout and blocked_root.read_text()=='user owned'
        assert (root/'calls').read_text()==before
        for native,sig in ((True,signal.SIGINT),(True,signal.SIGTERM),(False,signal.SIGTERM)):
            old_ops=set((root/'results/operations').iterdir())
            (root/'mode.json').write_text(json.dumps({'block' if native else 'block_container':True}))
            (root/'blocked').unlink(missing_ok=True)
            before=(root/'calls').read_text()
            proc = subprocess.Popen(base+(['--native'] if native else []),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline = time.monotonic()+5
            while not (root/'blocked').exists() and time.monotonic()<deadline: time.sleep(.01)
            assert (root/'blocked').exists()
            pending = [p for p in (root/'results/operations').glob('*/preflight.json')
                       if p.parent not in old_ops and p.is_file() and json.loads(p.read_text()).get('diagnostic',{}).get('status')=='pending']
            assert len(pending)==1
            receipt_path = pending[0].parent/'operation.json'
            assert json.loads(receipt_path.read_text())['status']=='pending'
            proc.send_signal(sig)
            out,err = proc.communicate(timeout=10)
            assert proc.returncode == 128+sig,(proc.returncode,out,err)
            assert json.loads(out)['diagnostic']['interrupted_by'] == sig.name
            assert json.loads(receipt_path.read_text())['status']=='interrupted'
            assert json.loads(pending[0].read_text())==json.loads(out)
            if not native:
                calls=[json.loads(line)[1] for line in (root/'calls').read_text()[len(before):].splitlines()]
                runs=[a for a in calls if a[0]=='run']
                removals=[a for a in calls if a[:2]==['rm','-f']]
                assert len(runs)==1 and removals
                assert all(a[2]==runs[0][runs[0].index('--name')+1] for a in removals)
        if consumer:
            # The stage metadata API uses a different container invocation from
            # standalone/coarse readiness, but must retain the same precedence.
            for target in ('host','container'):
                for sig in (signal.SIGINT,signal.SIGTERM):
                    directory=root/f'stage-{target}-{sig}'; directory.mkdir()
                    (root/'mode.json').write_text(json.dumps(dict(storage_signal=target,signal=sig,stage_probe=True)))
                    before=(root/'calls').read_text()
                    r=subprocess.run([str(consumer),'--runtime-observe',str(directory),str(bin/'python'),str(bin/'docker'),
                        str(package/'stage_metadata_probe.py'),str(bundle/'manifest.json'),str(pdk)],
                        env=env,text=True,capture_output=True,timeout=10)
                    assert r.returncode==128+sig and 'evidence write failed' in r.stdout,(r.returncode,r.stdout,r.stderr)
                    calls=[json.loads(x)[1] for x in (root/'calls').read_text()[len(before):].splitlines()]
                    assert len([a for a in calls if a[0]=='run'])==(target=='container'),calls
            # Installed linked consumer exercises legacy-only waiver seam.
            for explicit_policy in (False,True):
                req = copy.deepcopy(requested)
                if explicit_policy:
                    req['runtime_policy'] = official_policy()
                manifest['requested_tools'] = req
                (bundle/'manifest.json').write_text(json.dumps(manifest))
                (root/'mode.json').write_text(json.dumps({'host_python':'3.12.1'}))
                r = subprocess.run([str(consumer),str(bundle),str(tools),str(pdk),str(bin/'python'),str(package),str(root/'consumer-results')],
                    env=env,text=True,capture_output=True,timeout=15)
                assert r.returncode==0,(r.stdout,r.stderr)
                report=json.loads(r.stdout)
                assert bool(report['waivers']) == (not explicit_policy),report
                assert report['runtime_contract']['normalized_policy']['source']==('runtime-policy' if explicit_policy else 'legacy')
                assert report['ready'] == (not explicit_policy),report
        print(f'preflight: {checks} exact Python report differentials; '
              f'{len(routes)*24} terminal-publication cancellation cases; '
              'public routing, typed API, pending-record limits, output, receipt, '
              'failure precedence and SIGINT/SIGTERM checks passed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cli',type=Path)
    parser.add_argument('--installed',action='store_true')
    parser.add_argument('--package',type=Path)
    parser.add_argument('--public',type=Path)
    parser.add_argument('--consumer',type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    if args.installed:
        with tempfile.TemporaryDirectory(prefix='installed-preflight-',dir='/tmp/opencode') as tmp:
            root=Path(tmp); prefix=root/'prefix'
            from reference_harness import installed_prefix
            installed_prefix(repo, prefix)
            package=prefix/'lib/hardcaml_asic/flow/hardcaml_asic_flow'
            driver=root/'check_preflight.py'; shutil.copyfile(__file__,driver)
            consumer=root/'consumer'; consumer.mkdir()
            (consumer/'dune-project').write_text('(lang dune 3.0)\n')
            (consumer/'dune').write_text('(executable (name main) (libraries hardcaml_asic.execution unix)\n'
                ' (link_flags -ccopt -Wl,--strip-debug))\n')
            shutil.copyfile(repo/'test/preflight_consumer.ml',consumer/'main.ml')
            env=dict(os.environ,OCAMLPATH=str(prefix/'lib'))
            subprocess.run(['dune','build','-j','20','main.exe'],cwd=consumer,env=env,check=True)
            # Import installed Python resources with checkout opens denied.
            launcher=root/'launch.py'
            launcher.write_text('import sys, runpy\n'
                f'checkout={str(repo)!r}\n'
                'def audit(event,args):\n'
                ' if event=="open" and isinstance(args[0],str) and args[0].startswith(checkout+"/"):\n'
                '  raise AssertionError("checkout read: "+args[0])\n'
                'sys.addaudithook(audit)\nfrom reference_harness import activate\nactivate()\n'
                f'runpy.run_path({str(driver)!r},run_name="__main__")\n')
            subprocess.run(['bwrap','--ro-bind','/','/','--bind',str(root),str(root),
                '--tmpfs',str(repo),'--proc','/proc','--dev-bind','/dev','/dev',
                sys.executable,'-B',str(launcher),'--cli',str(prefix/'bin/hardcaml-asic-experimental-preflight'),
                '--package',str(package),'--public',str(prefix/'bin/hardcaml-asic-flow'),
                '--consumer',str(consumer/'_build/default/main.exe')],cwd=root,
                env=dict(os.environ,TMPDIR=str(root)),check=True)
    else:
        suite((args.cli or repo/'_build/default/bin/experimental_preflight.exe').resolve(),
              (args.package or repo/'flow/hardcaml_asic_flow').resolve(),
              (args.public or repo/'flow/hardcaml-asic-flow').resolve(),
              args.consumer.resolve() if args.consumer else None)


if __name__=='__main__': main()
