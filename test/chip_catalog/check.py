"""Exercise the installed CLI shape using independently buildable Dune targets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

cli = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="chip targets ") as tmp:
    root = Path(tmp) / "project"
    root.mkdir()
    (root / "dune-project").write_text("(lang dune 3.0)\n")
    manifest = root / "hardcaml-asic-targets.json"
    entries = []
    for chip in ("fifo", "ram"):
        src = root / chip
        src.mkdir()
        (src / "dune").write_text("(executable (name driver))\n")
        (src / "driver.ml").write_text(
            f'let () = print_endline "{chip}"; '
            'Array.iteri (fun i s -> if i > 0 then print_endline s) Sys.argv; '
            'if Sys.argv.(1) = "fail" then exit 23\n')
        entries.append({"id": chip, "executable": f"{chip}/driver.exe"})
    declaration = dict(schema_version=1, default_target="ram", targets=entries)
    manifest.write_text(json.dumps(declaration))

    def run(*args, status=0, cwd=tmp):
        p = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                           env={k: v for k, v in os.environ.items()
                                if k not in ("DUNE_BUILD_DIR", "DUNE_WORKSPACE")})
        assert p.returncode == status, (args, p.returncode, p.stdout, p.stderr)
        return p.stdout if status == 0 else p.stderr

    def call(command, *args, status=0):
        return run(cli, command, "--catalog", manifest, *args, status=status)

    assert json.loads(run(cli, "list", cwd=root)) == declaration
    assert json.loads(call("select")) == entries[1]
    assert not (root / "_build").exists()  # discovery doesn't invoke Dune
    argv = ("flows", "a b", "--target", "ram", "$(exit 99)", "")
    for entry in entries:
        assert json.loads(call("select", "--target", entry["id"])) == entry
        assert call("exec", "--target", entry["id"], "--", *argv).splitlines() == [
            entry["id"], *argv]
    call("exec", "--", "fail", status=23)
    assert "ram" in call("exec", "--target", "removed", "--", status=2)
    declaration["targets"] = [entries[1]]
    manifest.write_text(json.dumps(declaration))
    assert "fifo" not in call("list")
    assert "Unknown chip target" in call("select", "--target", "fifo", status=2)
    assert run("dune", "exec", "./fifo/driver.exe", "--", "direct", cwd=root) == "fifo\ndirect\n"

    for change, expected in [
        ({"targets": entries * 2}, "Duplicate chip target ID"),
        ({"default_target": "removed"}, "default_target"),
        ({"schema_version": 2}, "schema_version"),
        ({"targets": []}, "nonempty"),
        ({"targets": [dict(entries[0], id="Bad")]}, "Invalid chip target ID"),
        ({"targets": [dict(entries[0], executable="../elsewhere.exe")]}, "project-relative"),
        ({"targets": [dict(entries[0], executable="/elsewhere.exe")]}, "project-relative"),
        ({"targets": [dict(entries[0], typo=True)]}, "unknown field"),
    ]:
        manifest.write_text(json.dumps({**declaration, **change}))
        assert expected in call("list", status=2)
    manifest.write_text('{"schema_version":1,"schema_version":2}')
    assert call("list", status=2)
