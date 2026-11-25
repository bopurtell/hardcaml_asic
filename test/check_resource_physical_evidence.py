"""Resource physical/schema/replay integration. Rebound fixtures are not EDA proof."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from check_resource_evidence import fixture, sha, write

MASTER = 'RM_IHPSG13_1P_256x16_c2_bm_bist'
INSTANCE = 'hc_resource_6_memory'


def physical_fixture(root):
    flow, mapped, record = fixture(root)
    manifest = json.loads((root/'project/manifest.json').read_text())
    manifest['resources'][0]['selection'] = manifest['resources'][0]['selection'].replace('Macro MAC', f'Macro {MASTER}')
    manifest['macro_contract']['macros'][0]['module'] = MASTER
    manifest['flow_configuration']['MACROS'][MASTER] = manifest['flow_configuration']['MACROS'].pop('MAC')
    manifest['flow_configuration']['MACROS'][MASTER]['lef'] = ['pdk_dir::lef/macro.lef']
    manifest.pop('identity')
    identity = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest()
    write(root/'project/manifest.json', dict(identity=identity, **manifest))
    record.update(build_identity=identity, manifest_sha256=sha(root/'project/manifest.json'),
                  requested_stage='full', completed_stage='full')
    mapped['modules'][MASTER] = mapped['modules'].pop('MAC')
    mapped['modules']['top']['cells'][INSTANCE]['type'] = MASTER
    mapped['modules']['sg13g2_spacer'] = dict(attributes=dict(area='1', blackbox='1'))
    mapped['modules']['top']['cells']['spacer'] = dict(type='sg13g2_spacer')
    write(flow/'06-yosys-synthesis/top.nl.v.json', mapped)
    write(flow/'06-yosys-synthesis/reports/stat.json', dict(design=dict(
        num_cells=3, num_cells_by_type={MASTER: 1, 'sg13g2_inv_1': 1, 'sg13g2_spacer': 1}, area=3.5)))
    lib = root/'models/MAC.lib'
    lib.write_text(f'library(x) {{ cell({MASTER}) {{ area:28127.104; }} }}')
    baseline = root/'baseline-inputs'
    baseline.mkdir()
    (baseline/'0-macro.lef').write_text(f'MACRO {MASTER}\n CLASS BLOCK ;\n ORIGIN 0 0 ;\n SIZE 236.8 BY 118.78 ;\nEND {MASTER}\n')
    (baseline/'1-cells.lef').write_text('MACRO sg13g2_inv_1\n CLASS CORE ;\n ORIGIN 0 0 ;\n SIZE 2.5 BY 1 ;\nEND sg13g2_inv_1\n'
                                          'MACRO sg13g2_spacer\n CLASS CORE SPACER ;\n ORIGIN 0 0 ;\n SIZE 1 BY 1 ;\nEND sg13g2_spacer\n')
    write(root/'preflight.json', dict(references=[
        dict(reference='pdk_dir::lib/MAC.lib', sha256=sha(lib)),
        dict(reference='pdk_dir::lef/macro.lef', role='macro:Physical_abstract', sha256=sha(baseline/'0-macro.lef')),
        dict(path='/external/cells.lef', role='(Technology_view Standard_cell_lef)', sha256=sha(baseline/'1-cells.lef'))]))
    net = flow/'final/nl/top.nl.v'
    net.parent.mkdir(parents=True)
    net.write_text(f'module top(input a);\n{MASTER} {INSTANCE}(.A(a));\nsg13g2_inv_1 gate(.A(a));\nsg13g2_spacer spacer();\nendmodule\n')
    gds = flow/'final/gds/top.gds'
    gds.parent.mkdir()
    gds.write_bytes(b'fixture, not real GDS')
    geo = flow/'21-openroad-generatepdn/macro-power-geometry.json'
    write(geo, dict(schema_version=1, strategy='cmos5l_m4_edge_access_v1', dbu_per_um=1000, manufacturing_grid_dbu=5,
                    macros=[dict(resource='memory', instance=INSTANCE, module=MASTER, orientation='R0',
                                 footprint_dbu=[40000, 81000, 276800, 199780], halo_dbu=2000)], public_pins=[]))
    deffile = flow/'54-odb-cellfrequencytables/top.def'
    deffile.parent.mkdir()
    deffile.write_text(f'VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 500000 500000 ) ;\n'
                       f'COMPONENTS 3 ;\n- {INSTANCE} {MASTER} + FIXED ( 40000 81000 ) N ;\n'
                       '- gate sg13g2_inv_1 + PLACED ( 2000 2000 ) N ;\n'
                       '- spacer sg13g2_spacer + PLACED ( 5000 2000 ) N ;\nEND COMPONENTS\nEND DESIGN\n')
    write(flow/'final/metrics.json', dict(design__die__bbox='0 0 500 500', design__core__bbox='2 2 498 498',
        design__instance__area=28130.6, design__instance__area__stdcell=2.5,
        design__instance__area__macros=28127.1, design__instance__area__class_missing=0,
        **{'design__instance__area__class:fill_cell': 1}))

    def rebind():
        pre = json.loads((root/'preflight.json').read_text())
        if (baseline/'0-macro.lef').exists():
            pre['references'][1]['sha256'] = sha(baseline/'0-macro.lef')
        if (baseline/'1-cells.lef').exists():
            pre['references'][2]['sha256'] = sha(baseline/'1-cells.lef')
        write(root/'preflight.json', pre)
        checks = dict(schema_version=1, status='pass', checking_profile='cmos5l_real_gds_v1',
                      lvs_scope='abstract_macro_boundary_with_schematic_cdl',
                      gds_sha256=sha(gds), power_geometry_sha256=sha(geo), final_power_geometry='retained_in_real_gds',
                      artifacts={str(p.relative_to(flow)): sha(p) for p in [geo, deffile, net]})
        write(root/'physical-checks.json', checks)
        record['physical_checks_sha256'] = sha(root/'physical-checks.json')
        write(root/'run.json', record)
    rebind()
    return flow, rebind


def attach_sampled_timing(root, flow):
    """Diagnostic metadata around B's actual excerpt; no timing producer run."""
    corner = 'nom_slow_1p08V_125C'
    manifest_path = root/'project/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['flow_configuration']['MACROS'][MASTER]['lib'][corner] = ['pdk_dir::lib/MAC.lib']
    manifest.pop('identity')
    identity = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest()
    write(manifest_path, dict(identity=identity, **manifest))
    record = json.loads((root/'run.json').read_text())
    record.update(run_id='fixturept5b', build_identity=identity, manifest_sha256=sha(manifest_path))
    write(root/'run.json', record)
    producer = flow/'56-openroad-stapostpnr'
    recorded_flow = '/recorded/20261027_fixturep/project/runs/asic'
    recorded_netlist = recorded_flow+'/53-openroad-fillinsertion/top.nl.v'
    retained_netlist = flow/'53-openroad-fillinsertion/top.nl.v'
    retained_netlist.parent.mkdir()
    retained_netlist.write_bytes((flow/'final/nl/top.nl.v').read_bytes())
    write(producer/'config.json', dict(meta=dict(step='OpenROAD.STAPostPNR', librelane_version='3.1.0.dev3'),
        STA_CORNERS=[corner], MACROS={MASTER: dict(lib={corner: ['/external/lib/MAC.lib']})}))
    write(producer/'state_in.json', dict(nl=recorded_netlist))
    write(producer/'state_out.json', dict(nl=recorded_netlist,
        lib={corner: recorded_flow+'/56-openroad-stapostpnr/'+corner+'/top__'+corner+'.lib'}))
    (producer/'runtime.txt').write_text('00:00:01.000\n')
    path = producer/corner
    path.mkdir()
    (path/'top__nom_slow_1p08V_125C.lib').write_text('library(x) {\n time_unit : "1ns";\n capacitive_load_unit (1,pF);\n}\n')
    (path/'sta.log').write_text(f"+ define_corners {corner}\nReading top-level netlist at '{recorded_netlist}'…\n"
        f"Reading cell library for the '{corner}' corner at '/external/lib/MAC.lib'…\n")
    report = (Path(__file__).parent/'resource_timing/fixtures/slow-max.rpt').read_text()
    (path/'max.rpt').write_text(report.replace('hc_resource_7_program', INSTANCE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--consumer', type=Path, required=True)
    args = parser.parse_args()
    checks = 0

    def invoke(root, code=0):
        nonlocal checks
        p = subprocess.run([str(args.cli.resolve()), 'resources-inspect', str(root), '--json'], capture_output=True, text=True)
        if p.returncode != code:
            detail = json.loads(p.stdout) if p.stdout else p.stderr
            if isinstance(detail, dict) and detail.get('observations'):
                detail = detail['observations'][-1].get('physical', detail)
                detail = dict(status=detail.get('status'), reason=detail.get('reason'),
                    measurements={k:v['value'] for k,v in detail.get('measurements',{}).items()})
            raise AssertionError((code, p.returncode, detail, p.stderr))
        checks += 1
        return json.loads(p.stdout)

    def consume(op, path):
        p = subprocess.run([str(args.consumer.resolve()), op, str(path)], capture_output=True, text=True)
        assert p.returncode == 0, (op, p.stdout, p.stderr)
        return json.loads(p.stdout)

    with tempfile.TemporaryDirectory(prefix='resource-physical-') as tmp:
        root = Path(tmp)
        flow, rebind = physical_fixture(root)
        baseline = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
        e = invoke(root)
        assert e['schema_version'] == 2
        physical = e['observations'][1]['physical']
        m = physical['measurements']
        assert abs(m['macro_footprint_area']['value'] - 28127.104) < 1e-8
        assert m['nonspacer_component_area']['value'] == 2.5
        assert m['spacer_component_count']['value'] == 1
        assert abs(m['all_component_area']['value'] - 28130.604) < 1e-8
        assert physical['floorplan']['core']['area']['value'] == 496**2
        assert len(physical['ratios']) == 6
        assert e['observations'][1]['resources'][0]['geometry']['instances'][0]['bounds_dbu'] == [40000,81000,276800,199780]
        write(root/'results.json', dict(resource_evidence=e))
        verdict = consume('verify', root)
        assert verdict == 'valid', verdict
        advertised = {r['path'] for r in e['evidence_refs']}
        assert 'baseline-inputs/1-cells.lef' in advertised
        assert advertised <= set(consume('archive', root)), 'archive must include every new source'
        # Historical physical schema 1 deliberately lacks the richer geometry,
        # even though today's schema 2 can derive it from these same bytes.
        old = copy.deepcopy(e)
        old['schema_version'] = 1
        old['evidence_refs'] = [r for r in old['evidence_refs'] if not r['path'].startswith('baseline-inputs/')]
        for o in old['observations']:
            o.pop('physical', None)
            o.pop('timing_diagnostics', None)
            for r in o['resources']:
                r.pop('geometry')
                if o['stage'] == 'final_physical':
                    r['timing_links'] = dict(status='unavailable', reason='no resource-linked path evidence retained')
                    r['electrical_links'] = dict(status='unavailable', reason='global checks do not establish per-resource attribution')
                    r['area'] = dict(value=None, unit='um^2', definition='resource area attribution',
                        stage='final_physical', scope='per-resource area', evidence_refs=o['evidence_refs'],
                        unavailable_reason='no resource-specific area evidence retained')
        write(root/'results.json', dict(resource_evidence=old))
        verdict = consume('verify', root)
        assert verdict == 'valid', ('physical schema 1 replay', verdict)
        write(root/'results.json', dict(resource_evidence=e))

        # Present contradictory sources must fail even after hashes are rebound.
        geo = flow/'21-openroad-generatepdn/macro-power-geometry.json'
        deffile = flow/'54-odb-cellfrequencytables/top.def'
        macro_lef = root/'baseline-inputs/0-macro.lef'
        cells_lef = root/'baseline-inputs/1-cells.lef'
        cells_lef.write_bytes(baseline[cells_lef] + b'\n# stale bytes\n')
        assert consume('verify', root) != 'valid'
        cells_lef.write_bytes(baseline[cells_lef])
        mutations = [
            (geo, lambda j: j['macros'][0].update(instance=INSTANCE+'_near')),
            (geo, lambda j: j['macros'][0].update(module='wrong')),
            (geo, lambda j: j['macros'].append(copy.deepcopy(j['macros'][0]))),
            (geo, lambda j: j.update(macros=[])),
            (geo, lambda j: j.update(dbu_per_um=2000)),
            (geo, lambda j: j.update(dbu_per_um=1000.0)),
            (geo, lambda j: j['macros'][0].update(orientation='R90')),
            (geo, lambda j: j['macros'][0].update(halo_dbu=-1)),
            (geo, lambda j: j['macros'][0].update(footprint_dbu=[41000,81000,277800,199780])),
            (geo, lambda j: j['macros'][0].update(footprint_dbu=[40000,81000,276805,199780])),
            (deffile, lambda t: t.replace('COMPONENTS 3', 'COMPONENTS 2')),
            (deffile, lambda t: t.replace('40000 81000', '40005 81000')),
            (deffile, lambda t: t.replace(') N ;', ') FN ;', 1)),
            (deffile, lambda t: t.replace('END COMPONENTS', '')),
            (deffile, lambda t: t.replace('END COMPONENTS', 'END COMPONENTS_WRONG')),
            (deffile, lambda t: t.replace('END COMPONENTS', 'END COMPONENTS-WRONG')),
            (macro_lef, lambda t: t.replace('ORIGIN 0 0', 'ORIGIN 1 0')),
            (macro_lef, lambda t: t.replace('CLASS BLOCK', 'CLASS CORE SPACER')),
            (macro_lef, lambda t: t.replace('236.8', '236.9')),
            (macro_lef, lambda t: t.replace('236.8', '236.80000000000001')),
            (macro_lef, lambda t: t.replace('SIZE 236.8 BY 118.78 ;', 'PROPERTY fake "ignored\nSIZE 236.8 BY 118.78 ;\n" ;')),
            (cells_lef, lambda t: t.replace('SIZE 1 BY 1', 'SIZE 1 BY 1 ;\n SIZE 2 BY 2')),
            (cells_lef, lambda t: t.replace('END sg13g2_inv_1', 'PIN A\n DIRECTION INPUT ;\nEND sg13g2_inv_1')),
            (cells_lef, lambda t: t.replace('END sg13g2_inv_1', 'PIN A\n PORT\n END A\nEND sg13g2_inv_1')),
            (cells_lef, lambda t: t.replace('END sg13g2_inv_1', 'OBS\n LAYER M1 ;\nEND sg13g2_inv_1')),
            (flow/'final/metrics.json', lambda j: j.update(design__core__bbox='2 2 2 498')),
            (flow/'final/metrics.json', lambda j: j.update(design__core__bbox='-2 2 498 498')),
            (flow/'final/metrics.json', lambda j: j.update(design__core__bbox='2 2 100 100')),
            (flow/'final/metrics.json', lambda j: j.update(design__die__bbox='0 0 501 500')),
            (flow/'final/metrics.json', lambda j: j.update(design__instance__area=1)),
            (flow/'final/metrics.json', lambda j: j.update(design__instance__count__stdcell=200)),
        ]
        for nested in ['PIN A\n DIRECTION INPUT ;\nEND A', 'OBS\n LAYER M1 ;\nEND', 'DENSITY\n LAYER M1 ;\nEND']:
            for attribute in ['SIZE 200 BY 200 ;', 'CLASS CORE SPACER ;', 'ORIGIN 1 0 ;']:
                mutations.append((cells_lef, lambda t, nested=nested, attribute=attribute:
                    t.replace('END sg13g2_inv_1', nested+'\n '+attribute+'\nEND sg13g2_inv_1')))
        for path, mutate in mutations:
            raw = baseline[path].decode()
            if path.suffix == '.json':
                j = json.loads(raw)
                mutate(j)
                write(path, j)
            else:
                path.write_text(mutate(raw))
            rebind()
            bad = invoke(root, 1)
            assert bad['observations'][1]['physical']['status'] == 'invalid', path
            for p, data in baseline.items():
                p.write_bytes(data)

        # Unsupported/missing optional LEF coverage does not borrow external PDK.
        cells_lef.unlink()
        absent = invoke(root)
        assert absent['observations'][1]['physical']['measurements']['nonspacer_component_area']['value'] is None
        assert absent['observations'][1]['resources'][0]['geometry']['status'] == 'available'
        macro_lef.write_text(baseline[macro_lef].decode().replace('236.8', '236.9'))
        rebind()
        assert invoke(root, 1)['observations'][1]['physical']['status'] == 'invalid', 'partial retained coverage must still reject contradictions'
        for p, data in baseline.items():
            p.write_bytes(data)
        cells_lef.write_bytes(baseline[cells_lef])
        preflight_path = root/'preflight.json'
        preflight = json.loads(preflight_path.read_text())
        preflight['references'][2]['role'] = 'unrelated_collateral'
        write(preflight_path, preflight)
        unbound = invoke(root)
        assert unbound['observations'][1]['physical']['measurements']['nonspacer_component_area']['value'] is None
        preflight_path.write_bytes(baseline[preflight_path])

        # Real nested terminators return to macro scope; supported well-formed
        # nested groups remain usable rather than being blanket rejected.
        nested = ('PIN A\n DIRECTION INPUT ;\n PORT\n LAYER M1 ;\n RECT 0 0 1 1 ;\n END\nEND A\n'
                  'OBS\n LAYER M1 ;\n RECT 0 0 1 1 ;\nEND\nDENSITY\n LAYER M1 ;\nEND\n')
        cells_lef.write_text(baseline[cells_lef].decode().replace('END sg13g2_inv_1', nested+'END sg13g2_inv_1'))
        rebind()
        assert invoke(root)['observations'][1]['physical']['measurements']['nonspacer_component_area']['value'] == 2.5
        for p, data in baseline.items():
            p.write_bytes(data)

        # Partial coverage must not suppress independent class comparisons,
        # lower bounds or native category/total consistency.
        partial_cases = [
            ([cells_lef], None, dict(design__instance__area=1), 1),
            ([macro_lef], None, dict(design__instance__area__stdcell=1), 1),
            ([macro_lef], None, {'design__instance__area__class:fill_cell': 0}, 1),
            ([macro_lef], None, {}, 0),
            ([macro_lef, cells_lef], None, dict(design__instance__area=1), 1),
            ([macro_lef, cells_lef], None, dict(design__instance__area=28127.1), 1),
            ([macro_lef, cells_lef], None, dict(design__instance__count__stdcell=3), 1),
            ([], 'MACRO sg13g2_spacer', dict(design__instance__area__stdcell=1), 1),
            ([], 'MACRO sg13g2_spacer', dict(design__instance__area__stdcell=5,
                design__instance__count__stdcell=1, design__instance__area=28133.1), 1),
        ]
        for removed, truncate, updates, code in partial_cases:
            for p, data in baseline.items():
                p.write_bytes(data)
            for p in removed:
                p.unlink()
            if truncate:
                cells_lef.write_text(baseline[cells_lef].decode().split(truncate)[0])
            metrics_path = flow/'final/metrics.json'
            metrics = json.loads(metrics_path.read_text())
            metrics.update(updates)
            write(metrics_path, metrics)
            rebind()
            try:
                partial = invoke(root, code)
            except AssertionError as e:
                raise AssertionError((removed, truncate, updates, e)) from e
            assert partial['observations'][1]['physical']['status'] == ('invalid' if code else 'available')
            write(root/'results.json', dict(resource_evidence=partial))
            assert consume('verify', root) == 'valid', 'genuine failing evidence still replays'
            assert {r['path'] for r in partial['evidence_refs']} <= set(consume('archive', root))
        for p, data in baseline.items():
            p.write_bytes(data)

        # Strict validation of every new nested object, not only source hashes.
        for mutate in [
            lambda j: j['observations'][1]['physical']['floorplan']['core'].update(hidden=1),
            lambda j: j['observations'][1]['physical']['floorplan']['core']['area'].update(unit='mm^2'),
            lambda j: j['observations'][1].update(physical=dict(status='unavailable',reason='forged unavailable')),
            lambda j: j['observations'][1]['physical']['ratios'][0].update(value=0),
            lambda j: j['observations'][1]['physical']['ratios'][0]['denominator'].update(value=0),
            lambda j: j['observations'][1]['resources'][0]['geometry']['instances'][0].update(origin_dbu=[0,0]),
            lambda j: j['observations'][1]['resources'][0]['geometry']['instances'][0].update(orientation='N'),
            lambda j: j['observations'][1]['resources'][0]['geometry']['instances'][0].update(corner='typ'),
            lambda j: j['observations'][1]['resources'][0]['area'].update(unit='mm^2'),
            lambda j: j['observations'][1]['resources'][0]['area'].update(evidence_refs=[]),
            lambda j: j['observations'][1]['resources'][0]['area'].update(evidence_refs=j['observations'][1]['resources'][0]['geometry']['evidence_refs'][:1]),
            lambda j: j['observations'][1]['physical']['measurements']['macro_component_count'].update(value=0.5),
        ]:
            forged = copy.deepcopy(e)
            mutate(forged)
            write(root/'assertion.json', forged)
            assert consume('validate', root/'assertion.json') != 'valid'

        # A coherent geometry forgery over unchanged sources must fail replay.
        forged = copy.deepcopy(e)
        g = forged['observations'][1]['resources'][0]['geometry']['instances'][0]
        g['origin_dbu'][0] += 5
        g['bounds_dbu'][0] += 5
        g['bounds_dbu'][2] += 5
        write(root/'results.json', dict(resource_evidence=forged))
        assert consume('verify', root) != 'valid'

        # A's public schema/replay/archive seam includes B's actual report parser,
        # and rejects coherent timing forgeries over unchanged source hashes.
        attach_sampled_timing(root, flow)
        timed = invoke(root)
        row = timed['observations'][1]['resources'][0]
        assert row['timing_links']['status'] == 'observed', (row['timing_links'], timed['observations'][1]['timing_diagnostics'])
        assert len(row['timing_links']['paths']) == 1
        assert row['timing_links']['paths'][0]['slack'] == 11.246561
        write(root/'results.json', dict(resource_evidence=timed))
        assert consume('verify', root) == 'valid'
        timed_refs = {r['path'] for r in timed['evidence_refs']}
        assert any(p.endswith('/max.rpt') for p in timed_refs)
        assert any(p.endswith('/top__nom_slow_1p08V_125C.lib') for p in timed_refs)
        assert timed_refs <= set(consume('archive', root))
        forged = copy.deepcopy(timed)
        path = forged['observations'][1]['resources'][0]['timing_links']['paths'][0]
        path['slack'] += 1
        path['required'] += 1
        write(root/'assertion.json', forged)
        assert consume('validate', root/'assertion.json') == 'valid', 'coherent forgery should reach source replay'
        write(root/'results.json', dict(resource_evidence=forged))
        assert 'source-aware' in consume('verify', root)

    # Schema-1 synthesis assertions replay with schema-1 semantics, not schema 2.
    with tempfile.TemporaryDirectory(prefix='resource-schema1-') as tmp:
        root = Path(tmp)
        fixture(root)
        old = invoke(root)
        old['schema_version'] = 1
        for o in old['observations']:
            for r in o['resources']:
                r.pop('geometry')
        write(root/'results.json', dict(resource_evidence=old))
        verdict = consume('verify', root)
        assert verdict == 'valid', verdict
        old['observations'][0]['resources'][0]['geometry'] = dict(status='unavailable',reason='forged extension')
        write(root/'assertion.json', old)
        assert consume('validate', root/'assertion.json') != 'valid'
    print(f'Resource physical evidence: {checks} CLI observations; semantic rebound negatives, strict schema and recorded-schema replay passed')


if __name__ == '__main__':
    main()
