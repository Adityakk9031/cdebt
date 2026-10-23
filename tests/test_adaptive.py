"""The quiz rules: a fixed set per function, a score verdict, and a timing check that earns ONE hard
follow-up. Timings are simulated with a scripted `ask`; no real clock, no network, no agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdebt.adaptive import (
    Policy,
    answers_look_suspiciously_uniform,
    load_policy,
    record_outcomes,
    required_correct,
    run_adaptive,
    select_questions,
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
        _ret(r, "classify", -1),
        _ret(r, "classify", 12),
    ]


class Scripted:
    """A fake person: right or wrong by rule, with a fixed response time per difficulty."""

    def __init__(
        self, easy=4.0, medium=8.0, hard=20.0, follow=5.0, jitter=0.0, right=lambda p, i: True
    ) -> None:  # type: ignore[no-untyped-def]
        self.t = {"easy": easy, "medium": medium, "hard": hard}
        self.follow, self.right, self.jitter = follow, right, jitter
        self.calls: list[tuple[Probe, float | None]] = []
        self.main_seen: dict[str, int] = {}

    def __call__(self, p: Probe, timeout: float | None):  # type: ignore[no-untyped-def]
        self.calls.append((p, timeout))
        idx = self.main_seen.get(
            p.symbol, 0
        )  # follow-ups come after the main set, so idx >= its size
        self.main_seen[p.symbol] = idx + 1
        secs = (
            self.follow
            if timeout is not None
            else self.t[p.difficulty or "easy"] + self.jitter * idx
        )
        if self.right(p, idx):
            return LETTERS[p.options.index(p.answer)], secs
        wrong = next(k for k, o in enumerate(p.options) if o != p.answer)
        return LETTERS[wrong], secs

    @property
    def timed(self) -> list[Probe]:
        return [p for p, t in self.calls if t is not None]


def _with_hard(r: Repo) -> list[Probe]:
    """The pool plus hard questions (what `label` held mid-run, from a real trace)."""
    hard = []
    for arg in (-3, 0, 7):
        p = execute(
            r.path,
            REL,
            parse_proposal(
                {
                    "target": "classify",
                    "kind": "variable_at_line",
                    "line": 12,
                    "variable": "label",
                    "call_inputs": {"args": [arg]},
                }  # fmt: skip
            ),
            Config(),
            proposer="builtin",
        )
        p.evidence["args_repr"] = repr([arg])
        hard.append(p)
    assert all(p.difficulty == "hard" for p in hard)
    return [*_pool(r), *hard]


def _run(r: Repo, ask, policy: Policy | None = None, pool: list[Probe] | None = None):  # type: ignore[no-untyped-def]
    src = (r.path / REL).read_text()
    units = extract_units(REL, src)
    outs = run_adaptive(
        r.path, REL, Config(), units, src, pool or _pool(r), ask, policy or Policy()
    )
    return {o.symbol: o for o in outs}, units


def _wrong_first(k: int):  # type: ignore[no-untyped-def]
    """Right, except the first `k` main-set answers of each function."""
    return lambda p, i: i >= k


NATURAL = {
    "easy": 4.0,
    "medium": 9.0,
    "hard": 26.0,
    "jitter": 4.0,
}  # a real person is never this even


def test_required_correct_table() -> None:
    assert [required_correct(n) for n in (1, 2, 3, 4, 5, 6)] == [1, 2, 2, 3, 3, 4]


def test_labels_follow_kind_and_complexity(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    assert _ret(r, "inc", 1).difficulty == "easy"
    assert _ret(r, "classify", 1).difficulty == "medium"  # five branches


def test_set_mixes_tiers_and_is_asked_easiest_first() -> None:
    def pr(n: str, d: str) -> Probe:
        return Probe(id=n, kind="runtime", path="m.py", symbol="f", question=n, options=["a", "b"],
                     answer="a", verified_by="sandbox-exec", difficulty=d)  # type: ignore[arg-type]  # fmt: skip

    pool = [
        pr("h1", "hard"),
        pr("m1", "medium"),
        pr("e1", "easy"),
        pr("e2", "easy"),
        pr("m2", "medium"),
    ]
    assert [p.difficulty for p in select_questions(pool, 4)] == ["easy", "easy", "medium", "hard"]


def test_one_wrong_of_four_is_understood_and_adds_no_question(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(**NATURAL, right=_wrong_first(1))
    cl = _run(r, ask)[0]["classify"]
    assert len(cl.asked) == 4 and cl.correct == 3 and cl.needed == 3
    assert cl.cleared and cl.reason == "verified" and not ask.timed


def test_two_wrong_of_four_is_not_understood(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(**NATURAL, right=_wrong_first(2))
    cl = _run(r, ask)[0]["classify"]
    assert not cl.cleared and cl.reason == "score_too_low" and cl.correct == 2 and cl.needed == 3
    assert len(cl.asked) == 4 and not ask.timed


def test_three_questions_allow_one_wrong_and_five_allow_two(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    c3 = _run(r, Scripted(**NATURAL, right=_wrong_first(1)), Policy(questions_per_unit=3))[0][
        "classify"
    ]
    assert len(c3.asked) == 3 and c3.needed == 2 and c3.cleared
    c5 = _run(r, Scripted(**NATURAL, right=_wrong_first(2)), Policy(questions_per_unit=5))[0][
        "classify"
    ]
    assert len(c5.asked) == 5 and c5.needed == 3 and c5.cleared
    bad = _run(r, Scripted(**NATURAL, right=_wrong_first(3)), Policy(questions_per_unit=5))[0][
        "classify"
    ]
    assert not bad.cleared and bad.correct == 2


def _planned(pool: list[Probe], n: int) -> list[Probe]:
    chosen = select_questions([p for p in pool if p.symbol == "classify"], n)
    assert sum(1 for p in chosen if p.difficulty == "hard") == 1  # a 5-set has one hard slot
    return chosen


def _wrong_ids(*probes: Probe):  # type: ignore[no-untyped-def]
    qs = {p.question for p in probes}
    return lambda p, i: p.question not in qs


def test_three_of_five_with_no_hard_correct_is_not_understood(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    pool = _with_hard(r)
    plan = _planned(pool, 5)
    hard = next(p for p in plan if p.difficulty == "hard")
    other = next(p for p in plan if p is not hard)
    ask = Scripted(**NATURAL, right=_wrong_ids(hard, other))  # 3 of 5 right, the hard one wrong
    cl = _run(r, ask, Policy(questions_per_unit=5), pool)[0]["classify"]
    assert len(cl.asked) == 5 and cl.correct == 3 and cl.needed == 3  # the 60% score passes
    assert not cl.cleared and cl.reason == "no_hard_correct" and not ask.timed


def test_three_of_five_with_one_hard_correct_is_understood(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    pool = _with_hard(r)
    plan = _planned(pool, 5)
    hard = next(p for p in plan if p.difficulty == "hard")
    others = [p for p in plan if p is not hard][:2]
    ask = Scripted(**NATURAL, right=_wrong_ids(*others))  # 3 of 5 right, the hard one among them
    cl = _run(r, ask, Policy(questions_per_unit=5), pool)[0]["classify"]
    assert len(cl.asked) == 5 and cl.correct == 3
    assert any(p.difficulty == "hard" and p.correct for p in cl.asked)
    assert cl.cleared and cl.reason == "verified"


def test_require_hard_can_be_switched_off_and_is_waived_with_no_hard_question(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    pool = _with_hard(r)
    hard = next(p for p in _planned(pool, 5) if p.difficulty == "hard")
    pol = Policy(questions_per_unit=5, require_hard=False)
    assert _run(r, Scripted(**NATURAL, right=_wrong_ids(hard)), pol, pool)[0]["classify"].cleared
    inc = _run(r, Scripted(**NATURAL))[0][
        "inc"
    ]  # `inc` only has easy questions: nothing to require
    assert all(p.difficulty != "hard" for p in inc.asked) and inc.cleared


def test_followup_offers_a_fresh_input_when_every_traced_question_was_asked(make_repo) -> None:  # type: ignore[no-untyped-def]
    """The rc5 demo gap: all traced follow-ups had been asked, so no follow-up existed at all."""
    from cdebt.adaptive import followups, trace_questions

    r = _repo(make_repo)
    src = (r.path / REL).read_text()
    unit = next(u for u in extract_units(REL, src) if u.symbol == "classify")
    hard = next(p for p in _with_hard(r) if p.difficulty == "hard")
    args = [hard.evidence["args_repr"]]
    traced = trace_questions(r.path, REL, Config(), unit, src, eval(args[0]), {}, seed=101)
    assert traced  # these are what the old code would have offered
    asked = {q.question for q in traced}
    fus = followups(r.path, REL, Config(), unit, src, hard, Policy(), asked)
    assert fus, "a fresh input of the same function must still be available"
    assert all(f.question not in asked | {hard.question} for f in fus)
    assert fus[0].kind == "return_value" and fus[0].verified_by in (
        "sandbox-exec",
        "execution-trace",
    )


def test_the_even_pace_rule_matches_the_examples() -> None:
    pol = Policy()
    even = [("easy", 8.0), ("easy", 8.0), ("medium", 8.0), ("hard", 9.5)]  # 8, 8, 8, then 9-10 s
    assert answers_look_suspiciously_uniform(even, pol)
    natural = [("easy", 5.0), ("easy", 7.0), ("medium", 12.0), ("hard", 30.0)]
    assert not answers_look_suspiciously_uniform(natural, pol)
    assert not answers_look_suspiciously_uniform(even[:2], pol)  # too few answers to judge
    assert not answers_look_suspiciously_uniform(
        [("easy", 0.2), ("medium", 0.2), ("hard", 0.2)], pol
    )


def test_even_pace_and_passing_score_asks_one_hard_followup_and_passes(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=8.0, medium=8.0, hard=9.5)
    cl = _run(r, ask)[0]["classify"]
    assert len(ask.timed) == 1 and all(t == 15.0 for _, t in ask.calls if t is not None)
    assert ask.timed[0].verified_by == "execution-trace"
    # the follow-up is a NEW question, never one that was already asked in the main set
    assert ask.timed[0].question not in {p.question for p, t in ask.calls if t is None}
    assert cl.cleared and cl.reason == "verified_after_follow_up"


def test_failing_the_followup_is_not_understood_whatever_the_score(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    base = Scripted(easy=8.0, medium=8.0, hard=9.5)

    def ask(p, timeout):  # type: ignore[no-untyped-def]
        ans, secs = base(p, timeout)
        if timeout is not None:  # the follow-up: answer wrongly
            wrong = next(k for k, o in enumerate(p.options) if o != p.answer)
            return LETTERS[wrong], secs
        return ans, secs

    cl = _run(r, ask)[0]["classify"]
    assert cl.correct >= cl.needed  # the score itself passed
    assert not cl.cleared and cl.reason == "follow_up_failed"


def test_a_timeout_on_the_followup_counts_as_failing_it(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    base = Scripted(easy=8.0, medium=8.0, hard=9.5)

    def ask(p, timeout):  # type: ignore[no-untyped-def]
        return (None, timeout) if timeout is not None else base(p, timeout)

    assert not _run(r, ask)[0]["classify"].cleared


def test_a_failing_score_gets_no_followup_even_at_an_even_pace(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=8.0, medium=8.0, hard=9.5, right=_wrong_first(2))
    cl = _run(r, ask)[0]["classify"]
    assert not cl.cleared and cl.reason == "score_too_low" and not ask.timed


def test_natural_pace_and_instant_scripted_answers_do_not_trigger(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    for ask in (Scripted(**NATURAL), Scripted(easy=0.3, medium=0.3, hard=0.3)):
        outs, _ = _run(r, ask)
        assert not ask.timed and outs["classify"].cleared and outs["classify"].reason == "verified"


def test_no_timer_policy_asks_the_followup_untimed(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ask = Scripted(easy=8.0, medium=8.0, hard=9.5)
    _run(r, ask, Policy(timer=False))
    assert not ask.timed
    assert any(p.verified_by == "execution-trace" and t is None for p, t in ask.calls)


def test_a_function_with_only_two_questions_needs_both_right(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ok = _run(r, Scripted(**NATURAL))[0]["inc"]
    assert len(ok.asked) == 2 and ok.needed == 2 and ok.cleared
    bad = _run(r, Scripted(**NATURAL, right=_wrong_first(1)))[0]["inc"]
    assert not bad.cleared and bad.reason == "score_too_low"


def test_policy_file_keys_and_old_keys_are_tolerated(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    assert load_policy(r.path) == Policy()
    r.write(
        ".cdebt/policy.toml",
        "timer = false\nfollow_up_seconds = 9\nmax_rounds = 3\nenforce = true\n",
    )
    pol = load_policy(r.path)  # `max_rounds` is from the old design and is ignored
    assert pol.timer is False and pol.follow_up_seconds == 9 and pol.enforce is True
    assert pol.questions_per_unit == 4 and pol.pass_ratio == 0.6
    assert "trusted" not in Policy.model_fields and "agent" not in Policy.model_fields


def test_no_timing_value_is_persisted_anywhere(make_repo, capsys) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    marker = 7.31337
    ask = Scripted(easy=marker, medium=marker, hard=marker, follow=marker + 1)
    outs, units = _run(r, ask)
    store = Store(r.path)
    recs = record_outcomes(
        REL, units, list(outs.values()), store=store, label="audit-x", expires_days=None
    )
    assert recs and all(rec.status_reason for rec in recs)
    assert not [f for f in Probe.model_fields if "time" in f or "second" in f or "elapsed" in f]
    joined = "\n".join(
        p.read_text(errors="replace")
        for p in Path(r.path).rglob("*")
        if p.is_file() and ".git/" not in str(p)
    )
    for needle in ("7.31337", "8.31337", "7.3133"):
        assert needle not in joined
    out = capsys.readouterr()
    assert "7.31337" not in out.out + out.err
    assert "status_reason" in (r.path / ".cdebt" / "understanding.json").read_text()


def test_no_timing_value_reaches_git_notes_the_gate_or_the_pr_comment(make_repo, capsys) -> None:  # type: ignore[no-untyped-def]
    """Marker timings go in; the git note (what the hook pushes), the stored record, the gate result
    and the markdown posted on the PR must hold none of them, nor any time-like field."""
    import json
    import re
    import subprocess

    from cdebt import notes
    from cdebt.gate import run_gate, to_markdown

    r = _repo(make_repo)
    markers = ("6.41729", "7.41729", "9.8765432")
    ask = Scripted(
        easy=6.41729, medium=6.41729, hard=6.41729, follow=7.41729
    )  # even pace: follow-up too
    outs, units = _run(r, ask)
    recs = record_outcomes(REL, units, list(outs.values()), store=Store(r.path), label="audit-x")
    assert any(o.reason == "verified_after_follow_up" for o in outs.values())  # the follow-up ran
    sha = (
        r.git("rev-parse", "HEAD").strip()
        if hasattr(r, "git")
        else subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=r.path, capture_output=True, text=True
        ).stdout.strip()
    )
    notes.write_records(r.path, sha, recs)
    note = subprocess.run(
        ["git", "notes", "--ref=cdebt", "show", sha], cwd=r.path, capture_output=True, text=True
    ).stdout
    gate = run_gate(r.path, Config(), "HEAD~0")
    md = to_markdown(gate, pr=1)
    stored = (r.path / ".cdebt" / "understanding.json").read_text()
    out = capsys.readouterr()
    for name, text in (("note", note), ("stored", stored), ("gate", repr(gate)), ("pr comment", md),
                       ("console", out.out + out.err)):  # fmt: skip
        for m in markers + ("6.4172", "7.4172"):
            assert m not in text, f"{name} holds a timing value"

    def keys(o):  # type: ignore[no-untyped-def]
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    bad = re.compile(r"time|second|elapsed|duration|latency|speed|pace|took", re.I)
    for doc in (json.loads(note), json.loads(stored)):
        assert not [k for k in keys(doc) if bad.search(str(k))]


def test_cli_quiz_runs_the_flow_and_records_reasons(make_repo, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    from typer.testing import CliRunner

    from cdebt import adaptive
    from cdebt.cli import app

    r = _repo(make_repo)
    monkeypatch.setenv("CDEBT_USER_CONFIG", str(tmp_path / "none.toml"))  # no agent: builtin only

    def person(p: Probe, timeout: float | None):  # type: ignore[no-untyped-def]
        return LETTERS[p.options.index(p.answer)], 30.0  # always right

    monkeypatch.setattr(adaptive, "tty_ask", person)
    res = CliRunner().invoke(app, ["quiz", REL, "--repo", str(r.path), "--no-timer"])
    assert res.exit_code == 0, res.output
    assert (
        "UNDERSTOOD" in res.output and "timer off" in res.output and "right, needed" in res.output
    )
    data = json.loads((r.path / ".cdebt" / "understanding.json").read_text())
    reasons = {x["unit_symbol"]: x["status_reason"] for x in data["records"]}
    assert set(reasons.values()) <= {
        "verified",
        "verified_after_follow_up",
        "verified_no_follow_up_possible",
    }
