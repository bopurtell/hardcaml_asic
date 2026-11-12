"""Pure metadata release review; never builds, installs, runs EDA, or edits pins.

Writes a new review record. Historical diagnostic and recheck receipts are inputs
only. A successful review is not new physical qualification or provider acceptance.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = ROOT / "flow/hardcaml_asic_flow/physical_runtime.py"
    spec = importlib.util.spec_from_file_location("physical_runtime", source)
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    diagnostic, output = args.diagnostic.resolve(), args.output.resolve()
    rt.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(diagnostic),
               "new separate review output required")
    frozen = rt.load(diagnostic / "plan.json")
    run = Path(frozen["run"])
    rt.require(not output.is_relative_to(run), "review output must be outside original run")
    project = run / "project"
    plan = rt.load(project / "manifest.json")["macro_contract"]["physical_plan"]
    identity = rt.retained_obstruction_identity(project / "runs/asic", plan, diagnostic / "controls")
    digest = rt.sha(source)
    pin_sources = {
        "python_bridge": (ROOT / "flow/hardcaml_asic_flow/librelane_bridge.py", r'^PHYSICAL_RUNTIME_SHA256 = "([a-f0-9]{64})"'),
        "native_segment_service": (ROOT / "execution/backends/librelane/segment_service.ml", r'^let macro_runtime_sha256 = "([a-f0-9]{64})"'),
    }
    pins = {}
    for label, (path, pattern) in pin_sources.items():
        match = re.search(pattern, path.read_text(), re.M)
        rt.require(match is not None, "runtime pin declaration missing: " + str(path))
        pins[label] = {"path": str(path), "recorded_sha256": match[1], "expected_sha256": digest,
                       "matches": match[1] == digest}
    dune = (ROOT / "flow/dune").read_text()
    rt.require("(hardcaml_asic_flow/physical_runtime.py as flow/hardcaml_asic_flow/physical_runtime.py)" in dune,
               "physical runtime missing from installed package")
    coarse = (ROOT / "execution/coarse_run_service.ml").read_text()
    rt.require('copy (resources ^ "/physical_runtime.py") (project ^ "/physical-runtime.py")' in coarse and
               '"physical_checker_sha256" (s (hash (project ^ "/physical-runtime.py")))' in coarse,
               "coarse helper staging/hash association missing")
    record = {
        "schema_version": 1, "record_kind": "pure_source_release_review",
        "status": "ready_for_install" if all(pin["matches"] for pin in pins.values()) else "requires_owner_digest_refresh_before_install",
        "runtime_source": str(source), "runtime_sha256": digest, "segmented_runtime_pins": pins,
        "coarse_runtime_staging": "package_resource_copied_and_hashed_dynamically",
        "packaging": "physical_runtime.py_explicitly_installed_by_flow/dune",
        "installer_source_capture": "scripts/install-tools.py:capture_inventories_current_tracked_and_unignored_files_with_content_hashes",
        "automatic_segmented_pin_refresh": False,
        "transitive_pin_note": "Changing PHYSICAL_RUNTIME_SHA256 changes librelane_bridge.py bytes; its native bridge_sha256 pin must also be refreshed by its owner.",
        "retained_control_identity": "pass", "diagnostic_identity_artifacts": identity,
        "run_id": frozen["run_id"], "build_identity": frozen["build_identity"], "original_run_status": "failed",
        "provider_acceptance": False, "new_physical_qualification": False,
        "external_controls_scope": "separate_record_only_exact_failed_run_identity_frozen_GDS_DEF_LEF_models_and_reader_inputs",
        "source_version_policy": "only_recorded_diagnostic_checker_source_may_differ_all_other_frozen_inputs_must_match",
        "timeout_scope": {"production_full_DEF_control_seconds": 300, "production_standalone_abstract_baseline_seconds": 180,
                          "explicit_diagnostic_case_seconds": 480, "diagnostic_supervisor_seconds": 1980,
                          "production_timeout_changed": False,
                          "limitation": "witnessed_stock_full_DEF_check_takes_about403s_so_300s_production_control_may_fail_closed"},
        "work_performed": {"builds": 0, "EDA_launches": 0, "installs": 0, "git_operations": 0},
    }
    with output.open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": record["status"], "runtime_sha256": digest, "review": str(output)}))


if __name__ == "__main__":
    main()
