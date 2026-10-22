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
