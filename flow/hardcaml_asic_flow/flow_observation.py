"""Path-bound stage observation journal, independent of runtime observations.

Atomic JSON promotion gives process-restart readability, not fsync durability.
One live writer per workspace; readers never repair records or finish attempts.
This is deliberately not the provisional store-wide provider fixture contract.
"""

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import re
from pathlib import Path
import signal
import stat
import time

from . import result_store
from .execution_plan import StagePlan, boundary_capabilities
from .runner import Occurrence, execute

CONTRACT = "hardcaml-asic.stage-observation"
VERSION = 2
SUPPORTED_VERSIONS = (1, 2)


def recorded_at_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_timing(record, previous_elapsed=None):
    stamp = record["recorded_at"]
    require(isinstance(stamp, str) and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", stamp),
        "recorded_at must be canonical UTC with six fractional digits and Z")
    try:
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ObservationError("INVALID_RECORD", "invalid recorded_at calendar time") from exc
    elapsed = record["elapsed_seconds"]
    try:
        finite = type(elapsed) in (int, float) and math.isfinite(elapsed)
    except OverflowError:
        finite = False
    require(finite and elapsed >= 0,
            "elapsed_seconds must be a finite nonnegative number")
    require(previous_elapsed is None or elapsed >= previous_elapsed,
            "elapsed_seconds regressed")


class ObservationError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def require(condition, detail):
    if not condition:
        raise ObservationError("INVALID_RECORD", detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_read(path):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate JSON key")
            value[key] = item
        return value
    def invalid(value):
        raise ValueError("nonfinite JSON: " + value)
    try:
        value = json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=invalid)
        canonical(value)  # also rejects exponent overflow (e.g. 1e999)
        return value
    except (OSError, ValueError) as exc:
        raise ObservationError("INVALID_RECORD", str(exc)) from exc


def describe():
    return {"contract": CONTRACT, "schema_version": VERSION,
            "supported_schema_versions": list(SUPPORTED_VERSIONS),
            "response_schema_policy": "inspect_read_updates_watch_match_stream; CLI_allocation_terminal_error_envelopes_remain_1",
            "timing": {"recorded_at": "UTC_YYYY-MM-DDTHH:MM:SS.ffffffZ_before_atomic_promotion",
                       "elapsed_seconds": "finite_nonnegative_monotonic_seconds_since_attempt_creation",
                       "ordering": "sequence_authoritative; wall_clock_may_regress",
                       "legacy": "schema_1_has_no_timing; never_synthesized",
                       "stream": "single_schema_version; timing_inside_record_hash"},
            "scope": "one_path_bound_stage_attempt", "cursor": "stream_id_and_sequence",
            "events": ["allocated", "planned", "prepared", "started", "published", "finished", "outcome"],
            "capabilities": {"inspect": True, "read_updates": True, "watch": True,
                             "incremental_reader": True, "reader_mutations": False,
                             "producer_event_timestamps": True, "monotonic_elapsed_seconds": True,
                             "stage_lifecycle": True, "backend_step_lifecycle": False,
                             "runtime_observation": False, "execution_resume": False,
                             "request_idempotency": False, "store_wide_journal": False,
                             "fsync_durability": False},
            "writer_policy": "nonblocking_advisory_workspace_flock; conflict=BUSY; no_takeover",
            "retention": "caller_retains_store_workspace_checkpoints_and_external_roots",
            "lost_writer": "last_recorded_facts_preserved; outer_outcome_unknown",
            "watch": {"mode": "foreground_in_process_read_only", "interval_seconds": 0.5,
                      "interval_rule": "positive_finite", "reconnect": "stream_id_and_after_required_together",
                      "initial_replay": "validate_complete_prefix; deliver_events_after_cursor",
                      "idle": "metadata_checks_and_liveness; no_record_reparse; suppress_duplicate_status",
                      "release": "drain_committed_tail_after_inactive_or_unknown_lock",
                      "prefix_integrity": "dev_inode_size_mtime_ns_ctime_ns; trusted_local_immutable_convention",
                      "exit_codes": {"recorded_terminal": 0, "writer_lost_or_unknown": 3,
                                     "query_error": 2, "reader_sigint": 130},
                      "delivery": "flushed_JSONL; updates_only_when_nonempty; changed_status_and_final_status"},
            "schemas": {
                "legacy_record": {"contract": CONTRACT, "schema_version": 1, "stream_id": "32_lowercase_hex",
                                  "sequence": "positive_integer", "previous_sha256": "sha256_or_null_at_1",
                                  "event": "declared_event_enum", "data": "event_specific_object",
                                  "sha256": "canonical_unsigned_record_sha256"},
                "record": {"contract": CONTRACT, "schema_version": VERSION, "stream_id": "32_lowercase_hex",
                           "sequence": "positive_integer", "previous_sha256": "sha256_or_null_at_1",
                           "recorded_at": "canonical_UTC_with_six_fractional_digits_and_Z",
                           "elapsed_seconds": "finite_nonnegative_number_nondecreasing",
                           "event": "declared_event_enum", "data": "event_specific_object", "sha256": "canonical_unsigned_record_sha256"},
                "event_data": {
                    "allocated": {"request": "object", "workspace": "absolute_path", "checkpoint_store": "absolute_path",
                                  "lock_path": "absolute_path", "preflight_record": "absolute_path"},
                    "planned": {"plan": "StagePlan_schema_2_or_3"},
                    "prepared": {"runtime_record": "absolute_path_or_null"},
                    "started|published|finished": {"occurrence": "occurrence", "artifact": "checkpoint_reference_or_null"},
                    "outcome": {"status": "completed|failed|interrupted", "exit_code": "integer", "error": "string_or_null"}},
                "occurrence": {"occurrence_id": "stream_id/action_key", "action_key": "implementation.boundary",
                               "status": "unreached|running|completed|failed|interrupted", "exit_code": "integer_or_null",
                               "duration_seconds": "nonnegative_number", "best_effort": False,
                               "error": "string_or_null", "verdict": None},
                "checkpoint_reference": {"path": "absolute_path", "identity": "manifest_sha256", "boundary": "stage_name",
                                         "restart_supported": "boolean", "restart_targets": "stage_name_array",
                                         "closure_scope": "path_bound", "tested_image_id": "image_id", "acceptance": "not_evaluated"},
                "inspect": {"contract": CONTRACT, "schema_version": VERSION, "stream_id": "32_lowercase_hex",
                            "cursor": "prefix_length", "state": "reduced_state", "writer_active": "boolean_or_null",
                            "observation_status": "final|live|writer_lost_or_unknown", "final_results": "final_results"},
                "reduced_state": {"attempt_id": "stream_id", "request": "object", "workspace": "absolute_path",
                                  "checkpoint_store": "absolute_path", "lock_path": "absolute_path", "preflight_record": "absolute_path",
                                  "phase": "preparing|executing|finished", "plan": "StagePlan_or_null", "occurrences": "occurrence_array",
                                  "artifacts": "checkpoint_reference_array", "runtime_record": "absolute_path_or_null", "outcome": "outcome_data_or_null"},
                "final_results": {"physical_completion": "completed|not_recorded", "checkpoint_refs": "checkpoint_reference_array",
                                  "acceptance": "not_evaluated", "external_postcheck": "not_evaluated"},
                "read_updates": {"contract": CONTRACT, "schema_version": VERSION, "stream_id": "32_lowercase_hex",
                                  "after": "requested_cursor", "cursor": "prefix_length", "events": "record_array"},
                "watch_updates": {"event": "updates", "contract": CONTRACT, "schema_version": VERSION,
                                  "stream_id": "32_lowercase_hex", "after": "requested_or_last_delivered_cursor",
                                  "cursor": "validated_prefix_length", "events": "complete_record_array_after_cursor"},
                "watch_status": {"event": "status", "contract": CONTRACT, "schema_version": VERSION,
                                 "stream_id": "32_lowercase_hex", "cursor": "validated_prefix_length",
                                 "writer_active": "boolean_or_null", "observation_status": "final|live|writer_lost_or_unknown",
                                 "outcome": "outcome_data_or_null", "final_results": "final_results"},
                "error": {"contract": CONTRACT, "schema_version": 1,
                          "error": {"code": "INVALID_RECORD|RESET_REQUIRED|BUSY|EXECUTION_FAILED", "detail": "string"}}}}


def reduce_event(state, event, data, stream):
    """Closed transition grammar; corrupt/incomplete prefixes never imply success."""
    require(isinstance(data, dict), "event data must be an object")
    if state is None:
        require(event == "allocated" and set(data) == {"request", "workspace", "checkpoint_store", "lock_path", "preflight_record"}, "missing allocation")
        require(isinstance(data["request"], dict) and all(isinstance(data[k], str) and Path(data[k]).is_absolute()
                for k in ("workspace", "checkpoint_store", "lock_path", "preflight_record")), "invalid allocation paths")
        return {"attempt_id": stream, **deepcopy(data), "phase": "preparing", "plan": None,
                "occurrences": [], "artifacts": [], "runtime_record": None, "outcome": None}
    require(state["outcome"] is None, "event after final outcome")
    state = deepcopy(state)
    if event == "planned":
        require(state["plan"] is None and set(data) == {"plan"}, "duplicate plan")
        plan = StagePlan.from_dict(data["plan"])
        state["plan"] = plan.as_dict()
        state["occurrences"] = [Occurrence(f"{stream}/{a.key}", a.key).as_dict() for a in plan.actions]
    elif event == "prepared":
        require(state["plan"] is not None and state["phase"] == "preparing" and set(data) == {"runtime_record"}, "invalid preparation")
        require(data["runtime_record"] is None or (isinstance(data["runtime_record"], str) and Path(data["runtime_record"]).is_absolute()), "invalid runtime reference")
        state.update(phase="executing", runtime_record=data["runtime_record"])
    elif event in ("started", "published", "finished"):
        require(state["phase"] == "executing" and set(data) == {"occurrence", "artifact"}, "invalid lifecycle fields")
        occurrence, artifact = data["occurrence"], data["artifact"]
        require(isinstance(occurrence, dict) and set(occurrence) == set(Occurrence.__dataclass_fields__), "invalid occurrence")
        matches = [i for i, old in enumerate(state["occurrences"]) if old["occurrence_id"] == occurrence["occurrence_id"]]
        require(len(matches) == 1, "unknown occurrence")
        i = matches[0]
        old = state["occurrences"][i]
        require(occurrence["action_key"] == old["action_key"] and occurrence["best_effort"] is False
                and occurrence["verdict"] is None, "invalid stage identity/verdict")
        require(type(occurrence["duration_seconds"]) in (int, float) and math.isfinite(occurrence["duration_seconds"]) and occurrence["duration_seconds"] >= 0
                and (occurrence["error"] is None or isinstance(occurrence["error"], str)), "invalid occurrence facts")
        if event == "started":
            require(old["status"] == "unreached" and occurrence["status"] == "running"
                    and occurrence["exit_code"] is None and artifact is None
                    and all(o["status"] == "completed" for o in state["occurrences"][:i]), "invalid start order")
        else:
            require(old["status"] == "running", "lifecycle without start")
            if event == "published":
                require(occurrence == old and isinstance(artifact, dict)
                        and set(artifact) == {"identity", "path", "boundary", "restart_supported", "restart_targets", "closure_scope", "tested_image_id", "acceptance"}
                        and artifact["acceptance"] == "not_evaluated" and artifact["closure_scope"] == "path_bound", "invalid publication")
                require(artifact["boundary"] == occurrence["action_key"].removeprefix("implementation.")
                        and isinstance(artifact["identity"], str) and len(artifact["identity"]) == 64
                        and all(c in "0123456789abcdef" for c in artifact["identity"]) and isinstance(artifact["path"], str)
                        and Path(artifact["path"]).is_absolute() and type(artifact["restart_supported"]) is bool
                        and not any(a["boundary"] == artifact["boundary"] for a in state["artifacts"]), "invalid artifact")
                capability = boundary_capabilities()[artifact["boundary"]]
                targets = [capability["restart_targets"]]
                targets += {"synthesis": [[], ["floorplan"]], "timing": [[]],
                            "routing": [["signoff"]]}.get(artifact["boundary"], [])
                require(artifact["restart_targets"] in targets
                        and artifact["restart_supported"] == bool(artifact["restart_targets"])
                        and all(artifact[k] == capability[k] for k in
                                ("tested_image_id", "closure_scope", "acceptance")), "invalid checkpoint capability")
                state["artifacts"].append(deepcopy(artifact))
            else:
                require(occurrence["status"] in ("completed", "failed", "interrupted")
                        and type(occurrence["exit_code"]) is int, "invalid finish")
                published = next((a for a in state["artifacts"] if a["boundary"] == occurrence["action_key"].removeprefix("implementation.")), None)
                require((occurrence["status"] == "completed" and occurrence["exit_code"] == 0 and artifact == published and published is not None)
                        or (occurrence["status"] in ("failed", "interrupted") and occurrence["exit_code"] != 0 and artifact is None), "unearned completion")
        state["occurrences"][i] = deepcopy(occurrence)
    elif event == "outcome":
        require(set(data) == {"status", "exit_code", "error"} and data["status"] in ("completed", "failed", "interrupted")
                and type(data["exit_code"]) is int and (data["error"] is None or isinstance(data["error"], str)), "invalid outcome")
        if data["status"] == "completed":
            require(state["phase"] == "executing" and data["exit_code"] == 0 and state["occurrences"]
                    and all(o["status"] == "completed" for o in state["occurrences"]), "unearned outer success")
        else:
            require(data["exit_code"] != 0, "failure with zero exit")
        state.update(phase="finished", outcome=deepcopy(data))
    else:
        raise ObservationError("INVALID_RECORD", "unknown event")
    return state


class Writer:
    """Hold the workspace lock across allocation, preparation and runner execution."""
    def __init__(self, root, *, workspace, checkpoint_store, request):
        workspace = Path(workspace).resolve()
        root = Path(root).resolve()
        if root.is_relative_to(workspace):
            raise ValueError("observation store must be outside the fresh/path-bound workspace")
        lock_path = workspace.parent / (".stage-writer-" + digest(str(workspace)) + ".lock")
        self.lock = lock_path.open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise ObservationError("BUSY", "workspace already has an observation writer") from exc
        except BaseException:
            self.lock.close()
            raise
        try:
            # Attempt creation starts immediately before allocation, after ownership.
            self.created_monotonic = time.monotonic()
            self.elapsed_seconds = None
            allocation = result_store.allocate(root)
            self.directory, self.stream_id = allocation.directory, allocation.identity
            activity_path = self.directory / "writer.lock"
            self.activity_lock = activity_path.open("a+b")
            fcntl.flock(self.activity_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.sequence, self.previous, self.state = 0, None, None
            self.append("allocated", {"request": request, "workspace": str(workspace),
                        "checkpoint_store": str(Path(checkpoint_store).resolve()), "lock_path": str(activity_path),
                        "preflight_record": str(self.directory / "preflight.json")})
        except BaseException:
            self.close()
            raise

    def close(self):
        if getattr(self, "activity_lock", None) is not None:
            self.activity_lock.close()
        self.lock.close()

    def append(self, event, data):
        require(not self.lock.closed, "writer is closed; no append after ownership release")
        state = reduce_event(self.state, event, data, self.stream_id)
        value = {"contract": CONTRACT, "schema_version": VERSION, "stream_id": self.stream_id,
                 "sequence": self.sequence + 1, "previous_sha256": self.previous,
                 "event": event, "data": deepcopy(data),
                 "recorded_at": recorded_at_now(),
                 "elapsed_seconds": time.monotonic() - self.created_monotonic}
        validate_timing(value, self.elapsed_seconds)
        value["sha256"] = digest(value)
        path = self.directory / f"{value['sequence']:012d}.json"
        require(not path.exists(), "record overwrite conflict")
        result_store.save_json(path, value)
        self.sequence, self.previous, self.state = value["sequence"], value["sha256"], state
        self.elapsed_seconds = value["elapsed_seconds"]

    def lifecycle(self, event, action, occurrence, artifact):
        self.append(event, {"occurrence": occurrence, "artifact": artifact})

    def run(self, plan_factory, prepare, *, finalize=None):
        """Allocation is already visible before either callback is entered."""
        with result_store.interruption_handlers():
            try:
                plan = plan_factory()
                self.append("planned", {"plan": plan.as_dict()})
                adapter = prepare()
                self.append("prepared", {"runtime_record": str(adapter.runtime_record) if getattr(adapter, "runtime_record", None) else None})
                execution = execute(plan, adapter, attempt_id=self.stream_id, lifecycle=self.lifecycle)
                if finalize is not None:
                    finalize(execution)
                self.append("outcome", {"status": execution.status, "exit_code": execution.exit_code, "error": None})
                return execution
            except (Exception, KeyboardInterrupt) as exc:
                signum = signal.SIGINT if isinstance(exc, KeyboardInterrupt) else getattr(exc, "signum", None)
                self.append("outcome", {"status": "interrupted" if signum else "failed",
                            "exit_code": 128 + signum if signum else 2, "error": str(exc)})
                raise


def validate_record(value, *, sequence, stream, previous, state, schema_version=None, previous_elapsed=None):
    """Shared strict record and transition validator for cold and warm readers."""
    require(isinstance(value, dict), "record must be an object")
    version = value.get("schema_version")
    require(type(version) is int and version in SUPPORTED_VERSIONS
            and (schema_version is None or version == schema_version), "unsupported or mixed record schema")
    fields = {"contract", "schema_version", "stream_id", "sequence", "previous_sha256", "event", "data", "sha256"}
    require(set(value) == fields | ({"recorded_at", "elapsed_seconds"} if version == 2 else set()), "invalid record fields")
    require(value["contract"] == CONTRACT
            and type(value["sequence"]) is int and value["sequence"] == sequence, "unsupported record")
    if version == 2:
        validate_timing(value, previous_elapsed)
    if stream is None:
        stream = value["stream_id"]
        require(isinstance(stream, str) and len(stream) == 32 and all(c in "0123456789abcdef" for c in stream), "invalid stream identity")
    require(value["stream_id"] == stream and value["previous_sha256"] == previous
            and value["sha256"] == digest({k: v for k, v in value.items() if k != "sha256"}), "record chain mismatch")
    try:
        state = reduce_event(state, value["event"], value["data"], stream)
    except (KeyError, TypeError, ValueError) as exc:
        raise ObservationError("INVALID_RECORD", str(exc)) from exc
    return state, stream


def record_metadata(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "record must be a regular non-symlink file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class IncrementalReader:
    """Read-only local immutable-prefix reader. Idle polls stat, never reparse.

    Metadata detection is not protection against a hostile filesystem owner.
    A failed reader must be discarded; reconnect validates the full prefix again.
    """
    def __init__(self, directory, *, stream_id=None, after=None):
        if (stream_id is None) != (after is None):
            raise ObservationError("RESET_REQUIRED", "stream-id and after are required together")
        self.directory = Path(directory).resolve()
        self.state, self.stream_id, self.previous = None, None, None
        self.events, self.metadata = [], []
        self._refresh()
        after = 0 if after is None else after
        if (stream_id is not None and stream_id != self.stream_id) or type(after) is not int or not 0 <= after <= len(self.events):
            raise ObservationError("RESET_REQUIRED", "wrong stream or cursor outside retained prefix")
        self.after = after

    def _refresh(self):
        records = sorted(self.directory.glob("[0-9]*.json"))
        require(bool(records), "missing allocation record")
        require(len(records) >= len(self.metadata), "retained prefix deleted")
        for sequence, path in enumerate(records, 1):
            require(path.name == f"{sequence:012d}.json", "journal gap or malformed record name")
            before = record_metadata(path)
            if sequence <= len(self.metadata):
                require(before == self.metadata[sequence - 1], "retained prefix changed or replaced")
                continue
            value = strict_read(path)
            state, stream = validate_record(value, sequence=sequence, stream=self.stream_id,
                                            previous=self.previous, state=self.state,
                                            schema_version=self.events[0]["schema_version"] if self.events else None,
                                            previous_elapsed=self.events[-1].get("elapsed_seconds") if self.events else None)
            require(record_metadata(path) == before, "record changed during read")
            self.state, self.stream_id, self.previous = state, stream, value["sha256"]
            self.events.append(value)
            self.metadata.append(before)

    def poll(self):
        """Return (complete updates payload, reader status); drain after release."""
        self._refresh()
        active = writer_active(self.state["lock_path"])
        if active is not True:
            # A writer can promote its final record between listing and unlocking.
            self._refresh()
        version = self.events[0]["schema_version"]
        updates = {"contract": CONTRACT, "schema_version": version, "stream_id": self.stream_id,
                   "after": self.after, "cursor": len(self.events), "events": deepcopy(self.events[self.after:])}
        self.after = len(self.events)
        snapshot = observation_snapshot(self.state, len(self.events), active, schema_version=version)
        status = {k: snapshot[k] for k in ("contract", "schema_version", "stream_id", "cursor",
                                          "writer_active", "observation_status", "final_results")}
        status["outcome"] = deepcopy(self.state["outcome"])
        return updates, status


def load(directory):
    reader = IncrementalReader(directory)
    return reader.state, reader.events


def writer_active(lock_path):
    try:
        with Path(lock_path).open("rb") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except OSError:
        return None


def inspect(directory):
    state, events = load(directory)
    active = writer_active(state["lock_path"])
    return observation_snapshot(state, len(events), active, schema_version=events[0]["schema_version"])


def observation_snapshot(state, cursor, active, *, schema_version=VERSION):
    outcome = state["outcome"]
    physical = any(a["boundary"] == "signoff" for a in state["artifacts"]) and any(
        o["action_key"] == "implementation.signoff" and o["status"] == "completed" for o in state["occurrences"])
    return {"contract": CONTRACT, "schema_version": schema_version, "stream_id": state["attempt_id"],
            "cursor": cursor, "state": deepcopy(state), "writer_active": active,
            "observation_status": "final" if outcome else "live" if active else "writer_lost_or_unknown",
            "final_results": {"physical_completion": "completed" if physical else "not_recorded",
                              "checkpoint_refs": deepcopy(state["artifacts"]), "acceptance": "not_evaluated",
                              "external_postcheck": "not_evaluated"}}


def read_updates(directory, *, stream_id, after=0):
    state, events = load(directory)
    if stream_id != state["attempt_id"] or type(after) is not int or not 0 <= after <= len(events):
        raise ObservationError("RESET_REQUIRED", "wrong stream or cursor outside retained prefix")
    return {"contract": CONTRACT, "schema_version": events[0]["schema_version"], "stream_id": stream_id,
            "after": after, "cursor": len(events), "events": events[after:]}
