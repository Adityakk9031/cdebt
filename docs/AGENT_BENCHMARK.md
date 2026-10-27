# Does the developer's agent raise how much AI-written code a quiz can verify?

Measured 2026-10-22. Reproduce with `scripts/bench_agent_coverage.py` (needs the Claude Code CLI
logged in; it uses a little of your usage). Raw rows:
`docs/case_studies/agent_benchmark.after_fix.json`; totals:
`docs/case_studies/agent_benchmark.summary.json`.

## Setup

The 6 highest-debt Python files of each of two real repositories from the case studies
(`space_chassis` at 9a0430a37c and `agent-meta` at 8457634f1e, attribution `trailer`), run in the
default strict sandbox with no `--trusted`. For each file: how many AI-written functions/methods
have at least one execution-verified question from (a) the built-in generator only and (b) the real
Claude Code agent through `cdebt mcp`. A unit "counts" if it has one verified question, not if it
is fully cleared. Every agent `return_value` answer was re-run through a second, independent
sandbox path (the plain `call` runner) and compared.

## Result

| | built-in only | with the agent |
|---|---|---|
| AI-written functions with a verified question (12 files, 565 units) | 1 | 41 |
| AI lines in those functions (of 12688) | 240 (1.9%) | 1089 (8.6%) |

- The agent made 130 proposals; 55 were accepted and verified by cdebt's own execution (42.3%).
- Of the agent's `return_value` answers, 22 could be compared through the second sandbox path and
  **all 22 agreed**; 27 could not be compared (harness probes, keyword arguments, methods).
- Most common rejections: a harness without a `question` (20; the adapter prompt now says it is
  required, not re-measured), a test that writes a file (11), no SCIP index for call-graph
  questions (10), `which_test_fails` without `--trusted` (5).

## What this does and does not show

- It shows the agent adds verifiable code on files where the built-in generator finds almost none
  (custom classes, methods, test files). It does **not** show most AI code becomes quizzable: on
  these hard files it is under a tenth of the AI lines.
- Small sample: 12 files, 2 repositories, the highest-debt (hardest) files, one agent run per file,
  one machine. Questions differ from run to run. Nothing here measures question quality or whether
  answering them shows understanding (that is the validity study, not run).

## A bug this measurement found

The first run showed **0 of 444** AI functions quizzable even with the agent (9 files). The cause
was the sandbox hardening of the previous release: it blocked `sys._getframe` outright, but the
standard library calls it for `@dataclass`, `typing.TypeVar`/`NamedTuple`,
`collections.namedtuple`, `logging`, `warnings.warn`, `argparse` and even `import pathlib`, so most
real modules could not be imported. The guard now refuses only the runner's own frames; the
red-team suite gained a deep frame-walking attack and a stdlib regression test
(`tests/test_sandbox_redteam.py`). Running the earlier release `v1.0.0-rc1` on the same repository
gives the same built-in coverage as the fixed code, so the fix restores the earlier level.

## The older coverage figures

`docs/CASE_STUDIES.md` quotes verifiable shares over the top 25 files measured earlier. Re-measuring
`space_chassis` today with the same settings gives 9% (older release and current code agree), not
the older 19%: the top-25 selection depends on recent churn and moves with the date, so those
figures are examples from one day, not constants. `agent-meta` (call-graph index) and the
TypeScript repository were not re-measured.

## Reach: batching, retry and the fake world (measured 2026-10-27)

Change: the agent now gets a file's functions in batches of 6 (one run each) and any function with
fewer than 3 verified agent questions is offered once more; clock, random and environment reads are
faked in the sandbox (see CHANGELOG). Reproduce with `scripts/bench_agent_reach.py`.

Sample: 6 files of cdebt's own code (52 functions), strict sandbox, Claude Code agent, **one run
per mode**. Questions differ from run to run, so treat the gap as indicative, not exact.

| | before (1 run, 1 round) | after (batches + retry) |
|---|---|---|
| functions with >= 1 verified question | 32 of 52 | 41 of 52 |
| functions with >= 3 verified questions | 3 of 52 | 28 of 52 |
| verified questions | 53 | 128 |
| agent time | 297 s | 953 s |

- **Cost: about 3x the agent time.** The gain comes from more agent runs, not from a better prompt.
- "Before" counted a function with 1 question as covered; the quiz needs 3 (see the second row).
- This is cdebt's own code, which the agent can read freely and whose dependencies are installed.
  It says nothing about repositories with heavy dependencies (next point). The 2026-10-22 numbers
  above (7% on hard files) are a different sample and were not re-run.
- Two earlier attempts produced 0 questions for reasons of my own setup, not of cdebt: a scratch copy
  that was not a git repo (the sandbox copies tracked files) and an editable install of the project
  (the sandbox cannot list the original source folder). Both are documented in the script's header.
- `backtest/matched.py` got **0 questions in both runs**. Cause (checked by running a proposal
  directly): it imports `numpy`. First, the sandbox runs with the Python that runs cdebt, which did
  not have `numpy` (a project's own dependencies are not available unless that Python has them).
  After installing `numpy` there, the next block is `ctypes.dlopen: network/native`: native
  libraries that load through `ctypes` at import are blocked on purpose. So code that needs such
  packages (numpy, and likely pandas and similar) cannot be quizzed by execution today. Allowing
  them would be a sandbox change that needs a security review; it was not made.
