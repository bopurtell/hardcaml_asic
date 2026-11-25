"""Independent review repro regression over a genuine emitted bundle envelope.
Tool events/netlist/reports remain synthetic. Fresh CLI children measure RSS;
no address-space cap (incompatible with this OCaml runtime's heap reservation).
"""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

generator, helper = (Path(x).resolve() for x in sys.argv[1:])
baseline = json.loads(subprocess.check_output([str(generator), "--emit-real-fixture"], text=True))
inputs = {"manifest", "sdc", "netlist", "liberty", "driver"}
cases = 0


def encode(j):
    return json.dumps(j, ensure_ascii=False, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def coherent(files, receipt_change=lambda r: r):
    audit = json.loads(files["audit.json"])
    receipt = json.loads(files["receipt.txt"])
    build = json.loads(files["manifest.txt"])["identity"]
    audit["build_identity"] = receipt["build_identity"] = build
    receipt["inputs"] = [dict(source, sha256=sha(files[source["path"]]))
                         for source in audit["sources"] if source["role"] in inputs]
    receipt = receipt_change(receipt)
    files["receipt.txt"] = encode(receipt)
    log = files["log.txt"]
    prefix, marker, rest = log.partition("PT7_AUDIT_RECEIPT ")
    assert marker
    _, newline, suffix = rest.partition("\n")
    assert newline
    files["log.txt"] = prefix + marker + files["receipt.txt"] + "\n" + suffix
    process = json.loads(files["process_result.txt"])
    process["receipt_sha256"] = sha(files["receipt.txt"])
    files["process_result.txt"] = encode(process)
    for source in audit["sources"]:
        source["sha256"] = sha(files[source["path"]])
    files["audit.json"] = encode(audit)


def resign_payload(raw):
    # Identity is the first field of the real library-emitted manifest. Preserve
    # its exact remaining numeric/object serialization, including ordered payload.
    payload = "{" + raw.split(",", 1)[1]
    return '{"identity":' + encode(sha(payload)) + "," + payload[1:]


def mutate_manifest(change):
    def mutate(files):
        manifest = json.loads(files["manifest.txt"])
        change(manifest)
        # This test-only codec only reserializes/reseals bytes, not validators:
        # Yojson's real compact/pretty float spelling is authoritative for bundle
        # identity and the config member. Do not let a Python formatter mismatch
        # turn an envelope contradiction repro into an unrelated checksum failure.
        files["manifest.txt"] = subprocess.check_output(
            [str(generator), "--seal-manifest"], input=encode(manifest) + "\n", text=True).strip()
    return mutate


def inspect(root, name, mutate=lambda _: None, receipt_change=lambda r: r, expected=1, error=None, base=baseline):
    global cases
    cases += 1
    files = dict(base)
    mutate(files)
    coherent(files, receipt_change)
    directory = root / name
    directory.mkdir()
    for path, raw in files.items():
        (directory / path).write_text(raw)
    with (root / (name + ".out")).open("wb") as out, (root / (name + ".err")).open("wb") as err:
        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        p = subprocess.Popen([str(helper), "--json", str(directory)], stdout=out, stderr=err, preexec_fn=limits)
        _, status, usage = os.wait4(p.pid, 0)
        p.returncode = os.waitstatus_to_exitcode(status)
    stderr = (root / (name + ".err")).read_text()
    assert p.returncode == expected, (name, p.returncode, stderr)
    if error:
        assert error in stderr, (name, stderr)
    if expected == 1:
        evidence = json.loads((root / (name + ".out")).read_text())
        assert evidence["verdict"] == "unavailable"
        assert evidence["source_authentication"] == "hash_checked_retained_producer_assertions_not_authenticated_execution"
    return usage.ru_maxrss


with tempfile.TemporaryDirectory(prefix="timing-evidence-regression-") as temp:
    root = Path(temp)
    rss = inspect(root, "baseline")
    inspect(root, "producer-order", receipt_change=lambda r: dict(r, producer=dict(reversed(list(r["producer"].items())))))
    inspect(root, "input-object-order", receipt_change=lambda r: dict(r, inputs=[dict(reversed(list(x.items()))) for x in r["inputs"]]))
    inspect(root, "input-array-order", receipt_change=lambda r: dict(r, inputs=list(reversed(r["inputs"]))), expected=2, error="input closure mismatch")
    inspect(root, "schema999", lambda f: f.update({"manifest.txt": resign_payload(f["manifest.txt"].replace('"schema_version":1,', '"schema_version":999,', 1))}), expected=2, error="unsupported bundle envelope")
    def minimal(f):
        m = json.loads(f["manifest.txt"])
        payload = {"schema_version": 999, "files": m["files"], "timing_intent": m["timing_intent"]}
        f["manifest.txt"] = encode(dict(identity=sha(encode(payload)), **payload))
    inspect(root, "minimal-self-hash", minimal, expected=2, error="invalid audit object fields")
    inspect(root, "sdc-prefix-fixed-build", lambda f: f.update({"sdc.txt": "set_false_path -from [get_ports {*} ]\n" + f["sdc.txt"]}), expected=2, error="SDC differs from emitted bundle")
    for name, line in [("bracket-error", "[ERROR STA-0001] synthetic fatal"),
                       ("ansi-bracket-error", "\x1b[31m[ERROR STA-0001] synthetic fatal\x1b[0m"),
                       ("bracket-fatal", "[FATAL STA-0001] synthetic fatal")]:
        inspect(root, name, lambda f, line=line: f.update({"log.txt": line + "\n" + f["log.txt"]}), expected=2, error="retained STA error")
    inspect(root, "info-quoted-error", lambda f: f.update({"log.txt": '[INFO STA-0001] quoted "[ERROR STA-0001]" example\n' + f["log.txt"]}))
    large_rss = inspect(root, "two-million-lines", lambda f: f.update({"log.txt": "\n" * 2_000_000 + f["log.txt"]}), expected=2, error="line budget exceeded")
    assert large_rss <= rss + 32 * 1024, ("log memory amplification", rss, large_rss)
    inspect(root, "oversized-line", lambda f: f.update({"log.txt": "x" * 65537 + "\n" + f["log.txt"]}), expected=2, error="line exceeds 64KiB")
    inspect(root, "generated-missing-null", mutate_manifest(lambda m: m["generated_settings"].update(REVIEW_MISSING_KEY=None)),
            expected=2, error="missing bundle configuration key: REVIEW_MISSING_KEY")
    def present_null(m):
        m["generated_settings"]["REVIEW_PRESENT_NULL"] = None
        m["flow_configuration"]["REVIEW_PRESENT_NULL"] = None
    inspect(root, "generated-present-null", mutate_manifest(present_null))
    def different_null(m):
        present_null(m)
        m["flow_configuration"]["REVIEW_PRESENT_NULL"] = "different"
    inspect(root, "generated-different-null", mutate_manifest(different_null), expected=2, error="generated/configuration mismatch")
    def missing_resolved(m):
        m["resolved_settings"].append(dict(key="REVIEW_RESOLVED_NULL", value=None, owner="review fixture"))
    inspect(root, "resolved-missing-null", mutate_manifest(missing_resolved), expected=2, error="missing bundle configuration key: REVIEW_RESOLVED_NULL")
    inspect(root, "simulation-is-sdc", mutate_manifest(lambda m: m.update(simulation_sources=["constraints/top.sdc"])),
            expected=2, error="simulation RTL/top mismatch")
    inspect(root, "empty-runtime-policy", mutate_manifest(lambda m: m["requested_tools"].update(runtime_policy={})),
            expected=2, error="invalid bundle runtime policy")
    inspect(root, "unknown-runtime-policy-schema", mutate_manifest(lambda m: m["requested_tools"]["runtime_policy"].update(schema_version=999)),
            expected=2, error="invalid bundle runtime policy")
    inspect(root, "contradictory-runtime-host", mutate_manifest(lambda m: m["requested_tools"]["runtime_policy"]["host"]["python"].update(version="9.99")),
            expected=2, error="invalid bundle runtime policy")
    def floorplan_value(m, value):
        for setting in m["resolved_settings"]:
            if setting["key"] == "FP_DEF_TEMPLATE":
                setting["value"] = value
                return
        raise AssertionError("real emitter omitted FP_DEF_TEMPLATE")
    inspect(root, "fp-template-object", mutate_manifest(lambda m: floorplan_value(m, {"not": "a reference"})),
            expected=2, error="FP_DEF_TEMPLATE/floorplan reference mismatch")
    inspect(root, "fp-template-other-path", mutate_manifest(lambda m: floorplan_value(m, "other.def")),
            expected=2, error="FP_DEF_TEMPLATE/floorplan reference mismatch")
    inspect(root, "fp-template-bad-rewrite", mutate_manifest(lambda m: m["flow_configuration"].update(FP_DEF_TEMPLATE="dir::../tt/other.def")),
            expected=2, error="FP_DEF_TEMPLATE/floorplan reference mismatch")
    inspect(root, "fp-template-missing", mutate_manifest(lambda m: m.update(resolved_settings=[x for x in m["resolved_settings"] if x["key"] != "FP_DEF_TEMPLATE"])),
            expected=2, error="missing bundle FP_DEF_TEMPLATE resolved setting")
    def reverse_objects(j):
        if isinstance(j, dict):
            return {k: reverse_objects(v) for k, v in reversed(list(j.items()))}
        if isinstance(j, list):
            return [reverse_objects(v) for v in j]
        return j
    inspect(root, "runtime-object-key-order", mutate_manifest(lambda m: m["requested_tools"].update(runtime_policy=reverse_objects(m["requested_tools"]["runtime_policy"]))))
    real_simulation = json.loads(subprocess.check_output([str(generator), "--emit-real-simulation-fixture"], text=True))
    inspect(root, "real-separate-simulation-RTL", base=real_simulation)
    print(f"Independent evidence review regression: {cases} cases passed; RSS baseline={rss} KiB, two-million-line={large_rss} KiB (<= baseline+32MiB).")
