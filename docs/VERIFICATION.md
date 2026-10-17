# Verification log

What was actually run, where, and what it showed. Dates are 2026-10-17/18.

## GitHub Action on a real PR

Scratch private repo `Adityakk9031/cdebt-action-scratch` (action.yml + script copied in,
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

Scratch repo `Adityakk9031/cdebt-ci-scratch`, workflow `.github/workflows/ci.yml`:
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
