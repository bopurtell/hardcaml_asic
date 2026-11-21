"""Compile an unrelated consumer against this checkout's public Dune install tree.

Run after `dune build @install`:
  python3 test/check_resolved_flow_api.py
Optionally compare every emitted byte with an emitter saved BEFORE the API change:
  python3 test/check_resolved_flow_api.py --baseline-emitter /path/to/old.exe
Both emitters read the same current source inputs and Git state. No Git mutations,
installation, PDK or flow tools are used. This is not installed-release acceptance.
"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


def run(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def checked(*args, **kwargs):
    result = run(*args, **kwargs)
    assert result.returncode == 0, result.stdout
    return result.stdout


def inventory(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-emitter", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    public_lib = repo / "_build/install/default/lib"
    assert (public_lib / "hardcaml_asic").is_dir(), "run dune build @install first"
    env = dict(os.environ)
    env["OCAMLPATH"] = str(public_lib) + (
        os.pathsep + env["OCAMLPATH"] if env.get("OCAMLPATH") else "")

    with tempfile.TemporaryDirectory(prefix="asic-resolved-api-") as temporary:
        root = Path(temporary)
        consumer = root / "consumer"
        consumer.mkdir()
        (consumer / "dune-project").write_text("(lang dune 3.17)\n")
        (consumer / "dune").write_text(
            "(executable (name main) (libraries core hardcaml hardcaml_asic))\n")
        # The existing emitter exercises both flow resolution and Bundle.render/write.
        # Copy it into an independent project; no in-tree test library is linked.
        positive = (repo / "examples/tt_bundle_example.ml").read_text()
        needle = "  let simulation_project =\n"
        inspection = """  let { Resolved_flow.clock_period_ns; clock_hz; target; settings; _ } =
    resolved
  in
  assert (Float.(clock_period_ns > 0.));
  assert (Float.(clock_hz = 1e9 /. clock_period_ns));
  assert (String.equal target.tiles "6x4");
  List.iter settings ~f:(fun setting ->
    ignore (Resolved_flow.Setting.sexp_of_t setting : Sexp.t);
    ignore (Yojson.Safe.to_string setting.value : string));
  List.iter (resolved.input_delays_ns @ resolved.output_delays_ns) ~f:(fun delay ->
    assert (Float.(delay.minimum <= delay.maximum)));
  ignore (Resolved_flow.resolve resolved.elaborated_project |> Or_error.ok_exn : Resolved_flow.t);
"""
        assert positive.count(needle) == 1
        positive = positive.replace(needle, inspection + needle)
        source = consumer / "main.ml"
        source.write_text(positive)

        def compile_consumer():
            return run("dune", "build", "-j", "20", "--root", str(consumer), "main.exe",
                       cwd=consumer, env=env)

        result = compile_consumer()
        assert result.returncode == 0, result.stdout
        executable = consumer / "_build/default/main.exe"
        for kind in ("observable", "memory"):
            output = root / (kind + "-new")
            checked(str(executable), kind, str(output), str(repo), env=env)
            if args.baseline_emitter:
                baseline = root / (kind + "-old")
                checked(str(args.baseline_emitter.resolve()), kind, str(baseline),
                        str(repo), env=env)
                old, new = inventory(baseline), inventory(output)
                differences = sorted(path for path in old.keys() | new.keys()
                                     if old.get(path) != new.get(path))
                assert not differences, (kind, "before/after bundle bytes differ", differences)
                digest = hashlib.sha256(new["manifest.json"]).hexdigest()
                print(f"{kind}: {len(new)} files byte-identical; manifest SHA-256 {digest}")
        print("external consumer: resolution, field reads, patterns, JSON and bundles pass")

        negatives = {
            "construct": ("t", """
let forge (r : Resolved_flow.t) : Resolved_flow.t =
  { elaborated_project = r.elaborated_project; target = r.target; clock = r.clock;
    clock_period_ns = 0.; clock_hz = r.clock_hz;
    input_delays_ns = r.input_delays_ns; output_delays_ns = r.output_delays_ns;
    settings = r.settings; methodology = r.methodology;
    effective_parameters = r.effective_parameters }
"""),
            "update_clock": ("t", """
let forge (r : Resolved_flow.t) = { r with clock_period_ns = 0. }
"""),
            "replace_target": ("t", """
let forge (r : Resolved_flow.t) =
  { r with target = { r.target with views = [] } }
"""),
            "replace_settings": ("t", """
let forge (r : Resolved_flow.t) = { r with settings = [] }
"""),
            "construct_setting": ("Setting.t", """
let forge : Resolved_flow.Setting.t =
  { key = "CLOCK_PERIOD"; value = `Float 0.; owner = Clock }
"""),
            "update_setting_json": ("Setting.t", """
let forge (s : Resolved_flow.Setting.t) = { s with value = `Float 0. }
"""),
            "construct_delay": ("Delay_ns.t", """
let forge : Resolved_flow.Delay_ns.t =
  { port = "missing"; minimum = 2.; maximum = 1. }
"""),
            "update_delay": ("Delay_ns.t", """
let forge (d : Resolved_flow.Delay_ns.t) = { d with maximum = -1. }
"""),
            "forge_effective": ("Effective_parameter.t", """
let forge (e : Resolved_flow.Effective_parameter.t) = { e with overridden = [] }
"""),
            "forge_contribution": ("Effective_parameter.contribution", """
let forge (c : Resolved_flow.Effective_parameter.contribution) =
  { c with owner = Resolved_flow.Setting.Owner.Default }
"""),
        }
        for name, (type_name, snippet) in negatives.items():
            source.write_text("open Hardcaml_asic\n" + snippet)
            result = compile_consumer()
            diagnostic = re.sub(r"\s+", " ", result.stdout)
            assert result.returncode != 0, f"{name}: forged value compiled"
            assert "Cannot create values of the private type" in diagnostic, diagnostic
            assert "Resolved_flow." + type_name in diagnostic, diagnostic
            print(f"{name}: rejected for private Resolved_flow.{type_name}")


if __name__ == "__main__":
    main()
