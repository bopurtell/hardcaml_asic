"""Independent Tcl source-channel newline oracle; inert commands only, no EDA."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

cases = json.loads(Path(sys.argv[1]).read_text())
assert len(cases) == 17
assert any(b"\r" in c["state"].encode() and b"\r\n" not in c["state"].encode() for c in cases)
with tempfile.TemporaryDirectory(prefix="native-newline-language-") as root:
    root = Path(root)
    oracle = root / "oracle.tcl"
    oracle.write_text('''set false_paths 0
set disables 0
set designs {}
proc current_design {name} { lappend ::designs $name }
proc get_ports {args} { return $args }
proc get_pins {args} { return $args }
proc get_cells {args} { return $args }
proc set_false_path {args} { incr ::false_paths }
proc set_disable_timing {args} { incr ::disables }
proc set_load {args} {}
source [lindex $argv 0]
puts $::false_paths
puts $::disables
puts [join $::designs ,]
''')
    for case in cases:
        source = root / (case["name"] + ".sdc")
        # Binary write is essential: the oracle must receive literal CR bytes,
        # not Python universal-newline input normalization or backslash-r text.
        source.write_bytes(case["state"].encode("utf-8"))
        result = subprocess.run(["tclsh", str(oracle), str(source)], text=True,
                                capture_output=True, check=True, timeout=10)
        expected = [str(case["false_paths"]), str(case["disables"]), ",".join(case["designs"])]
        assert result.stdout.splitlines() == expected, (case["name"], result, expected)
print("Native timing independent tclsh LF/CR/CRLF source-channel controls: 17 passed (no EDA)")
