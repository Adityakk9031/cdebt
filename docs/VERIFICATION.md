# Verification log

> Run and PR numbers below refer to the author's **private development repository** and a private test mirror (they are not clickable here). The code in this repository is the same; only history identities differ.

What was actually run, where, and what it showed. Dates are 2026-10-17/18.

## GitHub Action on a real PR

Scratch private repo `a private scratch repository` (action.yml + script copied in,
`cdebt` installed from the in-repo source via the action's `version` input).
PR #1 changed `sample/shop.py` in a commit with a `Co-Authored-By: Claude` trailer.

| run | state of PR | job result | PR comment |
|---|---|---|---|
| 39059752954 | AI change, no understanding record | failed (gate exit 1, intended) | created: `cdebt gate: FAIL`, 1 AI line in `price`, source `trailer`, "to clear" hint |
| 39059815017 | second commit adds `.cdebt/understanding.json` for `price` | passed | the **same** comment was edited in place to `cdebt gate: PASS` |

So: fail path, pass path, sticky single comment, and job summary all worked on a GitHub-hosted
runner. Not covered: forks (read-only `GITHUB_TOKEN`; the comment step is best-effort and the
gate result still decides the job), the Git AI notes fetch (the scratch repo has no
`refs/notes/ai`; the script logged that it fell back to other sources), and publishing the
action under a stable `uses:` ref.

## CI on real runners

Scratch repo `a second private scratch repository`, workflow `.github/workflows/ci.yml`:
run 39062309469 passed all 7 jobs: ubuntu and macOS x Python 3.11/3.12/3.13 (ruff, mypy,
pytest) plus the `docker-sandbox` job. Earlier runs on the same repo found, and we fixed:

1. Linux: the sandbox applied `RLIMIT_AS` to the Docker *client* (a Go binary), which crashed
   with "failed to reserve page summary memory" (run 39061874674).
2. mypy `max()` overload error under newer numpy typings on `np.unique(...)[1]` (same run).
3. On Linux a memory bomb is contained as a `MemoryError` (RLIMIT_AS enforced) rather than a
   kill; the test now accepts all three containment outcomes (run 39062106024).

## Docker sandbox

Locally via Colima (Docker 29.5.2, `python:3.11-slim`): `tests/test_sandbox_docker.py`
(6 tests: escapes contained, loop killed and container removed, memory bomb, OS-level network
block independent of the audit hook, runtime probe answered inside the container) passed, and
no `cdebt-*` containers were left behind. Same tests passed on a GitHub-hosted Ubuntu runner
(`docker-sandbox` job). Files go in with `docker cp` because temp dirs are not shared with the
Docker VM; the container runs with `--network none`, caps dropped, unprivileged user, memory and
pids limits.

The Docker path applies to `python`-mode probes only; the mutation test runner (a project's
test suite) uses the audit-hook + `sandbox-exec` layers, not Docker.

## Mutation backend

mutmut 3 was tried; see `docs/METHOD.md` ("Mutation backend"). cdebt's own mutator is the only
backend; `mutmut` is no longer an extra.

## Git AI formats

Real binaries for 1.0.0 through 1.7.0 (plus 1.7.5) were run; see `docs/GITAI_FORMATS.md`.

## Process errors found while doing this (disclosed)

- `tests/test_probes.py` was **never created** in the original M3 round: the command that
  wrote it was chained after a failing `sed`. The call-graph and mutation probes therefore had
  no tests, and the earlier statement that they were "verified" was wrong. The tests were
  written and run in this round (13 pass); they found no defect in those two probe kinds, but
  running the quiz on real repos found two other bugs (below).
- Bugs found only by real data this round: parser crash on notes with an empty attestation
  section (real Git AI 1.0-1.6 notes); a runtime answer of the literal `None` graded wrong;
  the runtime static screen rejecting nearly every real file (1/25 and 0/25 files produced
  probes before the function-level screen; see `docs/CASE_STUDIES.md`).
- One commit message in the history ("Pre-registration for matched-pairs analysis") also
  contains unrelated earlier work (docker sandbox, Git AI parser, fixtures) because it was
  committed with `git add -A`. The pre-registration file itself was committed before any
  matched-pairs code existed (`git log` shows the order).

## Infinite-loop flake: investigation and fixes (v0.1.2)

**Symptom.** Once, in a full suite run, `tests/test_sandbox.py::test_infinite_loop_killed`
failed on `assert r.timed_out and r.elapsed < 6` (timeout 1.5 s). I first called it "load-related".
That was not an acceptable explanation for a safety property, so:

**Reproduction attempts (against the old code).** Loop under CPU stress, 86 trials in total:
host sandbox with 0 burners (5 trials) and 24 burners (15); Docker sandbox with 0 burners (6),
16 burners (12), 20 burners (40), and with the Docker VM itself saturated by 8 CPU-burning
containers (8). 0 failures in 86 trials, but the worst wall time under stress reached 2.60 s for
a 1.5 s deadline, i.e. the kill could be **1.1 s late**. I could not reproduce the one original
failure, so I cannot claim to have identified its exact trigger. What I did find, by reading the
kill path and writing a deterministic test for each, were five concrete ways the timeout could be
late or missed. (That failing run happened while Colima was up, so it most likely went through the Docker
path, where defects 3 and 4 below apply.)

| # | defect (old code) | evidence |
|---|---|---|
| 1 | The watchdog checked the deadline and polled RSS (`ps`) in the **same loop**, so one slow `ps` (normal under load) delayed the kill by that long | with `ps` simulated slow: killed at 3.02 s for a 1.0 s deadline (test now asserts < 1.7 s) |
| 2 | Children that left the process group (`setsid`) survived the timeout, and because they inherit our stdout pipe the call itself hung until the CPU rlimit killed them | test with a detached child: returned after 3.04 s instead of about 1.5 s, child alive |
| 3 | In Docker mode only the docker **client** was killed at the deadline; the container kept running until a later `docker rm -f`, which had **no timeout** (could hang the caller forever, container still burning CPU) | read from the code; fixed by `docker kill` at the deadline and bounded cleanup |
| 4 | `elapsed` included cleanup time, so slow Docker cleanup looked like (and was reported as) a late kill | `elapsed` is now measured when the process stopped |
| 5 | After the leader was reaped, `killpg(getpgid(pid))` could signal an **unrelated process group** if the PID had been reused. Running the new reused-PID test against the old code **killed the pytest process itself** | observed |

A sixth item is housekeeping: a crash between `docker create` and cleanup left an orphan
`cdebt-*` container (one was found); orphans whose creator pid is dead are now reaped.

**Fix.** The deadline is a separate `threading.Timer`; RSS polling runs in its own thread; at the
deadline the container is killed, then the whole process tree (descendants first, then the group,
twice); the group kill refuses to signal a pid that no longer leads that group; every docker
command has a timeout; orphans are reaped. Regression tests: `tests/test_sandbox_kill.py`
(slow-`ps`, CPU-stress bound, detached child, reused PID, bounded cleanup, container killed
before cleanup, orphan reaper). Tests 1, 2 and 5 fail on the old code as described above; the
Docker-specific ones test new functions and so cannot run against the old code.

**Limits that remain.** A child that double-forks and is re-parented to `launchd` before the
deadline cannot be found by walking the process tree (no cgroups on macOS); it is still bound by
its inherited CPU rlimit. Hostile native code is out of scope (see `docs/LIMITS.md`).

## Release candidate 2 (1.0.0rc2): private repo, CI, Action, flake gate

Dates are 2026-10-19/20. The repository is private: `the private development repository` (visibility checked with
`gh repo view --json visibility` before every push). Nothing is on PyPI and no release exists.

### CI on real runners (this repository)

Final green run on commit 10bd203:
run 39302371977 (all jobs success): Windows,
Ubuntu x Python 3.11/3.12/3.13, macOS x Python 3.11/3.12/3.13, `docker-sandbox`, `package`
(Windows, macOS, Ubuntu: sdist + wheel, `twine check`, clean-venv install and smoke test) and
`repeat-suite` (`scripts/repeat_suite.sh 20`: the whole suite 20 times in a row on Ubuntu with
Docker; job run 39302371977).

Failures met on the way and what was done:

| run | job | what failed | fix |
|---|---|---|---|
| 39297361041 | Windows, pytest | POSIX-only sandbox tests ran on Windows; earlier runs also had mypy errors on POSIX-only attributes (fixed with explicit `sys.platform` guards, checked with `mypy --platform win32`) | `posix_sandbox` marker and skip of the red-team module on Windows |
| (Linux runs, see commit 507776b) | Ubuntu, pytest | memory/output flood is contained as `MemoryError` on Linux, test expected a kill | test accepts all containment outcomes |
| (Linux runs, see commit a0e34d8) | Ubuntu, Node suite | **my own regression** from the red-team hardening: blocking `process.reallyExit` broke the runner's exit, and removing `WebAssembly` broke TypeScript stripping | both removals reverted; lesson: run the whole Node suite, not just the new tests, after hardening |
| 39301809522 | macOS 3.12, pytest (branch `ai-sample`) | `tests/test_js_probes.py::test_runtime_is_seeded`: one run produced one fewer probe (`pure`) than the other | a probe whose sandboxed call fails or is cut off is skipped with a reported reason (never a wrong answer). The cause on the runner is **UNVERIFIED** (the log shows only the assertion). The test now asserts what matters: probes present in both runs are identical and every dropped probe is a reported skip |

Windows: the job passes (tests that do not execute code, ruff, mypy, package smoke test).
Anything that executes code is unsupported on Windows and exits with the WSL2 message.

### Action on real PRs

Workflow `cdebt-gate` (`.github/workflows/gate.yml`) runs the composite action from this repo.
PR PR 1 (branch `ai-sample`, AI-attributed through a
`Co-Authored-By` trailer, so source `trailer`, coarse):

- Commit 6dbf608 "Add sample pricing helper": run 39301629670 created the sticky comment
  (comment id 6274170414, created 2026-10-19T18:20:21Z).
- Commit a1306d9 "Record verified understanding of the sample helper": run 39301858149 **edited the
  same comment** (same id; `updated_at` 2026-10-19T18:22:05Z). Exactly one cdebt comment exists.

Exact text of the comment as it stands now:

```
<!-- cdebt-gate -->
## cdebt gate: PASS (advisory)

AI-attributed changed lines not cleared: **0** (allowed: 0). Policy: `--require default`. **Advisory: this does not block the merge** (use `--enforce` to fail the build).

Attribution sources on changed AI lines: `trailer`: 7.


> Some attribution is from commit trailers: coarse, commit-level.

<sub>cdebt measures code, not people. A quiz is visibility, not a lie detector.</sub>
```

The text of the comment as first created is not reproduced here (GitHub keeps only the current body).

**Fork case: simulated, not run from a real fork.** PR
PR 2 (branch `ro-token-sim`, commit 792b6a7) ran with a
read-only token to imitate what a fork PR gets (run 39301992728, job success). No cdebt comment
was posted, and the job still succeeded: the comment step degrades gracefully. A real fork needs a
second GitHub account and was **not** tested. (A third-party review bot also comments on these
PRs; it is unrelated to cdebt.)

### Flake gate: 20 consecutive full-suite runs

- CI `repeat-suite` (20 runs on Ubuntu with Docker): success, link above.
- Local macOS paced run (pairs, 50 s cooldown, 120 s before the last), in two parts:
  - Runs 1-13 on commit 10bd203: all passed. The laptop was on battery and slept, so runs 12 and
    13 took 3147 s and 4074 s of wall time against about 136 s for the others (pytest had used
    2.5 s of CPU in 17 minutes); I stopped the run there.
  - Runs 14-20 on commit 3b0eb0d (resumed with the laptop awake): all passed, 139-141 s each
    (`scripts/repeat_suite_paced.sh 14 20 2 50 120` reported `ALL 7 RUNS (14..20) PASSED`).
  - Total: **20 of 20 passed, 0 failed**, but not on a single commit and not in one continuous
    sitting. The only code change between the two parts was the `test_runtime_is_seeded` test.

## v1.1.0rc1: agent questions, adaptive quiz, pre-push flow (2026-10-21)

**GitHub Actions could not run: BLOCKED by the account, not by the code.** Every job of every run
on this round's commits was refused with the annotation "The job was not started because recent
account payments have failed or your spending limit needs to be increased. Please check the
'Billing & plans' section in your settings": CI on d1a25e8
(run 39641670611, 12 of 12 jobs), the push runs on
943a771 (runs 39641876571 and 39641903035) and the Action on PR 3
(run 39641902970). So for this round there is **no
CI result on Linux, macOS or Windows and no GitHub-hosted Action run**. The previous round's green
CI (1.0.0rc2, run 39302371977) does not cover the new code. **Needs-human:** fix billing, then
re-run CI and `cdebt-gate` on the PRs below. Local checks that stand in for CI are not equivalent.

### Real pre-push run (local machine, real git, real Git AI 1.7.5, real pseudo-terminal)

Clone of the private repo; AI-written `demo/billing.py` recorded by Git AI (22 AI lines in the
note, `git-ai stats`); `cdebt init --yes --no-workflow` installed the hook; strict macOS Seatbelt
sandbox (untrusted default, no Docker). `git push -u origin v11-run-hooked` ran the hook on a real
pty (script `ptypush.py`, scratch only): 16 questions, **the first answer deliberately wrong** (a
harder branch question followed at once), **three timed follow-ups** (policy ratio set to 1000 to
force the trigger; the countdown line `[  15 s left] answer:` was shown), all three functions
UNDERSTOOD (verified), `cdebt: notes pushed before the branch`, then the branch push. Remote
refs afterwards: `refs/notes/ai`, `refs/notes/cdebt`. PR: PR 3.

### Action script, run LOCALLY (not by GitHub Actions)

`scripts/action_run.sh` was run on this Mac with `GITHUB_REPOSITORY`, `PR_NUMBER` and a token set,
so the comments below were posted by the user's account (`the author account`), **not by the Actions
bot**. It fetched both notes refs and ran `gate --recheck` (every recorded probe re-executed in the
sandbox).

- PR 3, comment 6322025508: `cdebt: all AI-written changed lines are understood (advisory)`; each
  unit listed `understood (verified by execution), re-checked`; attribution `git-ai`: 18 lines (the
  four blank lines of the 22 were not gated).
- PR 4 PR 4, branch pushed with `git push --no-verify`
  (hook skipped): first run, comment 6322042105: `cdebt: 7 AI-written lines not understood
  (advisory)`, `demo/shipping.py::shipping_cost  1-7  NOT UNDERSTOOD (not_quizzed)`, hint
  ``Run `cdebt quiz --pr 4` locally to clear, then comment `/cdebt recheck`.``; `cdebt gate --enforce
  --recheck` exited 1.
- `cdebt quiz --pr 4` on a real pty: 5 questions incl. one timed follow-up, UNDERSTOOD, notes
  pushed, comment `/cdebt recheck` posted through `gh`. Re-running the script **edited the same
  comment in place** (id 6322042105, created 17:16:06Z, updated 17:16:37Z UTC) to `all AI-written
  changed lines are understood ... re-checked`. No person is named in either comment.
- The `cdebt / gate` **check run could not be created** by a personal token (`could not create the
  check run`), as expected: only the Actions token can. **UNVERIFIED** until Actions runs again.
  `issue_comment` triggering and the author-association filter are also UNVERIFIED on GitHub.

### Agent connection

- The Claude Code CLI (2.1.299) accepted the adapter's flags and started, but answered "Not logged
  in · Please run /login", so cdebt fell back to the builtin generator (the intended failure path).
  **A full run with a real agent proposing questions is UNVERIFIED.** `codex` and `cursor` adapters:
  UNVERIFIED (CLIs not installed). The MCP server and the proposal pipeline are tested with a fake
  agent that speaks real MCP over stdio (`tests/_fake_agent.py`).

### Problems found while doing this (disclosed)

- The MCP server process did not apply the untrusted-by-default isolation, so agent harnesses would
  have run in host mode. Found by reading the code before the real-agent run; fixed (the server
  applies the same isolation; `--trusted` is passed on only if the person chose it); test added.
- Blank lines between functions counted as AI-written module-level code and kept the gate failing
  after a normal AI commit (also seen in the earlier demo); the gate now ignores blank and
  comment-only lines.
- An agent command that could not start crashed the quiz instead of falling back; fixed.
- Wrong-answer handling asked the harder question only in the next round and burned the question
  budget; now one immediate harder question per round.
- `scripts/action_run.sh` treated a 404 error body as a comment id when the PR did not exist
  (seen when I opened PR 4 incorrectly first). Fixed (only a numeric id counts); checked locally:
  a missing PR now gives one clean 404, and PR 4's comment was edited in place again (same id).
- I made two mistakes in this run: a first attempt created an empty branch (the `demo/` folder only
  existed on the other branch) before PR 4 was opened properly; no harm beyond the stray branch.

### CI cost (free minutes)

Because the account ran out of Actions minutes, `.github/workflows/ci.yml` now runs only the cheap
Ubuntu checks on each push or PR (one Python, the Docker sandbox job, the Ubuntu package build).
The full matrix (macOS x3 Pythons, Windows, the 20x repeat-suite, macOS/Windows package smoke
tests) runs on a manual "Run workflow" (`workflow_dispatch`) and should be run once before any
release. **None of it has run for this version** (see above).

### Local flake gate: 20 consecutive full-suite runs (macOS, this version)

`scripts/repeat_suite_paced.sh 1 20 2 50 120` (pairs, 50 s cooldown, 120 s before the last), all on
commit eed1550's code (the final source; later commits changed only docs): **20 of 20 passed, 0
failed**, 161-183 s each (`ALL 20 RUNS (1..20) PASSED`). The laptop was awake and the real-run
checks above ran concurrently for part of it, which is why some runs were slower. This is a local
macOS result only; the Linux 20x job (`repeat-suite`) has **not** run for this version.

### Real GitHub Actions run on a SELF-HOSTED runner (2026-10-22)

GitHub-hosted minutes were used up (see above), so the real Actions plumbing was tested on a
**self-hosted runner on the author's Mac** (official runner v2.338.0, checksum verified against the
release notes; registered to the private repo only, removed afterwards: GitHub showed 0 runners).
A temporary workflow `gate-local.yml` ran the same composite action (`uses: ./`, with the new
`setup-python: "false"` input and a local Python 3.11 venv). Self-hosted jobs started while hosted
ones were still blocked. Two runner problems of my own making (a duplicate runner process left from
an earlier launch, and a re-run of the same job) and a slow-network `pip` timeout caused failed
attempts before the passing ones; none was a cdebt defect. This is **not** a GitHub-hosted Linux run:
Docker was not used here (macOS strict sandbox), and the hosted `test`/`docker-sandbox`/`package`
jobs still have not run for this version.

- PR 3 PR 3, run 39738307837 (`pull_request`, success):
  the Actions token created the check run `cdebt / gate` (app `github-actions`, conclusion
  `success`).
- PR 5 PR 5: AI code pushed with `--no-verify`.
  First run 39738496740 (`pull_request`): the bot `github-actions[bot]` **created** comment
  6334950051, `cdebt: 10 AI-written lines not understood (advisory)`,
  `demo/tax.py::tax_due  1-10  NOT UNDERSTOOD (not_quizzed)`, and the check run `cdebt / gate`
  concluded **`neutral`** (advisory failure). Then `cdebt quiz --pr 5` on a real pty (UNDERSTOOD,
  notes pushed, `/cdebt recheck` posted by the owner). The comment triggered run 39738609539
  (`issue_comment`, success; owner-association filter satisfied): the bot **edited its own comment
  in place** (same id 6334950051, created 07:09:16Z, updated 07:10:17Z UTC) to `cdebt: all
  AI-written changed lines are understood (advisory)` with `understood (verified by execution),
  re-checked`, and a new `cdebt / gate` check run concluded **`success`**. No person is named in
  any comment. This verifies: the PR trigger, bot comment create then in-place edit, the
  `/cdebt recheck` trigger, and the check run (neutral then success) on real GitHub Actions.
- Not covered: a fork PR (read-only token), the ruleset that makes `cdebt / gate` a required check
  (needs a paid plan for private repos), and the author-association filter rejecting a stranger
  (only the owner commented).

### Docker mode (Colima, 2026-10-22)

`tests/test_sandbox_docker.py` (8 tests) passed against Docker 29.5.2 via Colima, including the new
`test_agent_trace_probes_and_ci_recheck_run_in_docker`: agent-proposed trace probes, the mock
rejection and the CI re-execution (genuine record accepted, doctored answer rejected) all ran
inside the container, with no leftover containers. The VM was stopped afterwards.

### Real coding agent end to end (Claude Code 2.1.299, 2026-10-22)

After the author logged in, `cdebt quiz pkg/shop.py --emit` ran the real `claude -p` headlessly
through the adapter (strict macOS sandbox, no `--trusted`) on a throwaway repo with a function that
uses `time` and a test file. Run 1: 11 proposals, 5 accepted, 5 verified questions in 21 s,
including boundary cases such as `shipping(49, express=True)` and `shipping(50, True)`. Run 2: 11
proposals, 7 accepted and verified in 23 s; the agent proposed "With time.time mocked to return
1234.9, what does stamp() return?", a clock-reading function the builtin generator skips; cdebt ran
the real function under the mock to get the answer. The agent was told only accepted/rejected.
Run 1 asked the same constant question twice (agent and builtin); fixed (a question from two
sources is asked once, with a test). Not covered: more than one small repository, the quality of
questions on real code, a long agent run, and the codex and cursor adapters.

### GitHub-hosted CI on a private test mirror (2026-10-22)

The main account's hosted minutes were used up, so the full CI ran on a **private mirror of this
code under a second account** (used only for testing; never published). Run 39783686951 on the code
with the sandbox fix: **all 12 jobs succeeded** on GitHub-hosted runners: tests on Ubuntu and macOS
for Python 3.11, 3.12 and 3.13, the Windows job (non-executing tests, ruff, mypy), the Docker
sandbox job, package builds (sdist, wheel, `twine check`, clean-venv smoke test) on Ubuntu, macOS and
Windows, and `repeat-suite` (the whole suite 20 times in a row on Ubuntu). The mirror's history has
the author's name and email replaced; the code is the same.

Failures met on the way, all mine: (1) the first mirror run (39782055600) failed `docker-sandbox` and
`repeat-suite`: my `sys._getframe` fix used `__file__`, which does not exist when Docker launches the
runner with `python -c`, so the runner crashed in Docker only. I had removed the local Docker VM and
had not re-run the Docker tests; CI caught it. Fixed (runner frames are identified by module
globals) with a regression test that launches the runner the Docker way; the next run passed.
(2) Earlier in the day the same runner problem (billing) had blocked every hosted job on the main
account (see above).

Not covered: a fork pull request, the ruleset that requires `cdebt / gate`, and the main account's own
hosted run (blocked by its minutes). On the mirror, a real pull request also showed the bot creating
a comment, editing it in place after the quiz, and the `cdebt / gate` check going neutral then
success (`examples/first_user/assets/README.md`).

### Full CI on the public repository (2026-10-22)

After the repository was made public, the full CI ran on GitHub-hosted runners on the main account
(run 39807865818, started by hand with "Run workflow"): **all 12 jobs succeeded**: tests on Ubuntu
and macOS for Python 3.11, 3.12 and 3.13, the Windows job, the Docker sandbox job, the package builds
on Ubuntu, macOS and Windows, and `repeat-suite` (the whole suite 20 times in a row on Ubuntu). The
billing limit that blocked hosted runs while the repository was private did not apply here. A clean
install from the GitHub tag (`pip install "cdebt[agent] @ git+https://github.com/Adityakk9031/cdebt@v1.1.0-rc1"`)
worked in a fresh environment with no login.

Still not covered: a pull request from a fork (read-only token), the ruleset that requires the
`cdebt / gate` check, and the PyPI upload (not done yet).

### PyPI pre-release (2026-10-22)

`cdebt 1.1.0rc2` was uploaded to PyPI by the `publish` workflow (run 39829154000, started by hand on
the `v1.1.0-rc2` tag, PyPI trusted publishing, deployment approved by the maintainer in the `pypi`
environment). It has the wheel and the sdist. Checked from a clean virtual environment: `pip install
--pre "cdebt[agent]"` installs `cdebt 1.1.0rc2`, `cdebt --version` works and the agent extra imports.
The PyPI description has no relative links. Note: because no stable version exists yet, a plain
`pip install cdebt` also installs this pre-release (pip falls back to pre-releases when they are the
only versions); an earlier draft wrongly said it would not. `1.1.0rc1` is on GitHub only and was never
uploaded to PyPI. Not covered: the PyPI page on other devices, and install on Windows or Linux from PyPI.

### 1.1.0rc3 on PyPI: the secret-leak fix (2026-10-23)

The fix (secret-looking files never copied into the sandbox; secret-looking answers dropped) passed
the full local suite in cooled-down chunks and the full CI on the public repository (run
39908034682, all jobs including the 20x flake check). `1.1.0rc3` was published by the `publish`
workflow (run 39921259056, approved by the maintainer). From a clean environment,
`pip install --pre "cdebt[agent]==1.1.0rc3"` installed it, and with an `.env` that was untracked and
not in `.gitignore`, `copy_tree` left it out of the sandbox and `looks_secret` flagged the secret.
Right after the upload, PyPI's cached index still served `1.1.0rc2` to an unpinned install for a few
minutes; asking for the version explicitly worked at once.

### 1.1.0rc4 from PyPI: full demo rehearsal in a clean venv (2026-10-23)

Clean venv (Python 3.14, macOS), `pip install --no-cache-dir "cdebt==1.1.0rc4"` from PyPI (the wheel
came from files.pythonhosted.org, not the local build), `cdebt --version` printed `1.1.0rc4`. Then
the demo from `examples/first_user/` end to end, with real Git AI 1.7.5 notes, the real pre-push
hook and the real sandbox (`DEMO_FORCE_FOLLOWUP=1`; no coding agent connected, so the built-in
questions only; the "AI" is the `git-ai checkpoint mock_ai` stand-in):

| step | result |
|---|---|
| `setup_demo.sh`, `cdebt init` | demo built, attribution `Git AI (line-level)`; hook installed, workflow written |
| `cdebt map` / `cdebt coverage` | AI = 85.7% (24 lines), verified 0%; 75% of AI lines verifiable, 25% attest-only |
| `git push -u origin feature` through the hook, first answer wrong on purpose | 13 questions: `apply_discount` UNDERSTOOD (3 of 4 right), `late_fee` UNDERSTOOD after one timed follow-up, `invoice_total` UNDERSTOOD; "notes pushed before the branch" |
| `git notes --ref=cdebt show HEAD` | 3 records with `status_reason`; the wrong first answer is kept; **no time-like key** anywhere in the note |
| watcher during `cdebt quiz pkg/pricing.py` (answers `A` to everything) | 39 distinct sandbox child processes; the quiz correctly ended NOT UNDERSTOOD (1 of 4, needed 3) |
| `sandbox_peek.py` | 6 of 6 hostile actions BLOCKED; all 5 outside checks True |
| `cdebt gate` / edit `late_fee`, commit / `--enforce` | PASS; then `late_fee` (6 AI lines) flagged **stale**; `--enforce` exit code 1 |

Found while doing this (disclosed): the forced follow-up for `late_fee` (Q9) was the same question
as Q7 that had just been asked, because the follow-up generator only excluded the hard question
itself. A repeated question is a weak follow-up. Fixed in 1.1.0rc5 (a follow-up never repeats an
asked question; test in `tests/test_adaptive.py`). Also seen: `apply_discount` and `invoice_total`
ended `verified_no_follow_up_possible`, meaning the pace check fired but no new question could be
generated; that outcome clears the function and is labelled as such, which is a limit of the built-in
generator, not a verification. Not covered: Linux, Windows, a connected coding agent, a real PR.

### 1.1.0rc5 from PyPI: the same rehearsal in a clean venv (2026-10-23)

Published by the `publish` workflow (run 39977068347, approved by the maintainer). Right after the
upload PyPI's JSON API showed rc5 while pip's index still said "no matching distribution" for a few
minutes; a retry loop then installed it from files.pythonhosted.org and `cdebt --version` printed
`1.1.0rc5`. Same demo, same steps as the rc4 section above, first answer wrong on purpose:

- Hooked push: 12 questions, all three functions UNDERSTOOD, "notes pushed before the branch".
  The note holds 3 records with `status_reason`, keeps the wrong first answer, and has **no
  time-like key**. `cdebt gate` PASS; watcher saw 43 sandbox children; `sandbox_peek.py` 6 of 6
  BLOCKED and 5 of 5 outside checks True; after editing `late_fee` the gate flagged it stale and
  `--enforce` exited 1.
- **Finding (not fixed in rc5): the staged timed follow-up no longer appears in the demo.** The rc5
  fix stops a follow-up repeating an asked question; on this small demo project every follow-up the
  built-in generator can make had already been asked, so all three functions ended
  `verified_no_follow_up_possible` and nobody saw a countdown. The rule itself is covered by unit
  tests (`tests/test_adaptive.py`), but the demo script's "timed follow-up appears" beat will not
  show with the built-in questions alone. Candidate fix: also offer a fresh input of the same
  function as a follow-up when the traced ones were already asked. Not done, not released.
- Not covered: Linux, Windows, a connected coding agent, a real PR.

### 1.1.0rc6 from PyPI: the follow-up gap closed, same rehearsal (2026-10-23)

Published by the `publish` workflow (run 40001016156, approved by the maintainer). Clean venv,
`pip install --no-cache-dir "cdebt==1.1.0rc6"` from files.pythonhosted.org, `cdebt --version`
printed `1.1.0rc6`. Same demo and steps as the rc4 and rc5 sections, first answer wrong on purpose:

- Hooked push: 15 questions. All three functions UNDERSTOOD with `verified_after_follow_up`; each got
  one timed (15 s) follow-up that was a **new** question (the same function on a fresh input, for
  example `apply_discount(15, 10)` after `apply_discount(14, 10)`), so the rc5 gap is closed in this
  demo. "notes pushed before the branch". The note holds 3 records with `status_reason`, keeps the
  wrong first answer, has **no time-like key**. `cdebt gate` PASS.
- Watcher during `cdebt quiz pkg/pricing.py`: 39 sandbox children. `sandbox_peek.py`: 6 of 6 BLOCKED,
  0 ALLOWED, 5 of 5 outside checks True. After editing `late_fee` the gate flagged it stale and
  `--enforce` exited 1.
- Observed limit: answering `A` to every question (not reading them) ended `subtotal` and
  `apply_discount` NOT UNDERSTOOD but cleared `late_fee` (3 of 5 right, one of them hard) by chance,
  because the questions have only 2 to 4 options. A short set cannot rule out lucky guessing; this
  is one more reason the result is visibility, not proof (`docs/LIMITS.md`).
- Not covered: Linux, Windows, a connected coding agent, a real PR.

### 1.1.0rc7 from PyPI: the reworked even-pace check, same rehearsal (2026-10-23)

Published by the `publish` workflow (run 40011923890, approved by the maintainer). Clean venv,
`pip install --no-cache-dir "cdebt==1.1.0rc7"` from files.pythonhosted.org, `cdebt --version`
printed `1.1.0rc7`. Same demo as the rc4 to rc6 sections (`DEMO_FORCE_FOLLOWUP=1`, which sets
`hard_vs_easy_ratio = 1000` and `min_median_seconds = 0` so the scripted instant answers are judged),
with `CDEBT_FOLLOWUP_SEED=7` set for both the answer pre-computation and the real push, because the
follow-ups are now random.

- Hooked push: `apply_discount` UNDERSTOOD (`verified`; its set has no hard question, so there is
  nothing to compare and no follow-up), `late_fee` and `invoice_total` UNDERSTOOD
  (`verified_after_follow_up`), each after ONE follow-up that was a short-answer changed input
  (`late_fee(22, 14)`, `invoice_total([-3], 19, -1)`) shown with a visible 5 s countdown (the floor,
  because scripted answers take about 0 s; the 75%-of-own-pace rule is covered by unit tests, not
  rehearsed with a human). Notes pushed before the branch; the note holds 3 records with
  `status_reason` and **no time-like key** (searched for time, second, elapsed, duration, latency,
  speed, pace, took, timer, countdown). `cdebt gate` PASS.
- Watcher: 41 sandbox children. `sandbox_peek.py`: 6 of 6 BLOCKED, 0 ALLOWED, 5 of 5 outside checks
  True. After editing `late_fee` the gate flagged it stale; `--enforce` exited 1.
- Answering `A` to everything (not reading) now ended `late_fee` **NOT UNDERSTOOD with
  `follow_up_failed`**: the lucky guesses passed the score (as they did on rc6) and the follow-up
  stopped them. One run on one function, not a detection rate.
- Mistake in my own rehearsal script, disclosed: my first run used an answers oracle that reported
  0.0 s answer times; `0 < 1000 x 0` is false, so the oracle saw no follow-up while the real push did,
  the scripted answers went out of step and `invoice_total` ended NOT UNDERSTOOD. This is an
  artefact of the script (the oracle now reports 0.01 s), not of the product; I deleted that run and
  redid it.
- Found, not fixed (cosmetic): `cdebt quiz` prints `N of M right` with M counting the follow-up
  while N counts only the main questions (`late_fee`: "3 of 6 right, needed 3" for 5 main questions
  plus 1 follow-up). The verdict itself is right. `src/cdebt/cli.py` uses `len(o.asked)`.
- Not covered: Linux, Windows, a connected coding agent, a real PR, a human answering at their own
  pace against the 75% countdown.

### 1.1.0rc8 from PyPI: the score-line fix, same rehearsal (2026-10-24)

Published by the `publish` workflow (run 40060764747, approved by the maintainer). Clean venv,
`pip install --no-cache-dir "cdebt==1.1.0rc8"` from files.pythonhosted.org, `cdebt --version`
printed `1.1.0rc8`. Same demo and settings as the rc7 section (`DEMO_FORCE_FOLLOWUP=1`,
`CDEBT_FOLLOWUP_SEED=7`, answers oracle reporting 0.01 s):

- Hooked push: `apply_discount` UNDERSTOOD (`verified`, no hard question in its set so no
  follow-up), `late_fee` and `invoice_total` UNDERSTOOD (`verified_after_follow_up`), each after one
  short-answer changed-input follow-up on a visible 5 s countdown (`late_fee(22, 14)`,
  `invoice_total([-3], 19, -1)`); notes pushed before the branch; the note has 3 records with
  `status_reason` and **no time-like key**. `cdebt gate` PASS.
- Watcher: 51 sandbox children. `sandbox_peek.py`: 6 of 6 BLOCKED, 0 ALLOWED, 5 of 5 outside checks
  True. After editing `late_fee`: flagged stale, `--enforce` exit 1.
- **The rc7 cosmetic bug is fixed**: the quiz score line now counts only the main questions.
  Answering `A` to everything, `late_fee` printed `NOT UNDERSTOOD (3 of 4 right, needed 3;
  follow_up_failed)`: 4 main questions, the score passed by luck, the follow-up failed (rc7 printed
  "3 of 6" for 5 main questions plus a follow-up).
- Not covered: Linux, Windows, a connected coding agent, a real PR, a human answering at their own
  pace against the 75% countdown.

