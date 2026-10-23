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

- **Coverage is narrow on real code** (see `docs/CASE_STUDIES.md`; run `cdebt coverage` first).
  Python runtime probes need plain, typed top-level functions with simple argument types (no
  keyword-only/variadic parameters, a return annotation) and no side effects.
- **TypeScript/JavaScript** (Node 24 or newer) get runtime probes for top-level functions with
  type annotations (or JSDoc `@param` types), constant probes, and call-graph probes through
  `scip-typescript`. Not supported: JSX/TSX, enums and namespaces (not erasable), classes and
  methods as runtime targets, generics and destructured parameters, and any module that imports
  npm packages, path aliases or extensionless relative paths that plain Node cannot resolve. On a
  React front-end repository that excludes most files; call-graph questions then carry most of
  the verifiable share.
- **Module-level code is only partly verifiable.** `constant` probes verify the values of
  top-level constants (Python and TypeScript/JavaScript). Everything else at module level
  (imports, class headers, decorators, registrations, top-level calls) can only be **attested**,
  which is a human sign-off, NON-VERIFIED, and trivially easy to over-use (one note can cover
  hundreds of units). It is labelled NON-VERIFIED in the map, gate, plan and PR comment;
  `--require verified` ignores it and `attested_weight` controls how much it counts.
- Anything touching files, network, time, randomness, subprocesses or globals is skipped (with the
  reason shown) and cannot be verified by runtime probes.
- Call-graph probes need a fresh SCIP index (`cdebt index`, needs node and network for the
  indexers). Dynamic dispatch, reflection and string-based calls are invisible to SCIP; an
  approximate answer is never used as quiz ground truth.
- Mutation probes are Python-only, slow (a full test run per mutant), only emitted when the
  baseline suite is green and the true failing set is small, and need `--trusted` because they run
  the project's own test suite.
- **Isolation.** Repositories are untrusted by default: probes need Docker (image already pulled)
  or macOS strict Seatbelt, else cdebt refuses; `--trusted` / `CDEBT_TRUSTED=1` opts out. On Linux
  without Docker there is no OS-level layer in host mode. The sandbox is containment against
  accidental and AI-written side effects, **not a security boundary against deliberately hostile
  native code**; see `docs/THREAT_MODEL.md`.
- **Windows is unsupported** for everything that executes code (`quiz`, `attest`, `coverage`,
  `study init`); those commands exit with a message pointing to WSL2. `map`, `gate`, `plan`,
  `migrate`, `backtest` and `study run/analyze` are written to work there, and the Windows CI job
  (tests that do not execute code, ruff, mypy) passed on a hosted runner; see
  `docs/VERIFICATION.md` and `docs/V1_CHECKLIST.md`. It is not a supported platform for quizzing.

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

## v1.1: agent questions, adaptive quiz, pre-push flow

- **Records are created locally, so a determined person can forge one.** The Action re-executes
  every recorded probe it can in the CI sandbox (`gate --recheck`) and rejects records whose
  answers do not match, whose grading is inconsistent, or that hold no probes. That catches wrong
  or hand-edited answers. It does **not** catch someone who never sat the quiz and wrote *correct*
  answers into a record by hand. UNVERIFIED: only a quiz run inside a service cdebt controls (for
  example a future GitHub App web quiz) would close that gap. Call-graph and mutation probes are
  not re-executed (they depend on an index or the project's tests); a record holding only those is
  accepted but not re-checked.
- **An AI in the editor can still answer quickly.** The adaptive quiz raises the effort of copying
  answers (harder questions after a wrong answer, timed follow-ups answered by tracing the real
  execution when a hard question was answered no slower than easy ones) but does not prevent it.
  Timing is weak evidence: it only triggers extra questions and is never stored or reported.
  With `--no-timer` or `timer = false` follow-ups are untimed and the check is weaker.
- **The developer's agent proposes; it does not decide.** Questions it proposes are only kept if
  cdebt can execute them, twice, and the real target's lines run (a harness that tests a mock is
  rejected). The agent is never told an answer, but the same agent (or any AI the developer
  pastes the questions into) can answer them. Agent-proposed probes are Python-only.
- **The pre-push hook never blocks.** `git push --no-verify` skips it, an agent that pushes with no
  terminal gets a notice, and it exits 0 on any problem. Only CI can stop a merge, and only if
  `cdebt / gate` is a required check **and** the gate is in `enforce` mode; the default is
  advisory (the check is `neutral`). Rulesets on private repositories need a paid GitHub plan.
- **Notes belong to commits.** Records are written as a note on the pushed tip commit. They stay
  valid for any later commit whose unit code is unchanged (they are hash-bound), but a rebase or
  squash moves the commits and the notes stay on the old ones: re-run `cdebt quiz --pr`.
- **Blank and comment-only lines are not gated.** They carry no behaviour. Changes to behaviour
  hidden in a comment-only line do not exist; a change to a string or docstring still counts.
- **Adapters.** Only the Claude Code adapter was exercised end to end against a real CLI (2.1.299), on one tiny
  repository. The
  `codex` and `cursor` adapters are UNVERIFIED (those CLIs were not installed); `command` is the
  generic route.

- **Secret-looking files are not copied into the sandbox**, and questions whose answers look like
  secrets are dropped. Code that needs those files to run is skipped. Detection is pattern-based
  and can miss unusual formats (`cdebt.secretguard`).
