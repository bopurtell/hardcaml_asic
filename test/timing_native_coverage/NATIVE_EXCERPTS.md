# Durable native-writer grammar regressions

These excerpts were copied byte-for-byte (including backslash-newline placement)
from C's retained standalone OpenSTA 2.7.0 reports on 2026-10-28. They are parser
regressions, not new native execution or a complete audit. Tests use the local
copies and do not depend on the example checkout or its provider/PDK.

The additional `native-continued-comment-excerpt.sdc` is B's independent
A-NATIVE-R1 **Tcl-language counterexample**, not a native-writer capture. It keeps
the exact two-line continued-comment spelling from
the historical native timing review. `comment_check.py` sources it under
ordinary `tclsh` with inert object/false-path procedures, observing zero exception
calls, versus one for the ordinary-comment control. The production parser never
sources it: pure and hash-consistent full-audit tests require an unsupported-
continued-comment rejection. The native reader's version-2 fixtures add the
manifest top context and category-bound reports; this does not relabel C's older
version-1 native producer captures as version-2 acceptance.

Source root: `hardcaml_asic_example/acceptance/pt7c/`.
Image: `sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a`.
See example `docs/pt7c_native_findings.md` for actual attempt/process provenance.

| Local excerpt | Native source | Inclusive source lines, in order |
| --- | --- | --- |
| `native-baseline-excerpt.sdc` | `producer-probe-01-baseline/native-state.sdc` | 4, 8–11, 15–16, 79–80, 95–104, 115, 140, 151–152, 156 |
| `native-setup-only-excerpt.sdc` | `native-diagnostics-01/setup-only.sdc` | 95–97 |
| `native-masking-excerpt.sdc` | `native-diagnostics-01/masking.sdc` | 95 |

SHA-256 of the complete original source reports:

```text
1626f634fa0efdd0a8f90b46eab41e57982eda2db16a36fa0f2f1d6b57474f7b  producer-probe-01-baseline/native-state.sdc
68457edc7b5c4bb60004b59de8f0be34cb291576a5ce543d07aaf8d6e1996ed6  native-diagnostics-01/setup-only.sdc
4cf98b144ae8d7460bbe91cf419adc1c6dc2427cc9a358d79f136c47c304492a  native-diagnostics-01/masking.sdc
```

The baseline resolves to six exact relations: `ena -> _4_/D` and
`rst_n -> {_2_,_3_,_4_,_5_,_6_}/RESET_B`. Test request order deliberately differs
from native serialization order. The setup-only and masking excerpts must reject
for their semantic restrictions, not tokenizer or ordinary-environment errors.
Additional mutations keep this positive baseline and exercise continuation,
derate scope/value, current-design arity/substitution, broad/extra exceptions and
continued masking. Bare newlines/semicolons inside command substitutions reject:
only the observed single-query grammar, with explicit continuations, is supported.

## A-NATIVE-R1b source-channel newline controls

`native-newline-cases.json` contains 17 durable language cases with literal newline
bytes represented by JSON escapes. `newline_check.py` decodes each state and
writes its `.sdc` bytes in **binary mode**, then sources it under ordinary `tclsh`
with inert procedures. The Tcl oracle checks false-path and disable call counts
and the exact sequence of current-design selections. These are language tests,
not native-writer captures or EDA.

The bare-CR extra-relation / masking / wrong-top variants reproduce B's R1b
counterexamples. Additional cases cover CRLF, mixed LF/CRLF, CR-only files,
indented/semicolon comments, CR outside comments and in braced values, continued
comments, and native command continuations. Tcl accepts/normalizes the relevant
source-channel newline forms; the qualified native reader now rejects **every CR
byte** before tokenization. LF-only ordinary comments and native continuations
remain positive controls; LF-exposed extra scope, masking and design changes
reject for their intended semantic diagnostics.

`check.ml` uses the same decoded states through both the pure mandatory-top API
and complete hash-consistent schema2 audits with matching manifest top, profile2,
V2 diagnostics/completion and valid resolution/application/process receipts.
Rejection cannot be attributed to an unrelated missing native context. Existing
LF native-v2 production sources remain reusable; no producer/profile change or
fresh EDA is required by this reader-only grammar restriction.
