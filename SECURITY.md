# Security policy

## Review status (read this first)

**This tool, and especially its sandbox, was reviewed only by its author and an AI model. It has
not been reviewed independently.** It executes code from the repositories you point it at
(`quiz`, `coverage`, `attest`, `study init`). Treat it as pre-release software and read
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) before running it on code you do not trust.

## What to do if you run it on untrusted code

- Leave the default: repositories are untrusted, so probes run in Docker or, on macOS, under a
  strict Seatbelt profile, or not at all. Do not pass `--trusted` / `CDEBT_TRUSTED=1` for a
  repository you did not write.
- Run it on a machine or CI job with no secrets, and prefer a throwaway VM or container.
- The sandbox is containment against accidental and AI-written side effects. It is **not** a
  defence against a determined attacker with native-code or interpreter exploits.
- `cdebt index` runs npm-distributed indexers on the repository and is not sandboxed.
- Windows is unsupported for anything that executes code.

## Reporting a vulnerability

Please report privately, not in a public issue: use the repository's private vulnerability
reporting (GitHub: Security, then "Report a vulnerability") or contact the maintainers directly.
Include the payload or steps; `tests/_fuzz_worker.py` runs one payload through the sandbox in a
disposable process and is the easiest way to reproduce an escape. There is no bounty and no
response-time guarantee.

## Scope

In scope: sandbox escapes (writes, reads outside the sandbox, network, signals to other processes,
process creation, resource exhaustion of the host), the trust policy (anything that lets a
repository mark itself trusted), reading or leaking secrets, and forged-evidence bypasses of the
`--require verified` gate. Out of scope: a hostile Docker daemon or kernel, attacks that need
`--trusted` on a hostile repository, and the (documented) fact that `.cdebt/understanding.json` is
self-asserted and can be edited by whoever opens a pull request.

## Supported versions

Pre-release only (1.0.0 release candidates). There are no supported stable releases yet.

## Secrets and quiz records

cdebt copies the project into a temporary folder before running any of its code. Files that
usually hold credentials (`.env*`, private keys, `*.pem`/`*.key`, credential and secrets files,
`.aws/`, `.ssh/` and similar) are never copied, even if git would include them, and a question
whose answer looks like a secret is dropped, because answers are stored in `refs/notes/cdebt` and
pushed. This is pattern matching and can miss an unusual secret format: keep secrets out of the
repository, and treat the notes like any other pushed data.
