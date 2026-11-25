"""Persistent lifecycle/reconnect evidence, including real local process signals."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PACKAGE_ROOT = os.environ.get("STAGE_TEST_PACKAGE_ROOT", str(Path(__file__).resolve().parents[1] / "flow"))
sys.path.insert(0, PACKAGE_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hardcaml_asic_flow import flow_observation as obs, execution_plan as plans, stages, result_store
from hardcaml_asic_flow import presentation
import test_stages


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_stages.StagesTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.writer = obs.Writer(self.root / "observations", workspace=self.root / "workspace",
                                 checkpoint_store=self.fixture.store, request={"fixture": True})

    def tearDown(self):
        self.writer.close()
        self.fixture.tearDown()

    def run_plan(self, endpoint="floorplan", **kwargs):
        return self.writer.run(lambda: plans.resolve_stages(endpoint), lambda: self.fixture.adapter(**kwargs))

    def test_recording_clock_regression_and_monotonic_origin_before_promotion(self):
        self.writer.close()
        stamps = ["2026-10-13T12:00:01.123456Z", "2026-10-13T11:59:59.000000Z",
                  "2026-10-13T12:00:02.000000Z"]
        save = result_store.save_json
        visible = []
        def promote(path, value):
            self.assertFalse(path.exists())
            visible.append((value["recorded_at"], value["elapsed_seconds"]))
            save(path, value)
        with patch.object(obs.time, "monotonic", side_effect=[100, 100.25, 101, 101]), \
             patch.object(obs, "recorded_at_now", side_effect=stamps), \
             patch.object(result_store, "save_json", side_effect=promote):
            self.writer = obs.Writer(self.root / "clock", workspace=self.root / "clock-work",
                                     checkpoint_store=self.fixture.store, request={})
            with patch.object(obs.time, "monotonic", return_value=100.1), \
                 patch.object(obs, "recorded_at_now", return_value=stamps[0]):
                with self.assertRaisesRegex(obs.ObservationError, "regressed"):
                    self.writer.append("planned", {"plan": plans.resolve_stages("synthesis").as_dict()})
            self.assertEqual(self.writer.sequence, 1)
            self.assertFalse((self.writer.directory / "000000000002.json").exists())
            self.writer.append("planned", {"plan": plans.resolve_stages("synthesis").as_dict()})
            self.writer.append("outcome", {"status": "failed", "exit_code": 2, "error": None})
        events = obs.load(self.writer.directory)[1]
        self.assertEqual(visible, list(zip(stamps, [.25, 1, 1])))
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3])
        reader = obs.IncrementalReader(self.writer.directory, stream_id=self.writer.stream_id, after=1)
        self.assertEqual(reader.poll()[0]["events"], events[1:])
        output = []
        presentation.observation_updates(obs.read_updates(self.writer.directory, stream_id=self.writer.stream_id),
                                         path=self.writer.directory, output=output.append)
        self.assertIn("recorded " + stamps[1] + "; +1.000s", "\n".join(output))

    def test_strict_timing_validation_even_when_rehashed(self):
        # strftime %Y is not four-digit padded on every platform for years <1000.
        from datetime import datetime, timezone
        with patch.object(obs, "datetime") as clock:
            clock.now.return_value = datetime(1, 1, 1, tzinfo=timezone.utc)
            self.assertEqual(obs.recorded_at_now(), "0001-01-01T00:00:00.000000Z")
        for stamp in ("0001-01-01T00:00:00.000000Z", "2024-02-29T23:59:59.999999Z"):
            obs.validate_timing({"recorded_at": stamp, "elapsed_seconds": 0})
        path = self.writer.directory / "000000000001.json"
        original = obs.strict_read(path)
        bad_stamps = [None, 123, "2026-10-13T12:00:00Z", "2026-10-13T12:00:00.000000+00:00",
                      "2026-02-30T12:00:00.000000Z", "2026-10-13T12:00:60.000000Z",
                      "0000-01-01T00:00:00.000000Z", "2026-02-29T00:00:00.000000Z",
                      "2026-10-13t12:00:00.000000z", "2026-10-13T12:00:00.000000Z\n"]
        for key, values in (("recorded_at", bad_stamps),
                            ("elapsed_seconds", [None, True, "1", -1, float("inf"), float("nan"), 10**400])):
            for bad in values:
                with self.subTest(key=key, bad=bad):
                    value = dict(original, **{key: bad})
                    # Nonfinite JSON fails at decoding/canonicalization; all finite
                    # mutations have a valid hash so validation must reject facts.
                    try:
                        value["sha256"] = obs.digest({k: v for k, v in value.items() if k != "sha256"})
                    except ValueError:
                        pass
                    path.write_text(json.dumps(value))
                    with self.assertRaises(obs.ObservationError):
                        obs.load(self.writer.directory)
        for mutation in ({k: v for k, v in original.items() if k != "recorded_at"},
                         {k: v for k, v in original.items() if k != "elapsed_seconds"},
                         dict(original, schema_version=1)):
            mutation["sha256"] = obs.digest({k: v for k, v in mutation.items() if k != "sha256"})
            path.write_text(json.dumps(mutation))
            with self.assertRaisesRegex(obs.ObservationError, "record fields"):
                obs.load(self.writer.directory)
        path.write_text(json.dumps(original))

    def test_timing_hash_mixed_schema_and_elapsed_regression(self):
        self.writer.append("outcome", {"status": "failed", "exit_code": 2, "error": None})
        events = obs.load(self.writer.directory)[1]
        # Fixed elapsed values make the malformed-chain cases clock-independent.
        for index, event in enumerate(events):
            event.update(elapsed_seconds=index + 1,
                         previous_sha256=events[index - 1]["sha256"] if index else None)
            event["sha256"] = obs.digest({k: v for k, v in event.items() if k != "sha256"})
            (self.writer.directory / f"{index + 1:012d}.json").write_text(json.dumps(event))
        path = self.writer.directory / "000000000002.json"
        for key, value in (("recorded_at", "2026-01-01T00:00:00.000000Z"), ("elapsed_seconds", 999)):
            path.write_text(json.dumps(dict(events[1], **{key: value})))
            with self.assertRaisesRegex(obs.ObservationError, "chain mismatch"):
                obs.load(self.writer.directory)
        for mutation in (dict(events[1], elapsed_seconds=0),
                         {k: v for k, v in dict(events[1], schema_version=1).items()
                          if k not in ("recorded_at", "elapsed_seconds")}):
            path.unlink()
            reader = obs.IncrementalReader(self.writer.directory, stream_id=self.writer.stream_id, after=1)
            mutation["sha256"] = obs.digest({k: v for k, v in mutation.items() if k != "sha256"})
            path.write_text(json.dumps(mutation))
            with self.assertRaises(obs.ObservationError):
                obs.load(self.writer.directory)
            with self.assertRaises(obs.ObservationError):
                reader.poll()
        allocation = {k: v for k, v in dict(events[0], schema_version=1).items()
                      if k not in ("recorded_at", "elapsed_seconds")}
        allocation["sha256"] = obs.digest({k: v for k, v in allocation.items() if k != "sha256"})
        (self.writer.directory / "000000000001.json").write_text(json.dumps(allocation))
        tail = dict(events[1], previous_sha256=allocation["sha256"])
        tail["sha256"] = obs.digest({k: v for k, v in tail.items() if k != "sha256"})
        path.write_text(json.dumps(tail))
        with self.assertRaisesRegex(obs.ObservationError, "mixed"):
            obs.load(self.writer.directory)

    def test_legacy_journal_exact_payloads_no_synthesized_timing(self):
        self.run_plan("synthesis")
        self.writer.close()
        expected_state = obs.load(self.writer.directory)[0]
        expected_inspect = dict(obs.inspect(self.writer.directory), schema_version=1)
        events = []
        previous = None
        for path in sorted(self.writer.directory.glob("[0-9]*.json")):
            value = obs.strict_read(path)
            value.pop("recorded_at")
            value.pop("elapsed_seconds")
            value.update(schema_version=1, previous_sha256=previous)
            value["sha256"] = obs.digest({k: v for k, v in value.items() if k != "sha256"})
            path.write_text(json.dumps(value))
            previous = value["sha256"]
            events.append(value)
        before = {p.name: p.read_bytes() for p in self.writer.directory.iterdir()}
        expected = {"contract": obs.CONTRACT, "schema_version": 1, "stream_id": self.writer.stream_id,
                    "after": 2, "cursor": len(events), "events": events[2:]}
        self.assertEqual(obs.read_updates(self.writer.directory, stream_id=self.writer.stream_id, after=2), expected)
        reader = obs.IncrementalReader(self.writer.directory, stream_id=self.writer.stream_id, after=2)
        updates, status = reader.poll()
        self.assertEqual(updates, expected)
        self.assertEqual(status["schema_version"], 1)
        inspected = obs.inspect(self.writer.directory)
        self.assertEqual(inspected, expected_inspect)
        self.assertEqual(inspected["schema_version"], 1)
        self.assertEqual(inspected["state"], expected_state)
        entry = "import sys; sys.path.insert(0, sys.argv.pop(1)); from hardcaml_asic_flow.cli import main; sys.exit(main())"
        command = [sys.executable, "-I", "-B", "-c", entry, PACKAGE_ROOT,
                    "observation", "watch", str(self.writer.directory), "--stream-id", self.writer.stream_id, "--after", "2"]
        if os.environ.get("PUBLIC_FLOW_CLI"):
            command = [os.environ["PUBLIC_FLOW_CLI"], *command[6:]]
        watched = subprocess.run([*command, "--json"], capture_output=True, text=True, timeout=10)
        self.assertEqual(watched.returncode, 0, watched.stderr)
        envelopes = [json.loads(line) for line in watched.stdout.splitlines()]
        self.assertEqual(envelopes[0], dict(expected, event="updates"))
        self.assertEqual(envelopes[1], dict(status, event="status"))
        human = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertNotIn("[recorded", human.stdout)
        output = []
        presentation.observation_updates(updates, path=self.writer.directory, output=output.append)
        self.assertNotIn("[recorded", "\n".join(output))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.writer.directory.iterdir()})

    def test_publication_precedes_finished_and_replay_reconnect(self):
        result = self.run_plan()
        state, events = obs.load(self.writer.directory)
        self.assertEqual([e["event"] for e in events], ["allocated", "planned", "prepared"] + ["started", "published", "finished"] * 2 + ["outcome"])
        for event in events:
            if event["event"] == "published":
                self.assertEqual(event["data"]["occurrence"]["status"], "running")
                artifact = event["data"]["artifact"]
                test_stages.cp.validate(artifact["path"], compatibility=self.fixture.compat)
        for cursor in range(len(events) + 1):
            updates = obs.read_updates(self.writer.directory, stream_id=result.attempt_id, after=cursor)
            self.assertEqual(updates["events"], events[cursor:])
        self.assertEqual(state["artifacts"], result.artifacts)
        before = {p.name: p.read_bytes() for p in self.writer.directory.iterdir()}
        observation = obs.inspect(self.writer.directory)
        self.assertEqual(observation["final_results"]["acceptance"], "not_evaluated")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.writer.directory.iterdir()})

    def test_seven_stage_physical_completion_does_not_mean_acceptance(self):
        self.run_plan("signoff")
        final = obs.inspect(self.writer.directory)["final_results"]
        self.assertEqual(final["physical_completion"], "completed")
        self.assertEqual([ref["boundary"] for ref in final["checkpoint_refs"]],
                         list(test_stages.EXPECTED_STAGE_SEQUENCE))
        self.assertEqual(final["acceptance"], "not_evaluated")
        self.assertEqual(final["external_postcheck"], "not_evaluated")

    def test_process_failure_and_publication_failure_preserve_unreached(self):
        result = self.run_plan(missing=True)
        self.assertEqual(result.exit_code, 2)
        state = obs.inspect(self.writer.directory)["state"]
        self.assertEqual([o["status"] for o in state["occurrences"]], ["failed", "unreached"])
        self.assertEqual(state["artifacts"], [])

    def test_preflight_failure_preserves_report_before_workspace_exists(self):
        def prepare():
            return stages.prepare(bundle=self.root / "bundle", support_tools=self.root / "tt", pdk_root=self.root / "pdk",
                                  python=sys.executable, workspace=self.root / "workspace", checkpoint_store=self.root / "workspace/checkpoints",
                                  preflight_output=self.writer.directory / "preflight.json")
        with patch.object(stages.coarse_flow_oracle, "preflight", return_value={"ready": False, "issues": ["fixture unavailable"]}):
            with self.assertRaises(ValueError):
                self.writer.run(lambda: plans.resolve_stages("floorplan"), prepare)
        state = obs.inspect(self.writer.directory)["state"]
        self.assertEqual(state["outcome"]["status"], "failed")
        self.assertEqual([o["status"] for o in state["occurrences"]], ["unreached", "unreached"])
        self.assertEqual(json.loads(Path(state["preflight_record"]).read_text())["issues"], ["fixture unavailable"])
        self.assertFalse((self.root / "workspace").exists())

    def test_invalid_plan_and_adapter_validation_are_final_failures(self):
        with self.assertRaises(ValueError):
            self.writer.run(lambda: plans.resolve_stages("unknown"), lambda: None)
        self.assertIsNone(obs.inspect(self.writer.directory)["state"]["plan"])
        self.assertEqual(obs.inspect(self.writer.directory)["state"]["outcome"]["status"], "failed")

    def test_workspace_conflict_across_different_stores(self):
        with self.assertRaises(obs.ObservationError) as caught:
            obs.Writer(self.root / "other", workspace=self.root / "workspace", checkpoint_store=self.fixture.store, request={})
        self.assertEqual(caught.exception.code, "BUSY")
        self.assertFalse((self.root / "other").exists())

    def test_temporary_tail_ignored_promoted_corruption_and_gaps_rejected(self):
        (self.writer.directory / ".000000000002.json.incomplete").write_text('{"status":"completed"')
        self.assertEqual(obs.inspect(self.writer.directory)["cursor"], 1)
        bad = self.writer.directory / "000000000002.json"
        for raw in ('{"status":"completed"', '{"x":1,"x":2}', '{"x":NaN}'):
            bad.write_text(raw)
            with self.assertRaises(obs.ObservationError):
                obs.inspect(self.writer.directory)
        bad.unlink()
        (self.writer.directory / "000000000003.json").write_text("{}")
        with self.assertRaises(obs.ObservationError):
            obs.inspect(self.writer.directory)

    def test_cursor_and_hash_chain_reject_bad_records(self):
        self.run_plan()
        for stream, cursor in (("other", 0), (self.writer.stream_id, -1), (self.writer.stream_id, 100), (self.writer.stream_id, True)):
            with self.assertRaises(obs.ObservationError) as caught:
                obs.read_updates(self.writer.directory, stream_id=stream, after=cursor)
            self.assertEqual(caught.exception.code, "RESET_REQUIRED")
        path = self.writer.directory / "000000000004.json"
        value = json.loads(path.read_text())
        value["data"]["occurrence"]["status"] = "completed"
        path.write_text(json.dumps(value))
        with self.assertRaises(obs.ObservationError):
            obs.inspect(self.writer.directory)

    def test_transition_validation_rejects_rehashed_unearned_success(self):
        state = self.writer.state
        with self.assertRaises(obs.ObservationError):
            obs.reduce_event(state, "outcome", {"status": "completed", "exit_code": 0, "error": None}, self.writer.stream_id)

    def test_final_record_publication_failure_is_not_success(self):
        with self.assertRaises(OSError):
            self.writer.run(lambda: plans.resolve_stages("synthesis"), lambda: self.fixture.adapter(),
                            finalize=lambda _: (_ for _ in ()).throw(OSError("execution record unavailable")))
        state = obs.inspect(self.writer.directory)["state"]
        self.assertEqual(state["occurrences"][0]["status"], "completed")
        self.assertEqual(state["outcome"]["status"], "failed")

    def subprocess_case(self, mode, signum):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, STAGE_TEST_PACKAGE_ROOT=PACKAGE_ROOT, PYTHONDONTWRITEBYTECODE="1")
            child = subprocess.Popen([sys.executable, "-B", __file__, "--worker", root, mode], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                attempt = Path(child.stdout.readline().strip())
                target = "published" if mode == "publication" else "started" if mode == "stage" else "planned"
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state, events = obs.load(attempt)
                    if events[-1]["event"] == target and (mode != "preflight" or Path(state["preflight_record"]).exists()):
                        break
                    time.sleep(.02)
                else:
                    self.fail("child did not reach requested lifecycle boundary")
                prefix = len(events)
                stream = state["attempt_id"]
                # Reader disconnect/reattach occurs through an independent process.
                command = [sys.executable, "-B", "-m", "hardcaml_asic_flow", "observation", "inspect", str(attempt), "--json"]
                if os.environ.get("PUBLIC_FLOW_CLI"):
                    command = [os.environ["PUBLIC_FLOW_CLI"], *command[4:]]
                read_env = dict(env, PYTHONPATH=PACKAGE_ROOT)
                live = subprocess.run(command, env=read_env, text=True, capture_output=True, check=True)
                self.assertTrue(json.loads(live.stdout)["writer_active"])
                with self.assertRaises(obs.ObservationError) as conflict:
                    obs.Writer(Path(root) / "other-store", workspace=Path(root) / "workspace",
                               checkpoint_store=Path(root) / "checkpoints", request={})
                self.assertEqual(conflict.exception.code, "BUSY")
                child.send_signal(signum)
                child.communicate(timeout=10)
                reattached = json.loads(subprocess.run(command, env=read_env, text=True, capture_output=True, check=True).stdout)
                self.assertFalse(reattached["writer_active"])
                final = reattached["state"]
                if signum == signal.SIGKILL:
                    self.assertIsNone(final["outcome"])
                    self.assertEqual(reattached["observation_status"], "writer_lost_or_unknown")
                    self.assertEqual(final["occurrences"][0]["status"], "running")
                    self.assertEqual(len(final["artifacts"]), 1 if mode == "publication" else 0)
                else:
                    self.assertEqual(final["outcome"]["status"], "interrupted")
                    self.assertEqual(final["outcome"]["exit_code"], 128 + signum)
                    self.assertEqual(final["occurrences"][0]["status"], "unreached" if mode == "preflight" else "interrupted")
                    if mode == "preflight":
                        self.assertEqual(json.loads(Path(final["preflight_record"]).read_text())["diagnostic"]["status"], "interrupted")
                updates = obs.read_updates(attempt, stream_id=stream, after=prefix)
                self.assertEqual(updates["cursor"], reattached["cursor"])
                self.assertEqual(reattached["final_results"]["acceptance"], "not_evaluated")
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
                for path in Path(root).rglob("*"):
                    path.chmod(0o755 if path.is_dir() else 0o644)

    def test_real_sigterm_stage_and_restart_reader(self):
        self.subprocess_case("stage", signal.SIGTERM)

    def test_real_sigterm_long_preparation_preserved(self):
        self.subprocess_case("preflight", signal.SIGTERM)

    def test_real_sigkill_after_publication_never_completes_stage(self):
        self.subprocess_case("publication", signal.SIGKILL)

    def test_real_sigterm_after_publication_retains_artifact_and_interruption(self):
        self.subprocess_case("publication", signal.SIGTERM)

    def test_atomic_write_failure_leaves_only_committed_prefix(self):
        original = result_store.save_json
        def save(path, value):
            if value.get("event") in ("finished", "outcome"):
                raise OSError("journal publication unavailable")
            original(path, value)
        with patch.object(result_store, "save_json", save):
            with self.assertRaises(OSError):
                self.run_plan()
        state, events = obs.load(self.writer.directory)
        self.assertEqual(events[-1]["event"], "published")
        self.assertEqual(state["occurrences"][0]["status"], "running")
        self.assertIsNone(state["outcome"])

    def test_released_writer_cannot_append_and_new_writer_does_not_revive_old(self):
        self.writer.close()
        with self.assertRaises(obs.ObservationError):
            self.writer.append("planned", {"plan": plans.resolve_stages("synthesis").as_dict()})
        other = obs.Writer(self.root / "other", workspace=self.root / "workspace", checkpoint_store=self.fixture.store, request={})
        try:
            self.assertFalse(obs.inspect(self.writer.directory)["writer_active"])
            self.assertTrue(obs.inspect(other.directory)["writer_active"])
        finally:
            other.close()


def worker(root, mode):
    fixture = test_stages.StagesTest()
    temporary_directory = tempfile.TemporaryDirectory
    with patch.object(test_stages.tempfile, "TemporaryDirectory", lambda: temporary_directory(dir=root)):
        fixture.setUp()
    writer = obs.Writer(Path(root) / "observations", workspace=Path(root) / "workspace",
                        checkpoint_store=fixture.store, request={"fixture": True})
    print(writer.directory, flush=True)
    def prepare():
        if mode == "preflight":
            checked = result_store.durable_preflight(Path(root) / "bundle", writer.directory / "preflight.json", lambda: time.sleep(60))
            if checked.status == "interrupted":
                raise result_store.FlowInterrupted(checked.exit_code - 128)
            return None
        adapter = fixture.adapter()
        if mode == "stage":
            adapter.launch = lambda *args: time.sleep(60)
        return adapter
    if mode == "publication":
        original = writer.lifecycle
        def lifecycle(event, *args):
            original(event, *args)
            if event == "published":
                time.sleep(60)
        writer.lifecycle = lifecycle
    try:
        writer.run(lambda: plans.resolve_stages("floorplan"), prepare)
    finally:
        writer.close()
        fixture.tearDown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
