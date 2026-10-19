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
| `cdebt quiz <file>` | Questions whose answers come from **executing** the code (function results, call graph, module constants) in Python and TypeScript/JavaScript (Node 24 or newer); records what is now verified |
| `cdebt attest <file> --note ...` | A human sign-off for code a quiz cannot cover. **NON-VERIFIED**, hash-bound, counts less (default 0.5 [src: src/cdebt/config.py]) |
| `cdebt coverage` | Before you trust the gate: how much of the top-N debt files can be verified, only attested, or not quizzed at all |
| `cdebt gate --base origin/main` | CI check: fail a PR whose AI-authored changed lines sit in un-understood code |
| `cdebt plan` | Ranked pay-down plan (quiz / review / rewrite-or-delete) with effort estimates |
| `cdebt backtest` | Does the (proxy) score predict later fixes and reverts on AIDev? Reproducible, published as measured |
| `cdebt index` | Build the SCIP index/indexes (Python and TypeScript) used for exact call-graph facts and blast radius |
| `cdebt study` | Validity-study kit: fixed question set, anonymous responses, team-level analysis (see [docs/STUDY_PROTOCOL.md](docs/STUDY_PROTOCOL.md)); not yet run |
| `cdebt migrate` | Upgrade `.cdebt/understanding.json` to the current schema (reading always migrates in memory) |

## The four differentiators

1. **Execution-verified answers.** Quiz answers come from sandboxed execution, a SCIP call
   graph, or mutation runs. Grading is a deterministic rule. No model ever judges an answer.
2. **Real line-level AI attribution** from [Git AI](https://github.com/git-ai-project/git-ai)
   (installed separately, read from `refs/notes/ai`; never vendored), plus Agent Trace records.
   Co-Authored-By trailers are a labelled, coarse fallback. Every output names its source:
   `git-ai | agent-trace | trailer | none`, where `none` means *unknown*, not "human".
3. **Expiring understanding.** A record is bound to a hash of the code it covers and is stale
   the moment that code changes. Understanding has three states: **verified** (execution-checked),
   **attested** (a human signed off; labelled NON-VERIFIED everywhere, weighted 0.5 in the score [src: src/cdebt/config.py])
   and none. `cdebt gate --require verified|attested` picks the policy; by default attestation is
   accepted only for module-level code and for functions a quiz cannot verify.
4. **Backtested.** `cdebt backtest` runs a pre-registered analysis on the AIDev dataset and
   reports effect sizes with confidence intervals, including null results
   ([docs/RESULTS.md](docs/RESULTS.md)).

## What we know and don't know

**Know (tested, with evidence in [docs/VERIFICATION.md](docs/VERIFICATION.md)):**
the tool runs end to end on real repos; the Git AI notes parser handles real notes from
git-ai 1.0.0-1.7.5; the sandbox contains file-write, network, fork-bomb, infinite-loop and
memory-bomb payloads (also inside Docker, on a GitHub runner); the GitHub Action fails and
passes a real PR and keeps one sticky comment.

**Don't know whether the score predicts trouble.** Two analyses on the AIDev dataset, both
published as measured:

- The first analysis (167,163 PR-file units in 1,551 repos [src: docs/RESULTS.md]): the proxy score
  had **no clear link** to later fixes (risk ratio 0.997, 95% CI 0.657-1.524 [src: docs/RESULTS.md]).
  Reverts were too rare to analyse (2 events [src: docs/RESULTS.md]). Time-to-fix was *shorter* for
  the high-score group. Only an adjusted odds ratio pointed the predicted way (1.927, CI 1.126-3.487
  [src: docs/RESULTS.md]).
- The second analysis (within-repo matched pairs, PRs with no human review vs substantive review,
  same agent/size/time): **null** (163 pairs, odds ratio 1.000, CI 0.542-2.000
  [src: docs/RESULTS_MATCHED.md]). Low power.

Both test only a proxy (human review evidence, plus churn in the first analysis); the dataset has no
cdebt records, no blast radius and no quiz results, and fixes are visible only when they are
themselves agent PRs. So the honest reading is: **mostly null, proxy only, nothing here shows
the score works, and nothing shows it doesn't.** See [docs/RESULTS.md](docs/RESULTS.md) and
[docs/RESULTS_MATCHED.md](docs/RESULTS_MATCHED.md).

**Also know (from the pilot on three real repos, [docs/CASE_STUDIES.md](docs/CASE_STUDIES.md)):**
quiz coverage is narrow today. Runtime probes need typed, side-effect-free Python functions and
produced questions for only a minority of the most-flagged files; call-graph probes need a SCIP
index; TypeScript files can be mapped and gated but not quizzed; and module-level code cannot be
cleared by a quiz at all.

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
cdebt coverage                         # what can a quiz actually clear here?
cdebt attest src/settings.py --note "read; constants match the deploy config"
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
comment. Verified on a real PR in a scratch repo (see [docs/VERIFICATION.md](docs/VERIFICATION.md)).

## Safety model

`cdebt quiz`, `coverage`, `attest` and `study init` **run code from the repository you point them
at**. Read the [threat model](docs/THREAT_MODEL.md) first.

- Repositories are **untrusted by default**: probes run in Docker (image already pulled) or, on
  macOS, under a strict Seatbelt profile (no network, no writes outside a temp dir, cannot read
  your home directory, cannot signal other processes). If neither is available cdebt refuses and
  says why. `--trusted` or `CDEBT_TRUSTED=1`, set by the person running cdebt, switches to the host
  sandbox; trust is never read from a file in the repository.
- Probes skip code with file, network, time or randomness side effects, and mutation probes (which
  run a project's own tests) need `--trusted`.
- The sandbox targets accidental and AI-written side effects, not a determined attacker with
  native-code exploits. On Windows, anything that executes code is unsupported and says so.
- Read-only on your repo: git calls are allow-listed read commands; writes go only to `.cdebt/`
  (understanding records, and a git-ignored result cache). No network or API key is needed for any
  core feature.

## Development

```bash
uv sync --extra backtest
uv run pytest -q && uv run ruff check . && uv run mypy src
bash scripts/make_gitai_fixtures.sh   # regenerate real Git AI fixtures (needs git-ai)
```

Docs: [PLAN](docs/PLAN.md) · [METHOD](docs/METHOD.md) · [LIMITS](docs/LIMITS.md) ·
[RESULTS](docs/RESULTS.md)

License: Apache-2.0.
