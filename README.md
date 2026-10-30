# cdebt

[![PyPI](https://img.shields.io/pypi/v/cdebt?include_prereleases&label=PyPI)](https://pypi.org/project/cdebt/)
[![Python](https://img.shields.io/pypi/pyversions/cdebt)](https://pypi.org/project/cdebt/)
[![License](https://img.shields.io/github/license/Adityakk9031/cdebt)](https://github.com/Adityakk9031/cdebt/blob/main/LICENSE)

**Who actually understood the code your AI wrote?** cdebt quizzes you on AI-written code before you
push, checks every answer by running the code, and shows the result on the pull request.

[Demo](#watch-the-demo) · [Quick start](#quick-start) · [How it works](#how-it-works) ·
[Quiz rules](#quiz-rules) · [Status and limits](#status-read-this-before-using-it) ·
[Commands](#commands) · [Your coding agent](#connect-your-coding-agent-optional)

**Comprehension debt** is the gap between the code that exists in a repo and the code any human
actually understands. AI agents write much of the code, reviews get skipped or rubber-stamped,
and nobody knows how big the gap is. `cdebt` tries to make it visible and testable, and can warn
(or, if you choose, block) when AI-written changes land in code nobody has understood.

> **cdebt measures code and teams, never individuals.** There are no leaderboards and no
> per-person scores in any report. An optional audit label is stored but never aggregated.
>
> **A quiz is not a lie detector.** It cannot stop anyone pasting the questions into an AI.
> cdebt gives *visibility*, not proof of understanding. See [docs/LIMITS.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/LIMITS.md).

## Watch the demo

[![Watch the cdebt demo on YouTube](https://raw.githubusercontent.com/Adityakk9031/cdebt/main/docs/assets/cdebt-demo-thumbnail.jpg)](https://youtu.be/zO4w61hFr9A)

**[▶ Watch the demo on YouTube](https://youtu.be/zO4w61hFr9A)**

*A real push, a real quiz answered by hand, the real pull request comment, the sandbox, and the
honest limits. Source: [examples/first_user/launch-video](https://github.com/Adityakk9031/cdebt/tree/main/examples/first_user/launch-video).*

## Quick start

```bash
pip install --pre "cdebt[agent]"     # the agent extra lets your coding agent write the questions
cd your-repo
cdebt init                           # pre-push hook + .cdebt/policy.toml + GitHub workflow (asks first)
git switch main && git add .github .cdebt && git commit -m "Add cdebt" && git push   # the workflow must be on main
cdebt agent connect --name claude_code --yes   # optional: needs the `claude` CLI, logged in
cdebt map                            # where is the AI-written code nobody has understood?
git push                             # on a feature branch: the hook quizzes you before the push
```

## How it works

1. **You push.** The pre-push hook finds the AI-written functions that nobody has understood yet
   (attribution from Git AI line notes, or commit trailers as a labelled fallback).
2. **Your coding agent writes the questions**, or the built-in generator does. The agent never
   sees an answer.
3. **cdebt runs every question in a sandbox** to get the true answer and grades yours by a plain
   rule. No AI grades you. Code that touches files, the network, shell commands, the clock or
   randomness runs against a fake world, so nothing real is contacted, spawned or written.
   Questions it cannot answer by running the code are thrown away, never guessed.
4. **The result travels with the push** as a git note, bound to a hash of that exact code. Change
   the code and the understanding expires.
5. **On the pull request**, the GitHub Action re-checks every recorded answer in CI and posts one
   comment: understood or NOT UNDERSTOOD per function, naming no one. `/cdebt recheck` re-runs it.

## Quiz rules

- At most 6 questions per push, 3 per function, biggest function first [src: src/cdebt/adaptive.py].
  A function the push does not reach stays NOT UNDERSTOOD; `cdebt quiz --pr <number>` asks only
  what is left.
- 2 of 3 right passes [src: src/cdebt/adaptive.py], so one wrong answer is fine, and a hard
  question must be among the right ones. A failing score gets no follow-up.
- **The timing check.** Someone who pastes every question into an AI answers easy and hard
  questions at the same speed; a person who is thinking slows down on the hard ones. When the hard
  answers come about as fast as the easy ones, cdebt asks 1 or 2 short follow-ups on changed values
  of the same code, with a countdown of 75% of that person's own median answer time, between 5 and
  15 seconds [src: src/cdebt/adaptive.py]. Right and in time: understood. Wrong or too slow: NOT
  UNDERSTOOD. This raises the effort of pasting into an AI; it does not prove anything, and timing
  is never stored or reported about anyone.

**What makes a question.** A proposal is kept only if running it gives a stable answer twice.
It is refused when the answer appears in the question, when the value comes from a hash or a
random draw, when the call fails before the function even runs, or when the result is an object
with no stable printed form. The wrong options are believable: near misses of the real answer
(one segment dropped, a number off by one, a list one element longer) and other values the
function itself can return, with lengths mixed so that picking the longest or shortest option
does no better than a blind guess. Each function gets at most one True/False question
[src: src/cdebt/agents/probes.py, src/cdebt/agents/mcp_server.py].

## Status (read this before using it)

Experimental pre-release (release candidate). Not validated: read this section before relying on it.

- **The debt score is unvalidated.** The two backtests were mostly null and test only a proxy (see
  below). Nothing here shows that a high score means real trouble.
- **The quizzes are unvalidated.** A study to test whether they separate people who know the code
  from people who don't is written and pre-registered ([docs/STUDY_PROTOCOL.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/STUDY_PROTOCOL.md)) and
  **has not been run**.
- **Most clearing will be NON-VERIFIED attestation, not quizzing**: on two of the three pilot repos
  most AI lines in the top files could only be attested, not verified (details below).
- **The gate is advisory by default**; it only fails a build with `--enforce`.
- **Windows is unsupported** for anything that executes code (`quiz`, `attest`, `coverage`,
  `study init`); those commands exit with a message pointing to WSL2. The Windows CI job covers the
  rest; see [docs/V1_CHECKLIST.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/V1_CHECKLIST.md) for its current status.
- Reviewed only by its author and an AI, not independently; see [SECURITY.md](https://github.com/Adityakk9031/cdebt/blob/main/SECURITY.md).
- **Newest features, lightly tested:** questions proposed by your own coding agent, the adaptive quiz
  and the pre-push flow were tested with a fake agent and real git, and the agent connection was
  run once end to end with the real Claude Code CLI (other agents are unverified). A record is made locally, so a determined
  person can forge one; CI re-execution catches wrong answers, not hand-written correct ones
  ([docs/LIMITS.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/LIMITS.md)).

## Commands

| command | what it does |
|---|---|
| `cdebt map` | Which files hold the most AI-written code no human has verifiably understood? |
| `cdebt quiz <file>` | Questions whose answers come from **executing** the code (function results, call graph, module constants) in Python and TypeScript/JavaScript (Node 24 or newer); records what is now verified |
| `cdebt attest <file> --note ...` | A human sign-off for code a quiz cannot cover. **NON-VERIFIED**, hash-bound, counts less (default 0.5 [src: src/cdebt/config.py]) |
| `cdebt coverage` | Before you trust the gate: how much of the top-N debt files can be verified, only attested, or not quizzed at all |
| `cdebt gate --base origin/main` | CI check on a PR whose AI-authored changed lines sit in un-understood code. **Advisory by default** (it reports and the build stays green); `--enforce` makes a failing verdict fail the build |
| `cdebt plan` | Ranked pay-down plan (quiz / review / rewrite-or-delete) with effort estimates |
| `cdebt backtest` | Does the (proxy) score predict later fixes and reverts on AIDev? Reproducible, published as measured |
| `cdebt index` | Build the SCIP index/indexes (Python and TypeScript) used for exact call-graph facts and blast radius |
| `cdebt study` | Validity-study kit: fixed question set, anonymous responses, team-level analysis (see [docs/STUDY_PROTOCOL.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/STUDY_PROTOCOL.md)); not yet run |
| `cdebt init` | Sets up the pre-push flow, asking before every change: a pre-push hook (never overwrites an existing one), `.cdebt/policy.toml`, a GitHub workflow, and optionally a GitHub ruleset that requires the `cdebt / gate` check |
| `cdebt quiz --pr N` | Take the quiz for an existing PR, push the notes, and ask CI to re-check |
| `cdebt agent connect` | Opt in to letting your own coding agent propose the quiz questions (cdebt still runs every question to get the answer) |
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
   ([docs/RESULTS.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/RESULTS.md)).

## What we know and don't know

**Know (tested; evidence in [docs/VERIFICATION.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/VERIFICATION.md) and [docs/V1_CHECKLIST.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/V1_CHECKLIST.md)):**
the tool runs end to end on real repos; the Git AI notes parser handles real notes from
git-ai versions 1.0.0 through 1.7.5; the sandbox contains the escape payloads in its test and fuzz suites,
including a second adversarial pass that found and fixed real holes
([docs/THREAT_MODEL.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/THREAT_MODEL.md)); the GitHub Action posts and then edits a single PR
comment (see VERIFICATION.md for the run URLs).

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
the score works, and nothing shows it doesn't.** See [docs/RESULTS.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/RESULTS.md) and
[docs/RESULTS_MATCHED.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/RESULTS_MATCHED.md).

**Also know (from the pilot on three real repos, [docs/CASE_STUDIES.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/CASE_STUDIES.md)):**
quiz coverage is narrow and varies a lot by repo. On the 25 highest-debt files, the share of AI
lines a quiz could verify was 19% for one Python repo, 77% for another (with a call-graph index) and
33% for the TypeScript repo; the rest could only be attested (23%, 81% and 61%) or, for JSX files,
not covered at all (6%)
[src: docs/case_studies/space_chassis.coverage.json, docs/case_studies/agent-meta.coverage.json, docs/case_studies/hive.coverage-ts.json].
Re-measured on 2026-10-22, `space_chassis` gives 9% for the same settings [src: docs/case_studies/space_chassis.coverage.2026-10-22.json] (the top-25 selection moves with the date, so treat these as examples, not constants).
In the 12 highest-debt Python files of two of these repos, the built-in generator could quiz 1 of 565 AI-written functions and the real Claude Code agent added 40 (7.1%) [src: docs/case_studies/agent_benchmark.summary.json]; 55 of its 130 proposals were verified by execution, and all 22 answers that could be compared agreed with a second sandbox path ([docs/AGENT_BENCHMARK.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/AGENT_BENCHMARK.md)).
**Sandbox reach on three third-party Python repos (one run each, Claude Code agent plus the fake
world, measured 2026-10-28 to 2026-10-30, not a benchmark):** about 85% of 2,298 functions got at
least one execution-verified question: inspect-robots 458 of 481 (95%), xai-sdk 356 of 363
(98%), AMFS 1,133 of 1,454 (78%, an older run before the latest sandbox fixes; a partial rerun of
4 AMFS files after the fixes gave 412 of 418). Three or more questions (what a quiz needs): 49%,
41% and 36%. What still fails: code that needs a real database, network or a running event loop,
and functions the agent kept calling with the wrong arguments. GPU and hardware code was not
targeted. TypeScript reach is much lower (about 5% on one repo). Details:
[docs/AGENT_BENCHMARK.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/AGENT_BENCHMARK.md).
The built-in generator alone (no agent) still needs typed, side-effect-free functions and, for TypeScript, modules plain Node can
resolve; call-graph probes need a SCIP index. Run `cdebt coverage` before trusting any gate.

## How a team uses it

1. Someone runs `cdebt init` once and commits the workflow and policy files.
2. A developer's coding agent writes code. On `git push`, the pre-push hook finds AI-written lines
   that nobody has understood and asks questions on the terminal, following the
   [quiz rules](#quiz-rules); every answer is checked by running the code in a sandbox. The records
   go to `refs/notes/cdebt` and are pushed before the branch. With no terminal (an agent pushing on
   its own) the hook prints a notice and lets the push through.
3. On the pull request the Action re-runs the recorded questions, posts one comment that marks
   CODE as understood or not (never a person), and sets a `cdebt / gate` check that is `neutral`
   in advisory mode. `git push --no-verify` skips the hook, and CI then shows the code as not
   understood. To block merges, make `cdebt / gate` a required check and turn `enforce` on.
4. Anyone can run `cdebt quiz --pr 123` and comment `/cdebt recheck` to clear it.

## A first-user walkthrough you can follow or record

`examples/first_user/` builds a small demo project with an AI-written change and walks through
install, setup, the quiz on `git push`, and the gate:
[DEMO_SCRIPT.md](https://github.com/Adityakk9031/cdebt/blob/main/examples/first_user/DEMO_SCRIPT.md) and `setup_demo.sh`.

## 60-second demo

```bash
# Python 3.11+. Only pre-releases exist so far, so plain pip installs the newest one; once a
# stable version exists, add --pre to get pre-releases:
pip install --pre "cdebt[agent]"          # from PyPI
# or straight from GitHub:
pip install "cdebt[agent] @ git+https://github.com/Adityakk9031/cdebt"
bash examples/demo.sh        # builds a throwaway repo and walks map -> quiz -> map -> gate
```

What it shows (real output, see `examples/demo.sh`):

```
cdebt map     ... AI lines verified: 0%   | AI units verified: 0/3
cdebt quiz    ... total: 2/2 -> UNDERSTOOD (bound to hash ccffd334cb)
cdebt map     ... AI lines verified: 67%  | AI units verified: 2/3
(edit total() and commit)
cdebt gate    ... FAIL (advisory; use --enforce to fail the build)   pkg/shop.py::total  1 AI lines NOT UNDERSTOOD (code_changed) [trailer]
cdebt gate --enforce  ... same verdict, non-zero exit status
```

Interactive use:

```bash
cdebt map                              # table or --json
cdebt quiz src/billing.py --n 5        # answer in the terminal
git add .cdebt/understanding.json && git commit -m "record understanding"
cdebt coverage                         # what can a quiz actually clear here?
cdebt attest src/settings.py --note "read; constants match the deploy config"
cdebt gate --base origin/main --strict-unknown   # advisory; add --enforce to block
cdebt plan --top 10
```

For non-interactive use: `cdebt quiz FILE --emit q.json` writes the questions (no answers),
then `cdebt quiz FILE --answers answers.json`.

## Connect your coding agent (optional)

By default cdebt writes the quiz questions itself (the built-in generator). You can let your own
coding agent write them instead: it reads only the code being quizzed and proposes questions;
cdebt runs every proposal in the sandbox to get the true answer, keeps only those it can verify,
and never shows the agent an answer. The agent never grades you.

The code for this ships in every release on PyPI since 1.1.0rc1 [src: CHANGELOG.md]. You need three things:

1. **The `agent` extra** (it adds the `mcp` library the connection uses). A plain
   `pip install cdebt` gives built-in questions only.

   ```bash
   pip install --pre "cdebt[agent]"
   ```

2. **The agent's own CLI, installed and logged in.** For Claude Code that is the terminal
   `claude` command (the Claude desktop app alone is not enough: cdebt has to start the agent from
   the terminal during your push).

   ```bash
   npm install -g @anthropic-ai/claude-code
   claude            # once: type /login, finish in the browser, then /exit
   ```

   If npm says `EACCES: permission denied`, install into your home folder instead of using sudo:

   ```bash
   npm install -g --prefix ~/.npm-global @anthropic-ai/claude-code
   echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> ~/.zshrc && export PATH="$HOME/.npm-global/bin:$PATH"
   ```

3. **Connect it** (saved only in your user config, `~/.config/cdebt/config.toml`, never in the
   repository):

   ```bash
   cdebt agent connect --name claude_code --yes
   cdebt agent status        # shows the connected agent
   cdebt agent disconnect    # back to the built-in generator
   ```

After a quiz, cdebt prints how many questions the agent proposed and how many it verified.
Expect a push to take longer (the agent is called each time) and it uses your agent plan's usage;
a cheaper default model in the agent's own settings keeps that down. Only `claude_code` has been
run against a real CLI; `codex` and `cursor` are UNVERIFIED.

If `cdebt agent status` lists `claude_code` but `claude --version` fails in your terminal,
install the CLI as above (fixed in 1.1.0rc10 [src: CHANGELOG.md]: only a `claude` that runs counts).

## Attribution setup (optional but recommended)

Without Git AI, cdebt uses commit trailers (coarse) and reports everything else as unknown.
For line-level attribution install Git AI per its docs. Git AI 1.7.x records through a daemon
that needs git trace2 configuration; see its docs (`git-ai debug` shows what is missing).
cdebt only reads the notes; it never writes to your repo except `.cdebt/`.

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: {fetch-depth: 0}
- uses: Adityakk9031/cdebt@v1     # the stable ref exists after the first release
  with: {max-ai-lines: "0", strict-unknown: "false", enforce: "false"}   # advisory by default
```

It runs `cdebt gate`, fetches `refs/notes/ai` best-effort, writes a job summary and a sticky PR
comment. It is advisory unless you set `enforce: "true"`.

**Commit the workflow to your default branch (`main`).** `cdebt init` writes
`.github/workflows/cdebt.yml`; it must be on `main`, not only on a feature branch. Pull-request
runs work from either, but GitHub runs the `/cdebt recheck` comment trigger only from the default
branch's workflow, so on a feature branch alone a recheck silently does nothing. `cdebt init`
prints the exact commands when you run it on another branch. A recheck edits the sticky comment
in place and replies "re-checked: ..." under your `/cdebt recheck` comment. See [docs/VERIFICATION.md](https://github.com/Adityakk9031/cdebt/blob/main/docs/VERIFICATION.md) for what was tested on a real PR.

## Safety model

`cdebt quiz`, `coverage`, `attest` and `study init` **run code from the repository you point them
at**. Read the [threat model](https://github.com/Adityakk9031/cdebt/blob/main/docs/THREAT_MODEL.md) first.

- Repositories are **untrusted by default**: probes run in Docker (image already pulled) or, on
  macOS, under a strict Seatbelt profile (no network, no writes outside a temp dir, cannot read
  your home directory, cannot signal other processes). If neither is available cdebt refuses and
  says why. `--trusted` or `CDEBT_TRUSTED=1`, set by the person running cdebt, switches to the host
  sandbox; trust is never read from a file in the repository.
- Code with file, network, shell, time or randomness side effects runs against a fake world:
  HTTP and shell commands are answered by fakes, the clock and random numbers are fixed, writes go
  to a scratch copy, files outside the repository do not exist and writes outside it fail as on a
  read-only disk. Mutation probes (which run a project's own tests) need `--trusted`.
- The sandbox targets accidental and AI-written side effects, not a determined attacker with
  native-code exploits. On Windows, anything that executes code is unsupported and says so.
- Your coding agent (if you connect one) only sees the code of functions being quizzed and two
  tools; it is never told an answer, and its other tools are limited to read-only. The agent choice
  lives in your user config, never in the repository.
- Read-only on your repo except `.cdebt/` (understanding records, policy, and a git-ignored result
  cache), the git ref `refs/notes/cdebt`, and the pre-push hook that `cdebt init` installs only
  with your explicit consent; other git calls are allow-listed read commands. No API key is needed. Network is needed
  only to build SCIP indexes (`cdebt index`, via `npx`) and to download the backtest dataset.

## Development

```bash
uv sync --extra backtest
uv run pytest -q && uv run ruff check . && uv run mypy src
bash scripts/make_gitai_fixtures.sh   # regenerate real Git AI fixtures (needs git-ai)
```

Docs: [PLAN](https://github.com/Adityakk9031/cdebt/blob/main/docs/PLAN.md) · [METHOD](https://github.com/Adityakk9031/cdebt/blob/main/docs/METHOD.md) · [LIMITS](https://github.com/Adityakk9031/cdebt/blob/main/docs/LIMITS.md) ·
[RESULTS](https://github.com/Adityakk9031/cdebt/blob/main/docs/RESULTS.md)

License: Apache-2.0.
