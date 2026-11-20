"""Test the installed CLI boundary; --consumer also builds in a fresh Dune root."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


exe = Path(sys.argv[1]).resolve()
# For plan/emit provenance, the optional consumer fixture can live inside a
# checkout with a HEAD. It only observes Git; the fixture never initializes,
# stages, or commits anything.
with tempfile.TemporaryDirectory(dir=os.environ.get("STARTER_TEST_CHECKOUT_DIR")) as tmp:
    root = Path(tmp)

    def invoke(*args):
        return subprocess.run([exe, *args], cwd=root, capture_output=True,
                              env={**os.environ, "PATH": ""})

    for args in [(), ("render",), ("render", "--name", "../oops"),
                 ("render", "--name", "a" * 65), ("render", "--name", "a", tmp)]:
        bad = invoke(*args)
        assert bad.returncode == 2 and bad.stderr and not bad.stdout

    def render(name):
        result = invoke("render", "--name", name)
        result.check_returncode()
        assert not result.stderr
        return result.stdout

    output = render("led-demo")
    assert output == render("led-demo") and output != render("other")
    assert not list(root.iterdir())
    data = json.loads(output)
    assert (data["schema_version"], data["provider"], data["version"]) == (
        1, "hardcaml_asic", "0.1.0")
    paths = {f["path"] for f in data["files"]}
    assert len(paths) == len(data["files"])
    assert paths == {"README.md", "chip.ml", "dune-project", "dune", "rtl.ml", "test_chip.ml"}
    result = invoke("render-existing", "--name", "led-demo")
    result.check_returncode()
    nested = json.loads(result.stdout)["files"]
    assert all(f["path"].startswith("asic_led-demo/") for f in nested)
    assert not any(f["path"].endswith("dune-project") for f in nested)
    assert invoke("render-existing", "--name", "../escape").returncode == 2
    if "--consumer" in sys.argv[2:]:
        for f in data["files"]:
            (root / f["path"]).write_text(f["contents"])
        for command in [["build"], ["runtest"], ["exec", "./rtl.exe"]]:
            result = subprocess.run(["dune", command[0], "--root", ".", *command[1:]], cwd=root, check=True, stdout=subprocess.PIPE)
        assert b"module tt_um_led_demo" in result.stdout
        # Same project already has a chip library, a root Dune file, and sources.
        for f in nested:
            path = root / f["path"]
            path.parent.mkdir(exist_ok=True)
            with path.open("x") as out:
                out.write(f["contents"])
        target = "./asic_led-demo/project.exe"
        def command(*args):
            return subprocess.run(["dune", args[0], "--root", ".", *args[1:]], cwd=root, check=True, stdout=subprocess.PIPE).stdout
        command("build", target)
        command("runtest", "asic_led-demo")
        def query(*args):
            return json.loads(command("exec", target, "--", *args, "--json"))
        assert query("chip", "list")["default_chip"] == "led-demo"
        assert query("flow", "list")
        assert query("flow", "describe", "--flow", "standard")
        assert subprocess.run(["dune", "exec", "--root", ".", target, "--", "flow", "list", "--chip", "wrong"],
                              cwd=root, capture_output=True).returncode != 0
        if os.environ.get("STARTER_TEST_CHECKOUT_DIR"):
            os.environ["GIT_OPTIONAL_LOCKS"] = "0"
            selection = ["--flow", "standard", "--profile", "balanced", "--endpoint",
                         "synthesis", "--source-root", str(root), "--chip", "led-demo",
                         "--variant", "default"]
            plan = query("flow", "plan", *selection)
            receipt = query("flow", "emit", *selection, "--expected-plan-identity",
                            plan["plan_identity"], "--results-root", str(root / "results"))
            assert receipt["plan_identity"] == plan["plan_identity"]
            assert Path(receipt["output_dir"]).is_dir()
