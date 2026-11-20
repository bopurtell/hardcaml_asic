# TT 6x4 / IHP CMOS5L target reference

The first target resolver accepts Tiny Tapeout `6x4` with `ihp-sg13cmos5l`.
`Target.resolve` returns die bounds in micrometres, the floorplan DEF reference,
power pins, the allowed project routing range, the nominal timing corner, and
standard-cell view references. It rejects unsupported combinations and incomplete
input records before any flow input is emitted. It reads no installed PDK and
performs no setup.

The default `Target.Inputs.reference_cmos5l_6x4` adopts the revisions and
floorplan hash originally adopted from the consumer's toolchain lock. The current
local sources are [`lib/platform/target.ml`](../../lib/platform/target.ml) and
[`toolchain.lock`](../../toolchain.lock); the absent scaf checkout is not required:

| Input | Adopted value |
| --- | --- |
| Support tools | `d66cf179e7bc4d296362ab7e2e3b344dc3c4f665` |
| IHP Open PDK | `2bbec755dc67ca3db0261c3d6163e15735d66710` |
| Floorplan | `tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def` |
| DEF SHA-256 | `b46d9a0ee8352160e48dbc8312f092f985629061df736c7f46d58686535a76f4` |
| Die bounds | `(0, 0)` to `(1289.28, 710.64)` µm |

The support-tools pin moved on 2026-10-23 from the consumer-adopted
`da63c9927411e3aca350977d653d24bbf5bca972` to `d66cf179…`, the observed
`ihp-sg13cmos5l` head that day. The three intervening commits add the SRAM
marker layers to the precheck layer list
([`cfa06bae`](https://github.com/TinyTapeout/tt-support-tools/commit/cfa06bae536d00282cb2fdb3d0bdeebc562c6365)),
change TT's own CI, and add the 8x4 tile DEF and `tile_sizes.yaml` entry. The
6x4 DEF hash above is unchanged across them, as are `project.py`, `tech.py`,
`precheck/default.nix`, `precheck/tool-versions.json` and both requirements files,
so flop-only 6x4 bundles are unaffected. Consumers that copied the old revision
still name `da63c992…` until they move themselves. The 8x4 tile is present at
this revision but is not a resolvable target.

The dimensions agree with the pinned support tools'
[`tile_sizes.yaml`](https://github.com/TinyTapeout/tt-support-tools/blob/d66cf179e7bc4d296362ab7e2e3b344dc3c4f665/tech/ihp-sg13cmos5l/tile_sizes.yaml).
The [pinned `project.py`](https://github.com/TinyTapeout/tt-support-tools/blob/d66cf179e7bc4d296362ab7e2e3b344dc3c4f665/project.py)
derives the DEF path, `VPWR`/`VGND`, and the project top routing layer. The
[pinned `tech.py`](https://github.com/TinyTapeout/tt-support-tools/blob/d66cf179e7bc4d296362ab7e2e3b344dc3c4f665/tech.py)
sets that layer to `Metal4` and the nominal corner to
`nom_typ_1p20V_25C`. The [PDK LibreLane configuration](https://github.com/IHP-GmbH/IHP-Open-PDK/blob/2bbec755dc67ca3db0261c3d6163e15735d66710/ihp-sg13cmos5l/libs.tech/librelane/config.tcl)
identifies the standard-cell LEF, GDS, and Verilog paths, the `VDD`/`VSS`
cell pins, and routing from `Metal2` through `Metal4` for this TT project.
The resolver records the nominal standard-cell Liberty reference under that
same PDK revision.

`Target.Inputs.constraints` adopts the timing and drive environment from the
same pinned PDK revision, read off its own LibreLane standard-cell
configuration,
[`libs.tech/librelane/sg13cmos5l_stdcell/config.tcl`](https://github.com/IHP-GmbH/IHP-Open-PDK/blob/2bbec755dc67ca3db0261c3d6163e15735d66710/ihp-sg13cmos5l/libs.tech/librelane/config.tcl):

| Constraint | Adopted value | LibreLane variable |
| --- | --- | --- |
| Driving cell | `sg13cmos5l_buf_4` pin `X` | `SYNTH_DRIVING_CELL` |
| Output load | 6 fF | `OUTPUT_CAP_LOAD` |
| Maximum fanout | 10 cells | `MAX_FANOUT_CONSTRAINT` |
| Clock uncertainty | 0.25 ns | `CLOCK_UNCERTAINTY_CONSTRAINT` |
| Clock transition | 0.15 ns | `CLOCK_TRANSITION_CONSTRAINT` |
| Timing derate | 5% | `TIME_DERATING_CONSTRAINT` |

These are the pinned PDK's values, not values characterised for this process;
upstream's own comment on the last four is "a bit random ... from sky130".
`Target.resolve` checks that the driving cell and pin are nonblank and contain
no `/`, that the load, uncertainty and transition are finite and nonnegative,
that the fanout is positive, and that the derate is below 100% so the early
multiplier stays positive. It does **not** check that the driving cell exists in
the standard-cell Liberty: this module opens no file, so a cell that does not
exist resolves here and is first caught when OpenSTA reads the SDC, or by the
P4 environment preflight. A project that needs different values passes its own
`Target.Inputs.constraints`; that is the only supported route, because every
LibreLane variable above is protected against raw overrides.

The `6x4` pair is the only resolved combination so far. Other tile constructors
remain available for declarations used by P0/P1 examples, but attempting to
resolve them fails until their floorplans and revisions are adopted. Physical
file presence and revision/hash checks belong to the explicit P4 preflight before
physical execution; no PDK is present in ordinary library tests. These
standard-cell views do not establish an SRAM macro.

## Validated TT/LibreLane flow

`Chip_target.elaborate_for_flow` elaborates an implementation and calls
`Resolved_flow.resolve`. Its private result retains the abstract
`Elaborated_project.t` in `elaborated_project`, alongside the resolved target,
facts validated against the checked interface,
clock and I/O delays in nanoseconds, clock frequency in hertz, and LibreLane
settings with an owner for each value. `Chip_target.elaborate` still supports
resource-only examples and behavioral simulation without requiring a supported
physical target. Elaboration produces a circuit with metadata, intent and resources,
without synthesis. `Resolved_flow.Setting.t` and `Resolved_flow.Delay_ns.t` are
private too: callers can read and pattern-match but cannot construct or update
these records. `Target.Resolved` is unchanged.
[P3 emission](../guides/bundle-emission.md) consumes the validated result.

The TT top has exactly these ports:

| Inputs | Outputs |
| --- | --- |
| `ui_in[7:0]`, `uio_in[7:0]`, `ena`, `clk`, `rst_n` | `uo_out[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]` |

Resolution checks names, widths, and directions, including unused input ports
that Hardcaml records as phantom inputs. The declaration supplies a nonblank
title, author, and description plus one description for each of the 24 `ui`,
`uo`, and `uio` bits. The harness defines the reset, enable, and bidirectional
pins, but the consumer design owns reset/disable behavior and `uio_out`/`uio_oe`
arbitration; these checks only validate the boundary.

The initial timing subset is one primary clock on input `clk`, with optional
input and output delays on named top-level ports. Each delay declares both a
`minimum` (hold) and a `maximum` (setup); both are required because an SDC
delay with only `-max` leaves hold unconstrained, so STA reports no hold paths
and the resizer inserts no hold buffers. `Clock.period` and the delay bounds use
`Time_float.Span.t`, allowing the 48 MHz reference period of about 20.833333
ns. Resolution rejects nonfinite or nonpositive clock periods, negative or
nonfinite maximums, nonfinite minimums or minimums above their maximum
(negative minimums are allowed), duplicate delays, wrong-direction endpoints,
delays on `clk`, and additional clocks. It sorts delay endpoints for
stable emission. P3 renders the resolved primary clock to LibreLane
`CLOCK_PORT`/`CLOCK_PERIOD` and TT `clock_hz`, and the validated delay
subset to SDC. PT.7 additionally supports exact scalar-stage asynchronous-capture
and enumerated active-low/reset-to-zero exceptions, registered Q anchors and
Yosys `keep=1` preservation intent. The [timing-intent guide](../guides/timing-intent.md)
defines the supported mapped-cell/pin subset, fail-closed resolution and standalone
native acceptance limits. General false paths, generated clocks and arbitrary SDC
remain unsupported.
The pinned `src/config.json` declares only `CLOCK_PORT` and `CLOCK_PERIOD` as
timing inputs; it has no reviewed board-specific I/O delay values. Those
delays therefore remain optional declarations rather than fabricated defaults.

Everything else the SDC applies is environment rather than project intent, so
it comes from the resolved target's constraint environment above and not from a
declaration. Setting `PNR_SDC_FILE` and `SIGNOFF_SDC_FILE` means LibreLane's
`FALLBACK_SDC` (`librelane/scripts/base.sdc`) never runs, and that script is
the only place LibreLane applies clock uncertainty and transition, timing
derate, the driving cell, the output load and the maximum fanout. The emitted
SDC therefore applies all six itself; without them a path from an input pad
through a buffer to a flip-flop is analysed with no input slew, no on-chip
variation margin and no uncertainty, hold appears met, and the post-CTS resizer
finds nothing to repair. The emitted SDC deliberately writes no
`set_propagated_clock`: LibreLane's `read_current_sdc` greps the file for it and
applies ideal clocks before CTS and propagated clocks afterwards when it finds
neither, which is the behaviour each step wants. It also writes no
`set_max_transition` or `set_max_capacitance`, because the pinned PDK leaves
`MAX_TRANSITION_CONSTRAINT` and `MAX_CAPACITANCE_CONSTRAINT` unset and the
Liberty's own limits then apply.

| Configuration fact | Owner | Override rule |
| --- | --- | --- |
| Top name and generated RTL list | Elaborated design and P3 source set | Protected |
| Clock port and period | Typed clock | Protected |
| Die area, floorplan, PDK, cell library, routing maximum, power pins | Resolved target | Protected |
| Driving cell, output load, maximum fanout, clock uncertainty and transition, timing derate | Resolved target constraint environment | Protected; change it through `Target.Inputs.constraints` |
| `MAX_TRANSITION_CONSTRAINT`, `MAX_CAPACITANCE_CONSTRAINT`, `IO_DELAY_CONSTRAINT`, `SYNTH_CLK_DRIVING_CELL` | Emitted SDC | Protected; the SDC is the only authority, and a raw override would apply to synthesis only |
| `MACROS` | Registered resources | Raw override rejected; no CMOS5L macro is supported |
| `FP_SIZING`, `RUN_LINTER` | Initial adapter defaults | Reasoned override allowed |
| Other LibreLane settings such as density, margins, PDN pitch, and checks | Reasoned override | Recorded with key, strict JSON value, and reason |
| The pinned TT CMOS5L template's twenty settings | `Tt_cmos5l.template_overrides` | Library-owned defaults; replace or drop one with `Tt_cmos5l.overrides ~extra ~without` |

The existing consumer's `src/config.json` settings fit this boundary: clock
values come from the typed clock, `FP_SIZING` and `RUN_LINTER` have defaults,
and its other keys can be supplied as reasoned overrides. P3 populates the
generated RTL list from emitted files. `Resolved_flow` rejects protected keys
even when the proposed value matches, so a raw setting cannot become a second
authority. Its adapter capability check accepts implementation elaborations for
LibreLane synthesis and hardening. Synthesis requires the PDK configuration,
standard-cell Liberty, and cell Verilog roles; hardening additionally needs the
technology LEF, cell LEF, and cell GDS roles. Every registered flop resource
must carry generated synthesis RTL. A macro selection is currently rejected by
this adapter, independently of technology selection policy.
