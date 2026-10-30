"""Question-quality filters for agent proposals: wrong options a reader could believe, no answer
given away in the question, nothing that only a hash or a random draw explains."""

from __future__ import annotations

import ast
import random

import pytest

from cdebt.agents.probes import Rejected, _near_misses, _options, execute, parse_proposal
from cdebt.agents.session import Session, SessionData
from cdebt.config import Config

SRC = """def echo(s: str) -> str:
    return s


def greet(name: str) -> str:
    return "hello " + name + " 7"


def tags(n: int) -> list:
    return ["a", "b", n]
"""


def _run(r, **kw):  # type: ignore[no-untyped-def]
    return execute(r.path, "pkg/m.py", parse_proposal(kw), Config(), proposer="agent:test")


@pytest.fixture
def repo(make_repo):  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/m.py", SRC)
    r.commit("m")
    return r


def test_string_options_are_near_misses_not_cosmetic_edits() -> None:
    opts = _options(repr("hello ada 7"), random.Random(1))
    assert opts is not None and len(opts) >= 3
    wrong = {ast.literal_eval(o) for o in opts} - {"hello ada 7"}
    for w in wrong:
        assert w not in ("HELLO ADA 7", "7 ada olleh"[::-1], "hello ada 7x", "7 ada olleh")
        assert w != "hello ada 7"[::-1]
        assert w == w.strip() and w.lower() == w  # no upper-casing
    assert "hello ada 8" in wrong  # the number changed by one: a believable slip


def test_list_options_change_an_element_and_vary_in_length() -> None:
    wrong = _near_misses(["a", "b", 3])
    assert ["a", "b", 4] in wrong
    assert any(len(w) < 3 for w in wrong) and any(len(w) > 3 for w in wrong)  # both directions


def test_answer_in_the_question_is_rejected(repo) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Rejected, match="appears in the question"):
        _run(repo, target="echo", kind="return_value", call_inputs={"args": ["abcdef"]})


def test_a_question_that_does_not_give_the_answer_away_is_kept(repo) -> None:  # type: ignore[no-untyped-def]
    p = _run(repo, target="greet", kind="return_value", call_inputs={"args": ["ada"]})
    assert p.answer == "'hello ada 7'" and p.answer in p.options
    assert "'HELLO ADA 7'" not in p.options


def test_session_remembers_one_true_false_question_per_target(tmp_path) -> None:  # type: ignore[no-untyped-def]
    data = SessionData(repo=str(tmp_path), agent="t", max_per_target=6, targets=[])
    s = Session.create(tmp_path, "t", [], 6)
    s.data = data
    assert not s.accepted_marked("f", "bool")
    s.record("f", {}, True, "bool")
    assert s.accepted_marked("f", "bool") and not s.accepted_marked("g", "bool")
    s.close()


def test_right_answer_is_not_the_longest_option_more_often_than_chance() -> None:
    longest = total = 0
    cases = [
        "'hello ada 7'",
        "['a', 'b', 3]",
        "('move left', 'ui')",
        "{'a': 1, 'b': 2}",
        "'status: ok, 12 rows'",
        "['x', 'y']",
    ]
    for case in cases:
        for seed in range(40):
            opts = _options(case, random.Random(seed))
            if not opts or len(opts) < 3:
                continue
            total += 1
            lens = [len(o) for o in opts]
            longest += len(case) == max(lens) and lens.count(len(case)) == 1
    assert total > 100
    assert longest / total < 0.4  # chance with 4 options is 25%; the old filter gave ~26% overall


OVERLOADED = """from typing import overload


@overload
def pick(x: int) -> int: ...


@overload
def pick(x: str) -> str: ...


def pick(x):
    return x * 2
"""


def test_overloaded_function_runs_its_real_definition(make_repo) -> None:  # type: ignore[no-untyped-def]
    from cdebt.agents.session import targets_for_file

    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/o.py", OVERLOADED)
    r.commit("o")
    ids = [t.symbol for t in targets_for_file(r.path, "pkg/o.py", Config())]
    assert ids == ["pick#3"]  # the two @overload stubs never run and are not offered
    p = execute(
        r.path,
        "pkg/o.py",
        parse_proposal({"target": "pick#3", "kind": "return_value", "call_inputs": {"args": [21]}}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "42"
    with pytest.raises(Rejected, match="overload stub"):
        execute(
            r.path,
            "pkg/o.py",
            parse_proposal(
                {"target": "pick", "kind": "return_value", "call_inputs": {"args": [1]}}
            ),
            Config(),
            proposer="agent:test",
        )


def test_no_length_strategy_beats_a_blind_guess() -> None:
    """Picking the longest (or the shortest) option must not beat guessing at random."""
    cases = [
        "'hello ada 7'",
        "['a', 'b', 3]",
        "('move left', 'ui')",
        "{'a': 1, 'b': 2}",
        "7",
        "125",
    ]

    def score(ans: str, opts: list[str], pick: object) -> float:
        lens = [len(o) for o in opts]
        best = pick(lens)  # type: ignore[operator]
        tied = [o for o in opts if len(o) == best]
        return (ans in tied) / len(tied)

    longest = shortest = blind = 0.0
    n = 0
    for case in cases:
        for seed in range(60):
            opts = _options(case, random.Random(seed))
            assert opts is not None
            n += 1
            longest += score(case, opts, max)
            shortest += score(case, opts, min)
            blind += 1 / len(opts)
    assert longest / n <= blind / n + 0.05
    assert shortest / n <= blind / n + 0.05


OBJECTS = """from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass
class Point:
    x: int
    y: int


def lock_path(name: str) -> PurePosixPath:
    return PurePosixPath("run") / name / "locks"


def origin() -> Point:
    return Point(0, 0)


class Opaque:
    pass


def thing() -> Opaque:
    return Opaque()
"""


def test_object_results_are_asked_about_by_their_printed_form(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/ob.py", OBJECTS)
    r.commit("ob")

    def run(**kw):  # type: ignore[no-untyped-def]
        return execute(r.path, "pkg/ob.py", parse_proposal(kw), Config(), proposer="agent:test")

    p = run(target="lock_path", kind="return_value", call_inputs={"args": ["cam"]})
    assert p.answer == "PurePosixPath('run/cam/locks')" and p.answer in p.options
    assert len(p.options) >= 3
    assert run(target="origin", kind="return_value").answer == "Point(x=0, y=0)"
    with pytest.raises(Rejected):  # <Opaque object at 0x...> changes between runs: never asked
        run(target="thing", kind="return_value")


def test_text_near_misses_never_double_the_whole_answer() -> None:
    from cdebt.agents.probes import _text_misses

    for v in ["abc", "run/cam/locks", "inspect-robots-501", "status: ok, 12 rows"]:
        for w in _text_misses(v):
            assert w is None or (w != v and w != f"{v} {v}" and w != f"{v}, {v}")
    assert "run/cam" in _text_misses("run/cam/locks")
    assert "status: ok, 12" in _text_misses("status: ok, 12 rows")


def test_object_options_keep_the_printed_shape() -> None:
    opts = _options("PosixPath('run/cam/locks')", random.Random(1))
    assert opts and all(o.startswith("PosixPath('") and o.endswith("')") for o in opts)
    pts = _options("Point(x=0, y=0)", random.Random(1))
    assert pts and all(o.startswith("Point(x=") and o.endswith(")") for o in pts)


STATUS = '''def status(code: int) -> str:
    """Map an HTTP code to a word."""
    if code >= 500:
        return "error"
    if code >= 400:
        return "rejected"
    if code == 304:
        return "cached"
    return "ok"
'''


def test_wrong_options_include_other_values_the_function_can_return(make_repo) -> None:  # type: ignore[no-untyped-def]
    from cdebt.agents.probes import body_literals

    lits = body_literals(STATUS)
    assert "Map an HTTP code to a word." not in lits  # never the docstring
    assert {"error", "rejected", "cached", "ok"} <= set(lits)
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/st.py", STATUS)
    r.commit("st")
    p = execute(
        r.path,
        "pkg/st.py",
        parse_proposal(
            {"target": "status", "kind": "return_value", "call_inputs": {"args": [200]}}
        ),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "'ok'"
    siblings = {"'error'", "'rejected'", "'cached'"}
    assert len(siblings & set(p.options)) >= 2  # the branches the reader must rule out
