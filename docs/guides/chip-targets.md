# Several chip targets in one Dune project

Place `hardcaml-asic-targets.json` next to the project's `dune-project`:

```json
{
  "schema_version": 1,
  "default_target": "fifo",
  "targets": [
    {"id": "fifo", "executable": "asics/fifo/cli/project.exe"},
    {"id": "ram", "executable": "asics/ram/cli/project.exe"}
  ]
}
```

`hardcaml-asic-targets list` prints this validated JSON in declaration order;
`select --target ram` prints just the selected `{"id":…,"executable":…}`.
`exec --target ram -- flow list --json` invokes that target's own `flow list --json`
command (when provided). Use `--catalog PATH` after any subcommand to address
a catalog outside the current directory. `select` and `exec` without `--target`
use `default_target`. An explicit unknown ID is an error, never a fallback.

`exec` runs `dune exec --root ROOT ./EXECUTABLE -- ARGS…` from the catalog
directory and preserves argv and exit status. Dune builds the executable as
needed. `list` and `select` neither build nor check for executable files. All
three commands reject duplicate IDs, malformed declarations, and unsupported
schema versions, with a diagnostic on stderr and status 2. Consumers should
check the returned catalog version before interpreting entries.

IDs match `[a-z][a-z0-9_-]*`, are unique within the project, and should stay
stable across renames of files. A catalog needs at least one entry and a
registered default. Executables are project-relative Dune `.exe` targets;
use source-tree paths without `_build`, `.`, or `..` components. The catalog
contains no pinout, clock, IP, methodology, or profile declarations.

### Generated targets and Silver

`hardcaml-asic-starter render --name first` emits one Dune root and the
`asic_first/` target. To add a second register example to the same root, use
`render-target --name second`; to wrap an existing sample IP, also pass
`--library ip_decoder --mapping ui_in:uo_out:comb8`. Both commands return JSON
descriptors; the caller previews and writes the files. Register the executable
as `asic_second/project.exe` without changing `default_target`. From the project root:

```sh
dune build asic_first/project.exe asic_second/project.exe
dune runtest
dune exec ./asic_first/project.exe -- chip list --json
dune exec ./asic_first/project.exe -- flow list --json --chip second --variant default
```

The generated CLI translates the root catalog to Silver's chip/variant list;
the generated targets each expose one `default` variant. Explicit selection
forwards the flow request to the built executable for that target. Registration
is optional: an unregistered target still supports direct `dune exec`.

### Migration

Use `Hardcaml_asic.Chip_target.create` for a chip declaration. `Project` remains
a type-compatible module alias. Existing single-target consumers need neither
a manifest nor a declaration change. `render-existing` is an alias of
`render-target`. Older fresh starters kept `chip.ml` and `rtl.exe` at the root;
new ones place them beside the target CLI under `asic_NAME/`. Existing projects
can keep their layout and register the actual path to their CLI. A hand-written
Silver CLI owns its own `chip list` and selection protocol; a registration
entry does not rewrite that code.
