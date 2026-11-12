"""Pure interpreter-activation regression: no provider, build, install or EDA."""
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import qualify_sram_segmented as qualification


class InterpreterPathTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qualifier-venv-", dir="/tmp/opencode")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.venv = self.root / "venv with spaces"
        (self.venv / "bin").mkdir(parents=True)
        self.base = Path(sys.executable).resolve(strict=True)
        self.python = self.venv / "bin/python"
        self.python.symlink_to(self.base)
        (self.venv / "pyvenv.cfg").write_text(
            f"home = {self.base.parent}\ninclude-system-site-packages = false\n")
        # Minimal distribution metadata exercises the same importlib.metadata
        # lookup as provider readiness, without installing any package or pip.
        metadata = (self.venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
                    / "site-packages/qualifier_venv_fixture-1.2.3.dist-info")
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: qualifier-venv-fixture\nVersion: 1.2.3\n")

    def observe(self, executable):
        probe = (
            "import importlib.metadata as m,json,sys\n"
            "try: version=m.version('qualifier-venv-fixture')\n"
            "except m.PackageNotFoundError: version=None\n"
            "print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':version}))\n"
        )
        result = subprocess.run([str(executable), "-I", "-B", "-c", probe],
                                text=True, capture_output=True, timeout=10, check=True)
        return json.loads(result.stdout)

    def test_symlink_keeps_virtualenv_package_metadata(self):
        selected = qualification.interpreter_path(self.python)
        self.assertEqual(selected, self.python)
        self.assertTrue(selected.is_symlink())
        virtual = self.observe(selected)
        self.assertEqual(virtual["version"], "1.2.3")
        self.assertEqual(Path(virtual["prefix"]), self.venv)
        self.assertNotEqual(virtual["prefix"], virtual["base_prefix"])
        bare = self.observe(self.python.resolve(strict=True))
        self.assertIsNone(bare["version"])
        self.assertEqual(bare["prefix"], bare["base_prefix"])

    def test_relative_interpreter_keeps_activation_path(self):
        with contextlib.chdir(self.root):
            selected = qualification.interpreter_path(Path("venv with spaces/bin/python"))
        self.assertEqual(selected, self.python)
        self.assertEqual(self.observe(selected)["version"], "1.2.3")

    def test_missing_interpreter_rejected(self):
        with self.assertRaises(FileNotFoundError):
            qualification.interpreter_path(self.venv / "bin/missing")


if __name__ == "__main__":
    unittest.main()
