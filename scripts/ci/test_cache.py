"""Offline tests of the native CI cache lifecycle."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
FAKE_OPAM = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ['LOG'], 'a') as f:
    print(json.dumps(a), file=f)
root = Path(os.environ['OPAMROOT'])
switch = root / 'asic'
inventory = switch / 'inventory'
if a[0] == 'init':
    root.mkdir()
elif a[:2] == ['repository', 'add']:
    pass
elif a[:2] == ['switch', 'create']:
    switch.mkdir()
elif a[0] == 'install':
    if os.environ.get('FAIL_INSTALL'):
        sys.exit('installation failed')
    if '--deps-only' in a:
        assert '--with-test' in a and Path(a[-1]).is_file()
    else:
        inventory.write_text('\\n'.join(p for p in a[1:] if not p.startswith('--')) + '\\n')
elif a[0] == 'clean':
    pass
elif a[:2] == ['var', 'root']:
    print(root)
elif a[:3] == ['switch', 'list', '--short']:
    print('asic')
elif a[0] == 'list':
    packages = inventory.read_text().splitlines()
    if '--columns=package' in a:
        print('\\n'.join(packages))
    else:
        print('\\n'.join(p.split('.', 1)[0] for p in packages))
elif a[:3] == ['exec', '--switch=asic', '--']:
    if a[3:] == ['ocamlc', '-version']:
        print('5.2.0+ox')
    elif a[3:] == ['dune', '--version']:
        print('3.24.2')
    elif a[3:] == ['ocamlfind', 'printconf']:
        print('fake findlib configuration')
    elif a[3:] == ['ocamlfind', 'query', 'hardcaml_asic']:
        sys.exit(1)
    elif a[3:] == ['bash', 'scripts/ci/check.sh']:
        os.execvp('bash', ['bash', a[4]])
    else:
        sys.exit('unexpected exec: ' + repr(a))
else:
    sys.exit('unexpected opam arguments: ' + repr(a))
"""
FAKE_CURL = """#!/usr/bin/env python3
import io, sys, tarfile
with tarfile.open(sys.argv[sys.argv.index('-o')+1], 'w:gz') as archive:
    info = tarfile.TarInfo('snapshot/repo')
    data = b'opam-version: "2.0"'
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
"""
FAKE_GIT = """#!/bin/sh
test "$1" = status || exit 1
if [ -d _build ]; then echo '!! _build/'; fi
"""


class CacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.home = self.root / "home"
        self.state = self.home / "share/asic"
        self.log = self.root / "opam.log"
        tools = self.root / "bin"
        tools.mkdir()
        for name, script in (("opam", FAKE_OPAM), ("curl", FAKE_CURL), ("git", FAKE_GIT),
                             ("strace", "#!/bin/sh\nexit 0\n"),
                             ("tclsh", "#!/bin/sh\nexit 0\n")):
            path = tools / name
            path.write_text(script)
            path.chmod(0o755)
        self.env = dict(PATH=f"{tools}:{os.defpath}", HOME=str(self.home),
                        XDG_DATA_HOME=str(self.home / "share"),
                        ASIC_CACHE_KEY="exact-key", LOG=str(self.log))
        self.fresh_source()

    def fresh_source(self):
        if self.source.exists():
            shutil.rmtree(self.source)
        ci = self.source / "scripts/ci"
        ci.mkdir(parents=True)
        for name in ("container.sh", "toolchain.env"):
            shutil.copy2(REPO / "scripts/ci" / name, ci / name)
        shutil.copy2(REPO / "scripts/ocaml-deps.sh", ci.parent)
        shutil.copy2(REPO / "hardcaml_asic.opam", self.source)
        (ci / "check.sh").write_text(
            'set -eu\n'
            'test "$OPAM_SWITCH" = asic\n'
            'test "$DUNE_CACHE" = disabled\n'
            'test ! -e _build\n'
            'mkdir _build\n'
        )

    def invoke(self, phase, **env):
        return subprocess.run(
            ["bash", "-c", 'source scripts/ci/container.sh; worker "$1"', "bash", phase],
            cwd=self.source, env=dict(self.env, **env), text=True,
            capture_output=True, timeout=30,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def cold(self):
        self.assert_success(self.invoke("provision"))
        self.assertTrue((self.state / "ci-cache-key").is_file())
        self.assertFalse((self.source / "_build").exists())
        self.assertTrue(any(c[0] == "install" for c in self.commands()))
        self.assertTrue(any("--deps-only" in c and "--with-test" in c for c in self.commands()))
        self.assertTrue(any(c[0] == "clean" for c in self.commands()))

    def assert_read_only_opam(self):
        self.assertTrue(all(c[0] in ("list", "exec", "var") or
                            c[:3] == ["switch", "list", "--short"]
                            for c in self.commands()), self.commands())

    def test_cold_and_restored_cache_both_test_fresh_source(self):
        self.cold()
        archive = self.root / "archive"
        shutil.copytree(self.state, archive)
        self.assertFalse(any(p.name == "_build" for p in archive.rglob("*")))
        self.log.unlink()
        self.assert_success(self.invoke("test"))
        self.assertTrue((self.source / "_build").is_dir())
        self.assert_read_only_opam()

        shutil.rmtree(self.home)
        self.fresh_source()
        shutil.copytree(archive, self.state)
        self.log.unlink()
        self.assert_success(self.invoke("provision", ASIC_CACHE_HIT="true"))
        self.assertFalse((self.source / "_build").exists())
        self.assert_success(self.invoke("test"))
        self.assertTrue((self.source / "_build").is_dir())
        self.assert_read_only_opam()

    def test_bad_key_or_changed_pins_are_rejected(self):
        self.cold()
        self.assertNotEqual(self.invoke("provision", ASIC_CACHE_HIT="true",
                                        ASIC_CACHE_KEY="wrong").returncode, 0)
        with (self.source / "scripts/ci/toolchain.env").open("a") as out:
            out.write("\n# changed\n")
        self.assertNotEqual(self.invoke("provision", ASIC_CACHE_HIT="true").returncode, 0)

    def test_changed_inventory_and_incomplete_receipt_are_rejected(self):
        self.cold()
        inventory = self.state / "opam/asic/inventory"
        inventory.write_text("dune.0.0\n")
        self.assertNotEqual(self.invoke("provision", ASIC_CACHE_HIT="true").returncode, 0)
        # A matching inventory/receipt is still insufficient if dependencies
        # are missing or wrong.
        (self.state / "ci-installed-packages").write_text(inventory.read_text())
        self.assertNotEqual(self.invoke("test").returncode, 0)

    def test_failed_install_never_publishes_marker(self):
        self.assertNotEqual(self.invoke("provision", FAIL_INSTALL="1").returncode, 0)
        self.assertFalse((self.state / "ci-cache-key").exists())
        self.assertNotEqual(self.invoke("provision", ASIC_CACHE_HIT="true").returncode, 0)
        self.assertNotEqual(self.invoke("provision").returncode, 0)

    def test_installed_provider_is_rejected_even_with_matching_receipt(self):
        self.cold()
        inventory = self.state / "opam/asic/inventory"
        installed = sorted(inventory.read_text().splitlines() + ["hardcaml_asic.0.1.0"])
        contents = "\n".join(installed) + "\n"
        inventory.write_text(contents)
        (self.state / "ci-installed-packages").write_text(contents)
        result = self.invoke("provision", ASIC_CACHE_HIT="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed provider", result.stderr)

    def test_dirty_checkout_is_rejected(self):
        self.cold()
        (self.source / "_build").mkdir()
        result = self.invoke("test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean checkout", result.stderr)


if __name__ == "__main__":
    unittest.main()
