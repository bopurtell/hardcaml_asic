"""Public PT.5 reconciliation and execution acceptance fixtures. No EDA."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, separators=(',', ':')) + '\n')


def fixture(root):
    flow = root / 'project/runs/asic'
    macro, name = 'MAC', 'hc_resource_6_memory'
    manifest = dict(schema_version=1, adapter='LibreLane', project='test', top_module='top',
                    resources=[dict(id='memory', request='((kind single_port_ram) (contract ()))',
                                    selection='((implementation (Macro MAC)) (reason (Exact_mapping (technology fixture))))',
                                    requirement='Exact', policy='Default', elaborated_as='Selected_implementation')],
                    macro_contract=dict(schema_version=1, execution='synthesis_only',
                                        macros=[dict(resource='memory', module=macro, instance=name)]),
                    flow_configuration=dict(DEFAULT_CORNER='typ', MACROS={macro: dict(lib={'typ': ['pdk_dir::lib/MAC.lib']})}),
                    files=[], synthesis_sources=[], simulation_sources=[], source_inputs=[])
    identity = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest()
    write(root/'project/manifest.json', dict(identity=identity, **manifest))
    record = dict(run_id='fixture', build_identity=identity, manifest_sha256=sha(root/'project/manifest.json'),
                  bundle=str(root/'project'), status='completed', requested_stage='synthesis', completed_stage='synthesis',
                  outputs=dict(project='project', flow='project/runs/asic'))
    write(root/'run.json', record)
    mapped = dict(creator='Yosys fixture', modules={
        'sg13g2_inv_1': dict(attributes=dict(area='2.5',blackbox='1')),
        macro: dict(attributes=dict(blackbox='1')),
        'top': dict(cells={name: dict(type=macro), 'gate': dict(type='sg13g2_inv_1')})})
    write(flow/'06-yosys-synthesis/top.nl.v.json', mapped)
    write(flow/'06-yosys-synthesis/reports/stat.json', dict(creator='Yosys fixture', design=dict(
        num_cells=2, num_cells_by_type={macro: 1, 'sg13g2_inv_1': 1}, area=2.5, sequential_area=0)))
    write(flow/'06-yosys-synthesis/state_out.json', dict(metrics=dict(
        design__instance_unmapped__count=0, synthesis__check_error__count=0, design__inferred_latch__count=0)))
    lib=root/'models/MAC.lib';lib.parent.mkdir()
    lib.write_text('library (fixture) { cell(MAC) { area : 100.25; } }\n')
    write(root/'preflight.json', dict(references=[dict(reference='pdk_dir::lib/MAC.lib', sha256=sha(lib))]))
    return flow, mapped, record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--consumer', type=Path, required=True)
    a=parser.parse_args();checks=0
    def invoke(root, code=0):
        nonlocal checks
        p=subprocess.run([str(a.cli.resolve()), 'resources-inspect', str(root), '--json'], capture_output=True, text=True)
        assert p.returncode==code, (p.returncode, code, p.stdout, p.stderr)
        checks+=1
        return json.loads(p.stdout) if p.stdout else None
    def consume(op, path):
        p=subprocess.run([str(a.consumer.resolve()), op, str(path)], capture_output=True, text=True)
        assert p.returncode==0, (op, p.stdout, p.stderr)
        return json.loads(p.stdout)
    with tempfile.TemporaryDirectory(prefix='pt5-resource-') as tmp:
        root=Path(tmp);flow,mapped,record=fixture(root)
        original={p:sha(p) for p in root.rglob('*') if p.is_file()}
        e=invoke(root);synth=e['observations'][0];m=synth['measurements']
        assert synth['resources'][0]['status']=='matched'
        assert m['total_mapped_cells']['value']==2 and m['standard_cell_count']['value']==1
        assert m['macro_count']['value']==1
        assert m['synthesis_standard_cell_area']['value']==2.5
        assert m['macro_library_area']['value']==100.25
        assert m['combined_cell_area_estimate']['value']==102.75
        assert synth['resources'][0]['area']['value']==100.25
        assert m['sequential_cell_count']['value'] is None
        assert {t['module']:t['count'] for t in synth['cell_type_counts']['types']}=={'MAC':1,'sg13g2_inv_1':1}
        assert e['provenance']['preparation_link']['status']=='unavailable'
        assert e['coverage']=='legacy_resources_macro_contract'
        assert original=={p:sha(p) for p in original}
        assert consume('report',root)['accepted']
        results=consume('collect',root)
        assert results['resource_evidence']==consume('report',root)['results']['resource_evidence']
        write(root/'results.json',results)
        stored_digest=sha(root/'results.json')
        archived=consume('archive',root)
        assert all(ref['path'] in archived for ref in results['resource_evidence']['evidence_refs'])
        assert consume('reconcile',root)['status']=='pass', 'multiple resources sharing a module'
        net=flow/'06-yosys-synthesis/top.nl.v.json';name='hc_resource_6_memory'
        baseline=json.loads(json.dumps(mapped))
        for label,cells in [
            ('missing',{'gate':dict(type='sg13g2_inv_1')}),
            ('renamed',{'renamed':dict(type='MAC'),'gate':dict(type='sg13g2_inv_1')}),
            ('wrong-type',{name:dict(type='sg13g2_inv_1'),'gate':dict(type='sg13g2_inv_1')}),
            ('extra',{**baseline['modules']['top']['cells'],'extra':dict(type='MAC')}),
            ('unknown',{**baseline['modules']['top']['cells'],'hidden':dict(type='UNCLASSIFIED')})]:
            broken=json.loads(json.dumps(baseline));broken['modules']['top']['cells']=cells
            write(net,broken);bad=invoke(root,1)
            assert bad['check_outcomes'][0]['status']=='fail',label
            assert not consume('report',root)['accepted'],label
            assert sha(root/'results.json')==stored_digest, 'historical report inspection must not rewrite stored results'
            assert consume('collect',root)['resource_evidence']['check_outcomes'][0]['status']=='fail'
        net.unlink()
        assert invoke(root,1)['observations'][0]['resources'][0]['status']=='unresolved'
        net.write_text('{"modules":{},"modules":{}}')
        assert invoke(root,1)['observations'][0]['resources'][0]['status']=='unresolved'
        write(net,baseline)
        stat=flow/'06-yosys-synthesis/reports/stat.json'
        stat_original=json.loads(stat.read_text())
        for value in [True, float('nan'), float('inf'), -1]:
            broken=json.loads(json.dumps(stat_original));broken['design']['area']=value;write(stat,broken)
            assert invoke(root,1)['observations'][0]['measurements']['synthesis_standard_cell_area']['value'] is None
        write(stat,stat_original)
        (root/'models/MAC.lib').write_text('library(x) { cell(MAC) { area: 999; } }')
        p=subprocess.run([str(a.consumer.resolve()),'archive',str(root)],capture_output=True,text=True)
        assert p.returncode!=0, 'archive must refuse stale advertised evidence'
        assert invoke(root)['observations'][0]['measurements']['macro_library_area']['value'] is None
        for mutate in [lambda x:x.update(coverage='unknown'),lambda x:x.update(check_outcomes=[]),
                       lambda x:x['observations'][0]['measurements']['macro_count'].update(value=float('nan')),
                       lambda x:x['observations'][0]['resources'][0]['realized'].update(count=0),
                       lambda x:x['observations'][0].update(build_identity='other-build'),
                       lambda x:x['observations'][0].update(stage='final_physical'),
                       lambda x:x['provenance'].update(preparation_link=dict(status='verified', reason='invented'))]:
            bad=json.loads(json.dumps(e));mutate(bad);path=root/'invalid.json';write(path,bad)
            assert consume('validate',path)!='valid'
        record.update(requested_stage='full',completed_stage='full');write(root/'run.json',record)
        final=flow/'final/nl/top.nl.v';final.parent.mkdir(parents=True)
        final.write_text('module top(input a); wire b; MAC hc_resource_6_memory (.A(a)); sg13g2_inv_1 gate (.A(a)); endmodule\n')
        assert [o['status'] for o in invoke(root)['observations']]==['pass','pass']
        # A final-netlist hash must agree with the original retained physical checker.
        gds=flow/'final/gds/top.gds';gds.parent.mkdir(parents=True);gds.write_bytes(b'fixture geometry bytes')
        checker=dict(schema_version=1,status='pass',checking_profile='cmos5l_real_gds_v1',
                     lvs_scope='abstract_macro_boundary_with_schematic_cdl',gds_sha256=sha(gds),
                     artifacts={'final/nl/top.nl.v':sha(final)})
        write(root/'physical-checks.json',checker)
        record['physical_checks_sha256']=sha(root/'physical-checks.json');write(root/'run.json',record)
        assert invoke(root)['observations'][1]['status']=='pass'
        final.write_text(final.read_text()+'\n// changed after physical checking\n')
        assert invoke(root,1)['observations'][1]['resources'][0]['status']=='unresolved'
        record.pop('physical_checks_sha256');write(root/'run.json',record)
        final.write_text('module top(input a); wire b; sg13g2_inv_1 gate (.A(a)); endmodule\n')
        assert [o['status'] for o in invoke(root,1)['observations']]==['pass','fail']
        final.write_text('module top(input a); MAC hc_resource_6_memory (.A(a)); MAC hc_resource_6_memory (.A(a)); endmodule\n')
        assert invoke(root,1)['observations'][1]['resources'][0]['status']=='unresolved'
        final.write_text('module top(input a); always @* a=0; endmodule')
        assert invoke(root,1)['observations'][1]['resources'][0]['status']=='unresolved'
        # A producer-bound Liberty with a comment-only area is unavailable, not a number.
        lib=root/'models/MAC.lib'
        lib.write_text('library (x) { cell (MAC) { /* area : 42; */ pin(A) { area : 999; } } }')
        write(root/'preflight.json',dict(references=[dict(reference='pdk_dir::lib/MAC.lib',sha256=sha(lib))]))
        record.update(requested_stage='synthesis',completed_stage='synthesis');write(root/'run.json',record)
        assert invoke(root)['observations'][0]['measurements']['macro_library_area']['value'] is None
        # Explicit flops/fallback keep per-resource attribution unavailable, never optimized-away.
        manifest=json.loads((root/'project/manifest.json').read_text())
        manifest.pop('macro_contract');manifest['flow_configuration']['MACROS']={}
        mapped['modules']['top']['cells']={'gate':dict(type='sg13g2_inv_1')};write(net,mapped)
        stat_original['design'].update(num_cells=1,num_cells_by_type={'sg13g2_inv_1':1});write(stat,stat_original)
        def bind():
            manifest.pop('identity',None)
            identity=hashlib.sha256(json.dumps(manifest,separators=(',',':')).encode()).hexdigest()
            write(root/'project/manifest.json',dict(identity=identity,**manifest))
            record.update(build_identity=identity,manifest_sha256=sha(root/'project/manifest.json'));write(root/'run.json',record)
        for req,reason in [('Flops','Explicit_flops'),('Exact_or_flop_fallback','(Flop_fallback (no_exact_mapping_in fixture))'),
                           ('Exact_or_flop_fallback','(Budget_exhausted (macro MAC) (limit 0))')]:
            manifest['resources'][0].update(requirement=req,selection=f'((implementation Flops) (reason {reason}))')
            bind();row=invoke(root)['observations'][0]['resources'][0]
            assert row['status']=='unresolved' and row['realized']['status']=='unavailable'
            assert row['attribution']['status']=='unavailable' and row['selection'].find(reason)>=0
        manifest['resources']=[];bind()
        assert invoke(root)['observations'][0]['resources']==[]
        # New identity-covered snapshots use the same public inspection engine.
        manifest['resource_snapshot']=dict(schema_version=1,project='test',top='top',mode='implementation',scope='resolved',resources=[])
        bind();assert invoke(root)['coverage']=='resource_snapshot'
        old=dict(id='memory',request='((kind single_port_ram) (contract ()))',requirement='Flops',policy='Default',
                 selection='((implementation Flops) (reason Explicit_flops))',elaborated_as='Selected_implementation',
                 source_roles=['Generated_synthesis_rtl'])
        row=dict(id=dict(path=[],name='memory',display='memory'),request=dict(kind='single_port_ram',contract={'list':[]}),
                 requirement='flops',policy=dict(kind='default'),selection=dict(implementation=dict(kind='flops'),reason=dict(kind='explicit_flops')),
                 elaborated_as='selected_implementation',sources=[dict(kind='generated_synthesis_rtl')],actual_instances=dict(status='not_applicable'),
                 collateral=[],physical_intent=dict(status='unavailable'))
        manifest['resources']=[old];manifest['resource_snapshot']['resources']=[row];bind()
        structured=invoke(root)
        assert structured['coverage']=='resource_snapshot'
        assert structured['observations'][0]['resources'][0]['status']=='unresolved'
        old.update(requirement='Exact_or_flop_fallback',selection='((implementation Flops) (reason (Budget_exhausted (macro MAC) (limit 0))))')
        row.update(requirement='exact_or_flop_fallback')
        row['selection']['reason']=dict(kind='budget_exhausted',macro='MAC',limit=0)
        bind()
        assert invoke(root)['resource_inventory'][0]['selection']==row['selection']
        forged=invoke(root)
        for inv in [forged['resource_inventory'][0],forged['observations'][0]['resources'][0]]:
            inv['selection']['reason']['limit']=2
            inv['inventory_record']['selection']['reason']['limit']=2
        path=root/'invalid-budget.json';write(path,forged)
        assert consume('validate',path)!='valid'
        row['selection']['reason']['macro']='other';bind();invoke(root,2)
        manifest['resource_snapshot']['schema_version']=999;bind();invoke(root,2)
        record['build_identity']='stale';write(root/'run.json',record);invoke(root,2)
    print(f'PT.5 resource evidence: {checks} public CLI cases plus collection/report/archive/strict-reader assertions passed')


if __name__=='__main__':
    main()
