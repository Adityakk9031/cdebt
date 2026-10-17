"""`.cdebt/understanding.json`: hash-bound, expiring understanding records.

A record is VALID only while the covered unit's current content hash equals the stored
hash (and `expires_at`, if set, is in the future). Edit the code and understanding is
stale. Stale records are kept for audit, never silently deleted.
This is the ONLY place cdebt writes inside the user's repo.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cdebt.models import CodeUnit, Record

SCHEMA = 1
Status = Literal["verified", "attested", "stale", "none"]


class _Doc(BaseModel):
    schema_version: int = SCHEMA
    records: list[Record] = Field(default_factory=list)


class Store:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.dir = repo / ".cdebt"
        self.file = self.dir / "understanding.json"
        self._doc = self._load()

    @classmethod
    def at_rev(cls, repo: Path, rev: str, working_fallback_rev: str | None = None) -> Store:
        """Store as committed at `rev` (what CI should trust). Falls back to the working-tree
        file only when `rev` resolves to the checked-out HEAD and nothing is committed."""
        from cdebt import gitutil

        s = cls.__new__(cls)
        s.repo, s.dir, s.file = repo, repo / ".cdebt", repo / ".cdebt" / "understanding.json"
        text = gitutil.show_file(repo, rev, ".cdebt/understanding.json")
        if text is None and working_fallback_rev == rev and s.file.exists():
            text = s.file.read_text()
        s._doc = _Doc.model_validate_json(text) if text else _Doc()
        return s

    def _load(self) -> _Doc:
        if not self.file.exists():
            return _Doc()
        doc = _Doc.model_validate_json(self.file.read_text())
        if doc.schema_version != SCHEMA:
            raise ValueError(f"unsupported understanding.json schema {doc.schema_version}")
        return doc

    @property
    def records(self) -> list[Record]:
        return list(self._doc.records)

    def add(self, record: Record) -> None:
        self._doc.records.append(record)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(json.loads(self._doc.model_dump_json()), indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".understanding.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, self.file)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _valid(self, r: Record, unit: CodeUnit, now: datetime) -> bool:
        return (
            r.passed
            and r.content_hash == unit.content_hash
            and (r.expires_at is None or r.expires_at > now)
        )

    def record_for(self, unit: CodeUnit, now: datetime | None = None) -> Record | None:
        """The record that currently clears `unit` (verified preferred over attested)."""
        now = now or datetime.now(UTC)
        best: Record | None = None
        for r in self._doc.records:
            if r.unit_path != unit.path or r.unit_symbol != unit.symbol:
                continue
            if self._valid(r, unit, now):
                if r.kind == "verified":
                    return r
                best = best or r
        return best

    def status(self, unit: CodeUnit, now: datetime | None = None) -> Status:
        """verified / attested (NON-VERIFIED human sign-off): a passing record matches the
        current hash. stale: records exist for this unit but none match the current content.
        none: never quizzed or attested."""
        r = self.record_for(unit, now)
        if r is not None:
            return "verified" if r.kind == "verified" else "attested"
        seen = any(
            x.unit_path == unit.path and x.unit_symbol == unit.symbol for x in self._doc.records
        )
        return "stale" if seen else "none"

    def is_understood(self, unit: CodeUnit) -> bool:
        """VERIFIED understanding only (attestation does not count here)."""
        return self.status(unit) == "verified"
