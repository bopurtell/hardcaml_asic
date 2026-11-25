"""External Project consumer and optional provider suite, with disposable Git fixtures.

Run: python3 test/check_project_api.py --provider-checks
Or, after dune build @install: python3 test/check_project_api.py
No installation, dependency downloads, provisioning or physical/synthesis EDA.
Provider checks retain normal PATH tool selection, including available RTL simulators.
Git mutations are confined to temporary fixtures; all commits use Bundle test identity.
Includes exact publication reuse/collisions, failed-write cleanup, two independent
processes racing after rendering, and coarse flow/archive root-link compatibility.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import resource
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

from check_resolved_flow_api import inventory


def checked(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def isolated_environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Bundle test", GIT_COMMITTER_NAME="Bundle test",
               GIT_AUTHOR_EMAIL="bundle@test.invalid",
               GIT_COMMITTER_EMAIL="bundle@test.invalid",
               GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="commit.gpgSign",
               GIT_CONFIG_VALUE_0="false", GIT_CONFIG_KEY_1="core.hooksPath",
               GIT_CONFIG_VALUE_1=os.devnull, GIT_CONFIG_KEY_2="init.templateDir",
               GIT_CONFIG_VALUE_2="")
    return env


def snapshot(root):
    """Preserve files, directories and link text without following any links."""
    result = {}
    def walk(path):
        stat = path.lstat()
        key = str(path.relative_to(root))
        if path.is_symlink():
            result[key] = ("link", os.readlink(path), stat.st_ino, stat.st_mtime_ns)
        elif path.is_dir():
            result[key] = ("directory", stat.st_ino)
            for child in path.iterdir():
                walk(child)
        else:
            result[key] = ("file", path.read_bytes(), stat.st_ino, stat.st_mtime_ns)
    walk(root)
    return result


def publication_checks(executable, source, root, env, repo):
    def invoke(store, *, extra=(), success=True, **kwargs):
        result = subprocess.run([str(executable), "publish", str(source), str(store), *extra],
                                env=env, text=True, capture_output=True, **kwargs)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return json.loads(result.stdout) if success else result.stderr

    # Relative caller-owned root and spaces; public path is absolute, not realpath.
    store = root / "results with spaces" / "bundles"
    first = invoke(Path("results with spaces/bundles"), cwd=root)
    identity = first["identity"]
    destination = store / identity
    assert first == dict(identity=identity, directory=str(destination), reused=False)
    assert destination.is_symlink()
    assert os.readlink(destination).startswith(".objects/" + identity + ".")
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["identity"] == identity == destination.name
    before = snapshot(store)
    original_store = before
    repeated = invoke(store)
    assert repeated == dict(first, reused=True)
    assert snapshot(store) == before, "repeat publication modified store"
    assert len(list((store / ".objects").iterdir())) == 1

    # The exact plain-directory compatibility path must also be a bytewise no-op.
    direct_store = root / "direct"
    direct_store.mkdir()
    shutil.copytree(destination, direct_store / identity)
    before = snapshot(direct_store)
    assert invoke(direct_store)["reused"]
    assert snapshot(direct_store) == before

    # Every collision is preserved, including dangling/escaping links and extra dirs.
    for case in ("corrupt", "manifest", "missing", "extra", "emptydir", "file",
                 "empty", "escape", "dangling", "memberlink", "dirlink", "objectlink"):
        bad_store = root / ("collision " + case)
        bad_store.mkdir()
        bad = bad_store / identity
        if case == "file":
            bad.write_text("owner file")
        elif case == "empty":
            bad.mkdir()
        elif case in ("escape", "dangling"):
            bad.symlink_to(destination.resolve() if case == "escape" else root / "absent")
        elif case == "objectlink":
            objects = bad_store / ".objects"
            objects.mkdir()
            (objects / (identity + ".foreign")).symlink_to(destination.resolve())
            bad.symlink_to(".objects/" + identity + ".foreign")
        else:
            shutil.copytree(destination, bad)
            if case == "corrupt":
                (bad / "info.yaml").write_text("wrong bytes")
            elif case == "manifest":
                (bad / "manifest.json").write_text("{}\n")
            elif case == "missing":
                (bad / "info.yaml").unlink()
            elif case == "extra":
                (bad / "extra").write_text("extra")
            elif case == "emptydir":
                (bad / "extra empty").mkdir()
            elif case == "memberlink":
                (bad / "info.yaml").unlink()
                (bad / "info.yaml").symlink_to(destination.resolve() / "info.yaml")
            elif case == "dirlink":
                shutil.rmtree(bad / "src")
                (bad / "src").symlink_to(destination.resolve() / "src")
        before = snapshot(bad_store)
        diagnostic = invoke(bad_store, success=False)
        assert "bundle publication failed" in diagnostic, diagnostic
        assert snapshot(bad_store) == before, case

    for case in ("storefile", "parentfile", "parentlink", "objectsfile", "objectslink"):
        location = root / case
        if case in ("storefile", "parentfile"):
            location.write_text("owner bytes")
            store_arg = location if case == "storefile" else location / "bundles"
        elif case == "parentlink":
            location.symlink_to(store)
            store_arg = location / "nested"
        else:
            location.mkdir()
            if case == "objectsfile":
                (location / ".objects").write_text("owner object")
            else:
                (location / ".objects").symlink_to(store / ".objects")
            store_arg = location
        before = snapshot(location)
        diagnostic = invoke(store_arg, success=False)
        assert "not a plain directory" in diagnostic, diagnostic
        assert snapshot(location) == before
        assert snapshot(store) == original_store

    # Real write failure, even as root: enforce a small per-file resource limit.
    failed_store = root / "failed write"
    def small_files():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))
    diagnostic = invoke(failed_store, success=False, preexec_fn=small_files)
    assert "bundle publication failed" in diagnostic, diagnostic
    assert not os.path.lexists(failed_store / identity)
    assert list(failed_store.iterdir()) == [failed_store / ".objects"]
    assert not list((failed_store / ".objects").iterdir()), "unpublished stage leaked"

    # Two independent OCaml processes render first, then race through Bundle.publish.
    race_store = root / "process race" / "bundles"
    gate = root / "go"
    ready = [root / ("ready-" + str(i)) for i in range(2)]
    processes = [subprocess.Popen([str(executable), "publish", str(source), str(race_store),
                                  str(gate), str(marker)], env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for marker in ready]
    try:
        deadline = time.monotonic() + 30
        while not all(marker.exists() for marker in ready):
            assert all(p.poll() is None for p in processes), "publisher failed before race"
            assert time.monotonic() < deadline, "publisher gate timeout"
            time.sleep(0.01)
        gate.write_text("go")
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
            outcomes.append(json.loads(stdout))
        assert sorted(o["reused"] for o in outcomes) == [False, True]
        assert all(o["identity"] == identity for o in outcomes)
        assert inventory(race_store / identity) == inventory(destination)
        assert len(list((race_store / ".objects").iterdir())) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()

    # Reference coarse run entry canonicalizes the root before preflight. Rejecting
    # preflight exercises that entry without any external tool/PDK invocation.
    sys.path.insert(0, str(repo / "flow"))
    from hardcaml_asic_flow import archive, coarse_flow_oracle
    args = SimpleNamespace(bundle=destination, runs=root / "runs", stage="synthesis",
                           support_tools=root / "tools", pdk_root=root / "pdk",
                           python=Path(sys.executable), allow_python_mismatch=False, native=True)
    def preflight(bundle, *_args):
        assert bundle == destination.resolve() and not bundle.is_symlink()
        return {"ready": False, "issues": ["fixture stops before EDA"]}
    with mock.patch.object(coarse_flow_oracle, "preflight", side_effect=preflight):
        outcome = coarse_flow_oracle.run(args)
    record = coarse_flow_oracle.read_json(outcome.run_record)
    assert record["bundle"] == str(destination.resolve())
    kind, selected, value, files = archive.bundle_source(outcome.run_dir, record)
    assert kind == "original" and selected == destination.resolve()
    assert value == manifest
    assert {relative for _, relative in files} == set(inventory(destination)) - {"manifest.json"}
    tarball = root / "input-bundle.tar.gz"
    archive.write_input_bundle(tarball, selected / "manifest.json", files)
    archive.verify_input_bundle(tarball, record, archive.manifest_paths(manifest))
    assert invoke(store)["reused"]
    assert snapshot(store) == original_store
    print("publication: exact/no-op reuse, preserved collisions, failed-write cleanup, "
          "real-process race and coarse flow/archive root-link compatibility pass")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-checks", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="asic-project-api-") as temporary:
        root = Path(temporary)
        env = isolated_environment()
        if args.provider_checks:
            checked("dune", "build", "-j", "20", "@install", cwd=repo, env=env)
            checked("dune", "runtest", "-j", "20", "--force", cwd=repo, env=env)
            checked("python3", "test/check_resolved_flow_api.py", cwd=repo, env=env)
        public_lib = repo / "_build/install/default/lib"
        assert (public_lib / "hardcaml_asic").is_dir(), "run dune build @install first"
        env["OCAMLPATH"] = str(public_lib) + (
            os.pathsep + env["OCAMLPATH"] if env.get("OCAMLPATH") else "")
        source = root / "source with spaces"
        source.mkdir()
        for name in ("lib", "examples"):
            shutil.copytree(repo / name, source / name)
        shutil.copy2(repo / "dune-project", source / "dune-project")
        (source / "lib/source with spaces.ml").write_text("(* publication fixture *)\n")
        checked("git", "init", "-q", str(source), env=env)
        checked("git", "add", ".", cwd=source, env=env)
        checked("git", "commit", "-qm", "baseline", cwd=source, env=env)
        consumer = root / "consumer"
        consumer.mkdir()
        (consumer / "dune-project").write_text("(lang dune 3.17)\n")
        (consumer / "dune").write_text(
            "(executable (name main) (libraries core hardcaml hardcaml_asic))\n")
        # Preserve the in-tree low-level oracle. Only this disposable copy gains
        # declaration injection points; the design, policy and timing stay exact.
        oracle = (repo / "examples/tt_bundle_example.ml").read_text().split("let () =\n")[0]
        for old, new in [
            ("let project kind =", "let project ?source_inputs ?design_override ?target_override kind ="),
            ("  Chip_target.create\n", "  Chip_target.create ?source_inputs\n"),
            ("    ~design\n", "    ~design:(Option.value design_override ~default:design)\n"),
            ("    ~target:\n", "    ~target:(Option.value target_override ~default:\n"),
            ("{ harness = Tiny_tapeout", "{ Target.harness = Tiny_tapeout"),
            ("technology = Technology.ihp_sg13cmos5l\n      }",
             "technology = Technology.ihp_sg13cmos5l\n      })"),
        ]:
            assert oracle.count(old) == 1, old
            oracle = oracle.replace(old, new)
        (consumer / "oracle.ml").write_text(oracle)
        shutil.copy2(repo / "test/project_api_consumer.ml", consumer / "main.ml")
        checked("dune", "build", "-j", "20", "--root", str(consumer), "main.exe", cwd=consumer, env=env)
        outputs = root / "outputs"
        outputs.mkdir()
        checked(str(consumer / "_build/default/main.exe"), str(source), str(outputs), env=env)
        for kind in ("observable", "memory"):
            for simulation in ("true", "false"):
                suffix = kind + simulation
                assert inventory(outputs / ("high-" + suffix)) == inventory(outputs / ("manual-" + suffix))
                store = outputs / ("store-" + suffix)
                public = [p for p in store.iterdir() if p.name != ".objects"]
                assert len(public) == 1
                assert inventory(public[0]) == inventory(outputs / ("manual-" + suffix))
        print("external Project consumer: four complete on-disk bundles byte-identical")
        publication_checks(consumer / "_build/default/main.exe", source, root, env, repo)
        for label, snippet in {
            "construct": 'let forge : Bundle.Publication.t = { directory = "/tmp"; reused = true }',
            "update": 'let forge (p : Bundle.Publication.t) = { p with reused = true }',
        }.items():
            (consumer / "main.ml").write_text("open Hardcaml_asic\n" + snippet + "\n")
            result = subprocess.run(["dune", "build", "-j", "20", "--root", str(consumer), "main.exe"],
                                    cwd=consumer, env=env, text=True, capture_output=True)
            diagnostic = result.stdout + result.stderr
            assert result.returncode != 0, "forged publication compiled"
            assert "Cannot create values of the private type" in diagnostic, diagnostic
            assert "Bundle.Publication.t" in diagnostic, diagnostic
            print(f"Publication {label}: rejected for private Bundle.Publication.t")


if __name__ == "__main__":
    main()
