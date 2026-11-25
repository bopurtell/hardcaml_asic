"""Independent Tcl-language oracle for the retained comment counterexample.

Only inert mock commands run under tclsh; no native STA or production parser.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

hidden = Path(sys.argv[1]).read_text()
assert hidden == '# harmless-looking continued comment \\\nset_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]\n'
with tempfile.TemporaryDirectory(prefix="native-comment-language-") as root:
    root = Path(root)
    oracle = root / "oracle.tcl"
    oracle.write_text('''set calls 0
proc get_ports {args} { return $args }
proc get_pins {args} { return $args }
proc set_false_path {args} { incr ::calls }
source [lindex $argv 0]
puts $calls
''')
    for name, text, expected in [
        ("continued-comment", hidden, "0\n"),
        ("ordinary-comment", hidden.replace(" \\\n", "\n", 1), "1\n"),
        ("continued-comment-after-semicolon", ";" + hidden, "0\n"),
        ("indented-continued-comment", "  " + hidden, "0\n"),
    ]:
        source = root / (name + ".tcl")
        source.write_text(text)
        result = subprocess.run(["tclsh", str(oracle), str(source)], text=True,
                                capture_output=True, check=True, timeout=10)
        assert result.stdout == expected, (name, result)
print("Native timing independent tclsh comment-language controls passed (no EDA)")
