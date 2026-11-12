"""Read-only candidate-library replay of the retained Protemu timing observation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay(args):
    report = json.loads(args.report.read_text())
    old = report["timing_analysis"]
    execution = Path(old["execution_file"])
    terminal = Path(old["transport_terminal"])
    launches = [row["path"] for row in old["inventory"]
                if Path(row["path"]).name == old["request_id"] + "-transport.json"]
    assert len(launches) == 1
    # Include original receipts/journal metadata in the read-only fence, in
    # addition to every source bound by the old and recomputed observations.
    fenced = {str(p): sha(p) for p in args.report.parent.rglob("*.json")}
    for row in old["inventory"]:
        assert sha(row["path"]) == row["sha256"], row
        fenced[row["path"]] = row["sha256"]
    result = subprocess.run([str(args.consumer.resolve()), str(execution), launches[0],
                             str(terminal), "--capture"], check=True,
                            capture_output=True, text=True, timeout=120)
    observation = json.loads(result.stdout)
    assert observation["status"] == "completed", observation
    assert observation["timing_goal"] == "fail"
    for key in ("execution_file", "execution_sha256", "request_id", "request_sha256",
                "attempt_id", "occurrence_id", "analysis_id", "stage", "schema_version"):
        assert observation[key] == old[key], key
    expected = {"nom_fast_1p32V_m40C": (11.110903622757752, 0),
                "nom_slow_1p08V_125C": (-3.0998031685543976, 444),
                "nom_typ_1p20V_25C": (5.886082898801281, 0)}
    for corner, (slack, count) in expected.items():
        for key, value in (("setup_slack", slack), ("setup_violations", count)):
            metric = next(m for m in observation["metrics"]
                          if m["corner"] == corner and m["key"] == key)
            assert metric["availability"] == "available" and metric["value"] == value
            source = metric["source"]["file"]
            with Path(source["path"]).open() as stream:
                line = next(s for i, s in enumerate(stream, 1) if i == metric["source"]["line"])
            tag, raw_key, token = line.split()
            assert raw_key == metric["raw_key"] and float(token) == value
    for row in observation["inventory"]:
        assert sha(row["path"]) == row["sha256"], row
    if args.public:
        current_execution = str(execution).removesuffix(".timing-source")
        command = [str(args.public.resolve()), "inspect", current_execution, "--domain", "timing",
                   "--stage", "timing", "--refresh-timing", "--json"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        index = json.loads(result.stdout)
        lineage = index["reinspection"]
        assert lineage["original_analysis_digest"] == old["source_digest"]
        assert lineage["original_status"] == "unavailable" and lineage["original_timing_goal"] == "unknown"
        assert lineage["recomputed_analysis"] == observation
        row = next(r for r in index["rows"] if r["corner"] == "nom_slow_1p08V_125C"
                   and r["value"] == -3.099803 and r["unit"] == "ns")
        source = json.loads(row["source"])
        assert sha(source["file"]["path"]) == source["file"]["sha256"]
        result = subprocess.run(command + ["--row-id", row["id"], "--expected-digest", index["source_digest"]],
                                check=True, capture_output=True, text=True, timeout=120)
        detail = json.loads(result.stdout)
        assert "-3.099803" in detail["detail"] and detail["reinspection"] == lineage
        print("Public read-only timing refresh index/detail and original lineage: passed", file=__import__("sys").stderr)
    for path, digest in fenced.items():
        assert sha(path) == digest, "retained evidence changed: " + path
    print(json.dumps(observation, indent=2, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--public", type=Path, help="Installed hardcaml-asic-flow executable; also verify public refresh index/detail")
    replay(parser.parse_args())
