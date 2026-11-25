"""Actual wrapper Tcl with LibreLane's rerouted environment, no EDA.

Model the boundary observed in C's runtime-api-06 and failed coarse-integration-02:
PT7_* live only in _TCL_ENV_IN. The normal corner script bootstraps that file again
and owns the only read_sdc. Native resolution/report APIs below are fixtures.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

spec = importlib.util.spec_from_file_location("pt7_runtime", sys.argv[1])
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
tests = 0

with tempfile.TemporaryDirectory(prefix="linked timing Tcl startup ") as temporary:
    root = Path(temporary)
    receipt = root / "receipt.json"
    receipt.write_text(json.dumps(dict(schema=1, audit_id="actual-rerouted-receipt")) + "\n")
    sdc = root / "fixture.sdc"
    sdc.write_text('''puts "HC_ASIC_TIMING_V1\\t{\\"schema_version\\":1,\\"event\\":\\"begin\\"}"
if {$::env(FIXTURE_FAILURE) eq "sdc"} { error "fixture SDC resolution failed" }
puts "HC_ASIC_TIMING_V1\\t{\\"schema_version\\":1,\\"event\\":\\"complete\\"}"
''')
    corner = root / "corner.tcl"
    corner.write_text('''# Model the native common/io.tcl bootstrap before read_timing_info.
source $::env(_TCL_ENV_IN)
if {$::env_bootstraps != 2} { error "wrapper failed to bootstrap before native io" }
proc read_sdc {path} {
    incr ::sdc_loads
    if {$::sdc_loads != 1} { error "duplicate SDC load" }
    puts "FIXTURE_SDC_LOAD"
    source $path
}
set ::sdc_loads 0
read_sdc $::env(_SDC_IN)
if {$::env(FIXTURE_FAILURE) eq "hook"} { error "fixture hook failed" }
puts "HC_ASIC_NATIVE_V2_COMPLETE"
if {$::env(FIXTURE_FAILURE) eq "reports"} { error "fixture standard reports failed after hook" }
puts "FIXTURE_STANDARD_REPORTS_COMPLETE"
puts "FIXTURE_SDC_LOAD_COUNT $::sdc_loads"
''')
    environment_file = root / "_env_ThreadPoolExecutor-1_0.tcl"
    driver = root / "driver.tcl"
    driver.write_text(runtime.DRIVER)
    assignments = {
        "PT7_RECEIPT": str(receipt), "PT7_CORNER_SCRIPT": str(corner),
        "PT7_AUDIT_DIR": str(root), "FIXTURE_FAILURE": "none",
    }

    def run(mode="normal", script=runtime.DRIVER):
        global tests
        values = dict(assignments, FIXTURE_FAILURE=mode)
        if mode == "missing_receipt":
            values["PT7_RECEIPT"] = str(root / "missing-receipt.json")
        elif mode == "missing_corner":
            values["PT7_CORNER_SCRIPT"] = str(root / "missing-corner.tcl")
        environment_file.write_text("incr ::env_bootstraps\n" + "".join(
            f"set ::env({key}) {runtime.tcl_word(value)}\n" for key, value in values.items()))
        # Underscore variables are directly passed by _reroute_env; PT7 fields
        # are assignments in the generated file, not process environment values.
        env = {k: v for k, v in os.environ.items() if not k.startswith("PT7_") and k != "_TCL_ENV_IN"}
        env.update(_TCL_ENV_IN=str(environment_file), _SDC_IN=str(sdc))
        if mode == "missing_pointer":
            env.pop("_TCL_ENV_IN")
        elif mode == "missing_env_file":
            env["_TCL_ENV_IN"] = str(root / "missing-env.tcl")
        elif mode == "broken_env_file":
            environment_file.write_text('error "fixture bootstrap failure"\n')
        elif mode == "stale_direct_context":
            env.update(PT7_RECEIPT=str(root / "wrong-receipt.json"),
                       PT7_CORNER_SCRIPT=str(root / "wrong-corner.tcl"))
        driver.write_text(script)
        # Initialize instrumentation without defining any native STA command.
        launcher = root / "launch.tcl"
        launcher.write_text("set ::env_bootstraps 0\nsource " + runtime.tcl_word(str(driver)) + "\n")
        result = subprocess.run(["tclsh", str(launcher)], env=env, text=True, capture_output=True, timeout=10)
        tests += 1
        return result

    for mode in ("normal", "stale_direct_context"):
        result = run(mode)
        assert result.returncode == 0 and not result.stderr, result
        lines = result.stdout.splitlines()
        assert len([line for line in lines if line.startswith("PT7_AUDIT_RECEIPT ")]) == 1
        assert json.loads(lines[0].removeprefix("PT7_AUDIT_RECEIPT "))["audit_id"] == "actual-rerouted-receipt"
        assert lines[1:] == ["FIXTURE_SDC_LOAD",
            'HC_ASIC_TIMING_V1\t{"schema_version":1,"event":"begin"}',
            'HC_ASIC_TIMING_V1\t{"schema_version":1,"event":"complete"}',
            "HC_ASIC_NATIVE_V2_COMPLETE", "FIXTURE_STANDARD_REPORTS_COMPLETE",
            "FIXTURE_SDC_LOAD_COUNT 1", "PT7_AUDIT_COMPLETE"], lines
    for mode in ("missing_pointer", "missing_env_file", "broken_env_file", "missing_receipt", "missing_corner", "sdc", "hook", "reports"):
        result = run(mode)
        assert result.returncode == 1 and "PT7_ERROR " in result.stderr, (mode, result)
        assert "PT7_AUDIT_COMPLETE" not in result.stdout, (mode, result)
        if mode in ("missing_pointer", "missing_env_file", "broken_env_file", "missing_receipt"):
            assert "PT7_AUDIT_RECEIPT" not in result.stdout and "HC_ASIC_TIMING_V1" not in result.stdout
        if mode == "reports":
            assert "HC_ASIC_NATIVE_V2_COMPLETE" in result.stdout
    # This control reproduces the original bug: PT7 fields were not exported.
    broken = runtime.DRIVER.replace("    source $::env(_TCL_ENV_IN)\n", "")
    result = run(script=broken)
    assert result.returncode == 1 and "PT7_RECEIPT" in result.stderr
    assert "PT7_AUDIT_RECEIPT" not in result.stdout and "FIXTURE_SDC_LOAD" not in result.stdout

print(f"Linked timing Tcl startup: {tests} rerouted-environment/order/failure checks passed; tclsh fixtures, no EDA")
