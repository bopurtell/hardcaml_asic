# Program memory contract (`Single_port_ram`)

Behavioral contract for the implemented primitive.
Normative for `lib/single_port_ram.mli`. This document decides behaviour; the
interface and implementation must follow it. The context-taking API is implemented.

The [architecture](../architecture.md) defines project ownership, resource selection and registration. This document defines the memory behavior and verification obligations.

---

## 1. Why this memory, and only this memory

The emulator studies three storage users. The dimensions below are illustrative
sweep points, not a frozen ISA or guaranteed macro shape:

| Storage | Size | Decision |
| --- | --- | --- |
| Program memory, 256x16 example | 4,096 bits | **This primitive.** About 94% of program-plus-FIFO bits, before registers/metadata |
| Two byte FIFOs, 16x8 | 256 bits | Flip-flops. Not a macro, not this primitive |
| Register file, 8x16 | 128 bits | Flip-flops. Multi-port read; wrong shape for 1RW |

Original program-memory requirement — *"256x16 program bits plus two 16x8 FIFOs already
total 4,352 storage bits before registers and metadata. That can dominate a small
design if implemented in flip-flops."*

At these study sizes, program memory is the only initial macro candidate. Routing the
FIFOs or the register file through this interface would buy interface complexity
and no area, and is explicitly out of scope for v0.1.

---

## 2. Port shape: single-port 1RW, shared address

**Decision: one address bus, one enable, one write-enable. A cycle is a read or a
write, never both.**

Forced by Original program-memory requirement — *"Permit host program writes only while
halted with engines idle."*

Host program load and instruction fetch are **temporally exclusive by
construction**. They cannot occur in the same cycle, because the core is halted
during load. There is therefore no requirement for concurrent read and write, and
no reason to pay for a second port.

This is the most consequential decision in the document. 1RW is the smallest,
cheapest and most widely available compiled-macro configuration. A 1R1W or true
dual-port memory is substantially larger and much less likely to exist in an open
PDK. Host writes during execution would require an arbitration/stall policy or
another memory shape; the initial emulator requires neither.

**Consequence for the architecture:** exclusive program-memory access is now a
hardware contract, not only a safety convention. The emulator initially restricts
both host loading and readback to halted execution with engines idle; requests
during execution are rejected without taking a fetch cycle. Supporting live host
access would require an explicit arbitration/stall timing contract or a different
memory shape, not only a firmware change. Ordinary status/data-queue operations
do not imply access to this port.

---

## 3. No independent read during a write

The concept note's sketch carries a `read_during_write` field with `Read_first`,
`Write_first` and `Undefined`. **This primitive does not have that field.**

With a shared address and a single write-enable, a simultaneous read and write of
the same address cannot be expressed: when `write_enable` is high the cycle is a
write, when it is low the cycle is a read. There is no second port to read from.

This does not mean a physical 1RW memory cannot expose old data, new data, or a
held value on its output during a write. Those are possible implementation
behaviors, but this contract deliberately promises none of them. A policy enum
would expand the portable promise beyond the emulator's requirement. A future
resource can specify such behavior if a real consumer needs it.

The real question that *does* need answering is what the output does during a
write cycle. That is section 4.

---

## 4. Cycle-by-cycle contract

`read_latency = 1`. Inputs are sampled on the rising edge of cycle *n*; the value
returned in cycle *n+1* reflects the operation presented in cycle *n*.

| `enable` | `write_enable` | Operation | Read data in the following cycle |
| --- | --- | --- | --- |
| 0 | x | none | **holds** its previous value |
| 1 | 0 | read `address` | `mem[address]` |
| 1 | 1 | write `write_data` to `address` | **unspecified** — poison in simulation |

```text
cycle               1      2      3      4      5      6
--------------------------------------------------------
enable              1      1      0      1      1      0
write_enable        1      0      0      0      1      0
address             A0     A1     --     A2     A3     --
write_data          D0     --     --     --     D3     --
--------------------------------------------------------
operation          WR     RD     --     RD     WR     --
read_data (out)     ?   POISON  M[A1]  M[A1]  M[A2] POISON
                           ^      ^      ^             ^
                           |      |      |             result of
                           |      |      held          cycle 5's
                           |      |      (enable=0)    write
                           |      read of A1 lands
                           write cycle: unspecified
```

`POISON` denotes the behavioral model's diagnostic value, not a required physical
output. The disabled operation in cycle 6 holds that value into cycle 7 (not
shown). `M[A1]` and `M[A2]` are defined only if those locations were previously
written; the trace alone does not initialize them.

### 4.1 Hold on disable — why this is pinned rather than left open

Real SRAM macros typically hold the previous output when chip-enable is
deasserted. A naive behavioural model emits zero, or re-reads, or emits X. The
three disagree, and the disagreement only shows up after tapeout.

This matters because the emulator *will* stall fetch:
Original program-memory requirement — the Time family includes *"Wait cycles, wait
level/edge with timeout"*, and *"Wait stalls control, not engines."* During a
stall the fetch stage can deassert `enable` and rely on a stable memory output.
The consumer must still gate instruction acceptance with fetch validity: held
data alone does not identify a new or valid instruction.

**Hold is therefore required of every backend, including the flop fallback and the
behavioural model.** A macro that cannot hold is not a valid backend for this
primitive.

### 4.2 Read latency 1 — the pipeline consequence

Original program-memory requirement — *"First use a synchronous-read memory abstraction
with an explicit latency."*

Latency 1 requires the consumer to account for the edge between a read request
and its result, and to track fetch validity separately from held read data. The
Original program-memory requirement
requires the Flow family to *"publish cycle counts for every path"*: branch and
jump latency become a function of fetch
latency, so those published numbers cannot be finalised before this value is.

Latency is a `Config` parameter rather than a constant so the call site states it
explicitly, and so a future registered-output macro at latency 2 has somewhere to
live. **v0.1 accepts only 1 and raises on anything else** rather than silently
rounding.

### 4.3 No write mask, no byte enables

Writes replace a complete configured memory word. The emulator still compares
16-bit instructions with extensions against fixed 32-bit instructions; the RAM
contract does not settle the ISA or equate instruction width with memory width.
The consumer defines packing, extension layout, byte order, and number of fetches
for each candidate. A byte-oriented transport assembles a full memory word before
writing and rejects incomplete words without declaring the image valid.

Byte enables are a portability liability: many compiled macros do not offer them,
and emulating them costs a read-modify-write that this single-port shape cannot
perform in one cycle. Not offered, and should not be added speculatively.

---

## 5. Initialisation: none, and the model must be hostile about it

**Decision: contents are undefined at power-up. There is no reset port, no
initialisation file, and no zero-fill.**

The emulator states this three separate times:

- Original program-memory requirement — *"Do not depend on uninitialized program/data memory."*
- Original program-memory requirement — *"Avoid bulk reset on program RAM. Reset its validity instead."*
- Original program-memory requirement — *"Any ROM is a deliberate hardware implementation, not an assumed power-up file load."*

Validity is the **consumer's** concern: a separate `program_valid` flop, outside
this primitive, gates execution until a load completes. This primitive has no
opinion about it and no port for it.

### 5.1 Poison rather than X

Hardcaml's `Cyclesim` is two-state — `Bits.t` holds 0 and 1, not X — so "drives X"
is not implementable in that model. Use a **poison pattern** as a diagnostic
substitute: a deterministic value chosen to stand out in waveforms. It is still
an ordinary bit pattern, not an unknown value or an automatic assertion failure.

The behavioural model must therefore:

1. Fill all of memory with poison at time zero, **not** with zeros. Zero-fill is
   the failure mode that works in simulation and fails in silicon, because
   firmware that assumes a cleared memory passes every test and then meets an
   uninitialised macro.
2. Drive poison on `read_data` for the cycle following a write, per section 4.
3. Use a pattern that is implausible as real data and obvious in a waveform.
   Alternating bits (`0xAAAA` / `0x5555` by word parity) is suggested so that a
   stuck output is also visible.

Poison makes accidental dependencies easier to notice, but cannot guarantee that
misuse fails: a consumer might accept the pattern as data. Consumer verification
should assert valid use and can repeat tests with varied unspecified values.
Track written locations and output definedness in the testbench; this tracking
does not add reset, initialization, or validity ports to the primitive.

Poison initialization and post-write injection belong to the behavioral simulation
model. They must not introduce an initialization guarantee or extra poison logic
into the synthesizable flop implementation or a physical macro. Simulation and
synthesis artifacts remain separate, as specified in the architecture plan.

---

## 6. Out-of-range addresses

`Config.create_exn` requires `width >= 1`, `depth >= 2`, and `read_latency = 1`.
The address width is `ceil(log2(depth))`; the write-data and returned read-data
widths are `width`. Enable and write-enable are one bit, and all ports use the
same clock domain. Construction must reject signal-width mismatches at elaboration.

`depth` is not required to be a power of two, so `address` can encode values at or
above `depth`. Such an access is **out of contract**: the primitive makes no
promise about what is read or which location is written.

The behavioural model should flag it — an assertion or a poison return — rather
than wrap silently. A macro will do something technology-specific and nothing
useful, so no portable behaviour can be offered.

---

## 7. What this contract deliberately does not promise

Listed so that a consumer cannot acquire a dependency on an accident of the
implementation:

- A particular output value in response to a write or a read of an unwritten
  location. Disabled cycles must still hold the backend's previous output; a
  later read of a written location restores a defined result.
- Any relationship between contents at power-up and any prior state.
- Byte, bit or masked writes.
- Simultaneous read and write.
- Read latency other than 1.
- Defined behaviour for addresses at or above `depth`.
- Power, area or timing characteristics. Those come from a hardening run, not
  from this document.

---

## 8. Backend obligations

Implementations must satisfy the hardware contract in sections 4 through 6.
Poison and invalid-access diagnostics are simulation obligations; section 9
defines comparison where hardware values are unspecified.

Here, "backend" means a resource implementation (behavioral, flop, or technology
macro), not a flow adapter such as LibreLane or a future commercial adapter.
Changing EDA tools does not change this contract or authorize a different memory
selection. The resource registers its selected implementation and collateral;
the flow adapter validates and renders the views needed for its operation.

| Backend | Status | Notes |
| --- | --- | --- |
| Behavioural model | implemented | Diagnostic poison per section 5.1; reference for defined behavior |
| Generic flop implementation | implemented | Synthesizable, no macro; explicitly selected or explicitly allowed as fallback |
| IHP CMOS5L macro | **exact 256×16 backend and fresh synthesis/full implemented** | Functional, mapped and physical example evidence; timed-model qualification is not claimed |

The stock macro is directly instantiated without adding registers or altering this contract. The exact stock R0 physical profile supports fresh coarse runs and [limited segmented execution](../guides/segmented-sram.md). LVS is abstract macro-boundary/interconnect with schematic CDL, not internal-device verification. Zero-delay replay is not timed-model conformance. Functional conformance, timing libraries/constraints, STA and electrical/physical acceptance remain required.

The initial implementation used an explicitly selected flop implementation
and did not wait for a macro. A macro-required request must fail if no supported
exact mapping exists and no fallback was explicitly permitted. Never silently
change shape, latency, or behavior, or compose several macros. Record the chosen
implementation and any fallback reason in the build manifest.

---

## 9. Verification obligation

Concept-note open question 7 asks how to verify that simulation semantics match
the selected physical macro. The answer this contract enables:

Write one cycle-accurate test suite from section 4 and run it against every
backend. The six-cycle trace is a seed, not a complete test: initialize locations
through writes before expecting defined read data, and extend it to observe the
disabled cycle following a write.

The scoreboard tracks written locations, their expected contents, read latency,
and whether the current output has a contract-defined value:

- An enabled write updates the addressed location and makes the next output
  unspecified. Check poison separately for the behavioral model only.
- An enabled read of a written location produces its expected value one cycle
  later. Compare this value across implementations.
- An enabled read of an unwritten location produces an unspecified value.
  Physical implementations need not match the behavioral poison pattern.
- A disabled cycle preserves each backend's own prior output, including after a
  write. Check hold within that backend even when cross-backend value comparison
  is inappropriate. Output definedness is also preserved.
- An enabled out-of-range access is out of contract. Verify the behavioral
  diagnostic separately; do not require agreement on subsequent hardware state.

Cover disabled writes, consecutive reads and writes, write-then-read, holds after
defined and unspecified results, and non-power-of-two address bounds. For a
four-state macro model, account for unknown values when checking stability rather
than treating behavioral poison as the expected value.

A backend passes by satisfying defined behavior and temporal obligations, not
by reproducing the simulation model's choices for unspecified values. The
consumer separately verifies that it never depends on those unspecified values.

---

## 10. Project integration decisions

Resource construction takes the temporary `Elaboration.Context` belonging to one
project elaboration, registers the resource, and continues to return read data.
The implemented signature is:

```ocaml
val create
  :  Elaboration.Context.t
  -> ?name:string
  -> Config.t
  -> clock:Signal.t
  -> enable:Signal.t
  -> write_enable:Signal.t
  -> address:Signal.t
  -> write_data:Signal.t
  -> Signal.t
```

Registration records instance identity, requested contract, selected
implementation and fallback reason, source-set membership, and any required
wrapper, model, timing, or physical files. The finalized immutable build uses
those records to emit flow inputs and its manifest. Flop-backed instances are
registered too, even though they have no macro collateral.

The design constructor discovers resource instances through these calls. The
project supplies selection policy, not a duplicate list of memories. Exact
mapping and explicit fallback apply as in the architecture plan. No global
mutable project or backend selection is required.

Returning `Signal.t` is sufficient for this primitive. Macro-specific power or
ready ports must not be added merely to carry registration information; any
future architectural requirement needs an explicit contract decision.

Program validity remains owned by the consumer. The context does not take over
program loading, halted-state arbitration, or fetch-pipeline validity. The
emulator validates its declared loaded image and prevents out-of-range/out-of-image
fetches; these checks do not add validity or bounds ports to this primitive.
The library owns backend conformance; the consumer separately verifies that load,
readback, fetch, reset, and recovery never depend on unspecified values.

## 11. Hierarchical naming

The context carries the current scope, so `create` needs no scope argument. Identity is the scope path plus the instance name. The default name is `ram`; callers give distinct explicit names to multiple RAMs in one scope. Memory size, instruction packing and placement of validity metadata are consumer decisions.
