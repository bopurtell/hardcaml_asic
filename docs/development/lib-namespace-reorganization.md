# Reorganize `lib/` into qualified module groups

Status: implemented 2026-10-15, uncommitted in the working tree at the time of
writing; the owner commits. Written 2026-10-14. Decisions revised and the tree
re-surveyed on 2026-10-15 at commit `936e82d`, after the named-flow feature
(`ecbcd57`) landed in `lib/`.

This is a record of the decisions and the change. "Today" in the mapping table
and the [2026-10-15 survey](#survey-2026-10-15) describe the tree before the
change. What was built and how it was verified is in
[Implementation and verification](#implementation-and-verification-2026-10-15).

## Motivation

The number of files in `lib/` is not the problem. `hardcaml/src` is one flat
directory of 73 modules, and Jane Street's convention is that a directory marks
a library boundary rather than a browsing aid. What makes `lib/` hard to read is
structure:

1. **Name collisions.** `lib/flow.ml` (in `hardcaml_asic`) sits next to the
   `lib/flow/` directory, which is a different library
   (`hardcaml_asic.flow_domain`). `lib/timing.ml` and
   `lib/flow/analysis/timing.ml` are unrelated `Timing` modules.
   `flow_stage.ml` mostly re-exports `Hardcaml_asic_flow_domain.Stage_id`.
2. **Nesting points the wrong way.** `lib/flow/` and `lib/execution/` look like
   subfolders of `hardcaml_asic`, but each is its own library. The dependency
   order is `flow_domain` → `hardcaml_asic` → `execution`, so the parent
   directory depends on one child and the other child depends on the parent.
3. **Three conventions in one tree.** The top level is flat, `execution/` uses
   `(include_subdirs unqualified)`, and `flow/` uses `(include_subdirs qualified)`.
4. **No table of contents.** There is no hand-written `hardcaml_asic.ml`, so
   nothing tells a reader how the 25 top-level modules group.

## What the package delivers

The package ships three libraries and an executable. The consumer's library and
the CLI are connected through files on disk rather than through linking:

```text
consumer design repo (e.g. hardcaml_asic_example) — its own executable
  -> links Hardcaml_asic (Project, Tt_cmos5l, Pinout, Bundle, Flow, ...)
  -> writes a bundle directory (RTL + configuration + manifest)
  -> hardcaml-asic-flow (bin/flow_cli; links only unix and yojson)
  -> LibreLane -> physical implementation tools
```

- `hardcaml_asic.flow_domain` is a Core-only description of stages, operations,
  the LibreLane mapping and the named-flow catalog (`Catalog`, `Methodology`,
  `Profile`, `Selection`, `Parameter`). It deliberately has no hardware,
  project or execution dependency: `test/catalog_domain/` and
  `test/stage_domain_consumer.ml` link only it to prove that a client such as
  Workbench can read stage definitions and a project's flow catalog without
  `hardcaml`.
- `hardcaml_asic` is the hardware library consumers link.
- `hardcaml_asic.execution` is the native execution engine behind the CLI and
  the `bin/experimental_*` executables.

One opam package carries all of it so a consumer pinning one revision gets a
library and CLI that agree (see the comment in `flow/dune`).

## Decisions

| Question | Decision |
|---|---|
| Qualified or unqualified subdirectories for `hardcaml_asic` (2026-10-14) | **Qualified.** Subdirectories become real namespaces (`Hardcaml_asic.Resource.Id`). |
| How consumers reach modules afterwards (2026-10-14) | **Short aliases plus qualified groups.** A hand-written `lib/hardcaml_asic.ml` exposes the groups and also re-exports the consumer entry points at the top level, so consumer code does not change. |
| `hardcaml_asic.flow_domain` (revised 2026-10-15) | **Keep it a separate library.** It moves out of `lib/` to a repo-root `flow_domain/`. Its library name, `public_name` and internal layout are unchanged. This supersedes the 2026-10-14 decision to merge it into `hardcaml_asic`, whose rationale ("keeping it separate spares no one a dependency") stopped holding once the catalog moved into it and gained Core-only consumers. |
| Where `execution` lives (2026-10-14) | **Repo-root `execution/`**, a sibling of `lib/` and `bin/`. Its `public_name` stays `hardcaml_asic.execution` and its internal layout is unchanged. |
| `flow_domain`'s `stages/<stage>/` (2026-10-14) | **Keep as is**, including the small per-step files. |
| Rename `Flow.Operation` (`Synthesis \| Hardening`) to `Goal` (2026-10-15) | **No.** The rename was only needed to avoid a clash with `flow_domain`'s `Operation` after a merge. Without the merge, the curated `Flow` does not re-export the domain `Operation`, so `Flow.Operation` keeps its name and the example's `asic_test/resolution_test.ml` needs no edit. |
| `lib/flow.ml` becomes `lib/flow/config.ml` | **Yes.** It holds the project's flow selection: `t`, `librelane`, `validate`, `Operation`, `Librelane`, `Adapter`. |
| Placement of the four modules added by the named-flow feature (2026-10-15, revised the same day) | **`Plan_json`, `Spec` and `Methodology_inventory` join `Flow`; `Methodology_plan` stays a root module.** All four were first assigned to `Flow`, but `Methodology_plan` depends on `Project` and `Bundle`, which would make `Flow` depend on the groups above it (see constraint 3). |
| Group layering (2026-10-15) | **Groups depend on each other in one direction only:** `Resource`, `Flow` → `Platform` → `Elaboration` → root modules. `Technology` joins `Resource`; `Resolved` and `Bundle` join `Elaboration`; `Project`, `Methodology_plan` and `Single_port_ram` stay at the `lib/` root. This replaces the 2026-10-14 grouping, which had group-level cycles. `Elaboration` could also hold `Project` and `Methodology_plan` without a cycle; they stay at the root by choice, as the layer that composes every group, so `Elaboration` does not become a catch-all. |
| Implementation without git mutations (2026-10-15) | **Use plain `mv`, not `git mv`.** The implementation session makes no commits, adds or other index changes; the owner commits. Git detects the renames at commit time as long as moved files are not also heavily edited in the same commit. |

## Constraints verified with dune 3.17

Both were tested in a scratch project, not in this repository.

1. **A library stanza is not allowed under a `(include_subdirs qualified)`
   directory.** dune reports: *"This stanza is not allowed in a sub-directory of
   directory with (include_subdirs qualified). Hint: add (include_subdirs no)"*.
   So `flow_domain` and `execution` cannot stay nested under a qualified `lib/`.
   The opt-out hint works, but it would keep the misleading nesting, and
   `lib/flow/` is also the natural directory for `hardcaml_asic`'s own `Flow`
   group.
2. **A file named after its directory becomes that group's interface module.**
   For example, `resource/resource.ml` becomes `Resource`, and it must re-export
   every member itself. The groups depend on each other (`Tt_cmos5l` uses
   `Flow`; `Bundle` uses `Target`), so such interface modules create dependency
   cycles. **Rule: no group directory contains a file with its own name.** That
   rules out `target/target.ml` and `flow/flow.ml`, which is why one group is
   called `platform/` and `flow.ml` becomes `flow/config.ml`.
3. **Dependencies are tracked per group, not per module (found 2026-10-15
   during implementation, reproduced with dune 3.24.2).** A reference such as
   `B.Z.t` from a module in group `A` makes it depend on every module in group
   `B`. So any two groups that reference each other produce a dune
   "Dependency cycle between: transitive deps of …" error, even when no
   module-level cycle exists. A three-file scratch project (`a/x.ml`,
   `a/y.ml` using `B.Z.t`, `b/z.ml` using `A.X.t`) reproduces it. The 2026-10-14
   version of this record assumed module-level resolution; that was wrong.
   **Rule: the groups must form a DAG.** Root modules of `lib/` are individual
   nodes and may depend on any group, but no group module may depend on a root
   module.

## Target layout

```text
flow_domain/                  library hardcaml_asic.flow_domain (moved from lib/flow/)
  stage_id, operation, registry, catalog, methodology, profile, selection,
  parameter, stages/, analysis/, backends/librelane/
lib/                          library hardcaml_asic, (include_subdirs qualified)
  dune
  hardcaml_asic.ml            curated public surface (see below)
  project.ml                  root modules: may use any group
  methodology_plan.ml
  single_port_ram.ml
  resource/                   id request policy technology selection record
  flow/                       config stage recipe plan_json spec methodology_inventory
                              (hardcaml_asic's Flow group, not the domain library)
  platform/                   harness target clock pinout timing tt_cmos5l
  elaboration/                mode context metadata elaborated_project resolved bundle
execution/                    library hardcaml_asic.execution (moved from lib/execution/)
bin/                          executables, unchanged
flow/                         Python CLI and runtime data, unchanged
```

The three libraries sit side by side in dependency order. Repo-root `flow/` is
already the Python CLI package, which is why the domain library is called
`flow_domain/` rather than reusing that name.

The groups come from the actual `ocamldep` graph of the top-level modules,
arranged so that group dependencies run one way:

```text
Resource ──┐
           ├─> Platform ─> Elaboration ─> root (Project, Methodology_plan, Single_port_ram)
Flow ──────┘
```

`Platform` uses `Resource` (`Target` → `Technology`) and `Flow` (`Tt_cmos5l` →
`Config`). `Elaboration` uses all three. `Resource` and `Flow` use neither
each other nor anything above them.

### Module mapping

| Today | New qualified path | Top-level alias in `hardcaml_asic.ml` |
|---|---|---|
| `Resource_id` | `Resource.Id` | — |
| `Resource_request` | `Resource.Request` | — |
| `Resource_policy` | `Resource.Policy` | `Resource_policy` (used by the example) |
| `Resource_record` | `Resource.Record` | — |
| `Selection` | `Resource.Selection` | — |
| `Technology` | `Resource.Technology` | `Technology` (used by the example; added in step 5) |
| `Single_port_ram` | root module, unchanged | `Single_port_ram` |
| `Target` | `Platform.Target` | `Target` |
| `Harness` | `Platform.Harness` | — |
| `Tt_cmos5l` | `Platform.Tt_cmos5l` | `Tt_cmos5l` |
| `Pinout` | `Platform.Pinout` | `Pinout` |
| `Clock` | `Platform.Clock` | `Clock` |
| `Timing` (`lib/timing.ml`) | `Platform.Timing` | `Timing` |
| `Project` | root module, unchanged | `Project` |
| `Elaboration_context` | `Elaboration.Context` | — |
| `Elaboration_mode` | `Elaboration.Mode` | — |
| `Elaborated_project` | `Elaboration.Elaborated_project` | `Elaborated_project` |
| `Metadata` | `Elaboration.Metadata` | — |
| `Flow` (`lib/flow.ml`) | `Flow.Config` | merged into curated `Flow`, see below |
| `Flow_stage` | `Flow.Stage` | via curated `Flow` |
| `Flow_recipe` | `Flow.Recipe` | via curated `Flow` |
| `Resolved_flow` | `Elaboration.Resolved` | `Resolved_flow` (used by the example) |
| `Bundle` | `Elaboration.Bundle` | `Bundle` |
| `Plan_json` | `Flow.Plan_json` | — |
| `Flow_spec` | `Flow.Spec` | `Flow_spec` (used by the example; added in step 5) |
| `Methodology_inventory` | `Flow.Methodology_inventory` | — |
| `Methodology_plan` | root module, unchanged | `Methodology_plan` (used by the example) |

Since the groups become namespaces, the `Resource_*`, `Elaboration_*` and
`Flow_*` prefixes would stutter, so they are dropped.

`Hardcaml_asic_flow_domain.*` names do not change. The curated `Flow` keeps
re-exporting `Catalog`, `Methodology`, `Profile`, `Selection` and `Parameter`
from it, as `lib/flow.ml` does today. It does not re-export `Stage_id`,
`Operation`, `Registry`, `Stages`, `Analysis` or `Backends`.

**Placement of the four named-flow modules (decided 2026-10-15).** `Plan_json` has no
intra-library dependencies and `Bundle` uses it. `Flow_spec` uses `Flow` and
`Plan_json`. `Methodology_inventory` uses `Plan_json`. Those three join `Flow`.
`Methodology_plan` uses `Project`, `Bundle`, `Elaborated_project`,
`Resolved_flow`, `Flow`, `Flow_spec`, `Methodology_inventory` and `Plan_json`;
in `Flow` it would make `Flow` depend on `Elaboration` and on `Project`, so it
stays a root module beside `Project`.

**Keeping the layering.** A new module joins the lowest group that all of its
dependencies allow. If a module in a lower group would need something from a
higher group, the module belongs higher (or at the root), not the dependency
lower. `Platform.Tt_cmos5l` depends on `Flow.Config` because the TT template
supplies default LibreLane overrides; it does not depend on `Target`.

### `lib/hardcaml_asic.ml`

Once this file exists, only what it re-exports is public. It must expose all
four groups, the aliases from the table, and everything `execution/`, `bin/`,
`test/` and `examples/` reference. Use `(** {1 ...} *)` sections, as
`hardcaml/src/hardcaml.ml` does. Its structure as implemented, without the doc
comments (the file itself is authoritative):

```ocaml
(** {1 Groups} *)

module Resource = Resource
module Platform = Platform
module Elaboration = Elaboration

(** Flow selection, stages, recipes and plan serialization. Includes
    [Flow.Config] so [Flow.librelane], [Flow.validate], [Flow.t] and
    [Flow.Operation] keep working, and re-exports the catalog modules from
    [Hardcaml_asic_flow_domain]. *)
module Flow = struct
  include Flow
  include Flow.Config
  module Catalog = Hardcaml_asic_flow_domain.Catalog
  module Methodology = Hardcaml_asic_flow_domain.Methodology
  module Profile = Hardcaml_asic_flow_domain.Profile
  module Selection = Hardcaml_asic_flow_domain.Selection
  module Parameter = Hardcaml_asic_flow_domain.Parameter
end

(** {1 Consumer entry points} *)

module Project = Project
module Methodology_plan = Methodology_plan
module Single_port_ram = Single_port_ram
module Elaborated_project = Elaboration.Elaborated_project
module Target = Platform.Target
module Tt_cmos5l = Platform.Tt_cmos5l
module Pinout = Platform.Pinout
module Clock = Platform.Clock
module Timing = Platform.Timing
module Resource_policy = Resource.Policy
module Technology = Resource.Technology
module Resolved_flow = Elaboration.Resolved
module Bundle = Elaboration.Bundle
module Flow_spec = Flow.Spec
```

Things to check while writing it:

- `include Flow` followed by `include Flow.Config` must not produce duplicate
  names. `Config` carries `t`, `librelane`, `validate`, `Operation`,
  `Librelane` and `Adapter`. The group's members are `Config`, `Stage`,
  `Recipe`, `Plan_json`, `Spec` and `Methodology_inventory`, so nothing
  clashes. The added catalog modules do not clash either: `Methodology` and
  `Methodology_inventory` are distinct names.
- Today `lib/flow.ml` both defines the configuration and re-exports `Stage`,
  `Recipe` and the catalog modules. After the move, `flow/config.ml` keeps only
  the configuration; the re-exports move into the curated `Flow` above.
- Inside the library, modules refer to groups through dune's generated alias
  module (`Flow.Config`, `Platform.Target`), not through the curated `Flow`.
  Internal callers of the old `Flow.t` and `Flow.Operation`
  (`elaborated_project`, `project`, `resolved_flow`, `tt_cmos5l`, `bundle`,
  `flow_spec`, `methodology_plan`) change to `Flow.Config.t` and
  `Flow.Config.Operation`. Root modules refer to each other by plain name.

## Consumer impact

Usage measured at `936e82d`:

- **`hardcaml_asic_example`** (separate repo, `~/devel/jane/hardcaml_asic_example`)
  uses `Flow.*` (58 references: `Catalog`, `Parameter`, `Selection`, `Stage`,
  `Profile`, `Methodology`, `Recipe`, `Operation`), `Tt_cmos5l`,
  `Methodology_plan` (in `bin/project.ml`), `Bundle`, `Timing`,
  `Resolved_flow` and `Pinout`. With the aliases above, **no edit is
  required**. Still rebuild it and follow the
  [consumer dependency workflow](consumer-dependency-workflow.md) to move its
  pin. This survey's grep missed the example's uses of `Technology` and
  `Flow_spec`; both became top-level aliases during step 5.

  **Correction (2026-10-15, after commit `ad290d8`):** the example's OCaml needs
  no edit, but its dependency script does. `scripts/asic_dependency.py` checks
  provider source and installed `.mli` files at fixed flat paths
  (`lib/elaborated_project.mli`, `lib/flow/catalog.mli`, …) and the spelling of
  types in `project.mli`, so a development refresh of `ad290d8` was refused
  before building. The script now selects a `flat` or `grouped` layout and
  refuses a mix; its setup tests cover both (22 tests). That change is in the
  example repo, not here. With it, a development refresh of `ad290d8` followed
  by the `standard`/`balanced` named flow through signoff and accepted
  finalization passed: six stages in 26 min 27 s, finalization 669.6 s,
  DRC/LVS/antenna, TT precheck and the 11,429-row gate replay all pass, and
  cells, area and slack match
  [the previous physical evidence](../../evidence/librelane-observation/native-final-integration.json)
  exactly. Results are under the example's
  `_flow_results/reorg-ad290d8-20261015/` (not curated evidence).
- **References inside this repo to modules that lose their top-level name.**
  These either get a qualified path or, if a name is wanted publicly, an alias:

  | File | Modules |
  |---|---|
  | `bin/experimental_stages.ml` | `Methodology_plan` |
  | `examples/memory_example.ml` | `Elaboration_mode`, `Resource_id`, `Selection`, `Technology` |
  | `examples/tt_bundle_example.ml` | `Technology` |
  | `lib/execution/backends/librelane/stage_preparation.ml(i)` | `Methodology_plan` |
  | `lib/execution/methodology_execution_record.ml(i)` | `Methodology_plan`, `Plan_json` |
  | `lib/execution/methodology_execution_service.ml(i)` | `Methodology_plan`, `Methodology_inventory` |
  | `lib/execution/results/finalization_service.ml` | `Methodology_plan` |
  | `lib/execution/results/historical_methodology.ml` | `Methodology_plan` |
  | `lib/execution/results/stage_execution_record.ml` | `Methodology_plan` |
  | `test/fixture.ml` | `Elaboration_context`, `Resource_id`, `Resource_record`, `Resource_request`, `Selection`, `Technology` |
  | `test/named_observation_race.ml` | `Methodology_plan` |
  | `test/plan_contract/consumer.ml` | `Flow_spec`, `Technology` |
  | `test/project_api_consumer.ml` | `Elaboration_context`, `Elaboration_mode`, `Technology` |
  | `test/stage_preparation_consumer.ml` | `Methodology_plan` |
  | `test/test_lifecycle.ml` | `Elaboration_context`, `Elaboration_mode`, `Resource_id`, `Resource_record` |
  | `test/test_resolved_flow.ml` | `Metadata`, `Technology` |
  | `test/test_selection.ml` | `Elaboration_mode`, `Resource_record`, `Selection`, `Technology` |
  | `test/test_single_port_ram.ml` | `Elaboration_mode`, `Resource_id`, `Resource_record`, `Resource_request`, `Selection`, `Technology` |
  | `test/test_target.ml` | `Technology` |

  `Methodology_plan` remains a top-level name, so the execution and `bin/`
  uses keep working. The rest
  are mostly tests of internal modules and should use qualified paths
  (`Hardcaml_asic.Resource.Record`), not new aliases.
  `test/catalog_domain/catalog_domain_consumer.ml` uses the domain library's
  own `Selection` and is unaffected.
- **`Hardcaml_asic_flow_domain` references are unaffected.** The library name
  and installed library list do not change, so `execution/`, `test/dune`,
  `test/catalog_domain/`, `test/stage_domain_consumer.ml` and
  `test/check_stage_domain.py` need no edits for the move.
- **Source path strings.** No code, dune file or test hard-codes `lib/flow` or
  `lib/execution`. Only `README.md` (lines 153–154, the layout table) names
  them.
- **Docs** that name the old modules or paths include `README.md`,
  `docs/architecture.md`, `docs/phase_plan.md`,
  `docs/guides/ocaml-stage-recipes.md`, the `docs/guides/` flow pages, and §5 of
  [flow-infrastructure-refactor](../flow-infrastructure-refactor.md). §5's
  status note places stage domains under `lib/flow/` and execution under
  `lib/execution/`; update it to match this record. Dated records under
  `docs/development/` stay as historical text.

## Order of work

Work on a branch from a clean `main`, one commit per step. Run
`dune build -j 20` and `dune runtest -j 20 --force` after each step.

1. **Move `lib/execution/` to `execution/`.** A pure move. The dune file
   and `public_name` stay unchanged.
2. **Move `lib/flow/` to `flow_domain/`.** A pure move. The dune file,
   library name and `public_name` stay unchanged. After this step `lib/` holds
   only `hardcaml_asic`.
3. **Create the groups.** Switch `lib/dune` to `(include_subdirs qualified)`.
   Create `resource/`, `platform/`, `elaboration/` and `flow/` with the renames
   from the mapping table: `flow.ml` to `flow/config.ml` (moving its
   `Stage`/`Recipe`/catalog re-exports out, see above), `flow_stage` to
   `flow/stage`, `flow_recipe` to `flow/recipe`, `flow_spec` to `flow/spec`,
   `plan_json` and `methodology_inventory` into `flow/`, `technology` into
   `resource/`, and `resolved_flow` to `elaboration/resolved`, `bundle` to
   `elaboration/bundle`. `project`, `methodology_plan` and `single_port_ram`
   stay at the root. Update internal references.
4. **Add `lib/hardcaml_asic.ml`** with the groups and aliases. Add a `.mli` if
   the surface should be documented separately.
5. **Update `execution/`, `test/`, `bin/` and `examples/`** using the table in
   [Consumer impact](#consumer-impact). Then rebuild `hardcaml_asic_example`
   against the new revision.
6. **Update the docs** listed above, and add this record to the
   `docs/README.md` index under completed work.

Steps 3 and 4 cannot build separately in practice: once `lib/` is qualified,
consumers see only the generated group aliases until `hardcaml_asic.ml` exists.
Either combine them in one commit, or add `hardcaml_asic.ml` in step 3 and
treat step 4 as its documentation pass.

Do not reformat files while moving them, and keep content edits to what the
move requires, so the owner can commit renames separately from content changes
and `git log --follow` keeps history. Per the decisions above, moves use plain
`mv`; the implementation session does not run `git mv`, `git add` or commits.

Converting `execution/` from `(include_subdirs unqualified)` to qualified is a
separate later change (decided 2026-10-15). It would rename the library's public
modules (`Hardcaml_asic_execution.*`), so it stays out of this reorganization.

Missing `.mli` files are a separate follow-up, not part of this change. These
modules have none: `flow.ml`, `selection`, `resource_record`, `pinout`,
`clock`, `timing`, `metadata`, `harness`, `elaboration_mode`, and in
`flow_domain`, `analysis/timing` and `stages/*`.

## Survey (2026-10-15)

Taken at `936e82d` on `main`. `lib/` was unchanged by the docs-only commits
after `ecbcd57`.

**Baseline.** `dune build -j 20` and `dune runtest -j 20 --force` both exited 0
(the test run took about 9 minutes). Uncommitted documentation edits were
present in the working tree; no source or dune file was modified.

**Size.** `lib/` without `execution/` is 8,467 lines; `lib/execution/` is
7,220 lines. There are 85 `.ml` files in all.

**Libraries and `include_subdirs` modes.** Unchanged from 2026-10-14:
`lib/dune` (flat, depends on `hardcaml_asic.flow_domain`), `lib/flow/dune`
(qualified, `core` only), `lib/execution/dune` (unqualified, C stubs).

**`flow_domain` contents.** `stage_id`, `operation`, `registry`, `catalog`,
`methodology`, `profile`, `selection`, `parameter`, `analysis/timing`,
`backends/librelane/mapping`, and `stages/{synthesis,floorplan,placement,cts,routing,signoff}/recipe`
plus placement's `global_placement`, `design_repair` and `detailed_placement`.
The catalog, methodology, profile, selection and parameter modules were added by
`ecbcd57`.

**Top-level dependency graph** (intra-library only; `[fd]` marks a direct
`Hardcaml_asic_flow_domain` reference):

```text
bundle:                Elaborated_project Pinout Plan_json Resolved_flow Resource_id
                       Resource_policy Resource_record Resource_request Selection
                       Target Technology
clock:
elaborated_project:    Clock Elaboration_mode Flow Metadata Pinout Resource_id
                       Resource_record Target Timing
elaboration_context:   Elaboration_mode Resource_id Resource_policy Resource_record
                       Resource_request Selection Technology
elaboration_mode:
flow:                  Flow_recipe Flow_stage  [fd]
flow_recipe:           Flow_stage
flow_spec:             Flow Plan_json
flow_stage:            [fd]
harness:
metadata:
methodology_inventory: Plan_json
methodology_plan:      Bundle Elaborated_project Flow Flow_spec Methodology_inventory
                       Plan_json Project Resolved_flow
pinout:
plan_json:
project:               Bundle Clock Elaboration_context Flow Metadata Pinout
                       Resolved_flow Resource_policy Target Timing
resolved_flow:         Clock Elaborated_project Elaboration_mode Flow Resource_record
                       Selection Target Technology
resource_id:
resource_policy:       Resource_id
resource_record:       Resource_id Resource_policy Resource_request Selection Technology
resource_request:      Resource_id
selection:             Resource_policy Resource_request Technology
single_port_ram:       Elaboration_context Resource_id Resource_request
target:                Harness Technology
technology:            Resource_request
timing:
tt_cmos5l:             Flow
```

**Files referencing the `flow_domain` library.** Source: `lib/dune`,
`lib/flow/dune`, `lib/flow.ml`, `lib/flow_stage.ml(i)`,
`lib/execution/execution_plan.ml` and `classic_contract.ml`,
`segment_service.ml`, `stage_preparation.ml` under
`lib/execution/backends/librelane/`. Tests: `test/dune`,
`test/stage_domain_consumer.ml`, `test/check_stage_domain.py`,
`test/catalog_domain/{dune,catalog_domain_consumer.ml}`. Docs: `README.md`,
`docs/architecture.md`, `docs/phase_plan.md`,
`docs/guides/ocaml-stage-recipes.md` and dated development records. None of
these change under the revised plan except the docs that name source paths.

## Implementation and verification (2026-10-15)

Steps 1–5 were done together in the working tree on `main`, with plain `mv`
and no git index changes, rather than as one commit per step on a branch.

**Built.** `lib/execution/` moved to repo-root `execution/` and `lib/flow/` to
`flow_domain/`, both with unchanged dune files, library names and module names;
`execution/` still uses `(include_subdirs unqualified)`. `lib/` is
`(include_subdirs qualified)` with the `resource/`, `flow/`, `platform/` and
`elaboration/` groups and root modules from the [target layout](#target-layout),
and `lib/hardcaml_asic.ml` is the curated surface shown above. `Flow.Operation`
kept its name. Code in `execution/`, `bin/`, `test/` and `examples/` uses
qualified paths for internal modules (`Resource.Record`, `Elaboration.Mode`,
`Flow.Plan_json`).

**Verified.** `dune build` of the whole repo and `dune runtest -j 20 --force`
pass. The separate `hardcaml_asic_example` builds and passes its tests against
this working tree with no source edits.

**Non-OCaml fixes the survey did not predict.** Bundle source identity and two
Python checks assumed a flat `lib/`:

- `examples/tt_bundle_example.ml` `source_inputs` now walks `lib/` recursively,
  so bundle source identity still covers the whole library;
  `test/check_project_api.py` takes that function from the example's source
  text, so it picks up the change without an edit;
- `test/check_bundle.py` now targets `lib/elaboration/elaborated_project.ml`;
- comment-only path updates in `toolchain.lock`, `scripts/flow.sh` and
  `flow/hardcaml_asic_flow/data/toolchain.sh`.

**Fixed 2026-10-15.** The manual `test/check_methodology_legacy.py` (not part
of `runtest`) broke: it overwrote `lib/{project,resolved_flow}.ml(i)` with frozen
`0d04bae` versions in a copy of the current `lib/`, which no longer contains
`flow_domain/` and is no longer flat. It now takes `lib/` and `dune-project` via
`git archive` from `--environment-ref` (default `0ed1b3d`, the last
pre-reorganization commit), applies the same splice there, and builds the
*current* `examples/tt_bundle_example.ml`, so both emitters share the recursive
`source_inputs`. To compile against both layouts the example now names the
top-level alias `Technology` instead of `Resource.Technology`. The manual `test/check_project_api.py` was not run.

**2026-11-20 follow-up.** The historical and current emitters now span two
intentional manifest changes: the support-tools revision (`60f03f2`) and the
resolved resource snapshot (`e46bfd7`), both covered by the bundle identity.
`test/check_methodology_legacy.py` bridges those formats with the pre-starter
provider (`6195834`). It checks every non-manifest byte and the remaining
manifest fields across eras, then compares the complete current bundles,
including the manifest bytes, to that independently built provider.

Comments and doc strings in moved files may still name modules by their old
names in prose (for example `Resolved_flow`, `Resource_id`,
`Elaboration_context`); only references that the compiler checks were updated.
