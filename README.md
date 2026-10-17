# cdebt

**Comprehension debt** is the gap between the code that exists in a repo and the code any human
actually understands. AI agents write much of the code, reviews get skipped or rubber-stamped,
and nobody knows how big the gap is. `cdebt` makes it visible, testable and enforceable.

> **cdebt measures code and teams, never individuals.** There are no leaderboards and no
> per-person scores in any report. An optional audit label is stored but never aggregated.
>
> **A quiz is not a lie detector.** It cannot stop anyone pasting the questions into an AI.
> cdebt gives *visibility*, not proof of understanding. See [docs/LIMITS.md](docs/LIMITS.md).

| command | what it does |
|---|---|
| `cdebt map` | Which files hold the most AI-written code no human has verifiably understood? |
| `cdebt quiz <file>` | Questions whose answers come from **executing** the code; records what is now understood |
| `cdebt gate --base origin/main` | CI check: fail a PR whose AI-authored changed lines sit in un-understood code |
| `cdebt plan` | Ranked pay-down plan (quiz / review / rewrite-or-delete) with effort estimates |
| `cdebt backtest` | Does the (proxy) score predict later fixes and reverts on AIDev? Reproducible, published as measured |
| `cdebt index` | Build the SCIP index used for exact call-graph facts and blast radius |

## The four differentiators

1. **Execution-verified answers.** Quiz answers come from sandboxed execution, a SCIP call
   graph, or mutation runs. Grading is a deterministic rule. No model ever judges an answer.
2. **Real line-level AI attribution** from [Git AI](https://github.com/git-ai-project/git-ai)
   (installed separately, read from `refs/notes/ai`; never vendored), plus Agent Trace records.
   Co-Authored-By trailers are a labelled, coarse fallback. Every output names its source:
   `git-ai | agent-trace | trailer | none`, where `none` means *unknown*, not "human".
3. **Expiring understanding.** A record is bound to a hash of the code it covers and is stale
   the moment that code changes.
4. **Backtested.** `cdebt backtest` runs a pre-registered analysis on the AIDev dataset and
   reports effect sizes with confidence intervals, including null results
   ([docs/RESULTS.md](docs/RESULTS.md)).

## Backtest result (as measured, 2026-10-17, AIDev revision 7840b6e)

On 167,163 eligible (PR, file) units in 1,551 repos, the *proxy* score did **not** show a
clear, consistent link to later fixes: the pre-registered repo-stratified risk ratio for a fix
within 90 days was 0.997 (95% CI 0.657-1.524), time-to-fix was *shorter* for the high group
(Cliff's delta -0.32), reverts were too rare to analyse (2 events), and only the size/churn/agent
adjusted odds ratio pointed the predicted way (1.93, CI 1.13-3.49). The crude association (RR 1.63)
largely disappears once you compare within repos. Full table, caveats and a post-hoc deviation
are in [docs/RESULTS.md](docs/RESULTS.md). This does not validate the score; treat it as
unproven.

## 60-second demo

```bash
pip install cdebt            # Python 3.11+
bash examples/demo.sh        # builds a throwaway repo and walks map -> quiz -> map -> gate
```

What it shows (real output, see `examples/demo.sh`):

```
cdebt map     ... AI lines understood: 0%   | AI units understood: 0/3
cdebt quiz    ... total: 2/2 -> UNDERSTOOD (bound to hash ccffd334cb)
cdebt map     ... AI lines understood: 67%  | AI units understood: 2/3
(edit total() and commit)
cdebt gate    ... FAIL  pkg/shop.py::total  1 AI lines [stale, trailer]
```

Interactive use:

```bash
cdebt map                              # table or --json
cdebt quiz src/billing.py --n 5        # answer in the terminal
git add .cdebt/understanding.json && git commit -m "record understanding"
cdebt gate --base origin/main --strict-unknown
cdebt plan --top 10
```

For non-interactive use: `cdebt quiz FILE --emit q.json` writes the questions (no answers),
then `cdebt quiz FILE --answers answers.json`.

## Attribution setup (optional but recommended)

Without Git AI, cdebt uses commit trailers (coarse) and reports everything else as unknown.
For line-level attribution install Git AI per its docs. Git AI 1.7.x records through a daemon
that needs git trace2 configuration; see its docs (`git-ai debug` shows what is missing).
cdebt only reads the notes; it never writes to your repo except `.cdebt/`.

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: {fetch-depth: 0}
- uses: <owner>/cdebt@v0.1.0
  with: {max-ai-lines: "0", strict-unknown: "false"}
```

It runs `cdebt gate`, fetches `refs/notes/ai` best-effort, writes a job summary and a sticky PR
comment. (UNVERIFIED on a live GitHub run: the script was exercised locally only.)

## Safety model

Read-only on your repo (git calls are allow-listed read commands; writes go only to `.cdebt/`).
Probe execution is sandboxed (isolated interpreter, temp cwd, timeout, no network, resource
limits, `sandbox-exec` on macOS, Docker when available) and skips code with file, network,
time or randomness side effects. The sandbox targets accidental/AI-generated side effects, not
hostile native code. No network or API key is needed for any core feature.

## Development

```bash
uv sync --extra backtest
uv run pytest -q && uv run ruff check . && uv run mypy src
bash scripts/make_gitai_fixtures.sh   # regenerate real Git AI fixtures (needs git-ai)
```

Docs: [PLAN](docs/PLAN.md) · [METHOD](docs/METHOD.md) · [LIMITS](docs/LIMITS.md) ·
[RESULTS](docs/RESULTS.md)

License: Apache-2.0.
