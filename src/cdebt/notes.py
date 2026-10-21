"""`refs/notes/cdebt`: hash-bound understanding records stored as git notes.

This is the ONLY git ref cdebt writes (besides the hook `cdebt init` installs with consent). It
never touches `refs/notes/ai` except to PUSH it. A note is a JSON document attached to a commit:
{"schema": 1, "records": [Record, ...]}. Records are bound to a hash of the code they cover, so a
note on one commit still validates any later commit whose code for that unit is unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from cdebt import gitutil
from cdebt.models import Record

NOTES_NAME = "cdebt"
NOTES_REF = f"refs/notes/{NOTES_NAME}"
AI_REF = "refs/notes/ai"
SCHEMA = 1


def _parse(text: str | None) -> list[Record]:
    if not text:
        return []
    try:
        doc = json.loads(text)
        return [Record.model_validate(r) for r in doc.get("records", [])]
    except (ValueError, AttributeError):
        return []  # a malformed note is ignored (it can only ever mean "not understood")


def read_commit(repo: Path, sha: str) -> list[Record]:
    return _parse(gitutil.note(repo, sha, NOTES_NAME))


def read_range(repo: Path, base: str, head: str) -> list[Record]:
    """Records from notes on every commit in base..head, plus the base commit itself."""
    out: list[Record] = []
    shas = gitutil.run(["rev-list", f"{base}..{head}"], repo).split()
    for sha in [*shas, gitutil.resolve(repo, base)]:
        out.extend(read_commit(repo, sha))
    return out


def _identity_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_COMMITTER_NAME", "cdebt")
    env.setdefault("GIT_COMMITTER_EMAIL", "cdebt@localhost")
    env.setdefault("GIT_AUTHOR_NAME", "cdebt")
    env.setdefault("GIT_AUTHOR_EMAIL", "cdebt@localhost")
    return env


def write_records(repo: Path, sha: str, records: list[Record]) -> None:
    """Merge `records` into the note on commit `sha` (replaces the note with the union)."""
    existing = read_commit(repo, sha)
    doc = {
        "schema": SCHEMA,
        "records": [json.loads(r.model_dump_json()) for r in existing + records],
    }
    p = subprocess.run(  # noqa: S603 - fixed argv, writes only refs/notes/cdebt
        ["git", "notes", f"--ref={NOTES_NAME}", "add", "-f", "-F", "-", sha],
        cwd=repo,
        input=json.dumps(doc, sort_keys=True),
        capture_output=True,
        text=True,
        env=_identity_env(),
    )
    if p.returncode != 0:
        raise gitutil.GitError(f"could not write {NOTES_REF}: {p.stderr.strip()}")


class NotesSink:
    """Duck-typed like Store for `adaptive.record_outcomes`: collect, then write one note."""

    def __init__(self, repo: Path, sha: str) -> None:
        self.repo, self.sha = repo, sha
        self.pending: list[Record] = []

    def add(self, record: Record) -> None:
        self.pending.append(record)

    def save(self) -> None:
        if self.pending:
            write_records(self.repo, self.sha, self.pending)
            self.pending = []


def push_notes(repo: Path, remote: str) -> tuple[bool, str]:
    """Push refs/notes/ai (if present) and refs/notes/cdebt to `remote` WITHOUT hooks (so the
    pre-push hook does not recurse). Fast-forward only: never forced. Returns (ok, message)."""
    refs = [r for r in (AI_REF, NOTES_REF) if gitutil.run(["for-each-ref", r], repo).strip()]
    if not refs:
        return True, "no notes to push"
    p = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "push", "--no-verify", "--quiet", remote, *[f"{r}:{r}" for r in refs]],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "CDEBT_IN_HOOK": "1"},
    )
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]
