# Bounded resource inspection for clients

The public `resource-inspect` command projects ASIC-owned inventory and realization
into bounded index/detail/source pages. It supplements the raw
[`resources-inspect` evidence API](resource-realization.md), which remains compatible.
Clients do not need to parse tool reports or legacy resource S-expressions.

```sh
hardcaml-asic-flow resource-inspect "$TARGET" --kind coarse_run --json
hardcaml-asic-flow resource-inspect "$BUNDLE" --kind bundle --json
```

Kinds: `bundle`, `coarse_run`, `execution`, `finalization`. They are explicit:
do not infer a coarse run from the parent directory of a named execution file.
Bundle inspection verifies the manifest and all advertised member bytes. Named
execution/finalization association uses existing checked records, preserving operation
and run identities. Unsupported realization remains unavailable, not inferred from
checkpoint counts. No query builds, emits, finalizes, probes EDA or changes evidence.

Public library:

```ocaml
Hardcaml_asic_execution.Resource_inspection_service.inspect
  (Coarse_run path) ()
(* (Evidence_json.t, string) result; Bundle/Execution/Finalization also supported *)
```

Envelope: `hardcaml-asic.resource-inspection`, schema 1, separately advertised by
`resource_inspection1`. This is not implied by CLI4 or `resource_evidence2`.

The index includes `inventory_status` (`available`, `unavailable`, `unknown`) and
`inventory_reason`, concise resource rows/section counts, measurements, original
provenance, `source_digest`, verdict, diagnostics and truncation. Available zero rows
means a verified empty inventory; unavailable zero rows is not a resource-free design.
Measurements carry provider-owned `observed`, `derived`, `estimate` or `unknown`
classification as well as units, stage, definition, scope and unavailable reason.

Exit 0 means readable `pass`/`not_evaluated` evidence; exit 1 means readable `fail`
evidence; exit 2 means invalid source/query. Clients must retain exit-1 JSON while
rejecting inconsistent exit/verdict pairs. This does not make failed retention a pass.

## Detail and source pages

Use the exact index digest for subsequent reads:

```sh
hardcaml-asic-flow resource-inspect "$TARGET" --kind coarse_run \
  --query detail --resource-id program --section timing \
  --expected-digest "$DIGEST" --offset 0 --limit 8 --json

hardcaml-asic-flow resource-inspect "$TARGET" --kind coarse_run \
  --query artifacts --expected-digest "$DIGEST" --offset 0 --limit 16 --json

hardcaml-asic-flow resource-inspect "$TARGET" --kind coarse_run \
  --query bytes --artifact-id "$OPAQUE_ID" --expected-digest "$DIGEST" \
  --offset 0 --limit 4096 --json
```

Sections: inventory, synthesis, final, geometry, timing, electrical, provenance.
Provider-owned detail fields retain scope/coverage, line spans and source references.
Original structured/legacy inventory remains inspectable without a second selector.
Source IDs come only from the authorized catalog; arbitrary file paths are not
accepted. Bytes are hex encoded with original-byte offsets and SHA-256 identity.

Limits: index ≤2 MiB/4096 rows, detail/catalog ≤64 KiB, requested item limit 1–64,
source byte limit 1–16384. A byte budget can shorten an item page; always follow
`next_offset`/`total`. Oversized indivisible items fail explicitly. Digest fencing
revalidates the exact source on page requests. The digest identifies source evidence;
cache consumers must also bind provider identity and normalized projection contents.

The actual augmented SRAM capture produces one `program` row, 76 authorized sources,
186 sampled timing paths and 372 electrical observations. Original physical-p4 lacks
raw STA reports and stays timing-unavailable. Electrical compliance is not assessed;
geometry, sampled coverage and area-scope qualifications remain those of PT.5b.

Workbench consumes this interface through its [Resources inspector](../../../silver/docs/resource_inspector.md).
Real native acceptance and exact provider identities are recorded in the
[integration evidence](../../../silver/docs/resource_inspector_acceptance.md).
