"""A quiz-generation session shared between `cdebt quiz` (parent) and `cdebt mcp` (child).

The directory holds ONLY what the agent may see (targets) and what the agent sent (proposal specs).
It never holds an answer: the parent re-executes every spec itself to get the true answer, so an
agent with file-read access cannot find answers on disk."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from cdebt.agents.probes import shadowed
from cdebt.config import Config
from cdebt.models import CodeUnit
from cdebt.store import Store
from cdebt.units import extract_units

MAX_TESTS_CHARS = 3000


class Target(BaseModel):
    id: str  # "<path>::<symbol>"
    path: str
    symbol: str
    signature: str
    source: str
    tests: list[str] = Field(default_factory=list)  # nearby test code mentioning the symbol


class SessionData(BaseModel):
    repo: str
    agent: str
    max_per_target: int
    targets: list[Target]


def _signature(source: str, unit: CodeUnit) -> str:
    first = min(a for a, _ in unit.ranges)
    lines = source.splitlines()
    out = []
    for ln in lines[first - 1 : first + 5]:
        out.append(ln)
        if ln.rstrip().endswith(":"):
            break
    return "\n".join(out)


def _unit_source(source: str, unit: CodeUnit) -> str:
    lines = source.splitlines()
    return "\n".join(lines[n - 1] for n in unit.lines() if 0 < n <= len(lines))


def _nearby_tests(repo: Path, leaf: str) -> list[str]:
    pat = re.compile(rf"\b{re.escape(leaf)}\b")
    found: list[str] = []
    for d in ("tests", "test"):
        base = repo / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py"))[:200]:
            try:
                t = f.read_text(errors="replace")
            except OSError:
                continue
            if pat.search(t):
                found.append(f"# {f.relative_to(repo)}\n{t[:MAX_TESTS_CHARS]}")
            if len(found) >= 3:
                return found
    return found


def targets_for_file(repo: Path, rel: str, cfg: Config, store: Store | None = None) -> list[Target]:
    """Functions/methods in `rel` that are not yet verified (stale, attested or never quizzed)."""
    source = (repo / rel).read_text()
    store = store or Store(repo)
    out: list[Target] = []
    units = extract_units(rel, source)
    for u in units:
        if u.symbol.startswith("<") or shadowed(units, u) or store.status(u) == "verified":
            continue
        out.append(
            Target(
                id=f"{rel}::{u.symbol}",
                path=rel,
                symbol=u.symbol,
                signature=_signature(source, u),
                source=_unit_source(source, u),
                tests=_nearby_tests(repo, u.symbol.split(".")[-1]),
            )
        )
    return out


class Session:
    def __init__(self, directory: Path, data: SessionData) -> None:
        self.dir = directory
        self.data = data
        self.proposals_file = directory / "proposals.jsonl"

    @classmethod
    def create(cls, repo: Path, agent: str, targets: list[Target], max_per_target: int) -> Session:
        d = Path(tempfile.mkdtemp(prefix="cdebt-session-"))
        os.chmod(d, 0o700)
        data = SessionData(
            repo=str(repo), agent=agent, max_per_target=max_per_target, targets=targets
        )
        (d / "session.json").write_text(data.model_dump_json(indent=1))
        (d / "proposals.jsonl").write_text("")
        return cls(d, data)

    @classmethod
    def load(cls, directory: Path) -> Session:
        data = SessionData.model_validate_json((directory / "session.json").read_text())
        return cls(directory, data)

    def target(self, target_id: str) -> Target | None:
        return next((t for t in self.data.targets if t.id == target_id), None)

    def submitted(self, target_id: str) -> int:
        n = 0
        for rec in self.read():
            if rec["target_id"] == target_id:
                n += 1
        return n

    def accepted_marked(self, target_id: str, mark: str) -> bool:
        return any(
            r["target_id"] == target_id and r["accepted"] and r["why"] == mark for r in self.read()
        )

    def record(self, target_id: str, spec: dict[str, Any], accepted: bool, why: str) -> None:
        line = json.dumps(
            {"target_id": target_id, "spec": spec, "accepted": accepted, "why": why}, sort_keys=True
        )
        with open(self.proposals_file, "a") as f:  # O_APPEND: one atomic line per proposal
            f.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.proposals_file.exists():
            for ln in self.proposals_file.read_text().splitlines():
                if ln.strip():
                    out.append(json.loads(ln))
        return out

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
