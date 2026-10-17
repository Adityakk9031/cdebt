"""Agent Trace (https://agent-trace.dev, RFC v0.1.0) reader.

UNVERIFIED: the spec does not define where records live. We read `*.json` / `*.jsonl` under
`.agent-trace/` at the repo root (configurable). Records give lines at `vcs.revision`, so a
HEAD line maps via blame to (commit, orig line); we match records whose revision == that commit.
Contributor types: ai -> AI, human -> HUMAN, mixed -> AI (human-edited AI output), unknown -> no claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cdebt.gitutil import BlameLine
from cdebt.models import AttributionSource, AuthorKind

_MAP = {"ai": AuthorKind.AI, "mixed": AuthorKind.AI, "human": AuthorKind.HUMAN}


def load_records(directory: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if not directory.is_dir():
        return recs
    for f in sorted(directory.iterdir()):
        try:
            if f.suffix == ".jsonl":
                for ln in f.read_text().splitlines():
                    if ln.strip():
                        recs.append(json.loads(ln))
            elif f.suffix == ".json":
                data = json.loads(f.read_text())
                recs.extend(data if isinstance(data, list) else [data])
        except (OSError, json.JSONDecodeError):
            continue
    return [r for r in recs if isinstance(r, dict) and "files" in r]


def index(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[int, AuthorKind]]:
    """(revision, path) -> {line: kind}. Range-level contributor overrides conversation-level."""
    idx: dict[tuple[str, str], dict[int, AuthorKind]] = {}
    for r in records:
        rev = str((r.get("vcs") or {}).get("revision", ""))
        for f in r.get("files", []):
            m = idx.setdefault((rev, str(f.get("path", ""))), {})
            for conv in f.get("conversations", []):
                base = (conv.get("contributor") or {}).get("type", "unknown")
                for rg in conv.get("ranges", []):
                    t = (rg.get("contributor") or {}).get("type", base)
                    kind = _MAP.get(t)
                    if kind is None:
                        continue
                    for ln in range(int(rg["start_line"]), int(rg["end_line"]) + 1):
                        m[ln] = kind
    return idx


class AgentTraceProvider:
    source = AttributionSource.AGENT_TRACE

    def __init__(self, repo: Path, directory: Path | None = None) -> None:
        self._idx = index(load_records(directory or repo / ".agent-trace"))

    def claims(
        self, path: str, rev: str, blame: list[BlameLine]
    ) -> list[tuple[AuthorKind, bool] | None]:
        out: list[tuple[AuthorKind, bool] | None] = []
        for bl in blame:
            m = self._idx.get((bl.sha, bl.path or path))
            kind = m.get(bl.orig) if m else None
            out.append((kind, False) if kind else None)
        return out
