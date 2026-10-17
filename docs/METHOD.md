# Method

## Attribution (per line)

Priority per line, first claim wins: `git-ai` > `agent-trace` > `trailer` > `none`.
`none` means *no evidence* (unknown); it is never counted as human-written.

- **git-ai** (verified against git-ai 1.7.5, schema `authorship/3.0.0`): a note on each
  commit lists, per file, line ranges in that commit's version of the file, tagged `s_…::t_…`
  (AI session) or `h_…` (explicit human edit). We map HEAD lines through `git blame
  --porcelain` (blamed commit + original line number) and look the line up in that commit's
  note. Lines absent from a note are unknown.
- **agent-trace** (RFC v0.1.0; storage location UNVERIFIED, we read `.agent-trace/*.json[l]`).
- **trailer**: `Co-Authored-By:` naming a known agent. Commit-level, therefore *coarse*
  (marked `*`): human lines in the same commit are over-claimed.

## Understanding

A record binds to `sha256(normalised unit text)`. Units are functions/methods (tree-sitter,
Python and TS/JS) plus one `<module>` residual per file; other languages are one `<file>` unit.
Normalisation: CRLF to LF, trailing whitespace stripped, trailing blank lines dropped.
Comments are included. Any change to a unit's text makes its records *stale*. Optional
`expires_at` adds time-based expiry. An AI line is "understood" iff its unit has a passing,
hash-matching, unexpired record.

## Debt score (`cdebt map`)

```
score = ai_share x (1 - understood_share) x (1 + w_churn x churn) x blast_radius
```

| term | definition |
|---|---|
| `ai_share` | AI lines / total lines in the file (unknown lines are in the denominator, never the numerator, unless `unknown_as_ai`) |
| `understood_share` | understood AI lines / AI lines |
| `churn` | commits touching the file in `churn_days` (default 90), divided by the max over all files, so in [0, 1] |
| `blast_radius` | `log` mode (default): `1 + ln(1 + refs)`; `raw` mode: `max(1, refs)`. `refs` = distinct (file, line) reference sites outside the file to symbols defined in it |

`refs` comes from a SCIP index when present (`blast src = scip`). Otherwise a name-match
fallback is used and labelled `approx`; it over-counts common names and ignores names of
1-2 characters. The `log` default is a deliberate deviation from the raw-count spec so one hub
file cannot dwarf all others; set `blast_mode = "raw"` for the literal formula.

Weights live in `[tool.cdebt]` (pyproject.toml) or `.cdebt/config.toml`.

## Summary statistics

"AI units understood" is a proportion over units that contain AI lines; it carries a Wilson
95% interval and its sample size. The score is a *ranking heuristic*, not a probability.
