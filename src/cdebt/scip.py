"""Minimal, dependency-free SCIP index reader (protobuf wire format) + indexer runner.

Fields used (scip.proto, UNVERIFIED against the .proto but VERIFIED on a real scip-python
index, see tests/fixtures/scip/): Index{documents=2}; Document{relative_path=1,
occurrences=2}; Occurrence{range=1 (packed int32), symbol=2, symbol_roles=3 (bit 0x1 =
definition)}. Unknown fields are skipped, so newer indexes still parse.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFINITION = 0x1


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7


def _fields(b: bytes) -> list[tuple[int, int, int | bytes]]:
    i = 0
    out: list[tuple[int, int, int | bytes]] = []
    while i < len(b):
        key, i = _varint(b, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
            out.append((fn, wt, v))
        elif wt == 2:
            n, i = _varint(b, i)
            out.append((fn, wt, b[i : i + n]))
            i += n
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
    return out


@dataclass(frozen=True)
class Occurrence:
    line: int  # 1-indexed start line
    symbol: str
    is_definition: bool


@dataclass
class ScipIndex:
    docs: dict[str, list[Occurrence]] = field(default_factory=dict)

    def definitions(self, path: str) -> dict[str, int]:
        return {o.symbol: o.line for o in self.docs.get(path, []) if o.is_definition}

    def references_to(self, symbol: str) -> list[tuple[str, int]]:
        return [
            (p, o.line)
            for p, occs in self.docs.items()
            for o in occs
            if o.symbol == symbol and not o.is_definition
        ]

    def definition_site(self, symbol: str) -> tuple[str, int] | None:
        for p, occs in self.docs.items():
            for o in occs:
                if o.symbol == symbol and o.is_definition:
                    return p, o.line
        return None


def _occurrence(b: bytes) -> Occurrence | None:
    rng: list[int] = []
    sym, roles = "", 0
    for fn, wt, v in _fields(b):
        if fn == 1 and wt == 2 and isinstance(v, bytes):
            i = 0
            while i < len(v):
                x, i = _varint(v, i)
                rng.append(x)
        elif fn == 2 and isinstance(v, bytes):
            sym = v.decode("utf-8", "replace")
        elif fn == 3 and isinstance(v, int):
            roles = v
    if not rng or not sym or sym.startswith("local "):
        return None
    return Occurrence(rng[0] + 1, sym, bool(roles & DEFINITION))


def parse_index(data: bytes) -> ScipIndex:
    idx = ScipIndex()
    for fn, _wt, v in _fields(data):
        if fn != 2 or not isinstance(v, bytes):
            continue
        path, occs = "", []
        for dfn, _dwt, dv in _fields(v):
            if dfn == 1 and isinstance(dv, bytes):
                path = dv.decode("utf-8", "replace")
            elif dfn == 2 and isinstance(dv, bytes):
                o = _occurrence(dv)
                if o:
                    occs.append(o)
        if path:
            idx.docs[path] = occs
    return idx


def load_index(path: Path) -> ScipIndex:
    return parse_index(path.read_bytes())


def index_path(repo: Path) -> Path:
    return repo / ".cdebt" / "cache" / "index.scip"


def build_index(repo: Path, language: str = "python", timeout: int = 600) -> Path | None:
    """Run the SCIP indexer (optional external tool). Writes only under .cdebt/cache/.
    Returns None if node/npx are unavailable or indexing fails."""
    npx = shutil.which("npx")
    if npx is None:
        return None
    out = index_path(repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    pkg = {"python": "@sourcegraph/scip-python", "typescript": "@sourcegraph/scip-typescript"}
    if language == "python":
        cmd = [
            npx,
            "--yes",
            pkg[language],
            "index",
            "--project-name",
            repo.name,
            "--project-version",
            "0",
            "--output",
            str(out),
            ".",
        ]
    else:
        cmd = [npx, "--yes", pkg[language], "index", "--output", str(out)]
    try:
        p = subprocess.run(cmd, cwd=repo, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out if p.returncode == 0 and out.exists() else None
