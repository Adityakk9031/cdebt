"""Pay-down plan: rank files by debt removed per minute of effort.

Effort model is a HEURISTIC with configurable defaults (reading_lines_per_min,
unit_overhead_min): they are guesses, not measurements."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cdebt import gitutil
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.config import Config
from cdebt.mapper import compute_map
from cdebt.models import AttributionSource, AuthorKind
from cdebt.scip import ScipIndex
from cdebt.store import Store
from cdebt.units import extract_units, unit_for_line

Action = Literal["quiz", "review", "rewrite-or-delete"]


class PlanItem(BaseModel):
    rank: int
    path: str
    action: Action
    reason: str
    score: float
    effort_min: float
    priority: float  # score / effort_min
    units: list[str]
    unquizzable_ai_lines: int  # module-level AI lines (quiz covers constants only)
    attested_ai_lines: int = 0  # ATTESTED (NON-VERIFIED): no effort counted, score weighted
    attribution_source: AttributionSource


def compute_plan(
    repo: Path, cfg: Config, rev: str = "HEAD", top: int = 20, scip_idx: ScipIndex | None = None
) -> list[PlanItem]:
    store = Store(repo)
    res = compute_map(repo, cfg, rev, store=store, scip_idx=scip_idx)
    providers = default_providers(repo, use_trailers=cfg.use_trailers)
    items: list[PlanItem] = []
    for f in res.files:
        if f.score <= 0:
            continue
        text = gitutil.show_file(repo, rev, f.path) or ""
        fa = resolve_file(repo, f.path, rev, providers)
        units = extract_units(f.path, text)
        need: dict[str, int] = {}
        residual = 0
        attested = 0
        for i, k in enumerate(fa.kinds):
            if k is not AuthorKind.AI and not (cfg.unknown_as_ai and k is AuthorKind.UNKNOWN):
                continue
            u = unit_for_line(units, i + 1)
            if u is None:
                continue
            st = store.status(u)
            if st == "verified":
                continue
            if st == "attested":  # a human signed off (NON-VERIFIED): no reading effort left
                attested += 1
                continue
            if u.symbol.startswith("<"):
                residual += 1
            need[u.symbol] = need.get(u.symbol, 0) + 1
        lines = sum(need.values())
        effort = lines / cfg.reading_lines_per_min + cfg.unit_overhead_min * len(need)
        effort = max(effort, 1.0)
        if f.blast_source == "scip" and f.blast_radius == 1.0 and f.ai_share > 0.5:
            action: Action = "rewrite-or-delete"
            why = (
                "mostly AI, nothing outside references it (per SCIP): delete or rewrite is cheaper"
            )
        elif not need:
            action = "review"
            why = (
                f"nothing left to quiz: {attested} AI line(s) cleared on NON-VERIFIED attestation; "
                "the remaining score is the attestation discount and blast radius"
                if attested
                else "nothing left to clear in the AI lines"
            )
        elif residual > lines / 2 or not f.path.endswith(".py"):
            action = "review"
            why = (
                "module-level or non-Python code: read it, then `cdebt attest` "
                "(NON-VERIFIED); only module constants can be verified by quiz"
            )
        else:
            action = "quiz"
            why = f"{len(need)} unit(s) can be verified with `cdebt quiz {f.path}`"
        items.append(
            PlanItem(
                rank=0,
                path=f.path,
                action=action,
                reason=why,
                score=f.score,
                effort_min=round(effort, 1),
                priority=f.score / effort,
                units=sorted(need),
                unquizzable_ai_lines=residual,
                attested_ai_lines=attested,
                attribution_source=f.attribution_source,
            )
        )
    items.sort(key=lambda x: (-x.priority, x.path))
    for i, it in enumerate(items[:top], 1):
        it.rank = i
    return items[:top]
