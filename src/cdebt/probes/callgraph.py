"""Call-graph probes answered by a SCIP index. No index (or a stale one) -> no probes.
Approximate answers are never used as ground truth."""

from __future__ import annotations

import json
import random
from pathlib import Path

from cdebt.models import CodeUnit, Probe
from cdebt.probes.base import NONE_OPTION, probe_id, shuffled
from cdebt.scip import ScipIndex
from cdebt.units import extract_units, unit_for_line


def _function_symbols(idx: ScipIndex, rel: str, units: list[CodeUnit]) -> dict[str, str]:
    """unit symbol -> SCIP symbol, for function-like definitions in `rel`."""
    out: dict[str, str] = {}
    for sym, line in idx.definitions(rel).items():
        if not sym.endswith("()."):
            continue
        u = unit_for_line(units, line)
        if u and not u.symbol.startswith("<"):
            out.setdefault(u.symbol, sym)
    return out


def generate(
    repo: Path,
    rel: str,
    units: list[CodeUnit],
    idx: ScipIndex,
    seed: int,
    index_label: str = "scip",
    per_unit: int = 2,
) -> tuple[list[Probe], list[str]]:
    rng = random.Random(seed)
    skips: list[str] = []
    probes: list[Probe] = []
    all_files = sorted(idx.docs)
    src_cache: dict[str, list[CodeUnit]] = {}

    def units_of(p: str) -> list[CodeUnit]:
        if p not in src_cache:
            f = repo / p
            src_cache[p] = extract_units(p, f.read_text(errors="replace")) if f.is_file() else []
        return src_cache[p]

    for usym, scip_sym in _function_symbols(idx, rel, units).items():
        refs = [(p, ln) for p, ln in idx.references_to(scip_sym) if p != rel]
        if not refs:
            skips.append(f"{rel}::{usym}: no references in index (cannot ask)")
            continue
        ref_files = sorted({p for p, _ in refs})
        others = [
            f
            for f in all_files
            if f not in ref_files and f != rel and not f.endswith("__init__.py")
        ]
        made = 0
        # Q1: which files reference X (what breaks if its signature changes)
        if others and made < per_unit:
            true = rng.sample(ref_files, min(3, len(ref_files)))
            cand = shuffled(true + rng.sample(others, min(len(others), max(1, 4 - len(true)))), rng)
            q = (
                f"If the signature of `{usym}` (in {rel}) changed, which of these files would "
                f"need updating (contain references to it)?"
            )
            probes.append(
                Probe(
                    id=probe_id("callgraph", rel, usym, q, seed),
                    kind="callgraph",
                    path=rel,
                    symbol=usym,
                    question=q,
                    options=cand + [NONE_OPTION],
                    answer=json.dumps(sorted(true)),
                    multi=True,
                    verified_by="scip-index",
                    seed=seed,
                    evidence={
                        "index": index_label,
                        "scip_symbol": scip_sym,
                        "reference_sites": [f"{p}:{ln}" for p, ln in refs[:20]],
                    },
                )
            )
            made += 1
        # Q2: which code units reference X
        ref_units = sorted(
            {
                f"{p}::{u.symbol}"
                for p, ln in refs
                if (u := unit_for_line(units_of(p), ln)) is not None
            }
        )
        other_units = sorted(
            {f"{p}::{u.symbol}" for p in all_files if p != rel for u in units_of(p)}
            - set(ref_units)
        )
        if ref_units and other_units and made < per_unit:
            true_u = rng.sample(ref_units, min(3, len(ref_units)))
            cand_u = shuffled(
                true_u + rng.sample(other_units, min(len(other_units), max(1, 4 - len(true_u)))),
                rng,
            )
            q = f"Which of these code units reference `{usym}` (in {rel})?"
            probes.append(
                Probe(
                    id=probe_id("callgraph", rel, usym, q, seed),
                    kind="callgraph",
                    path=rel,
                    symbol=usym,
                    question=q,
                    options=cand_u + [NONE_OPTION],
                    answer=json.dumps(sorted(true_u)),
                    multi=True,
                    verified_by="scip-index",
                    seed=seed,
                    evidence={
                        "index": index_label,
                        "scip_symbol": scip_sym,
                        "reference_sites": [f"{p}:{ln}" for p, ln in refs[:20]],
                    },
                )
            )
    return probes, skips
