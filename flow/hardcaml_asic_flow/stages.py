"""Independently callable segments and shared-runner adapter.

SegmentAdapter takes an explicit execution transport and publication store. The
separate prepare service binds runtime once and stages inputs for the CLI. Neither
segment implementation chooses a transport/store or performs hidden sequencing.
"""

import ast
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import uuid

from . import checkpoint as checkpoints, execution_transport as transport, coarse_flow_oracle, result_store
from .execution_plan import StagePlan, BOUNDARIES, TESTED_IMAGE
from . import backend_protocol as protocol


@dataclass(frozen=True)
class SegmentResult:
    boundary: str
    candidate: Path
    status: str
    exit_code: int

    def as_dict(self):
        return {"boundary": self.boundary, "candidate": str(self.candidate),
                "status": self.status, "exit_code": self.exit_code}


class SegmentAdapter:
    def __init__(self, *, project, pdk_root, compatibility, launch, checkpoint_store,
                 initial_checkpoint=None):
        self.project, self.pdk_root = Path(project), Path(pdk_root)
        self.compatibility = copy.deepcopy(compatibility)
        self.launch = launch
        self.checkpoint_store = Path(checkpoint_store)
        self.initial_checkpoint = initial_checkpoint
        self.current_checkpoint = initial_checkpoint
        self._completed_requests = {}

    def verify_bridge(self):
        bridge = self.project / "bridge/librelane_bridge.py"
        if checkpoints.digest(bridge) != self.compatibility["bridge_sha256"]:
            raise ValueError("transported bridge changed")
        pins = [ast.literal_eval(node.value) for node in ast.parse(bridge.read_text()).body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == "PROTOCOL_SHA256" for target in node.targets)]
        support = bridge.with_name("backend_protocol.py")
        if (len(pins) != 1 or pins[0] != checkpoints.digest(support)
                or pins[0] != checkpoints.digest(Path(protocol.__file__))):
            raise ValueError("transported backend protocol changed")
        return bridge

    def validate(self, plan):
        if not isinstance(plan, StagePlan) or plan.resume != (self.initial_checkpoint is not None):
            raise ValueError("stage plan does not match the supplied handoff")
        if self.initial_checkpoint is not None:
            manifest = checkpoints.validate(self.initial_checkpoint, compatibility=self.compatibility,
                                            restart=True, restart_to=plan.sequence[0])
            if manifest["boundary"] != plan.source_boundary:
                raise ValueError("stage sequence initial boundary does not match checkpoint")

    def run_segment(self, boundary, *, checkpoint=None):
        """Useful standalone API; returns mutable candidate, never claims readiness."""
        if boundary not in BOUNDARIES or (boundary == "synthesis") != (checkpoint is None):
            raise ValueError("physical segments require an explicit tested checkpoint")
        source_boundary = None
        if checkpoint is not None:
            manifest = checkpoints.validate(checkpoint, compatibility=self.compatibility,
                                            restart=True, restart_to=boundary)
            source_boundary = manifest["boundary"]
        bridge = self.verify_bridge()
        tag = boundary + "-" + uuid.uuid4().hex
        candidate = self.project / "candidates" / tag
        candidate.parent.mkdir(exist_ok=True)
        request = {"protocol_version": protocol.VERSION, "operation": "execute_segment",
                   "request_id": tag, "reply_path": str(self.project / (tag + "-reply.json")),
                   "boundary": boundary, "checkpoint": str(Path(checkpoint).absolute()) if checkpoint else None,
                   "source_boundary": source_boundary,
                   "candidate": str(candidate), "project": str(self.project),
                   "pdk_root": str(self.pdk_root), "tag": tag,
                   "bridge_sha256": self.compatibility["bridge_sha256"]}
        request_path = self.project / (tag + "-request.json")
        protocol.validate_request(request)
        request_path.write_text(checkpoints.canonical(request) + "\n")
        code = self.launch(bridge, request_path, tag)
        if code:
            return SegmentResult(boundary, candidate, "failed", code)
        protocol.read_reply(request)
        report = checkpoints.read(candidate / "segment.json")
        if (report.get("status") != "completed" or report.get("boundary") != boundary
                or report.get("bridge_sha256") != self.compatibility["bridge_sha256"]):
            raise ValueError("missing or inconsistent outer segment completion")
        self._completed_requests[str(candidate)] = request
        return SegmentResult(boundary, candidate, "completed", 0)

    def invoke(self, action, *, best_effort=False):
        return self.run_segment(action.stage, checkpoint=(self.current_checkpoint
                                                         if action.stage != "synthesis" else None))

    def publish(self, action, result):
        if result.exit_code or result.status != "completed" or result.boundary != action.stage:
            raise ValueError("unsuccessful segment cannot be published")
        request = self._completed_requests.get(str(result.candidate))
        if request is None or request["boundary"] != result.boundary:
            raise ValueError("publication requires this adapter's completed backend call")
        self.verify_bridge()
        protocol.read_reply(request)
        def validate_runtime(candidate):
            bridge = self.verify_bridge()
            tag = "validate-" + result.boundary + "-" + uuid.uuid4().hex
            request = {"protocol_version": protocol.VERSION, "operation": "validate_publication",
                       "request_id": tag, "reply_path": str(self.project / (tag + "-reply.json")),
                       "checkpoint": None, "source_boundary": None, "tag": tag,
                       "candidate": str(candidate),
                       "boundary": result.boundary, "project": str(self.project),
                       "pdk_root": str(self.pdk_root), "bridge_sha256": self.compatibility["bridge_sha256"]}
            request_path = self.project / (tag + "-request.json")
            protocol.validate_request(request)
            request_path.write_text(checkpoints.canonical(request) + "\n")
            if self.launch(bridge, request_path, tag):
                raise ValueError("runtime closure validation failed; checkpoint not published")
            protocol.read_reply(request)

        artifact = checkpoints.publish(result.candidate, self.checkpoint_store,
                                       compatibility=self.compatibility, boundary=result.boundary,
                                       validate_runtime=validate_runtime)
        self.current_checkpoint = artifact["path"]
        return artifact


def prepare(*, bundle, support_tools, pdk_root, python, workspace, checkpoint_store,
            initial_checkpoint=None, timeout=600, preflight_output=None):
    """Explicit host composition service. Docker-only demonstrated capability.

    Fresh workspace for a new recipe; resume uses the retained path-bound workspace.
    Published stores must be in the mounted workspace. Native/relocated/macro
    checkpoints are intentionally not advertised.
    """
    bundle, support_tools, pdk_root = (Path(p).resolve() for p in (bundle, support_tools, pdk_root))
    workspace, checkpoint_store = Path(workspace).absolute(), Path(checkpoint_store).absolute()
    if not checkpoint_store.is_relative_to(workspace):
        raise ValueError("checkpoint store must be under the explicitly mounted workspace")
    if timeout <= 0:
        raise ValueError("segment timeout must be positive")
    probe = lambda: coarse_flow_oracle.preflight(bundle, support_tools, pdk_root, Path(python).absolute())
    if preflight_output is None:
        report = probe()
    else:
        checked = result_store.durable_preflight(bundle, preflight_output, probe)
        if checked.status == "interrupted":
            raise result_store.FlowInterrupted(checked.exit_code - 128)
        report = checked.result
    if not report["ready"]:
        raise ValueError(f"stage preflight failed: {report['issues']}")
    binding = transport.from_preflight(report, native=False)
    transport.verify_engine(binding)
    transport.verify_image(binding)
    if binding["image_identity"]["image_id"] != TESTED_IMAGE:
        raise ValueError("explicit physical stages have only been tested with the declared exact image")
    project = workspace / "project"
    manifest = coarse_flow_oracle.read_json(bundle / "manifest.json")
    bridge_source = Path(__file__).with_name("librelane_bridge.py")
    protocol_source = Path(__file__).with_name("backend_protocol.py")
    compatibility = {"bundle_identity": manifest["identity"], "target": manifest["target"],
                     "requested_tools": manifest["requested_tools"], "binding": binding,
                     "bridge_sha256": checkpoints.digest(bridge_source), "project": str(project),
                     "pdk_root": str(pdk_root)}
    if initial_checkpoint is None:
        workspace.mkdir()  # no overwrite of existing user work
        shutil.copytree(bundle, project)
        for ref in manifest["target"]["external_references"]:
            if ref["source"] == "Support_tools":
                source = coarse_flow_oracle.safe_child(support_tools, ref["path"])
                target = coarse_flow_oracle.safe_child(project / "tt", ref["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (project / "bridge").mkdir()
        shutil.copyfile(bridge_source, project / "bridge/librelane_bridge.py")
        shutil.copyfile(protocol_source, project / "bridge/backend_protocol.py")
        checkpoint_store.mkdir(parents=True)
    else:
        checkpoints.validate(initial_checkpoint, compatibility=compatibility, restart=True)
    # One immutable image identity per run; no per-segment preflight/tag resolution.
    record = {"binding": binding, "commands": [], "bridge_sha256": compatibility["bridge_sha256"]}
    record_path = workspace / ("transport-" + uuid.uuid4().hex + ".json")
    coarse_flow_oracle.save(record_path, record)
    coarse_flow_oracle.save(workspace / (record_path.stem + "-preflight.json"), report)

    def launch(bridge, request, tag):
        name = transport.owned_name()
        transport.verify_engine(binding)
        transport.verify_image(binding)
        argv = transport.docker_command(binding, workspace, pdk_root, name=name,
                                        workdir=project, pdk=manifest["target"]["technology"])
        argv += [*binding["interpreter_flags"], str(bridge), str(request)]
        return coarse_flow_oracle.run_logged(argv, project, transport.environment(), workspace / (tag + ".log"),
                                 record, record_path, timeout=timeout,
                                 cleanup=lambda: transport.cleanup(binding, name))

    adapter = SegmentAdapter(project=project, pdk_root=pdk_root, compatibility=compatibility,
                           launch=launch, checkpoint_store=checkpoint_store,
                           initial_checkpoint=initial_checkpoint)
    adapter.runtime_record = record_path
    return adapter
