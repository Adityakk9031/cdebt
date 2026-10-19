# cdebt: measuring the code nobody has understood (draft, not published)

AI agents now write a large share of the code in some repositories, reviews get skipped or
rubber-stamped, and nobody can say how big the gap is between the code that exists and the code a
human has actually understood. cdebt is an open-source tool that tries to make that gap visible,
testable, and optionally enforceable. This post says what it does, what we measured, and, more importantly,
what we could not show.

## What it does

- **`cdebt map`** ranks files by AI-written code that nobody has verifiably understood.
- **`cdebt quiz`** asks questions whose answers come from *executing* the code (function results,
  call graphs, module constants), never from a model grading free text.
- **`cdebt attest`** records a human sign-off for code a quiz cannot cover. It is labelled
  NON-VERIFIED everywhere and counts for less.
- **`cdebt gate`** reports on a pull request that changes AI-attributed lines in code nobody has
  understood. It is advisory by default; `--enforce` makes it fail the build. **`cdebt coverage`** tells you, before you trust the gate, how much of your
  highest-debt code a quiz can actually cover.

Attribution comes from Git AI notes or Agent Trace records when they exist, and from
`Co-Authored-By` trailers otherwise (coarse, and labelled as such). "Unknown" is never treated as
"human-written". Understanding is bound to a hash of the code and expires when the code changes.
cdebt measures code and teams, never individuals.

## What we measured, and what it means

**Does the score predict later trouble? Not that we could show.** On the AIDev dataset of
agent-authored pull requests we pre-registered two analyses and published both as measured.
The first covered 167,163 PR-file units in 1,551 repos: the proxy score showed no clear link to
later bug-fix pull requests (risk ratio 0.997, 95% CI 0.657-1.524), reverts were too rare to
analyse, and time-to-fix went the opposite way from the prediction
[src: docs/RESULTS.md]. The second, a within-repo matched-pairs comparison of PRs with and
without human review, was a clean null on 163 pairs (odds ratio 1.000, CI 0.542-2.000)
[src: docs/RESULTS_MATCHED.md]. Both test only a proxy for understanding, fixes are visible only
when they are themselves agent PRs, and neither tests the quiz itself. The honest reading is
"mostly null, proxy only": nothing here shows the score works, and nothing shows it doesn't.

**How much can a quiz cover on real code?** Less than we hoped, and it depends heavily on the
language. On the 25 highest-debt files of three public repositories that use AI agents
[src: docs/case_studies/hive.coverage-ts.json, docs/case_studies/hive.coverage.json, docs/case_studies/agent-meta.coverage.json]:
a TypeScript front-end repository went from 0% to 33% of AI lines verifiable once TypeScript
quizzes existed, with 61% attest-only and 6% unquizzable (JSX); a Python tooling repository
reached 77% verifiable with a call-graph index and 23% attest-only. A second Python repository
managed 19% verifiable and 81% attest-only [src: docs/case_studies/space_chassis.coverage.json].
The call-graph questions carry most of the verifiable share. Runtime questions need pure,
typed functions, which top-debt files often are not.

**What did the pilot cost to fix?** Running on real repositories found problems the unit tests
did not: a screen so strict that almost no file produced a question, a parser crash on real Git AI
notes, a quadratic blast-radius step that made `map` take 53.7 s on a 509-file repo (now 9.8 s with
a warm cache) [src: docs/case_studies/perf.json]. The full list, including what looked wrong or
confusing, is in `docs/CASE_STUDIES.md`.

## Security: this tool runs code

A quiz executes functions from the repository you point it at. cdebt treats every repository as
untrusted by default: probes run in Docker or, on macOS, under a strict Seatbelt profile, or not
at all; `--trusted` is the only opt-out and is never read from the repository. We attacked the
sandbox twice with random and hand-written hostile payloads and it failed both times before it was
fixed: the first round found six holes (a payload could signal and kill other processes, read your
home directory, or open files through SQLite), and the second showed that code inside the sandbox
could switch off the in-process guard, that a repository symlink could get a host secret copied into
the sandbox, and that Node's permission model follows symlinks out of its root. All are fixed and
covered by tests [src: docs/THREAT_MODEL.md]. That is evidence, not proof. The sandbox is containment
against accidental and AI-written side effects, not a defence against a determined attacker with
native-code exploits, and it has been reviewed only by its author and an AI. Read the threat model
before running it on code you do not trust.

## What it is not

- **Not a lie detector.** A quiz cannot stop someone pasting the questions into an AI. It gives
  visibility, not proof.
- **Not a ranking of people.** There are no per-person scores anywhere, by design.
- **Not validated.** We have not shown that passing a probe means someone understands the code.
  A study protocol to test the minimum version of that claim, "people who wrote or reviewed the
  code answer more often than people who never saw it", is written and pre-registered; the
  planning estimate is about 46 participants [src: docs/study_power.json]. We have not run it.
  `cdebt study` gives any team the kit to run it on their own repository and share only
  team-level aggregates.
- **Not finished, and not published.** This is a pre-release that has not been published anywhere;
  it was reviewed only by its author and an AI. Windows is unsupported for anything that executes code. Mutation probes are
  Python-only. Attestation is easy to over-use. See `docs/LIMITS.md`.

## What we need

Teams willing to run `cdebt coverage` on a real repository and tell us what looks wrong, and teams
willing to run the study. The most useful outcome of the next few months is not a bigger number,
it is a clear answer to whether the quizzes measure what we say they measure.
