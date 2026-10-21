# CLAUDE.md — cdebt

cdebt measures, tests and pays down **comprehension debt**: the gap between code that
exists and code a human has verifiably understood. Full plan: `docs/PLAN.md`.

## The four differentiators (never water down)

1. **Execution-verified answers.** Quiz answers come from sandboxed execution, SCIP
   call-graph queries, or mutation runs. The developer's own coding agent may PROPOSE
   questions, call inputs and test harnesses; cdebt executes every proposal in the sandbox to get
   the true answer and discards anything it cannot execute or verify. An LLM never decides
   correctness and never grades free text. The agent is never shown the correct answer.
2. **Real line-level AI attribution.** Git AI is an *installed* dependency
   (`refs/notes/ai`, `git-ai stats --json`). Never vendor or fork it. Agent Trace is
   also supported. `Co-Authored-By` trailers are a labelled fallback only.
3. **Expiring understanding.** Every record is bound to a content hash of the code it
   covers and is stale the moment that code changes.
4. **Backtested.** Reproducible AIDev backtest; effect sizes with CIs; negative
   results published as-is. Never invent numbers.

## Hard rules

- **Never rank individuals.** No leaderboards, no per-person scores in any output.
  `audit_label` is stored for audit only and is never aggregated.
- **Label attribution source** (`git-ai | agent-trace | trailer | none`) on every
  output. `none` = unknown, never "human-written".
- **Fail safe on unknown.** Unknown attribution is reported, not silently gated;
  `--strict-unknown` opts in to failing on it.
- **Read-only on the user's repo** except `.cdebt/`, the git ref `refs/notes/cdebt`, and the
  pre-push hook that `cdebt init` installs with the developer's explicit consent. Never rewrite
  git history, never touch `refs/notes/ai` or working files. Git calls are otherwise read-only.
- **No timing data stored, no per-person flags.** Answer timing is used in memory during a quiz
  session and discarded. Nothing stores or reports suspicion about a person; status reasons
  describe the CODE record, never a person.
- **Sandbox all execution** (isolated subprocess, temp cwd, timeout, no network,
  resource limits; Docker if available). Skip code with file/network/time/randomness
  side effects. Never run dangerous payloads outside the sandbox.
- **Deterministic first.** Rules decide pass/fail. Core works with no API key and
  no network.
- **Report uncertainty.** Wilson intervals for rates; always show n.
- **Honest limits.** A quiz can't stop someone pasting questions into an AI.
  Position as visibility, not a lie detector (README + `docs/LIMITS.md`).

## Working rules

- Work milestone by milestone (M0–M6 in the plan). **Stop for review after each.**
- Before declaring a milestone done run: `pytest -q && ruff check . && mypy src`
  and show the real commands and output.
- Never claim a feature works without running it.
- Don't invent benchmark numbers, dataset statistics, or Git AI / Agent Trace /
  AIDev behaviour. Mark anything unverified `UNVERIFIED` in code comments and reports.
- Ask before adding any dependency outside the approved list (Python 3.11+,
  Pydantic v2, Typer, Rich, pytest, ruff, mypy, GitPython or `git` subprocess,
  tree-sitter, SCIP indexers, mutmut, Stryker, PyDriller, uv).
- If the spec is ambiguous or a better approach exists, say so at the milestone and
  wait; don't silently diverge.
- Small, reviewable commits. Only commit when asked once the repo exists.
- Unit tests use fixtures and throwaway git repos built in the test. **No network.**
- Parsers are written against real captured tool output (committed to
  `tests/fixtures/`), not guesses.

## Stack and layout

Python 3.11+, Pydantic v2, Typer, Rich, pytest, ruff, mypy (strict on core), `uv` +
`pyproject.toml`, entry point `cdebt`. Source in `src/cdebt/` (models, gitutil,
attribution/, store, mapper, probes/, sandbox, gate, plan, backtest/, cli).

## Commands (once M1 lands)

```bash
uv sync
uv run pytest -q && uv run ruff check . && uv run mypy src
```
