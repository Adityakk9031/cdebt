"""Validity study kit: does familiarity predict answering execution-verified questions?

Design (see docs/STUDY_PROTOCOL.md): a coordinator builds a FIXED set of probes on a repo
(`init`), sends participants the study file (questions only), each participant answers and
self-reports per question whether they WROTE, REVIEWED or NEVER SAW the code (`run`), and the
coordinator analyses the pooled responses (`analyze`).

Privacy by construction:
  * participants get no answers (the study file holds none; grading needs a coordinator-held
    secret via HMAC, so options cannot be brute-forced from the file);
  * a participant id is a random 8-hex string, never derived from a name, host or account;
  * `analyze` prints team-level and pooled aggregates only: no per-participant result, no ids,
    and any cell with fewer than `min_participants` distinct participants is suppressed;
  * nothing here ranks people, and a team label is free text the participants choose."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
import secrets
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from cdebt.stats import wilson

SCHEMA = "cdebt-study/1"
Familiarity = Literal["wrote", "reviewed", "never"]
FAMILIAR = ("wrote", "reviewed")


class StudyProbe(BaseModel):
    id: str
    kind: str
    path: str
    unit: str
    question: str
    options: list[str]
    multi: bool
    verified_by: str
    unit_ai_share: float | None = None  # share of the unit's lines attributed to AI at `commit`


class StudyFile(BaseModel):
    schema_id: str = Field(default=SCHEMA, alias="schema")
    study_id: str
    created: str  # date only
    repo_name: str
    commit: str
    seed: int
    probes: list[StudyProbe]
    instructions: str = (
        "Answer from what you know. Do not run the code, search, or ask an AI or a colleague; "
        "if you did consult something, say so. For each question say whether you wrote the code "
        "it asks about, reviewed it, or never saw it. Do not enter your name."
    )

    model_config = {"populate_by_name": True}


class SecretFile(BaseModel):
    """Coordinator-only. Never send this to participants."""

    schema_id: str = Field(default=SCHEMA, alias="schema")
    study_id: str
    key: str  # hex HMAC key
    answer_macs: dict[str, str]  # probe id -> HMAC of the canonical correct answer

    model_config = {"populate_by_name": True}


class Answer(BaseModel):
    answer: str
    familiarity: Familiarity
    consulted: bool = False  # self-reported: looked it up / asked an AI / asked someone


class ResponseFile(BaseModel):
    schema_id: str = Field(default=SCHEMA, alias="schema")
    study_id: str
    participant: str  # random 8-hex, not derived from anything about the person
    team: str | None = None  # free-text team label chosen by the participant; never a name
    date: str
    answers: dict[str, Answer]

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------------------------
def canonical(probe_answer: str, multi: bool) -> str:
    """Stable form of a correct answer: sorted JSON list for multi-select, text otherwise."""
    if multi:
        return json.dumps(sorted(json.loads(probe_answer)))
    return probe_answer.strip()


def _mac(key: bytes, study_id: str, probe_id: str, answer: str) -> str:
    return hmac.new(key, f"{study_id}|{probe_id}|{answer}".encode(), hashlib.sha256).hexdigest()


def new_participant_id() -> str:
    return secrets.token_hex(4)


def build_study(
    repo_name: str, commit: str, seed: int, probes: list[tuple[StudyProbe, str]]
) -> tuple[StudyFile, SecretFile]:
    """`probes` = (public probe, canonical correct answer). Returns (study file, secret file)."""
    study_id = secrets.token_hex(8)
    key = secrets.token_bytes(32)
    pub = [p for p, _ in probes]
    macs = {p.id: _mac(key, study_id, p.id, ans) for p, ans in probes}
    sf = StudyFile(
        study_id=study_id,
        created=datetime.now(UTC).date().isoformat(),
        repo_name=repo_name,
        commit=commit,
        seed=seed,
        probes=pub,
    )
    return sf, SecretFile(study_id=study_id, key=key.hex(), answer_macs=macs)


def resolve_option(probe: StudyProbe, raw: str) -> str:
    """Participant text ('B', 'A,C', option text, 'none') -> canonical comparable answer."""
    from cdebt.probes.base import LETTERS, NONE_OPTION

    raw = raw.strip()
    parts = [x.strip() for x in raw.split(",")] if probe.multi else [raw]
    chosen: list[str] = []
    for p in parts:
        if len(p) == 1 and p.upper() in LETTERS[: len(probe.options)]:
            chosen.append(probe.options[LETTERS.index(p.upper())])
        elif probe.multi and p.lower() == "none":
            chosen.append(NONE_OPTION)
        elif p:
            chosen.append(p)
    return json.dumps(sorted(chosen)) if probe.multi else (chosen[0] if chosen else "")


def grade(study: StudyFile, secret: SecretFile, probe_id: str, raw: str) -> bool:
    probe = next(p for p in study.probes if p.id == probe_id)
    key = bytes.fromhex(secret.key)
    mine = _mac(key, study.study_id, probe_id, resolve_option(probe, raw))
    return hmac.compare_digest(mine, secret.answer_macs[probe_id])


def validate_response(study: StudyFile, resp: ResponseFile) -> None:
    if resp.study_id != study.study_id:
        raise ValueError("response belongs to a different study")
    ids = {p.id for p in study.probes}
    extra = set(resp.answers) - ids
    if extra:
        raise ValueError(f"response answers unknown probes: {sorted(extra)}")


# ---- analysis ----------------------------------------------------------------------------------
class Cell(BaseModel):
    correct: int | None
    n: int | None
    rate: float | None
    ci95: tuple[float, float] | None
    participants: int
    suppressed: bool = False


def _cell(rows: list[tuple[str, bool]], min_p: int) -> Cell:
    people = {pid for pid, _ in rows}
    if len(people) < min_p:
        return Cell(
            correct=None, n=None, rate=None, ci95=None, participants=len(people), suppressed=True
        )
    k, n = sum(1 for _, ok in rows if ok), len(rows)
    lo, hi = wilson(k, n)  # NB: treats responses as independent; the cluster CI below is primary
    return Cell(correct=k, n=n, rate=k / n if n else None, ci95=(lo, hi), participants=len(people))


class Analysis(BaseModel):
    study_id: str
    participants: int
    teams: int
    responses: int
    min_participants: int
    by_familiarity: dict[str, Cell]
    familiar_vs_never: dict[str, object]
    sensitivity_excluding_consulted: dict[str, object]
    by_kind: dict[str, dict[str, Cell]]
    by_ai_share: dict[str, dict[str, Cell]]
    by_team: dict[str, Cell]
    notes: list[str]


def _diff(rows: list[tuple[str, str, bool]]) -> tuple[float, int, int] | None:
    """(familiar_rate - never_rate, n_familiar, n_never) or None if a group is empty."""
    fam = [ok for _, f, ok in rows if f in FAMILIAR]
    nev = [ok for _, f, ok in rows if f == "never"]
    if not fam or not nev:
        return None
    return sum(fam) / len(fam) - sum(nev) / len(nev), len(fam), len(nev)


def _cluster_bootstrap(
    rows: list[tuple[str, str, bool]], reps: int, seed: int
) -> dict[str, object]:
    d = _diff(rows)
    if d is None:
        return {"difference": None, "ci95": None, "note": "need both familiar and never responses"}
    by_p: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for r in rows:
        by_p[r[0]].append(r)
    people = sorted(by_p)
    rng = random.Random(seed)
    vals: list[float] = []
    for _ in range(reps):
        sample: list[tuple[str, str, bool]] = []
        for _i in people:
            sample.extend(by_p[rng.choice(people)])
        dd = _diff(sample)
        if dd is not None:
            vals.append(dd[0])
    vals.sort()
    ci = None
    if len(vals) >= max(50, reps // 10):
        ci = (vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))])
    return {
        "difference": d[0],
        "familiar_n": d[1],
        "never_n": d[2],
        "ci95": ci,
        "bootstrap": f"participants resampled, {reps} replicates, seed {seed}",
        "hypothesis_supported": bool(ci is not None and ci[0] > 0),
    }


def analyze(
    study: StudyFile,
    secret: SecretFile,
    responses: list[ResponseFile],
    *,
    min_participants: int = 5,
    reps: int = 2000,
    seed: int = 0,
) -> Analysis:
    """Pooled, team-level analysis. Never returns anything about an individual."""
    seen: set[str] = set()
    graded: list[tuple[str, str, bool, bool, str, str, str]] = []
    # participant, familiarity, correct, consulted, kind, ai-bucket, team
    probes = {p.id: p for p in study.probes}
    notes: list[str] = []
    for r in responses:
        validate_response(study, r)
        if r.participant in seen:
            raise ValueError("duplicate participant id in responses")
        seen.add(r.participant)
        for pid, a in r.answers.items():
            p = probes[pid]
            share = p.unit_ai_share
            bucket = (
                "unknown" if share is None else ("ai-heavy" if share >= 0.5 else "mostly-human")
            )
            graded.append(
                (
                    r.participant,
                    a.familiarity,
                    grade(study, secret, pid, a.answer),
                    a.consulted,
                    p.kind,
                    bucket,
                    r.team or "(none)",
                )
            )
    n_people = len(seen)
    teams = {g[6] for g in graded}
    if n_people < min_participants:
        notes.append(
            f"only {n_people} participants (< {min_participants}): cells are suppressed; "
            "this output is not interpretable"
        )

    def cells(
        key_idx: int, keys: list[str], rows: list[tuple[str, str, bool, bool, str, str, str]]
    ) -> dict[str, Cell]:
        return {
            k: _cell([(g[0], g[2]) for g in rows if g[key_idx] == k], min_participants)
            for k in keys
        }

    fam = cells(1, ["wrote", "reviewed", "never"], graded)
    tri = [(g[0], g[1], g[2]) for g in graded]
    tri_clean = [(g[0], g[1], g[2]) for g in graded if not g[3]]
    kinds = sorted({g[4] for g in graded})
    by_kind = {
        k: cells(1, ["wrote", "reviewed", "never"], [g for g in graded if g[4] == k]) for k in kinds
    }
    by_ai = {
        b: cells(1, ["wrote", "reviewed", "never"], [g for g in graded if g[5] == b])
        for b in sorted({g[5] for g in graded})
    }
    by_team = {
        t: _cell([(g[0], g[2]) for g in graded if g[6] == t], min_participants)
        for t in sorted(teams)
    }
    consulted = sum(1 for g in graded if g[3])
    if consulted:
        notes.append(
            f"{consulted} of {len(graded)} responses were self-reported as consulted "
            "(looked up / asked); see the sensitivity analysis"
        )
    primary = (
        _cluster_bootstrap(tri, reps, seed)
        if n_people >= min_participants
        else {"difference": None, "ci95": None, "note": "too few participants"}
    )
    sens = (
        _cluster_bootstrap(tri_clean, reps, seed)
        if n_people >= min_participants
        else {"difference": None, "ci95": None, "note": "too few participants"}
    )
    return Analysis(
        study_id=study.study_id,
        participants=n_people,
        teams=len(teams),
        responses=len(graded),
        min_participants=min_participants,
        by_familiarity=fam,
        familiar_vs_never=primary,
        sensitivity_excluding_consulted=sens,
        by_kind=by_kind,
        by_ai_share=by_ai,
        by_team=by_team,
        notes=notes,
    )


# ---- power -------------------------------------------------------------------------------------
def required_responses_per_group(
    p1: float, p2: float, alpha: float = 0.05, power: float = 0.8, deff: float = 1.0
) -> int:
    """Responses per group to detect p1 > p2 (one-sided), normal approximation, times a design
    effect for clustering. A planning estimate, not a guarantee."""
    from statistics import NormalDist

    z = NormalDist()
    za, zb = z.inv_cdf(1 - alpha), z.inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = 2 * (za + zb) ** 2 * pbar * (1 - pbar) / (p1 - p2) ** 2
    return math.ceil(n * deff)


def design_effect(probes_per_participant: int, icc: float) -> float:
    return 1 + (probes_per_participant - 1) * icc


def write_json(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(by_alias=True, indent=2) + "\n")
