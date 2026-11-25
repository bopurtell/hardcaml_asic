"""Explicit terminal segmented evidence adapter and separate operation receipts.

No legacy run record is created. The canonical context is passed in memory to
the existing collector, external checks and acceptance reporter. Receipt schema
1 is a snapshot API, deliberately separate from the terminal stage journal.
"""

import ast
import csv
from pathlib import Path
import shutil
import signal
import uuid
from types import SimpleNamespace

from . import backend_protocol as protocol, checkpoint, execution_plan, execution_transport as transport
from . import coarse_flow_oracle, report, result_store
from .flow_observation import strict_read

CONTRACT = "hardcaml-asic.segment-finalization"


def inspect(directory):
    """Read a versioned historical snapshot, never assert current readiness."""
    value = strict_read(Path(directory) / "operation.json")
    required = {"contract", "schema_version", "attempt_id", "status", "phase", "started_at", "ended_at",
                "acceptance", "external_postcheck", "request", "commands", "results", "error"}
    optional = {"exit_code", "execution", "binding", "selected_checkpoints", "runtime_validation",
                "selected_evidence", "mapped_files", "adapter", "evidence_context", "postcheck_collateral",
                "postcheck_record", "postcheck_sha256", "acceptance_report", "container_cleanup", "client_termination",
                "timing_analysis"}
    if (not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional
            or value["contract"] != CONTRACT or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or not isinstance(value["attempt_id"], str)
            or not isinstance(value["request"], dict) or type(value["request"].get("accept")) is not bool
            or not isinstance(value["commands"], list)
            or value["status"] not in ("running", "completed", "failed", "interrupted")
            or value["phase"] not in ("validation", "collection", "external_postcheck", "acceptance")
            or value["external_postcheck"] not in ("not_evaluated", "running", "completed", "failed", "interrupted")
            or value["acceptance"] not in ("not_evaluated", "pass", "fail")):
        raise ValueError("malformed finalization receipt")
    terminal = value["status"] != "running"
    if terminal != (value["ended_at"] is not None) or terminal != ("exit_code" in value):
        raise ValueError("inconsistent finalization lifecycle")
    if terminal and (type(value["exit_code"]) is not int or (value["status"] == "completed") != (value["exit_code"] == 0)):
        raise ValueError("inconsistent finalization exit status")
    if value["status"] == "completed" and value["results"] is None:
        raise ValueError("completed finalization has no results")
    if value["acceptance"] == "pass" and (not value["request"]["accept"]
            or value["external_postcheck"] != "completed" or value["status"] != "completed"):
        raise ValueError("unearned finalization acceptance")
    return value


def selected(execution):
    """Validate the bounded complete six-stage execution and its publications."""
    value = strict_read(execution)
    if (not isinstance(value, dict) or set(value) != {"attempt_id", "plan", "occurrences", "status", "exit_code", "artifacts"}
            or not isinstance(value["attempt_id"], str) or not value["attempt_id"]
            or type(value["exit_code"]) is not int
            or not isinstance(value["occurrences"], list) or not isinstance(value["artifacts"], list)):
        raise ValueError("malformed segmented execution record")
    plan = execution_plan.StagePlan.from_dict(value["plan"])
    if plan.sequence != plan.order or plan.source_boundary is not None:
        raise ValueError("finalization requires a complete fresh six-stage execution")
    if value.get("exit_code") != 0 or value.get("status") != "completed":
        raise ValueError("implementation execution did not complete")
    occurrences = value["occurrences"]
    artifacts = value["artifacts"]
    if len(occurrences) != len(plan.order) or len(artifacts) != len(plan.order):
        raise ValueError("missing selected stage evidence")
    compatibility = None
    publications = []
    for boundary, occurrence, artifact in zip(plan.order, occurrences, artifacts):
        if (occurrence.get("status") != "completed" or type(occurrence.get("exit_code")) is not int
                or occurrence.get("exit_code") != 0
                or occurrence.get("action_key") != "implementation." + boundary
                or occurrence.get("occurrence_id") != value["attempt_id"] + "/implementation." + boundary
                or artifact.get("boundary") != boundary):
            raise ValueError("inconsistent stage completion/evidence")
        path = Path(artifact["path"])
        manifest = checkpoint.read(path / "manifest.json")
        compatibility = compatibility or manifest["compatibility"]
        checkpoint.validate(path, compatibility=compatibility)
        if manifest["identity"] != artifact["identity"] or manifest["boundary"] != boundary:
            raise ValueError("selected publication identity/boundary mismatch")
        if manifest["schema_version"] != 2:
            raise ValueError("finalization requires checkpoint schema 2")
        publications.append((path, manifest))
    for filename in ("closure.json", "resolved.json"):
        if len({checkpoint.digest(path / filename) for path, _ in publications}) != 1:
            raise ValueError("selected stages have different configuration/closure")
    return value, compatibility, publications


def runtime_validate(compatibility, publications, directory, receipt):
    binding = transport.validate(compatibility["binding"])
    transport.verify_engine(binding)
    transport.verify_image(binding)
    project, pdk = Path(compatibility["project"]), Path(compatibility["pdk_root"])
    bridge = project / "bridge/librelane_bridge.py"
    if checkpoint.digest(bridge) != compatibility["bridge_sha256"]:
        raise ValueError("retained bound bridge changed")
    pins = [ast.literal_eval(node.value) for node in ast.parse(bridge.read_text()).body
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == "PROTOCOL_SHA256" for target in node.targets)]
    support = bridge.with_name("backend_protocol.py")
    if (len(pins) != 1 or pins[0] != checkpoint.digest(support)
            or pins[0] != checkpoint.digest(Path(protocol.__file__))):
        raise ValueError("retained backend protocol changed or unsupported")
    # All selected resolved configurations and closures are byte-identical;
    # validate that shared closure in the exact bound runtime, without Flow.start.
    tag = "finalize-validate-" + uuid.uuid4().hex
    request = {"protocol_version": protocol.VERSION, "operation": "validate_publication",
               "request_id": tag, "tag": tag, "checkpoint": None, "source_boundary": None,
               "reply_path": str(directory / "runtime-validation-reply.json"),
               "candidate": str(publications[-1][0]),
               "boundary": "signoff", "project": str(project), "pdk_root": str(pdk),
               "bridge_sha256": compatibility["bridge_sha256"]}
    request_path = directory / "runtime-validation-request.json"
    protocol.validate_request(request)
    coarse_flow_oracle.save(request_path, request)
    name = transport.owned_name()
    argv = transport.docker_command(binding, project.parent, pdk, name=name,
                                   workdir=project, pdk=compatibility["target"]["technology"])
    image_index = argv.index(binding["image_identity"]["image_id"])
    argv[image_index:image_index] = ["-v", f"{directory}:{directory}"]
    argv += [*binding["interpreter_flags"], str(bridge), str(request_path)]
    if coarse_flow_oracle.run_logged(argv, project, transport.environment(), directory / "runtime-validation.log",
                         receipt, directory / "operation.json", timeout=120,
                         cleanup=lambda: transport.cleanup(binding, name)):
        raise ValueError("bound runtime closure validation failed")
    protocol.read_reply(request)


def adapt(directory, compatibility, publications, receipt):
    """Copy selected evidence and explicitly derive CSV from official metrics."""
    project = Path(compatibility["project"])
    manifest = coarse_flow_oracle.read_json(project / "manifest.json")
    if manifest["identity"] != compatibility["bundle_identity"]:
        raise ValueError("retained project manifest identity mismatch")
    target = directory / "project"
    target.mkdir()
    shutil.copy2(project / "manifest.json", target / "manifest.json")
    shutil.copytree(project / "constraints", target / "constraints")
    flow = directory / "evidence"
    flow.mkdir()
    provenance = []
    for boundary, (path, publication) in zip(execution_plan.STAGE_ORDER, publications):
        provenance.append({"boundary": boundary, "path": str(path), "identity": publication["identity"],
                           "manifest_sha256": checkpoint.digest(path / "manifest.json")})
    synth = flow / "synthesis-state.json"
    shutil.copy2(publications[0][0] / "state.json", synth)
    final_state = flow / "signoff-state.json"
    shutil.copy2(publications[-1][0] / "state.json", final_state)
    state = coarse_flow_oracle.read_json(final_state)
    final = flow / "final"
    final.mkdir()
    shutil.copy2(project / "info.yaml", final / "info.yaml")
    with (final / "metrics.csv").open("w", newline="") as stream:
        csv.writer(stream).writerows(sorted(state["metrics"].items()))
    top = manifest["top_module"]
    for key, relative in (("gds", f"gds/{top}.gds"), ("nl", f"nl/{top}.nl.v"),
                          ("lef", f"lef/{top}.lef")):
        source = Path(state[key])
        destination = final / relative
        destination.parent.mkdir()
        shutil.copy2(source, destination)
        provenance.append({"boundary": "signoff", "view": key, "source": str(source),
                           "sha256": checkpoint.digest(source), "mapped": str(destination.relative_to(directory))})
    context = {"run_id": receipt["attempt_id"], "build_identity": manifest["identity"],
               "bundle": str(project), "status": "completed", "requested_stage": "full",
               "completed_stage": "full", "outputs": {"flow": "evidence"},
               "execution_binding_required": True, "execution_binding": compatibility["binding"],
               "evidence_origin": {"contract": CONTRACT, "schema_version": 1,
                                   "operation": str(directory / "operation.json"),
                                   "selected_checkpoints": receipt["selected_checkpoints"]},
               "execution_mode": "dockerized", "environment": {
                   "requested": compatibility["requested_tools"],
                   "execution_binding": compatibility["binding"]}}
    receipt["evidence_context"] = context
    receipt["selected_evidence"] = provenance
    receipt["mapped_files"] = {str(path.relative_to(directory)): checkpoint.file_record(path)
                               for root in (flow, target) for path in sorted(root.rglob("*")) if path.is_file()}
    receipt["adapter"] = {"metrics_csv": "derived from signoff-state.json metrics, cumulative final state",
                          "synthesis": "published synthesis-state.json; no synthetic Yosys report",
                          "scope": "full denotes selected physical completion, not historical run completion"}
    return context, synth


def finalize(*, execution, store, accept=False, support_tools=None, precheck_python=None,
             testbench=None, testbench_top=None, replay_trace=None, allocated=None):
    allocation = result_store.allocate(Path(store))
    directory = allocation.directory.resolve()
    path_value = lambda path: str(path) if path is not None else None
    receipt = {"contract": CONTRACT, "schema_version": 1, "attempt_id": allocation.identity,
               "status": "running", "phase": "validation", "started_at": coarse_flow_oracle.now(),
               "ended_at": None, "acceptance": "not_evaluated", "external_postcheck": "not_evaluated",
               "request": {"execution": str(Path(execution).absolute()), "accept": accept,
                           "support_tools": path_value(support_tools), "precheck_python": path_value(precheck_python),
                           "testbench": path_value(testbench), "testbench_top": testbench_top,
                           "replay_trace": path_value(replay_trace)},
               "commands": [], "results": None, "error": None}
    save = lambda: coarse_flow_oracle.save(directory / "operation.json", receipt)
    save()
    try:
        if allocated is not None:
            allocated(directory, receipt["attempt_id"])
        with coarse_flow_oracle.interruption_handlers():
            value, compatibility, publications = selected(Path(execution))
            receipt["execution"] = {"path": str(Path(execution).resolve()),
                                    "sha256": checkpoint.digest(execution), "attempt_id": value["attempt_id"]}
            receipt["binding"] = compatibility["binding"]
            receipt["selected_checkpoints"] = [{"path": str(path), "identity": manifest["identity"],
                                                 "boundary": manifest["boundary"]}
                                                for path, manifest in publications]
            save()
            runtime_validate(compatibility, publications, directory, receipt)
            receipt["runtime_validation"] = "completed"
            context, synth = adapt(directory, compatibility, publications, receipt)
            receipt["phase"] = "collection"
            save()
            if accept:
                if not all((support_tools, precheck_python, testbench, testbench_top)):
                    raise ValueError("accept requires support tools, precheck Python, testbench and top")
                # External postchecks use explicit caller-selected collateral.
                # Record every consumed support file rather than claiming the
                # physical checkpoint's closure also covered these later checks.
                receipt["postcheck_collateral"] = {
                    str(path): checkpoint.file_record(path)
                    for subdir in ("precheck", "tech")
                    for path in sorted((Path(support_tools) / subdir).rglob("*")) if path.is_file()}
                receipt["phase"] = "external_postcheck"
                receipt["external_postcheck"] = "running"
                save()
                outcome = coarse_flow_oracle.check(SimpleNamespace(run_dir=directory,
                    support_tools=Path(support_tools).resolve(), precheck_python=Path(precheck_python).absolute(),
                    testbench=Path(testbench).resolve(), testbench_top=testbench_top,
                    pdk_root=Path(compatibility["pdk_root"])), context=context, replay_trace=replay_trace)
                receipt["external_postcheck"] = outcome.status
                receipt["postcheck_record"] = str(outcome.check_record)
                receipt["postcheck_sha256"] = checkpoint.digest(outcome.check_record)
                save()
                if outcome.status == "interrupted":
                    raise result_store.FlowInterrupted(outcome.exit_code - 128)
            # Fail closed if selected input records, copied evidence or later
            # collateral changed while the operation was using them.
            selected(Path(execution))
            if checkpoint.digest(execution) != receipt["execution"]["sha256"]:
                raise ValueError("selected execution changed during finalization")
            for relative, expected in receipt["mapped_files"].items():
                if checkpoint.file_record(directory / relative) != expected:
                    raise ValueError("mapped evidence changed during finalization")
            for path, expected in receipt.get("postcheck_collateral", {}).items():
                if checkpoint.file_record(path) != expected:
                    raise ValueError("postcheck collateral changed during finalization")
            results = coarse_flow_oracle.collect(directory, context=context, synthesis_state=synth)
            results["provenance"] = {"contract": CONTRACT, "operation": str(directory / "operation.json"),
                                     "selected_evidence": receipt["selected_evidence"], "adapter": receipt["adapter"]}
            for item in list(results["metrics"].values()) + list(results["synthesis_checks"].values()) + list(results["checks"].values()):
                item["origin_boundary"] = "synthesis" if item["stage"] == "Yosys.Synthesis" else "signoff"
            coarse_flow_oracle.save(directory / "results.json", results)
            receipt["results"] = {"path": str(directory / "results.json"),
                                  "sha256": checkpoint.digest(directory / "results.json")}
            code = 0
            if accept:
                receipt["phase"] = "acceptance"
                save()
                lines = []
                code = report.report(directory, results, "segmented mapped evidence", output=lines.append, context=context)
                (directory / "acceptance.txt").write_text("\n".join(lines) + "\n")
                receipt["acceptance_report"] = {"path": str(directory / "acceptance.txt"),
                                                 "sha256": checkpoint.digest(directory / "acceptance.txt")}
                receipt["acceptance"] = "pass" if code == 0 else "fail"
            receipt["status"] = "completed" if code == 0 else "failed"
    except (result_store.FlowInterrupted, KeyboardInterrupt) as exc:
        code = 128 + getattr(exc, "signum", signal.SIGINT)
        receipt["status"] = "interrupted"
        if receipt["external_postcheck"] == "running":
            receipt["external_postcheck"] = "interrupted"
        receipt["error"] = str(exc)
    except Exception as exc:
        code = 1
        receipt["status"] = "failed"
        if receipt["external_postcheck"] == "running":
            receipt["external_postcheck"] = "failed"
        receipt["error"] = str(exc)
    receipt["ended_at"] = coarse_flow_oracle.now()
    receipt["exit_code"] = code
    save()
    return directory, receipt
