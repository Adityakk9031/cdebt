"""Agent reach: targets go to the agent in batches, and functions that ended with too few verified
questions are offered again. The agent run is replaced by a stub; no real agent, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

import cdebt.agents as agents
from cdebt.agents.userconfig import AgentChoice
from cdebt.config import Config
from cdebt.models import Probe
from conftest import Repo

CODE = "".join(f"def f{i}(x: int) -> int:\n    return x + {i}\n\n\n" for i in range(7))
CHOICE = AgentChoice(name="command", command="unused")


def _probe(sym: str, k: int) -> Probe:
    return Probe(
        id=f"{sym}-{k}",
        kind="return_value",
        path="pkg/m.py",
        symbol=sym,
        question=f"q {sym} {k}",
        options=["1", "2"],
        answer="1",
        verified_by="execution-trace",
        seed=0,
    )


@pytest.fixture
def repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/m.py", CODE)
    r.commit("m")
    return r


def _stub(monkeypatch: pytest.MonkeyPatch, per_call: dict[str, int]) -> list[list[str]]:
    calls: list[list[str]] = []
    made: dict[str, int] = {}

    def fake(repo: Path, cfg: Config, choice: AgentChoice, targets, **kw):  # type: ignore[no-untyped-def]
        calls.append([t.symbol for t in targets])
        out = []
        for t in targets:
            for _ in range(per_call.get(t.symbol, 3)):
                made[t.symbol] = made.get(t.symbol, 0) + 1
                out.append(_probe(t.symbol, made[t.symbol]))
        return out, []

    monkeypatch.setattr(agents, "_one_round", fake)
    return calls


def test_targets_are_split_into_batches(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {})
    probes, notes = agents.propose_with_agent(repo.path, "pkg/m.py", Config(), CHOICE, batch=3)
    assert [len(c) for c in calls] == [3, 3, 1]
    assert len(probes) == 7 * 3
    assert any("7 of 7 functions reached 3" in n for n in notes)


def test_short_functions_get_a_second_round_only(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub(monkeypatch, {"f2": 1, "f5": 2})  # others reach 3 on the first try
    probes, notes = agents.propose_with_agent(
        repo.path, "pkg/m.py", Config(), CHOICE, batch=10, rounds=2
    )
    assert calls[0] == [f"f{i}" for i in range(7)]
    assert calls[1:] == [["f2", "f5"]]  # one retry round, only the short ones
    got = {s: sum(1 for p in probes if p.symbol == s) for s in ("f2", "f5", "f0")}
    assert got == {"f2": 2, "f5": 4, "f0": 3}  # stub adds more per round; f2 still short
    assert any("6 of 7 functions reached 3" in n for n in notes)


def test_only_limits_the_targets(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {})
    agents.propose_with_agent(repo.path, "pkg/m.py", Config(), CHOICE, only={"f1", "f6"})
    assert calls == [["f1", "f6"]]


def test_a_missing_agent_is_not_retried(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(repo, cfg, choice, targets, **kw):  # type: ignore[no-untyped-def]
        calls.append(len(targets))
        return [], ["agent command: no such agent; using the builtin generator"]

    monkeypatch.setattr(agents, "_one_round", fake)
    probes, notes = agents.propose_with_agent(repo.path, "pkg/m.py", Config(), CHOICE, batch=3)
    assert probes == [] and calls == [3]
