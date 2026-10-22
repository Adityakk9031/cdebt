# Public launch playbook (nothing here has been run)

Positioning for a first public release: **an experimental alpha** that gives visibility into
AI-written code nobody has understood. It is not a validated measure (the score is unvalidated,
the backtests were mostly null, the validity study has not been run) and not a lie detector. The
README Status section already says so; keep it on the front page.

## Ready now

| item | state |
|---|---|
| Code, tests, lint, types, claims audit | done (`docs/V1_CHECKLIST.md`) |
| LICENSE (Apache-2.0), SECURITY.md, CHANGELOG, CONTRIBUTING, issue and PR templates | done |
| No secrets in files or history | checked (scan of all commits) |
| Honest README status, limits, threat model | done |
| Local test evidence, real agent run, real Actions flow on a self-hosted runner | done (`docs/VERIFICATION.md`) |

## Decisions only the owner can make

1. **Where it lives.** Which GitHub account or org, and the repo name (`cdebt`). Placeholders
   `<owner>/cdebt@<ref>` in `README.md` and `src/cdebt/init.py` and `[project.urls]` in
   `pyproject.toml` are filled in once this is chosen.
2. **Your commit email.** 19 commits carry a personal email (the other 47 use `dev@cdebt.invalid`).
   Publishing this history makes that email public. Options: accept it; or publish a fresh history
   with a GitHub noreply address (rewrites history; the previous rule was not to, so this needs an
   explicit yes); or set a noreply address for all future commits only.
3. **Package name.** `cdebt` was free on PyPI, npm and GitHub on 2026-10-19 (`docs/PACKAGING_CHECK.md`).
   A related project, `comprehension-debt`, exists. Registering the name needs a PyPI account.
4. **Version label.** Recommended: publish `1.1.0rc1` as a pre-release (pip does not install it
   unless asked with `--pre`) so the first public version cannot be pulled in by accident.
5. **CI on the public repo.** Public repositories get free hosted Actions minutes, which also removes
   the billing blocker. The full CI matrix then needs one manual "Run workflow" before release.

## Steps (in order; each needs the owner's go-ahead)

1. Fill in the placeholders (owner, repo URL, authors) and re-run the checks.
2. Create the public repository and push (`git push` of `main` and the tag).
   Making a private repo public cannot be undone: forks and caches may keep copies.
3. Turn on the repository's security reporting and branch rules in the GitHub settings.
4. Run the full CI once (Actions tab, "Run workflow") and fix whatever it finds.
5. Build and check the package: `uv build && uvx twine check dist/*` (a CI job does this).
6. Upload to PyPI (or TestPyPI first) with the owner's token, as a pre-release.
7. Create a GitHub release from the tag with the CHANGELOG entry; only then tag a stable Action
   ref (`v1`) so `uses: <owner>/cdebt@v1` works.
8. Publish `docs/LAUNCH_POST.md` after re-reading it line by line against the current README.
9. Watch the issues for the first week; treat sandbox reports as security reports.

## After launch

Independent security review of the sandbox, the validity study (`docs/STUDY_PROTOCOL.md`), a real
fork-PR test of the Action, and the codex and cursor adapters (`docs/LIMITS.md`).
