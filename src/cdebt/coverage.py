"""How much of the highest-debt code can each mechanism actually clear?

Per file, over the top-N files by debt score, every AI-bearing unit is classified:
  verifiable   at least one execution-verified probe can be generated (runtime / call-graph with
               a fresh SCIP index / module constants). Mutation probes are slow and opt-in, so
               they are NOT counted here.
  attest-only  no probe can be generated: a Python unit that can only be cleared by a
               NON-VERIFIED human attestation.
  no-quiz      the file's language has no quiz support at all (TypeScript/JavaScript, ...)
               unless a SCIP index yields call-graph probes. Also attestation-only in practice.
Counts are AI lines (line-weighted) and files. Read-only: nothing is written to the repo."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from cdebt.config import Config
from cdebt.mapper import compute_map
from cdebt.probes.js import JS_EXT as JS_PROBE_EXT
from cdebt.quiz import unit_probe_kinds
from cdebt.sandbox import docker_available
from cdebt.scip import ScipIndex, index_is_fresh


class FileCoverage(BaseModel):
    path: str
    score: float
    ai_lines: int
    verifiable_lines: int
    attest_only_lines: int
    no_quiz_lines: int
    klass: str  # verifiable | partly verifiable | attest-only | no-quiz
    probe_kinds: list[str]


class CoverageResult(BaseModel):
    top_n: int
    files: list[FileCoverage]
    files_by_class: dict[str, int]
    verifiable_share: float | None  # of AI lines in the top-N files
    attest_only_share: float | None
    no_quiz_share: float | None
    scip_index: bool
    sandbox: str
    notes: list[str]


def compute_coverage(
    repo: Path, cfg: Config, top_n: int = 25, rev: str = "HEAD", scip_idx: ScipIndex | None = None
) -> CoverageResult:
    res = compute_map(repo, cfg, rev, scip_idx=scip_idx)
    rows: list[FileCoverage] = []
    for f in [x for x in res.files if x.ai_lines > 0][:top_n]:
        kinds: set[str] = set()
        ver = att = noq = 0
        if f.path.endswith((".py", *JS_PROBE_EXT)) or index_is_fresh(repo, cfg.extensions):
            avail, _ = unit_probe_kinds(repo, f.path, cfg)
        else:
            avail = {}
        for sym, n in f.ai_units.items():
            ks = avail.get(sym, set())
            if ks:
                ver += n
                kinds |= ks
            elif f.path.endswith((".py", *JS_PROBE_EXT)):
                att += n
            else:
                noq += n
        total = ver + att + noq
        if total == 0:
            continue
        klass = (
            "verifiable"
            if ver == total
            else "attest-only"
            if att == total
            else "no-quiz"
            if noq == total
            else "partly verifiable"
        )
        rows.append(
            FileCoverage(
                path=f.path,
                score=f.score,
                ai_lines=total,
                verifiable_lines=ver,
                attest_only_lines=att,
                no_quiz_lines=noq,
                klass=klass,
                probe_kinds=sorted(kinds),
            )
        )
    tot = sum(r.ai_lines for r in rows)

    def share(k: str) -> float | None:
        return sum(getattr(r, k) for r in rows) / tot if tot else None

    by: dict[str, int] = {}
    for r in rows:
        by[r.klass] = by.get(r.klass, 0) + 1
    fresh = index_is_fresh(repo, cfg.extensions)
    notes = ["Mutation probes are opt-in and slow; not counted."]
    if not fresh:
        notes.append(
            "No fresh SCIP index: call-graph probes are not counted "
            "(run `cdebt index`). TypeScript/JavaScript stay no-quiz without one."
        )
    return CoverageResult(
        top_n=top_n,
        files=rows,
        files_by_class=by,
        verifiable_share=share("verifiable_lines"),
        attest_only_share=share("attest_only_lines"),
        no_quiz_share=share("no_quiz_lines"),
        scip_index=fresh,
        sandbox="docker" if docker_available() else "host (audit hook + rlimits [+ sandbox-exec])",
        notes=notes,
    )
