"""Durable same-process directory rejection regression, not review characterization."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def raw(j, sort=False):
    return json.dumps(j, separators=(',', ':'), sort_keys=sort).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(p, j):
    p.write_bytes(raw(j))


def ref(p):
    return dict(path=p.name, sha256=sha(p.read_bytes()), bytes=p.stat().st_size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--consumer', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    a = parser.parse_args()
    variant = json.loads(subprocess.check_output([str(a.consumer.resolve()), 'variant']))
    with tempfile.TemporaryDirectory(prefix='pt8-directory-regression-') as tmp:
        root = Path(tmp)
        bundle = root / 'bundle'
        bundle.mkdir()
        rtl = b'module top; endmodule\n'
        (bundle / 'top.v').write_bytes(rtl)
        manifest = dict(schema_version=1, adapter='LibreLane', project='fd-regression', top_module='top',
                        resources=[], files=[dict(path='top.v', sha256=sha(rtl))], source_inputs=[],
                        synthesis_sources=['top.v'], simulation_sources=['top.v'], hardware_variant=variant)
        manifest = dict(identity=sha(raw(manifest)), **manifest)
        write(bundle / 'manifest.json', manifest)
        artifacts = []
        for role, data in [('rtl', rtl), ('model', b'no-external-model'),
                           ('testbench', b'fixture'), ('tool', b'fixture-not-executed')]:
            p = root / (role + '.txt')
            p.write_bytes(data)
            artifacts.append(dict(role=role, source=ref(p)))
        op = dict(enable=False, write=False, address=0, data=0)
        workload = dict(protocol='memory-1rw-latency1-v1', seed=0, depth=256, width=16,
                        window=dict(start=0, stop=1), operations=[op], expected=[dict(before=None, after=None)])
        workload['identity'] = sha(raw(workload, True))
        write(root / 'stimulus.json', dict(operations=[op]))
        write(root / 'trace.json', dict(rows=[dict(cycle=0, input=op, before=None, after=None)]))
        simulation = dict(contract='hardcaml-asic.workload-simulation', schema_version=1,
                          attempt_id='fd-regression', status='completed', bundle_identity=manifest['identity'],
                          hardware_variant_identity=variant['identity'], workload_identity=workload['identity'],
                          stimulus=ref(root / 'stimulus.json'), trace=ref(root / 'trace.json'),
                          rtl_mode='implementation', artifacts=artifacts, adapter='memory-1rw-latency1-v1',
                          clock='posedge-single-clock-v1', model='memory-interface-v1',
                          measurement='memory-1rw-window-v1')
        write(root / 'simulation.json', simulation)
        result = dict(contract='hardcaml-asic.workload-result', schema_version=1, bundle=str(bundle),
                      workload=workload, simulation=ref(root / 'simulation.json'), stimulus=simulation['stimulus'],
                      trace=simulation['trace'], counters=dict(cycles=1, reads=0, writes=0, operations=0,
                                                            defined_observations=0, read_latency=None))
        positive = root / 'valid.json'
        write(positive, result)
        directory = root / 'directory'
        directory.mkdir()
        directory_ref = dict(path=directory.name, sha256=sha(b''), bytes=0)
        rejected = [directory]
        for key in ['stimulus', 'trace', 'simulation']:
            bad = dict(result, **{key: directory_ref})
            path = root / ('directory-' + key + '.json')
            write(path, bad)
            rejected.append(path)
        bad_sim = copy.deepcopy(simulation)
        bad_sim['artifacts'][1]['source'] = directory_ref
        sim_path = root / 'directory-artifact-simulation.json'
        write(sim_path, bad_sim)
        path = root / 'directory-artifact.json'
        write(path, dict(result, simulation=ref(sim_path)))
        rejected.append(path)
        p = subprocess.run([str(a.probe.resolve()), str(positive), *map(str, rejected)],
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        receipts = json.loads(p.stdout)
        assert len(receipts) == 5
        assert all(r['growth'] == 0 and r['errors'] == 128 for r in receipts)
        print('workload_result directory regression: 640 same-process errors, zero descriptor growth')
        print(json.dumps(receipts))


if __name__ == '__main__':
    main()
