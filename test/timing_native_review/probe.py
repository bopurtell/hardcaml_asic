"""B's independent A-contract review. No imported A tests and no EDA.

Runs public OCaml APIs and the actual emitted producer with independent Tcl API
models. The three corrected findings are asserted regressions; unrelated
characterizations remain labelled observations. Results live under /tmp/opencode.
"""
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

CONSUMER, EMITTER = [Path(p).resolve() for p in sys.argv[1:]]
NAMES = ["no_input_delay", "no_output_delay", "multiple_clock", "no_clock", "unconstrained_endpoints", "loops", "generated_clocks"]
results = []
CASES = Path(tempfile.mkdtemp(prefix="native-timing-review-cases-", dir="/tmp/opencode"))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, j):
    p.write_text(json.dumps(j, separators=(",", ":"), ensure_ascii=False) + "\n")


def invoke(args, **kwargs):
    extra_env = kwargs.pop("extra_env", {})
    return subprocess.run(list(map(str, args)), text=True, capture_output=True, timeout=40,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **extra_env), **kwargs)


def api(kind, target):
    p = invoke([CONSUMER, kind, target])
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    return json.loads(p.stdout)


def record(label, result, expected=None, diagnostic=None, **facts):
    row = dict(label=label, accepted=result["ok"], **facts)
    if result["ok"]:
        value = result["value"]
        native = value.get("native_coverage", value)
        row["relation_count"] = native.get("stored_exception_scope", {}).get("relation_count")
        row["overall_verdict"] = value.get("verdict")
    else:
        row["error"] = result["error"]
    results.append(row)
    if label in ("candidate-schema2-comment-hides-scope", "candidate-schema2-category-relabel", "candidate-native-current-design-mismatch",
                 "candidate-schema2-bare-CR-extra-scope", "candidate-schema2-bare-CR-masking", "candidate-schema2-bare-CR-design-selection"):
        target = CASES / label
        shutil.copytree(audit_root, target)
        row["audit_repro"] = str(target)
    if expected is not None:
        assert row["accepted"] == expected, row
    if diagnostic is not None:
        assert not result["ok"] and diagnostic in result["error"], row
    return result


def data(groups=None, endpoints=None):
    groups = groups or {}
    endpoints = ["u0/D", "u1/D"] if endpoints is None else endpoints
    cats = [dict(name=n, group_count=len(groups.get(n, [])), groups=groups.get(n, [])) for n in NAMES]
    j = dict(schema=2, contract="hardcaml-asic.timing-native-coverage", profile="opensta-2.7-native-state-v2",
             complete=True, categories=cats, endpoint_count=len(endpoints), endpoints=endpoints)
    checks = "HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2\n" + "".join(
        "HC_ASIC_NATIVE_CATEGORY_BEGIN " + c["name"] + "\n" +
        "".join(g["message"] + "\n" + "".join("  " + d + "\n" for d in g["details"]) for g in c["groups"]) +
        "HC_ASIC_NATIVE_CATEGORY_END " + c["name"] + "\n" for c in cats) + "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n"
    report = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in endpoints) + "HC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n"
    return j, checks, report


BASE = "set_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]\n"
RESOLVED = [dict(source="ena", targets=["u0/D"])]

with tempfile.TemporaryDirectory(prefix="native timing independent ") as temporary:
    root = Path(temporary)
    def native(label, sdc=BASE, resolved=RESOLVED, native_data=None, checks=None, endpoints=None, expected=None,
               top="top", raw_sdc=False, diagnostic=None):
        d, raw, eps = data() if native_data is None else native_data
        request = dict(top=top, resolved=resolved, native_sdc=sdc if raw_sdc or sdc.startswith("current_design ") else "current_design " + top + "\n" + sdc,
                       native_data=d if isinstance(d, str) else json.dumps(d, ensure_ascii=False),
                       native_checks=raw if checks is None else checks,
                       native_endpoints=eps if endpoints is None else endpoints)
        path = root / "request.json"
        write(path, request)
        result = record(label, api("native", path), expected, diagnostic=diagnostic)
        if label.startswith("candidate-"):
            saved = CASES / (label + ".json")
            shutil.copyfile(path, saved)
            results[-1]["native_repro"] = str(saved)
        return result

    native("baseline", expected=True)
    native("real-writer-continuation", sdc="set_false_path\\\n -from [get_ports {ena}]\\\n -to [get_pins {u0/D}]\n", expected=True)
    for command in ("set_disable_timing -from CLK -to Q [get_cells u0]", "set_case_analysis 0 [get_ports ena]",
                    "set_clock_groups -asynchronous -group [get_clocks clk]", "set_min_delay 0 -to [get_pins u1/D]",
                    "set_max_delay 0 -to [get_pins u1/D]", "reset_path -from [get_ports ena]", "set_disable_inferred_clock_gating true"):
        native("masking-" + command.split()[0], sdc=BASE + command + "\n", expected=False)
    for flag in ("-setup", "-hold", "-rise_from", "-fall_to", "-through"):
        native("restricted-" + flag, sdc=BASE.replace(" -from", " " + flag + " -from"), expected=False)
    sources, targets = ["a", "b"], ["u0/D", "u1/D"]
    cartesian = [dict(source=s, targets=targets) for s in sources]
    merged = "set_false_path -to [get_pins {u0/D u1/D}] -from [get_ports {a b}]\n"
    native("Cartesian-merged-4", sdc=merged, resolved=cartesian, expected=True)
    split = "".join(f"set_false_path -from [get_ports {{{s}}}] -to [get_pins {{{t}}}]\n" for s, t in itertools.product(sources, targets))
    native("Cartesian-split-4", sdc=split + split, resolved=cartesian, expected=True)
    native("Cartesian-is-not-zipped", sdc=merged, resolved=[dict(source="a", targets=["u0/D"]), dict(source="b", targets=["u1/D"])], expected=False)
    many_sources = [f"p{i:03d}" for i in range(256)]
    many_targets = [f"u{i:03d}/D" for i in range(256)]
    many_sdc = "set_false_path -from [get_ports {" + " ".join(many_sources) + "}] -to [get_pins {" + " ".join(many_targets) + "}]\n"
    many_resolved = [dict(source=s, targets=many_targets) for s in many_sources]
    native("Cartesian-bound-65536", sdc=many_sdc, resolved=many_resolved, expected=True)
    native("Cartesian-bound-65792", sdc=many_sdc.replace("{p000", "{extra p000"), resolved=many_resolved, expected=False)
    native("nested-selector-lists", sdc="set_false_path -from [list [get_ports a] [list [get_ports b]]] -to [list [get_pins u0/D] [get_pins u1/D]]\n", resolved=cartesian, expected=True)
    native("bit-name", sdc=BASE.replace("{ena}", "{ui_in[3]}"), resolved=[dict(source="ui_in[3]", targets=["u0/D"])], expected=True)
    for selector in ("*", "ena?", "ena[ab]", "ena[0-3]", "", "-hierarchical ena"):
        native("selector-" + repr(selector), sdc=BASE.replace("{ena}", "{" + selector + "}"), expected=False)
    sentinel = root / "must-not-exist"
    for payload in (f"exec touch {sentinel}\n", f"source {sentinel}\n",
                    BASE.replace("[get_ports {ena}]", f"[exec touch {sentinel}]"),
                    BASE + f"set_load [get_ports ena; exec touch {sentinel}] 1\n",
                    BASE + f"set_load [list [eval {{exec touch {sentinel}}}]] 1\n"):
        native("Tcl-nonexecution-" + str(len(results)), sdc=payload, expected=False)
        assert not sentinel.exists()

    # Compare this lexical corner against the actual Tcl language, not an A fixture.
    commented = "# harmless-looking continued comment \\\n" + BASE
    native("candidate-comment-hides-required-relation", sdc=commented, expected=False,
           diagnostic="continued native SDC comment")
    language = root / "language.tcl"
    language.write_text("set calls 0\nproc get_ports {args} {return $args}\nproc get_pins {args} {return $args}\nproc set_false_path {args} {incr ::calls}\n" + commented + "puts $calls\n")
    p = invoke(["tclsh", language])
    assert p.returncode == 0 and p.stdout.strip() == "0", p
    results[-1]["actual_tcl_false_path_calls"] = 0

    # A real Tcl oracle establishes parity. Rejecting even-terminal-backslash
    # comments is conservative unsupported grammar, not a claim they hide scope.
    oracle = root / "oracle.tcl"
    oracle.write_text('''set calls 0
set selections {}
set design {}
proc current_design {args} {
    if {[llength $args]} {set ::design [lindex $args 0];lappend ::selections $::design}
    return $::design
}
proc get_ports {args} {return $args}
proc get_pins {args} {return $args}
proc get_cells {args} {return $args}
proc set_load {args} {}
proc set_disable_timing {args} {incr ::masks}
set masks 0
proc set_false_path {args} {incr ::calls}
if {[catch {source [lindex $argv 0]} message]} {puts stderr $message;puts $calls;exit 1}
puts $calls
''')
    def tcl_count(script):
        path = root / "oracle-input.sdc"
        path.write_bytes(script.encode())
        p = invoke(["tclsh", oracle, path])
        return p.returncode, int(p.stdout.strip()), p.stderr
    for newline_name, newline in (("LF", "\n"), ("CRLF", "\r\n")):
        for prefix_name, prefix in (("leading", ""), ("indented", " \t"), ("semicolon", "set_load 1 [get_ports ena]; ")):
            for slashes in range(7):
                script = "current_design top\n" + prefix + "# parity " + "\\" * slashes + newline + BASE
                code, calls, stderr = tcl_count(script)
                assert code == 0 and not stderr and calls == (1 if slashes % 2 == 0 else 0), (script, code, calls, stderr)
                native(f"comment-{newline_name}-{prefix_name}-slashes-{slashes}", sdc=script, raw_sdc=True,
                       expected=slashes == 0 and newline_name == "LF",
                       diagnostic="LF-only" if newline_name == "CRLF" else "continued native SDC comment" if slashes else None)
                results[-1].update(actual_tcl_false_path_calls=calls, backslash_count=slashes,
                                   policy="LF-only; ordinary LF accepted; all terminal-backslash comments unsupported")
    for tail in ("\\ ", "\\\t", "\\ text", "\\\\ "):
        script = "current_design top\n# not a continuation " + tail + "\n" + BASE
        assert tcl_count(script)[:2] == (0, 1)
        native("comment-trailing-whitespace-" + repr(tail), sdc=script, raw_sdc=True, expected=True)
    for quoted in (False, True):
        for slashes in range(1, 5):
            value = "x" + "\\" * slashes + "\n y"
            value = '"' + value + '"' if quoted else "{" + value + "}"
            script = "current_design top\nset_load " + value + " [get_ports ena]\n" + BASE
            assert tcl_count(script)[:2] == (0, 1)
            native(f"literal-continuation-quoted-{quoted}-slashes-{slashes}", sdc=script, raw_sdc=True, expected=True)
    for quoted in (False, True):
        value = "# literal comment-like text \\\nset_false_path -from [get_ports ena] -to [get_pins u0/D]"
        value = '"' + value.replace("[", "\\[").replace("]", "\\]") + '"' if quoted else "{" + value + "}"
        script = "current_design top\nset_load " + value + " [get_ports ena]\n"
        assert tcl_count(script)[:2] == (0, 0)
        native("literal-does-not-create-relation-" + str(quoted), sdc=script, raw_sdc=True, expected=False,
               diagnostic="missing exception relations")

    # A bare CR is a source-channel line ending in Tcl, not comment whitespace.
    # Keep the complete fixtures and fail the review at the end if the native
    # decoder hides a real extra relation or masking command behind it.
    hidden_extra = "# ordinary comment\rset_false_path -from [get_ports {ena}] -to [get_pins {u1/D}]\n" + BASE
    hidden_mask = "# ordinary comment\rset_disable_timing -from {CLK} -to {Q} [get_cells {u0}]\n" + BASE
    hidden_design = "# ordinary comment\rcurrent_design wrong_top\n" + BASE
    for label, script, expected_calls in (("extra-scope", hidden_extra, 2), ("masking", hidden_mask, 1), ("design-selection", hidden_design, 1)):
        assert tcl_count("current_design top\n" + script)[:2] == (0, expected_calls)
        native("candidate-bare-CR-" + label, sdc=script, expected=False, diagnostic="LF-only")
        results[-1].update(actual_tcl_false_path_calls=expected_calls, required_outcome="reject", open_issue="A-NATIVE-R1b")
    # Establish that the masker is recognized/rejected when LF exposes it.
    native("LF-visible-masker-control", sdc=hidden_mask.replace("\r", "\n"), expected=False,
           diagnostic="unsupported native SDC command: set_disable_timing")
    oracle_mask = root / "oracle-mask.tcl"
    oracle_mask.write_text(oracle.read_text().removesuffix("puts $calls\n") + 'puts "calls=$calls masks=$masks"\n')
    oracle_input = root / "mask-input.sdc"; oracle_input.write_bytes(("current_design top\n" + hidden_mask).encode())
    p = invoke(["tclsh", oracle_mask, oracle_input])
    assert p.returncode == 0 and p.stdout.strip() == "calls=1 masks=1", p
    oracle_design = root / "oracle-design.tcl"
    oracle_design.write_text(oracle.read_text().removesuffix("puts $calls\n") + 'puts "selections=[llength $selections] design=$design"\n')
    oracle_input.write_bytes(("current_design top\n" + hidden_design).encode())
    p = invoke(["tclsh", oracle_design, oracle_input])
    assert p.returncode == 0 and p.stdout.strip() == "selections=2 design=wrong_top", p
    native("LF-visible-design-selection-control", sdc=hidden_design.replace("\r", "\n"), expected=False,
           diagnostic="repeated/ambiguous")

    for spelling in ("top", "{top}", '"top"'):
        native("exact-native-top-" + spelling, sdc=" \n# ordinary\n;current_design " + spelling + "\n" + BASE,
               raw_sdc=True, expected=True)
    for label, script, error in (
        ("missing", BASE, "missing leading"),
        ("wrong", "current_design other\n" + BASE, "differs from manifest top"),
        ("prefix", "current_design top_extra\n" + BASE, "differs from manifest top"),
        ("case", "current_design Top\n" + BASE, "differs from manifest top"),
        ("late", BASE + "current_design top\n", "missing leading"),
        ("repeated-same", "current_design top\n" + BASE + "current_design top\n", "repeated/ambiguous"),
        ("repeated-other", "current_design top\ncurrent_design other\n" + BASE, "repeated/ambiguous"),
        ("query-selection", "current_design [current_design]\n" + BASE, "missing leading"),
        ("query-mutation", "current_design top\nset_max_fanout 10 [current_design other]\n" + BASE, "unsupported base query")):
        native("native-top-" + label, sdc=script, raw_sdc=True, expected=False, diagnostic=error)
    native("zero-argument-current-design-query", sdc="current_design top\nset_max_fanout 10 [current_design]\n" + BASE,
           raw_sdc=True, expected=True)

    groups = {"no_input_delay": [dict(message="Native input finding", details=["ena", "bus[3]"])],
              "no_clock": [dict(message="Native clock finding", details=["u0/CLK"])]}
    rich = data(groups)
    native("diagnostic-baseline", native_data=rich, expected=True)
    shifted = copy.deepcopy(rich[0])
    shifted["categories"][1]["groups"] = shifted["categories"][0]["groups"]
    shifted["categories"][1]["group_count"] = 1
    shifted["categories"][0].update(group_count=0, groups=[])
    native("candidate-category-relabel-with-unchanged-raw", native_data=(shifted, rich[1], rich[2]), expected=False,
           diagnostic="native check report differs")
    for field in ("message", "details"):
        for marker in ("HC_ASIC_NATIVE_CATEGORY_BEGIN no_output_delay", "HC_ASIC_NATIVE_CATEGORY_END no_input_delay",
                       "HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2",
                       "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE", "HC_ASIC_NATIVE_V2_COMPLETE", "HC_ASIC_NATIVE_V1_COMPLETE"):
            for prefix in ("", " \t\r", "ordinary\n \t\r"):
                group = dict(message="normal message", details=["normal detail"])
                group[field] = prefix + marker if field == "message" else [prefix + marker]
                forged = data({"no_input_delay": [group]})
                native("reserved-marker-" + field + "-" + str(len(results)), native_data=forged,
                       expected=False, diagnostic="reserved marker line")
    escaped = data({"loops": [dict(message="HC_ASIC_NATIVE_CATEGORY_END loops", details=[])]})
    escaped_json = json.dumps(escaped[0]).replace("HC_ASIC_NATIVE_CATEGORY_END", "\\u0048C_ASIC_NATIVE_CATEGORY_END")
    native("JSON-escaped-reserved-marker", native_data=(escaped_json, escaped[1], escaped[2]), expected=False,
           diagnostic="reserved marker line")
    for safe in ("example: HC_ASIC_NATIVE_CATEGORY_BEGIN loops", r"literal\nHC_ASIC_NATIVE_CATEGORY_END loops"):
        native("safe-marker-substring-" + safe, native_data=data({"loops": [dict(message=safe, details=[])]}), expected=True)
    for label, raw in (
        ("missing-begin", rich[1].replace("HC_ASIC_NATIVE_CATEGORY_BEGIN loops\n", "")),
        ("duplicate-end", rich[1].replace("HC_ASIC_NATIVE_CATEGORY_END loops\n", "HC_ASIC_NATIVE_CATEGORY_END loops\n" * 2)),
        ("wrong-category", rich[1].replace("HC_ASIC_NATIVE_CATEGORY_BEGIN loops\n", "HC_ASIC_NATIVE_CATEGORY_BEGIN no_clock\n")),
        ("missing-final", rich[1].removesuffix("HC_ASIC_NATIVE_CHECKS_V2_COMPLETE\n"))):
        native("raw-marker-" + label, native_data=rich, checks=raw, expected=False, diagnostic="native check report differs")
    for field in ("group_count", "groups"):
        changed = copy.deepcopy(rich[0])
        changed["categories"][0][field] = 0 if field == "group_count" else []
        native("diagnostic-forged-" + field, native_data=(changed, rich[1], rich[2]), expected=False)
    native("raw-missing-detail", native_data=rich, checks=rich[1].replace("  ena\n", ""), expected=False)
    native("raw-extra-finding", native_data=rich, checks="unreported finding\n" + rich[1], expected=False)
    extra_summary = dict(rich[0], stored_exception_scope=dict(status="matched"))
    native("forged-precomputed-summary", native_data=(extra_summary, rich[1], rich[2]), expected=False)
    incomplete = copy.deepcopy(rich[0]); incomplete["complete"] = False
    native("native-data-incomplete", native_data=(incomplete, rich[1], rich[2]), expected=False)
    categories_missing = copy.deepcopy(rich[0]); categories_missing["categories"].pop()
    native("category-missing", native_data=(categories_missing, rich[1], rich[2]), expected=False)
    native("endpoint-truncated-marker", endpoints='"u0/D"\n"u1/D"\n', expected=False)
    native("endpoint-wrong-raw", endpoints='"other/D"\nHC_ASIC_NATIVE_ENDPOINTS_COMPLETE\n', expected=False)
    native("endpoint-escaped-newline-and-quotes", native_data=data(endpoints=['a\n"b\\c', "u0/D"]), expected=True)
    native("candidate-empty-endpoints-with-resolved-flops", native_data=data(endpoints=[]))
    native("endpoint-bound-65536", native_data=data(endpoints=[f"p{i:05d}/D" for i in range(65536)]), expected=True)
    native("endpoint-bound-65537", native_data=data(endpoints=[f"p{i:05d}/D" for i in range(65537)]), expected=False)
    native("diagnostic-bound-65536", native_data=data({"loops": [dict(message="loop", details=["d"] * 65535)]}), expected=True)
    native("diagnostic-bound-65537", native_data=data({"loops": [dict(message="loop", details=["d"] * 65536)]}), expected=False)
    native("SDC-nesting-bound", sdc="set_load " + "[list " * 18 + "1" + "]" * 18 + " 1\n" + BASE, expected=False)
    native("SDC-truncated", sdc=BASE.rstrip("\n"), expected=False)
    native("JSON-duplicate", native_data=('{"schema":1,"schema":1}', rich[1], rich[2]), expected=False)
    native("wrong-native-profile", native_data=(dict(rich[0], profile="different-native-profile"), rich[1], rich[2]), expected=False)

    # Independent genuine emitted-bundle envelope + manually constructed audit.
    bundle = root / "bundle"
    p = invoke([EMITTER, bundle]); assert p.returncode == 0, p.stderr
    manifest = json.loads((bundle / "manifest.json").read_text())
    audit_root = root / "audit"; audit_root.mkdir()
    producer = dict(tool="OpenSTA", version="2.7.0")
    context = dict(build_identity=manifest["identity"], audit_id="independent-review", stage="synthesized_netlist", producer=producer, corner="typical")
    counts = dict(stage_count=2, exception_count=1)
    def event(name, **fields):
        return dict(schema_version=1, event=name, **fields)
    stages = [event("stage", id=f"rx{i}", anchor=f"hc_pt7_rx{i}", cell=f"u{i}", master="sg13cmos5l_dfrbpq_1",
                    q_pin=f"u{i}/Q", data_pin=f"u{i}/D", async_pin=f"u{i}/RESET_B") for i in range(2)]
    relation = dict(id="capture", kind="async_capture", source="ena", targets=["u0/D"])
    wire = "".join("HC_ASIC_TIMING_V1\t" + json.dumps(e, separators=(",", ":")) + "\n" for e in
                   [event("begin", **counts), *stages, event("resolved", **relation), event("validated", **counts), event("applied", **relation), event("complete", **counts)])
    role_bytes = dict(manifest=(bundle / "manifest.json").read_text(), sdc=(bundle / "constraints/top.sdc").read_text(),
        native_producer=(bundle / "constraints/pt7-native-coverage.tcl").read_text(), netlist="// review synthetic mapped netlist\n",
        liberty='library (review) { time_unit : "1ns"; }\n', driver="# review synthetic native driver\n",
        resolution=wire, exceptions="retained native report\n", unconstrained="retained native checks\n",
        downstream_min="slack -0.044244 VIOLATED\n", downstream_max="slack 19.432287 MET\n",
        native_sdc="current_design " + manifest["top_module"] + "\n" + BASE,
        native_data=json.dumps(rich[0]), native_checks=rich[1], native_endpoints=rich[2], producer_version="2.7.0\n")
    input_roles = ["manifest", "sdc", "netlist", "liberty", "driver", "native_producer"]

    def seal(overrides=None, context_override=None, audit_schema=2, native_completion="HC_ASIC_NATIVE_V2_COMPLETE"):
        c = copy.deepcopy(context)
        if context_override:
            c.update(context_override)
        contents = dict(role_bytes, **(overrides or {}))
        if overrides and "manifest" in overrides and not (context_override and "build_identity" in context_override):
            c["build_identity"] = json.loads(contents["manifest"])["identity"]
        active_inputs = input_roles if audit_schema == 2 else [r for r in input_roles if r != "native_producer"]
        if audit_schema == 1:
            contents = {r: b for r, b in contents.items() if r not in
                        ("native_producer", "native_sdc", "native_data", "native_checks", "native_endpoints")}
        if context_override and "producer" in context_override:
            contents["producer_version"] = c["producer"]["version"] + "\n"
        def source(role):
            return dict(role=role, path=role + ".txt", sha256=hashlib.sha256(contents[role].encode()).hexdigest())
        receipt = dict(schema=1, **c, inputs=[source(role) for role in active_inputs])
        contents["receipt"] = json.dumps(receipt, separators=(",", ":"))
        contents["log"] = "PT7_AUDIT_RECEIPT " + contents["receipt"] + "\n" + wire + (
            native_completion + "\n" if audit_schema == 2 else "") + "PT7_AUDIT_COMPLETE\n"
        contents["process_result"] = json.dumps(dict(schema=1, receipt_sha256=hashlib.sha256(contents["receipt"].encode()).hexdigest(),
                                                   exit_code=0, argv=["sta", "-threads", "1", "driver.txt"]))
        for role, raw in contents.items():
            if (audit_root / (role + ".txt")).is_symlink():
                (audit_root / (role + ".txt")).unlink()
            (audit_root / (role + ".txt")).write_text(raw)
        audit = dict(schema=audit_schema, kind="hardcaml-asic.timing-intent-audit", scope="standalone_synthesized_netlist_sta", **c,
                     sources=[source(role) for role in active_inputs + [role for role in contents if role not in active_inputs]])
        write(audit_root / "audit.json", audit)

    seal(); record("schema2-independent-baseline", api("audit", audit_root), True)
    seal(dict(native_sdc="current_design wrong_top\n" + BASE)); record("candidate-native-current-design-mismatch", api("audit", audit_root), False,
                                                                   diagnostic="native current_design differs from manifest top")
    seal(dict(native_sdc="current_design " + manifest["top_module"] + "\n" + commented)); record("candidate-schema2-comment-hides-scope", api("audit", audit_root), False,
                                                                                            diagnostic="continued native SDC comment")
    seal(dict(native_data=json.dumps(shifted))); record("candidate-schema2-category-relabel", api("audit", audit_root), False,
                                                       diagnostic="native check report differs")
    for label, script in (("extra-scope", hidden_extra), ("masking", hidden_mask), ("design-selection", hidden_design)):
        seal(dict(native_sdc="current_design " + manifest["top_module"] + "\n" + script))
        record("candidate-schema2-bare-CR-" + label, api("audit", audit_root),
               False, diagnostic="LF-only", required_outcome="reject", open_issue="A-NATIVE-R1b")
    for label, native_sdc, error in (
        ("missing", BASE, "missing leading"),
        ("late", BASE + "current_design " + manifest["top_module"] + "\n", "missing leading"),
        ("same-repeated", role_bytes["native_sdc"] + "current_design " + manifest["top_module"] + "\n", "repeated/ambiguous"),
        ("prefix", "current_design " + manifest["top_module"] + "_other\n" + BASE, "differs from manifest top"),
        ("different-after-first", "current_design " + manifest["top_module"] + "\ncurrent_design other\n" + BASE, "repeated/ambiguous")):
        seal(dict(native_sdc=native_sdc))
        record("schema2-native-top-" + label, api("audit", audit_root), False, diagnostic=error)
    for spelling in ("{" + manifest["top_module"] + "}", '"' + manifest["top_module"] + '"'):
        seal(dict(native_sdc="# ordinary comment\ncurrent_design " + spelling + "\n" + BASE))
        accepted = record("schema2-native-top-exact-" + spelling, api("audit", audit_root), True)["value"]
        assert accepted["native_coverage"]["native_design"] == manifest["top_module"]
    seal(dict(driver="exec touch " + str(sentinel) + "\n"))
    record("schema2-retained-driver-is-not-executed", api("audit", audit_root), True)
    assert not sentinel.exists()  # This is source replay, not execution authentication.
    seal(context_override=dict(producer=dict(tool="OpenSTA", version="unqualified-99.0")))
    record("characterize-unqualified-version-with-matching-version-source", api("audit", audit_root))
    seal(context_override=dict(build_identity="0" * 64)); record("schema2-coherent-audit-receipt-wrong-build", api("audit", audit_root), False)
    seal(context_override=dict(stage="post_pnr")); record("schema2-coherent-wrong-stage", api("audit", audit_root), False)
    seal(context_override=dict(producer=dict(tool="unrelated-tool", version="2.7.0"))); record("schema2-unrelated-producer-tool", api("audit", audit_root), False)
    seal(dict(native_sdc=BASE + "set_false_path -from [get_ports {ena}] -to [get_pins {u1/D}]\n"))
    record("schema2-coherent-native-extra-scope", api("audit", audit_root), False)
    seal(); changed = json.loads((audit_root / "audit.json").read_text())
    changed["sources"] = [s for s in changed["sources"] if s["role"] != "native_endpoints"]
    write(audit_root / "audit.json", changed)
    record("schema2-missing-required-native-source", api("audit", audit_root), False)
    seal(); process = json.loads((audit_root / "process_result.txt").read_text()); process["exit_code"] = 9; write(audit_root / "process_result.txt", process)
    changed = json.loads((audit_root / "audit.json").read_text())
    for source in changed["sources"]:
        if source["role"] == "process_result": source["sha256"] = sha(audit_root / "process_result.txt")
    write(audit_root / "audit.json", changed)
    record("schema2-hook-complete-process-failed", api("audit", audit_root), False)
    seal(); changed = json.loads((audit_root / "audit.json").read_text()); changed["native_coverage"] = dict(status="matched"); write(audit_root / "audit.json", changed)
    record("schema2-forged-precomputed-summary", api("audit", audit_root), False)
    seal(dict(native_producer="not emitted producer\n")); record("schema2-producer-member-mismatch", api("audit", audit_root), False)
    seal(dict(native_data=json.dumps(dict(rich[0], profile="unrelated")))); record("schema2-data-profile-mismatch", api("audit", audit_root), False)
    seal(dict(sdc=role_bytes["sdc"] + "# changed entire SDC\n")); record("schema2-whole-SDC-member-mismatch", api("audit", audit_root), False)
    seal(); audit = json.loads((audit_root / "audit.json").read_text()); audit["corner"] = "other-corner"; write(audit_root / "audit.json", audit)
    record("schema2-audit-receipt-corner-mismatch", api("audit", audit_root), False)
    seal(); (audit_root / "native_checks.txt").unlink(); (audit_root / "native_checks.txt").symlink_to(audit_root / "unconstrained.txt")
    record("schema2-native-source-symlink", api("audit", audit_root), False)

    # v1 rejection must be for the version boundary, not a corrupt receipt/hash.
    legacy_data = dict(rich[0], schema=1, profile="opensta-2.7-native-state-v1")
    legacy_checks = "".join(g["message"] + "\n" + "".join("  " + d + "\n" for d in g["details"])
                            for c in rich[0]["categories"] for g in c["groups"]) + "HC_ASIC_NATIVE_CHECKS_COMPLETE\n"
    native("pure-explicit-native-v1-rejection", native_data=(legacy_data, legacy_checks, rich[2]), expected=False,
           diagnostic="unsupported legacy native profile 1; recapture")
    native("v2-data-does-not-qualify-v1-raw", native_data=(rich[0], legacy_checks, rich[2]), expected=False,
           diagnostic="native check report differs")
    def reseal_manifest(m):
        path = root / "manifest-to-seal.json"; write(path, m)
        return api("seal-manifest", path)
    legacy_producer = "# retained native-v1 fixture; never executed by inspection\n"
    old_m = copy.deepcopy(manifest)
    old_m["timing_native_coverage"].update(schema=1, profile="opensta-2.7-native-state-v1")
    for member in old_m["files"]:
        if member["path"] == "constraints/pt7-native-coverage.tcl":
            member["sha256"] = hashlib.sha256(legacy_producer.encode()).hexdigest()
    old_m = reseal_manifest(old_m)
    seal(dict(manifest=json.dumps(old_m, ensure_ascii=False), native_producer=legacy_producer,
              native_data=json.dumps(legacy_data), native_checks=legacy_checks), native_completion="HC_ASIC_NATIVE_V1_COMPLETE")
    record("schema2-explicit-v1-producer-rejection", api("audit", audit_root), False,
           diagnostic="unsupported legacy native producer profile; recapture")
    seal(dict(native_data=json.dumps(legacy_data), native_checks=legacy_checks))
    record("schema2-v1-data-under-v2-declaration-rejection", api("audit", audit_root), False,
           diagnostic="unsupported legacy native profile 1; recapture")
    seal(native_completion="HC_ASIC_NATIVE_V1_COMPLETE")
    record("schema2-v1-hook-marker-rejection", api("audit", audit_root), False,
           diagnostic="missing/reordered native producer completion")
    seal(dict(native_checks=legacy_checks))
    record("schema2-v2-labels-cannot-qualify-undelimited-raw", api("audit", audit_root), False,
           diagnostic="native check report differs")
    for label, chosen in (("v1-optional", old_m), ("v2-optional", manifest)):
        seal(dict(manifest=json.dumps(chosen, ensure_ascii=False)), audit_schema=1)
        accepted = record("schema1-backward-" + label, api("audit", audit_root), True)["value"]
        assert accepted["native_coverage"]["status"] == "unavailable"
        assert accepted["verdict"] == "unavailable" and accepted["unconstrained"]["status"] == "retained_not_interpreted"
    no_native = copy.deepcopy(manifest); no_native.pop("timing_native_coverage")
    no_native["files"] = [f for f in no_native["files"] if f["path"] != "constraints/pt7-native-coverage.tcl"]
    no_native = reseal_manifest(no_native)
    seal(dict(manifest=json.dumps(no_native, ensure_ascii=False)), audit_schema=1)
    record("schema1-backward-no-native-declaration", api("audit", audit_root), True)
    # Coherently rehash a changed SDC so rejection isolates original suffix replay.
    changed_sdc = role_bytes["sdc"] + "# after current serializer suffix\n"
    changed_manifest = copy.deepcopy(old_m)
    for member in changed_manifest["files"]:
        if member["path"] == "constraints/top.sdc": member["sha256"] = hashlib.sha256(changed_sdc.encode()).hexdigest()
    changed_manifest = reseal_manifest(changed_manifest)
    seal(dict(manifest=json.dumps(changed_manifest, ensure_ascii=False), sdc=changed_sdc), audit_schema=1)
    record("schema1-still-enforces-current-SDC-suffix", api("audit", audit_root), False,
           diagnostic="library serializer replay")

    # Actual producer source, tested against an independent seven-boolean model.
    p = invoke([CONSUMER, "producer"]); assert p.returncode == 0, p.stderr
    producer_tcl = root / "producer.tcl"; producer_tcl.write_text(p.stdout)
    model = root / "model.tcl"
    model.write_text(r'''set output [lindex $argv 1]
set mode [lindex $argv 2]
set vectors {}
namespace eval sta {}
proc sta::check_timing_cmd {args} {
    if {[llength $args] != 7 || [llength [lsearch -all -exact $args 1]] != 1} {error "bad native vector"}
    lappend ::vectors $args
    if {$::mode eq "native-api-failure"} {error "native diagnostics failed"}
    if {$::mode eq "empty-group"} {return [list {}]}
    if {$::mode eq "diagnostic-overflow"} {return [list [linsert [lrepeat 65536 d] 0 message]]}
    if {[lindex $args 0] && $::mode in {marker-message safe-marker-message}} {return [list [list $::env(REVIEW_MARKER) detail]]}
    if {[lindex $args 0] && $::mode eq "marker-detail"} {return [list [list message $::env(REVIEW_MARKER)]]}
    if {[lindex $args 0]} {return [list [list "Native input finding" "ena" "bus\[3\]"] [list "Native second group" "detail\ncontinued" "λ"]]}
    if {[lindex $args 3]} {return [list [list "Native clock finding" "u0/CLK"]]}
    return {}
}
proc sta::endpoints {} {
    if {$::mode eq "endpoint-overflow"} {return [lrepeat 65537 u0/D]}
    if {$::mode eq "string-overflow"} {return [list [string repeat a 65537]]}
    if {$::mode eq "unicode"} {return [list "u😀/D" u0/D]}
    return [list u1/D u0/D u0/D]
}
proc get_full_name {p} {return $p}
proc write_sdc {args} {
    if {[lrange $args 0 2] ne {-no_timestamp -digits 12}} {error "wrong native writer flags"}
    set f [open [lindex $args end] w]; puts $f {current_design top}; puts $f {set_false_path -from [get_ports {ena}] -to [get_pins {u0/D}]}; close $f
}
proc check_setup {args} {
    set expected [lindex {no_input_delay no_output_delay multiple_clock no_clock unconstrained_endpoints loops generated_clocks} [expr {[llength $::vectors]-1}]]
    if {[lrange $args 0 1] ne [list -verbose -$expected]} {error "wrong native printer category"}
    if {$::mode eq "printer-failure"} {error "native printer failed"}
    set f [open [lindex $args end] a]
    if {$expected eq "no_input_delay" && $::mode eq "safe-marker-message"} {puts $f $::env(REVIEW_MARKER);puts $f "  detail"
    } elseif {$expected eq "no_input_delay"} {puts $f "Native input finding\n  ena\n  bus\[3\]\nNative second group\n  detail\ncontinued\n  λ"}
    if {$expected eq "no_clock"} {puts $f "Native clock finding\n  u0/CLK"}
    close $f
}
source [lindex $argv 0]
if {[catch {::hc_pt7_native::write $output} message]} {puts stderr $message; exit 1}
if {[llength $vectors] != 7} {error "missing category calls"}
for {set i 0} {$i < 7} {incr i} {
    set expected [lreplace {0 0 0 0 0 0 0} $i $i 1]
    if {[lindex $vectors $i] ne $expected} {error "wrong category/vector association"}
}
''')
    for mode in ("normal", "printer-failure", "endpoint-overflow", "native-api-failure", "empty-group", "diagnostic-overflow", "string-overflow", "unicode"):
        output = root / ("producer-" + mode); output.mkdir()
        p = invoke(["tclsh", model, producer_tcl, output, mode])
        if mode not in ("normal", "unicode"):
            assert p.returncode == 1 and "HC_ASIC_NATIVE_V2_COMPLETE" not in p.stdout, (mode, p)
            results.append(dict(label="producer-" + mode, rejected=True, error=p.stderr.strip()))
        else:
            assert p.returncode == 0 and p.stdout == "HC_ASIC_NATIVE_V2_COMPLETE\n", (mode, p)
            generated = ((output / "native-data.json").read_text(), (output / "native-checks.rpt").read_text(), (output / "native-endpoints.rpt").read_text())
            first = json.loads(generated[0])["categories"][0]
            assert first["group_count"] == 2 and first["groups"][1]["details"] == ["detail\ncontinued", "λ"]
            if mode == "unicode":
                assert json.loads(generated[0])["endpoints"] == ["u0/D", "u😀/D"]
            native("producer-" + mode + "-roundtrip", sdc=(output / "native-state.sdc").read_text(), native_data=generated, expected=True)
    p = invoke(["tclsh", model, producer_tcl, root / "producer-normal", "normal"])
    assert p.returncode == 1 and "already exists" in p.stderr and "HC_ASIC_NATIVE_V2_COMPLETE" not in p.stdout
    results.append(dict(label="producer-existing-output", rejected=True, error=p.stderr.strip()))
    for field in ("message", "detail"):
        for marker in ("HC_ASIC_NATIVE_CATEGORY_BEGIN no_clock", "HC_ASIC_NATIVE_CATEGORY_END loops",
                       "HC_ASIC_NATIVE_CHECKS_V2 opensta-2.7-native-state-v2", "HC_ASIC_NATIVE_CHECKS_V2_COMPLETE",
                       "HC_ASIC_NATIVE_V2_COMPLETE", "HC_ASIC_NATIVE_V1_COMPLETE"):
            for prefix in ("", " \t\r", "ordinary\n \t\r"):
                output = root / ("producer-marker-" + str(len(results))); output.mkdir()
                p = invoke(["tclsh", model, producer_tcl, output, "marker-" + field], extra_env={"REVIEW_MARKER": prefix + marker})
                assert p.returncode == 1 and "reserved marker line" in p.stderr, (field, marker, prefix, p)
                assert "HC_ASIC_NATIVE_V2_COMPLETE" not in p.stdout and not (output / "native-data.json").exists()
                results.append(dict(label="producer-marker-" + field + "-" + str(len(results)), rejected=True, error=p.stderr.strip()))
    for safe in ("ordinary: HC_ASIC_NATIVE_CATEGORY_END loops", r"literal\nHC_ASIC_NATIVE_CATEGORY_BEGIN loops"):
        output = root / ("producer-safe-marker-" + str(len(results))); output.mkdir()
        p = invoke(["tclsh", model, producer_tcl, output, "safe-marker-message"], extra_env={"REVIEW_MARKER": safe})
        assert p.returncode == 0 and p.stdout == "HC_ASIC_NATIVE_V2_COMPLETE\n", p
        generated = ((output / "native-data.json").read_text(), (output / "native-checks.rpt").read_text(), (output / "native-endpoints.rpt").read_text())
        native("producer-safe-marker-roundtrip-" + safe, sdc=(output / "native-state.sdc").read_text(), native_data=generated, expected=True)

output = Path("/tmp/opencode/native-timing-review-results.json")
open_findings = [r["label"] for r in results if r.get("open_issue") and r["accepted"]]
payload = dict(scope="independent B review; no EDA", native_profile="opensta-2.7-native-state-v2", cases_directory=str(CASES),
               open_findings=open_findings, probes=results)
write(CASES / "results.json", payload)
write(output, payload)
print(f"Native timing independent review: {len(results)} probes recorded at {output}")
for row in results:
    if row["label"].startswith("candidate-"):
        print(json.dumps(row, sort_keys=True))
assert not open_findings, "OPEN A-NATIVE-R1b: bare-CR comment boundaries admit hidden scope/masking; see " + str(CASES / "results.json")
