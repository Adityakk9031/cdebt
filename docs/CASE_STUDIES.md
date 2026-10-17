# Case studies: cdebt on 3 real open-source repos that use AI coding agents

Reproduce with `scripts/pilot.sh WORKDIR` then `CDEBT_NO_DOCKER=1 uv run python scripts/pilot_quiz.py WORKDIR docs/case_studies` (raw outputs are in `docs/case_studies/`; gate and map JSON are trimmed to the top 25 rows). Run on 2026-10-18 with cdebt at the commit that added this file.

All three repos credit an AI agent via `Co-Authored-By` trailers, so **attribution is `trailer` (coarse, commit-level) in every case**; none publishes Git AI notes. That is the common real-world situation and it shapes every number below: lines last touched by a trailered commit count as AI (including any human lines in that commit), and lines last touched by a commit without the trailer are `unknown`, never human.

| repo | language | pinned HEAD | files mapped | lines | AI share (trailer) | unknown share | AI lines understood |
|---|---|---|---|---|---|---|---|
| [tachyon-beep/space_chassis](https://github.com/tachyon-beep/space_chassis) | Python | `9a0430a37c` | 135 | 109,942 | 56.8% | 43.2% | 0% |
| [lucascaro/hive](https://github.com/lucascaro/hive) | TypeScript/JS | `d6620e6226` | 434 | 100,974 | 96.8% | 3.2% | 0% |
| [Popoboxxo/agent-meta](https://github.com/Popoboxxo/agent-meta) | Python | `8457634f1e` | 509 | 140,429 | 46.1% | 53.9% | 0% |

Understanding is 0% everywhere, as expected: nobody has run cdebt on these repos before.

## 1. `cdebt map`

Top 5 files by score per repo (`--no-scip`, so blast radius is the `approx` name-match):

**space_chassis**

| file | AI% | unknown% | churn | blast (approx) | score |
|---|---|---|---|---|---|
| `vehicle/tools/console.py` | 96% | 4% | 0.32 | 10.02 | 12.67 |
| `vehicle/tools/episode_eval.py` | 100% | 0% | 0.16 | 9.55 | 11.10 |
| `vehicle/tests/test_rules_seam.py` | 100% | 0% | 0.19 | 9.26 | 11.01 |
| `vehicle/tests/test_interlocks.py` | 100% | 0% | 0.14 | 9.67 | 10.98 |
| `vehicle/tests/test_quiet_coast.py` | 100% | 0% | 0.19 | 9.04 | 10.75 |

**hive**

| file | AI% | unknown% | churn | blast (approx) | score |
|---|---|---|---|---|---|
| `cmd/hivegui/frontend/test/e2e/wails-mock.ts` | 96% | 4% | 1.00 | 8.97 | 17.25 |
| `cmd/hivegui/frontend/test/e2e-real/wails-bridge.ts` | 98% | 2% | 0.66 | 8.99 | 14.67 |
| `cmd/hivegui/frontend/test/dom/settings.test.tsx` | 100% | 0% | 0.38 | 9.15 | 12.65 |
| `cmd/hivegui/frontend/src/app/events.ts` | 97% | 3% | 0.91 | 6.78 | 12.64 |
| `cmd/hivegui/frontend/src/store/store.ts` | 96% | 4% | 0.55 | 8.38 | 12.53 |

**agent-meta**

| file | AI% | unknown% | churn | blast (approx) | score |
|---|---|---|---|---|---|
| `tests/test_release_gate_checksums.py` | 100% | 0% | 0.03 | 9.37 | 9.64 |
| `tests/test_antigravity_hooks_registration.py` | 99% | 1% | 0.03 | 9.37 | 9.57 |
| `scripts/lib/consistency/se_cascade.py` | 100% | 0% | 0.01 | 8.98 | 9.11 |
| `tests/test_doctor_cli.py` | 99% | 1% | 0.03 | 8.78 | 8.96 |
| `tests/test_doctor_runner.py` | 99% | 1% | 0.03 | 8.73 | 8.92 |

With a real SCIP index (`cdebt index`, scip-python) on agent-meta, blast radius comes from exact references and the ranking changes a lot:

| rank | `--no-scip` (approx) | with SCIP |
|---|---|---|
| 1 | `tests/test_release_gate_checksums.py` (9.64) | `scripts/lib/consistency/report.py` (7.13, blast 7.34) |
| 2 | `tests/test_antigravity_hooks_registration.py` (9.57) | `scripts/lib/log.py` (6.95, blast 7.85) |
| 3 | `scripts/lib/consistency/se_cascade.py` (9.11) | `scripts/lib/external_tools.py` (6.41, blast 5.74) |
| 4 | `tests/test_doctor_cli.py` (8.96) | `scripts/lib/cli_commands.py` (6.21, blast 5.14) |
| 5 | `tests/test_doctor_runner.py` (8.92) | `scripts/lib/config_audit_types.py` (6.16, blast 6.07) |

Overlap of the two top-25 lists: 5/25. Approx blast radius is dominated by common-name matches (a file full of `test_*` helpers or generic names scores high), which is exactly why `blast src` is printed in the table.

## 2. `cdebt gate --base HEAD~25`

| repo | passed | AI lines in un-understood units | violating units | unknown changed lines | diffstat of those 25 commits (from an earlier clone; space_chassis HEAD has moved since) |
|---|---|---|---|---|---|
| space_chassis | False | 62,412 | 3,250 | 47,530 | 589 files, +188,091/-10,459 |
| hive | False | 5,657 | 224 | 0 | 207 files, +19,922/-515 |
| agent-meta | False | 17,180 | 1,348 | 3,205 | 635 files, +52,690/-2,829 |

- space_chassis: 16 of the top 25 violations are module-level (`<module>`) units.
- hive: 19 of the top 25 violations are module-level (`<module>`) units.
- agent-meta: 24 of the top 25 violations are module-level (`<module>`) units.

## 3. `cdebt plan` (top 5 per repo)

| repo | # | file | action | est. min | unquizzable AI lines |
|---|---|---|---|---|---|
| space_chassis | 1 | `aurora/tests/trace_console.py` | quiz | 7.5 | 37 |
| space_chassis | 2 | `aurora/tests/read_probe_trace.py` | quiz | 9.2 | 28 |
| space_chassis | 3 | `vehicle/tests/test_keyed_states.py` | review | 10.8 | 47 |
| space_chassis | 4 | `vehicle/tests/test_state_level.py` | review | 13.8 | 28 |
| space_chassis | 5 | `aurora/scripts/build_console_seed.py` | quiz | 14.4 | 53 |
| hive | 1 | `cmd/hivegui/frontend/test/fixtures/plugins/ui-hang-activate/ui.mjs` | review | 2.1 | 0 |
| hive | 2 | `scripts/workflow-proto/emit.test.ts` | review | 5.2 | 34 |
| hive | 3 | `cmd/hivegui/frontend/test/fixtures/plugins/ui-throws/ui.mjs` | review | 4.5 | 0 |
| hive | 4 | `cmd/hivegui/frontend/src/components/Kbd.tsx` | review | 4.2 | 3 |
| hive | 5 | `cmd/hivegui/frontend/test/fixtures/plugins/ui-hang-import/ui.mjs` | review | 4.1 | 1 |
| agent-meta | 1 | `scripts/_migrations/2026-09-08-inject-auto-commit-block.py` | review | 8.3 | 39 |
| agent-meta | 2 | `tests/test_context_user_notes_preserved.py` | quiz | 12.1 | 30 |
| agent-meta | 3 | `tests/manual/measure_tokens.py` | quiz | 10.6 | 36 |
| agent-meta | 4 | `tests/routing-llm-eval/list_cases.py` | quiz | 8.5 | 36 |
| agent-meta | 5 | `tests/test_frontmatter_collision_warning.py` | quiz | 11.9 | 20 |

## 4. `cdebt quiz`

Probe coverage over the 25 highest-scoring Python files per repo (what fraction can be quizzed at all):

| setup | files checked | files with >= 1 probe | probes generated |
|---|---|---|---|
| space_chassis | 25 | 10 | {'runtime': 37} |
| agent-meta | 25 | 7 | {'runtime': 25} |
| agent-meta+scip | 25 | 11 | {'runtime': 21, 'callgraph': 28} |

Top skip reasons (space_chassis): unsupported signature x339, skipped x184, type Any x36, missing type hint x10.  (agent-meta): unsupported signature x226, skipped x89, type dict x15, type SEDoc x14.

One quiz session (`scripts/lib/io.py` in agent-meta; scripted answerer: all correct except the first multi-select question, which is deliberately wrong). Full transcript with every skip reason: `docs/case_studies/quiz-session.txt`.

```
$ cdebt quiz scripts/lib/io.py --n 6 --seed 0 --kinds runtime,callgraph --answers <scripted> --label pilot-audit
pathlib (side effects); uses tempfile (side effects))
(cannot ask)
(cannot ask)
sandbox: audit+rlimit+sandbox-exec
_write_yaml: wrong - answer was ["scripts/lib/config.py", 
"scripts/lib/sync_pipeline.py", "tests/test_se_persistence.py"] (verified by 
scip-index)
_normalize_enabled_config: correct - answer was ["scripts/lib/agent_sync.py", 
"scripts/lib/registry_query.py", "scripts/lib/skills.py"] (verified by 
scip-index)
_load_yaml_or_json: correct - answer was ["scripts/lib/mcp_provider_config.py", 
"scripts/lib/viz.py", "tests/test_se_persistence.py"] (verified by scip-index)
_deep_merge: correct - answer was ["scripts/lib/conventions.py", 
"scripts/lib/registry_query.py"] (verified by scip-index)
clear_gitignore_cache: correct - answer was None (verified by sandbox-exec)
_validate_on_error: correct - answer was raises ValueError (verified by 
sandbox-exec)
_write_yaml: 0/1 -> not yet (bound to hash 472c52caaa)
_normalize_enabled_config: 1/1 -> UNDERSTOOD (bound to hash 5895b181c3)
_load_yaml_or_json: 1/1 -> UNDERSTOOD (bound to hash 6831ead70c)
_deep_merge: 1/1 -> UNDERSTOOD (bound to hash 9ffdfdb52d)
clear_gitignore_cache: 1/1 -> UNDERSTOOD (bound to hash f1475ce7e2)
_validate_on_error: 1/1 -> UNDERSTOOD (bound to hash 7e9184c694)
Quizzes show visibility, not proof: they cannot stop answers being looked up.
```

The wrong answer is recorded as `0/1 -> not yet`; the other five units are bound to their current hashes. (An earlier run of this session surfaced a real bug: a function returning `None` had the literal answer `None`, which was graded wrong; fixed with a regression test.)

## 5. Timings (this MacBook Air, M4)

| repo | map | gate | plan |
|---|---|---|---|
| space_chassis | 0m24.938s | 0m12.415s | 0m36.978s |
| hive | 0m29.395s | 0m3.899s | 0m42.157s |
| agent-meta | 0m53.689s | 0m9.851s | 1m9.698s |

agent-meta SCIP: `cdebt index` 0m15.676s (npx cache warm; the first run took about two minutes including the download), map with SCIP 0m27.877s.

## 6. What looked wrong or confusing

1. **Quiz coverage is narrow.** Only 10/25 and 7/25 of the top files yield any runtime probe; the dominant skip reason by far is `unsupported signature` (no return annotation, keyword-only, `*args`, or non-simple parameter types), then functions that use `pathlib`/`os`/`sys`. With a SCIP index, call-graph probes raise agent-meta to 11/25. TypeScript (`hive`) cannot be quizzed at all, so every plan item there is `review`.
2. **The screen was first far too strict.** The initial file-level static screen rejected any file that imported `os`/`sys`/`pathlib`: **1/25 and 0/25** files produced probes. Moving to function-level screening (the audit hook and OS sandbox still contain actual side effects) raised that to the numbers above. This was found only by running on real code.
3. **Module-level code can never be cleared.** The top gate violations are often `<module>` units (imports, constants, top-level statements, test modules), and `quiz` has no probes for them. Those lines keep failing the gate until the code changes or `--max-ai-lines` is raised. The gate now prints a note about this. A real fix needs a different mechanism (for example an explicit, clearly labelled non-verified attestation); that is a design decision and is **not implemented**.
4. **Docker lowers coverage.** When Docker is available it is used automatically, but `python:3.11-slim` lacks the project's dependencies: on agent-meta 4/25 files had probes under Docker vs 7/25 with the host sandbox (`CDEBT_NO_DOCKER=1`). The quiz now prints which sandbox was used.
5. **Trailer attribution is coarse and lopsided.** 96-100% AI per file in the top rows is an upper bound (human edits in trailered commits count as AI), while 43-54% of lines in two repos are `unknown` because their last-touching commit had no trailer. Neither number says how much a human actually understands.
6. **Gate windows are huge on agent-driven repos.** 25 commits changed 20k-190k lines, so a gate with `--base HEAD~25` is effectively a whole-repo audit (tens of thousands of AI lines, thousands of violating units). Per-PR gating, the intended use, was tested separately on a scratch PR (see `docs/VERIFICATION.md`), not on these repos.
7. **`plan` ranks tiny files first.** priority = score / effort favours small files with high churn/AI share (even test fixtures such as `ui.mjs` in hive). Defensible as 'cheapest debt to pay', but it is not 'most important'; the heuristic effort model (30 lines/min, 2 min/unit) is a guess.
8. **`plan` and `map` repeat the same work.** `plan` recomputes attribution (git blame over every file), so it takes about 1.5x the time of `map` (up to 70 s here). Not a correctness issue.
9. **Noisy skip output.** The first quiz run printed ~30 skip lines per file. The default is now a one-line count; `--verbose` lists them.
10. **Nothing here validates the score.** These runs show the tool operating on real repos, not that high-score files are actually riskier; see the backtest results for that (mostly null).
