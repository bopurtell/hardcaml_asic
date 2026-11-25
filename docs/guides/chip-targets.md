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

## Render a standalone project

With the provider installed and its compatible opam switch selected, render a
new project in an empty directory. The renderer writes deterministic JSON file
descriptors, not files. The following writes the descriptors exclusively in a
new directory, then builds and tests the chip and commits its source for planning.

```sh
PROJECT=$(mktemp -d)
cd "$PROJECT"
hardcaml-asic-starter render --name first > starter.json
python3 - <<'PYTHON'
import json
from pathlib import Path
for item in json.loads(Path("starter.json").read_text())["files"]:
    path = Path(item["path"])
    assert not path.is_absolute() and ".." not in path.parts
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(item["contents"])
PYTHON
git init
git add dune-project asic_first
git commit -m "Initial ASIC target"
dune build asic_first/project.exe
dune runtest asic_first
dune exec ./asic_first/rtl.exe > chip.v
```

Configure Git author identity if needed before committing. See the generated
`asic_first/README.md` for the headless named-flow synthesis recipe. The root
catalog is optional for a single target.

For IPC clients, `render`, `render-target`, `render-ip` and `render-module`
return schema 1 with provider name/version and a `files` array of relative
`path` and UTF-8 `contents`. Chip names match `[a-z][a-z0-9_-]{0,63}`;
IP/module names match `[a-z][a-z0-9_]{0,63}`. Invalid arguments exit 2.

To create IP descriptors, run `hardcaml-asic-starter render-ip --name decoder --template sample`.
The sample contains `ip/decoder/dune`, a private `ip_decoder` library,
`block.ml`, a Cyclesim test and `chip-interface.json`. Write descriptors to the
project root as above, then run `dune build ip/decoder/ip_decoder.cmxa` and
`dune runtest ip/decoder`. `--template empty` supplies a stub without an
interface declaration. `render-module --name priority` emits `priority.ml`
relative to the chosen library directory. If a custom Dune stanza enumerates
modules explicitly, include new modules there.

For the sample mapping below the library implements an eight-bit combinational
input/output interface with `Ip_decoder.Block.create`. The caller validates the
interface declaration before claiming support; the renderer does not inspect
the filesystem. Add all implementation source dependencies to the target's
`~source_inputs` before planning or emission.

### Generated targets and client integration

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
