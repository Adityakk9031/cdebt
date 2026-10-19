"""Debt map: score = ai_share x (1 - understood) x (1 + w_churn * churn) x blast_radius,
where understood = (verified + attested_weight * attested) / ai lines."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from cdebt import gitutil
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.cache import Cache, make_key
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
    understood_ai_lines: int  # VERIFIED
    understood_ai_share: float | None  # VERIFIED share; None when there are no AI lines
    attested_ai_lines: int  # ATTESTED (NON-VERIFIED human sign-off)
    attested_ai_share: float | None
    units_attested: int
    units_with_ai: int
    units_understood: int
    understood_units_ci95: tuple[float, float]  # Wilson, over units that contain AI lines
    attribution_sources: dict[str, int]
    blast_source: str
    coarse_attribution: bool


class MapResult(BaseModel):
    summary: Summary
    files: list[FileDebt]


def _churn(repo: Path, path: str, rev: str, days: int) -> int:
    """Commits touching `path` in the last `days` days, cached by commit sha. (The window is
    relative to now, so the cache key includes today's date.)"""
    from datetime import date

    cache = Cache(repo)
    key = make_key("churn", gitutil.rev_sha(repo, rev), path, days, date.today().isoformat())
    hit = cache.get("churn", key)
    if isinstance(hit, int):
        return hit
    val = gitutil.file_churn(repo, path, rev, days)
    cache.put("churn", key, val)
    return val


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


class TokenIndex:
    """Word counts per file, built ONCE per map run (one regex pass over every source file), so
    the name-match blast radius is O(tokens + names) instead of O(files x files x regex)."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.per_file = {p: Counter(_WORD.findall(t)) for p, t in sources.items()}
        self.total: Counter[str] = Counter()
        for c in self.per_file.values():
            self.total.update(c)


_WORD = re.compile(r"\w+")


def approx_blast(idx: TokenIndex, path: str, source: str) -> int:
    """Name-match fallback (labelled `approx`): word occurrences of this file's top-level
    symbol names in OTHER files. Over-counts common names. Never used for quiz answers."""
    names = {
        u.symbol.split(".")[-1].split("#")[0]
        for u in extract_units(path, source)
        if not u.symbol.startswith("<")
    }
    own = idx.per_file.get(path, Counter())
    return sum(
        idx.total[n] - own[n]
        for n in names
        if len(n) > 2 and not n.startswith("__") and _WORD.fullmatch(n)
    )


def compute_map(
    repo: Path,
    cfg: Config,
    rev: str = "HEAD",
    store: Store | None = None,
    scip_idx: ScipIndex | None = None,
) -> MapResult:
    rev = gitutil.rev_sha(repo, rev)  # one `git rev-parse`, not one per file
    store = store or Store(repo)
    providers = default_providers(repo, use_trailers=cfg.use_trailers)
    paths = [p for p in gitutil.ls_files(repo, rev) if p.endswith(cfg.extensions)]
    sources: dict[str, str] = {}
    for p in paths:
        s = gitutil.show_file(repo, rev, p)
        if s:
            sources[p] = s
    rows: list[tuple[str, int, int, int, int, int, int, str, dict[str, int], bool, int]] = []
    units_ai = units_ok = units_att = 0
    coarse = False
    agg_sources: dict[str, int] = {}
    ai_units_by_path: dict[str, dict[str, int]] = {}
    for p, text in sources.items():
        fa = resolve_file(repo, p, rev, providers)
        if fa.total == 0:
            continue
        units = extract_units(p, text)
        status = {(u.path, u.symbol): store.status(u) for u in units}
        ai_lines = [
            i + 1
            for i, k in enumerate(fa.kinds)
            if k is AuthorKind.AI or (cfg.unknown_as_ai and k is AuthorKind.UNKNOWN)
        ]
        und = 0
        att = 0
        unit_ai: dict[str, int] = {}
        seen_units: set[str] = set()
        for ln in ai_lines:
            u = unit_for_line(units, ln)
            if u is None:
                continue
            st = status[(u.path, u.symbol)]
            unit_ai[u.symbol] = unit_ai.get(u.symbol, 0) + 1
            und += int(st == "verified")
            att += int(st == "attested")
            if u.symbol not in seen_units:
                seen_units.add(u.symbol)
                units_ai += 1
                units_ok += int(st == "verified")
                units_att += int(st == "attested")
        ai_units_by_path[p] = unit_ai
        coarse = coarse or fa.coarse
        for k, v in fa.source_counts().items():
            agg_sources[k] = agg_sources.get(k, 0) + v
        churn = _churn(repo, p, rev, cfg.churn_days)
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
                att,
            )
        )
    max_churn = max((r[6] for r in rows), default=0) or 1
    token_idx = TokenIndex(sources) if scip_idx is None else TokenIndex({})
    if scip_idx is not None:
        blast_src = "scip"
    elif rows:
        blast_src = "approx"
    else:
        blast_src = "none"
    files: list[FileDebt] = []
    for p, total, ai, human, unk, und, churn, src, counts, co, att in rows:
        if scip_idx is not None:
            refs = scip_blast(scip_idx, p)
        else:
            refs = approx_blast(token_idx, p, sources[p])
        ai_share = ai / total
        und_share = (und + cfg.attested_weight * att) / ai if ai else 0.0
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
                attested_ai_lines=att,
                ai_share=ai_share,
                understood_share=und_share,
                churn=c,
                blast_radius=br,
                blast_source=blast_src,
                score=score,
                attribution_source=AttributionSource(src),
                source_counts=counts,
                coarse=co,
                ai_units=ai_units_by_path.get(p, {}),
            )
        )
    files.sort(key=lambda f: (-f.score, f.path))
    tot = sum(f.total_lines for f in files)
    ai_t = sum(f.ai_lines for f in files)
    und_t = sum(f.understood_ai_lines for f in files)
    att_t = sum(f.attested_ai_lines for f in files)
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
        attested_ai_lines=att_t,
        attested_ai_share=(att_t / ai_t if ai_t else None),
        units_attested=units_att,
        units_with_ai=units_ai,
        units_understood=units_ok,
        understood_units_ci95=wilson(units_ok, units_ai),
        attribution_sources=agg_sources,
        blast_source=blast_src,
        coarse_attribution=coarse,
    )
    return MapResult(summary=summary, files=files)
