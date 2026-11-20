"""Host-only replay: actual checked plan, sealed checkpoint and fenced resource pages."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

helper, generator = [Path(p).resolve() for p in sys.argv[1:]]
plan_raw = subprocess.check_output([generator, 'inspection-fixture-plan'])
plan = json.loads(plan_raw)
bundle = json.loads(plan['bundle_manifest'])
profile = 'stock256x16_fresh_synthesis_v1'
image = 'sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a'
hash_ = lambda data: hashlib.sha256(data).hexdigest()
canonical = lambda obj: json.dumps(obj, separators=(',', ':'), sort_keys=True).encode()

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    cp = root / 'checkpoints/cp'
    views = cp / 'views'
    views.mkdir(parents=True)
    nl = views / 'chosen.nl.v'
    other = views / 'unselected.nl.v'
    macro, second = bundle['macro_contract']['macros']
    stock = macro['module']
    def netlist(*instances):
        return 'module '+bundle['top_module']+'();\n' + ''.join(stock+' '+i+'();\n' for i in instances) + 'endmodule\n'
    nl.write_text(netlist(macro['instance'], second['instance']))
    other.write_text(netlist())
    snapshot = root / 'methodology-plan.json'
    snapshot.write_bytes(plan_raw)
    host = root / 'host'
    host.write_text('external')
    def file(p):
        return dict(sha256=hash_(p.read_bytes()), bytes=p.stat().st_size)
    external = {str(host):file(host)}
    (cp/'closure.json').write_bytes(canonical(external))
    (cp/'resolved.json').write_text(plan['configuration_bytes'])
    state = dict(nl=str(nl), json_h=str(other), metrics={'metric':float('inf')})
    (cp/'state.json').write_bytes(canonical(state))
    (cp/'segment.json').write_bytes(canonical(dict(boundary='synthesis', metrics={}, macro_profile=profile, macro_evidence=[])))
    target = root / 'execution-test.json'
    method = dict(contract='hardcaml-asic.methodology-execution-reference', schema_version=1, attempt_id='test',
                  snapshot_path=str(snapshot), snapshot_sha256=hash_(plan_raw), expected_plan_identity=plan['plan_identity'],
                  **{k:plan[k] for k in ('methodology_hash','bundle_identity','plan_identity')})
    occurrence = dict(action_key='implementation.synthesis', occurrence_id='test/implementation.synthesis',
                      best_effort=False, duration_seconds=1, error=None, verdict=None, status='completed', exit_code=0)
    def seal():
        publication = dict(boundary='synthesis', closure_scope='path_bound', restart_supported=False,
                           restart_targets=[], tested_image_id=image, acceptance='not_evaluated')
        manifest = dict(**publication, schema_version=3, location=str(cp), external=external, metrics={},
                        compatibility=dict(bundle_identity=bundle['identity'],binding=dict(image_identity=dict(image_id=image))),
                        files={str(p.relative_to(cp)):file(p) for p in cp.rglob('*') if p.is_file() and p.name!='manifest.json'},
                        macro_evidence=[], macro_profile=profile)
        identity = hash_(canonical(manifest))
        (cp/'manifest.json').write_bytes(canonical(dict(**manifest,identity=identity)))
        target.write_bytes(canonical(dict(artifacts=[dict(**publication,path=str(cp),identity=identity)],attempt_id='test',
            methodology=method,plan=plan['methodology']['recipe'],occurrences=[occurrence],status='completed',exit_code=0)))
    def inspect(*args, ok=True, at=target):
        p = subprocess.run([helper,'--kind','execution','--target',at,'--json',*args],capture_output=True)
        if ok:
            assert p.returncode==0, p.stderr
            return json.loads(p.stdout)
        assert p.returncode!=0, p.stdout
    seal()
    index = inspect()
    def status(index):
        return {r['id']:r['synthesis'] for r in index['rows']}
    second_id = second['resource']
    def statuses(first, second):
        return {**dict.fromkeys(('program0','program1','program2'), 'unavailable'),
                macro['resource']:first, second_id:second}
    assert status(index)==statuses('matched','matched'), (status(index),index['diagnostics'])
    assert all(r['final']=='unavailable' and not r['measurements'] for r in index['rows'])
    assert index['verdict']=='not_evaluated'
    fence=['--expected-digest',index['source_digest']]
    detail=inspect('--query','detail','--resource-id',macro['resource'],'--section','synthesis',*fence)
    fields=dict(detail['items'][0]['fields'])
    assert json.loads(fields['realized'])['instances']==[{'instance':macro['instance'],'module':stock}]
    refs=json.loads(fields['evidence_refs'])
    assert {r['path']:r['sha256'] for r in refs}=={str(p.relative_to(root)):file(p)['sha256'] for p in (nl,cp/'state.json')}
    catalog=inspect('--query','artifacts',*fence)
    artifact,=[r for r in catalog['entries'] if r['label']==str(nl.relative_to(root))]
    assert bytes.fromhex(inspect('--query','bytes','--artifact-id',artifact['id'],'--limit','16384',*fence)['bytes_hex'])==nl.read_bytes()
    nl.write_text(netlist(macro['instance']))
    inspect(ok=False)  # tampering after publication, even before a fenced request
    seal()
    assert status(inspect())==statuses('matched','missing')
    inspect('--query','artifacts',*fence,ok=False)
    nl.write_text('not structural Verilog')
    seal()
    assert status(inspect())==statuses('unavailable','unavailable')
    state['nl']=str(host)  # an existing file, but outside the checkpoint
    (cp/'state.json').write_bytes(canonical(state)); seal(); inspect(ok=False)
    state['nl']=[str(other)]  # valid state leaves, not a single native NL view
    (cp/'state.json').write_bytes(canonical(state)); seal()
    assert status(inspect())==statuses('unavailable','unavailable')
    state['nl']=str(nl); (cp/'state.json').write_bytes(canonical(state));seal()
    sibling=root/'elsewhere';sibling.mkdir();copy=sibling/target.name;copy.write_bytes(target.read_bytes())
    assert status(inspect(at=copy))==statuses('unavailable','unavailable')
    nl.unlink(); nl.symlink_to(host); inspect(ok=False)
