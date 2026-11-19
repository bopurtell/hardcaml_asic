"""Bounded provider checks: synthetic collateral, no network or real provisioning."""

import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "flow"))
from hardcaml_asic_flow import probe

SCRIPT = REPO / "flow/hardcaml_asic_flow/data/toolchain.sh"
MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"


def snapshot(root):
    return {str(p.relative_to(root)): (p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in [root, *root.rglob("*")]}


class ToolchainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider space's ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / "_flow_tools"
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.log = self.work / "mutations"
        for tool in ("bash", "dirname", "sed", "head", "timeout", "stat", "id"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.executable("git", '''case "$*" in
"rev-parse --git-dir") printf '%s\\n' "$GIT_DIR" ;;
"rev-parse HEAD") printf '%040d\\n' 0 ;;
"status --porcelain") test "$GIT_OPTIONAL_LOCKS" = 0 ;;
*) printf 'git %s\\n' "$*" >> "$MUTATIONS"; exit 99 ;;
esac
''')
        for tool in ("docker", "podman", "nix-shell", "nix-store", "curl", "wget", "pip", "mkdir", "rm"):
            self.executable(tool, f'printf "{tool} %s\\n" "$*" >> "$MUTATIONS"; exit 99\n')
        # Nix availability is historical/nonfatal, and its only allowed query is version.
        self.executable("nix-store", 'test "$*" = --version || exit 99; printf "nix fixture\\n"\n')
        lock = (REPO / "toolchain.lock").read_text()
        values = dict(line.split("=", 1) for line in lock.splitlines() if line and not line.startswith("#"))
        self.pdk = values["pdk"]
        self.ll = values["librelane_version"]
        for key in ("support_tools_revision", "pdk_revision"):
            lock = lock.replace(values[key], "0" * 40)
        lock = lock.replace("python=" + values["python"], "python=" + MINOR)
        lock = lock.replace("runtime_host_python_version=" + values["python"], "runtime_host_python_version=" + MINOR)
        self.lock = self.work / "fixture.lock"
        self.lock.write_text(lock)
        self.env = dict(os.environ, PATH=str(self.bin), TOOLCHAIN=str(self.root), MUTATIONS=str(self.log))
        # A relocated installed-package fixture. The orchestration interpreter is
        # the running test Python, regardless of the target runtime's version.
        self.package = self.work / "prefix/flow/hardcaml_asic_flow"
        shutil.copytree(SCRIPT.parent.parent, self.package, ignore=shutil.ignore_patterns("__pycache__"))
        self.script = self.package / "data/toolchain.sh"
        self.helper_log = self.work / "helper-calls"
        self.env.update(HELPER_LOG=str(self.helper_log), HELPER_STATUS="3")
        # copytree preserves Dune's read-only mode; only this disposable stub changes.
        helper = self.package / "managed_python.py"
        helper.chmod(helper.stat().st_mode | 0o200)
        (self.package / "managed_python.py").write_text('''import json, os, sys
class Error(Exception):
    def __init__(self, message, status=2):
        super().__init__(message)
        self.status = status
def describe():
    return {"version": os.environ["FIXTURE_MINOR"] + ".17"}
def main(argv):
    with open(os.environ["HELPER_LOG"], "a") as out:
        out.write(json.dumps(argv) + "\\n")
    status = int(os.environ["HELPER_STATUS"])
    if status:
        print(os.environ.get("HELPER_ERROR", "fixture managed receipt missing"), file=sys.stderr)
        return status
    print(os.environ.get("HELPER_JSON", json.dumps({"interpreter": os.environ.get("HELPER_BASE")})))
    return 0
''')
        self.env["FIXTURE_MINOR"] = MINOR

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)
        return path

    def venv(self, name):
        target = self.root / name
        subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(target)], check=True)
        site = target / "lib" / ("python" + MINOR) / "site-packages"
        return site

    def ready(self):
        for part in ("tt/.git", "pdk/.git", "pdk/" + self.pdk,
                     "tt/tech/" + self.pdk + "/def", "tt/precheck"):
            (self.root / part).mkdir(parents=True, exist_ok=True)
        (self.root / "tt/tech" / self.pdk / "def/tt_block_6x4_pgvdd.def").touch()
        self.requirements = self.root / "tt/precheck/requirements.txt"
        self.requirements.write_text("klayout==0.28.0\n# exact pins\n")
        self.flow_site = self.venv(".venv")
        self.pre_site = self.venv(".venv-precheck")
        for site, name, version in ((self.flow_site, "librelane", self.ll), (self.pre_site, "klayout", "0.28.0")):
            (site / (name + ".py")).write_text("# synthetic import\n")
            metadata = site / (name + "-" + version + ".dist-info")
            metadata.mkdir()
            (metadata / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
        (self.flow_site / "tkinter.py").write_text("class Tcl:\n    def eval(self, expression): return '42'\n")
        # Select the scratch modules even if the host ships stdlib tkinter.
        (self.flow_site / "fixture_tcl.pth").write_text(
            f"import sys; sys.path.insert(0, {str(self.flow_site)!r})\n")
        (self.flow_site / "_tkinter.py").write_text("# synthetic system extension\n")
        self.write_env()

    def write_env(self):
        values = dict(ASIC_TOOLCHAIN=self.root, TT=self.root / "tt", PDK=self.root / "pdk",
                      PDK_ROOT=self.root / "pdk", FLOW_PY=self.root / ".venv/bin/python",
                      PRECHECK_PY=self.root / ".venv-precheck/bin/python")
        # Previous generator's format remains valid for ordinary paths/apostrophes.
        (self.root / "toolchain-env.sh").write_text("\n".join(
            f': "${{{key}:={value}}}"' for key, value in values.items()) +
            "\nexport " + " ".join(values) + "\n")

    def run_check(self, *args, check=True, no_container=True):
        before = snapshot(self.root) if self.root.exists() else None
        completed = subprocess.run(["/bin/bash", str(self.script), "--lock-file", str(self.lock),
                                    "--host-python", sys.executable,
                                    *(["--no-container"] if no_container else []),
                                    *(["--check"] if check else []), *args],
                                   env=self.env, text=True, capture_output=True, timeout=30)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")
        if check:
            self.assertEqual(snapshot(self.root) if self.root.exists() else None, before)
        return completed.returncode, completed.stdout + completed.stderr

    def test_ready_without_named_base_and_read_only(self):
        self.ready()
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertFalse(list(self.root.rglob("*.pyc")))
        before = snapshot(self.root)
        code, output = self.run_check(check=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.helper_log.exists())

    def test_readiness_failures(self):
        self.ready()
        cases = [(self.flow_site / "tkinter.py", "raise ImportError('missing Tcl')", "missing Tcl"),
                 (self.requirements, "klayout==9.9\n", "requested 9.9"),
                 (self.requirements, "klayout>=0.28\n", "unsupported requirement"),
                 (self.pre_site / "klayout.py", "raise ImportError('broken klayout')", "broken klayout"),
                 (self.root / "toolchain-env.sh", "touch SHOULD_NOT_RUN\n", "stale environment")]
        for path, text, expected in cases:
            with self.subTest(expected=expected):
                old = path.read_text()
                path.write_text(text)
                code, output = self.run_check()
                self.assertEqual(code, 2, output)
                self.assertIn(expected, output)
                path.write_text(old)
        self.requirements.unlink()
        code, output = self.run_check()
        self.assertEqual(code, 2, output)
        self.assertIn("requirements not found", output)

    def test_absent_root_missing_and_wrong_base_stop_before_mutators(self):
        for check in (True, False):
            for args in (("--python", "/missing/python"),):
                code, output = self.run_check(*args, check=check)
                self.assertEqual(code, 2, output)
                self.assertFalse(self.root.exists())
                self.assertFalse(self.helper_log.exists())
        self.wrong_minor()
        for check in (True, False):
            code, output = self.run_check("--python", sys.executable, check=check)
            self.assertEqual(code, 2, output)
            self.assertIn("requested 0.1", output)
            self.assertFalse(self.root.exists())

    def test_existing_wrong_python_never_recreated(self):
        self.ready()
        self.wrong_minor()
        for check in (True, False):
            before = snapshot(self.root)
            code, output = self.run_check(check=check)
            self.assertEqual(code, 2, output)
            self.assertIn("explicitly repair", output)
            self.assertEqual(snapshot(self.root), before)

    def test_base_selection_matches_actual_minor_and_explicit_wins(self):
        self.ready()
        base = self.root / ".venv/bin/python"
        code, output = self.run_check("--python", str(base))
        self.assertEqual(code, 0, output)
        (self.bin / ("python" + MINOR)).symlink_to(base)
        code, output = self.run_check("--python", "/absent/explicit")
        self.assertEqual(code, 2, output)
        self.assertIn("/absent/explicit", output)
        # A host interpreter on PATH is never the managed default.
        shutil.rmtree(self.root / ".venv-precheck")
        (self.bin / ("python" + MINOR)).unlink()
        self.executable("python3", f'exec {shlex.quote(str(base))} "$@"\n')
        code, output = self.run_check()
        self.assertEqual(code, 2, output)
        self.assertIn("managed runtime not installed", output)
        self.assertEqual(json.loads(self.helper_log.read_text()), ["--root", str(self.root), "--check"])
        self.wrong_minor()
        code, output = self.run_check()
        self.assertEqual(code, 2, output)
        self.assertIn("explicitly repair", output)

    def test_managed_check_without_root_is_read_only(self):
        code, output = self.run_check()
        self.assertEqual(code, 2, output)
        self.assertIn("managed runtime not installed", output)
        self.assertNotIn("base readiness: Python", output)
        self.assertFalse(self.root.exists())
        self.assertEqual(json.loads(self.helper_log.read_text()), ["--root", str(self.root), "--check"])

    def wrong_minor(self):
        self.lock.write_text(self.lock.read_text().replace("python=" + MINOR, "python=0.1")
                             .replace("runtime_host_python_version=" + MINOR,
                                      "runtime_host_python_version=0.1"))

    def test_managed_acquisition_and_offline_forwarding(self):
        for offline in (False, True):
            self.env["HELPER_STATUS"] = "4"
            code, output = self.run_check(*(["--offline"] if offline else []), check=False)
            self.assertEqual(code, 4, output)
            args = json.loads(self.helper_log.read_text().splitlines()[-1])
            self.assertEqual(args, ["--root", str(self.root), *(["--offline"] if offline else [])])
            self.assertFalse(self.root.exists())

    def test_managed_json_path_with_spaces_used_after_offline_cache_acquire(self):
        self.ready()
        shutil.rmtree(self.root / ".venv-precheck")
        self.env.update(HELPER_STATUS="0", HELPER_BASE=str(self.root / ".venv/bin/python"))
        before = snapshot(self.root)
        code, output = self.run_check("--offline", check=False)
        self.assertEqual(code, 4, output)  # Package policy still disallows offline pip.
        self.assertIn("no precheck environment", output)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(json.loads(self.helper_log.read_text()), ["--root", str(self.root), "--offline"])

    def test_managed_errors_preserve_public_categories(self):
        for status, expected in ((2, 2), (3, 5), (4, 4), (5, 7), (6, 2), (124, 7)):
            with self.subTest(status=status):
                self.env.update(HELPER_STATUS=str(status), HELPER_ERROR="corrupt managed/missing asset/deadline fixture")
                code, output = self.run_check(check=False)
                self.assertEqual(code, expected, output)
                self.assertIn(self.env["HELPER_ERROR"], output)
                self.assertFalse(self.root.exists())

    def test_invalid_helper_json_never_becomes_a_base_or_host_fallback(self):
        self.env["HELPER_STATUS"] = "0"
        for payload in ("not JSON", "{}", '{"interpreter": 3}', '{"interpreter": "relative/python"}'):
            with self.subTest(payload=payload):
                self.env["HELPER_JSON"] = payload
                code, output = self.run_check(check=False)
                self.assertEqual(code, 7, output)
                self.assertIn("invalid managed Python helper JSON", output)
                self.assertFalse(self.root.exists())

    def test_known_broken_environment_blocks_acquisition(self):
        self.ready()
        shutil.rmtree(self.root / ".venv")
        (self.pre_site / "klayout.py").write_text("raise ImportError('known broken klayout')\n")
        code, output = self.run_check(check=False)
        self.assertEqual(code, 2, output)
        self.assertIn("known broken klayout", output)
        self.assertFalse(self.helper_log.exists())

    def test_python_children_sanitized_without_changing_caller(self):
        self.ready()
        (self.flow_site / "tkinter.py").write_text(
            "import os\nassert not any(k in os.environ for k in "
            "('PYTHONHOME','PYTHONPATH','TCL_LIBRARY','TK_LIBRARY','TCLLIBPATH','LD_LIBRARY_PATH'))\n"
            "class Tcl:\n    def eval(self, expression): return '42'\n")
        self.env.update({k: "/bad inherited path" for k in
                         ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH", "LD_LIBRARY_PATH")})
        before = self.env.copy()
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.env, before)
        self.assertFalse(self.helper_log.exists())

    def test_engine_prevalidation_before_managed_acquisition(self):
        self.executable("docker", "exit 99\n")
        code, output = self.run_check(check=False, no_container=False)
        self.assertEqual(code, 2, output)
        self.assertIn("daemon is not reachable", output)
        self.assertFalse(self.helper_log.exists())
        self.assertFalse(self.root.exists())

    def test_container_check_shared_metadata_policy_no_pull_and_skip(self):
        self.ready()
        calls = self.work / "container-probes"
        self.env.update(CONTAINER_CALLS=str(calls), CONTAINER_LL=self.ll,
                        CONTAINER_PY="3.13.13", CONTAINER_REQ=">=3.10")
        self.executable("docker", r'''
printf '%s\n' "$1 $2" >> "$CONTAINER_CALLS"
case "$1 $2" in
    "info ") exit 0 ;;
    "info --format") printf '{"ServerVersion":"fixture","SecurityOptions":[]}\n' ;;
    "image inspect") printf '[{"Id":"sha256:1111111111111111111111111111111111111111111111111111111111111111","Os":"linux","Architecture":"amd64","RepoDigests":[]}]\n' ;;
    "run --rm") printf '{"python_version":"%s","librelane_version":"%s","requires_python":"%s","metadata_error":null}\n' "$CONTAINER_PY" "$CONTAINER_LL" "$CONTAINER_REQ" ;;
    "rm -f") exit 0 ;;
    *) printf 'unexpected Docker %s\n' "$*" >> "$MUTATIONS"; exit 99 ;;
esac
''')
        code, output = self.run_check(no_container=False)
        self.assertEqual(code, 0, output)
        self.assertIn("container runtime metadata matches", output)
        self.assertIn("run --rm", calls.read_text())
        for key, value in (("CONTAINER_LL", "9.9.9"), ("CONTAINER_PY", "3.15.1"),
                           ("CONTAINER_REQ", ">=3.11")):
            old = self.env[key]
            self.env[key] = value
            code, output = self.run_check(no_container=False)
            self.assertEqual(code, 2, output)
            self.assertIn("container runtime", output)
            self.env[key] = old
        calls.unlink()
        code, output = self.run_check(no_container=True)
        self.assertEqual(code, 0, output)
        self.assertFalse(calls.exists())

    def test_unknown_policy_stops_before_probes_or_acquisition(self):
        self.lock.write_text(self.lock.read_text().replace("runtime_policy_schema_version=1", "runtime_policy_schema_version=99"))
        code, output = self.run_check(no_container=False)
        self.assertEqual(code, 3, output)
        self.assertIn("runtime policy", output)
        self.assertFalse(self.helper_log.exists())

    def test_existing_requirements_validated_before_new_base(self):
        self.ready()
        shutil.rmtree(self.root / ".venv-precheck")
        self.requirements.write_text("klayout>=0.28\n")
        code, output = self.run_check(check=False)
        self.assertEqual(code, 2, output)
        self.assertIn("unsupported requirement", output)
        self.assertFalse(self.helper_log.exists())

    def mocked_python_installs(self):
        self.ready()
        shutil.rmtree(self.root / ".venv")
        shutil.rmtree(self.root / ".venv-precheck")
        log = self.work / "python-processes"
        self.env["PYTHON_PROCESS_LOG"] = str(log)
        self.env["FIXTURE_LL"] = self.ll
        base = self.executable("managed base python", r'''
[[ -z ${PYTHONHOME+x}${PYTHONPATH+x}${TCL_LIBRARY+x}${TK_LIBRARY+x}${TCLLIBPATH+x}${LD_LIBRARY_PATH+x} ]] || exit 91
[[ ${PYTHONDONTWRITEBYTECODE:-} == 1 ]] || exit 92
[[ $1 == -I && $2 == -B ]] || exit 93
printf '%s\n' "$*" >> "$PYTHON_PROCESS_LOG"
case "$*" in
    "-I -B -m venv "*)
        target=${@: -1}
        /bin/mkdir -p "$target/bin"
        /bin/cp "$0" "$target/bin/python" ;;
    "-I -B -m pip "*)
        [[ $5 == --cache-dir && $6 == "$TOOLCHAIN/cache/pip" ]] || exit 94 ;;
    *probe.py*) exit 0 ;;
    *importlib.metadata*)
        if [[ ${@: -1} == librelane ]]; then printf '%s\n' "$FIXTURE_LL"; else printf '0.28.0\n'; fi ;;
    *) exit 95 ;;
esac
''')
        self.env.update({k: "inherited bad path" for k in
                         ("PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH", "LD_LIBRARY_PATH")})
        return base, log

    def test_managed_default_creates_venvs_with_isolated_python_and_local_pip_cache(self):
        base, log = self.mocked_python_installs()
        self.env.update(HELPER_STATUS="0", HELPER_BASE=str(base))
        code, output = self.run_check(check=False)
        self.assertEqual(code, 0, output)
        calls = log.read_text().splitlines()
        self.assertEqual(sum("-m venv " in call for call in calls), 2)
        self.assertEqual(sum("-m pip " in call for call in calls), 4)
        self.assertTrue(all(call.startswith("-I -B ") for call in calls))
        self.assertEqual(json.loads(self.helper_log.read_text()), ["--root", str(self.root)])

    def test_explicit_external_creates_venvs_without_managed_fallback(self):
        base, log = self.mocked_python_installs()
        code, output = self.run_check("--python", str(base), check=False)
        self.assertEqual(code, 0, output)
        self.assertFalse(self.helper_log.exists())
        self.assertEqual(sum("-m venv " in call for call in log.read_text().splitlines()), 2)

    def test_bad_external_with_ready_existing_environments_still_fails_without_writes(self):
        self.ready()
        before = snapshot(self.root)
        code, output = self.run_check("--python", "/missing explicit/python", check=False)
        self.assertEqual(code, 2, output)
        self.assertIn("/missing explicit/python", output)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.helper_log.exists())

    def test_missing_metadata_is_not_ready(self):
        self.ready()
        for metadata in self.pre_site.glob("*.dist-info"):
            shutil.rmtree(metadata)
        code, output = self.run_check()
        self.assertEqual(code, 2, output)
        self.assertIn("No package metadata", output)

    def test_explicit_base_tcl_failure_blocks_full_provision(self):
        self.ready()
        (self.flow_site / "tkinter.py").write_text("raise ImportError('system Tcl absent')\n")
        code, output = self.run_check("--python", str(self.root / ".venv/bin/python"), check=False)
        self.assertEqual(code, 2, output)
        self.assertIn("pip cannot", output)

    def test_selected_base_needs_venv_and_ensurepip_before_mutators(self):
        self.ready()
        for module in ("venv", "ensurepip"):
            with self.subTest(module=module):
                (self.flow_site / "host_probe_fixture.pth").write_text("import host_probe_fixture\n")
                (self.flow_site / "host_probe_fixture.py").write_text(
                    "import builtins\noriginal = builtins.__import__\n"
                    "def missing(name, *args, **kwargs):\n"
                    f"    if name == {module!r}: raise ImportError('host lacks {module}')\n"
                    "    return original(name, *args, **kwargs)\n"
                    "builtins.__import__ = missing\n")
                code, output = self.run_check("--python", str(self.root / ".venv/bin/python"), check=False)
                self.assertEqual(code, 2, output)
                self.assertIn("host lacks " + module, output)

    def test_env_generator_shell_roundtrip(self):
        # Extract definitions only; no provisioning body is run.
        source = SCRIPT.read_text()
        functions = source[source.index("env_assignments() {"):source.index("write_env_file() {")]
        root = str(self.work / "root space's $literal `text`")
        shell = (f'TOOLCHAIN={shlex.quote(root)}; tt_dir="$TOOLCHAIN/tt"; pdk_dir="$TOOLCHAIN/pdk"; '
                 'venv_dir="$TOOLCHAIN/.venv"; precheck_venv_dir="$TOOLCHAIN/.venv-precheck";\n' + functions +
                 '\nenv_assignments')
        generated = subprocess.check_output(["/bin/bash", "-c", shell], text=True)
        result = subprocess.check_output(["/bin/bash", "-c", generated + '\nprintf "%s\\n" "$ASIC_TOOLCHAIN" "$FLOW_PY"'],
                                         env={"PATH": str(self.bin)}, text=True)
        self.assertEqual(result.splitlines(), [root, root + "/.venv/bin/python"])

    def test_actual_host_tcl_probe_is_headless_and_reports_blocked(self):
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        completed = subprocess.run([sys.executable, "-I", "-B", str(Path(probe.__file__)), "tcl", MINOR],
                                   env=env, text=True, capture_output=True, timeout=20)
        if completed.returncode:
            self.assertEqual(completed.returncode, 2)
            self.assertIn("pip cannot", completed.stderr)
        else:
            self.assertEqual(completed.stderr, "")

    def test_bootstrap_forwards_base_and_rejects_missing_value_before_layers(self):
        bootstrap = self.work / "bootstrap.sh"
        bootstrap.write_bytes((REPO / "bootstrap.sh").read_bytes())
        scripts = self.work / "scripts"
        scripts.mkdir()
        for name in ("ocaml-deps.sh", "toolchain.sh"):
            stub = scripts / name
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$LAYER_LOG"\n')
            stub.chmod(0o755)
        log = self.work / "layers"
        env = dict(self.env, LAYER_LOG=str(log))
        result = subprocess.run(["/bin/bash", str(bootstrap), "--check", "--no-container", "--python", "/base space's/python"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--python\n/base space's/python\n", log.read_text())
        log.unlink()
        result = subprocess.run(["/bin/bash", str(bootstrap), "--python", "--check"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
