"""The quiz: a fixed set of questions per function, a score verdict, and a timing check (v1.1).
Verdicts describe the CODE record, never a person.

Per function (unit): ask 3 to 5 questions (default 4), easy to hard. The unit is UNDERSTOOD when
enough are right: with 3 or 4 questions one wrong answer is fine and two wrong is NOT UNDERSTOOD;
with 5, three right is enough (`required_correct`, 60% of the set rounded up). At least one HARD
question must also be right (when the set has one), so a pass cannot come from easy questions
alone. A wrong answer does not earn an extra question.

Timing check: if the score passes but the person answered easy, medium and hard questions in about
the same time (the hardest tier is not meaningfully slower than the easiest, or every answer took
nearly the same time), ONE hard follow-up is asked, answered by TRACING the real execution, with a
visible countdown. Failing it makes the unit NOT UNDERSTOOD, whatever the score.

Privacy: response times live only in local variables while one unit is asked. They are never
attached to a probe or record and never written to disk, logs or notes.
"""

from __future__ import annotations

import ast
import contextlib
import math
import statistics
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from cdebt.agents.probes import Proposal, Rejected, execute
from cdebt.config import Config
from cdebt.models import CodeUnit, Probe, Record
from cdebt.probes import runtime
from cdebt.probes.base import grade
from cdebt.secretguard import drop_secret_probes
from cdebt.store import Store

# (answer or None when the countdown ran out, seconds taken)
AskFn = Callable[[Probe, float | None], tuple[str | None, float]]

REASONS = (
    "verified",
    "verified_after_follow_up",
    "verified_no_follow_up_possible",
    "score_too_low",
    "no_hard_correct",
    "follow_up_failed",
)
RANK = {"easy": 0, "medium": 1, "hard": 2}


class Policy(BaseModel):
    """`.cdebt/policy.toml`. Thresholds, timer and enforce mode; never trust or agent choice."""

    questions_per_unit: int = 4  # asked per function; 3 to 5
    pass_ratio: float = 0.6  # share that must be right; see required_correct
    require_hard: bool = True  # also need one hard question right (when the set has a hard one)
    timer: bool = True  # visible countdown on the follow-up; False asks it untimed (weaker)
    follow_up_seconds: float = 15.0
    hard_vs_easy_ratio: float = (
        1.3  # hard answered in under this x the easy time = suspiciously even
    )
    uniform_spread: float = 0.3  # every answer within this fraction of the median time = even
    min_median_seconds: float = 1.0  # faster than this (scripted input) is not judged on timing
    min_answers_for_timing: int = 3
    max_follow_ups: int = 1
    enforce: bool = False


def load_policy(repo: Path) -> Policy:
    f = repo / ".cdebt" / "policy.toml"
    if f.is_file():
        return Policy.model_validate(tomllib.loads(f.read_text()))
    return Policy()


def required_correct(n: int, pass_ratio: float = 0.6) -> int:
    """How many of `n` questions must be right: 2 of 3, 3 of 4, 3 of 5, 4 of 6. With only 1 or 2
    questions available every one must be right."""
    if n <= 2:
        return n
    return max(1, math.ceil(pass_ratio * n - 1e-9))


@dataclass
class UnitOutcome:
    symbol: str
    cleared: bool
    reason: str
    asked: list[Probe] = field(default_factory=list)
    correct: int = 0  # right answers among the main questions (follow-ups are separate)
    needed: int = 0


def _difficulty(p: Probe) -> str:
    return p.difficulty or "easy"


def answers_look_suspiciously_uniform(times: list[tuple[str, float]], policy: Policy) -> bool:
    """True when the hardest tier was not meaningfully slower than the easiest (under
    `hard_vs_easy_ratio` x), or every answer took nearly the same time (spread under
    `uniform_spread` of the median). Weak evidence: it only triggers ONE extra question, and a
    person who passes it is understood. Too-fast answers (scripted input) are not judged."""
    if len(times) < policy.min_answers_for_timing:
        return False
    secs = [s for _, s in times]
    med = statistics.median(secs)
    if med < policy.min_median_seconds:
        return False
    tiers = {d for d, _ in times}
    if len(tiers) > 1:
        lo = min(tiers, key=lambda d: RANK[d])
        hi = max(tiers, key=lambda d: RANK[d])
        easy = statistics.median([s for d, s in times if d == lo])
        hard = statistics.median([s for d, s in times if d == hi])
        if hard < policy.hard_vs_easy_ratio * easy:
            return True
    return (max(secs) - min(secs)) <= policy.uniform_spread * med


# ---- follow-up and harder-question generation (all answers come from tracing the real code) ----


def _function_node(source: str, unit: CodeUnit) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    leaf = unit.symbol.split(".")[-1]
    lo = min(a for a, _ in unit.ranges)
    for n in ast.walk(ast.parse(source)):
        if (
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == leaf
            and (n.lineno <= lo <= (n.end_lineno or n.lineno))
        ):
            return n
    return None


def _perturb(args: list[Any], kwargs: dict[str, Any]) -> tuple[list[Any], dict[str, Any]] | None:
    def bump(v: Any) -> Any:
        if isinstance(v, bool):
            return not v
        if isinstance(v, int):
            return v + 1
        if isinstance(v, float):
            return v + 1.0
        if isinstance(v, str):
            return v + "a"
        if isinstance(v, list) and v:
            return v[:-1]
        return v

    new_args = [bump(a) for a in args[:1]] + list(args[1:])
    new_kwargs = dict(kwargs)
    if new_args == args and kwargs:
        k = next(iter(kwargs))
        new_kwargs[k] = bump(kwargs[k])
    if (new_args, new_kwargs) == (args, kwargs):
        return None
    return new_args, new_kwargs


def _call_inputs(p: Probe) -> tuple[list[Any], dict[str, Any]] | None:
    ev = p.evidence
    try:
        if "args_repr" in ev:
            return list(ast.literal_eval(str(ev["args_repr"]))), dict(
                ast.literal_eval(str(ev.get("kwargs_repr", "{}")))
            )
        if "args" in ev:  # builtin runtime probes: repr of the args tuple
            return list(ast.literal_eval(str(ev["args"]))), {}
    except (ValueError, SyntaxError):
        return None
    return None


def trace_questions(
    repo: Path,
    rel: str,
    cfg: Config,
    unit: CodeUnit,
    source: str,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    seed: int,
) -> list[Probe]:
    """Hard questions about ONE call of the real function: did a branch run, what was a variable.
    Verified by execution trace; skipped silently when the sandbox cannot verify them."""
    node = _function_node(source, unit)
    if node is None:
        return []
    inputs = {"args": args, "kwargs": kwargs}
    props: list[Proposal] = []
    ifs = [n for n in ast.walk(node) if isinstance(n, (ast.If, ast.For, ast.While))]
    if ifs:
        props.append(
            Proposal(
                target=unit.symbol,
                kind="branch_taken",
                call_inputs=inputs,
                line=ifs[0].body[0].lineno,
            )
        )
    for i, st in enumerate(node.body[:-1]):
        if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
            nxt = node.body[i + 1].lineno
            props.append(
                Proposal(
                    target=unit.symbol,
                    kind="variable_at_line",
                    call_inputs=inputs,
                    line=nxt,
                    variable=st.targets[0].id,
                )
            )
            break
    out: list[Probe] = []
    for prop in props:
        try:
            p = execute(repo, rel, prop, cfg, proposer="builtin", seed=seed, source=source)
        except Rejected:
            continue
        p.evidence["args_repr"] = repr(args)
        p.evidence["kwargs_repr"] = repr(kwargs)
        out.append(p)
    return drop_secret_probes(out)[0]


def followups(
    repo: Path,
    rel: str,
    cfg: Config,
    unit: CodeUnit,
    source: str,
    hard: Probe,
    policy: Policy,
    exclude: set[str] | None = None,
) -> list[Probe]:
    """1-2 follow-ups on the SAME code as the hard question just answered: a branch question and
    a variable question on that very call, then the same function with a slightly changed input."""
    inputs = _call_inputs(hard)
    if inputs is None:
        gen, _ = runtime.generate(repo, rel, source, [unit], 99, per_unit=1)
        inputs = _call_inputs(gen[0]) if gen else None
    if inputs is None:
        return []
    args, kwargs = inputs
    out = trace_questions(repo, rel, cfg, unit, source, args, kwargs, seed=101)
    # fresh inputs of the same function (the traced questions above may all have been asked already)
    cur: tuple[list[Any], dict[str, Any]] = (args, kwargs)
    for seed in (103, 104):
        pert = _perturb(*cur)
        if pert is None:
            break
        cur = pert
        with contextlib.suppress(Rejected):
            out.append(
                execute(
                    repo,
                    rel,
                    Proposal(
                        target=unit.symbol,
                        kind="return_value",
                        call_inputs={"args": pert[0], "kwargs": pert[1]},
                    ),
                    cfg,
                    proposer="builtin",
                    seed=seed,
                    source=source,
                )
            )
    asked = {hard.question, *(exclude or ())}  # never repeat a question already asked
    out = drop_secret_probes(out)[0]
    out = [p for p in out if p.question not in asked]
    return sorted(out, key=lambda p: -RANK[_difficulty(p)])[
        : policy.max_follow_ups
    ]  # hardest first


# ---- the quiz loop ----

_NEAREST = {"easy": ("medium", "hard"), "medium": ("easy", "hard"), "hard": ("medium", "easy")}


def _ask(ask: AskFn, p: Probe, timeout: float | None) -> tuple[bool, float]:
    ans, secs = ask(p, timeout)
    p.user_answer = ans if ans is not None else ""
    p.correct = bool(ans is not None and grade(p, ans))
    return p.correct, secs


def select_questions(pool: list[Probe], n: int) -> list[Probe]:
    """`n` questions spread across easy, medium and hard where they exist (easy, medium, hard,
    then easy, medium), returned easiest first. Missing tiers are filled from the nearest one."""
    by: dict[str, list[Probe]] = {t: [] for t in RANK}
    for p in pool:
        by[_difficulty(p)].append(p)
    chosen: list[Probe] = []
    for tier in ("easy", "medium", "hard", "easy", "medium")[:n]:
        for t in (tier, *_NEAREST[tier]):
            if by[t]:
                chosen.append(by[t].pop(0))
                break
    return sorted(chosen, key=lambda p: RANK[_difficulty(p)])


def _topup(
    repo: Path, rel: str, cfg: Config, unit: CodeUnit, source: str, pool: list[Probe]
) -> list[Probe]:
    """Medium/hard questions from tracing the real function, when the pool lacks them."""
    inputs = next((i for i in (_call_inputs(p) for p in pool) if i), None)
    if inputs is None:
        gen, _ = runtime.generate(repo, rel, source, [unit], 7, per_unit=1)
        inputs = _call_inputs(gen[0]) if gen else None
    if inputs is None:
        return []
    seen = {p.question for p in pool}
    return [
        q
        for q in trace_questions(repo, rel, cfg, unit, source, inputs[0], inputs[1], seed=11)
        if q.question not in seen
    ]


def run_unit(
    repo: Path,
    rel: str,
    cfg: Config,
    unit: CodeUnit,
    source: str,
    pool: list[Probe],
    ask: AskFn,
    policy: Policy,
) -> UnitOutcome:
    """Ask a fixed set, then decide from the score; a suspiciously even pace earns one follow-up."""
    n = min(5, max(3, policy.questions_per_unit))
    if not any(_difficulty(p) == "hard" for p in pool) and len(pool) < n + 2:
        pool = [*pool, *_topup(repo, rel, cfg, unit, source, pool)]
    asked: list[Probe] = []
    times: list[tuple[str, float]] = []  # local to this call, never stored
    for p in select_questions(pool, n):
        _ok, secs = _ask(ask, p, None)
        times.append((_difficulty(p), secs))
        asked.append(p)
    correct = sum(1 for p in asked if p.correct)
    need = required_correct(len(asked), policy.pass_ratio)
    out = UnitOutcome(
        unit.symbol, False, "score_too_low", asked=list(asked), correct=correct, needed=need
    )
    if not asked or correct < need:
        return out
    if (
        policy.require_hard
        and any(_difficulty(p) == "hard" for p in asked)
        and not any(_difficulty(p) == "hard" and p.correct for p in asked)
    ):
        out.reason = "no_hard_correct"
        return out
    if not answers_look_suspiciously_uniform(times, policy):
        out.cleared, out.reason = True, "verified"
        return out
    hardest = max(asked, key=lambda p: (RANK[_difficulty(p)], bool(p.correct)))
    fus = followups(repo, rel, cfg, unit, source, hardest, policy, {p.question for p in asked})[
        : max(1, policy.max_follow_ups)
    ]
    if not fus:
        out.cleared, out.reason = True, "verified_no_follow_up_possible"
        return out
    passed = True
    for fu in fus:
        ok, _ = _ask(ask, fu, policy.follow_up_seconds if policy.timer else None)
        out.asked.append(fu)
        passed = passed and ok
    out.cleared = passed
    out.reason = "verified_after_follow_up" if passed else "follow_up_failed"
    return out


def run_adaptive(
    repo: Path,
    rel: str,
    cfg: Config,
    units: list[CodeUnit],
    source: str,
    probes: list[Probe],
    ask: AskFn,
    policy: Policy,
) -> list[UnitOutcome]:
    """Run every unit that has questions."""
    outcomes: list[UnitOutcome] = []
    by_unit: dict[str, list[Probe]] = {}
    for p in probes:
        by_unit.setdefault(p.symbol, []).append(p)
    for u in units:
        pool = by_unit.get(u.symbol)
        if pool:
            outcomes.append(run_unit(repo, rel, cfg, u, source, pool, ask, policy))
    return outcomes


def record_outcomes(
    rel: str,
    units: list[CodeUnit],
    outcomes: list[UnitOutcome],
    *,
    store: Store,
    label: str | None = None,
    expires_days: int | None = None,
) -> list[Record]:
    """One hash-bound Record per unit. `status_reason` describes the CODE record (never a person);
    no timing value is stored anywhere."""
    by_sym = {u.symbol: u for u in units}
    exp = datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
    recs: list[Record] = []
    for o in outcomes:
        rec = Record(
            unit_path=rel,
            unit_symbol=o.symbol,
            content_hash=by_sym[o.symbol].content_hash,
            probes=o.asked,
            passed=o.cleared,
            expires_at=exp,
            audit_label=label,
            status_reason=o.reason,
        )
        store.add(rec)
        recs.append(rec)
    store.save()
    return recs


# ---- interactive terminal answer with a visible countdown ----


def tty_ask(p: Probe, timeout: float | None) -> tuple[str | None, float]:
    """Read one answer from the terminal. With a timeout, show a countdown; None = ran out."""
    import select

    t0 = time.monotonic()
    if timeout is None or not sys.stdin.isatty():
        sys.stdout.write("answer: ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        return line.strip(), time.monotonic() - t0
    deadline = t0 + timeout
    sys.stdout.write("answer: ")
    sys.stdout.flush()
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            sys.stdout.write("\n  time is up\n")
            return None, time.monotonic() - t0
        sys.stdout.write(f"\r[{left:4.0f} s left] answer: ")
        sys.stdout.flush()
        ready, _, _ = select.select([sys.stdin], [], [], min(1.0, left))
        if ready:
            return sys.stdin.readline().strip(), time.monotonic() - t0
