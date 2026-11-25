"""Coarse plan contracts and shared execution, including real result services.

Synthetic subprocess/preflight boundaries do not prove physical ASIC execution.
"""

from dataclasses import replace
import json
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
from hardcaml_asic_flow import cli, execution_plan as plans, coarse_flow_oracle, runner
import test_coarse_flow
from test_flow_cli import FakeOperations


class PlanTest(unittest.TestCase):
    def test_roundtrip_labels_are_not_identity_or_runtime(self):
        for endpoint in ("synthesis", "full"):
            plan = plans.resolve(endpoint, archive=True)
            encoded = json.loads(json.dumps(plan.as_dict()))
            self.assertEqual(plans.Plan.from_dict(encoded), plan)
            encoded["actions"][0]["label"] = "A translated display label"
            translated = plans.Plan.from_dict(encoded)
            self.assertEqual(translated.actions[0].key, plan.actions[0].key)
            self.assertNotIn("run_id", encoded)
            self.assertNotIn("execution_binding", encoded)

    def test_invalid_contracts_cannot_change_execution(self):
        for field, value in (("key", "other"), ("operation", "arbitrary"),
                             ("enabled", False), ("gate", "always"),
                             ("requires", ["retention.archive"]),
                             ("outputs", ["checkpoint"])):
            with self.subTest(field=field):
                encoded = plans.resolve("full").as_dict()
                encoded["actions"][0][field] = value
                with self.assertRaises(ValueError):
                    plans.Plan.from_dict(encoded)
        for value in ({}, [], {"schema_version": 1, "endpoint": "full", "actions": []}):
            with self.assertRaises(ValueError):
                plans.Plan.from_dict(value)
        with self.assertRaises(ValueError):
            plans.resolve("routing")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.operations = FakeOperations()
        self.args = SimpleNamespace(stage="full", archive=Path("archive"),
                                    force=False, verbose=False)

    def execute(self, **kwargs):
        plan = kwargs.pop("plan", plans.resolve(self.args.stage, archive=self.args.archive is not None))
        return runner.execute(plan, runner.ServiceAdapter(self.args, self.operations), **kwargs)

    def test_sequence_handoffs_and_occurrence_identities(self):
        first = self.execute()
        self.assertEqual([name for name, _ in self.operations.calls],
                         ["run", "postcheck", "collect", "report", "archive"])
        self.assertEqual(self.operations.called("postcheck")[0].run_dir,
                         self.operations.run_outcome.run_dir)
        self.assertFalse(hasattr(self.args, "run_dir"))
        self.assertTrue(all(item.status == "completed" for item in first.occurrences))
        self.assertEqual(first.occurrences[3].verdict, "accepted")
        plan = replace(first.plan, actions=tuple(replace(action, label="same label")
                                                for action in first.plan.actions))
        second = self.execute(plan=plan)
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.assertEqual([item.action_key for item in first.occurrences],
                         [item.action_key for item in second.occurrences])
        self.assertTrue(set(item.occurrence_id for item in first.occurrences).isdisjoint(
                        item.occurrence_id for item in second.occurrences))
        self.assertEqual(json.loads(json.dumps(first.as_dict()))["run"]["run_id"], "run-1")

    def test_skipped_and_unreached_are_distinct(self):
        self.args.stage, self.args.archive = "synthesis", None
        self.operations.run_outcome.status, self.operations.run_outcome.exit_code = "failed", 1
        result = self.execute()
        self.assertEqual([item.status for item in result.occurrences],
                         ["failed", "skipped", "completed", "unreached", "skipped"])
        self.assertTrue(result.occurrences[2].best_effort)
        self.assertEqual((result.status, result.exit_code), ("failed", 1))

    def test_failure_and_signal_do_not_get_promoted_by_collection(self):
        for failure in (ValueError("check broke"), coarse_flow_oracle.FlowInterrupted(signal.SIGTERM),
                        KeyboardInterrupt()):
            for collect in (0, 7, OSError("collection broke"), coarse_flow_oracle.FlowInterrupted(signal.SIGINT)):
                with self.subTest(failure=failure, collect=collect):
                    self.operations = FakeOperations()
                    self.operations.postcheck = mock.Mock(side_effect=failure)
                    self.operations.collect = mock.Mock(**(
                        {"side_effect": collect} if isinstance(collect, Exception) else {"return_value": collect}))
                    result = self.execute()
                    expected = 130 if isinstance(failure, KeyboardInterrupt) else (
                        143 if hasattr(failure, "signum") else 2)
                    self.assertEqual(result.exit_code, expected)
                    self.operations.collect.assert_called_once()
                    self.assertTrue(result.occurrences[2].best_effort)
                    self.assertEqual(result.occurrences[3].status, "unreached")
                    self.assertEqual(result.occurrences[4].status, "unreached")

    def test_rejection_is_a_verdict_not_process_failure_or_acceptance(self):
        self.operations.codes["report"] = 1
        result = self.execute()
        self.assertEqual((result.status, result.exit_code), ("rejected", 1))
        self.assertEqual(result.occurrences[3].status, "completed")
        self.assertEqual(result.occurrences[3].verdict, "rejected")
        self.assertEqual(result.occurrences[4].status, "unreached")
        self.assertFalse(self.operations.called("archive"))

    def test_preallocation_failure_has_no_collectable_run(self):
        self.operations.run = mock.Mock(side_effect=FileNotFoundError("manifest"))
        with self.assertRaises(FileNotFoundError):
            self.execute()
        self.assertFalse(self.operations.called("collect"))

    def test_real_postcheck_signal_collects_and_restores_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        self.operations.postcheck = mock.Mock(side_effect=lambda _args: signal.raise_signal(signal.SIGTERM))
        result = self.execute()
        self.assertEqual((result.status, result.exit_code), ("interrupted", 143))
        self.assertEqual(result.occurrences[1].status, "interrupted")
        self.assertTrue(result.occurrences[2].best_effort)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_mismatched_plan_rejected_before_services(self):
        with self.assertRaises(ValueError):
            self.execute(plan=plans.resolve("synthesis"))
        self.assertEqual(self.operations.calls, [])


class ServiceIntegrationTest(unittest.TestCase):
    class PythonOperations(cli.Operations):
        # Explicit injected retained-service fixture. Production Operations.run
        # is now authoritative OCaml and is exercised in check_coarse_run.py.
        def run(self, args):
            return coarse_flow_oracle.run(args)

    def setUp(self):
        # Reuse the existing emitted-bundle/runtime fixture, not another service
        # implementation. Only the external probe/process boundaries are replaced.
        self.fixture = test_coarse_flow.RunnerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.args = self.fixture.args
        self.args.verbose, self.args.archive, self.args.force = False, None, False

    def test_preflight_failure_and_real_signal_publish_results_without_acceptance(self):
        for interrupted in (False, True):
            def probe(*_args):
                if interrupted:
                    signal.raise_signal(signal.SIGTERM)
                return {"ready": False, "issues": ["fixture unavailable"]}
            with self.subTest(interrupted=interrupted), \
                    mock.patch.object(coarse_flow_oracle, "preflight", side_effect=probe), \
                    mock.patch.object(coarse_flow_oracle, "run_logged") as backend, \
                    mock.patch.object(cli.reporter, "report") as acceptance:
                result = runner.execute(plans.resolve("synthesis"),
                                        runner.ServiceAdapter(self.args, self.PythonOperations()))
                self.assertEqual(result.exit_code, 143 if interrupted else 1)
                run = result.run_outcome.run_dir
                self.assertTrue((run / "results.json").is_file())
                self.assertEqual(coarse_flow_oracle.read_json(run / "run.json")["status"], result.status)
                self.assertEqual(coarse_flow_oracle.read_json(run / "results.json")["run_id"], result.run_outcome.run_id)
                backend.assert_not_called()
                acceptance.assert_not_called()

    def test_successful_process_does_not_bypass_real_acceptance(self):
        with mock.patch.object(coarse_flow_oracle, "preflight", return_value=self.fixture.ready()), \
                mock.patch.object(coarse_flow_oracle, "run_logged", return_value=0):
            result = runner.execute(plans.resolve("synthesis"),
                                    runner.ServiceAdapter(self.args, self.PythonOperations()))
        # The fixture produces no synthesis artifacts. Existing reporter policy
        # must reject it even though the coarse process returned zero.
        self.assertEqual(result.run_outcome.status, "completed")
        self.assertEqual((result.status, result.exit_code), ("rejected", 1))
        self.assertEqual(result.occurrences[3].verdict, "rejected")


if __name__ == "__main__":
    unittest.main()
