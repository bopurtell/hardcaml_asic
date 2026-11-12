"""Testing-only launcher around real bridge + native API fixture, never EDA."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

bridge, path = map(Path, sys.argv[1:])
req = json.loads(path.read_text())
mode = os.environ.get("SERVICE_FIXTURE_MODE", "success")
validating = req["operation"] == "validate_publication"
if mode.startswith("transport_record_failure"):
    terminal = path.parent.parent / (req["tag"] + "-transport-result.json")
    terminal.mkdir()
if mode == "timeout" or (validating and mode == "validate_timeout"):
    time.sleep(60)
if mode in ("cancel", "transport_record_failure_cancel") or (validating and mode == "validate_cancel"):
    os.kill(os.getppid(), signal.SIGTERM)
    time.sleep(60)
if validating and mode == "validate_mismatch":
    os.environ["BRIDGE_FIXTURE_MODE"] = "validation_mismatch"
elif mode == "backend_failure":
    os.environ["BRIDGE_FIXTURE_MODE"] = "failure"
elif mode == "backend_signal":
    os.environ["BRIDGE_FIXTURE_MODE"] = "signal"
spec = importlib.util.spec_from_file_location("native_fixture", Path(__file__).with_name("bridge_protocol_fixture.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.run(bridge, path)
alias = os.environ.get("SRAM_ALIAS_MUTATION")
if alias and not validating and req["boundary"] == "placement":
    target = Path(alias)
    target.unlink()
    target.symlink_to(os.environ["SRAM_ALIAS_DESTINATION"], target_is_directory=True)
mutation = os.environ.get("SRAM_COLLATERAL_MUTATION")
if mutation and not validating and req["boundary"] == os.environ.get("SRAM_MUTATE_BOUNDARY", "placement"):
    target = Path(mutation)
    target.write_text(target.read_text() + " changed after fixture tool success")
mutation = os.environ.get("NAMED_INPUT_MUTATION")
if mutation and (not os.environ.get("NAMED_MUTATE_BOUNDARY") or req["boundary"] == os.environ["NAMED_MUTATE_BOUNDARY"]):
    target = Path(req["project"]) / mutation
    target.write_text("changed during backend call")
reply_path = Path(req["reply_path"])
reply = json.loads(reply_path.read_text())
if mode in ("exit17", "transport_record_failure_exit17") or (validating and mode == "validate_exit17"):
    sys.exit(17)  # completed/validated evidence must lose to actual exit
if mode == "signaled_after_reply":
    os.kill(os.getpid(), signal.SIGTERM)
if mode == "sigpipe_after_reply":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGPIPE)
if validating and mode == "validate_manifest_mutation":
    manifest = Path(req["candidate"]) / "manifest.json"
    raw = json.loads(manifest.read_text())
    raw["identity"] = "0" * 64
    manifest.write_text(json.dumps(raw))
if validating and mode == "validate_artifact_mutation":
    resolved = Path(req["candidate"]) / "resolved.json"
    resolved.write_text(resolved.read_text() + " ")
if mode == "missing_reply" or (validating and mode == "validate_missing_reply"):
    reply_path.unlink()
elif mode == "malformed_reply":
    reply_path.write_text('{"protocol_version":1,"protocol_version":1}')
elif mode in ("wrong_id", "wrong_request_hash", "wrong_version", "failed_reply", "artifact_hash", "artifact_bytes", "reply_extra") or (validating and mode == "validate_wrong_id"):
    if mode in ("wrong_id", "validate_wrong_id"):
        reply["request_id"] = "other"
    elif mode == "wrong_request_hash":
        reply["request_sha256"] = "0" * 64
    elif mode == "wrong_version":
        reply["protocol_version"] = 2
    elif mode == "failed_reply":
        reply.update(outcome="failed", error="fixture failure", artifacts={})
    elif mode == "reply_extra":
        reply["extra"] = 1
    else:
        artifact = next(iter(reply["artifacts"].values()))
        artifact["sha256" if mode == "artifact_hash" else "bytes"] = "0" * 64 if mode == "artifact_hash" else 0
    reply_path.write_text(json.dumps(reply))
elif not validating and mode.startswith("report_"):
    report_path = Path(req["candidate"]) / "segment.json"
    report = json.loads(report_path.read_text())
    if mode == "report_extra":
        report["extra"] = True
    elif mode == "report_steps":
        report["returned_steps"] = ["wrong"]
    elif mode == "report_metric_bool":
        report["metrics"]["finite"]["value"] = True
    elif mode == "report_metric_extra":
        report["metrics"]["finite"]["extra"] = 1
    elif mode == "report_nonfinite":
        report["metrics"]["finite"]["value"] = float("inf")
    elif mode == "report_source":
        report["source_boundary"] = "routing"
    elif mode == "report_boundary":
        report["boundary"] = "placement"
    report_path.write_text(json.dumps(report))
    data = report_path.read_bytes()
    reply["artifacts"]["segment.json"].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    reply_path.write_text(json.dumps(reply))
elif mode == "mutate_artifact":
    (Path(req["candidate"]) / "state.json").write_text("changed")
