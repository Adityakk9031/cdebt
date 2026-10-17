"""Git AI notes reader (refs/notes/ai). Verified against REAL notes from git-ai 1.0.0 through
1.7.5 (tests/fixtures/gitai/ and tests/fixtures/gitai_versions/; see docs/GITAI_FORMATS.md).

Note layout (VERIFIED, all versions):

    <file path>                      # unindented; the whole section is empty if nothing attributed
      <attestation-id> <ranges>      # indented; ranges like "3-7" or "2-3,6"
    ---                              # a line of exactly three dashes (FIRST line when empty)
    { JSON metadata }

`schema_version` is "authorship/3.0.0" in every captured version, so it cannot tell layouts
apart; the metadata keys can: `prompts` (1.0-1.3, bare hex prompt-hash ids) vs `sessions`
(1.4+, `s_<id>::t_<id>` ids; 1.7+ also `humans` with `h_<id>` explicit human edits).
Line numbers are positions in the file AT THAT COMMIT, so HEAD lines are mapped through
`git blame --porcelain` (blamed commit + original line). Lines absent from a note are
*unknown* (git-ai calls them untracked), never "human". Notes whose schema is not
`authorship/3.x`, or that fail to parse, yield no claims.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from cdebt import gitutil
from cdebt.gitutil import BlameLine
from cdebt.models import AttributionSource, AuthorKind

SUPPORTED_SCHEMA_PREFIX = "authorship/3."
_DIVIDER = re.compile(r"^---[ \t]*$", re.MULTILINE)


@dataclass
class ParsedNote:
    # path -> list of (attestation_id, [(start, end)])
    files: dict[str, list[tuple[str, list[tuple[int, int]]]]] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
    malformed: bool = False

    @property
    def schema_version(self) -> str:
        return str(self.meta.get("schema_version", ""))

    @property
    def supported(self) -> bool:
        """Only `authorship/3.x` notes are interpreted; anything else yields no claims."""
        return self.schema_version.startswith(SUPPORTED_SCHEMA_PREFIX) and not self.malformed

    @property
    def layout(self) -> str:
        """`sessions` (1.7+: s_..::t_.. and h_.. ids), `prompts` (1.0-1.6: bare prompt-hash
        ids), or `empty`. NB: schema_version says `authorship/3.0.0` in ALL of them."""
        if "sessions" in self.meta or "humans" in self.meta:
            return "sessions"
        return "prompts" if self.meta.get("prompts") else "empty"


def parse_ranges(spec: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a), int(b)))
        else:
            out.append((int(part), int(part)))
    return out


def parse_note(text: str) -> ParsedNote:
    # The divider is a line of exactly `---`. It is the FIRST line when the attestation section
    # is empty (verified: git-ai 1.0.0-1.7.x notes for commits with no attributed lines).
    m = _DIVIDER.search(text)
    head, sep, tail = (text[: m.start()], True, text[m.end() :]) if m else (text, False, "")
    note = ParsedNote()
    if sep:
        try:
            meta = json.loads(tail)
            if isinstance(meta, dict):
                note.meta = meta
        except json.JSONDecodeError:
            pass
    cur: str | None = None
    for raw in head.splitlines():
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")):
            if cur is None:
                continue
            ident, _, spec = raw.strip().partition(" ")
            try:
                note.files[cur].append((ident, parse_ranges(spec)))
            except ValueError:
                note.malformed = True
        else:
            cur = raw.strip().strip('"')
            note.files.setdefault(cur, [])
    return note


def classify(ident: str) -> AuthorKind:
    if ident.startswith("h_"):
        return AuthorKind.HUMAN
    return AuthorKind.AI  # s_...::t_..., p_..., legacy prompt hash (UNVERIFIED legacy)


def line_kinds(note: ParsedNote, path: str, n_lines: int = 0) -> dict[int, AuthorKind]:
    out: dict[int, AuthorKind] = {}
    for ident, ranges in note.files.get(path, []):
        kind = classify(ident)
        for a, b in ranges:
            for ln in range(a, b + 1):
                out[ln] = kind
    return out


class GitAiProvider:
    source = AttributionSource.GIT_AI

    def __init__(self, repo: Path, ref: str = "ai") -> None:
        self.repo, self.ref = repo, ref
        self._cache: dict[str, ParsedNote | None] = {}

    def _note(self, sha: str) -> ParsedNote | None:
        if sha not in self._cache:
            txt = gitutil.note(self.repo, sha, self.ref)
            self._cache[sha] = parse_note(txt) if txt else None
        return self._cache[sha]

    def claims(
        self, path: str, rev: str, blame: list[BlameLine]
    ) -> list[tuple[AuthorKind, bool] | None]:
        per_commit: dict[tuple[str, str], dict[int, AuthorKind]] = {}
        out: list[tuple[AuthorKind, bool] | None] = []
        for bl in blame:
            key = (bl.sha, bl.path or path)
            if key not in per_commit:
                note = self._note(bl.sha)
                per_commit[key] = (
                    line_kinds(note, key[1]) if note is not None and note.supported else {}
                )
            kind = per_commit[key].get(bl.orig)
            out.append((kind, False) if kind else None)
        return out


def stats_json(repo: Path, commit: str = "HEAD") -> dict[str, object] | None:
    """Optional: `git-ai stats --json` (read-only). Returns None if git-ai is absent."""
    import shutil
    import subprocess

    exe = shutil.which("git-ai") or str(Path.home() / ".git-ai" / "bin" / "git-ai")
    if not Path(exe).exists():
        return None
    p = subprocess.run([exe, "stats", commit, "--json"], cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
