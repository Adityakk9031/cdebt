"""Milestone 2: adaptive quiz with timed follow-ups. Timings are simulated with a scripted `ask`;
no real clock, no network, no agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdebt.adaptive import (
    Policy,
    load_policy,
    record_outcomes,
    run_adaptive,
    timing_looks_uniform,
)
from cdebt.agents.probes import execute, parse_proposal
from cdebt.config import Config
from cdebt.models import Probe
from cdebt.probes.base import LETTERS
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import Repo

pytestmark = pytest.mark.posix_sandbox

CODE = """def inc(x: int) -> int:
    return x + 1


def classify(n: int, strict: bool = False) -> str:
    if n < 0:
        label = "neg"
    elif n == 0:
        label = "zero"
    else:
        label = "pos"
    if strict and n > 100:
        label = label + "!"
    return label
"""
REL = "m.py"


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write(REL, CODE)
    r.commit("m")
    return r


def _ret(r: Repo, target: str, *args) -> Probe:  # type: ignore[no-untyped-def]
    p = execute(
        r.path,
        REL,
        parse_proposal(
            {"target": target, "kind": "return_value", "call_inputs": {"args": list(args)}}
        ),
        Config(),
        proposer="builtin",
    )
    p.evidence["args_repr"] = repr(list(args))
    return p


def _pool(r: Repo) -> list[Probe]:
    return [
        _ret(r, "inc", 1),
        _ret(r, "inc", 5),
        _ret(r, "classify", -3),
        _ret(r, "classify", 0),
        _ret(r, "classify", 7),
    ]


class Scripted:
    """A fake person: answers right or wrong per rule, with fixed response times by difficulty."""

    def __init__(
        self, easy: float, hard: float, follow: float = 5.0, right=lambda p, n: True
    ) -> None:  # type: ignore[no-untyped-def]
        self.t = {"easy": easy, "hard": hard}
        self.follow, self.right = follow, right
        self.calls: list[tuple[Probe, float | None]] = []

    def __call__(self, p: Probe, timeout: float | None):  # type: ignore[no-untyped-def]
        self.calls.append((p, timeout))
        n = len(self.calls)
        secs = self.follow if timeout is not None else self.t[p.difficulty or "easy"]
        if self.right(p, n):
            return LETTERS[p.options.index(p.answer)], secs
        wrong = next(i for i, o in enumerate(p.options) if o != p.answer)
        return LETTERS[wrong], secs

    @property
    def timed(self) -> list[Probe]:
        return [p for p, t in self.calls if t is not None]


def _run(r: Repo, ask, policy: Policy | None = None):  # type: ignore[no-untyped-def]
    src = (r.path / REL).read_text()
    units = extract_units(REL, src)
    return run_adaptive(r.path, REL, Config(), units, src, _pool(r), ask, policy or Policy()), units


def test_difficulty_labels_follow_kind_and_complexity(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    assert _ret(r, "inc", 1).difficulty == "easy"
    assert _ret(r, "classify", 1).difficulty == "hard"  # five branches


def test_trigger_rule_is_a_ratio_after_enough_answers() -> None:
    pol = Policy()
    easy = [("easy", 4.0), ("easy", 4.0), ("hard", 6.0), ("hard", 6.0)]
    assert timing_looks_uniform(easy, 4.5, pol)  # hard no slower than 1.3 x easy median
    assert not timing_looks_uniform(easy, 20.0, pol)  # naturally slower: fine
    assert not timing_looks_uniform(easy[:3], 4.0, pol)  # under 4 answers: never
    assert not timing_looks_uniform([("hard", 3.0)] * 5, 3.0, pol)  # no easy baseline


def test_uniform_pace_triggers_timed_follow_ups_that_pass(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=5.0, hard=5.0)
    outs, _ = _run(r, ask)
    cl = next(o for o in outs if o.symbol == "classify")
    assert ask.timed, "uniform pace on a hard question must trigger follow-ups"
    assert 1 <= len(ask.timed) <= 2 and all(t == 15.0 for p, t in ask.calls if t is not None)
    assert cl.cleared and cl.reason == "verified"
    kinds = {p.kind for p in ask.timed}
    assert kinds <= {"branch_taken", "variable_at_line", "return_value"}
    assert all(p.verified_by == "execution-trace" for p in ask.timed)


def test_natural_pace_and_expert_pace_do_not_trigger(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    for easy, hard in ((4.0, 25.0), (2.0, 3.0)):  # natural; expert who is still slower on hard
        ask = Scripted(easy=easy, hard=hard)
        outs, _ = _run(r, ask)
        assert not ask.timed
        assert all(o.cleared for o in outs)


def test_failed_follow_up_keeps_the_unit_not_understood(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=5.0, hard=5.0, right=lambda p, n: True)
    orig = ask.__call__

    def fail_follow_ups(p, timeout):  # type: ignore[no-untyped-def]
        ans, secs = orig(p, timeout)
        if timeout is not None:
            wrong = next(i for i, o in enumerate(p.options) if o != p.answer)
            return LETTERS[wrong], secs
        return ans, secs

    outs, _ = _run(r, fail_follow_ups)
    cl = next(o for o in outs if o.symbol == "classify")
    assert not cl.cleared and cl.reason == "follow_up_failed" and cl.rounds == 3


def test_a_timeout_counts_as_a_failed_follow_up(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    base = Scripted(easy=5.0, hard=5.0)

    def ask(p, timeout):  # type: ignore[no-untyped-def]
        if timeout is not None:
            return None, timeout  # the countdown ran out
        return base(p, timeout)

    outs, _ = _run(r, ask)
    assert not next(o for o in outs if o.symbol == "classify").cleared


def test_wrong_answer_earns_a_harder_question_not_an_instant_fail(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    seen = {"classify": 0}

    def right(p: Probe, n: int) -> bool:
        if p.symbol == "classify":
            seen["classify"] += 1
            return seen["classify"] != 1  # the first classify answer is wrong
        return True

    ask = Scripted(easy=4.0, hard=25.0, right=right)
    outs, _ = _run(r, ask)
    cl = next(o for o in outs if o.symbol == "classify")
    first = next(p for p in cl.asked if p.symbol == "classify")
    assert first.correct is False
    # the wrong answer was followed at once by one more HARD question on the same unit
    assert len(cl.asked) >= 4 and all(p.difficulty == "hard" for p in cl.asked)
    assert cl.cleared and cl.reason == "verified"  # not an immediate fail


def test_three_round_cap_when_always_wrong(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=4.0, hard=25.0, right=lambda p, n: False)
    outs, _ = _run(r, ask)
    for o in outs:
        assert not o.cleared and o.rounds <= 3
        assert o.reason in ("max_rounds_reached", "follow_up_failed")
    cl = next(o for o in outs if o.symbol == "classify")
    assert cl.rounds == 3 and cl.reason == "max_rounds_reached"


def test_no_timer_policy_asks_follow_ups_untimed(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=5.0, hard=5.0)
    _run(r, ask, Policy(timer=False))
    assert not ask.timed  # follow-ups happen but with no countdown (timeout None)
    followed = [
        p
        for p, t in ask.calls
        if p.proposer == "builtin" and p.kind in ("branch_taken", "variable_at_line")
    ]
    assert followed


def test_policy_file_is_read_from_the_repo_but_has_no_trust_or_agent_keys(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    assert load_policy(r.path) == Policy()
    r.write(".cdebt/policy.toml", "timer = false\nfollow_up_seconds = 9\nenforce = true\n")
    pol = load_policy(r.path)
    assert pol.timer is False and pol.follow_up_seconds == 9 and pol.enforce is True
    assert "trusted" not in Policy.model_fields and "agent" not in Policy.model_fields


def test_no_timing_value_is_persisted_anywhere(make_repo, capsys, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    marker = 7.31337
    ask = Scripted(easy=marker, hard=marker, follow=marker + 1)
    outs, units = _run(r, ask)
    store = Store(r.path)
    recs = record_outcomes(REL, units, outs, store=store, label="audit-x", expires_days=None)
    assert recs and all(rec.status_reason for rec in recs)
    # no model has a time field, and no file written under the repo or its .cdebt holds a time
    assert not [f for f in Probe.model_fields if "time" in f or "second" in f or "elapsed" in f]
    bodies = [
        p.read_text(errors="replace")
        for p in Path(r.path).rglob("*")
        if p.is_file() and ".git/" not in str(p)
    ]
    joined = "\n".join(bodies)
    for needle in ("7.31337", "8.31337", "7.3133"):
        assert needle not in joined
    out = capsys.readouterr()
    assert "7.31337" not in out.out + out.err
    data = (r.path / ".cdebt" / "understanding.json").read_text()
    assert "status_reason" in data and "verified" in data


def test_cli_quiz_runs_the_adaptive_flow_and_records_reasons(
    make_repo, monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import json

    from typer.testing import CliRunner

    from cdebt import adaptive
    from cdebt.cli import app

    r = _repo(make_repo)
    monkeypatch.setenv("CDEBT_USER_CONFIG", str(tmp_path / "none.toml"))  # no agent: builtin only

    def person(p: Probe, timeout: float | None):  # type: ignore[no-untyped-def]
        return LETTERS[p.options.index(p.answer)], 30.0  # always right, slowly

    monkeypatch.setattr(adaptive, "tty_ask", person)
    res = CliRunner().invoke(app, ["quiz", REL, "--repo", str(r.path), "--no-timer"])
    assert res.exit_code == 0, res.output
    assert "UNDERSTOOD" in res.output and "timer off" in res.output
    data = json.loads((r.path / ".cdebt" / "understanding.json").read_text())
    reasons = {x["unit_symbol"]: x["status_reason"] for x in data["records"]}
    assert set(reasons.values()) <= {"verified", "verified_no_hard_available"}
    assert reasons["classify"] == "verified"
