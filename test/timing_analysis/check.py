"""Native terminal grammar + exact report/inspection, without EDA or a runtime."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_stage_report import execution, snapshot, write, canonical, IMAGE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root, case="good", outer="failed"):
    project = root / "project"
    run = project / "src/runs/signoff-exact-request"
    producer = run / "11-openroad-stapostpnr-1"
    producer.mkdir(parents=True)
    request = dict(protocol_version=1, operation="execute_segment", request_id="signoff-exact-request",
                   reply_path=str(project / "reply.json"), boundary="signoff",
                   checkpoint=str(root / "routing"), source_boundary="routing",
                   candidate=str(project / "candidates/signoff-exact-request"),
                   project=str(project), pdk_root=str(root / "pdk"), tag="signoff-exact-request",
                   bridge_sha256="a" * 64)
    request_path = project / "request.json"
    write(request_path, request)
    request_hash = hashlib.sha256(canonical(request).encode()).hexdigest()
    launch = root / "signoff-exact-request-transport.json"
    terminal = root / "signoff-exact-request-transport-result.json"
    write(launch, dict(request_path=str(request_path), request_id=request["request_id"],
                       request_sha256=request_hash, fresh_run_dir=str(run),
                       binding=dict(image_identity=dict(image_id=IMAGE))))
    write(terminal, dict(request_id=request["request_id"], request_sha256=request_hash,
                         transport_succeeded=False, process=dict(outcome="timed_out")))
    corners = ["fast", "slow", "extra1", "extra2"] if case == "aggregate_reports" else ["fast", "slow"]
    write(producer / "config.json", dict(meta=dict(step="OpenROAD.STAPostPNR", librelane_version="3.1.0.dev3"), STA_CORNERS=corners))
    metrics = {}
    libs = {}
    path_text = "Startpoint: in\nEndpoint: out\nPath Group: clk\nPath Type: max\n 1.0 data arrival time\n 2.0 data required time\n 1.0 slack (MET)\n"
    for corner in corners:
        d = producer / corner
        d.mkdir()
        lib = d / "generated.lib"
        lib.write_text('library (fixture) {\n time_unit : "1ns";\n}\n')
        libs[corner] = str(lib)
        log = []
        for mode in ("setup", "hold"):
            for kind, suffix, tag, value in (("slack", "__ws", "%OL_METRIC_F", 1.25),
                                              ("violations", "_vio__count", "%OL_METRIC_I", 0)):
                key = f"timing__{mode}{suffix}__corner:{corner}"
                if case == "negative" and corner == "slow" and mode == "setup" and kind == "slack":
                    value = -0.125
                if case == "nonfinite" and mode == "setup" and kind == "slack":
                    value = float("inf")
                metrics[key] = value
                if case != "inherited":
                    log.append(f"{tag} {key} {value}")
        (d / "sta.log").write_text("\n".join(log) + "\n")
        for name in ("max.rpt", "min.rpt"):
            (d / name).write_text(path_text)
    write(producer / "state_in.json", dict(metrics=copy.deepcopy(metrics), nl="/recorded/netlist.v")) if case != "nonfinite" else (producer / "state_in.json").write_text('{"metrics":{}}')
    (producer / "state_out.json").write_text(json.dumps(dict(metrics=metrics, lib=libs)))
    (producer / "runtime.txt").write_text("00:00:01.575")
    if case == "zero":
        (producer / "runtime.txt").write_text("00:00:00.000")
    if case == "bad_runtime":
        (producer / "runtime.txt").write_text("00:90:01.000")
    if case == "partial":
        (producer / "runtime.txt").unlink()
    if case == "missing_corner":
        (producer / "slow/sta.log").unlink()
    if case == "unit":
        (producer / "slow/generated.lib").write_text('library (x) { time_unit : "1ps"; }\n')
    if case == "duplicate":
        (run / "12-openroad-stapostpnr").mkdir()
    if case == "wrong_meta":
        write(producer / "config.json", dict(meta=dict(step="OpenROAD.STAPrePNR", librelane_version="3.1.0.dev3"), STA_CORNERS=corners))
    if case == "stale":
        value = json.loads(launch.read_text()); value["fresh_run_dir"] = None; write(launch, value)
    if case == "wrong_image":
        value = json.loads(launch.read_text()); value["binding"]["image_identity"]["image_id"] = "sha256:" + "0" * 64; write(launch, value)
    if case == "conflicting_metric":
        log = producer / "slow/sta.log"
        log.write_text(log.read_text() + "%OL_METRIC_F timing__setup__ws__corner:slow 2.0\n")
    if case == "mismatched_metric":
        log = producer / "slow/sta.log"
        log.write_text(log.read_text().replace("1.25", "2.5"))
    if case == "symlink":
        (producer / "runtime.txt").unlink()
        (root / "outside-runtime").write_text("00:00:01.575")
        (producer / "runtime.txt").symlink_to(root / "outside-runtime")
    if case == "oversized":
        with (producer / "config.json").open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
    if case.startswith("large"):
        # Retained Protemu b591d335: 280174--281300 lines, ~22.6 MB/log;
        # metrics occur AFTER the old 262144-line fence. Use actual native
        # point syntax and exact slow metrics, without embedding design data.
        point = b"     1    0.023926    0.055102    0.076388    0.602765 v _17566_/X (sg13cmos5l_and2_1)\n"
        for corner in corners:
            d = producer / corner
            log = d / "sta.log"
            tail = log.read_text()
            if case == "large_negative" and corner == "slow":
                tail = tail.replace("timing__setup__ws__corner:slow 1.25", "timing__setup__ws__corner:slow -3.0998031685543976")
                tail = tail.replace("timing__setup_vio__count__corner:slow 0", "timing__setup_vio__count__corner:slow 444")
                metrics["timing__setup__ws__corner:slow"] = -3.0998031685543976
                metrics["timing__setup_vio__count__corner:slow"] = 444
            with log.open("wb") as stream:
                for _ in range(281):
                    stream.write(point * 1000)
                stream.write((tail.rstrip("\n") if case == "large_negative" else tail).encode())
                if corner == "slow":
                    if case == "large_conflict":
                        stream.write(b"%OL_METRIC_F timing__setup__ws__corner:slow -99\n")
                    if case == "large_malformed":
                        stream.write(b"%OL_METRIC_F timing__setup__ws__corner:slow 1.25 extra\n")
                    if case == "large_wrong_tag":
                        stream.write(b"%OL_METRIC_X timing__setup__ws__corner:slow -99\n")
                    if case == "large_long_line":
                        stream.write(b"x" * 16385)
                    if case == "large_binary":
                        stream.write(b"\x00\n")
            # Hash-only reports also cross the old aggregate byte bound.
            for name in ("max.rpt", "min.rpt"):
                with (d / name).open("wb") as stream:
                    for _ in range(100):
                        stream.write(point * 1000)
                    stream.write(path_text.encode())
        write(producer / "state_out.json", dict(metrics=metrics, lib=libs))
    if case == "oversized_report":
        with (producer / "slow/max.rpt").open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
    if case == "aggregate_reports":
        for corner in corners:
            for name in ("max.rpt", "min.rpt"):
                with (producer / corner / name).open("wb") as stream:
                    stream.truncate(32 * 1024 * 1024)
    if case == "before_sta":
        import shutil
        shutil.rmtree(producer)
    value = execution([], ["signoff"], status=outer)
    value["plan"]["initial_boundary"] = "routing"
    value["occurrences"][0].update(status=outer, exit_code=value["exit_code"], error="later DRC timeout", duration_seconds=607.08)
    file = root / "execution-attempt.json"
    write(file, value)
    return file, launch, terminal, producer


def main(args):
    env = dict(os.environ, PATH="/no-eda-no-python", PYTHONDONTWRITEBYTECODE="1")
    def call(command, expected=0):
        p = subprocess.run([str(x) for x in command], env=env, text=True, capture_output=True, timeout=30)
        assert p.returncode == expected, (command, p.stdout, p.stderr)
        if expected:
            assert not p.stdout and p.stderr
            return None
        return json.loads(p.stdout)
    with tempfile.TemporaryDirectory(prefix="timing-analysis-") as temporary:
        root = Path(temporary)
        for case in ("good", "zero", "negative", "nonfinite", "inherited", "missing_corner", "unit", "conflicting_metric", "mismatched_metric", "bad_runtime", "partial", "duplicate", "wrong_meta", "stale", "before_sta", "wrong_image", "symlink", "oversized", "large_negative", "large_conflict", "large_malformed", "large_wrong_tag", "large_long_line", "large_binary", "oversized_report", "aggregate_reports"):
            d = root / case; d.mkdir()
            execution_file, launch, terminal, producer = fixture(d, case)
            observation = call([args.consumer, execution_file, launch, terminal])
            completed = case not in ("bad_runtime", "partial", "duplicate", "wrong_meta", "stale", "before_sta", "wrong_image", "symlink", "oversized", "large_long_line", "large_binary", "oversized_report", "aggregate_reports")
            assert observation["status"] == ("completed" if completed else "unavailable"), observation
            assert observation["enclosing_status"] == "failed"
            assert observation["occurrence_id"] == "attempt/implementation.signoff"
            if completed:
                assert observation["duration"]["seconds"] == (0 if case == "zero" else 1.575)
                assert observation["producer"]["step_id"] == "OpenROAD.STAPostPNR-1"
                goal = "pass" if case in ("good", "zero") else "fail" if case in ("negative", "large_negative") else "unknown"
                assert observation["timing_goal"] == goal, observation
                if case == "large_negative":
                    metric = next(m for m in observation["metrics"] if m["corner"] == "slow" and m["key"] == "setup_slack")
                    assert metric["value"] == -3.0998031685543976
                    assert metric["source"]["line"] == 281001
                    assert metric["source"]["file"] == dict(path=str(producer / "slow/sta.log"), sha256=sha(producer / "slow/sta.log"))
                    count = next(m for m in observation["metrics"] if m["corner"] == "slow" and m["key"] == "setup_violations")
                    assert count["value"] == 444
            else:
                assert observation["duration"] is None and observation["timing_goal"] == "unknown"
                if case == "aggregate_reports":
                    assert observation["reason"] == "timing analysis: aggregate report byte bound exceeded"
            before = snapshot(d)
            replay = call([args.consumer, execution_file, launch, terminal, "--capture"])
            assert replay == observation
            assert snapshot(d) == before, "capture mutated retained evidence"
            report = call([args.report, "--target", execution_file, "--json"])
            assert report["timing_analysis"] == observation and report["execution"]["status"] == "failed"
            assert not report["checkpoints"] and report["acceptance"] == "not_evaluated"
            index_cmd = [args.inspect, "--execution", execution_file, "--domain", "timing", "--stage", "signoff", "--json"]
            index = call(index_cmd + ["--expected-digest", observation["source_digest"]])
            assert index["analysis_digest"] == observation["source_digest"]
            if completed:
                assert index["rows"], index
                row = next(r for r in index["rows"] if r["category"] == "timing_path")
                detail = call(index_cmd + ["--row-id", row["id"], "--expected-digest", index["source_digest"]])
                assert detail["row_id"] == row["id"] and detail["analysis_digest"] == observation["source_digest"]
            else:
                assert not index["rows"]
            call(index_cmd + ["--expected-digest", "0" * 64], expected=2)
            assert snapshot(d) == before, "report/inspection mutated evidence"
            # A frozen observation never overwrites an earlier publication.
            call([args.consumer, execution_file, launch, terminal], expected=2)
            if case == "good":
                public = call([args.public, "inspect", execution_file, "--domain", "timing", "--stage", "signoff", "--expected-digest", observation["source_digest"], "--json"])
                assert public == index
                (producer / "fast/max.rpt").write_text("replacement report")
                call(index_cmd, expected=2)
                # Completion persists; report reads immutable facts, inspection rejects changed sources.
                assert call([args.report, "--target", execution_file, "--json"])["timing_analysis"]["status"] == "completed"
                (producer / "runtime.txt").unlink()
                call(index_cmd, expected=2)
                execution_file.write_text(execution_file.read_text() + " ")
                call([args.report, "--target", execution_file, "--json"], expected=2)
        # Cancellation preserves completed analysis too.
        d = root / "legacy-unavailable"; d.mkdir()
        file, launch, terminal, producer = fixture(d, "large_negative")
        report_path = producer / "slow/max.rpt"
        report_path.write_text(report_path.read_text().replace("1.0 slack (MET)", "-3.099803 slack (VIOLATED)"))
        original = call([args.consumer, file, launch, terminal])
        # Reproduce an old collector's immutable unavailable observation over
        # the exact same source bytes, not a new execution or changed report.
        original.update(status="unavailable", reason="timing analysis: log line bound exceeded",
                        duration=None, producer=None, corners=[], metrics=[], timing_goal="unknown")
        del original["source_digest"]
        original["source_digest"] = hashlib.sha256(canonical(original).encode()).hexdigest()
        write(Path(str(file) + ".timing-analysis.json"), original)
        before = snapshot(d)
        cmd = [args.public, "inspect", file, "--domain", "timing", "--stage", "signoff", "--json"]
        assert not call(cmd)["rows"]
        refreshed = call(cmd + ["--refresh-timing"])
        lineage = refreshed["reinspection"]
        assert lineage["original_status"] == "unavailable" and lineage["original_timing_goal"] == "unknown"
        assert lineage["original_analysis_digest"] == original["source_digest"]
        assert lineage["recomputed_analysis"]["timing_goal"] == "fail"
        assert refreshed["analysis_digest"] == lineage["recomputed_analysis"]["source_digest"]
        row = next(r for r in refreshed["rows"] if r["value"] == -3.099803 and r["unit"] == "ns")
        source = json.loads(row["source"])
        assert source["file"] == dict(path=str(report_path), sha256=sha(report_path))
        assert source["evidence_scope"] == "read_only_reinspection_of_retained_sources_not_original_observation"
        detail = call(cmd + ["--refresh-timing", "--row-id", row["id"], "--expected-digest", refreshed["source_digest"]])
        assert "-3.099803" in detail["detail"] and detail["reinspection"] == lineage
        assert call(cmd + ["--refresh-timing", "--expected-digest", refreshed["analysis_digest"]]) == refreshed
        call(cmd + ["--refresh-timing", "--expected-digest", original["source_digest"]], expected=2)
        call([args.public, "inspect", file, "--domain", "physical", "--stage", "signoff", "--refresh-timing", "--json"], expected=2)
        assert call([args.report, "--target", file, "--json"])["timing_analysis"] == original
        assert snapshot(d) == before, "public reinspection changed original sources/observation"
        report_path.write_text("changed source")
        call(cmd + ["--refresh-timing"], expected=2)
        d = root / "cancel"; d.mkdir()
        file, launch, terminal, _ = fixture(d, outer="interrupted")
        assert call([args.consumer, file, launch, terminal])["status"] == "completed"
        # Never borrow another execution's observation, even when attempt labels match.
        copies = []
        for name in ("identity-a", "identity-b"):
            d = root / name; d.mkdir()
            file, launch, terminal, _ = fixture(d)
            call([args.consumer, file, launch, terminal])
            copies.append(file)
        Path(str(copies[1]) + ".timing-analysis.json").write_bytes(Path(str(copies[0]) + ".timing-analysis.json").read_bytes())
        call([args.report, "--target", copies[1], "--json"], expected=2)
        call([args.inspect, "--execution", copies[1], "--domain", "timing", "--stage", "signoff", "--json"], expected=2)
        capability = call([args.public, "--version", "--json"])
        assert capability["cli_contract"]["capabilities"]["timing_analysis1"] is True
        assert capability["cli_contract"]["capabilities"]["timing_reinspection1"]["read_only"] is True
    print("timing analysis: 26 native grammar cases, large native reports, cancellation, public report/index/detail, immutable source fencing and read-only capture passed")
    if args.retain_fixture:
        args.retain_fixture.mkdir()
        file, launch, terminal, _ = fixture(args.retain_fixture, case="negative")
        observation = call([args.consumer, file, launch, terminal])
        assert observation["status"] == "completed" and observation["timing_goal"] == "fail"
        print("Retained failed-signoff fixture: " + str(file))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("consumer", "inspect", "report", "public"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--retain-fixture", type=Path, help="Fresh persistent directory for an Agent B/C failed-signoff fixture")
    args = parser.parse_args()
    for name in ("consumer", "inspect", "report", "public"):
        setattr(args, name, getattr(args, name).resolve())
    main(args)
