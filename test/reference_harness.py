"""Test-only source oracle, explicitly separated from installed runtime bytes.

The small prefix-shaped oracle contains copied retirement candidates and links
to the actual runtime package and native executables. No build tree is copied.
Callers must supply both roots; installed tests never discover a checkout oracle.
"""
import copy
import os
from pathlib import Path
import shutil
import sys

RETIRED = (
    "__main__", "archive", "cli", "ocaml_backend", "checkpoint", "stages",
    "identity", "execution_transport", "execution_plan", "coarse_flow_oracle", "report",
    "presentation", "result_store", "runner", "flow_observation", "finalization",
)
RUNTIME = (
    "__init__", "backend_protocol", "librelane_bridge", "stage_metadata_probe",
    "probe", "runtime_observation", "runtime_contract", "managed_python", "paths",
)


def observation_description(base):
    """Exact native named-only extension of the unchanged legacy description."""
    expected = copy.deepcopy(base)
    assert 'named_execution_delivery' not in expected
    expected['named_execution_delivery'] = {
        'execution_file': 'named_inspect_and_terminal_only; absolute_validated_readable_record_or_null; empty_reservation_is_null',
        'validation': 'strict_stage_execution_record; exact_attempt_recipe_snapshot_plan_bundle; no_runtime_or_catalog',
        'stored_record': 'unchanged; delivery_field_is_not_persisted'}
    additions = {
        'methodology': 'named_only; hardcaml-asic.historical-methodology_v1_recorded|pending; absent_means_not_recorded',
        'execution_request': 'named_only; retained_native_request_or_original_allocation_request',
        'execution_file': 'named_only; absolute_validated_readable_record_or_null'}
    assert not additions.keys() & expected['schemas']['inspect'].keys()
    expected['schemas']['inspect'].update(additions)
    return expected


def hardlink_prefix(staging, prefix):
    shared = os.environ.get("FLOW_TEST_INSTALLED_PREFIX")
    if shared:
        shared = Path(shared).resolve(strict=True)
        if not shared.is_relative_to(Path('/tmp/opencode')):
            raise ValueError("shared test prefix must be private under /tmp/opencode")
        package = shared / 'lib/hardcaml_asic/flow/hardcaml_asic_flow'
        if {p.stem for p in package.glob('*.py')} != set(RUNTIME):
            raise ValueError("shared test prefix must already be thin")
        Path(prefix).symlink_to(shared, target_is_directory=True)
        return
    shutil.copytree(staging, prefix, symlinks=False,
                    copy_function=lambda src, dst: os.link(Path(src).resolve(), dst))


def copy_reference(source_root, reference_root, runtime_root):
    source_root, reference_root, runtime_root = map(Path, (source_root, reference_root, runtime_root))
    source_root, reference_root, runtime_root = (p.resolve() for p in (source_root, reference_root, runtime_root))
    if runtime_root.parts[-3:] != ("lib", "hardcaml_asic", "flow"):
        raise ValueError("runtime root must be PREFIX/lib/hardcaml_asic/flow")
    if reference_root.is_relative_to(source_root) or reference_root.is_relative_to(runtime_root.parents[2]):
        raise ValueError("reference must live outside source and installed runtime")
    package = reference_root / "lib/hardcaml_asic/flow/hardcaml_asic_flow"
    package.mkdir(parents=True)
    for name in RETIRED:
        shutil.copyfile(source_root / "hardcaml_asic_flow" / (name + ".py"), package / (name + ".py"))
    for name in RUNTIME:
        target = runtime_root / "hardcaml_asic_flow" / (name + ".py")
        if not target.is_file():
            raise FileNotFoundError(target)
        (package / (name + ".py")).symlink_to(target)
    (package / "data").symlink_to(runtime_root / "hardcaml_asic_flow/data", target_is_directory=True)
    native_bin = runtime_root.parents[2] / "bin"
    (reference_root / "bin").symlink_to(native_bin, target_is_directory=True)
    return package.parent


def thin_prefix(prefix):
    """Unlink private hardlinks only; never modify shared installed files."""
    package = Path(prefix) / "lib/hardcaml_asic/flow/hardcaml_asic_flow"
    for name in RETIRED:
        (package / (name + ".py")).unlink(missing_ok=True)
    assert {p.stem for p in package.glob("*.py")} == set(RUNTIME)


def installed_prefix(checkout, prefix):
    """Prepare one disposable thin prefix and an explicit source reference."""
    checkout, prefix = Path(checkout), Path(prefix)
    hardlink_prefix(checkout / "_build/install/default", prefix)
    thin_prefix(prefix)
    runtime = prefix / "lib/hardcaml_asic/flow"
    reference = copy_reference(checkout / "flow", prefix.parent / "reference", runtime)
    os.environ.update(FLOW_TEST_REFERENCE_ROOT=str(reference),
                      FLOW_TEST_RUNTIME_ROOT=str(runtime), PYTHONDONTWRITEBYTECODE='1')
    # Copied standalone drivers use the same helper, even under checkout denial.
    shutil.copyfile(checkout / "test/reference_harness.py", prefix.parent / "reference_harness.py")
    # Some service drivers import their source fixture at module load time.
    # Rebind the comparison package before beginning installed-mode denial.
    for name in tuple(sys.modules):
        if name == "hardcaml_asic_flow" or name.startswith("hardcaml_asic_flow."):
            del sys.modules[name]
    activate(reference, runtime)
    return reference


def activate(reference_root=None, runtime_root=None):
    reference_root = reference_root or os.environ.get("FLOW_TEST_REFERENCE_ROOT")
    runtime_root = runtime_root or os.environ.get("FLOW_TEST_RUNTIME_ROOT")
    if not reference_root or not runtime_root:
        raise ValueError("installed oracle requires explicit reference and runtime roots")
    reference_root, runtime_root = Path(reference_root).resolve(), Path(runtime_root).resolve()
    if reference_root == runtime_root:
        raise ValueError("reference and runtime roots must differ")
    for name in RUNTIME:
        linked = reference_root / "hardcaml_asic_flow" / (name + ".py")
        expected = runtime_root / "hardcaml_asic_flow" / (name + ".py")
        if linked.resolve() != expected.resolve() or not expected.is_file():
            raise ValueError("oracle runtime must resolve to installed bytes: " + name)
    sys.path.insert(0, str(reference_root))
    from hardcaml_asic_flow import ocaml_backend
    assert Path(ocaml_backend.__file__).resolve().parent == reference_root / "hardcaml_asic_flow"
    # Bind native helpers/resources explicitly, including imports used by fixtures.
    ocaml_backend.PACKAGE = runtime_root / "hardcaml_asic_flow"
    from hardcaml_asic_flow import backend_protocol, probe
    for module in (backend_protocol, probe):
        assert Path(module.__file__).resolve().parent == runtime_root / "hardcaml_asic_flow"
    return reference_root


ENTRY = '''import os, runpy, sys
from pathlib import Path
checkout, driver = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(driver.parent))
from reference_harness import activate
def deny_checkout(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if Path(os.fsdecode(args[0])).resolve().is_relative_to(checkout):
            raise RuntimeError("checkout read denied: " + str(args[0]))
sys.addaudithook(deny_checkout)
activate()
sys.argv = [str(driver), *sys.argv[3:]]
runpy.run_path(str(driver), run_name="__main__")
'''
