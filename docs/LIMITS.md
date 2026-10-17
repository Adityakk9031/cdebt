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
- Git AI 1.2+ records through a background daemon that needs git trace2 configuration. The
  notes parser is verified against real output from git-ai 1.0.0 through 1.7.5; see
  `docs/GITAI_FORMATS.md`. 0.x notes are not supported; unsupported or malformed notes yield
  no claims (lines stay unknown).

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

- **Coverage is narrow on real code** (see `docs/CASE_STUDIES.md`): runtime probes need plain,
  typed top-level Python functions with simple argument types, no keyword-only/variadic
  parameters and a return annotation; most files in real repos yield few or none. TypeScript
  and JavaScript can be mapped and gated but not quizzed.
- **Module-level code is only partly verifiable.** `constant` probes verify the values of
  top-level constants (Python only). Everything else at module level (imports, class headers,
  decorators, registrations, top-level calls) can only be **attested**, which is a human sign-off,
  NON-VERIFIED, and trivially easy to over-use (one note can cover hundreds of units). It is
  labelled NON-VERIFIED in the map, gate, plan and PR comment; `--require verified` ignores it and
  `attested_weight` controls how much it counts in the score.
- TypeScript/JavaScript cannot be quizzed at all (no runtime probes; call-graph probes need a
  SCIP index for that language). They can only be attested. `cdebt coverage` shows this.

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
  **It is not a security boundary against deliberately hostile native code.** The Docker
  path is tested (locally via Colima and on a GitHub runner) but only covers probe execution;
  the mutation test runner uses the audit-hook and `sandbox-exec` layers. Run untrusted code in
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
