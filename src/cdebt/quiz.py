"""Quiz orchestration: generate probes, grade DETERMINISTICALLY, bind records to hashes."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cdebt import gitutil
from cdebt.config import Config
from cdebt.models import CodeUnit, Probe, Record
from cdebt.probes import callgraph, mutation, runtime
from cdebt.probes.base import grade
from cdebt.scip import index_is_fresh, index_path, load_index
from cdebt.store import Store
from cdebt.units import extract_units

ALL_KINDS = ("runtime", "callgraph", "mutation")
DEFAULT_KINDS = ("runtime", "callgraph")  # mutation is slow: opt in


def build_quiz(
    repo: Path,
    rel: str,
    cfg: Config,
    *,
    n: int = 5,
    seed: int = 0,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    store: Store | None = None,
    per_unit: int = 2,
) -> tuple[list[Probe], list[CodeUnit], list[str]]:
    f = repo / rel
    if not f.is_file():
        raise FileNotFoundError(rel)
    source = f.read_text()
    units = extract_units(rel, source)
    store = store or Store(repo)
    skips: list[str] = []
    pool: dict[str, list[Probe]] = {}
    if "runtime" in kinds:
        ps, sk = runtime.generate(repo, rel, source, units, seed, per_unit)
        pool["runtime"], skips = ps, skips + sk
    if "callgraph" in kinds:
        if index_is_fresh(repo, cfg.extensions):
            ps, sk = callgraph.generate(
                repo, rel, units, load_index(index_path(repo)), seed, per_unit=per_unit
            )
            pool["callgraph"], skips = ps, skips + sk
        else:
            skips.append(
                "callgraph: no fresh SCIP index (run `cdebt index`); skipped, "
                "approximate answers are never used"
            )
    if "mutation" in kinds:
        ps, sk = mutation.generate(repo, rel, source, units, seed)
        pool["mutation"], skips = ps, skips + sk
    # prefer units that are not yet understood, then spread round-robin across units
    status = {u.symbol: store.status(u) for u in units}
    order = sorted(
        {p.symbol for ps in pool.values() for p in ps},
        key=lambda s: (status.get(s, "none") == "valid", s),
    )
    rng = random.Random(seed)
    chosen: list[Probe] = []
    queues = {s: [p for k in kinds for p in pool.get(k, []) if p.symbol == s] for s in order}
    while len(chosen) < n and any(queues.values()):
        for s in order:
            if queues[s] and len(chosen) < n:
                chosen.append(queues[s].pop(0))
    rng.shuffle(chosen)
    return chosen, units, skips


def record_results(
    repo: Path,
    rel: str,
    cfg: Config,
    units: list[CodeUnit],
    probes: list[Probe],
    answers: dict[str, str],
    *,
    label: str | None = None,
    expires_days: int | None = None,
    store: Store | None = None,
) -> list[Record]:
    """Grade every answered probe by rule, write one hash-bound Record per unit."""
    store = store or Store(repo)
    by_unit: dict[str, list[Probe]] = {}
    available: dict[str, int] = {}
    for p in probes:
        available[p.symbol] = available.get(p.symbol, 0) + 1
    for p in probes:
        raw = answers.get(p.id)
        if raw is None:
            continue
        p.user_answer = raw
        p.correct = grade(p, raw)
        by_unit.setdefault(p.symbol, []).append(p)
    uh = {u.symbol: u for u in units}
    exp = datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
    recs: list[Record] = []
    for sym, ps in by_unit.items():
        need = min(cfg.min_probes_per_unit, available[sym])
        passed = len(ps) >= need and all(p.correct for p in ps)
        rec = Record(
            unit_path=rel,
            unit_symbol=sym,
            content_hash=uh[sym].content_hash,
            probes=ps,
            passed=passed,
            expires_at=exp,
            audit_label=label,
        )
        store.add(rec)
        recs.append(rec)
    store.save()
    return recs


def dirty(repo: Path, rel: str) -> bool:
    head = gitutil.show_file(repo, "HEAD", rel)
    return head is None or head != (repo / rel).read_text()
