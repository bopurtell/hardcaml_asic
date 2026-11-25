"""Differential identity verification against actual provider Python helpers.

Run under @test/execution-identity, or with --installed for an independently
compiled consumer of a disposable installed prefix. All Dune calls are sequential.
"""
import argparse
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import struct
import sys
import tempfile


def verify(emitter, provider):
    sys.path.insert(0, str(provider))
    from hardcaml_asic_flow import checkpoint, flow_observation, result_store

    inputs = []
    expected = []

    def positive(text, unsigned_field=None, value=None):
        if value is None:
            value = json.loads(text)
        if unsigned_field is not None:
            del value[unsigned_field]
        canonical = checkpoint.canonical(value)
        assert canonical == flow_observation.canonical(value)
        digest = checkpoint.identity(value)
        assert digest == flow_observation.digest(value)
        assert digest == hashlib.sha256(canonical.encode()).hexdigest()
        inputs.append(text if unsigned_field is None else 'UNSIGNED:' + unsigned_field + '\t' + text)
        expected.append(canonical + "\t" + digest)

    # Literal golden vectors pin Python defaults, including DEL and surrogates
    # for supplementary scalars (not UTF-16 key sorting).
    golden = '{"a":-0.0,"z":1.0,"é":"😀\\u007f"}'
    assert checkpoint.canonical(json.loads(golden)) == (
        '{"a":-0.0,"z":1.0,"\\u00e9":"\\ud83d\\ude00\\u007f"}')
    assert checkpoint.identity({}) == '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
    for text in [golden, '{}', 'null', 'true', 'false', '-0', '0',
                 '12345678901234567890123456789012345678901234567890',
                 '-99999999999999999999999999999999999999999999999999',
                 '[1,1.0,-0,-0.0,0.125,1e0,125e-3,1e-999,-1e-999,-0e0]',
                 '[1.00000000000000001,0.49999999999999999,5.62949953421312E+14]',
                 '{"😀":1,"\\ue000":2,"é":3,"é":4,"\\u0061":5}',
                 '"\\b\\f\\n\\r\\t\\u0000\\/\\\\\\\""',
                 '{"escaped":"\\ud83d\\ude00","raw":"😀"}',
                 '[0.0000152587890625,0.000030517578125,0.0000457763671875,562949953421312.0]']:
        positive(text)

    rng = random.Random(7919)
    for _ in range(600):
        # Exactly representable fractions with independently Python-produced
        # float text. Integers exceed machine/IEEE exact integer precision.
        fraction = rng.randint(-1000000, 1000000) / 256
        value = {"z": [fraction, rng.randrange(-10**100, 10**100)],
                 "é/😀": {"bool": bool(rng.randrange(2)), "null": None},
                 "a": "".join(chr(rng.choice([0, 8, 31, 34, 47, 92, 127, 233, 0xe000, 0x1f600])) for _ in range(8))}
        positive(json.dumps(value, ensure_ascii=False, indent=2).replace('\n', ' '))

    # Exact fractions and earlier domain boundaries remain positive regressions.
    for numerator in [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 32768, 65536]:
        for sign in [-1, 1]:
            positive(json.dumps(sign * numerator / 65536))
    for x in [99999999999999.5, 99999999999999 / 2, -0.0]:
        positive(json.dumps(x))

    # Mixed magnitudes/denominators: every finite value is now admitted.
    for _ in range(5000):
        x = rng.randint(-10**8, 10**8) / 65536 * 2 ** rng.randrange(0, 35)
        positive(json.dumps(x))

    def bits_case(bits):
        # This path exercises the float smart constructor directly, independently
        # of either language's decimal parser or formatter.
        x = struct.unpack('>d', struct.pack('>Q', bits))[0]
        if math.isfinite(x):
            positive(f'BITS:{bits:016x}', value=x)
        else:
            inputs.append(f'BITS:{bits:016x}')
            expected.append('ERR:nonfinite number')

    for _ in range(30000):
        bits_case(rng.getrandbits(64))
    # Every binary exponent, both signs, near binade boundaries and midpoint
    # inclusion parity. Include all-ones significands and extremal subnormals.
    for exponent in range(2047):
        for fraction in [0, 1, 2, (1 << 51) - 1, (1 << 52) - 2, (1 << 52) - 1]:
            for sign in [0, 1 << 63]:
                bits_case(sign | (exponent << 52) | fraction)
    for bits in [0x7ff0000000000000, 0xfff0000000000000,
                 0x7ff8000000000000, 0x7ff0000000000001]:
        bits_case(bits)
    # Decimal exponent boundaries, including the -4/16 notation transitions,
    # and their immediate binary64 neighbours.
    for exponent in range(-323, 309):
        x = float(f'1e{exponent}')
        for y in [math.nextafter(x, 0.0), x, math.nextafter(x, math.inf)]:
            if math.isfinite(y):
                positive(json.dumps(y))
    # Exact decimal midpoint inputs test parser ties-to-even independently of
    # shortest-output tests. Decimal construction is exact, not context rounded.
    for bits in [1, 2, 0x000fffffffffffff, 0x0010000000000000,
                 0x3fb9999999999999, 0x3ff0000000000000,
                 0x3ff0000000000001, 0x4340000000000000,
                 0x7feffffffffffffe]:
        a = struct.unpack('>d', struct.pack('>Q', bits))[0]
        b = math.nextafter(a, math.inf)
        with localcontext() as context:
            context.prec = 1600
            midpoint = (Decimal.from_float(a) + Decimal.from_float(b)) / 2
            for sign in [1, -1]:
                for delta in [Decimal(0), Decimal('1e-1100'), -Decimal('1e-1100')]:
                    positive(str(sign * (midpoint + delta)))
    for text in ['0.1', '1e-5', '1e16', '1.2345678901234567', '0.9999847412109375',
                 '562949953421313.0', '99999999999999.25', '5e-324',
                 '1e23', '2.2250738585072014e-308', '1.7976931348623157e308',
                 '2.4703282292062327e-324', '2.4703282292062328e-324',
                 '-2.4703282292062327e-324', '9007199254740993.0']:
        positive(text)
    # Shortest-output decimal ties (both candidates round to the same binary64)
    # must select the even final digit, rather than always rounding upward.
    for integer in [10**14, 10**15, 2**50]:
        for fraction in [0.25, 0.75]:
            for sign in [-1, 1]:
                positive(json.dumps(sign * (integer + fraction)))

    # Real identity envelope shapes and the actual result-store pretty writer.
    unsigned = {"schema_version": 2, "location": "/tmp/é/checkpoint", "boundary": "placement",
                "restart_supported": True, "restart_targets": ["cts"],
                "tested_image_id": "sha256:" + "a" * 64, "closure_scope": "path_bound",
                "compatibility": {"bridge": {"sha256": "b" * 64, "bytes": 123}},
                "files": {"state.json": {"sha256": "c" * 64, "bytes": 456}},
                 "external": {}, "metrics": {"area": {"availability": "available", "value": 12345.6789},
                                              "timing": {"availability": "available", "value": -0.037}},
                "acceptance": "not_evaluated"}
    manifest = dict(unsigned, identity=checkpoint.identity(unsigned))
    previous = None
    records = []
    # Actual captured producer numbers from
    # the archived LibreLane journal-event timing captures.
    # Keep these literal regressions available in the installed-only test too.
    elapsed = [0.00030089501524344087, 0.002011571021284908,
               0.655296418000944, 0.6555905290006194, 5.464505295007257,
               5.464894789009122, 5.4653915369999595,
               0.00031195400515571237, 0.002112260990543291,
               0.6821005099918693, 0.6825274029979482, 5.443038233002881,
               5.443440698989434, 5.443737483990844, 9.81468330300413,
               9.815066743001807, 9.815546596015338]
    for i, seconds in enumerate(elapsed, 1):
        record = {"contract": flow_observation.CONTRACT, "schema_version": 2,
                  "stream_id": "d" * 32, "sequence": i, "previous_sha256": previous,
                  "recorded_at": "2026-10-13T00:00:00.000000Z", "elapsed_seconds": seconds,
                  "event": "finished", "data": {"duration_seconds": [4.809296, 4.760916, 4.371336][i % 3]}}
        previous = flow_observation.digest(record)
        records.append(dict(record, sha256=previous))
        positive(json.dumps(record))
    positive(json.dumps(unsigned))
    # Existing report/finalization fixture numbers, retaining float/count types.
    positive(json.dumps({"metrics": {"mapped_area": {"value": 123.5},
                                     "mapped_cells": {"value": 16294},
                                     "setup_slack": {"value": 0.3},
                                     "hold_slack": {"value": 0.12},
                                     "timing__hold__ws": {"value": 0.1}}}))
    with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
        path = Path(tmp) / 'record.json'
        for record in [manifest, *records]:
            result_store.save_json(path, record)
            assert checkpoint.read(path) == flow_observation.strict_read(path) == record
            positive(path.read_text().replace('\n', ' '))
            positive(path.read_text().replace('\n', ' '),
                     'identity' if record is manifest else 'sha256')

    def negative(text, reason):
        inputs.append(text)
        expected.append('ERR:' + reason)

    strict_bad = ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}',
                  '{"x":[{"a":1,"a":2}]}', 'NaN', 'Infinity', '-Infinity',
                  '1e999', '-1e999', '01', '-01', '+1', '.5', '1.', '1e',
                  '[1,]', '{"a":1,}', '{1:2}', "{'a':1}", '[true false]',
                  'null false', '/*comment*/null', '"raw\tcontrol"', '"\\x41"',
                  '"\\u12"', '"unterminated', '{', '[', '']
    with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
        path = Path(tmp) / 'invalid.json'
        for text in strict_bad:
            path.write_text(text)
            for reader in [checkpoint.read, flow_observation.strict_read]:
                try:
                    reader(path)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'Python accepted {text!r}')
            negative(text, '')  # Error category varies; all must reject.
    for text in ['"\\ud800"', '"\\udc00"', '"\\ud800x"']:
        negative(text, 'invalid or unsupported Unicode')
    negative('[' * 258 + '0' + ']' * 258, '')

    result = subprocess.run([str(emitter)], input='\n'.join(inputs) + '\n',
                            text=True, capture_output=True, check=True)
    actual = result.stdout.splitlines()
    assert len(actual) == len(expected), result.stderr
    for text, got, want in zip(inputs, actual, expected):
        assert got == want if want != 'ERR:' else got.startswith('ERR:'), (text, got, want)
    # Different numeric types and signed float zero are identity-significant.
    assert checkpoint.identity(1) != checkpoint.identity(1.0)
    assert checkpoint.identity(0.0) != checkpoint.identity(-0.0)
    # Raw byte hashing must not reinterpret newline/JSON escaping.
    print(f'identity parity: {sum(not x.startswith("ERR:") for x in expected)} accepted; '
          f'{sum(x.startswith("ERR:") for x in expected)} rejected')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--emitter', type=Path)
    parser.add_argument('--installed', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if not args.installed:
        verify(args.emitter.resolve(), repo / 'flow')
        return
    with tempfile.TemporaryDirectory(prefix='execution-installed-', dir='/tmp/opencode') as tmp:
        root = Path(tmp)
        prefix = root / 'prefix'
        from reference_harness import installed_prefix
        installed_prefix(repo, prefix)
        consumer = root / 'consumer'
        consumer.mkdir()
        shutil.copyfile(repo / 'test/execution_identity_driver.ml', consumer / 'execution_identity_driver.ml')
        (consumer / 'dune-project').write_text('(lang dune 3.17)\n(name installed_execution_test)\n')
        (consumer / 'dune').write_text('(executable (name execution_identity_driver) (libraries hardcaml_asic.execution))\n')
        env = dict(os.environ)
        env['OCAMLPATH'] = str(prefix / 'lib') + (':' + env['OCAMLPATH'] if env.get('OCAMLPATH') else '')
        subprocess.run(['dune', 'build', '-j', '20', 'execution_identity_driver.exe'], cwd=consumer, env=env, check=True)
        # Execution may depend on hardware declarations, never the reverse.
        meta = (prefix / 'lib/hardcaml_asic/META').read_text()
        execution = meta.split('package "execution" (', 1)[1].split('\n)', 1)[0]
        assert 'requires = "cryptokit hardcaml_asic spawn str unix yojson zarith"' in execution, execution
        # Deny any Python reads from the checkout while exercising installed
        # provider helpers and the freshly linked installed-only consumer.
        auditing = True
        def audit(event, values):
            if auditing and event == 'open' and isinstance(values[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(values[0])).resolve()
                if path.is_relative_to(repo):
                    raise AssertionError(f'checkout read in installed test: {path}')
        sys.addaudithook(audit)
        try:
            verify(consumer / '_build/default/execution_identity_driver.exe',
                   prefix / 'lib/hardcaml_asic/flow')
        finally:
            auditing = False
        print('independent installed execution consumer passed (cryptokit/hardware/zarith dependencies)')


if __name__ == '__main__':
    main()
