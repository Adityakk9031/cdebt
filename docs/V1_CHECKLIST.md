# v1.0 release checklist (release candidate 1)

Status key: **done** (with evidence), **needs-human** (a person must act or decide), **blocked**
(could not be done in this environment; reason given). Nothing has been published.

## A. Product claims (the four differentiators)

| bar | status | evidence |
|---|---|---|
| Quiz answers come from execution / call graph / mutation, never a model | done | `tests/test_probes.py` (Python), `tests/test_js_probes.py` (TypeScript/JavaScript, cross-checked against a plain `node` evaluation), `tests/test_attest.py` (constants); grading is a rule (`cdebt.probes.base.grade`, HMAC in `cdebt.study`) |
| Real line-level attribution from Git AI, verified on real output | done | `docs/GITAI_FORMATS.md`: real binaries 1.0.0-1.7.5, fixtures in `tests/fixtures/gitai*`, `tests/test_gitai_versions.py` |
| Understanding expires with the code (hash-bound) | done | `tests/test_store.py`, `tests/test_attest.py::test_attestation_is_hash_bound`, `tests/test_gate.py` |
| Backtest reproducible and published as measured | done | `docs/RESULTS.md`, `docs/RESULTS_MATCHED.md`, pre-registrations `docs/METHOD.md` and `docs/PREREGISTRATION.md` (committed before the runs); results are **mostly null** |
| The score predicts real trouble | **not shown** | the two backtests above; see README "What we know and don't know". This is a finding, not a pending task |
| Quiz questions measure understanding | **not shown** | study kit and pre-registered protocol exist (`docs/STUDY_PROTOCOL.md`); **needs-human: run it** |

## B. Security

| bar | status | evidence |
|---|---|---|
| Threat model written | done | `docs/THREAT_MODEL.md` |
| Sandbox escape tests (writes, network, fork bomb, loops, memory) | done | `tests/test_sandbox.py`, `tests/test_sandbox_node.py`, `tests/test_sandbox_kill.py` |
| Fuzz / property suite, findings fixed | done | `tests/test_sandbox_fuzz.py`, `tests/test_sandbox_node_fuzz.py`; six real holes found and fixed (listed in the threat model section 5); soak run with a second seed passed |
| Untrusted repos get strong isolation by default, `--trusted` opt-out, trust never read from the repo | done | `tests/test_trust_policy.py`; strict Seatbelt verified on macOS |
| Docker isolation path verified against the hardened code | done | `tests/test_sandbox_docker.py` (Python and Node images) passed locally via Colima, no leaked containers |
| Independent security review | **needs-human** | nobody but the author has reviewed the sandbox |
| Docker/Linux host-mode isolation on a GitHub runner for this version | **blocked** | pushing to a scratch repo to run CI was denied as a publication ("do not publish"); the earlier Docker CI run was for 0.1.1 |
| Hostile-native-code defence | not a goal | documented as a residual risk |

## C. Platforms

| bar | status | evidence |
|---|---|---|
| macOS: full suite, ruff, mypy, clean-venv install | done | local runs below; wheel installs in a fresh Python 3.11 venv, demo passes |
| Linux: clean-venv install and demo | done | `python:3.11-slim` container (aarch64): installed the wheel, ran the demo, same output as macOS |
| Linux: full test suite and CI matrix | **blocked** | needs a CI run (publication denied); the workflow `.github/workflows/ci.yml` is written (Ubuntu/macOS x Python 3.11-3.13, Node 24) |
| Windows | **blocked** + documented | `docs/LIMITS.md`: code-executing commands are unsupported and exit with a clear message (`tests/test_windows_message.py` runs on every OS); a Windows CI job is written but **has never run**; **needs-human: run CI on a Windows runner** |

## D. Quality gates

| bar | status | evidence |
|---|---|---|
| ruff clean | done | `uv run ruff check .` |
| mypy strict clean | done | `uv run mypy src` |
| Full suite 20 times in a row, zero failures | FLAKE_STATUS |
| Claims audit (every number in README and launch post has a source) | done | `scripts/check_claims.py`, `tests/test_claims.py`, CI step; README and `docs/LAUNCH_POST.md` pass |
| Schema versioning and migrations | done | `tests/test_store_migration.py` (real v1 fixture, backup, newer schema refused) |
| Performance at 500-file scale | done | `docs/case_studies/perf.json`: `map` 53.7 s to 9.8 s warm, `plan` 69.7 s to 18.8 s |

## E. Coverage of real code (what users will see)

| bar | status | evidence |
|---|---|---|
| Users can see limits before trusting the gate | done | `cdebt coverage` (`tests/test_coverage.py`) |
| TypeScript/JavaScript quizzable | done, limited | `docs/CASE_STUDIES.md` section 8: 0% to 33% verifiable on the pilot TypeScript repo, mostly call-graph questions; React/npm-heavy code stays attest-only |
| Mutation probes for TypeScript | not done | Python-only; documented |

## F. Release mechanics

| bar | status | evidence / next step |
|---|---|---|
| Wheel builds | done | `uv build` produces `cdebt-1.0.0rc1` |
| Public repository, PyPI publish, release tag on a remote | **needs-human** | not published by design |
| GitHub Action re-verified on a real PR for this version | **needs-human** | verified on 0.1.0 code (`docs/VERIFICATION.md`); re-run after publishing |
| Launch post | **needs-human** | `docs/LAUNCH_POST.md` is a draft; decide whether and where to publish |
| Cleanup of scratch repos, Docker VM, third-party app | **needs-human** | `docs/CLEANUP.md` (commands not run) |
| Feedback from teams outside the author | **needs-human** | none yet |

## G. Known gaps that remain after rc1 (not blockers by themselves, but say so in the release notes)

- Quiz coverage on real code is narrow, and most clearing will be NON-VERIFIED attestation.
- The debt score is unvalidated (mostly null backtests); the study has not been run.
- Windows and Linux CI have not been run for this version.
- Attribution is usually coarse (commit trailers); Git AI notes are rare in practice.
- `index` runs npm-distributed tools on the repository and is not sandboxed.
