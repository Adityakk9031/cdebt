# Pre-registration: Analysis 2, within-repo matched pairs (AIDev)

Status: written and committed **before** any matched-pairs code exists or has been run.
Analysis 1 (the proxy score, repo-stratified) is pre-registered in `docs/METHOD.md` and its
results are in `docs/RESULTS.md`. This document adds a second, narrower analysis. It does not
replace Analysis 1 and does not change its definitions.

## Question

Analysis 1 was mostly null and its proxy mixed several things. The narrower question here is:

> Among agent-authored PRs **in the same repo**, of the same agent and similar size and merge
> date, are PRs that merged **with no human review evidence** followed by a bug-fix PR more
> often than PRs that merged **with substantive human review**?

This is a test of the *understanding proxy* only (review evidence). It is not a test of the
score formula, blast radius, AI share, or the quiz.

## Data and unit

Same data as Analysis 1: AIDev `hao-li/AIDev` revision `7840b6e61a5828461cd96806c52c20194ef57979`
(tables `pull_request`, `pr_reviews`, `pr_review_comments`, `pr_commits`, `pr_commit_details`).
Unit = **merged PR** (not PR x file, to avoid counting one PR many times), restricted to PRs that
touch at least one code file (same extension list as Analysis 1). A PR is **eligible** iff
`merged_at <= max(merged_at) - 90 days`.

Per-PR variables (all computed with the frozen Analysis 1 rules, aggregated to PR level):

- `U_pr` = maximum over the PR's code files of the Analysis 1 `U` (human review evidence;
  reviewer is not the PR author and not a bot; file-level review comment = 1.0, review with body
  >= 20 chars = 0.5, bare review = 0.25, none = 0).
- `additions_pr` = sum of additions over the PR's code files.
- `FIX90_pr` = 1 if any (PR, file) unit of the PR has the Analysis 1 FIX90 outcome.
- `FIX30_pr` analogously. `agent`, `repo_id`, `merged_at` from `pull_request`.

## Exposure groups

- **UNREVIEWED**: `U_pr == 0`.
- **REVIEWED**: `U_pr >= 0.5` (a review comment on a file, or a human review with a substantive
  body). PRs with `U_pr == 0.25` (bare approvals) are excluded from this analysis.

## Repo restriction

Only repos with **at least N = 20** eligible merged PRs (from the whole curated set, any
exposure) that also contain at least one PR in each exposure group. Sensitivity runs, fixed now:
N = 10 and N = 50. No other N may be reported as primary.

## Matching (fixed algorithm, no outcome information used)

Within each repo, pair each UNREVIEWED PR with at most one REVIEWED PR such that:

1. same `agent`;
2. same size bin, where bins are the global terciles of `additions_pr` over eligible PRs
   (bin edges computed once on all eligible PRs, before matching);
3. `|merged_at difference| <= 30 days`.

Greedy nearest-in-time matching without replacement, processing UNREVIEWED PRs in order of
`(merged_at, pr_id)`; ties broken by smaller PR id. Unmatched PRs are dropped. The number of
pairs, and how many PRs were dropped, are reported.

## Hypotheses and statistics

Outcome pair table: `b` = pairs where only the UNREVIEWED PR has FIX90; `c` = pairs where only
the REVIEWED PR does; concordant pairs are ignored for the ratio.

- **H1 (primary)**: P(FIX90 | UNREVIEWED) > P(FIX90 | REVIEWED) within matched pairs.
  Statistic: conditional (matched-pairs) odds ratio `b / c`, and the paired risk difference
  `(b - c) / n_pairs`. 95% CI: bootstrap resampling **repos** (2000 resamples, seed 0),
  percentile interval. Supported only if the CI of the odds ratio excludes 1 from above
  (lower bound > 1).
- **H2 (secondary)**: same with FIX30.
- **H3 (secondary, direction check)**: the same statistic with the exposure labels swapped is
  just the reciprocal and is not tested separately; instead report the exact McNemar two-sided
  p-value for `b` vs `c` as descriptive information, flagged as not corrected for clustering.
- **Dose check (secondary)**: repeat H1 with REVIEWED redefined as `U_pr == 1.0` only.

If fewer than 10 discordant pairs exist in the primary analysis, the result is reported as
"inconclusive (<10 discordant pairs)". This rule is fixed now.

## What a positive or negative result would and would not mean

- A positive H1 would show only that, within repo/agent/size/time, unreviewed AI PRs are
  followed by dataset-visible fixes more often. It would not show that review *causes* fewer
  fixes.
- **Confounding by indication** can push the other way: humans may review the PRs they find
  risky, and risky PRs get fixed more. A null or reversed result is therefore not evidence that
  understanding does not matter.
- Outcomes are visible only when the follow-up fix is itself an agent PR in the curated set, so
  FIX rates are undercounted. Matching within repo and agent reduces, but does not remove,
  differences in how visible fixes are.
- Review evidence is a weak proxy for understanding (a rubber-stamp comment counts the same as
  a careful one).
- Reverts are not analysed (2 events in Analysis 1, no power).

## Reporting

`docs/RESULTS_MATCHED.md` and `docs/RESULTS_MATCHED.json`, generated by
`cdebt backtest --matched` with the settings above, exactly as measured. Any deviation from this
document (including bug fixes made after the first run) must be listed in that report with the
reason and whether it was decided after seeing results.
