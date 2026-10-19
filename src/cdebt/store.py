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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cdebt.models import CodeUnit, Record

CURRENT_SCHEMA = 2
SCHEMA = CURRENT_SCHEMA  # back-compat alias
Status = Literal["verified", "attested", "stale", "none"]


class StoreError(ValueError):
    """`.cdebt/understanding.json` cannot be read (corrupt, or written by a newer cdebt).
    The CLI reports it as `error: ...` with exit code 2 (see cli.command)."""


def _v1_to_v2(d: dict[str, Any]) -> dict[str, Any]:
    """v1 -> v2 (0.1.x -> 0.1.2+): records gain `kind` (verified/attested), `note`, `scope`.
    Every v1 record was execution-verified, so it becomes kind=verified."""
    for r in d.get("records", []):
        r.setdefault("kind", "verified")
        r.setdefault("note", None)
        r.setdefault("scope", None)
    d["schema_version"] = 2
    return d


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2}


def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Bring a raw document to CURRENT_SCHEMA. Returns (document, schema it was read as).
    A missing `schema_version` is treated as 1. A NEWER schema is refused, never guessed at."""
    found = int(data.get("schema_version", 1))
    if found > CURRENT_SCHEMA:
        raise StoreError(
            f"understanding.json uses schema {found}, but this cdebt only understands up to "
            f"{CURRENT_SCHEMA}. Upgrade cdebt (it was written by a newer version)."
        )
    v = found
    while v < CURRENT_SCHEMA:
        if v not in MIGRATIONS:
            raise StoreError(f"no migration from understanding.json schema {v}")
        data = MIGRATIONS[v](data)
        v += 1
    return data, found


def parse_document(text: str) -> tuple[_Doc, int]:
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise StoreError(f"understanding.json is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise StoreError("understanding.json must contain a JSON object")
    raw, found = migrate(raw)
    try:
        return _Doc.model_validate(raw), found
    except ValueError as e:
        raise StoreError(f"understanding.json does not match schema {CURRENT_SCHEMA}: {e}") from e


class _Doc(BaseModel):
    schema_version: int = CURRENT_SCHEMA
    records: list[Record] = Field(default_factory=list)


class Store:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.dir = repo / ".cdebt"
        self.file = self.dir / "understanding.json"
        self.migrated_from: int | None = None
        self._doc = self._load()

    @classmethod
    def at_rev(cls, repo: Path, rev: str, working_fallback_rev: str | None = None) -> Store:
        """Store as committed at `rev` (what CI should trust). Falls back to the working-tree
        file only when `rev` resolves to the checked-out HEAD and nothing is committed."""
        from cdebt import gitutil

        s = cls.__new__(cls)
        s.repo, s.dir, s.file = repo, repo / ".cdebt", repo / ".cdebt" / "understanding.json"
        s.migrated_from = None
        text = gitutil.show_file(repo, rev, ".cdebt/understanding.json")
        if text is None and working_fallback_rev == rev and s.file.exists():
            text = s.file.read_text()
        if text:
            s._doc, found = parse_document(text)
            s.migrated_from = found if found != CURRENT_SCHEMA else None
        else:
            s._doc = _Doc()
        return s

    def _load(self) -> _Doc:
        if not self.file.exists():
            return _Doc()
        doc, found = parse_document(self.file.read_text())
        self.migrated_from = found if found != CURRENT_SCHEMA else None
        return doc

    def needs_migration(self) -> bool:
        return self.migrated_from is not None

    @property
    def records(self) -> list[Record]:
        return list(self._doc.records)

    def add(self, record: Record) -> None:
        self._doc.records.append(record)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.migrated_from is not None and self.file.exists():
            bak = self.dir / f"understanding.v{self.migrated_from}.bak.json"
            if not bak.exists():  # keep the original bytes of the pre-migration file, once
                bak.write_bytes(self.file.read_bytes())
        self.migrated_from = None
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
