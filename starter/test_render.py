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
    assert paths == {"dune-project"} | {"asic_led-demo/" + path for path in
                                        ["README.md", "chip.ml", "project.ml", "dune", "rtl.ml", "test_chip.ml"]}
    result = invoke("render-target", "--name", "led-demo")
    result.check_returncode()
    nested = json.loads(result.stdout)["files"]
    assert nested == [f for f in data["files"] if f["path"] != "dune-project"]
    assert result.stdout == invoke("render-existing", "--name", "led-demo").stdout
    assert all(f["path"].startswith("asic_led-demo/") for f in nested)
    assert not any(f["path"].endswith("dune-project") for f in nested)
    assert invoke("render-existing", "--name", "../escape").returncode == 2
    for bad_args in [
        ("render-ip", "--name", "adder", "--template", "missing"),
        ("render-ip", "--name", "adder"),
        ("render-ip", "--name", "../adder", "--template", "empty"),
        ("render-module", "--name", "two-words"),
        ("render-module", "--name", "../escape"),
        ("render-target", "--name", "b", "--library", "ip_sample"),
        ("render-target", "--name", "b", "--library", "ip_sample", "--mapping", "auto"),
        ("render-target", "--name", "b", "--library", "../ip_sample", "--mapping", "ui_in:uo_out:comb8"),
    ]:
        bad = invoke(*bad_args)
        assert bad.returncode == 2 and bad.stderr and not bad.stdout

    def recipe(*args):
        output = invoke(*args)
        output.check_returncode()
        assert not output.stderr and output.stdout == invoke(*args).stdout
        descriptor = json.loads(output.stdout)
        assert {k: v for k, v in descriptor.items() if k != "files"} == {
            k: v for k, v in data.items() if k != "files"}
        return descriptor["files"]

    ips = {}
    for template in ["empty", "sample"]:
        entries = recipe("render-ip", "--name", template, "--template", template)
        expected = ["block.ml", "dune", "test/dune", "test/test_block.ml"]
        if template == "sample":
            expected += ["chip-interface.json"]
            declaration, = (json.loads(f["contents"]) for f in entries if f["path"].endswith(".json"))
            assert declaration == dict(schema_version=1, library="ip_sample", entrypoint="Block.create",
                                       mapping="ui_in:uo_out:comb8", clock="unused", reset="unused")
        assert sorted(f["path"] for f in entries) == sorted(f"ip/{template}/{p}" for p in expected)
        ips[template] = entries
    module = recipe("render-module", "--name", "new_block")
    assert [f["path"] for f in module] == ["new_block.ml"]
    assert not list(root.iterdir())
    if "--consumer" in sys.argv[2:]:
        def materialize(files):
            for f in files:
                p = root / f["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("x") as out:
                    out.write(f["contents"])
        materialize(data["files"])
        for command in [["build"], ["runtest"], ["exec", "./asic_led-demo/rtl.exe"]]:
            result = subprocess.run(["dune", command[0], "--root", ".", *command[1:]], cwd=root, check=True, stdout=subprocess.PIPE)
        assert b"module tt_um_led_hdemo" in result.stdout
        for template, entries in ips.items():
            dest = root / "ip" / template
            for base, files in [(root, entries), (dest, module)]:
                for f in files:
                    path = base / f["path"]
                    path.parent.mkdir(exist_ok=True, parents=True)
                    with path.open("x") as out:
                        out.write(f["contents"])
            # Compile a downstream reference to the added module as well.
            with (dest / "test/test_block.ml").open("a") as out:
                out.write(f"\nmodule Added = Ip_{template}.New_block\n")
            subprocess.run(["dune", "build", "--root", ".", f"ip/{template}/ip_{template}.cmxa"],
                           cwd=root, check=True)
            subprocess.run(["dune", "runtest", "--root", ".", f"ip/{template}"], cwd=root, check=True)
        # The escaped library identifiers are distinct even for "-" vs "_".
        b = recipe("render-target", "--name", "led_demo", "--library", "ip_sample",
                   "--mapping", "ui_in:uo_out:comb8")
        first = root / "asic_led-demo/chip.ml"
        with first.open("a") as out:
            out.write("\n(* local edit survives append *)\n")
        before = {p: p.read_bytes() for folder in ["asic_led-demo", "ip"]
                  for p in (root / folder).rglob("*") if p.is_file()}
        materialize(b)
        # Independent truth-table oracle through B's chip wrapper.
        test_b = root / "asic_led_demo/test_chip.ml"
        with test_b.open("a") as out:
            out.write("""
let () =
  let chip = Chip_target.elaborate Chip.chip_target ~mode:Simulation |> Or_error.ok_exn in
  let sim = Cyclesim.create (Elaborated_project.circuit chip) in
  for n = 0 to 255 do
    Cyclesim.in_port sim "ui_in" := Bits.of_int_trunc ~width:8 n;
    Cyclesim.cycle sim;
    assert (Bits.equal !(Cyclesim.out_port ~clock_edge:Before sim "uo_out")
              (Bits.of_int_trunc ~width:8 (n lxor 255)))
  done
""")
        target = "./asic_led-demo/project.exe"
        def command(*args):
            return subprocess.run(["dune", args[0], "--root", ".", *args[1:]], cwd=root, check=True, stdout=subprocess.PIPE).stdout
        command("build", target, "./asic_led_demo/project.exe")
        command("runtest")
        command("exec", "./asic_led_demo/rtl.exe")
        assert all(p.read_bytes() == content for p, content in before.items())
        def query(*args):
            return json.loads(command("exec", target, "--", *args, "--json"))
        assert query("chip", "list")["default_chip"] == "led-demo"
        assert [x["id"] for x in query("chip", "list")["chips"]] == ["led-demo"]
        catalog = {"schema_version": 1, "default_target": "led-demo", "targets": [
            {"id": name, "executable": f"asic_{name}/project.exe"} for name in ["led-demo", "led_demo"]]}
        (root / "hardcaml-asic-targets.json").write_text(json.dumps(catalog))
        discovery = query("chip", "list")
        assert discovery["default_chip"] == "led-demo"
        assert [(x["id"], x["default_variant"]) for x in discovery["chips"]] == [
            ("led-demo", "default"), ("led_demo", "default")]
        # Give B an observable flow label to prove it answered the dispatch.
        b_cli = root / "asic_led_demo/project.ml"
        b_cli.write_text(b_cli.read_text().replace('~label:"Standard"', '~label:"B only"'))
        command("build", "asic_led_demo/project.exe")
        second = ["--chip", "led_demo", "--variant", "default"]
        assert "B only" in json.dumps(json.loads(command(
            "exec", target, "--", "flow", "list", "--json", *second)))
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
            plan_b = query("flow", "plan", *selection[:-4], *second)
            assert plan_b["plan_identity"] != plan["plan_identity"]
            receipt = query("flow", "emit", *selection, "--expected-plan-identity",
                            plan["plan_identity"], "--results-root", str(root / "results"))
            assert receipt["plan_identity"] == plan["plan_identity"]
            assert Path(receipt["output_dir"]).is_dir()
