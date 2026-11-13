# Deferred work

This is not an automatic implementation queue. Reactivate work through the
[central phase plan](../phase_plan.md) with a concrete consumer and acceptance test.

| Item | Reactivation condition |
| --- | --- |
| [Progress inside long-running ASIC operations](operation-progress.md) | Installer stages/heartbeat/live output delivered; finer-grained diagnostics within silent test/tool operations remain deferred |
| ASIC P7.1–P7.6 historical consumer consolidation | The owner chooses to migrate that consumer and its current code can be audited; the absent scaf worktree is not a prerequisite for the new example |
| [TT GitHub submission inputs (`custom_gds`, `pdk`, `tools-ref`)](tt-github-submission-inputs.md) | A GDS built here is published to a TT GitHub repository for official precheck |
| [Nix development environment proposal](nix_reproducibility_repo_handoff.md) | Explicit environment-reproducibility project; independent of the already-used Nix postcheck invocation |
| Typed/canonical resource contract matching | A second resource author or exact macro mapping needs a stable public representation; current structural S-expression equality is field-order-sensitive |
| Additional targets and flow adapters | A real second target/backend demonstrates where to extract TT/LibreLane-specific code |
| Generated clocks and broader timing constraints | A selected design requires them; the first FIFO stays single-clock |
| General macro API, automatic memory composition and more port shapes | New resource contracts and verified collateral require these features |
| Commercial EDA, MMMC, DFT and power-intent frameworks | A concrete licensed flow needs a bounded adapter; not initial provider infrastructure |
| Generic resume/scheduler/plugin framework | An actual operation requires defined continuation semantics; step snapshots alone do not imply resumability |

SRAM has its own [capability track](../sram/README.md), with open gates; moving
its documents does not change any gate outcome. Workbench UI backlog belongs in
Workbench, including web rendering and a dedicated comparator tile.
