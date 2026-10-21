# Release checklist (v1.1.0 release candidate 1)

Status key: **done** (with evidence), **needs-human**, **blocked** (could not be done here; reason
given). Nothing has been published: the repository is private, nothing is on PyPI, no public release.

## v1.1 additions (this round)

| bar | status | evidence |
|---|---|---|
| Agent-proposed questions, verified by execution (MCP server, adapters) | done with fake agent; **real agent UNVERIFIED** | `tests/test_agents.py`, `tests/_fake_agent.py`; the real Claude CLI accepted the flags but was not logged in (`docs/VERIFICATION.md`). codex / cursor adapters UNVERIFIED |
| Agent never sees an answer; cannot pick itself from a repo | done | `test_end_to_end_with_a_fake_mcp_agent` (scans the session dir), `test_user_config_roundtrip_and_never_from_the_repo` |
| Adaptive quiz, timed follow-ups, 3-round cap, no timing persisted | done | `tests/test_adaptive.py`; a real pty run showed the countdown and follow-ups (`docs/VERIFICATION.md`) |
| Pre-push hook: quiz on the terminal, notes pushed before the branch, never blocks, no-TTY notice, `--no-verify` caught by CI logic | done locally | `tests/test_prflow.py` (real git + bare remote), real run on PR 3 / PR 4 |
| `cdebt init` (consent, never overwrites, chains hooks, rulesets) | done with a fake `gh`; **real GitHub ruleset UNVERIFIED** | `tests/test_prflow.py`; private repos need a paid plan for rulesets |
| CI re-execution rejects hand-edited records | done | `test_recheck_accepts_real_records_and_rejects_hand_edited_ones`; run locally on PR 3 / PR 4 |
| Sticky comment names no person; check run `cdebt / gate` | comment done locally; **check run UNVERIFIED** | PR 3 / PR 4 comments; a personal token cannot create check runs |
| **CI green on Linux, macOS, Windows for this version** | **blocked** | GitHub refused to start any job: billing / spending limit (`docs/VERIFICATION.md`). Needs-human: fix billing, re-run CI |
| **GitHub-hosted Action run (comment edit, check run, `/cdebt recheck`)** | **blocked** | same reason; the script was run locally instead |
| Local full suite 20 times in paced pairs (macOS) | done | 20 of 20 passed (`docs/VERIFICATION.md`); the Linux `repeat-suite` job has not run for this version |
| Real fork test, independent security review, validity study, name/publish decisions, `docs/CLEANUP.md` | **needs-human** | unchanged from the previous round |
| `scripts/action_run.sh` ignores API error bodies | done | checked locally against a missing PR and PR 4 |
| Full CI matrix (macOS, Windows, 20x repeat-suite) | **needs-human** | now manual (`workflow_dispatch`) to save free minutes; run it once billing or the monthly quota allows |

Everything below is the previous release candidate's checklist (1.0.0rc2) and is unchanged except
where it says "CI": its CI evidence is for that version only.


Status key: **done** (with evidence), **needs-human** (a person must act or decide), **blocked**
(could not be done in this environment; reason given). Nothing has been published: the repository
is private, nothing is on PyPI, there is no public release or stable Action ref.

## A. Product claims (the four differentiators)

| bar | status | evidence |
|---|---|---|
| Quiz answers come from execution / call graph / mutation, never a model | done | `tests/test_probes.py` (Python), `tests/test_js_probes.py` (TypeScript/JavaScript, cross-checked against plain `node`), `tests/test_attest.py` (constants); grading is a rule (`cdebt.probes.base.grade`, HMAC in `cdebt.study`) |
| Real line-level attribution from Git AI, verified on real output | done | `docs/GITAI_FORMATS.md`: real binaries 1.0.0-1.7.5, fixtures in `tests/fixtures/gitai*`, `tests/test_gitai_versions.py` |
| Understanding expires with the code (hash-bound) | done | `tests/test_store.py`, `tests/test_attest.py::test_attestation_is_hash_bound`, `tests/test_gate.py` |
| Backtest reproducible and published as measured | done | `docs/RESULTS.md`, `docs/RESULTS_MATCHED.md`, pre-registrations `docs/METHOD.md`, `docs/PREREGISTRATION.md`; results are **mostly null** |
| The score predicts real trouble | **not shown** | the two backtests above; a finding, not a pending task |
| Quiz questions measure understanding | **not shown** | the study has **not been run**; kit and protocol exist (`docs/STUDY_PROTOCOL.md`); pipeline dry run on fake data only (`docs/dry_run/STUDY_DRY_RUN.md`, not evidence). **needs-human: run the study** |

## B. Security

| bar | status | evidence |
|---|---|---|
| Threat model written | done | `docs/THREAT_MODEL.md` |
| Sandbox escape tests (writes, network, fork bomb, loops, memory) | done | `tests/test_sandbox.py`, `tests/test_sandbox_node.py`, `tests/test_sandbox_kill.py` |
| Fuzz suites, findings fixed | done | `tests/test_sandbox_fuzz.py`, `tests/test_sandbox_node_fuzz.py` |
| Second adversarial pass (new payload classes), findings fixed, regression tests | done | `tests/test_sandbox_redteam.py`; findings and residual risks in `docs/THREAT_MODEL.md` (section 5b). One of my own hardening changes broke the Node runner and was caught by Linux CI (`docs/VERIFICATION.md`) |
| Untrusted repos get strong isolation by default; `--trusted` opt-out; trust never read from the repo | done | `tests/test_trust_policy.py` |
| Docker path verified | done | `docker-sandbox` CI job (run 39302371977) and local Colima runs (`docs/VERIFICATION.md`) |
| **Independent security review** | **needs-human** | reviewed only by its author and AI, not independently (`SECURITY.md`) |
| Hostile-native-code defence | not a goal | documented residual risk |

## C. Platforms

| bar | status | evidence |
|---|---|---|
| macOS: CI (3.11-3.13) | done | run 39302371977, `docs/VERIFICATION.md` |
| Linux: CI (3.11-3.13), Docker job | done | same run |
| Windows | **unsupported** for anything that executes code | exits with the WSL2 message (`tests/test_windows_message.py`). The Windows CI job (non-executing tests, ruff, mypy, package smoke) passes: same run. Real-world use on Windows untested |

## D. Quality gates

| bar | status | evidence |
|---|---|---|
| ruff clean | done | `uv run ruff check .` (rerun before tagging) |
| mypy strict clean, also `--platform win32` | done | `uv run mypy src` (rerun before tagging) |
| Flake gate: full suite 20 times in a row | done | CI `repeat-suite`: 20 consecutive Ubuntu runs, success (run 39302371977). Local paced macOS run: 20 of 20 passed, 0 failed (runs 1-13 on commit 10bd203, runs 14-20 on 3b0eb0d after a pause caused by the laptop sleeping; details in `docs/VERIFICATION.md`) |
| One load-dependent test flake seen in CI (macOS, once) | done, cause UNVERIFIED | `test_runtime_is_seeded` now checks determinism of probes present in both runs and that drops are reported skips |
| Claims audit | done | `scripts/check_claims.py README.md docs/LAUNCH_POST.md` (rerun before tagging), `tests/test_claims.py`, CI step |
| Schema versioning and migrations | done | `tests/test_store_migration.py` |
| Performance at 500-file scale | done | `docs/case_studies/perf.json` |

## E. Coverage of real code

| bar | status | evidence |
|---|---|---|
| Users can see limits before trusting the gate | done | `cdebt coverage` (`tests/test_coverage.py`) |
| TypeScript/JavaScript quizzable | done, limited | `docs/CASE_STUDIES.md`; most clearing on real repos will be NON-VERIFIED attestation |
| Mutation probes for TypeScript | not done | Python-only; documented |

## F. Release mechanics

| bar | status | evidence / next step |
|---|---|---|
| Wheel and sdist build, `twine check`, clean-venv install | done | CI `package` job on Linux, macOS, Windows; `docs/PACKAGING_CHECK.md`; nothing uploaded |
| Name `cdebt` free on PyPI, npm and GitHub | done (read-only check) | `docs/PACKAGING_CHECK.md`: free today; a related project `comprehension-debt` exists on PyPI. **needs-human: decide the name and register it** |
| GitHub Action re-verified on a real PR for this version | done | PR 1: comment created, then the same comment edited (`docs/VERIFICATION.md`) |
| Action from a real fork | **needs-human** | only a read-only-token simulation was run (PR 2); a real fork needs a second account |
| Public repository, PyPI publish, stable Action ref, release | **needs-human** | not done by design |
| Launch post | **needs-human** | `docs/LAUNCH_POST.md` is a draft; decide whether and where to publish |
| Cleanup of scratch repos, Docker VM, third-party apps | **needs-human** | `docs/CLEANUP.md` (commands not run) |
| Feedback from teams outside the author | **needs-human** | none yet |

## G. Known gaps (say so in any release notes)

- The debt score is unvalidated and the backtests are mostly null; the validity study has not been run.
- Quiz coverage on real code is narrow; most clearing will be NON-VERIFIED attestation.
- Windows cannot run anything that executes code.
- The sandbox has had no independent review.
- Attribution is usually coarse (commit trailers); Git AI notes are rare in practice.
- `index` runs npm-distributed tools on the repository and is not sandboxed.
