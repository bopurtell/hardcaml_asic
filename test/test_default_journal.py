"""CLI4 lifecycle at the public subprocess boundary; real OCaml control, no EDA."""
import contextlib
import json
from pathlib import Path
import os
import signal
import tempfile
import time
import unittest
import sys
import io
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from public_cli_fixture import Fixture
from hardcaml_asic_flow import flow_observation as obs, execution_plan as ep, presentation, cli, ocaml_backend


class DefaultJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_stage(self, extra=(), endpoint="synthesis"):
        result = self.fixture.invoke([*self.fixture.command(endpoint), "--json", *extra])
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_default_override_resume_and_plan(self):
        result, (allocation, execution) = self.run_stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(allocation["attempt"]).parent, self.root / "observations")
        self.assertEqual(execution, json.loads((self.fixture.workspace / ("execution-" + execution["attempt_id"] + ".json")).read_text()))
        checkpoint = execution["artifacts"][0]["path"]
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(ep.resolve_sequence(["floorplan"], initial_boundary="synthesis").as_dict()))
        command = self.fixture.command()
        index = command.index("--endpoint")
        command[index:index+2] = ["--plan", "plan.json"]
        result = self.fixture.invoke([*command, "--checkpoint", checkpoint, "--observation-store", "override", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        resumed, execution = map(json.loads, result.stdout.splitlines())
        self.assertNotEqual(resumed["stream_id"], allocation["stream_id"])
        self.assertEqual(Path(resumed["attempt"]).parent, self.root / "override")
        self.assertEqual(execution["plan"]["initial_boundary"], "synthesis")
        self.assertFalse(obs.inspect(resumed["attempt"])["writer_active"])

    def test_workspace_named_stage_preflight(self):
        self.fixture.workspace = self.root / "stage-preflight"
        self.fixture.store = self.fixture.workspace / "checkpoints"
        result, (allocation, execution) = self.run_stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(execution["status"], "completed")
        receipts = list((self.root / "stage-preflight-evidence/operations").glob("*/operation.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        local = receipts[0].parent / receipt["report"]
        state, _ = obs.load(allocation["attempt"])
        self.assertEqual(local.read_bytes(), Path(state["preflight_record"]).read_bytes())
        requests = list((self.fixture.workspace / "project").glob("*-request.json"))
        physical_ids = {json.loads(p.read_text())["request_id"] for p in requests}
        records = [json.loads(p.read_text()) for p in self.fixture.workspace.glob("*-transport.json")]
        self.assertEqual({r["request_id"] for r in records}, physical_ids)
        events = [json.loads(line) for line in (self.root / "engine-events.jsonl").read_text().splitlines()]
        names = {r["container_name"] for r in records}
        self.assertEqual(len([e for e in events if e[:2] == ["rm", "-f"] and e[2] in names]), len(names))

    def test_invalid_plan_before_probes_and_preflight_failure(self):
        (self.root / "invalid.json").write_text("{}")
        command = self.fixture.command()
        index = command.index("--endpoint")
        command[index:index+2] = ["--plan", "invalid.json"]
        result = self.fixture.invoke([*command, "--json"])
        allocation, terminal = map(json.loads, result.stdout.splitlines())
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(terminal["observation"]["state"]["plan"])
        self.assertFalse((self.root / "preparation-events.jsonl").exists())
        self.fixture.env["PREPARATION_MODE"] = "host_mismatch"
        result, (allocation, terminal) = self.run_stage()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(terminal["observation"], obs.inspect(allocation["attempt"]))
        self.assertIsNone(terminal["execution_file"])
        self.assertFalse(self.fixture.workspace.exists())
        self.fixture.env["PREPARATION_MODE"] = "probe_failure"
        result, (allocation, terminal) = self.run_stage()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(terminal["exit_code"], 2)

    def test_missing_parent_busy_and_escape_hatch(self):
        command = self.fixture.command()
        command[command.index("--workspace")+1] = "missing/workspace"
        result = self.fixture.invoke([*command, "--json"])
        terminal, = map(json.loads, result.stdout.splitlines())
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(terminal["attempt"])
        with contextlib.closing(obs.Writer(self.root / "other", workspace=self.fixture.workspace,
                                          checkpoint_store=self.fixture.store, request={})):
            result, (terminal,) = self.run_stage()
            self.assertEqual(terminal["error"]["code"], "BUSY")
        result, (execution,) = self.run_stage(["--no-observation"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("occurrences", execution)
        self.assertFalse((self.root / "observations").exists())
        result = self.fixture.invoke([*self.fixture.command(), "--no-observation", "--observation-store", "other"])
        self.assertEqual(result.returncode, 2)

    def test_storage_failure_and_publication_preserved(self):
        self.fixture.env["JOURNAL_COLLISION"] = "1"
        result, (allocation, terminal) = self.run_stage()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(terminal["recording_error"])
        execution = json.loads(Path(terminal["execution_file"]).read_text())
        self.assertEqual(len(execution["artifacts"]), 1)
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(Path(allocation["attempt"], "000000000005.json").read_text(), "retained conflict")

    def test_aggregate_storage_failure(self):
        self.fixture.env["EXECUTION_STORAGE_FAULT"] = "1"
        result, (allocation, terminal) = self.run_stage()
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(terminal["execution_file"])
        self.assertEqual(terminal["observation"]["state"]["outcome"]["status"], "failed")
        self.assertEqual(len(terminal["observation"]["state"]["artifacts"]), 1)

    def test_cli4_timeout_and_publication_exception_exit_semantics(self):
        for setting in ("SERVICE_EXECUTION_SLEEP", "SERVICE_VALIDATION_EXIT"):
            with tempfile.TemporaryDirectory() as tmp:
                fixture = Fixture(tmp)
                fixture.env[setting] = "10" if setting.endswith("SLEEP") else "17"
                if setting == "SERVICE_VALIDATION_EXIT":
                    fixture.env["BRIDGE_FIXTURE_RUNTIME_FILE"] = str(next(p for p in Path("/nix/store").iterdir() if p.is_file()))
                result = fixture.invoke([*fixture.command(), "--json"])
                self.assertEqual(result.returncode, 2, result.stderr)
                allocation, execution = map(json.loads, result.stdout.splitlines())
                self.assertEqual(execution["exit_code"], 2)
                self.assertEqual(execution["status"], "failed")
                self.assertFalse(execution.get("artifacts"))
                state = obs.inspect(allocation["attempt"])
                self.assertEqual(state["state"]["outcome"]["exit_code"], 2)
                calls = [json.loads(line) for line in (Path(tmp) / "engine-events.jsonl").read_text().splitlines()]
                self.assertTrue(any(call[:2] == ["rm", "-f"] for call in calls))
                for call in calls:
                    if call[0] == "run":
                        self.assertIn("--cpus=6", call)
                        self.assertIn("_OPENLANE_MAX_CORES=6", call)
                        self.assertIn("OPENROAD_THREADS=6", call)
                        self.assertIn("OMP_NUM_THREADS=6", call)

    def test_sigint_sigterm_forwarded_from_public_pid(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with tempfile.TemporaryDirectory() as tmp:
                fixture = Fixture(tmp, "probe_timeout")
                process = fixture.invoke([*fixture.command(), "--json"], live=True)
                try:
                    allocation = json.loads(process.stdout.readline())
                    deadline = time.monotonic() + 10
                    while not (Path(tmp) / "preparation-events.jsonl").exists():
                        self.assertIsNone(process.poll())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(.01)
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 128 + signum, stderr)
                    terminal = json.loads(stdout.splitlines()[-1])
                    self.assertEqual(terminal["status"], "interrupted")
                    self.assertEqual(terminal["observation"], obs.inspect(allocation["attempt"]))
                    self.assertFalse(terminal["observation"]["writer_active"])
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

    def test_broken_allocation_output_releases_lock(self):
        read, write = os.pipe()
        os.close(read)
        try:
            result = self.fixture.invoke([*self.fixture.command(), "--json"], stdout=write)
        finally:
            os.close(write)
        self.assertEqual(result.returncode, 2, result.stderr)
        attempt, = (self.root / "observations").iterdir()
        state = obs.inspect(attempt)
        self.assertFalse(state["writer_active"])
        self.assertIsNone(state["state"]["plan"])
        self.assertEqual(state["state"]["outcome"]["status"], "failed")

    def test_human_output_exact_renderer_diff(self):
        result = self.fixture.invoke(self.fixture.command("floorplan"))
        self.assertEqual(result.returncode, 0, result.stderr)
        attempt, = (self.root / "observations").iterdir()
        state = obs.inspect(attempt)
        execution = json.loads(next(self.fixture.workspace.glob("execution-*.json")).read_text())
        # Render each line with the same print newline convention.
        lines = []
        presentation.allocation({"event": "observation_allocated", "attempt": str(attempt), "stream_id": state["stream_id"]}, output=lines.append)
        presentation.stage_execution(execution, workspace=self.fixture.workspace, output=lines.append)
        self.assertEqual(result.stdout, "\n".join(lines) + "\n")
        self.assertEqual(result.stderr, "")

    def test_reader_payload_parity_and_cursor_negatives(self):
        result, (allocation, execution) = self.run_stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        attempt = allocation["attempt"]
        command = ["observation", "inspect", attempt, "--json"]
        result = self.fixture.invoke(command)
        self.assertEqual(json.loads(result.stdout), obs.inspect(attempt))
        command = ["observation", "read-updates", attempt, "--json", "--stream-id", allocation["stream_id"], "--after", "0"]
        result = self.fixture.invoke(command)
        self.assertEqual(json.loads(result.stdout), obs.read_updates(attempt, stream_id=allocation["stream_id"], after=0))
        for after in ("-1", "999999999999999999999999999999999"):
            result = self.fixture.invoke([*command[:-1], after])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "RESET_REQUIRED")
        checkpoint = execution["artifacts"][0]["path"]
        result = self.fixture.invoke(["checkpoint-inspect", checkpoint, "--restart-to", "floorplan", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["manifest"], json.loads(Path(checkpoint, "manifest.json").read_text()))
        result = self.fixture.invoke(["checkpoint-inspect", checkpoint, "--restart-to", "signoff", "--json"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stdout))

    def test_parser_defaults_and_strict_options(self):
        args = cli.build_parser().parse_args(self.fixture.command())
        self.assertEqual(args.timeout, 600 if "--timeout" not in self.fixture.command() else 2)
        command = self.fixture.command()
        index = command.index("--timeout")
        del command[index:index+2]
        self.assertEqual(cli.build_parser().parse_args(command).timeout, 600)
        self.assertFalse(cli.build_parser().parse_args(command).no_observation)
        for extra in (["--source", "synthesis"], ["--endpoint", "unknown"], ["--plan", "missing.json"], ["--timeout", "1.5"]):
            result = self.fixture.invoke([*command, *extra])
            self.assertEqual(result.returncode, 2)
            self.assertFalse((self.root / "observations").exists())
        missing_package = self.root / "prefix/lib/hardcaml_asic/flow/hardcaml_asic_flow"
        stderr = io.StringIO()
        with patch.object(ocaml_backend, "PACKAGE", missing_package), contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(command, cwd=self.root), 2)
        self.assertIn("OCaml backend is missing", stderr.getvalue())
        self.assertIn("Reinstall", stderr.getvalue())
        self.assertFalse((self.root / "observations").exists())


if __name__ == "__main__":
    unittest.main()
