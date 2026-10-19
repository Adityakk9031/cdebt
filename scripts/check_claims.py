#!/usr/bin/env python3
"""Claims audit: every number in the checked Markdown files must be backed by a file in the repo.

A block (paragraph, list item or table row) that contains numbers must contain a citation
`[src: path, other/path.json]`. The audit fails when a cited file does not exist, or when a number
in the block does not appear in any cited file (verbatim, rounded, or as a percentage of a
fraction). Not counted as claims: code (fences and `inline`), link targets, HTML comments,
version strings (0.1.2, 3.11), dates, Python/Node versions, heading/list numbering, and lines
marked `<!-- claims:ignore -->` (listed in the report; keep them rare).

Usage: check_claims.py FILE.md [FILE.md ...] [--list]   (exit 1 on any failure)"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CITE = re.compile(r"\[src:\s*([^\]]+)\]")
NUM = re.compile(r"(?<![\w./:#-])[-+]?\d[\d,]*(?:\.\d+)?%?(?![\w-]*\()")
IGNORE_MARK = "claims:ignore"
VERSION = re.compile(
    r"\b\d+\.\d+\.\d+(?:[-.]\w+)*\b|\b\d+\.\d+\.x\b|\b(?:Python|Node(?:\.js)?|python|node|v)\s*>?=?\s*\d+(?:\.\d+)*\b"
)
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b20\d\d\b")


def strip_non_claims(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)  # inline code
    text = re.sub(r"\]\([^)]*\)", "]", text)  # link targets
    text = re.sub(r"https?://\S+", " ", text)
    text = VERSION.sub(" ", text)
    text = DATE.sub(" ", text)
    return text


def blocks(md: str) -> list[tuple[int, str, bool]]:
    """(first line number, text, ignored) for each paragraph / list item / table row."""
    out: list[tuple[int, str, bool]] = []
    fence = False
    cur: list[str] = []
    start = 0

    def flush() -> None:
        nonlocal cur
        if cur:
            text = "\n".join(cur)
            out.append((start, text, IGNORE_MARK in text))
        cur = []

    for i, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        stripped = line.strip()
        is_item = bool(re.match(r"([-*+]|\d+\.)\s", stripped)) or stripped.startswith("|")
        is_head = stripped.startswith("#")
        if not stripped or is_item or is_head:
            flush()
        if not stripped:
            continue
        if is_head:
            out.append((i, re.sub(r"^#+\s*(\d+[.)]?\s*)?", "", stripped), False))
            continue
        if not cur:
            start = i
        cur.append(re.sub(r"^([-*+]|\d+\.)\s+", "", stripped) if is_item else stripped)
        if is_item:
            flush()
    flush()
    return out


def numbers_in(text: str) -> list[str]:
    toks = []
    for m in NUM.finditer(strip_non_claims(text)):
        t = m.group(0)
        v = t.strip("+-%").replace(",", "")
        if v and not (v.isdigit() and t == v and len(v) == 1 and False):
            toks.append(t)
    return toks


def to_float(tok: str) -> tuple[float, int, bool]:
    pct = tok.endswith("%")
    s = tok.strip("+%").replace(",", "")
    dec = len(s.split(".")[1]) if "." in s else 0
    return float(s), dec, pct


def file_numbers(path: Path) -> list[float]:
    text = path.read_text(errors="replace")
    vals = [
        float(m.group(0).replace(",", "").lstrip("+"))
        for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", text)
    ]
    if path.suffix == ".json":
        try:

            def walk(o: object) -> None:
                if isinstance(o, bool):
                    return
                if isinstance(o, (int, float)):
                    vals.append(float(o))
                elif isinstance(o, dict):
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)

            walk(json.loads(text))
        except ValueError:
            pass
    return vals


def supported(tok: str, pool: list[float]) -> bool:
    v, dec, pct = to_float(tok)
    for x in pool:
        if abs(round(x, dec) - v) < 1e-9 or abs(x - v) < 1e-9:
            return True
        if abs(round(x * 100, dec) - v) < 1e-9 or abs(round(x * 100) - v) < 1e-9 and dec == 0:
            return True  # a fraction shown as a percentage
        if pct and abs(round(x, dec) - v) < 1e-9:
            return True
    return False


def check(files: list[Path], list_only: bool = False) -> int:
    failures: list[str] = []
    n_nums = n_cited = n_ignored = 0
    pools: dict[Path, list[float]] = {}
    for f in files:
        for line, text, ignored in blocks(f.read_text()):
            toks = numbers_in(text)
            if not toks:
                continue
            if ignored:
                n_ignored += len(toks)
                continue
            cites = [p.strip() for c in CITE.findall(text) for p in c.split(",") if p.strip()]
            # numbers inside the citation itself are paths, not claims
            toks = numbers_in(CITE.sub(" ", text))
            if not toks:
                continue
            n_nums += len(toks)
            if not cites:
                failures.append(f"{f.name}:{line}: no [src: ...] for number(s) {toks[:6]}")
                continue
            ok_files = []
            for c in cites:
                p = ROOT / c
                if not p.is_file():
                    failures.append(f"{f.name}:{line}: cited file does not exist: {c}")
                else:
                    ok_files.append(p)
                    pools.setdefault(p, file_numbers(p))
            if not ok_files:
                continue
            pool = [x for p in ok_files for x in pools[p]]
            for t in toks:
                if supported(t, pool):
                    n_cited += 1
                    if list_only:
                        print(f"ok   {f.name}:{line}: {t}  <- {', '.join(cites)}")
                else:
                    failures.append(f"{f.name}:{line}: {t} not found in cited {', '.join(cites)}")
    print(
        f"claims audit: {n_nums} numbers checked, {n_cited} supported, {n_ignored} ignored "
        f"(claims:ignore), {len(failures)} problem(s)"
    )
    for m in failures:
        print("  FAIL", m)
    return 1 if failures else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(
        check([Path(a) if Path(a).is_absolute() else ROOT / a for a in args], "--list" in sys.argv)
    )
