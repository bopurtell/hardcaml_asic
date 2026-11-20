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

`Hardcaml_asic.Chip_target.create` and `Hardcaml_asic.Project.create` are the
same API and use the same types. Registration is optional: removing an entry
does not affect its direct `dune exec`. Existing single-target consumers need
neither a manifest nor a declaration change.
