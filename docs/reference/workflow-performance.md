# Measure workflow performance

Run this measurement from a basicly source checkout. It uses a packaged engine
in disposable consumer repositories, with the installed Git hooks active.
It never dispatches a model, downloads a wheel or bypasses a gate.

Prepare a candidate wheel and the published release wheel before running it.
The wheel builder requires the verified Lefthook assets described in
[the development setup](../../CONTRIBUTING.md#development-setup).
Use separate directories when both distributions have the same version.

```sh
uv build --wheel --offline --out-dir build/workflow-performance/candidate
uv run python .scripts/measure_workflows.py \
  --wheel build/workflow-performance/candidate/basicly-0.25.1-py3-none-any.whl \
  --baseline-wheel build/workflow-performance/released/basicly-0.25.1-py3-none-any.whl
```

The published input is the wheel from the pinned release, not another build of
the candidate. Python dependencies must already be available in the uv cache.
Every package installation in the measurement is offline. To compare a later
version, substitute its wheel filename in `--wheel`.

The command prints the result path. `result.json` identifies both wheel hashes,
the measurement and fixture source hashes, the interpreter and the platform.
`commands.jsonl` preserves every subprocess argv, working directory, exit code,
stdout, stderr and monotonic elapsed time. Keep both files with the tracker
evidence. Transcripts can contain machine paths; use the tracker artifact API
to apply its normal redaction before committing them.

## Workloads and boundaries

| Workload | Included interval | Result required |
| --- | --- | --- |
| Installation and upgrade | Published install, fixture preparation, candidate runtime upgrade, consumer convergence, fresh candidate install and warm convergence | Consumer configuration and foreign hook configuration survive |
| Small change | Task creation and review through ordinary landing and ship | The changed Python value is confirmed, the task closes and its worktree disappears |
| Documentation | Task creation and review through ordinary landing and ship | The exact note is confirmed, the task closes and its worktree disappears |
| Two lanes | Both task lifecycles, with overlapping edits, checks and commits, followed by serial landing | Both confirmed changes land and both worktrees disappear |

The small-change and documentation fixtures have the same four cheap configured
checks. Their different paths exercise native hook selection; their timings do
not predict the cost of basicly's full repository suite. Setup for each change
fixture is outside its headline interval and remains in the command transcript.
The installation headline includes its setup; individual install spans are
also reported. Runtime creation and the initial wheel installation are separate
setup commands in the transcript.

Check execution counters live beside the report, outside consumer checkouts.
The measurement must not make its own telemetry an unknown ignored file that
prevents normal worktree cleanup. This does not grant ownership of consumer
files placed in `.basicly/usage/`.

Fresh means a new consumer repository. Dependencies and operating-system caches
are prepared; this is not a cold-machine measurement. Repeating the script
creates new consumers. Record each sample separately and compare matching
workloads, source hashes and environments. Inspect individual samples before
using their median; never compare a blocked baseline interval with a successful
candidate as if they had the same endpoint.

The owner must approve the deterministic fixture edits and checkpoint relays
before running this command. Relays use the native printed challenges and retain
ordinary expiry and replay checks. They are counted separately from human
interventions. Actual configured-check executions and native landing/verification
histories are preserved; a repeated check name alone does not prove equivalent
inputs or redundant execution.

A blocked native workflow is a measurement result, not a successful delivery.
The report lists it as blocked even when the measurement command exits zero.
An unexpected command failure raises and leaves its captured transcript.
Model tokens, actual agent authoring time and CPU saturation are unmeasured.
The two-lane fixture measures concurrent native builds, not paid supervisor
dispatch, model communication or scheduling performance.
