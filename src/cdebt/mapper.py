"""Debt map: score = ai_share x (1 - understood) x (1 + w_churn * churn) x blast_radius."""

from __future__ import annotations

import math
import re
from pathlib import Path

from pydantic import BaseModel

from cdebt import gitutil
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.config import Config
from cdebt.models import AttributionSource, AuthorKind, FileDebt
from cdebt.scip import ScipIndex
from cdebt.stats import wilson
from cdebt.store import Store
from cdebt.units import extract_units, unit_for_line


class Summary(BaseModel):
    rev: str
    files: int
    total_lines: int
    ai_lines: int
    human_lines: int
    unknown_lines: int
    ai_share: float  # ai / total
    unknown_share: float
    understood_ai_lines: int
    understood_ai_share: float | None  # None when there are no AI lines
    units_with_ai: int
    units_understood: int
    understood_units_ci95: tuple[float, float]  # Wilson, over units that contain AI lines
    attribution_sources: dict[str, int]
    blast_source: str
    coarse_attribution: bool


class MapResult(BaseModel):
    summary: Summary
    files: list[FileDebt]


def blast_factor(refs: int, mode: str) -> float:
    return 1.0 + math.log1p(refs) if mode == "log" else float(max(1, refs))


def scip_blast(idx: ScipIndex, path: str) -> int:
    """Distinct (file, line) reference sites OUTSIDE `path` to symbols defined in `path`."""
    defs = set(idx.definitions(path))
    sites: set[tuple[str, int]] = set()
    for p, occs in idx.docs.items():
        if p == path:
            continue
        for o in occs:
            if o.symbol in defs and not o.is_definition:
                sites.add((p, o.line))
    return len(sites)


def approx_blast(repo: Path, rev: str, path: str, source: str, others: dict[str, str]) -> int:
    """Name-match fallback (labelled `approx`): word occurrences of this file's top-level
    symbol names in other files. Over-counts common names. Never used for quiz answers."""
    names = {
        u.symbol.split(".")[-1].split("#")[0]
        for u in extract_units(path, source)
        if not u.symbol.startswith("<")
    }
    names = {n for n in names if len(n) > 2 and not n.startswith("__")}
    if not names:
        return 0
    pat = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\b")
    return sum(len(pat.findall(t)) for p, t in others.items() if p != path)


def compute_map(
    repo: Path,
    cfg: Config,
    rev: str = "HEAD",
    store: Store | None = None,
    scip_idx: ScipIndex | None = None,
) -> MapResult:
    store = store or Store(repo)
    providers = default_providers(repo, use_trailers=cfg.use_trailers)
    paths = [p for p in gitutil.ls_files(repo, rev) if p.endswith(cfg.extensions)]
    sources: dict[str, str] = {}
    for p in paths:
        s = gitutil.show_file(repo, rev, p)
        if s:
            sources[p] = s
    rows: list[tuple[str, int, int, int, int, int, int, str, dict[str, int], bool, int]] = []
    units_ai = units_ok = 0
    coarse = False
    agg_sources: dict[str, int] = {}
    for p, text in sources.items():
        fa = resolve_file(repo, p, rev, providers)
        if fa.total == 0:
            continue
        units = extract_units(p, text)
        status = {(u.path, u.symbol): store.is_understood(u) for u in units}
        ai_lines = [
            i + 1
            for i, k in enumerate(fa.kinds)
            if k is AuthorKind.AI or (cfg.unknown_as_ai and k is AuthorKind.UNKNOWN)
        ]
        und = 0
        seen_units: set[str] = set()
        for ln in ai_lines:
            u = unit_for_line(units, ln)
            if u is None:
                continue
            ok = status[(u.path, u.symbol)]
            und += int(ok)
            if u.symbol not in seen_units:
                seen_units.add(u.symbol)
                units_ai += 1
                units_ok += int(ok)
        coarse = coarse or fa.coarse
        for k, v in fa.source_counts().items():
            agg_sources[k] = agg_sources.get(k, 0) + v
        churn = gitutil.file_churn(repo, p, rev, cfg.churn_days)
        rows.append(
            (
                p,
                fa.total,
                len(ai_lines),
                fa.human_lines,
                fa.unknown_lines,
                und,
                churn,
                fa.source.value,
                fa.source_counts(),
                fa.coarse,
                0,
            )
        )
    max_churn = max((r[6] for r in rows), default=0) or 1
    if scip_idx is not None:
        blast_src = "scip"
    elif rows:
        blast_src = "approx"
    else:
        blast_src = "none"
    files: list[FileDebt] = []
    for p, total, ai, human, unk, und, churn, src, counts, co, _ in rows:
        if scip_idx is not None:
            refs = scip_blast(scip_idx, p)
        else:
            refs = approx_blast(repo, rev, p, sources[p], sources)
        ai_share = ai / total
        und_share = und / ai if ai else 0.0
        c = churn / max_churn
        br = blast_factor(refs, cfg.blast_mode)
        score = ai_share * (1 - und_share) * (1 + cfg.w_churn * c) * br
        files.append(
            FileDebt(
                path=p,
                total_lines=total,
                ai_lines=ai,
                human_lines=human,
                unknown_lines=unk,
                understood_ai_lines=und,
                ai_share=ai_share,
                understood_share=und_share,
                churn=c,
                blast_radius=br,
                blast_source=blast_src,
                score=score,
                attribution_source=AttributionSource(src),
                source_counts=counts,
                coarse=co,
            )
        )
    files.sort(key=lambda f: (-f.score, f.path))
    tot = sum(f.total_lines for f in files)
    ai_t = sum(f.ai_lines for f in files)
    und_t = sum(f.understood_ai_lines for f in files)
    summary = Summary(
        rev=gitutil.resolve(repo, rev)[:12],
        files=len(files),
        total_lines=tot,
        ai_lines=ai_t,
        human_lines=sum(f.human_lines for f in files),
        unknown_lines=sum(f.unknown_lines for f in files),
        ai_share=ai_t / tot if tot else 0.0,
        unknown_share=sum(f.unknown_lines for f in files) / tot if tot else 0.0,
        understood_ai_lines=und_t,
        understood_ai_share=(und_t / ai_t if ai_t else None),
        units_with_ai=units_ai,
        units_understood=units_ok,
        understood_units_ci95=wilson(units_ok, units_ai),
        attribution_sources=agg_sources,
        blast_source=blast_src,
        coarse_attribution=coarse,
    )
    return MapResult(summary=summary, files=files)
