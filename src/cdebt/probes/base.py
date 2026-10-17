"""Probe plumbing: ids, options, DETERMINISTIC grading. No model ever grades an answer."""

from __future__ import annotations

import ast
import hashlib
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from cdebt.models import Probe

LETTERS = "ABCDEFGH"
NONE_OPTION = "(none)"


def probe_id(kind: str, path: str, symbol: str, question: str, seed: int) -> str:
    return hashlib.sha1(f"{kind}|{path}|{symbol}|{question}|{seed}".encode()).hexdigest()[:10]


def shuffled(options: list[str], rng: random.Random) -> list[str]:
    out = list(dict.fromkeys(options))
    rng.shuffle(out)
    return out


def _norm(s: str) -> str:
    s = s.strip()
    try:
        return repr(ast.literal_eval(s))
    except (ValueError, SyntaxError):
        return " ".join(s.split())


def resolve_choice(probe: Probe, raw: str) -> list[str]:
    """Turn 'B', 'A,C' or option text(s) into canonical option strings."""
    raw = raw.strip()
    if probe.multi:
        parts = [p.strip() for p in raw.split(",")] if "," in raw or len(raw) <= 8 else [raw]
    else:
        parts = [raw]
    out: list[str] = []
    for p in parts:
        if len(p) == 1 and p.upper() in LETTERS[: len(probe.options)]:
            out.append(probe.options[LETTERS.index(p.upper())])
        elif p.lower() == "none":
            out.append(NONE_OPTION)
        elif p:
            out.append(p)
    return out


def grade(probe: Probe, raw: str) -> bool:
    """Pure rule: exact (normalised) match against the computed answer."""
    chosen = resolve_choice(probe, raw)
    if probe.multi:
        truth = set(json.loads(probe.answer))
        return {_norm(c) for c in chosen} == {_norm(t) for t in truth}
    return len(chosen) == 1 and _norm(chosen[0]) == _norm(probe.answer)


def copy_tree(repo: Path, dest: Path, *, max_mb: int = 100) -> None:
    """Copy tracked + untracked-but-not-ignored working-tree files (not .git/.venv/.cdebt)."""
    p = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
    )
    total = 0
    for rel in p.stdout.decode().split("\0"):
        if not rel or rel.startswith((".git/", ".venv/", ".cdebt/")):
            continue
        src = repo / rel
        if not src.is_file():
            continue
        total += src.stat().st_size
        if total > max_mb << 20:
            raise RuntimeError(f"repo too large to copy for sandboxing (> {max_mb} MB)")
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)


def temp_root(prefix: str = "cdebt-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
