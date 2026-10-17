# Limits (read before relying on cdebt)

## What cdebt is not

- **Not a lie detector.** A quiz cannot stop someone pasting the questions into an AI,
  reading the answers off a colleague's screen, or guessing. A passing record means "someone
  answered questions about this code correctly, and the answers match what the code does",
  nothing more. cdebt provides *visibility*, not proof of understanding.
- **Never ranks individuals.** There are no leaderboards and no per-person scores. Records may
  carry a free-text `audit_label` (opt-in, `--label`) that is stored for audit only and is never
  aggregated, sorted or reported per person. Do not build such reports on top of it.
- **Not evidence that code is correct.** "Understood" and "bug-free" are different things.

## Attribution

- `none` means *unknown*. It is never "human-written". By default unknown lines are reported
  but not gated; `--strict-unknown` fails on them.
- `git-ai` is line-level but only as good as the capture at write time (agent hooks, or
  checkpoints). Lines edited outside any hooked tool are unknown or human, not "probably AI".
- `trailer` is commit-level and coarse: every line last touched by such a commit counts as AI,
  including human lines in that commit. It is marked with `*`.
- `agent-trace` storage location is not defined by the RFC (v0.1.0); we read
  `.agent-trace/*.json[l]` (UNVERIFIED against any real producer).
- Git AI 1.7.x records through a background daemon that needs trace2 git config. The notes
  parser is verified against real 1.7.5 output (`tests/fixtures/gitai/`). Legacy pre-3.0.0 note
  layouts are treated as AI (UNVERIFIED, no real sample).

## Understanding records

- Records live in `.cdebt/understanding.json`. They are committed by the person who ran the
  quiz, so they are **self-asserted and forgeable** (anyone can hand-edit the file or delete a
  failing record). The gate reads the file at the PR head; protect it with CODEOWNERS/branch
  protection if you need more than visibility.
- Granularity is the function/method (Python, TS/JS via tree-sitter). Module-level code and
  other languages are one `<module>`/`<file>` unit and cannot be quizzed (the plan marks them
  `review`). Any edit to a unit, including comments, expires its records.
- Units are matched by qualified name; renaming a function drops its records (they show up as
  stale/none).

## Probes

- Runtime probes need typed, pure-ish Python functions with simple argument types and
  literal return values. Anything touching files, network, time, randomness, subprocesses or
  globals is skipped (with the reason shown). Skipped code cannot be verified by runtime
  probes.
- Call-graph probes need a fresh SCIP index (`cdebt index`, needs node + network for the
  indexer). Dynamic dispatch, reflection and string-based calls are invisible to SCIP; an
  approximate answer is never used as quiz ground truth.
- Mutation probes are Python-only, slow (a full test run per mutant), and only emitted when the
  baseline suite is green and the true failing set is small.
- The sandbox is layered containment against accidental or AI-generated side effects (static
  screen, isolated interpreter, audit hook, rlimits, `sandbox-exec` on macOS, Docker if present).
  **It is not a security boundary against deliberately hostile native code.** The Docker path
  is implemented but UNVERIFIED (no Docker on the development machine). Run untrusted code in
  a VM or container yourself.

## Score

- The map score is a ranking heuristic, not a probability. The blast-radius default
  (`1 + ln(1+refs)`) deliberately differs from a raw count; see `docs/METHOD.md`.
- Without a SCIP index, blast radius is a name-match approximation (`approx`) that over-counts
  common names.
- Plan effort numbers are configurable guesses (reading rate, per-unit overhead), not
  measurements.

## Backtest

See `docs/RESULTS.md`. It tests only a proxy (review evidence x churn) on agent-authored PRs,
observes outcomes only through other agent PRs in the curated set, and cannot establish
causation. Blast radius and the quiz itself are not evaluated.
