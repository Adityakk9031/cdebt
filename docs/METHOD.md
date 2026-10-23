# Method

## Attribution (per line)

Priority per line, first claim wins: `git-ai` > `agent-trace` > `trailer` > `none`.
`none` means *no evidence* (unknown); it is never counted as human-written.

- **git-ai** (verified against git-ai 1.7.5, schema `authorship/3.0.0`): a note on each
  commit lists, per file, line ranges in that commit's version of the file, tagged `s_…::t_…`
  (AI session) or `h_…` (explicit human edit). We map HEAD lines through `git blame
  --porcelain` (blamed commit + original line number) and look the line up in that commit's
  note. Lines absent from a note are unknown.
- **agent-trace** (RFC v0.1.0; storage location UNVERIFIED, we read `.agent-trace/*.json[l]`).
- **trailer**: `Co-Authored-By:` naming a known agent. Commit-level, therefore *coarse*
  (marked `*`): human lines in the same commit are over-claimed.

## Understanding

A record binds to `sha256(normalised unit text)`. Units are functions/methods (tree-sitter,
Python and TS/JS) plus one `<module>` residual per file; other languages are one `<file>` unit.
Normalisation: CRLF to LF, trailing whitespace stripped, trailing blank lines dropped.
Comments are included. Any change to a unit's text makes its records *stale*. Optional
`expires_at` adds time-based expiry. An AI line is "understood" iff its unit has a passing,
hash-matching, unexpired record.

### Verified vs attested

A record is `verified` (passing probes whose answers were computed by execution, a SCIP index or
a mutation run) or `attested` (a human signed off with `cdebt attest --note`; nothing is
executed). Both are bound to the unit's content hash and expire when it changes. An attested
record also stores `scope`, decided at attest time: `module` (module-level unit), `function-
unquizzable` (no probe could be generated for it) or `function-quizzable` (only with `--force`).
`understood_share = (verified_lines + attested_weight x attested_lines) / AI_lines`, with
`attested_weight` = 0.5 by default (configurable, 0..1; 0 ignores attestation in the score).
Gate policy: `--require verified` (attestation never counts), `--require attested` (it counts
everywhere), default (it counts for `module` and `function-unquizzable` scopes only).

`constant` probes ask "what is the value of NAME?" for top-level `NAME = expr` whose
right-hand side does not touch `os`, `sys`, `time`, `random`, `pathlib`, etc.; the module is
imported (and reloaded) in the sandbox and the repr of the value is the answer. They verify
constant values only.

## Agent-proposed questions (v1.1)

The developer's own coding agent (Claude Code, Codex CLI, Cursor agent, any command) may
**propose** questions, call inputs and test harnesses through the `cdebt mcp` server, with the
developer's explicit opt-in (`cdebt agent connect`). Rules that keep correctness deterministic:

- cdebt **executes every proposal in the sandbox** and takes the true answer from that execution
  (or from a trace, SCIP call graph or mutation run). The agent is **never shown the answer**.
- A proposal is **discarded** if it cannot be executed, is non-deterministic (two runs differ), or
  if an execution trace shows the real target's own lines did not run (a harness that tests a mock).
- An LLM never decides correctness and never grades free text.
- Each stored probe records its proposer (`agent:<name>` or `builtin`), the harness hash and how
  the answer was verified.

## Adaptive quiz (v1.1)

Questions are labelled easy, medium or hard from their kind and code complexity. Each function
gets a fixed set of 3 to 5 questions (default 4) mixing the tiers, asked easiest first. A wrong
answer never adds a question. The function clears when at least ceil(0.6 x n) answers are right
(2 of 3, 3 of 4, 3 of 5; with 2 or fewer questions all must be right; the 0.6 is a policy choice,
UNVERIFIED as a predictor of understanding), and at least one hard question must be among the
correct ones (`require_hard`; waived only when the set contains no hard question).

If the score passes but the hard answers were NOT meaningfully slower than the easy and medium ones
(hard median under 1.3 x the median of the easy and medium answers), the person gets 1 or 2 random
follow-ups about the same code, chosen from: the same function on a changed input, whether a branch
ran (yes/no), what a variable held before a line. Each has a short answer (a number, yes/no, a short
value; 12 characters at most) so an honest person can type it in time, and each is answered by
tracing the real execution; none repeats a question already asked. The countdown is visible and set
from that person's OWN pace: 75% of their median answer time, never under 5 s and never over 15 s
(`follow_up_fraction`, `follow_up_min_seconds`, `follow_up_max_seconds`; a usual pace of 9-10 s
gives about 7 s). Right and in time on all of them: understood. Wrong or too slow: NOT UNDERSTOOD
(`follow_up_failed`, shown in the PR comment, naming no person). The choice among follow-ups is
unpredictable unless `CDEBT_FOLLOWUP_SEED` is set in the environment (for demos and tests). A
failing score gets no follow-up. Timing lives only in memory for the session and is never
stored; status reasons describe the code record, not a person. This raises the effort of copying
answers from an AI but does not prevent it (`docs/LIMITS.md`).

## Debt score (`cdebt map`)

```
score = ai_share x (1 - understood_share) x (1 + w_churn x churn) x blast_radius
```

| term | definition |
|---|---|
| `ai_share` | AI lines / total lines in the file (unknown lines are in the denominator, never the numerator, unless `unknown_as_ai`) |
| `understood_share` | understood AI lines / AI lines |
| `churn` | commits touching the file in `churn_days` (default 90), divided by the max over all files, so in [0, 1] |
| `blast_radius` | `log` mode (default): `1 + ln(1 + refs)`; `raw` mode: `max(1, refs)`. `refs` = distinct (file, line) reference sites outside the file to symbols defined in it |

`refs` comes from a SCIP index when present (`blast src = scip`). Otherwise a name-match
fallback is used and labelled `approx`; it over-counts common names and ignores names of
1-2 characters. The `log` default is a deliberate deviation from the raw-count spec so one hub
file cannot dwarf all others; set `blast_mode = "raw"` for the literal formula.

Weights live in `[tool.cdebt]` (pyproject.toml) or `.cdebt/config.toml`.

## Summary statistics

"AI units understood" is a proportion over units that contain AI lines; it carries a Wilson
95% interval and its sample size. The score is a *ranking heuristic*, not a probability.

---

# Backtest pre-registration (written and committed BEFORE the full run)

Data: AIDev (`hao-li/AIDev`, CC-BY-4.0). Tables verified 2026-10-17 via the Hugging Face
datasets-server `/info` endpoint: `pull_request` (71,677 curated agent-authored PRs, repos with
>100 stars; agents OpenAI_Codex, Cursor, Copilot, Devin, Claude_Code, Google_Jules),
`pr_reviews`, `pr_review_comments`, `pr_commits`, `pr_commit_details`. **Every PR in the dataset
is agent-authored; there is no human-PR comparison group and no issue/bug table.**

## What this backtest can and cannot test

The real score is `ai_share x (1-understood) x (1+w*churn) x blast`. Historical repos have no
cdebt records and we do not clone 6.7k repos, so we test a **proxy**:
`S = (1 - U) x (1 + C)`, with `ai_share = 1` (agent-authored by construction) and
`blast = 1` (NOT TESTED). So the backtest evaluates only the understanding-proxy and churn
terms. Passing it would not validate blast radius, AI share, or the quiz itself.

## Unit and eligibility

Unit = (merged PR, source file) from `pr_commit_details.filename`, restricted to code
extensions (.py .js .jsx .ts .tsx .java .go .rs .c .cc .cpp .h .cs .rb .php .kt .swift) and
`status != removed`. PR is merged iff `merged_at` is non-null. Eligible iff
`merged_at <= max(merged_at in data) - 90 days` (full 90-day follow-up).

## Proxy terms (frozen)

- `U` (understanding proxy, human review evidence before merge; reviewer is not the PR author
  and `user_type != Bot`): `1.0` if a human review comment exists on that file path; else
  `0.5` if a human PR-level review with body >= 20 chars exists; else `0.25` if any human review
  exists; else `0`.
- `C` (churn): number of OTHER dataset PRs in the same repo merged in the 90 days before this
  PR's merge that touched the same file, divided by the max such count among units in that repo
  (0 if the max is 0).

## Outcomes (within 90 days after merge, secondary 30; only dataset-visible)

- **FIX**: a different, later-merged PR in the same repo touches the same file and its title
  matches `\b(fix|fixes|fixed|bug|bugfix|hotfix|regression|crash|error)\b` (case-insens.) and
  does not start with "revert".
- **REVERT**: a later merged PR in the same repo whose title starts with "revert" (case-insens.)
  and whose body or title references the original (`#<number>`, its `html_url`, a commit sha
  from `pr_commits`, or its quoted title). Applies to all files of the reverted PR.
- **TTF**: days from merge to the first FIX (defined only where FIX occurred).

**Known biases (stated up front):** follow-ups are observable only when they are themselves
agent PRs in the curated set, so outcomes are heavily undercounted and depend on how much a
repo uses agents. Repo activity is therefore a confounder; all primary comparisons are
stratified by repo. File-level co-touching is not line-level SZZ.

## Hypotheses and analysis

Exposure: HIGH = `S >= q75`, LOW = `S <= q25` over eligible units (if q25 == q75, reported as
degenerate). All rates reported with Wilson 95% CIs and n.

- **H1 (primary)**: P(FIX within 90d) is higher for HIGH than LOW. Statistic: repo-stratified
  Mantel-Haenszel risk ratio; 95% CI by cluster bootstrap over repos (2000 resamples, seed 0).
- **H2**: same for REVERT within 90d.
- **H3**: TTF is *longer* for HIGH among FIX units. Statistic: Cliff's delta with cluster
  bootstrap CI, plus median days per group.
- **H4 (adjusted)**: odds ratio for HIGH from logistic regression of FIX90 on
  `[HIGH, log1p(additions), C, agent dummies]` (IRLS, numpy), cluster-bootstrap CI.
- **Component check**: FIX90 risk ratio for `U == 0` vs `U >= 0.5`, repo-stratified.
- Spearman correlation between `S` and the FIX90 indicator.

A hypothesis counts as supported only if the 95% CI excludes 1 (ratios) or 0 (delta) in the
predicted direction. Null and opposite-direction results are published as measured in
`docs/RESULTS.md`. No analysis choice above may be changed after seeing outcomes; any deviation
must be listed in RESULTS.md as a deviation.

---

## Mutation backend (decision, 2026-10-18)

cdebt's own single-token AST mutator (`probes/mutation.py`) is the **only** mutation backend.
mutmut 3 was tried on a toy project: it runs, but it reports only killed/survived per *its own*
mutant set and cannot answer "which tests fail when this specific line changes", which is the
question the probe asks. Cross-checking would need a mapping between mutant sets, so mutmut
was removed from the extras rather than shipped untested. Stryker (JS/TS) is likewise not
supported: mutation probes are Python-only.
