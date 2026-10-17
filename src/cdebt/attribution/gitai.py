"""Git AI notes reader (refs/notes/ai). Format verified against git-ai 1.7.5 output,
see tests/fixtures/gitai/ (authorship/3.0.0).

Note layout (VERIFIED on real output):

    <file path>                      # unindented
      <attestation-id> <ranges>      # indented; ranges like "3-7" or "2-3,6"
    ---
    { JSON metadata: schema_version, sessions/prompts/humans, ... }

Attestation ids: `s_<id>::t_<id>` (AI session/trace) and `p_`/legacy 16-hex prompt hashes
are AI; `h_<id>` is an explicitly recorded human edit. Line numbers are positions in the
file AT THAT COMMIT, so we map HEAD lines through `git blame` (orig line + blamed commit).
Lines absent from the note are *unknown* (git-ai calls them untracked).
UNVERIFIED: legacy (pre-3.0.0) prompt-hash ids are treated as AI; no real sample captured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cdebt import gitutil
from cdebt.gitutil import BlameLine
from cdebt.models import AttributionSource, AuthorKind

SUPPORTED_SCHEMA_PREFIX = "authorship/3."


@dataclass
class ParsedNote:
    # path -> list of (attestation_id, [(start, end)])
    files: dict[str, list[tuple[str, list[tuple[int, int]]]]] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def schema_version(self) -> str:
        return str(self.meta.get("schema_version", ""))


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
    head, sep, tail = text.partition("\n---\n")
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
            note.files[cur].append((ident, parse_ranges(spec)))
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
                per_commit[key] = line_kinds(note, key[1]) if note else {}
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
