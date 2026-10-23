"""Adaptive quiz with timed follow-ups (v1.1). Verdicts describe the CODE record, never a person.

Flow per unit: ask a mix of easy and hard questions; the unit clears when enough answers are right,
including a hard one. A wrong answer earns a harder question on the same unit. If a hard question
was answered no slower than easy ones (ratio below the policy limit), 1-2 follow-ups are asked on
that same code, answered by TRACING the real execution, with a visible countdown. At most
`max_rounds` rounds per unit, then it stays NOT UNDERSTOOD.

Privacy: response times live only in the in-memory `_Session.times` list of one quiz session. They
are never attached to a probe or record and never written to disk, logs or notes.
"""

from __future__ import annotations

import ast
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

REASONS = ("verified", "verified_no_hard_available", "follow_up_failed", "max_rounds_reached")


class Policy(BaseModel):
    """`.cdebt/policy.toml`. Timer, thresholds and enforce mode; never trust or agent choice."""

    questions_per_unit: int = 3
    required_correct: int = 2
    max_rounds: int = 3
    timer: bool = True
    follow_up_seconds: float = 15.0
    hard_vs_easy_ratio: float = 1.3
    min_answers_for_timing: int = 4
    max_follow_ups: int = 2
    enforce: bool = False


def load_policy(repo: Path) -> Policy:
    f = repo / ".cdebt" / "policy.toml"
    if f.is_file():
        return Policy.model_validate(tomllib.loads(f.read_text()))
    return Policy()


@dataclass
class UnitOutcome:
    symbol: str
    cleared: bool
    reason: str
    asked: list[Probe] = field(default_factory=list)
    rounds: int = 0


@dataclass
class _Session:
    """Everything timing-related for ONE quiz session. Discarded when the session ends."""

    times: list[tuple[str, float]] = field(default_factory=list)  # (difficulty, seconds)


def _difficulty(p: Probe) -> str:
    return p.difficulty or "easy"


def timing_looks_uniform(
    times: list[tuple[str, float]], hard_seconds: float, policy: Policy
) -> bool:
    """True when a hard question took less than `ratio` x the median easy time (after enough
    answers). Weak evidence: it only triggers extra questions, never a verdict on a person."""
    easy = [s for d, s in times if d == "easy"]
    if len(times) < policy.min_answers_for_timing or not easy:
        return False
    return hard_seconds < policy.hard_vs_easy_ratio * statistics.median(easy)


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
    repo: Path, rel: str, cfg: Config, unit: CodeUnit, source: str, hard: Probe, policy: Policy
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
    pert = _perturb(args, kwargs)
    if pert is not None and len(out) < policy.max_follow_ups:
        try:
            p = execute(
                repo,
                rel,
                Proposal(
                    target=unit.symbol,
                    kind="return_value",
                    call_inputs={"args": pert[0], "kwargs": pert[1]},
                ),
                cfg,
                proposer="builtin",
                seed=103,
                source=source,
            )
            out.append(p)
        except Rejected:
            pass
    asked = {hard.question}
    out = drop_secret_probes(out)[0]
    return [p for p in out if p.question not in asked][: policy.max_follow_ups]


# ---- the adaptive loop ----


def _ask(ask: AskFn, p: Probe, timeout: float | None) -> tuple[bool, float]:
    ans, secs = ask(p, timeout)
    p.user_answer = ans if ans is not None else ""
    p.correct = bool(ans is not None and grade(p, ans))
    return p.correct, secs


def run_unit(
    repo: Path,
    rel: str,
    cfg: Config,
    unit: CodeUnit,
    source: str,
    pool: list[Probe],
    ask: AskFn,
    policy: Policy,
    sess: _Session,
) -> UnitOutcome:
    out = UnitOutcome(symbol=unit.symbol, cleared=False, reason="max_rounds_reached")
    unused = list(pool)
    state: dict[str, Any] = {
        "hard_available": any(_difficulty(p) == "hard" for p in pool),
        "attempts": 0,
        "no_more": False,
    }
    asked_q = {p.question for p in pool}

    def take(level: str, n: int) -> list[Probe]:
        got: list[Probe] = []
        for p in list(unused):
            if len(got) >= n:
                break
            if level == "any" or _difficulty(p) == level:
                got.append(p)
                unused.remove(p)
        return got

    def more_hard() -> list[Probe]:
        """A fresh hard question from tracing the real function, on slightly changed inputs each
        time, so a unit never runs out of questions before the round cap."""
        if state["no_more"]:
            return []
        state["attempts"] += 1
        if state["attempts"] > policy.max_rounds + 2:
            state["no_more"] = True
            return []
        inputs = next((i for i in (_call_inputs(p) for p in pool) if i), None)
        if inputs is None:
            gen, _ = runtime.generate(repo, rel, source, [unit], 7, per_unit=1)
            inputs = _call_inputs(gen[0]) if gen else None
        if inputs is None:
            state["no_more"] = True
            return []
        args, kwargs = inputs
        for _ in range(state["attempts"] - 1):
            args, kwargs = _perturb(args, kwargs) or (args, kwargs)
        qs = [
            q
            for q in trace_questions(
                repo, rel, cfg, unit, source, args, kwargs, seed=11 + state["attempts"]
            )
            if q.question not in asked_q
        ]
        asked_q.update(q.question for q in qs)
        if qs:
            state["hard_available"] = True
        else:
            state["no_more"] = True
        return qs

    main: list[Probe] = []  # graded questions (follow-ups are tracked separately)
    pending_failure = False
    for rnd in range(1, policy.max_rounds + 1):
        out.rounds = rnd
        if rnd == 1:
            batch = take("hard", 1)
            batch += take("easy", policy.questions_per_unit - len(batch))
            batch += take("any", policy.questions_per_unit - len(batch))
            if not any(_difficulty(p) == "hard" for p in batch):
                extra = more_hard()
                if extra:
                    unused.extend(extra[1:])
                    batch += extra[:1]
        else:  # a wrong answer or failed follow-up earns a HARDER question on the same unit
            batch = take("hard", 1)
            if not batch:
                extra = more_hard()
                if extra:
                    unused.extend(extra[1:])
                    batch = extra[:1]
            batch = batch or take("easy", 1)
        if not batch:
            out.reason = "follow_up_failed" if pending_failure else "max_rounds_reached"
            break
        pending_failure = False
        hard_right: list[tuple[Probe, float]] = []
        i = 0
        bonus_given = False  # one immediate harder question per round, not one per wrong answer
        while i < len(batch):
            p = batch[i]
            i += 1
            ok, secs = _ask(ask, p, None)
            sess.times.append((_difficulty(p), secs))
            main.append(p)
            out.asked.append(p)
            if ok and _difficulty(p) == "hard":
                hard_right.append((p, secs))
            elif not ok and not bonus_given:  # a wrong answer earns a HARDER question at once
                bonus_given = True
                harder = take("hard", 1)
                if not harder:
                    extra = more_hard()
                    unused.extend(extra[1:])
                    harder = extra[:1]
                batch.extend(harder)
        for hp, secs in hard_right:
            if timing_looks_uniform(sess.times, secs, policy):
                for fu in followups(repo, rel, cfg, unit, source, hp, policy):
                    ok, _ = _ask(ask, fu, policy.follow_up_seconds if policy.timer else None)
                    out.asked.append(fu)
                    pending_failure = pending_failure or not ok
                break  # at most one follow-up set per round
        correct = sum(1 for p in main if p.correct)
        hard_ok = any(p.correct and _difficulty(p) == "hard" for p in main)
        exhausted = not unused and state["no_more"]
        enough = correct >= policy.required_correct or (
            exhausted and bool(main) and all(p.correct for p in main)
        )
        if enough and (hard_ok or not state["hard_available"]) and not pending_failure:
            out.cleared = True
            out.reason = "verified" if hard_ok else "verified_no_hard_available"
            return out
        out.reason = "follow_up_failed" if pending_failure else "max_rounds_reached"
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
    """Run every unit that has probes. Times live in one session object, dropped on return."""
    sess = _Session()
    outcomes: list[UnitOutcome] = []
    by_unit: dict[str, list[Probe]] = {}
    for p in probes:
        by_unit.setdefault(p.symbol, []).append(p)
    try:
        for u in units:
            pool = by_unit.get(u.symbol)
            if pool:
                outcomes.append(run_unit(repo, rel, cfg, u, source, pool, ask, policy, sess))
    finally:
        sess.times.clear()
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
