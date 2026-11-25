# Native historical methodology presentation v1

`hardcaml-asic-flow report EXECUTION.json --json` adds `methodology` **only for
named stage records**, inside the existing `hardcaml-asic.report` schema-1
envelope. Legacy stage/checkpoint and coarse report JSON and human bytes retain
their contracts. `Stage_report_service.methodology report` explicitly returns
`state: "not_recorded"` when that optional field is absent.

`hardcaml-asic-flow observation inspect ATTEMPT --json` adds `methodology` and
`execution_request` and `execution_file` **only for named allocations containing
`methodology_launch`**, for both journal schemas 1 and 2. Legacy inspect JSON keeps
its exact prior shape and legacy human output has no methodology section. Clients
interpret absent methodology as `not_recorded`; absence does not supply evidence.
These are delivery metadata, outside `state`, hashed event records and their
identities. `read-updates`, pure `snapshot` and watch event/status delivery retain
their prior formats. `observation describe --json` intentionally adds the exact
`named_execution_delivery` capability metadata and three named-only inspect schema
entries; its existing base contract remains unchanged.

## Normalized payload

All variants have `contract: "hardcaml-asic.historical-methodology"` and integer
`schema_version: 1`:

| `state` | Additional fields |
| --- | --- |
| `not_recorded` | None; an old record provides no methodology evidence. |
| `pending` | `launch`: immutable allocation's `methodology_launch`; `validation: "not_published"`. No identities or resolved defaults are inferred. |
| `recorded` | Fields below, obtained exclusively from the retained checked snapshot. |

Recorded fields:

- `flow`: complete selected methodology definition (`id`, `label`, description,
  backend recipe, fresh input, endpoints, configurable keys and defaults).
- `profile`: complete selected profile (`id`, `label`, description, assignments).
- `endpoint`: requested endpoint under that fixed flow.
- `methodology_hash`, `plan_identity`, `bundle_identity`: main-library identities.
  The plan identity is the exact preview/expected identity retained in the run
  receipt, never an attempt ID or source stamp.
- `effective_parameters`: typed `winning: {assignment: {key, value}, owner}` and
  `overridden` contributions in oldest-first order. Owners retain methodology,
  profile, request or project identity; project owners retain their original reason.
- `frozen_methodology`: plan's complete methodology object (`snapshot`,
  `project_assignments`, `effective_parameters`, `translation`, `recipe`). The
  snapshot retains the full declaration and selection, not just display labels.
- `reference`: exact `hardcaml-asic.methodology-execution-reference` v1.
- `validation: "host_only_snapshot_and_recorded_checkpoints"`.
- `unspecified: "runtime-backend-default-not-observed"`.

`execution_request` is the native preflight request when retained, including the
reference, ordinary plan, bundle/workspace/checkpoint roots, tool roots and limits.
For pending named launches it is the original allocation request. Legacy responses
do not add an `execution_request` alias.
New named preflight reports retain this request before probes; failed reports
retain it through `previous_report`, including when no execution/workspace exists.

The **reference-bearing preflight report is the metadata commit marker**. The
snapshot sidecar is published first; while the report still has no reference,
inspection returns `pending` and the original allocation request even if the
sidecar already exists. Inspection rechecks the writer, journal and report across
this transition. Once a reference is committed, all snapshot/hash/identity checks
apply; missing or corrupt committed evidence is a query error. Failure handling
retains a published reference and native request even when preparation stops in
this interval. An abruptly lost writer or recorded interruption with only pending
evidence preserves that outcome (or unknown outcome) and pending metadata, without
inferring completion from the sidecar.

## Validation and OCaml seams

### Named execution-file delivery (additive CLI4 / namedflow1)

Successful or failed named `stages --json` execution summaries add
`execution_file: "/absolute/workspace/execution-<attempt>.json"` **to delivery
only**, after validating the actually persisted Runner record. The stored strict
Runner schema does not contain this field. No-methodology terminal JSON/human
bytes retain their previous contract.

Named `observation inspect ATTEMPT --json` adds the same `execution_file` field;
allocation alone suffices to recover it after losing terminal stdout. It is null
before a readable record exists, including an empty reserved file and failed
preflight. Snapshot `state: recorded` does not imply an execution record exists.
A partially failed/interrupted execution with a valid persisted record can be
reported normally (report query exit 0, execution still failed/interrupted).
There is no `report_target` alias. Legacy inspection has no `execution_file` field.

The provider shares `Stage_execution_record.path` with the authoritative writer;
inspection derives that exact path from the allocated workspace and stream ID.
It never scans directories or chooses a newest record. `available` requires a
regular readable file, the strict Stage_execution_record verifier, exact attempt
and methodology reference equality, checked frozen recipe/plan/bundle identities,
snapshot checksum and recorded checkpoint/resolved-value validation. Malformed or
tampered present evidence is a query error; completed named attempts missing their
record are errors too. Empty reservations are not published references. This adds
no journal event/schema field and needs neither tools nor current catalogs.

The standalone consumer uses only returned references, compares normalized report
plan/bundle and attempt identities with preview/allocation, and reconnects using
the saved public attempt/stream/cursor. No Workbench dependency is involved.

The report uses `Stage_execution_record.read`; observation inspection reuses
`Methodology_execution_record.of_json` (or `of_reference_json` before a plan event)
and `Stage_execution_record.check_methodology_checkpoints`. The latter is the same
validator used by strict execution decoding: publication identity/boundary, bundle
association, checkpoint file contents and actual backend `resolved.json` typed
values. No independent verifier, current catalog lookup, source PLANFILE read,
active selection resolution, Git, backend process or runtime probe is involved.
Missing/tampered required sidecars and mismatched references fail the query with
exit **2** (`INVALID_RECORD` for observation). They never become `not_recorded`.

Public `.mli` APIs are `Historical_methodology.{recorded,pending,not_recorded,human}`,
`Stage_report_service.methodology`, `Observation.inspect`, and the shared strict
reader APIs above. `Historical_methodology.recorded` formats already checked
evidence; callers reading raw records should use the strict readers first.

Default human output presents ID/label, profile, endpoint, preview identity and an
effective-setting table before checkpoints/stages. Report and observation inspect
`--verbose` add complete provenance, frozen definitions and snapshot reference.
No output claims all backend defaults or runtime readiness have been resolved.
