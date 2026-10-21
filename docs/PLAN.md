# cdebt v1 — build plan

Status: **M0 draft, awaiting approval.** Nothing below is implemented yet.

`cdebt` measures, tests and pays down *comprehension debt*: AI-written code that no
human has verifiably understood. This plan covers what gets built, in what order, how
each piece is verified, and where I want a decision from you before diverging from
the spec.

---

## 0. Environment found on this machine (2026-10-17)

| Tool | State | Consequence |
|---|---|---|
| python3 | 3.14.3 (no 3.11–3.13) | Use `uv` to pin a 3.11 interpreter for dev; CI matrix 3.11 / 3.12 / 3.13 |
| uv | 0.12.2 | Packaging + lockfile |
| git | 2.39.5 | Fine for read-only plumbing |
| git-ai | **missing** | Must install in M1 (see Q2) |
| docker | **missing** | Sandbox must work without Docker on macOS (see §6) |
| node / npx | 24.18 / 11.16 | Enough to run `scip-python`, `scip-typescript`, Stryker |
| scip-python, scip-typescript, mutmut | missing | Installed in M2/M3; all optional at runtime |

The working directory is empty and **not a git repo**. I will `git init` at the start of M1.

---

## 1. Core concepts and data model (`models.py`)

All models are Pydantic v2, frozen where possible, JSON-serialisable.

- **`AttributionSource`** = `git-ai | agent-trace | trailer | none`. `none` means
  *unknown*, never "human".
- **`LineAttribution`**: `path, line, author_kind (ai|human|unknown), source,
  agent?, model?, commit`. Stored as run-length ranges internally, not per line.
- **`FileAttribution`**: per-file ranges + `source` + counts (`ai_lines`,
  `human_lines`, `unknown_lines`).
- **`CodeUnit`**: the thing understanding attaches to. Default granularity is a
  **top-level symbol** (function/class/method, via tree-sitter), with a whole-file
  unit as fallback for unparseable files. `content_hash` = sha256 of the unit's
  normalised source (whitespace-trailing stripped, line endings unified; comments
  kept — see Q5).
- **`Probe`**: `id, kind (runtime|callgraph|mutation), unit, question, options?,
  answer, verified_by (sandbox-exec|scip-index|scip-approx|mutation-run),
  evidence` (stdout/exit code/index path/killing test id), `seed`.
- **`Record`** (understanding record): `unit_path, unit_symbol, content_hash,
  probes_asked, probes_correct, passed, created_at, expires_at?, audit_label`.
  `audit_label` is free text (default: none; opt-in `--label`). It is stored for
  audit only and **never aggregated or reported per person**.
- **`FileDebt`**: `path, ai_lines, understood_ai_lines, ai_share, understood_share,
  churn, blast_radius, blast_source (scip|approx|none), score, attribution_source`.
- **`GateResult`**: `passed, violations[], unknown_files[], strict_unknown,
  base, head, attribution_sources_used`.

### Expiry (differentiator 3)

A record is **valid** iff the unit's *current* content hash equals the record's
`content_hash` (and, optionally, `expires_at` has not passed — a configurable max
age, default off). Any edit to the unit invalidates its record. Records are never
deleted on expiry; they are reported as `stale` so history stays auditable.
Symbol-level hashing means editing one function doesn't wipe understanding of the
whole file (see Q5 — this is a refinement of the spec's "content hash of the code
it covers").

Store: `.cdebt/understanding.json` (schema-versioned, sorted keys, stable diffs so
it can be committed and reviewed). Writes are atomic (temp file + rename).
`.cdebt/` is the **only** place cdebt writes inside the user's repo.

---

## 2. Attribution (differentiator 2) — `attribution/`

Priority, highest first, per file:

1. **`gitai.py`** — Git AI installed as an external tool, never vendored.
   Reads `refs/notes/ai` via `git notes --ref=ai show <commit>` /
   `git log --notes=ai` (read-only) and `git-ai stats --json`.
   Line-level blame of AI ranges onto `HEAD` is done by mapping note ranges through
   `git blame --porcelain` (so a line is AI iff the commit that last touched it has a
   note marking that line range AI).
   **UNVERIFIED:** my expectation is that notes hold a per-file list of line ranges
   keyed to a prompt/session id, followed by a JSON metadata block (agent, model,
   human author). I will not write the parser until M1 has produced real notes in a
   scratch repo and committed them to `tests/fixtures/gitai/`. If the real format
   differs I'll stop and tell you.
2. **`agent_trace.py`** — Agent Trace records (JSON trace files describing
   files/ranges and contributor type). **UNVERIFIED:** exact location and schema;
   I'll fetch the published spec in M1, pin the version I target, and build fixtures
   from the spec's own examples.
3. **`trailer.py`** — fallback heuristic: commits with `Co-Authored-By:` trailers
   naming known agents (Claude, Copilot, Cursor, Codex, Devin, …; list is
   configurable). All lines last-touched by such commits are `ai`, labelled
   `trailer` and flagged as *coarse* (commit-level, not line-level).
4. **`none`** — no evidence. Lines are `unknown`.

`resolve.py` picks the highest-priority source that has data **for that file**
and labels every output with it. Mixed sources across a repo are allowed and shown.
Tests: one throwaway repo per source + a `none` repo, all built inside pytest
(`tmp_path`, `git init`, no network).

---

## 3. Map — `mapper.py`, `cdebt map`

Spec formula, kept as the default:

```
score = ai_share × (1 − understood_share) × (1 + w_churn · churn) × blast_radius
```

- `ai_share` = ai_lines / total_lines (unknown lines are **excluded** from the
  numerator and reported separately; `--unknown-as-ai` flag for a worst-case view).
- `understood_share` = understood_ai_lines / ai_lines, where an AI line is
  understood if it lies in a unit with a valid record.
- `churn` = commits touching the file in a window (default 90 days), normalised to
  [0,1] by repo max. `w_churn` default 1.0.
- `blast_radius` from SCIP: number of distinct external referencing sites to symbols
  defined in the file. **Proposed tweak (Q4):** use `1 + log1p(refs)` instead of a
  raw count so one hub file doesn't dwarf everything. Without a SCIP index we fall
  back to a tree-sitter name-match count, labelled `approx`; with neither, 1.0
  labelled `none`.

All weights live in `[tool.cdebt]` in `pyproject.toml` or `.cdebt/config.toml`.
Output: Rich table + `--json`; summary line gives repo AI share, % of AI lines
understood (with Wilson 95% CI over units, n shown), and the attribution sources
used. Formula and caveats go in `docs/METHOD.md`.

Performance target: map of a 50k-LOC repo in < 30 s without SCIP (blame is the
bottleneck; cached in `.cdebt/cache/` keyed by HEAD sha).

---

## 4. Probes and quiz (differentiator 1) — `probes/`, `sandbox.py`

Every probe's correct answer is **computed**, never judged. Questions are
multiple-choice or exact-value; the user's answer is compared by a deterministic
rule (normalised `repr` equality, set equality for "what calls X"). No LLM grades
anything. (Superseded in v1.1, see docs/METHOD.md "Agent-proposed questions".) An optional model may *reword* question text only, behind a flag, and
the stored probe keeps the canonical wording.

- **Runtime** (`runtime.py`, Python first): pick a function with type hints, generate
  arguments from hints (ints/strs/lists/dicts/dataclasses/Optional, small seeded
  values), execute in the sandbox, ask "what does `f(…)` return?" with the real value
  plus 3 distractors produced by mutating the real value (not by a model). Functions
  whose AST touches I/O, network, time, randomness, subprocess, env, or globals
  mutation are **skipped** with a reason.
- **Call graph** (`callgraph.py`): "which of these call `X`?" / "which files break if
  `Y`'s signature changes?" answered from a SCIP index (`scip-python`,
  `scip-typescript`, run via `npx`). Index read via the `scip` CLI's JSON print or
  protobuf (see Q3). Approximate tree-sitter fallback is allowed for the *map* but
  **not for quiz answers** — if there's no SCIP index, call-graph probes are skipped,
  because an approximate answer can't be the ground truth.
- **Mutation** (`mutation.py`): "if line N changes from `<` to `<=`, which test
  fails?" answered by applying the mutation **in a temp copy of the repo** and running
  the tests in the sandbox. See Q6 on mutmut vs a targeted in-house mutator.
- `cdebt quiz <file> [--n 5] [--seed S] [--label TEXT] [--json]`: generates probes
  across units in the file, asks interactively (or `--answers answers.json` for
  scripted use), and writes one `Record` per unit bound to its current hash. Pass
  threshold default: all probes for a unit correct, min 2 probes per unit
  (configurable).

### Sandbox (`sandbox.py`)

Layered, because Docker isn't guaranteed:

1. **Static screen**: refuse targets whose AST imports/calls `os`, `subprocess`,
   `socket`, `shutil`, `ctypes`, `open(..., 'w')`, `eval/exec`, `time`, `random`,
   etc. (deny-list + allow-list of safe stdlib modules).
2. **Child process**: `python -I -S`-style isolated interpreter, fresh temp cwd,
   scrubbed env, hard wall-clock timeout, `resource` limits (CPU, file size,
   open files, NPROC to stop fork bombs; address-space limit where the OS honours it).
3. **In-child audit hook** (`sys.addaudithook`) that kills the process on
   `open` for write, `socket.*`, `subprocess.Popen`, `os.system`, `os.fork`, etc.
4. **OS network isolation** when available: Docker (`--network none`) if present;
   Linux `unshare -n` / bubblewrap; macOS `sandbox-exec` deny-network profile.
   If none is available, network is still blocked by layer 3, and the result is
   labelled with the isolation level actually achieved.

Escape tests (release checklist): file write, network connect, fork bomb, infinite
loop, memory bomb, `ctypes`, `__import__('os').system` — each must be contained and
reported, and the tests run on macOS and Linux CI.

---

## 5. Gate and plan — `gate.py`, `plan.py`

- `cdebt gate --base origin/main [--head HEAD] [--max-ai-lines 0]
  [--strict-unknown] [--json] [--markdown out.md]`
  - Changed lines from `git diff --unified=0 base...head`.
  - Violation: an AI-attributed changed line inside a unit without a valid record.
    Fail if violating AI lines > `--max-ai-lines`.
  - Unknown-attribution changed lines: listed as a warning, **not** a failure,
    unless `--strict-unknown`.
  - Exit 0 pass / 1 fail / 2 tool error.
  - Markdown output for a PR comment: totals, files, attribution sources, what to
    run (`cdebt quiz <file>`). No person names.
- `action.yml`: composite action — set up Python, `pip install cdebt`, fetch notes
  (`git fetch origin refs/notes/ai:refs/notes/ai`, best-effort), run gate, post/update
  one sticky PR comment via `gh`/`GITHUB_TOKEN`.
- `cdebt plan [--top 20] [--json]`: rank by `score / effort`. Effort =
  estimated minutes to quiz-pass the unexplained AI units, a configurable heuristic
  (`lines / reading_rate + per-unit overhead`, defaults documented as guesses, not
  measurements). Each item gets a suggested action: `quiz` (small, well-tested),
  `review` (large, test-poor), `rewrite/delete` candidate (high score, no callers
  per SCIP).

---

## 6. Backtest (differentiator 4) — `backtest/`

Data: **AIDev** (MSR 2026 mining challenge; agent-authored PRs from Codex, Devin,
Copilot, Cursor, Claude Code on GitHub). **UNVERIFIED:** exact hosting, table names
and fields — I'll read the dataset card in M5 and document what I find before
coding against it. Download is a script (`scripts/fetch_aidev.sh` or
`python -m cdebt.backtest.fetch`), never part of tests; tests use a tiny
hand-built fixture with the same schema.

**Honest methodological caveat up front:** historical repos have no cdebt
understanding records, so the backtest cannot test the *full* score. It tests a
**proxy debt score** computed at PR-merge time:

- AI share: 1.0 for agent-authored PR lines (that's what AIDev is).
- Understanding proxy: review evidence on the PR (substantive review comments
  touching the file, requested changes, non-author commits) vs. bare approval or
  none. Defined before looking at outcomes and frozen in `docs/METHOD.md`.
- Blast radius: from reference counts at merge commit, on a sampled subset of repos
  (cloning thousands of repos is not feasible on one machine); sample size stated.

Outcomes per merged PR/file (windows pre-registered, e.g. 30/90 days):
- revert of the PR or its commits,
- later bug-fix commit touching the same lines (SZZ-lite: fix keyword + blame back),
- time-to-fix for issues linked to those files.

Analysis: quantile bins of proxy score; risk ratios / odds ratios with Wilson and
bootstrap 95% CIs; rank correlation; a logistic model controlling for PR size and
repo (cluster-robust by repo). Pre-register hypotheses and analysis in
`docs/METHOD.md` **before** running on the full data. `docs/RESULTS.md` reports
whatever comes out, including null or negative results, with n everywhere.
One command reproduces: `cdebt backtest --data <dir> --out docs/RESULTS.md --seed 0`.

---

## 7. Milestones, exit criteria

Every milestone ends with `pytest -q && ruff check . && mypy src` green, the
commands I ran and their real output, a list of anything `UNVERIFIED`, and a stop
for your review.

| M | Deliverables | Exit criteria |
|---|---|---|
| **M0** | `docs/PLAN.md`, `CLAUDE.md` | Your approval + answers to §8 |
| **M1** | `git init`; `pyproject.toml` (uv, entry point `cdebt`), `models.py`, `gitutil.py`, `store.py` (hash-bound expiry), `attribution/*`; Git AI installed, real notes from a scratch repo committed to `tests/fixtures/gitai/`; CI workflow skeleton | Parser tests pass against **real** Git AI output; resolver tests for git-ai / agent-trace / trailer / none; expiry test (edit unit → record stale); store never writes outside `.cdebt/` (asserted by a test diffing the repo tree) |
| **M2** | `mapper.py`, SCIP runner + reader, `cdebt map`, `docs/METHOD.md` | Map on a fixture repo matches hand-computed scores; `--json` schema test; runs with and without SCIP, labels blast source |
| **M3** | `sandbox.py`, runtime / callgraph / mutation probes, `cdebt quiz` | Every probe test asserts the stored answer came from execution/index/mutation; sandbox escape suite passes on macOS + Linux; quiz writes hash-bound records |
| **M4** | `gate.py`, `plan.py`, `action.yml`, PR-comment markdown | Gate fixture: AI change in un-understood file → exit 1; after quiz → exit 0; unknown → warn, `--strict-unknown` → fail; Action verified on a real test PR in a throwaway GitHub repo (with your OK) |
| **M5** | AIDev loader + fetch script, metrics, report, `docs/RESULTS.md` | Pre-registration committed before the full run; reproducible by one command; numbers in RESULTS.md come only from that run |
| **M6** | README (60-s demo, "never ranks individuals", "not a lie detector"), `docs/LIMITS.md`, examples, v0.1.0 | Clean-venv `pip install` of the built wheel works; CI green; release checklist ticked with evidence |

---

## 8. Decisions I need from you before M1

**Q1. Python version.** Target 3.11+ as specified; develop on a uv-managed 3.11 and
test 3.11–3.13 in CI. 3.14 is what's on this machine — add it to the CI matrix too?

**Q2. Installing Git AI.** Its installer (per its README, to be re-checked) is a
`curl | sh` script that installs a binary and, I believe, wraps/shims `git` and
installs hooks so it can record attribution. That changes global git behaviour on
your machine. Options: (a) install globally as documented; (b) install the binary
only and use it in a scratch repo with a local `PATH` override. I'll show you the
exact install commands and wait for your go-ahead either way.

**Q3. Dependencies not in §4** (I won't add any without a yes):
- `tree-sitter-python`, `tree-sitter-typescript` (grammar packages; `tree-sitter`
  alone has no languages) — needed.
- Reading SCIP indexes: either the `scip` CLI binary (Go, no Python dep) or
  `protobuf` + generated `scip_pb2`. I prefer the **`scip` CLI** (no new Python dep).
- Backtest only, as an optional extra `cdebt[backtest]`: `pyarrow` (parquet) and
  `huggingface_hub` (download). Stats (Wilson, bootstrap, logistic regression) I'd
  implement on numpy — is **`numpy`** OK, or do you want `scipy`/`statsmodels`?

**Q4. Blast radius scaling.** Raw SCIP reference count (spec) or `1 + log1p(refs)`
(my recommendation)? Either way configurable.

**Q5. Understanding granularity.** Spec says "content hash of the code it covers".
I propose symbol-level units (a function edit expires only that function), with
file-level fallback. Comments included in the hash (safer: a changed docstring can
change meaning) — OK?

**Q6. Mutation engine.** mutmut 3.x is built to mutate whole projects and is awkward
to aim at one line for a single question. Options: (a) use mutmut as specified,
accept slower probes; (b) a small in-house AST mutator for the targeted question
(a few operators: comparison, arithmetic, boolean, constant), with mutmut used to
cross-check in tests. I recommend (b). For JS/TS, Stryker probes are heavy — OK to
make v1 **Python-only for runtime and mutation probes**, with TS supported in
attribution, map, gate and call-graph probes?

**Q7. Backtest proxy.** Are you OK with the backtest testing a *proxy* score (review
evidence standing in for understanding) and saying so prominently? I don't see an
honest way to test the full score on historical data.

**Q8. Store committed to git.** For the CI gate to see understanding records,
`.cdebt/understanding.json` must be committed on the PR branch. That makes records
self-asserted and forgeable — consistent with "visibility, not a lie detector".
Acceptable, with LIMITS.md saying so?
