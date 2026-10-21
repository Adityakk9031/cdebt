"""Quiz orchestration: generate probes, grade DETERMINISTICALLY, bind records to hashes."""

from __future__ import annotations

import hashlib
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cdebt import gitutil, sandbox
from cdebt.agents.userconfig import AgentChoice
from cdebt.cache import Cache, make_key
from cdebt.config import Config
from cdebt.models import CodeUnit, Probe, Record
from cdebt.probes import callgraph, constants, js, mutation, runtime
from cdebt.probes.base import grade
from cdebt.scip import index_is_fresh, load_all
from cdebt.store import Store
from cdebt.units import extract_units

ALL_KINDS = ("runtime", "callgraph", "constant", "mutation")
DEFAULT_KINDS = ("runtime", "callgraph", "constant")  # mutation is slow: opt in


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
    agent: AgentChoice | None = None,
    agent_per_target: int = 8,
    agent_timeout: float = 90.0,
) -> tuple[list[Probe], list[CodeUnit], list[str]]:
    f = repo / rel
    if not f.is_file():
        raise FileNotFoundError(rel)
    source = f.read_text()
    units = extract_units(rel, source)
    store = store or Store(repo)
    skips: list[str] = []
    pool: dict[str, list[Probe]] = {}
    is_js = rel.endswith(js.JS_EXT + js.UNSUPPORTED_EXT)
    if "runtime" in kinds:
        gen = js.generate_runtime if is_js else runtime.generate
        ps, sk = gen(repo, rel, source, units, seed, per_unit)
        pool["runtime"], skips = ps, skips + sk
    if "callgraph" in kinds:
        if index_is_fresh(repo, cfg.extensions):
            ps, sk = callgraph.generate(repo, rel, units, load_all(repo), seed, per_unit=per_unit)
            pool["callgraph"], skips = ps, skips + sk
        else:
            skips.append(
                "callgraph: no fresh SCIP index (run `cdebt index`); skipped, "
                "approximate answers are never used"
            )
    if "constant" in kinds:
        cgen = js.generate_constants if is_js else constants.generate
        ps, sk = cgen(repo, rel, source, units, seed, per_unit)
        pool["constant"], skips = ps, skips + sk
    if "mutation" in kinds:
        ps, sk = mutation.generate(repo, rel, source, units, seed)
        pool["mutation"], skips = ps, skips + sk
    if agent is not None and agent.name != "builtin" and rel.endswith(".py"):
        from cdebt.agents import propose_with_agent

        aps, notes = propose_with_agent(
            repo, rel, cfg, agent, per_target=agent_per_target, timeout=agent_timeout,
            seed=seed, store=store,
        )  # fmt: skip
        pool["agent"], skips = aps, skips + notes
    # prefer units that are not yet understood, then spread round-robin across units
    status = {u.symbol: store.status(u) for u in units}
    order = sorted(
        {p.symbol for ps in pool.values() for p in ps},
        key=lambda s: (status.get(s, "none") == "verified", s),
    )
    rng = random.Random(seed)
    chosen: list[Probe] = []
    order_kinds = ("agent", *kinds)  # the developer's agent's questions first, builtin as filler
    queues = {s: [p for k in order_kinds for p in pool.get(k, []) if p.symbol == s] for s in order}
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


def _tree_fingerprint(repo: Path, cfg: Config) -> str:
    """Hash of every tracked source file (probe availability can depend on any import). Computed
    fresh each time: it is cheap next to running probes and must never be stale."""
    from cdebt.scip import fingerprint

    return fingerprint(repo, cfg.extensions)


def _cdebt_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cdebt")
    except PackageNotFoundError:  # pragma: no cover
        return "dev"


def unit_probe_kinds(
    repo: Path, rel: str, cfg: Config, source: str | None = None
) -> tuple[dict[str, set[str]], list[str]]:
    """Which probe kinds CAN be generated per unit symbol (one probe each; mutation excluded:
    slow, opt-in). Used by `attest` (is this unit quizzable?) and `coverage`."""
    f = repo / rel
    source = source if source is not None else f.read_text()
    units = extract_units(rel, source)
    cache = Cache(repo)
    ckey = make_key(
        "probe-kinds",
        _cdebt_version(),
        rel,
        hashlib.sha256(source.encode()).hexdigest(),
        _tree_fingerprint(repo, cfg),
        sandbox.DEFAULT_ISOLATION,
        sandbox.docker_available(),
        sandbox._sandbox_exec_available(),
        sys.version_info[:2],
        js.node_version(),
        index_is_fresh(repo, cfg.extensions),
    )
    hit = cache.get("probe-kinds", ckey)
    if hit is not None:
        return {k: set(v) for k, v in hit["avail"].items()}, list(hit["skips"])
    avail: dict[str, set[str]] = {}
    skips: list[str] = []
    if rel.endswith(js.JS_EXT + js.UNSUPPORTED_EXT):
        gens: list[tuple[list[Probe], list[str]]] = [
            js.generate_runtime(repo, rel, source, units, 0, 1),
            js.generate_constants(repo, rel, source, units, 0, 1),
        ]
    else:
        gens = [
            runtime.generate(repo, rel, source, units, 0, 1),
            constants.generate(repo, rel, source, units, 0, 1),
        ]
    if index_is_fresh(repo, cfg.extensions):
        gens.append(callgraph.generate(repo, rel, units, load_all(repo), 0, per_unit=1))
    for ps, sk in gens:
        skips += sk
        for p in ps:
            avail.setdefault(p.symbol, set()).add(p.kind)
    cache.put(
        "probe-kinds", ckey, {"avail": {k: sorted(v) for k, v in avail.items()}, "skips": skips}
    )
    return avail, skips


def attest(
    repo: Path,
    rel: str,
    cfg: Config,
    note: str,
    *,
    symbols: list[str] | None = None,
    label: str | None = None,
    expires_days: int | None = None,
    force: bool = False,
    store: Store | None = None,
) -> tuple[list[Record], list[str]]:
    """Record a hash-bound, NON-VERIFIED human sign-off. Module-level units may always be
    attested. A function that quiz COULD verify is refused unless `force` (then it is stored with
    scope `function-quizzable`, which the default gate does not accept)."""
    if not note.strip():
        raise ValueError("an attestation needs a non-empty --note")
    source = (repo / rel).read_text()
    units = extract_units(rel, source)
    store = store or Store(repo)
    avail, _ = unit_probe_kinds(repo, rel, cfg, source)
    wanted = set(symbols) if symbols else None
    unknown = (wanted or set()) - {u.symbol for u in units}
    if unknown:
        raise ValueError(f"no such unit(s) in {rel}: {sorted(unknown)}")
    exp = datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
    recs: list[Record] = []
    notes: list[str] = []
    for u in units:
        if wanted is not None and u.symbol not in wanted:
            continue
        st = store.status(u)
        if st == "verified":
            notes.append(f"{u.symbol}: already verified, nothing to attest")
            continue
        if st == "attested" and wanted is None:
            continue
        module = u.symbol.startswith("<")
        quizzable = not module and bool(avail.get(u.symbol))
        if quizzable and not force:
            notes.append(
                f"{u.symbol}: REFUSED, quiz can verify it ({sorted(avail[u.symbol])}); "
                "run `cdebt quiz` or pass --force to attest anyway"
            )
            continue
        scope = (
            "module" if module else ("function-quizzable" if quizzable else "function-unquizzable")
        )
        rec = Record(
            unit_path=rel,
            unit_symbol=u.symbol,
            content_hash=u.content_hash,
            passed=True,
            expires_at=exp,
            audit_label=label,
            kind="attested",
            note=note.strip(),
            scope=scope,
        )
        store.add(rec)
        recs.append(rec)
        notes.append(f"{u.symbol}: attested ({scope}) - NON-VERIFIED")
    if recs:
        store.save()
    return recs, notes
