"""No-methodology byte regression against the pre-seam provider implementation.

Run after dune build -j20 @install. Builds only a disposable copy, sequentially
with -j20; reads Git objects in the live checkout without modifying its state.
Both emitters observe identical current source bytes and Git state. No EDA.

Two refs shape the baseline build. --environment-ref supplies lib/ and
dune-project: the last flat tree before the lib namespace reorganization, so
the flat Project/Resolved_flow splice still fits. --baseline-ref supplies that
splice. examples/ is always the current copy, because the bundle's source
inventory comes from the example's own source_inputs.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="0d04baeb5c6538412e3dfb173422b4aa5e174544")
    parser.add_argument("--environment-ref", default="0ed1b3dfdb709a85a56aae8fb48ff91e7edfb4c8")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="asic-methodology-legacy-") as temporary:
        baseline = Path(temporary)
        # The pre-reorganization provider (flat lib/ with nested flow_domain),
        # read from Git objects only.
        archive = subprocess.run(
            ["git", "archive", args.environment_ref, "lib", "dune-project"],
            cwd=repo, check=True, stdout=subprocess.PIPE).stdout
        subprocess.run(["tar", "-x", "-C", str(baseline)], input=archive, check=True)
        # The current example: its source_inputs walk the live lib/ recursively,
        # so an older example would change the inventory for unrelated reasons.
        shutil.copytree(repo / "examples", baseline / "examples")
        # The chip declaration was called Project at the frozen revision.
        # Expose the renamed entry point to today's unmodified specimen.
        (baseline / "lib/chip_target.ml").write_text("include Project\n")
        # Freeze the original Project/settings boundary, retaining the accumulated
        # pure domain slice and the rest of the provider as the build environment.
        for name in ("project.ml", "project.mli", "resolved_flow.ml", "resolved_flow.mli"):
            data = subprocess.check_output(
                ["git", "show", f"{args.baseline_ref}:lib/{name}"], cwd=repo)
            (baseline / "lib" / name).write_bytes(data)
        # The new plan API explicitly depends on the checked render/merge seam
        # being replaced above. Exclude only that additive module in the fixture
        # build; keep Bundle and its unchanged identity/rendering implementation.
        dune = baseline / "lib/dune"
        text = dune.read_text()
        assert text.count("(name hardcaml_asic)") == 1
        dune.write_text(text.replace("(name hardcaml_asic)",
            "(name hardcaml_asic)\n (modules (:standard \\ methodology_plan))"))
        # Older Timing.t has fewer fields; today's source is valid there, with
        # a redundant-record-update warning. Use the release warning policy.
        subprocess.run(["dune", "build", "--profile", "release", "-j20", "--root", str(baseline),
                        "examples/tt_bundle_example.exe"], cwd=baseline, check=True)
        subprocess.run(["python3", "-B", "test/check_resolved_flow_api.py",
                        "--baseline-emitter",
                        str(baseline / "_build/default/examples/tt_bundle_example.exe")],
                       cwd=repo, check=True)


if __name__ == "__main__":
    main()
