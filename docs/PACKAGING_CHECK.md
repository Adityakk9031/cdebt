# Name and packaging check (read-only; nothing was uploaded)

Checked on 2026-10-19 with plain HTTP requests and the GitHub CLI. A 404 today does not reserve a
name: registering it on PyPI is what reserves it, and that has **not** been done.

## Is `cdebt` free?

| registry | result |
|---|---|
| PyPI `cdebt` | not registered (HTTP 404 from `https://pypi.org/pypi/cdebt/json`) |
| npm `cdebt` | not registered (HTTP 404) |
| GitHub user or organisation `cdebt` | does not exist (HTTP 404) |
| GitHub repositories with `cdebt` in the name | none that is related: `suchat-kl/CdebtLinuxApi`, `serverlus/1CDebt`, `openbankruptcyproject/1099cdebt.com` (a tax page about cancellation of debt, "1099-C"), and unrelated small repos |

Other PyPI names checked and **free** at the same time: `comprehensiondebt`, `cdebt-cli`, `cdebt-ai`,
`cdebt-tool`, `c-debt`, `c_debt`, `codedebt-ai`, `comprehension-debt-toolkit`.

## Things to know about the name

- **A related project already exists on PyPI as `comprehension-debt`** (version 0.13.2 when
  checked; summary: "Measure, and catch at creation, code your team no longer understands"). It is
  not named `cdebt`, but it covers the same idea, so expect overlap in search results and make the
  README's differences clear (execution-verified questions, attribution sources, expiring records,
  backtests and their null results).
- In finance "C-debt" / "cancellation of debt" is a common phrase (tax form 1099-C), so the bare
  word `cdebt` may be noisy to search for. `cdebt` also normalises to the same PyPI name as `c-debt`
  and `c_debt`.
- Whether to publish under this name, and the PyPI project owner, are decisions for you.

## Packaging

- `uv build` produces `cdebt-<version>.tar.gz` and a pure-Python wheel; `twine check` passes on
  both (also run in CI on Linux, macOS and Windows: job `package`).
- The sdist contains no data, logs, `.cdebt` caches or virtualenvs (checked); the wheel does not
  contain the tests.
- A clean-venv install and smoke test (`scripts/smoke_install.py`) runs in CI on all three
  operating systems and passed locally on macOS. The core install pulls only pydantic, typer, rich
  and tree-sitter packages; the backtest extra (`numpy`, `pyarrow`, `huggingface_hub`) is optional.
- Missing and left for you: project URLs (the repository is private), authors, and any decision on
  versioning beyond `1.0.0rc2`.
