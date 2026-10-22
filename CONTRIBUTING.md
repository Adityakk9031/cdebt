# Contributing to cdebt

cdebt measures, tests and pays down **comprehension debt**: the gap between code that exists and
code a human has verifiably understood. It is early software; read the Status section of the
README first so you know what is and is not validated.

## Ground rules (these are product rules, not style)

- **Never rank or score individuals.** Output is about code and teams. No leaderboards, no stored
  per-person flags, no timing data stored anywhere.
- **Correctness comes only from execution** (sandbox, SCIP call graph, mutation runs, traces). A
  model may propose questions; it never decides whether an answer is right.
- **Label the attribution source** (`git-ai | agent-trace | trailer | none`). `none` means
  unknown, never "human-written".
- **Be honest about limits.** A quiz cannot stop someone pasting questions into an AI. Mark
  anything you could not verify as `UNVERIFIED`, and never invent numbers: every number in the
  docs cites a file (`scripts/check_claims.py`).
- **Sandbox all execution** and keep the repository read-only except `.cdebt/` and
  `refs/notes/cdebt`.

## Development

```bash
uv sync --all-extras
uv run pytest -q && uv run ruff check . && uv run mypy src
```

The full suite is heavy (it runs sandboxed code). On a laptop, run it in chunks with cooldowns:
`bash scripts/chunked_tests.sh 50 6`. Unit tests use throwaway git repos built in the test, and
**no network**. Docker-dependent tests skip unless Docker and `python:3.11-slim` are present.

## Pull requests

- Keep changes small and say what you ran. Show the real output of the commands above.
- Add a test for every behaviour change, and a regression test for every bug.
- Do not add a dependency without raising it first.
- Security issues: do not open a public issue; see [SECURITY.md](SECURITY.md).

By contributing you agree your work is licensed under Apache-2.0.
