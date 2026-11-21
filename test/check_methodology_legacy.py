"""No-methodology byte regression against the pre-seam provider implementation.

Run after dune build -j20 @install. Builds only a disposable copy, sequentially
with -j20; reads Git objects in the live checkout without modifying its state.
Both emitters observe identical current source bytes and Git state. No EDA.

Two refs shape the pre-seam build. --environment-ref supplies lib/ and
dune-project: the last flat tree before the lib namespace reorganization, so
the flat Project/Resolved_flow splice still fits. --baseline-ref supplies that
splice. examples/ is always the current copy, because the bundle's source
inventory comes from the example's own source_inputs.

Bridge to the live build through the pre-starter provider: its manifest
already records PT.5 resources and the newer support-tools pin. Require
exact bytes against that frozen provider, including the manifest and identity.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from check_resolved_flow_api import inventory


def compare_eras(old, new):
    a, b = inventory(old), inventory(new)
    legacy, modern = (json.loads(x.pop("manifest.json")) for x in (a, b))
    assert a == b, "non-manifest members changed across format eras"
    # These are the only intentional differences between the two pinned
    # providers. The live emitter is compared byte-for-byte separately below.
    assert legacy.pop("identity") != modern.pop("identity")
    assert modern.pop("resource_snapshot")["schema_version"] == 1
    for manifest, pin in ((legacy, "da63c9927411e3aca350977d653d24bbf5bca972"),
                          (modern, "d66cf179e7bc4d296362ab7e2e3b344dc3c4f665")):
        assert manifest["requested_tools"].pop("support_tools_revision") == pin
        floorplan, = [r for r in manifest["target"]["external_references"]
                      if r["role"] == "Floorplan_def"]
        assert floorplan.pop("revision") == pin
    assert legacy == modern, "unexpected manifest change across format eras"


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
        modern = baseline / "pre-starter"
        modern.mkdir()
        archive = subprocess.check_output(
            ["git", "archive", "6195834d5a5c10885895ad5d217ffc01303902a0",
             "lib", "flow_domain", "dune-project"], cwd=repo)
        subprocess.run(["tar", "-x", "-C", str(modern)], input=archive, check=True)
        shutil.copytree(repo / "examples", modern / "examples")
        with (modern / "lib/hardcaml_asic.ml").open("a") as aliases:
            aliases.write("\nmodule Chip_target = Project\n")
        emitter = Path("_build/default/examples/tt_bundle_example.exe")
        subprocess.run(["dune", "build", "--root", str(modern), "examples/tt_bundle_example.exe"],
                       cwd=modern, check=True)
        for kind in ("observable", "memory"):
            old, new = (baseline / (kind + suffix) for suffix in ("-old", "-new"))
            for provider, output in ((baseline, old), (modern, new)):
                subprocess.run([provider / emitter, kind, output, repo], check=True)
            compare_eras(old, new)
        subprocess.run(["python3", "-B", "test/check_resolved_flow_api.py",
                        "--baseline-emitter",
                        str(modern / emitter)],
                       cwd=repo, check=True)


if __name__ == "__main__":
    main()
