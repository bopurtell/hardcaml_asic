"""Execute the actual emitted producer against a native-API model, never EDA."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

producer = subprocess.check_output([sys.argv[1], "--producer"], text=True)
model = r'''
namespace eval sta {
  proc check_timing_cmd {args} {
    if {[lindex $args 0]} {
      return [list [list {Native message "quoted"} "detail\\line\nsecond" "\u03bb"]]
    }
    return {}
  }
  proc endpoints {} { return [list {u1/D} {u0/D} {u0/D}] }
}
proc get_full_name {pin} { return $pin }
proc write_sdc {args} {
  if {$args ne [list -no_timestamp -digits 12 [lindex $args end]]} {error "bad native flags"}
  set f [open [lindex $args end] w]; puts $f {# native model only}; close $f
}
proc check_setup {args} {
  set f [open [lindex $args end] a]
  if {[lsearch -exact $args -no_input_delay] >= 0} {
    puts $f {Native message "quoted"}
    puts $f "  detail\\line\nsecond"
    puts $f "  \u03bb"
  }
  close $f
}
source [lindex $argv 0]
::hc_pt7_native::write [lindex $argv 1]
'''
with tempfile.TemporaryDirectory(prefix="native-timing-tcl-") as root:
    root = Path(root)
    (root / "producer.tcl").write_text(producer)
    (root / "model.tcl").write_text(model)
    out = root / "output"
    out.mkdir()
    args = ["tclsh", str(root / "model.tcl"), str(root / "producer.tcl"), str(out)]
    result = subprocess.run(args, text=True, capture_output=True, check=True)
    assert result.stdout == "HC_ASIC_NATIVE_V2_COMPLETE\n", result
    data = json.loads((out / "native-data.json").read_text())
    assert data["complete"] is True
    assert data["schema"] == 2 and data["profile"] == "opensta-2.7-native-state-v2"
    assert len(data["categories"]) == 7
    group = data["categories"][0]["groups"][0]
    assert group == {"message": 'Native message "quoted"', "details": ["detail\\line\nsecond", "λ"]}
    assert data["endpoints"] == ["u0/D", "u1/D"]
    checks = "HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2\n"
    for cat in data["categories"]:
        checks += "HC_ASIC_NATIVE_CATEGORY_BEGIN " + cat["name"] + "\n"
        checks += "".join(g["message"] + "\n" + "".join("  " + d + "\n" for d in g["details"])
                          for g in cat["groups"])
        checks += "HC_ASIC_NATIVE_CATEGORY_END " + cat["name"] + "\n"
    assert (out / "native-checks.rpt").read_text() == checks + "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n"
    again = subprocess.run(args, text=True, capture_output=True)
    assert again.returncode != 0 and "HC_ASIC_NATIVE_V2_COMPLETE" not in again.stdout
    for label, change in [
        ("native-error", 'proc sta::check_timing_cmd {args} { error "deliberate native failure" }'),
        ("endpoint-budget", 'proc sta::endpoints {} { return [lrepeat 65537 u0/D] }'),
        ("string-budget", 'proc sta::endpoints {} { return [list [string repeat x 65537]] }'),
        ("marker-injection", 'proc sta::check_timing_cmd {args} { return [list [list "message\\nHC_ASIC_NATIVE_CATEGORY_END no_input_delay"]] }'),
    ]:
        failed_out = root / label
        failed_out.mkdir()
        script = model.replace("source [lindex $argv 0]", change + "\nsource [lindex $argv 0]")
        (root / "failure.tcl").write_text(script)
        failure = subprocess.run(["tclsh", str(root / "failure.tcl"), str(root / "producer.tcl"), str(failed_out)],
                                 text=True, capture_output=True)
        assert failure.returncode != 0, (label, failure)
        assert "HC_ASIC_NATIVE_V2_COMPLETE" not in failure.stdout
        assert not (failed_out / "native-data.json").exists()
print("Native timing producer Tcl API-model checks passed (no EDA)")
