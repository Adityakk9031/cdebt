"""The quiz: a fixed set of questions per function, a score verdict, and a timing check (v1.1).
Verdicts describe the CODE record, never a person.

Per function (unit): ask 3 to 5 questions (default 4), easy to hard. The unit is UNDERSTOOD when
enough are right: with 3 or 4 questions one wrong answer is fine and two wrong is NOT UNDERSTOOD;
with 5, three right is enough (`required_correct`, 60% of the set rounded up). At least one HARD
question must also be right (when the set has one), so a pass cannot come from easy questions
alone. A wrong answer does not earn an extra question.

Timing check: if the score passes but the hard answers were NOT meaningfully slower than the easy
and medium ones (hard median under 1.3 x the easy+medium median), 1 or 2 random short-answer
follow-ups are asked about the same code (a changed input, a branch taken, a variable's value),
answered by TRACING the real execution. The countdown is 75% of that person's own median
answer time, kept between 5 s and 15 s, and visible. Right and in time on all of them: understood.
Wrong or too slow: NOT UNDERSTOOD (`follow_up_failed`), whatever the score.

Privacy: response times live only in local variables while one unit is asked. They are never
attached to a probe or record and never written to disk, logs or notes.
"""

from __future__ import annotations

import ast
import contextlib
import math
import os
import random
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
# called before a function's questions: (symbol, function number, functions in this run, questions)
AnnounceFn = Callable[[str, int, int, int], None]


@dataclass
class RunBudget:
    """Main questions still allowed in this quiz run (`max_questions_per_run`); None = no cap."""

    remaining: int | None = None


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

    questions_per_unit: int = 3  # asked per function; 3 to 5
    max_questions_per_run: int = (
        6  # main questions in one quiz/push, all functions together; 0 = no cap
    )
    pass_ratio: float = 0.6  # share that must be right; see required_correct
    require_hard: bool = True  # also need one hard question right (when the set has a hard one)
    timer: bool = True  # visible countdown on the follow-ups; False asks them untimed (weaker)
    follow_up_fraction: float = 0.75  # timer = this x the person's OWN median answer time...
    follow_up_min_seconds: float = 5.0  # ...but never under this
    follow_up_max_seconds: float = 15.0  # ...and never over this
    hard_vs_easy_ratio: float = 1.3  # hard answered in under this x median(easy+medium) = even
    min_median_seconds: float = 1.0  # faster than this (scripted input) is not judged on timing
    min_answers_for_timing: int = 3
    max_follow_ups: int = 2  # 1 to this many, picked at random
    short_answer_chars: int = 12  # follow-ups must have an answer this short (a number, yes/no)
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
    questions: int = 0  # size of the main set (`asked` also holds any follow-ups)


def _difficulty(p: Probe) -> str:
    return p.difficulty or "easy"


def answers_look_suspiciously_uniform(times: list[tuple[str, float]], policy: Policy) -> bool:
    """True when the hard answers were NOT meaningfully slower than the easy and medium ones: the
    hard median is under `hard_vs_easy_ratio` x the median of the easy and medium answers. Needs
    at least one hard and one easier answer. Weak evidence: it only earns 1-2 follow-ups, and a
    person who passes them is understood. Too-fast answers (scripted input) are not judged."""
    if len(times) < policy.min_answers_for_timing:
        return False
    if statistics.median([s for _, s in times]) < policy.min_median_seconds:
        return False
    hard = [s for d, s in times if d == "hard"]
    easier = [s for d, s in times if d != "hard"]
    if not hard or not easier:
        return False
    return statistics.median(hard) < policy.hard_vs_easy_ratio * statistics.median(easier)


def follow_up_timer(times: list[tuple[str, float]], policy: Policy) -> float:
    """Seconds allowed per follow-up: `follow_up_fraction` of this person's own median answer time,
    kept between the policy minimum and maximum (usual pace 9-10 s gives about 7 s). Computed from
    in-memory times for this one unit and never stored."""
    med = statistics.median([s for _, s in times]) if times else 0.0
    lo = policy.follow_up_min_seconds
    hi = max(lo, policy.follow_up_max_seconds)
    return round(min(hi, max(lo, policy.follow_up_fraction * med)), 1)


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
    """Candidate follow-ups on the SAME code as the hard question just answered: a branch question
    and a variable question on that very call, and the same function with changed inputs. Only
    short answers (a number, yes/no, a short value) so an honest person can type one in time.
    `pick_followups` chooses among them at random."""
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
    return [
        p
        for p in out
        if p.question not in asked
        and len(p.answer) <= policy.short_answer_chars
        and "\n" not in p.answer
    ]


def _rng() -> random.Random:
    """Unpredictable by default (so follow-ups cannot be prepared). `CDEBT_FOLLOWUP_SEED` (an
    environment variable, never read from the repo) makes demos and tests repeatable."""
    seed = os.environ.get("CDEBT_FOLLOWUP_SEED")
    return random.Random(seed) if seed else random.SystemRandom()


def pick_followups(
    candidates: list[Probe], policy: Policy, rng: random.Random | None = None
) -> list[Probe]:
    """1 to `max_follow_ups` follow-ups, chosen at random from the candidates."""
    if not candidates:
        return []
    r = rng or _rng()
    k = r.randint(1, max(1, min(policy.max_follow_ups, len(candidates))))
    return r.sample(candidates, k)


# ---- the quiz loop ----

_NEAREST = {"easy": ("medium", "hard"), "medium": ("easy", "hard"), "hard": ("medium", "easy")}


def _ask(ask: AskFn, p: Probe, timeout: float | None) -> tuple[bool, float]:
    ans, secs = ask(p, timeout)
    p.user_answer = ans if ans is not None else ""
    in_time = timeout is None or secs <= timeout  # enforced here, not only by the countdown display
    p.correct = bool(ans is not None and in_time and grade(p, ans))
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
    n_questions: int | None = None,
    announce: AnnounceFn | None = None,
    position: tuple[int, int] = (1, 1),
) -> UnitOutcome:
    """Ask a fixed set, then decide from the score; a suspiciously even pace earns one follow-up."""
    n = n_questions or min(5, max(3, policy.questions_per_unit))
    if not any(_difficulty(p) == "hard" for p in pool) and len(pool) < n + 2:
        pool = [*pool, *_topup(repo, rel, cfg, unit, source, pool)]
    asked: list[Probe] = []
    times: list[tuple[str, float]] = []  # local to this call, never stored
    chosen = select_questions(pool, n)
    if announce is not None:
        announce(unit.symbol, position[0], position[1], len(chosen))
    for p in chosen:
        _ok, secs = _ask(ask, p, None)
        times.append((_difficulty(p), secs))
        asked.append(p)
    correct = sum(1 for p in asked if p.correct)
    need = required_correct(len(asked), policy.pass_ratio)
    out = UnitOutcome(
        unit.symbol,
        False,
        "score_too_low",
        asked=list(asked),
        correct=correct,
        needed=need,
        questions=len(asked),
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
    fus = pick_followups(
        followups(repo, rel, cfg, unit, source, hardest, policy, {p.question for p in asked}),
        policy,
    )
    if not fus:
        out.cleared, out.reason = True, "verified_no_follow_up_possible"
        return out
    passed = True
    limit = follow_up_timer(times, policy) if policy.timer else None  # in-memory, never stored
    for fu in fus:
        ok, _ = _ask(ask, fu, limit)
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
    *,
    budget: RunBudget | None = None,
    announce: AnnounceFn | None = None,
    not_quizzed: list[str] | None = None,
    first: set[str] | None = None,
) -> list[UnitOutcome]:
    """Run the units that have questions, largest first, until the run's question budget is used.
    Functions the budget did not reach are NOT marked understood: their names are appended to
    `not_quizzed` so the caller can say so (the gate keeps flagging them)."""
    if budget is None:
        cap = policy.max_questions_per_run
        budget = RunBudget(cap if cap > 0 else None)
    by_unit: dict[str, list[Probe]] = {}
    for p in probes:
        by_unit.setdefault(p.symbol, []).append(p)
    todo = [u for u in units if by_unit.get(u.symbol)]
    # functions never quizzed come first (so a retry reaches them), then the largest
    todo.sort(key=lambda u: (u.symbol not in (first or set()), -len(u.lines())))
    per = min(5, max(3, policy.questions_per_unit))
    planned = len(todo)  # how many functions this run will reach (shown as "function i of N")
    if budget.remaining is not None:
        planned = min(
            len(todo), budget.remaining // per + (1 if budget.remaining % per >= 3 else 0)
        )
    outcomes: list[UnitOutcome] = []
    asked = 0
    for u in todo:
        n = per
        if budget.remaining is not None:
            if budget.remaining < 3:  # fewer than 3 would break the 3-to-5 rule: leave it unquizzed
                if not_quizzed is not None:
                    not_quizzed.append(u.symbol)
                continue
            n = min(per, budget.remaining)
        asked += 1
        total = max(planned, asked)
        out = run_unit(
            repo, rel, cfg, u, source, by_unit[u.symbol], ask, policy, n, announce, (asked, total)
        )
        outcomes.append(out)
        if budget.remaining is not None:
            budget.remaining -= out.questions
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


def _flush_typeahead() -> None:
    """Drop keys typed before the question appeared (for example Enter pressed while the agent was
    writing questions), so they are never taken as the answer."""
    if sys.platform == "win32":  # no termios there (and no sandboxed quiz either)
        return
    import termios

    try:
        if sys.stdin.isatty():
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (OSError, ValueError, AttributeError):
        pass


EMPTY_HINT = "  (type a letter or the value, then Enter)\n"


def tty_ask(p: Probe, timeout: float | None) -> tuple[str | None, float]:
    """Read one answer from the terminal. With a timeout, show a countdown; None = ran out.
    Keys typed before the question are dropped, and an empty line is asked again, never graded."""
    import select

    _flush_typeahead()
    t0 = time.monotonic()
    if timeout is None or not sys.stdin.isatty():
        while True:
            sys.stdout.write("answer: ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if line == "":  # end of input: nothing more will come
                return "", time.monotonic() - t0
            if line.strip():
                return line.strip(), time.monotonic() - t0
            sys.stdout.write(EMPTY_HINT)
    deadline = t0 + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            sys.stdout.write("\n  time is up\n")
            return None, time.monotonic() - t0
        sys.stdout.write(f"\r[{left:4.0f} s left] answer: ")
        sys.stdout.flush()
        ready, _, _ = select.select([sys.stdin], [], [], min(1.0, left))
        if ready:
            line = sys.stdin.readline()
            if line.strip() or line == "":
                return line.strip(), time.monotonic() - t0
            sys.stdout.write(EMPTY_HINT)
