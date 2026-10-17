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
