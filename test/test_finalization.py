"""Real collector/reporter and checkpoint contracts; synthetic tool boundaries."""
import json
import io
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.environ.get("STAGE_TEST_PACKAGE_ROOT", str(Path(__file__).resolve().parents[1] / "flow")))
from hardcaml_asic_flow import backend_protocol as protocol, checkpoint as cp, cli, execution_plan as plans, finalization as final, coarse_flow_oracle, result_store


class FinalizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "constraints").mkdir()
        (self.project / "constraints/top.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
        self.binding = {"schema_version": 1, "execution_mode": "dockerized", "interpreter": "python3",
            "interpreter_flags": ["-I", "-B"], "image_identity": {"declared_ref": "fixture:1",
            "image_id": plans.TESTED_IMAGE, "repo_digests": [], "binding": "local-image-id",
            "platform": {"os": "linux", "architecture": "amd64"}}, "engine_identity": {
            "name": "docker", "executable": "/fixture/docker", "rootless": False, "server_version": "fixture"}}
        self.compat = {"binding": self.binding, "project": str(self.project), "pdk_root": str(self.root / "pdk"),
            "bridge_sha256": "a" * 64, "bundle_identity": "fixture", "requested_tools": {"librelane": "fixture"},
            "target": {"technology": "ihp-sg13cmos5l"}}
        coarse_flow_oracle.save(self.project / "manifest.json", {"identity": "fixture", "top_module": "fixture_top"})
        (self.project / "info.yaml").write_text("project: {top_module: fixture_top}\n")
        self.store = self.root / "checkpoints"
        self.store.mkdir()
        artifacts = []
        self.final_metrics = {"timing__setup__ws": 1, "timing__setup__ws__corner:slow": 1,
            "timing__hold__ws": 0.1, "timing__hold__ws__corner:fast": 0.1,
            "route__drc_errors": 0, "magic__drc_error__count": 0, "design__lvs_error__count": 0,
            "antenna__violating__nets": 0, "antenna__violating__pins": 0}
        self.synth_metrics = {"design__instance__area": 42, "design__instance__count": 12,
            "design__instance_unmapped__count": 0, "design__inferred_latch__count": 0,
            "synthesis__check_error__count": 0}
        for boundary in plans.STAGE_ORDER:
            artifacts.append(self.publication(boundary))
        self.execution = self.root / "execution.json"
        self.value = {"attempt_id": "original", "plan": plans.resolve_stages("signoff").as_dict(),
            "status": "completed", "exit_code": 0, "artifacts": artifacts,
            "occurrences": [{"action_key": "implementation." + b, "status": "completed", "exit_code": 0,
                             "occurrence_id": "original/implementation." + b} for b in plans.STAGE_ORDER]}
        coarse_flow_oracle.save(self.execution, self.value)
        self.support = self.root / "tt"
        (self.support / "precheck").mkdir(parents=True)
        (self.support / "tech").mkdir()
        (self.support / "precheck/default.nix").write_text("{}")
        coarse_flow_oracle.save(self.support / "precheck/tool-versions.json", {"klayout": "fixture"})
        self.python = self.root / "python"
        self.python.write_text("fixture Python")
        self.bench = self.root / "bench.v"
        self.bench.write_text("module replay; endmodule")
        self.trace = self.root / "trace.txt"
        self.trace.write_text("oracle\n")
        cells = self.root / "pdk/ihp-sg13cmos5l/libs.ref"
        for relative in ("sg13cmos5l_io/verilog/sg13cmos5l_io.v", "sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v",
                         "sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v"):
            path = cells / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture model")

    def tearDown(self):
        for p in self.root.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        self.tmp.cleanup()

    def publication(self, boundary, metrics=None):
        candidate = self.root / ("candidate-" + boundary)
        candidate.mkdir(exist_ok=True)
        view = candidate / "tool-view"
        view.write_text("fixture tool output")
        state = {key: str(view) for key in ("nl", "json_h", "odb", "def", "gds", "lef")}
        state["metrics"] = metrics if metrics is not None else (
            self.synth_metrics if boundary == "synthesis" else self.final_metrics)
        coarse_flow_oracle.save(candidate / "state.json", state)
        coarse_flow_oracle.save(candidate / "resolved.json", {"fixture": True})
        coarse_flow_oracle.save(candidate / "closure.json", {str(self.project / "manifest.json"):
                                                   cp.file_record(self.project / "manifest.json")})
        coarse_flow_oracle.save(candidate / "segment.json", {"status": "completed", "boundary": boundary,
            "bridge_sha256": self.compat["bridge_sha256"], "metrics": {}})
        return cp.publish(candidate, self.store, compatibility=self.compat, boundary=boundary)

    def invoke(self, *, accept=False, precheck=0, gate=0, runtime_error=None, public_cli=False):
        def output(argv, **kwargs):
            return "KLayout fixture" if argv[0] == "nix-shell" else "0.28.17"
        def logged(argv, cwd, env, log, record, record_path, **kwargs):
            record["commands"].append(argv)
            if "iverilog" in argv:
                return gate
            return precheck if argv[0] == "nix-shell" else 0
        with patch.object(final, "runtime_validate", side_effect=runtime_error), \
             patch.object(coarse_flow_oracle, "command_output", side_effect=output), \
             patch.object(coarse_flow_oracle, "run_logged", side_effect=logged), \
             patch.object(final.transport, "verify_engine", return_value=self.binding["engine_identity"]), \
             patch.object(final.transport, "verify_image", return_value=plans.TESTED_IMAGE), \
             patch.object(final.transport, "cleanup"), \
             patch.object(coarse_flow_oracle.runtime_observation, "output", return_value="Icarus fixture"):
            if public_cli:
                output_stream = io.StringIO()
                with patch("sys.stdout", output_stream):
                    code = cli.main(["finalize", "--json", str(self.execution), "--operation-store", str(self.root / "operations"),
                        "--accept", "--support-tools", str(self.support), "--precheck-python", str(self.python),
                         "--testbench", str(self.bench), "--testbench-top", "replay", "--replay-trace", str(self.trace)], operations=cli.Operations())
                allocation, value = [json.loads(line) for line in output_stream.getvalue().splitlines()]
                self.assertEqual(allocation["event"], "allocated")
                self.assertEqual(code, value["exit_code"])
                return Path(value.pop("attempt")), value
            return final.finalize(execution=self.execution, store=self.root / "operations", accept=accept,
                support_tools=self.support, precheck_python=self.python, testbench=self.bench,
                testbench_top="replay", replay_trace=self.trace)

    def test_results_only_separate_attempt_no_legacy_facts(self):
        original = self.execution.read_bytes()
        directory, receipt = self.invoke()
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["acceptance"], "not_evaluated")
        self.assertNotEqual(receipt["attempt_id"], self.value["attempt_id"])
        self.assertFalse((directory / "run.json").exists())
        self.assertEqual(self.execution.read_bytes(), original)
        result = coarse_flow_oracle.read_json(directory / "results.json")
        self.assertEqual(result["metrics"]["setup_slack"]["corner"], "slow")
        self.assertEqual(result["metrics"]["mapped_area"]["origin_boundary"], "synthesis")
        self.assertEqual(len(receipt["selected_checkpoints"]), 7)
        self.assertFalse(list(directory.rglob("stat.json")))

    def test_accept_uses_real_shared_policy_and_exact_simulator(self):
        directory, receipt = self.invoke(accept=True)
        self.assertEqual(receipt["acceptance"], "pass")
        checks = coarse_flow_oracle.read_json(Path(receipt["postcheck_record"]))
        self.assertEqual(checks["tools"]["execution_binding"], self.binding)
        self.assertEqual(checks["inputs"]["replay_trace_sha256"], cp.digest(self.trace))
        self.assertIn(plans.TESTED_IMAGE, checks["commands"][-1])
        self.assertEqual(receipt["postcheck_sha256"], cp.digest(receipt["postcheck_record"]))
        self.assertIn("full-flow acceptance passed", (directory / "acceptance.txt").read_text())

    def test_public_cli_acceptance_success(self):
        _, receipt = self.invoke(public_cli=True)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["acceptance"], "pass")

    def test_failed_precheck_and_gate_never_accept(self):
        for precheck, gate in ((1, 0), (0, 1)):
            with self.subTest(precheck=precheck, gate=gate):
                _, receipt = self.invoke(accept=True, precheck=precheck, gate=gate)
                self.assertEqual(receipt["acceptance"], "fail")
                self.assertEqual(receipt["external_postcheck"], "failed")
                self.assertEqual(receipt["exit_code"], 1)

    def test_existing_timing_drc_lvs_antenna_rejections(self):
        for field, value in (("timing__setup__ws__corner:slow", -1), ("timing__hold__ws__corner:fast", -1),
                             ("timing__setup__ws", -1),
                             ("route__drc_errors", 1), ("design__lvs_error__count", 1),
                             ("antenna__violating__nets", 1), ("timing__setup__ws__corner:slow", 1e31)):
            with self.subTest(field=field):
                metrics = dict(self.final_metrics, **{field: value})
                self.value["artifacts"][-1] = self.publication("signoff", metrics)
                coarse_flow_oracle.save(self.execution, self.value)
                _, receipt = self.invoke(accept=True)
                self.assertEqual(receipt["acceptance"], "fail")

    def test_missing_or_malformed_execution_retains_failed_receipt(self):
        for value in ({}, dict(self.value, artifacts=[]), dict(self.value, status="failed"),
                      dict(self.value, plan=plans.resolve_stages("routing").as_dict())):
            coarse_flow_oracle.save(self.execution, value)
            _, receipt = self.invoke()
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["acceptance"], "not_evaluated")
            self.assertIsNone(receipt["results"])

    def test_identity_mismatch(self):
        self.value["artifacts"][-1]["identity"] = "0" * 64
        coarse_flow_oracle.save(self.execution, self.value)
        _, receipt = self.invoke()
        self.assertIn("identity", receipt["error"])

    def test_missing_view_fails_before_readiness(self):
        path = Path(self.value["artifacts"][-1]["path"])
        view = next((path / "views").iterdir())
        view.parent.chmod(0o755)
        view.unlink()
        _, receipt = self.invoke()
        self.assertEqual(receipt["status"], "failed")
        self.assertIsNone(receipt["results"])

    def test_runtime_failure(self):
        _, receipt = self.invoke(runtime_error=ValueError("bound image unavailable"))
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["binding"], self.binding)
        self.assertIsNone(receipt["results"])

    def test_runtime_validation_requires_correlated_v1_reply_and_artifact_bytes(self):
        bridge = self.project / "bridge/librelane_bridge.py"
        bridge.parent.mkdir()
        for name in ("librelane_bridge.py", "backend_protocol.py"):
            shutil.copyfile(Path(protocol.__file__).with_name(name), bridge.with_name(name))
        compatibility = dict(self.compat, bridge_sha256=cp.digest(bridge))
        publication = Path(self.value["artifacts"][-1]["path"])
        for case in ("valid", "missing", "wrong_request", "wrong_version", "failed", "changed_bytes", "nonzero"):
            with self.subTest(case=case):
                directory = self.root / ("runtime-" + case)
                directory.mkdir()
                def logged(argv, *args, **kwargs):
                    request = protocol.validate_request(protocol.loads(Path(argv[-1]).read_text()))
                    self.assertEqual(request["operation"], "validate_publication")
                    self.assertEqual(request["checkpoint"], None)
                    if case == "missing":
                        return 0
                    reply = protocol.reply(request, error="fixture failure" if case == "failed" else None)
                    if case == "wrong_request":
                        reply["request_sha256"] = "0" * 64
                    if case == "wrong_version":
                        reply["protocol_version"] = 2
                    if case == "changed_bytes":
                        reply["artifacts"]["closure.json"]["sha256"] = "0" * 64
                    Path(request["reply_path"]).write_text(protocol.canonical(reply))
                    return 1 if case == "nonzero" else 0
                with patch.object(final.transport, "verify_engine"), \
                     patch.object(final.transport, "verify_image"), \
                     patch.object(coarse_flow_oracle, "run_logged", side_effect=logged):
                    if case == "valid":
                        final.runtime_validate(compatibility, [(publication, {})], directory, {"commands": []})
                    else:
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            final.runtime_validate(compatibility, [(publication, {})], directory, {"commands": []})
        bridge.with_name("backend_protocol.py").write_text("mutated resource")
        with patch.object(final.transport, "verify_engine"), \
             patch.object(final.transport, "verify_image"), \
             patch.object(coarse_flow_oracle, "run_logged") as launch:
            with self.assertRaisesRegex(ValueError, "backend protocol"):
                final.runtime_validate(compatibility, [(publication, {})], self.root, {"commands": []})
            launch.assert_not_called()

    def test_interruption_no_success(self):
        _, receipt = self.invoke(runtime_error=result_store.FlowInterrupted(signal.SIGTERM))
        self.assertEqual(receipt["status"], "interrupted")
        self.assertEqual(receipt["exit_code"], 143)
        self.assertEqual(receipt["acceptance"], "not_evaluated")

    def test_missing_postcheck_prerequisite(self):
        self.bench.unlink()
        _, receipt = self.invoke(accept=True)
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("missing postcheck input", receipt["error"])
        self.assertEqual(receipt["acceptance"], "not_evaluated")

    def test_duplicate_execution_keys_fail_closed(self):
        self.execution.write_text('{"status":"completed","status":"failed"}')
        _, receipt = self.invoke()
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("duplicate", receipt["error"])

    def test_unavailable_timing_and_missing_signoff_metric_fail_policy(self):
        for field in ("timing__setup__ws__corner:slow", "design__lvs_error__count"):
            metrics = dict(self.final_metrics)
            del metrics[field]
            if field.startswith("timing"):
                del metrics["timing__setup__ws"]
            self.value["artifacts"][-1] = self.publication("signoff", metrics)
            coarse_flow_oracle.save(self.execution, self.value)
            _, receipt = self.invoke(accept=True)
            self.assertEqual(receipt["acceptance"], "fail")

    def test_selection_changed_during_check_fails_closed(self):
        def changed(*args, **kwargs):
            self.value["artifacts"] = []
            coarse_flow_oracle.save(self.execution, self.value)
            coarse_flow_oracle.save(self.root / "unused.json", {"fixture": "changed selection"})
            return coarse_flow_oracle.CheckOutcome("fixture", self.root, self.root / "unused.json", "completed", 0)
        with patch.object(coarse_flow_oracle, "check", side_effect=changed):
            _, receipt = self.invoke(accept=True)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["acceptance"], "not_evaluated")

    def test_receipt_inspection_read_only_and_unearned_acceptance_rejected(self):
        directory, receipt = self.invoke()
        original = (directory / "operation.json").read_bytes()
        self.assertEqual(final.inspect(directory), receipt)
        self.assertEqual((directory / "operation.json").read_bytes(), original)
        receipt["acceptance"] = "pass"
        coarse_flow_oracle.save(directory / "operation.json", receipt)
        with self.assertRaisesRegex(ValueError, "unearned"):
            final.inspect(directory)


if __name__ == "__main__":
    unittest.main()
