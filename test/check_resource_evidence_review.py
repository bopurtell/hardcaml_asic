"""Independent-review regressions: parser completeness and source-aware assertions.

Fixtures are disposable. In particular archive publication is tested both for a
genuine failed reconciliation and an internally coherent forgery over its bytes.
No EDA, nested builds or Git operations.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from check_resource_evidence import fixture, write, sha


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cli',type=Path,required=True)
    parser.add_argument('--consumer',type=Path,required=True)
    a=parser.parse_args()
    cli,consumer=str(a.cli.resolve()),str(a.consumer.resolve())
    cases=0
    def call(argv,code=0):
        p=subprocess.run(argv,text=True,capture_output=True)
        assert p.returncode==code,(argv,p.returncode,p.stdout,p.stderr)
        return json.loads(p.stdout) if p.stdout else None
    def inspect(root,code=0):
        nonlocal cases
        cases+=1
        return call([cli,'resources-inspect',str(root),'--json'],code)
    def consume(op,path):
        return call([consumer,op,str(path)])
    def reject_validate(root,e):
        write(root/'candidate.json',e)
        result=consume('validate',root/'candidate.json')
        assert result!='valid',e
        return result
    def reject_archive(root,out):
        call([cli,'archive',str(root),'--output',str(out),'--json'],2)
        assert not out.exists(),'a rejected assertion must not publish an archive'
    with tempfile.TemporaryDirectory(prefix='resource-review-regressions-') as temp:
        temp=Path(temp)
        root=temp/'parser';flow,mapped,record=fixture(root)
        record.update(requested_stage='full',completed_stage='full');write(root/'run.json',record)
        final=flow/'final/nl/top.nl.v';final.parent.mkdir(parents=True)
        # Count every comma-separated instance, including nested connection syntax.
        final.write_text('module top(input a); MAC hc_resource_6_memory (.A({a,a})), extra (.A(fn(a))); sg13g2_inv_1 gate (.A(a)); endmodule\n')
        e=inspect(root,1);o=e['observations'][1]
        assert o['measurements']['macro_count']['value']==2
        assert o['resources'][0]['status']=='unexpected'
        assert {i['instance'] for i in o['unexpected_instances']}=={'extra'}
        final.write_text('module top(input a); MAC hc_resource_6_memory (.A(a)); sg13g2_inv_1 gate (.A(a)), gate2 (.A({a,a})); endmodule\n')
        o=inspect(root)['observations'][1]
        assert o['measurements']['standard_cell_count']['value']==2
        for statement in ['MAC hc_resource_6_memory (.A(a)) omitted (.A(a));',
                          'MAC hc_resource_6_memory (.A(a)), extra [1:0] (.A(a));',
                          'MAC hc_resource_6_memory (.A(a), extra (.A(a));',
                          'MAC hc_resource_6_memory (.A({a,a]));']:
            final.write_text('module top(input a); '+statement+' endmodule\n')
            o=inspect(root,1)['observations'][1]
            assert o['resources'][0]['status']=='unresolved'
            assert o['measurements']['macro_count']['value'] is None

        root=temp/'hierarchy';flow,mapped,record=fixture(root)
        net=flow/'06-yosys-synthesis/top.nl.v.json'
        for definition in [
                dict(attributes=dict(area='2.5'),cells={'hidden_macro':dict(type='MAC')}),
                dict(attributes=dict(area='2.5',blackbox='1'),cells={'hidden_macro':dict(type='MAC')}),
                dict(attributes=dict(area='2.5',blackbox='1'),processes={'behavior':'unsupported'}),
                dict(attributes=dict(area='2.5'),cells={})]:
            hidden=copy.deepcopy(mapped)
            hidden['modules']['sg13g2_wrapper']=definition
            hidden['modules']['top']['cells']['gate']['type']='sg13g2_wrapper'
            write(net,hidden)
            o=inspect(root,1)['observations'][0]
            assert o['resources'][0]['status']=='unresolved'
            assert 'non-leaf/non-library' in o['resources'][0]['reason']
        write(net,mapped)
        assert inspect(root)['check_outcomes'][0]['status']=='pass'

        root=temp/'boundary';flow,mapped,record=fixture(root)
        for status in ['failed','interrupted','completed']:
            record.update(status=status,completed_stage=None);write(root/'run.json',record)
            o=inspect(root,1)['observations'][0]
            assert o['resources'][0]['status']=='unresolved'
            assert 'completion/producer boundary' in o['resources'][0]['reason']
        record.update(status='failed',requested_stage='full',completed_stage='synthesis');write(root/'run.json',record)
        assert [o['status'] for o in inspect(root,1)['observations']]==['pass','fail']
        record.update(status='completed',requested_stage='synthesis',completed_stage='synthesis')
        record['outputs']['flow']='not-the-producing-flow';write(root/'run.json',record)
        write(root/'mapped.json',mapped)
        o=inspect(root,1)['observations'][0]
        assert o['resources'][0]['status']=='unresolved','root aliases must not rescue an unrelated live flow'

        root=temp/'assertions';flow,mapped,record=fixture(root)
        valid=inspect(root)
        bad=copy.deepcopy(valid);bad['observations'][0]['measurements']['macro_count']['value']=0
        assert 'count' in reject_validate(root,bad)
        bad=copy.deepcopy(valid);bad['observations'][0]['resources']=[];bad['check_outcomes'][0]['required']=False
        assert 'resource inventory rows' in reject_validate(root,bad)
        bad=copy.deepcopy(valid);bad['provenance']['completed_stage']=None
        assert 'synthesis completion' in reject_validate(root,bad)
        # Genuine missing-macro evidence remains valid and archivable.
        mapped['modules']['top']['cells']={'gate':dict(type='sg13g2_inv_1')}
        write(flow/'06-yosys-synthesis/top.nl.v.json',mapped)
        write(flow/'06-yosys-synthesis/reports/stat.json',dict(creator='Yosys fixture',design=dict(
            num_cells=1,num_cells_by_type={'sg13g2_inv_1':1},area=2.5,sequential_area=0)))
        failed_results=consume('collect',root);failed=failed_results['resource_evidence']
        assert failed['observations'][0]['resources'][0]['status']=='missing'
        write(root/'results.json',failed_results)
        assert consume('verify',root)=='valid'
        genuine_out=temp/'genuine-failed-archive'
        call([cli,'archive',str(root),'--output',str(genuine_out),'--json'])
        assert sha(genuine_out/'results.json')==sha(root/'results.json')
        with tarfile.open(genuine_out/'reports.tar.gz') as archive:
            members=set(archive.getnames())
        assert all(ref['path'] in members for ref in failed['evidence_refs'])
        assert not consume('report',root)['accepted']
        # First reject a forged row that contradicts its own typed counts.
        forged_results=copy.deepcopy(failed_results);forged=forged_results['resource_evidence']
        o=forged['observations'][0];row=o['resources'][0]
        row.update(status='matched',realized=dict(instances=copy.deepcopy(row['expected']['instances']),count=1))
        o['status']='pass';forged['check_outcomes'][0]['status']='pass'
        assert 'macro type count' in reject_validate(root,forged)
        write(root/'results.json',forged_results)
        reject_archive(root,temp/'contradictory-archive')
        # Also reject a fully coherent assertion whose *unchanged hashed bytes*
        # still contain no macro. Hash/provenance verification alone is insufficient.
        o['cell_type_counts']['types'].append(dict(module='MAC',count=1,classification='declared_macro'))
        o['measurements']['macro_count']['value']=1;o['measurements']['total_mapped_cells']['value']=2
        write(root/'candidate.json',forged)
        assert consume('validate',root/'candidate.json')=='valid','offline shape validation is not source authentication'
        write(root/'results.json',forged_results)
        assert 'source-aware reconciliation replay' in consume('verify',root)
        reject_archive(root,temp/'coherent-forgery-archive')
        before=sha(root/'results.json')
        assert not consume('report',root)['accepted']
        assert before==sha(root/'results.json')
        # Even erasing the inventory anchor and making the rest coherent cannot
        # erase required expectations in the retained, hashed manifest on replay.
        forged['resource_inventory']=[];o['resources']=[];forged['check_outcomes'][0]['required']=False
        o['cell_type_counts']['types']=[t for t in o['cell_type_counts']['types'] if t['module']!='MAC']
        o['measurements']['macro_count']['value']=0;o['measurements']['total_mapped_cells']['value']=1
        write(root/'candidate.json',forged)
        assert consume('validate',root/'candidate.json')=='valid'
        write(root/'results.json',forged_results)
        assert 'source-aware reconciliation replay' in consume('verify',root)
        reject_archive(root,temp/'erased-inventory-archive')
        # Restoring genuine failed evidence remains archivable after negatives.
        write(root/'results.json',failed_results)
        assert consume('verify',root)=='valid'
        assert consume('archive',root)

        # Mapped finalization contexts have no coarse run.json. Replay must use
        # the explicit, digest-covered context rather than inventing a run link.
        root=temp/'explicit-context';flow,mapped,record=fixture(root)
        write(root/'context.json',record);(root/'run.json').unlink()
        results=consume('collect-context',root)
        assert results['resource_evidence']['provenance']['run']['status']=='unavailable'
        write(root/'results.json',results)
        assert consume('verify-context',root)=='valid'
        assert consume('verify',root)!='valid'
        record['run_id']='different-attempt';write(root/'context.json',record)
        assert 'source-aware reconciliation replay' in consume('verify-context',root)

        root=temp/'area';flow,mapped,record=fixture(root)
        manifest=json.loads((root/'project/manifest.json').read_text());manifest.pop('identity')
        typ='sg13g2_macro'
        manifest['resources'][0]['selection']=manifest['resources'][0]['selection'].replace('Macro MAC','Macro '+typ)
        manifest['macro_contract']['macros'][0]['module']=typ
        manifest['flow_configuration']['MACROS'][typ]=manifest['flow_configuration']['MACROS'].pop('MAC')
        identity=hashlib.sha256(json.dumps(manifest,separators=(',',':')).encode()).hexdigest()
        write(root/'project/manifest.json',dict(identity=identity,**manifest))
        record.update(build_identity=identity,manifest_sha256=sha(root/'project/manifest.json'));write(root/'run.json',record)
        mapped['modules'][typ]=mapped['modules'].pop('MAC');mapped['modules'][typ]['attributes']['area']='100.25'
        mapped['modules']['top']['cells']['hc_resource_6_memory']['type']=typ
        write(flow/'06-yosys-synthesis/top.nl.v.json',mapped)
        lib=root/'models/MAC.lib';lib.write_text('library (x) { cell ('+typ+') { area : 100.25; } }\n')
        write(root/'preflight.json',dict(references=[dict(reference='pdk_dir::lib/MAC.lib',sha256=sha(lib))]))
        for reported,expected_standard,expected_combined in [(102.75,None,None),(2.5,2.5,102.75)]:
            write(flow/'06-yosys-synthesis/reports/stat.json',dict(creator='Yosys fixture',design=dict(
                num_cells=2,num_cells_by_type={typ:1,'sg13g2_inv_1':1},area=reported,sequential_area=0)))
            o=inspect(root)['observations'][0];m=o['measurements']
            assert m['standard_cell_count']['value']==1 and m['macro_count']['value']==1
            assert m['macro_library_area']['value']==100.25
            assert m['synthesis_standard_cell_area']['value']==expected_standard
            assert m['combined_cell_area_estimate']['value']==expected_combined
            assert next(t for t in o['cell_type_counts']['types'] if t['module']==typ)['classification']=='declared_macro'
    print(f'PT.5 independent review regressions: {cases} CLI observations; strict count/inventory negatives; coherent source forgery rejection; genuine failed archive publication/closure passed')


if __name__=='__main__':
    main()
