"""CLI4 installed native composition corpus; no physical runtime is required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
from reference_harness import activate, copy_reference, hardlink_prefix, thin_prefix, ENTRY


def run(binary, args, cwd, **kwargs):
    return subprocess.run([str(binary), *args], cwd=cwd, text=True,
                          capture_output=True, **kwargs)


def main():
    with tempfile.TemporaryDirectory(dir='/tmp/opencode', prefix='native-cli-') as tmp:
        root = Path(tmp)
        # A prefix called default must never be mistaken for a Dune checkout.
        prefix = root / 'default'
        hardlink_prefix(REPO / '_build/install/default', prefix)
        binary = prefix / 'bin/hardcaml-asic-flow'
        package = prefix / 'lib/hardcaml_asic/flow/hardcaml_asic_flow'
        reference = copy_reference(REPO / 'flow', root / 'oracle', package.parent)
        activate(reference, package.parent)
        from hardcaml_asic_flow import identity
        thin_prefix(prefix)
        shutil.copyfile(Path(__file__).with_name('reference_harness.py'), root / 'reference_harness.py')
        oracle_driver = root / 'oracle_cli.py'
        oracle_driver.write_text('from hardcaml_asic_flow.cli import main\nimport sys\nsys.exit(main(sys.argv[1:]))\n')
        oracle_env = dict(os.environ, FLOW_TEST_REFERENCE_ROOT=str(reference),
                          FLOW_TEST_RUNTIME_ROOT=str(package.parent))
        env = dict(os.environ, PATH='/nonexistent', PYTHONPATH='/denied')
        for args in ([], ['--help'], ['--version'], ['--json', '--version']):
            p = run(binary, args, root, env=env)
            assert p.returncode == 0, p.stderr
        actual = json.loads(run(binary, ['--json', '--version'], root, env=env).stdout)
        expected = identity.describe()
        # Additive native-only named-flow launch contract; CLI4 is unchanged.
        expected['cli_contract']['capabilities']['namedflow1'] = True
        from check_synthesis_collection_metadata import CAPABILITY, verify
        expected['cli_contract']['capabilities']['synthesis_collection1'] = CAPABILITY
        expected['execution_envelope'] = None
        expected.update(module_dir=str(package), data_dir=str(package / 'data'),
                        toolchain_lock=str(package / 'data/toolchain.lock'),
                        package_version_source='installed-package-metadata')
        assert actual == expected, (actual, expected)
        verify(binary)
        # All three documented flag locations and error payload renderers.
        for args in (["observation", "describe"], ["--json", "observation", "describe"],
                     ["observation", "--json", "describe"], ["observation", "describe", "--json"],
                     ["observation", "inspect", "missing"], ["observation", "inspect", "missing", "--json"],
                     ["checkpoint-inspect", "missing"], ["checkpoint-inspect", "missing", "--json"],
                     ["finalization-inspect", "missing"], ["finalization-inspect", "missing", "--json"]):
            oracle = subprocess.run([sys.executable, '-I', '-B', '-c', ENTRY,
                str(REPO), str(oracle_driver), *args], cwd=root, env=oracle_env, text=True, capture_output=True)
            p = run(binary, args, root, env=env)
            assert p.returncode == oracle.returncode, (args, p.stderr, oracle.stderr)
            if '--json' in args:
                expected = json.loads(oracle.stdout)
                # ENTRY already applies the exact named-only describe expectation
                # from reference_harness; do not overwrite it with the old shape.
                assert json.loads(p.stdout) == expected, (args, p.stdout, oracle.stdout)
            else:
                assert p.stdout == oracle.stdout, (args, p.stdout, oracle.stdout)
            assert p.stderr == oracle.stderr, (args, p.stderr, oracle.stderr)
        invalid = [
            ['run', 'b'], ['run', 'b', '--stage', 'bad'], ['report'], ['report', 'r', '--runs', 'x'],
            ['collect', 'r', '--stdout', '--output', 'x'], ['archive', 'r'],
            ['archive', 'r', '--list', '--json'], ['archive', 'r', '--list', '--force'],
            ['--json', 'provision'], ['provision', '--json'], ['run', 'b', '--stage', 'full', '--native', '--toolchain', 'tc'],
            ['execute', 'b', '--stage', 'synthesis', '--testbench', 't', '--toolchain', 'tc'],
            ['execute', 'b', '--stage', 'full', '--toolchain', 'tc'],
            ['preflight', 'b'], ['preflight', 'b', '--results-root', ' '],
            ['preflight', 'b', '--toolchain', 'tc', '--waiver', 'bad=x'],
            ['stages', 'b'], ['observation'], ['observation', 'read-updates', 'a', '--stream-id', 's'],
            ['observation', 'watch', 'a', '--interval', 'nan'], ['observation', 'watch', 'a', '--interval', '0'],
            ['postcheck', 'r', '--testbench', 't'], ['finalize', 'e'], ['report', 'r', '--resources', 'x'],
        ]
        for args in invalid:
            p = run(binary, args, root, env=env)
            assert p.returncode == 2 and not p.stdout and p.stderr, (args, p)
        for cursor in ('+00', '1_000', '9999999999999999999999999999999999999'):
            args = ['observation', 'read-updates', 'missing', '--stream-id', 's', '--after', cursor, '--json']
            p = run(binary, args, root, env=env)
            assert p.returncode == 2 and json.loads(p.stdout)['error']['code'] == (
                'RESET_REQUIRED' if len(cursor) > 30 else 'INVALID_RECORD'), p
        # Observe native routing argv using private shell fixtures. Unlink before
        # replacement: staging and accepted prefixes share hardlinked inodes.
        for name in ('run', 'execute', 'preflight', 'postcheck', 'collect', 'archive', 'stages', 'finalization'):
            helper = prefix / ('bin/hardcaml-asic-experimental-' + name)
            helper.unlink()
            helper.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            helper.chmod(0o755)
        (root / 'real').mkdir()
        (root / 'alias').symlink_to('real', target_is_directory=True)
        p = run(binary, ['--json', 'run', 'alias/../bundle', '--stage', 'synthesis',
                         '--toolchain', 'alias', '--python', 'alias/../venv/python', '--verbose'], root, env=env)
        argv = p.stdout.splitlines()
        assert p.returncode == 0 and '--public-cli4' in argv and '--json' in argv and '--verbose' in argv
        assert argv[argv.index('--bundle') + 1] == str(root / 'bundle')
        assert argv[argv.index('--python') + 1] == str(root / 'alias/../venv/python')
        assert argv[argv.index('--runs') + 1] == str(root / '_flow_results/runs')
        for args in (['preflight', 'b', '--toolchain', 'tc'],
                     ['execute', 'b', '--toolchain', 'tc', '--stage', 'synthesis'],
                     ['postcheck', 'r', '--toolchain', 'tc', '--testbench', 't', '--testbench-top', 'tb'],
                     ['collect', 'r'], ['archive', 'r', '--list'],
                     ['finalize', 'e', '--operation-store', 'o']):
            assert run(binary, args, root, env=env).returncode == 0, args
        missing = prefix / 'bin/hardcaml-asic-experimental-run'
        missing.unlink()
        p = run(binary, ['run', 'b', '--toolchain', 'tc', '--stage', 'synthesis'], root, env=env)
        assert p.returncode == 2 and str(missing) in p.stderr and 'Reinstall' in p.stderr
        # Trace actual kernel file accesses: installed native cold commands must
        # neither open checkout source nor launch any Python interpreter.
        trace = root / 'trace'
        for args in (['--json', '--version'], ['observation', 'describe'],
                     ['checkpoint-inspect', 'missing', '--json'], ['report', 'missing', '--json']):
            subprocess.run(['/usr/bin/strace', '-f', '-e', 'trace=file,process', '-o', str(trace),
                            str(binary), *args], cwd=root, env=env, capture_output=True, check=False)
            lines = trace.read_text().splitlines()
            assert not any(str(REPO) + '/' in line for line in lines), lines
            assert not any('execve(' in line and 'python' in line for line in lines), lines
        print('native CLI: metadata, 10 cold presentation differentials, 24 contract negatives, prefix routing and Python/source-denied queries passed')


if __name__ == '__main__':
    main()
