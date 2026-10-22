# Changelog

## 1.1.0rc1

- **Questions from the developer's own coding agent.** `cdebt agent connect` (opt-in, saved in the
  USER config, never read from a repo) lets Claude Code / Codex / Cursor / any command propose
  questions, call inputs and test harnesses through `cdebt mcp` (tools `get_targets`,
  `propose_probe`; the reply is only accepted/rejected, never an answer). cdebt executes every
  proposal in the sandbox, twice, and discards anything it cannot verify, including harnesses
  whose execution trace shows the real target did not run. Python only. The Claude Code
  adapter was run end to end against the real CLI (2.1.299): the agent proposed questions, cdebt
  verified them by execution, including one for a clock-reading function that the builtin generator
  skips. `codex` and `cursor` adapters are UNVERIFIED.
- **Adaptive quiz.** Easy/hard labels, a harder question after a wrong answer, up to 3 rounds per
  unit, and 1-2 timed follow-ups (answered by tracing the real execution) when a hard question was
  answered no slower than the easy ones. Timing is kept in memory only and never stored.
  `--no-timer`, `.cdebt/policy.toml`, and a `status_reason` on every record (about the code).
- **Pre-push flow.** `cdebt init` (consent per step; never overwrites; chains existing hooks;
  workflow file; optional GitHub ruleset), a pre-push hook that quizzes on the terminal, writes
  `refs/notes/cdebt` and pushes the notes before the branch (never blocks; `--no-verify` skips it),
  and `cdebt quiz --pr N`.
- **CI.** `gate --recheck` re-executes recorded probes so hand-edited answers fail; the sticky
  comment lists units with line ranges and reasons (no person named); a `cdebt / gate` check run
  (neutral when advisory); `/cdebt recheck` comment trigger for owners, members and collaborators.
- The gate ignores blank and comment-only lines. The MCP server applies the same untrusted-by-default
  isolation as `cdebt quiz`.
- **Fixed a sandbox regression from 1.0.0rc2** found by measuring real repositories: blocking
  `sys._getframe` outright made `@dataclass`, `TypeVar`, `NamedTuple`, `namedtuple`, `logging`,
  `warnings.warn`, `argparse` and `import pathlib` fail, so most real modules could not be imported
  and the quiz could verify almost nothing. Now only the runner's own frames are refused; new
  red-team tests (deep frame walk, stdlib patterns). See `docs/AGENT_BENCHMARK.md`.
- Rules changed (CLAUDE.md, METHOD): the agent may propose, never decide; `refs/notes/cdebt` and the
  consented hook are the only writes besides `.cdebt/`; no timing data, no per-person flags.

## 1.0.0rc2

- **Gate is advisory by default** (`--enforce` / Action input `enforce` to fail the build); the PR
  comment says "(advisory)".
- **Sandbox red-team pass 2** (`tests/test_sandbox_redteam.py`, `docs/THREAT_MODEL.md` 5b): the
  in-process guard is now closure-scoped, binds the builtins it uses, has its own `realpath`, and
  `gc`/frame/trace introspection is blocked (6 of 13 tampering variants defeated the old guard);
  `copy_tree` no longer copies through repository symlinks (a host secret could be copied into the
  sandbox); the sandbox root is scrubbed of escaping symlinks and hard-linked files (Node's
  permission model follows symlinks out of the root); shared-memory modules, Node `inspector`, V8
  flags and diagnostic reports blocked.
- Linux CI caught two regressions of mine in the Node runner (blocking `process.reallyExit`, and
  deleting `WebAssembly`, which Node's TypeScript stripping needs); both fixed.
- `SECURITY.md` (states plainly: reviewed only by the author and an AI); PyPI metadata; CI package
  job (build, `twine check`, clean-venv smoke test on Linux, macOS, Windows); Windows CI job passes
  (non-executing tests; code-executing commands refuse with the WSL2 message).
- `docs/dry_run/STUDY_DRY_RUN.md`: a pipeline test of `cdebt study` with synthetic fake participants.
- Name check (2026-10-19, read-only): `cdebt` was not registered on PyPI, npm, or as a GitHub user
  or organisation; see `docs/PACKAGING_CHECK.md`.

## 1.0.0rc1

- **TypeScript/JavaScript quiz support** (Node 24+): sandboxed Node runner (permission model, network
  entry points removed), tree-sitter + type-annotation driven runtime probes, constant probes,
  `scip-typescript` call-graph probes; multi-project SCIP indexes merged per repo.
- **Security:** `docs/THREAT_MODEL.md`; repositories are untrusted by default (Docker or strict
  macOS Seatbelt, else refuse; `--trusted`/`CDEBT_TRUSTED=1` opt-out, never read from the repo);
  Python reads restricted to the sandbox root and interpreter, signals and `sqlite3` file opens
  blocked, output and process caps; fuzz suites (Python + Node) found and fixed six holes.
- **Windows:** execution commands refuse with a clear message; Windows CI job added (NOT yet run).
- **Performance/data:** on-disk result cache keyed by commit + blob + provider inputs; quadratic
  blast-radius step fixed; `understanding.json` schema v2 with migrations and `cdebt migrate`.
- **Validity study kit:** `cdebt study init/run/analyze/power`, `docs/STUDY_PROTOCOL.md`
  (pre-registered; not run).
- **Claims audit:** `scripts/check_claims.py` fails CI when a number in the README or launch post
  has no source in the repo.
- Not published; nothing has been released to PyPI or GitHub.

## 0.1.2

- **Understanding now has three states**: *verified* (execution-verified probes), *attested*
  (a human signed off with `cdebt attest`; **NON-VERIFIED**, hash-bound, expires on change,
  counts at `attested_weight` = 0.5 in the debt score) and *none*. Labelled NON-VERIFIED in map,
  gate, plan and the PR comment.
- `cdebt gate --require verified|attested`. Default: attested is accepted for module-level code
  and for functions quiz cannot verify; verified is required for functions quiz could verify.
- New `constant` probes: "what is the value of NAME?" for top-level constants, evaluated in the
  sandbox twice. Verifies constant values only, not other top-level statements.
- New `cdebt coverage`: per top-N debt files, what share of AI lines can be verified, only
  attested, or are in languages with no quiz support (TypeScript/JavaScript).
- **Sandbox timeout is now a hard guarantee** (see `docs/VERIFICATION.md`): deadline on its own
  timer instead of sharing a loop with `ps`; the Docker container is killed at the deadline (not
  just the client); the whole process tree is killed (children that `setsid` used to survive);
  group kill refuses a reused PID; docker cleanup has timeouts; orphaned `cdebt-*` containers are
  reaped; `elapsed` is measured at kill time.
- Fixed: `quiz` printed noisy skip lists, a literal `None` answer was graded wrong,
  runtime probes were rejected at file level (now function level).
- Flake gate: the suite was run 20 times in a row locally with **zero failures**. Runs 1-9 were
  unpaced with the Docker VM up (122 tests, ~80 s each); runs 10-20 were paced (batches of two,
  50 s cooldowns, 120 s before the last; `scripts/repeat_suite_paced.sh`) with the Docker VM off,
  so 8 Docker tests were skipped (~22 s each). The Docker tests are covered by the CI
  `docker-sandbox` job, and CI also has a `repeat-suite` job (20 runs); that job has NOT been run
  on a GitHub runner for this version (the scratch remote was removed).
- The CPU-stress test's burner processes now stop by themselves after 20 s (they had leaked and
  heated the machine when a run was killed).

## 0.1.1

- `cdebt[backtest]` extra (numpy, pyarrow, huggingface_hub) keeps the core install light.
- Git AI notes parser verified against real 1.0.0-1.7.5 output; fixed a crash on empty
  attestation sections; unsupported or malformed notes yield no claims.
- Docker sandbox path (docker cp, no bind mounts), verified locally and on a GitHub runner.
- mutmut removed; cdebt's own mutator is the only mutation backend.
- Matched-pairs backtest (`docs/PREREGISTRATION.md`, `docs/RESULTS_MATCHED.md`): null.
- Pilot on three real repos (`docs/CASE_STUDIES.md`), `docs/VERIFICATION.md`.
- Restored `tests/test_probes.py`, which was never created in 0.1.0 (see below).

## 0.1.0

- First release: map, quiz, gate, plan, backtest; Git AI / Agent Trace / trailer attribution;
  hash-bound expiring understanding; sandboxed execution; AIDev backtest (mostly null).

## History notes (git history is not rewritten)

- **Commit `66aa50b`** is titled "Pre-registration for matched-pairs analysis (committed before
  code or run)" but also contains unrelated work staged with `git add -A`: the Docker sandbox
  rewrite, the Git AI parser changes, the mutmut removal, the CI job and the Git AI version
  fixtures. The pre-registration file itself (`docs/PREREGISTRATION.md`) was still committed
  before any matched-pairs code existed: that code first appears in `bb15606`, two commits later,
  and the analysis was first run after that.
- **`tests/test_probes.py` was missing in 0.1.0**: the command that was supposed to create it was
  chained after a failing `sed`, so the call-graph and mutation probes had no tests when 0.1.0
  was described as verified. The tests were written in 0.1.1 (13 passed; later grown).
