# Supported Git AI note formats

cdebt reads `refs/notes/ai` and never writes it. Every row below was produced by **running the
real release binary** (checksum-verified against that release's `SHA256SUMS`) on the same
3-step scenario with the `mock_ai` preset, then committing the resulting notes as fixtures
(`scripts/capture_gitai_versions.sh`, `tests/fixtures/gitai_versions/`). Current format
(1.7.5) fixtures are in `tests/fixtures/gitai/`.

| git-ai | `schema_version` | layout | AI attestation id | explicit human lines | cdebt |
|---|---|---|---|---|---|
| 1.0.0 | `authorship/3.0.0` (no `git_ai_version` key) | `prompts` | 7 hex chars (`1b9f44b`) | no | supported |
| 1.1.1 - 1.3.0 | `authorship/3.0.0` | `prompts` | 16 hex chars | no | supported |
| 1.4.0 - 1.6.0 | `authorship/3.0.0` | `sessions` | `s_<14hex>::t_<14hex>` | no | supported |
| 1.7.0 - 1.7.5 | `authorship/3.0.0` | `sessions` + `humans` | `s_...::t_...` | yes (`h_<14hex>`) | supported |
| 0.1.0, 0.2.0 | n/a | n/a | n/a | n/a | **not captured, not supported** |
| any `authorship/<other major>` | | | | | parsed but **ignored** (no claims) |

Things worth knowing:

- `schema_version` stayed `authorship/3.0.0` across all of these, so it cannot identify the
  layout. cdebt keys off the metadata (`prompts` vs `sessions`/`humans`) and treats any
  non-`h_` id as AI.
- Versions before 1.7 never record human lines. A human line next to AI lines is therefore
  **unknown** under those versions, not human. Only 1.7+ produces `h_` attestations.
- A commit with no attributed lines still gets a note whose attestation section is empty, so
  the note body begins with a bare `---` line. (A parser bug on exactly this case was found
  by this capture and fixed; see `test_real_empty_attestation_notes_parse`.)
- 0.1.0 and 0.2.0 could not be driven by the capture harness (different CLI), so nothing is
  claimed about them.
- Capturing 1.2+ needs git trace2 events delivered to the daemon's Unix socket, which
  has a ~104-byte path limit on macOS; the capture script therefore works under `/private/tmp`.
- Mid-history format changes in one repo are handled per commit: each blamed commit's note is
  parsed independently.

Unsupported or malformed notes are skipped silently (lines stay unknown); the gate and map
still report unknown lines explicitly.
