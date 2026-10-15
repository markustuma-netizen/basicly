# How to wire up the verify gate

A fresh `basicly.toml` declares **no** verify checks, and an empty gate passes
vacuously:

```text
No verify checks configured for mode 'fast' in basicly.toml; nothing to gate.
```

The loop still records `verify: pass` and still ships the bead. Until you
declare the checks your stack actually has, the required gate proves nothing.
This is the first configuration a real repo owes.

## Declare the checks

Each check is a `[[verify.checks]]` table in `basicly.toml`: a name, an argv
list (no shell — the command is not string-parsed), and the modes it runs in.

```toml
[[verify.checks]]
name = "ruff"
command = ["uv", "run", "ruff", "check"]
modes = ["fast", "full", "staged"]
staged_suffix = ".py"

[[verify.checks]]
name = "ruff-format"
command = ["uv", "run", "ruff", "format", "--check"]
fix_command = ["uv", "run", "ruff", "format"]
modes = ["fast", "full", "staged"]
staged_suffix = ".py"

[[verify.checks]]
name = "pytest"
command = ["uv", "run", "pytest", "-q"]
modes = ["full"]
```

## The three modes, and which one runs when

| Mode | Runs | Put here |
| --- | --- | --- |
| `staged` | `basicly verify --mode staged`, with suffix-matching staged paths appended | file-local lint and format |
| `fast` | pre-commit, or `basicly verify --mode fast` | lint, type check, a quick unit subset |
| `full` | ordinary `basicly verify`, pre-push and loop landings | the whole suite |

A `staged` check without `staged_suffix` receives no filenames and uses its
configured command's scope. Add `--affected` to validate index contents from the
CLI; ordinary verification reads the working tree. A configured command that is
missing from `PATH` fails the run with a one-line
message rather than being skipped — a check you cannot run is not a check that
passed.

## Scope a check to what it reads

A check may declare the files it reads, as repo-relative globs:

```toml
[[verify.checks]]
name = "lint-imports"
command = ["uv", "run", "lint-imports"]
modes = ["fast", "full"]
inputs = ["src/**/*.py", ".importlinter"]
```

The pre-commit hook then skips that check, by name, on a commit whose staged diff
matches none of its globs — a docs-only commit stops paying for the type checker.
Nothing else reads the key: `full`, the mode a landing and a push run, runs every
declared check whatever it says about its inputs, so a skip can never be what a
green landing rested on.

Two rules keep that safe. A check declaring no `inputs` always runs, so the key is
opt-in per check and a repo that adds none keeps today's behaviour. And an
undeterminable diff runs everything: git failing to report the staged paths, or an
empty index, skips nothing.

Derive the list from the tool's own scope rather than guessing it. A list *narrower*
than what the check really reads costs a bounced landing — the gate the commit
skipped fails on the rebased tree instead. A glob that matches nothing is worse,
because it skips the check on every commit there is; this repo pins its own lists
against `git ls-files` in
`tests/test_git_hooks/test_check_runner.py::test_every_declared_input_set_matches_something_in_this_repo`.

## Select checks with a consumer-owned dependency graph

An optional provider can select existing checks from a graph your repository
already owns. basicly does not infer language dependencies from this declaration:

```toml
[verify.affected]
command = ["python3", ".scripts/affected-checks.py"]
checks = ["ruff", "ruff-format"]
timeout_seconds = 30
```

The named checks must already exist. Checks outside this list always run when
the provider is configured. Git pre-commit uses the provider inside its staged
snapshot. To request the same behavior explicitly:

```sh
basicly verify --affected
```

This command reads configuration and executes checks in a temporary copy of the
index. An unstaged repair cannot hide a staged failure. It preserves the original
index and working tree. `--affected` cannot combine with `--fix` or `--issue`;
use ordinary verification to repair files or record a required gate.
Ordinary verification, pre-push and loop landings retain broad checks.
Existing installations need no configuration changes when upgrading.

The provider receives one JSON document on stdin:

```json
{
  "schema": "basicly-affected/v1",
  "snapshot_tree": "<Git index tree id>",
  "base_tree": "<HEAD tree id>",
  "changes": [
    {"status": "R100", "old_path": "old.py", "path": "new.py"}
  ],
  "checks": ["ruff", "ruff-format"]
}
```

It returns that request unchanged, with four additional fields:

```json
{
  "complete": true,
  "selected": ["ruff", "ruff-format"],
  "reasons": {"ruff": "changed Python owner", "ruff-format": "changed Python owner"},
  "unverified": []
}
```

The response must echo the complete request, including the change list.
Explain every requested check, including checks left unselected. Additions carry
`path`; deletions carry `old_path`; modifications carry both; renames and copies
retain both names. The provider must account for shared configuration, lockfiles,
dependents and unknown ownership using its actual graph. Return `complete: false`
or nonempty `unverified` when it cannot answer. basicly validates the protocol and
snapshot binding; graph completeness remains a claim of the consumer's provider.

Missing, invalid, stale, failed or timed-out responses run all checks in the
requested mode and report the fallback. Responses over one MiB also fall back.
Provider execution is bounded by the configured timeout, at most 300 seconds.
Other untracked inputs prevent selective execution. The prepared `.venv`,
`node_modules`, `build/lefthook` and `.basicly/usage` links are runtime metadata.
A provider that changes snapshot inputs fails verification before checks run.

Git writes selection and command outcomes to
`.basicly/usage/affected-run.json`. The CLI records selection, fallback reasons,
executed checks and skipped checks in `.basicly/usage/verify-run.json`.
The selection records the provider request and bounded stdout/stderr diagnostic
paths under `.basicly/usage/affected-provider.*`.
Unselected checks did not execute; they are not cached passes. This integration
adds no build-system dependency or persistent result cache.

Maintainers can validate a built wheel outside this checkout:

```sh
BASICLY_BASELINE_WHEEL=/path/to/released.whl BASICLY_VALIDATION_WHEEL=/path/to/candidate.whl uv run python .scripts/validate_affected_consumer.py
```

Provide prepared wheels and offline Python dependencies. The script downloads
nothing. It exercises native commits, partial staging, graph dependents,
unrelated checks, foreign hooks, fresh installation and upgrade. It reports
command counts and three warm hook samples separately from repository setup.
Its small Python fixture does not establish mixed-stack scaling performance.

## Reuse a proven compiler result during landing

Legacy configurations need no changes. Checks run fresh unless they declare a
reviewed recipe:

```toml
[[verify.checks]]
name = "pyright-linux"
command = ["pyright", "--pythonplatform", "Linux"]
modes = ["full"]
reuse = "pyright"
```

The loop carries the actual successful report from its pre-merge verification.
During post-merge verification, it can reuse a matching compiler result.
Ordinary `basicly verify` executes its checks afresh by default.
The `inputs` globs above select staged checks and cannot establish reusable proof.

The initial recipe supports bundled Pyright 1.1.411 through its reviewed POSIX console launcher
and an existing global Node executable. It supports the Linux, Darwin and Windows
analysis targets. Windows hosts and unsupported launcher strategies run fresh.
The project must configure `.venv` under its checkout in `pyproject.toml`.
External search paths, configuration inheritance and other unsupported settings
also run fresh.

The receipt binds the command, mode, source names and contents, configuration,
analysis environment, tool files, interpreter, Node executable and environment.
It inventories Python sources and stubs, including untracked files, to detect new
imports and shadowing. It excludes Git metadata and virtual environments from
source discovery. Python files inside Node dependencies and bytecode directories
remain inputs because explicit imports can reach them.
Analysis search paths into excluded directories refuse reuse.
Protected dotenv files are never read. Protected source inputs refuse reuse.

The engine compares inputs before and after verification. A missing, failed,
unsupported or changed receipt executes the check afresh. If another check
changes inputs during post-merge verification, the borrowed checks execute again.
Without an enabled cache, receipts stay within one process and one landing.
Other checks, including tests, execute afresh.

### Reuse compiler results across commands

Enable the repository-local cache explicitly:

```toml
[verify]
cache = true
```

Checks still need the reviewed `reuse = "pyright"` recipe above. The cache is
bounded and stored in ignored `.basicly/usage/verify-cache.json`. The CLI and
loop use the same cache. Standalone hooks without the engine keep fresh execution.
An engine or repository identity change, corrupt cache, unsupported input or
changed fingerprint runs the compiler freshly. Only stable, successful
verification writes cache entries. Isolated engine installations fingerprint
the actual Pyright interpreter independently from the engine's interpreter.

To execute every check freshly for one command:

```sh
basicly verify --mode full --no-cache
```

This command leaves existing cache evidence intact. Reused outcomes name the
original run and its compiler duration; their current execution duration is zero.
The run's total duration includes fingerprinting and checks that ran freshly.
Compiler reuse does not establish faster tests or constant-time repository scaling.

`.basicly/usage/verify-run.json` records the run identity, duration, disposition,
source run and input fingerprint. `.basicly/usage/landing-timings.json` records
the first landing and the post-merge verification separately. Match their
`verify_run_id` and `source_run_id` fields when comparing elapsed times.

## Run it

```sh
basicly verify --mode fast
```

```text
============================================================
  notes-present: PASS
[verify] PASS (mode: fast)
```

On a failure it names the check and exits non-zero:

```text
  notes-present: PASS
  always-fails: FAIL
[verify] FAIL: always-fails
```

`--fix` applies each check's `fix_command` before the checks run. Keep those
strictly mechanical and lossless — a formatter, an import sorter. The check
still runs afterwards either way, so a `fix_command` never turns a red into a
green by itself.

## How the gate reaches a bead

You almost never record it by hand. The build→verify `loop advance` is the only
step that merges a worktree back, and it runs verify against the rebased tree
and reports the gate itself:

```text
[merged] build -> verify: merged harness/myrepo-etk @ 3f9c1ab07e42 (2 commit(s)) into main @ d605fb4
```

Recording the gate out of band during build (`basicly verify --issue …`) makes
the derived phase jump to verify **with the merge skipped**, and the loop then
ships and closes the bead with the code stranded on the branch. Record it
manually only *after* a landing has merged — re-verifying after rework, from the
base checkout:

```sh
basicly verify --mode full --issue myrepo-etk
```

Inspect what the loop will decide on:

```sh
basicly policy gate myrepo-etk
```

## Required versus advisory

`[policy] required_gates` decides which gate names block an advance. The default
is one:

```toml
[policy]
required_gates = ["verify"]
max_rework = 2
```

Any recorded gate *not* in that list is advisory — it is visible on the bead and
never blocks. That is the line between deterministic checks (tests, lint, types:
required) and judged output from an AI reviewer (advisory, always). A failed
required gate sends the node into the bounded rework loop, `max_rework` attempts
before it escalates to a human:

```sh
basicly policy rework myrepo-etk --gate verify
```
