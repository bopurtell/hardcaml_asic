"""Stat/header-only outer admission. Sparse bytes are never parsed or loaded."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

consumer = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="named-timing-outer-bounds-", dir="/tmp/opencode") as tmp:
    root = Path(tmp)
    paths = []
    for i in range(4097):
        path = root / f"source-{i}"
        path.touch()
        paths.append(path)
    gds = root / "native.gds"
    with gds.open("wb") as stream:
        stream.truncate(67_485_696)
    large = root / "over-limit"
    with large.open("wb") as stream:
        stream.truncate(128 * 1024 * 1024 + 1)
    aggregate = []
    for i in range(5):
        path = root / f"aggregate-{i}"
        with path.open("wb") as stream:
            stream.truncate(128 * 1024 * 1024)
        aggregate.append(path)
    def check(label, selected, code, message=None):
        request = root / "sources.json"
        request.write_text(json.dumps(list(map(str, selected))))
        p = subprocess.run([consumer, "precheck", request], text=True, capture_output=True, timeout=10)
        assert p.returncode == code, (label, p.returncode, p.stderr)
        if message:
            assert message in p.stderr, (label, p.stderr)
    check("actual PDK shape", paths[:1485] + [gds], 0)
    check("exact count", paths[:4096], 0)
    check("deduplicated paths", paths[:4096] + paths[:10], 0)
    check("count exceeded", paths, 2, "count exceeds 4096")
    check("individual bound", [large], 2, "exceeds 128MiB")
    check("exact aggregate", aggregate[:4], 0)
    check("aggregate exceeded", aggregate, 2, "exceed 512MiB")
    alias = root / "alias"
    alias.symlink_to(gds)
    check("unregistered symlink rejected", [alias], 2, "linked/noncanonical")
    request=root / "sources.json"
    request.write_text(json.dumps([str(alias),str(gds)]))
    roots=root / "roots.json"
    roots.write_text(json.dumps([str(root)]))
    p=subprocess.run([consumer,"precheck",request,roots],text=True,capture_output=True,timeout=10)
    assert p.returncode==0,(p.returncode,p.stderr)
    outside=root / "outside-root"
    outside.mkdir()
    roots.write_text(json.dumps([str(outside)]))
    p=subprocess.run([consumer,"precheck",request,roots],text=True,capture_output=True,timeout=10)
    assert p.returncode==2 and "outside allowed" in p.stderr,(p.returncode,p.stderr)
    print("10 named outer admission controls pass:1486-source/GDS shape,4096/128MiB/512MiB boundaries, exact declared keys and explicit-root aliases; unregistered/outside-root links reject")
