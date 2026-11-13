# Progress inside long-running ASIC operations

Status: Partially implemented — installation progress delivered 2026-11-13

`scripts/install.py` now supplies numbered build/test/install stages, elapsed
times, heartbeats, live unbuffered test output, verbose output, and supervised
cancellation. Ten fake-Dune contract tests and `@test/install-script` pass.
See [consumer installation](../guides/consumer-installation.md#source-checkout-installer-with-progress).
Fine-grained phase/case reporting inside individual silent operations remains
open; the installer heartbeat reports activity, not progress within those cases.

Reactivation condition: Electrum's local tools installation runs ASIC tests with
little visible activity; improve upstream operation-level reporting alongside
the consumer's installer output.

## Problem and ownership

Electrum captures ASIC sources and invokes Dune build, runtest, and install.
Its installer currently redirects subprocess output to a build log. Restoring
live output and reporting installation stages belongs to Electrum:
[consumer backlog](../../../electrum/docs/backlog/tool-installation-progress.md).

Even with live forwarding, a long-running ASIC test or operation may emit no
useful intermediate output. ASIC owns reporting within those operations, and
that reporting should also help direct ASIC users and other consumers.

## Proposed work

- Identify long-running ASIC tests and command-line tool operations with silent
  intervals, starting with those exercised by the consumer installation's
  `dune runtest` step.
- Report meaningful suite/phase/case boundaries, elapsed time, completion, and
  failure. Where no count is known, report activity rather than a fabricated
  percentage.
- Provide an explicit progress/verbose mode where routine output would disturb
  existing tests or consumers; document how callers enable it and any Dune
  output-capture limitations.
- Keep progress on a diagnostic channel separate from JSON/protocol stdout and
  deterministic evidence. Preserve useful raw logs and avoid interleaved,
  unreadable reporting from parallel workers.

## Acceptance

- Representative slow operations expose useful intermediate diagnostics when
  invoked directly and through a consumer forwarding subprocess output.
- Reporting identifies the active operation and accurately distinguishes
  success, failure, and interruption.
- Machine-readable output, existing test expectations, result identities, and
  exit-status behavior remain valid with progress enabled and disabled.
- Terminal and redirected-log output remain readable; documentation makes clear
  that consumer-level source capture/build/install stages remain consumer-owned.
