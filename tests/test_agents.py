"""Milestone 1: questions proposed by the developer's own coding agent, verified by execution.

No network and no real agent: `tests/_fake_agent.py` is a stub that speaks real MCP over stdio."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from cdebt.agents import adapters, propose_with_agent, userconfig
from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.agents.session import targets_for_file
from cdebt.agents.userconfig import AgentChoice
from cdebt.config import Config
from cdebt.quiz import build_quiz
from conftest import Repo

pytestmark = pytest.mark.posix_sandbox

SHOP = """import time
FEE = 5


def shipping(total: int, express: bool = False) -> int:
    base = 0 if total >= 50 else FEE
    if express:
        base = base + 10
    return base


def stamp() -> int:
    return int(time.time())
"""
FAKE = Path(__file__).parent / "_fake_agent.py"


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/shop.py", SHOP)
    r.commit("shop")
    return r


def _run(r: Repo, **kw):  # type: ignore[no-untyped-def]
    return execute(r.path, "pkg/shop.py", parse_proposal(kw), Config(), proposer="agent:test")


def test_return_value_branch_and_variable_probes_get_answers_from_a_trace(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="shipping", kind="return_value",
             call_inputs={"args": [60], "kwargs": {"express": True}})  # fmt: skip
    assert p.answer == "10" and p.verified_by == "execution-trace" and p.proposer == "agent:test"
    b = _run(r, target="shipping", kind="branch_taken", call_inputs={"args": [10]}, line=8)
    assert b.answer == "no" and b.difficulty == "medium" and b.options == ["yes", "no"]
    b2 = _run(r, target="shipping", kind="branch_taken", call_inputs={"args": [10, True]}, line=8)
    assert b2.answer == "yes"
    v = _run(r, target="shipping", kind="variable_at_line", call_inputs={"args": [10, True]},
             line=9, variable="base")  # fmt: skip
    assert v.answer == "15" and v.evidence["deterministic_runs"] == 2


def test_a_harness_may_mock_io_but_must_run_the_real_target(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    clock = "def probe():\n    import time\n    time.time = lambda: 1000\n    return target_module.stamp()\n"
    p = _run(
        r, target="stamp", kind="return_value", question="stamp, fake clock", harness_code=clock
    )
    assert p.answer == "1000" and p.harness_hash and len(p.harness_hash) == 64
    cases = {
        "replaced": ("def probe():\n    target_module.shipping = lambda *a, **k: 3\n"
                     "    return target_module.shipping(1)\n", "replaced the target"),
        "never called": ("def probe():\n    return 3\n", "did not execute"),
        "flaky": ("def probe():\n    import time\n    return time.time_ns() + target_module.stamp()\n",
                  "non-deterministic"),
    }  # fmt: skip
    for name, (code, why) in cases.items():
        tgt = "stamp" if name == "flaky" else "shipping"
        with pytest.raises(Rejected, match=why):
            _run(r, target=tgt, kind="return_value", question=name, harness_code=code)


def test_bad_proposals_are_rejected_with_reasons(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    for kw, why in [
        (dict(target="stamp", kind="return_value"), "side effects"),  # uses time, no harness
        (dict(target="nope", kind="return_value"), "no function"),
        (dict(target="shipping", kind="branch_taken", call_inputs={"args": [1]}), "`line`"),
        (dict(target="shipping", kind="branch_taken", call_inputs={"args": [1]}, line=999), "`line`"),
        (dict(target="shipping", kind="variable_at_line", call_inputs={"args": [1]}, line=9), "variable"),
        (dict(target="shipping", kind="constant_value"), "name"),
        (dict(target="shipping", kind="what_calls"), "SCIP"),
        (dict(target="shipping", kind="return_value", harness_code="x = 1", question="q"), "probe"),
    ]:  # fmt: skip
        with pytest.raises(Rejected, match=why):
            _run(r, **kw)
    with pytest.raises(Rejected, match="malformed"):
        parse_proposal({"target": "shipping", "kind": "teleport"})


def test_constant_value_reuses_the_module_import_probe(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="shipping", kind="constant_value", name="FEE")
    assert p.answer == "5" and p.proposer == "agent:test" and p.difficulty == "easy"


def test_proposal_has_no_answer_field() -> None:
    assert "answer" not in parse_proposal({"target": "f", "kind": "return_value"}).model_dump()
    # an agent that smuggles an `answer` is ignored, not trusted
    p = parse_proposal({"target": "f", "kind": "return_value", "answer": "42"})
    assert "answer" not in p.model_dump()


def test_end_to_end_with_a_fake_mcp_agent(make_repo, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    report = tmp_path / "report.json"
    monkeypatch.setenv("FAKE_AGENT_REPORT", str(report))
    choice = AgentChoice(name="command", command=shlex.join([sys.executable, str(FAKE)]))
    probes, notes = propose_with_agent(r.path, "pkg/shop.py", Config(), choice, timeout=120)
    rep = json.loads(report.read_text())
    replies = rep["replies"]
    assert replies[:4] == ["accepted"] * 4
    assert all(x.startswith("rejected: ") for x in replies[4:]) and len(replies) == 10
    assert any("replaced the target" in x for x in replies)
    assert any("did not execute" in x for x in replies)
    assert any("non-deterministic" in x for x in replies)
    assert any("unknown target" in x for x in replies) and any("kind must be" in x for x in replies)
    # the agent (which can read the session directory) never saw an answer, in a reply or on disk
    assert rep["leaked"] is False
    assert not any(a in x for x in replies for a in ("10", "15", "1000") if x == a)
    answers = {p.question: p.answer for p in probes}
    assert len(probes) == 4 and sorted(answers.values()) == ["10", "1000", "15", "no"]
    assert all(p.proposer == "agent:command" and p.verified_by == "execution-trace" for p in probes)
    assert any("4 accepted" in n for n in notes)
    assert not list(Path(tmp_path).glob("cdebt-session-*"))  # session directory removed


def test_budget_per_target_is_enforced(make_repo, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    report = tmp_path / "report.json"
    monkeypatch.setenv("FAKE_AGENT_REPORT", str(report))
    choice = AgentChoice(name="command", command=shlex.join([sys.executable, str(FAKE)]))
    propose_with_agent(r.path, "pkg/shop.py", Config(), choice, per_target=2, timeout=120)
    replies = json.loads(report.read_text())["replies"]
    # shipping gets 2 proposals; the rest of its attempts are refused for budget
    assert sum("budget of 2" in x for x in replies) >= 2


def test_builtin_is_used_when_no_agent_or_the_agent_fails(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    for choice in (None, AgentChoice(name="builtin"), AgentChoice(name="command", command="false")):
        probes, _units, skips = build_quiz(r.path, "pkg/shop.py", Config(), agent=choice, n=3)
        assert probes and all(p.proposer in (None, "builtin") for p in probes)
        if choice and choice.name == "command":
            assert any("produced no proposals" in s for s in skips)
    missing = AgentChoice(name="command", command="/nonexistent/agent-binary")
    probes, _u, _s = build_quiz(r.path, "pkg/shop.py", Config(), agent=missing, n=2)
    assert probes  # a launch failure never breaks the quiz


def test_agent_time_limit_kills_a_hung_agent(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    choice = AgentChoice(name="command", command="sleep 60")
    probes, notes = propose_with_agent(r.path, "pkg/shop.py", Config(), choice, timeout=1.0)
    assert probes == [] and any("timed out" in n for n in notes)


def test_targets_only_list_unverified_functions_with_source_and_tests(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    r.write(
        "tests/test_shop.py",
        "from pkg.shop import shipping\n\ndef test_s():\n    assert shipping(60) == 0\n",
    )
    r.commit("tests")
    ts = {t.symbol: t for t in targets_for_file(r.path, "pkg/shop.py", Config())}
    assert set(ts) == {"shipping", "stamp"}
    assert "def shipping(total: int" in ts["shipping"].signature
    assert "return base" in ts["shipping"].source
    assert ts["shipping"].tests and "test_s" in ts["shipping"].tests[0]


def test_user_config_roundtrip_and_never_from_the_repo(make_repo, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cfg = tmp_path / "cfg" / "config.toml"
    monkeypatch.setenv("CDEBT_USER_CONFIG", str(cfg))
    assert userconfig.load() is None
    userconfig.save(AgentChoice(name="command", command='my "agent" --flag'))
    got = userconfig.load()
    assert got and got.name == "command" and got.command == 'my "agent" --flag'
    # a malicious repo cannot select the agent: nothing under the repo is ever read
    r = _repo(make_repo)
    r.write(".cdebt/agent.toml", '[agent]\nname = "command"\ncommand = "touch pwned"\n')
    userconfig.clear()
    assert userconfig.load() is None
    assert userconfig.clear() is False


def test_claude_adapter_is_read_only_and_cdebt_tools_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    exe = adapters.find_claude()
    if exe is None:
        pytest.skip("claude CLI not installed")
    run = adapters.build(AgentChoice(name="claude_code"), tmp_path, tmp_path, 8)
    a = run.argv
    assert a[0] == exe and "-p" in a and "--strict-mcp-config" in a
    assert a[a.index("--tools") + 1] == "Read,Grep,Glob"  # no Bash, Edit or Write
    allowed = a[a.index("--allowedTools") + 1]
    assert allowed == "mcp__cdebt__get_targets,mcp__cdebt__propose_probe"
    assert a[a.index("--permission-mode") + 1] == "dontAsk"
    cfg = json.loads((tmp_path / "mcp.json").read_text())
    args = cfg["mcpServers"]["cdebt"]["args"]
    assert args[args.index("mcp") + 1 :][-2:] == ["--session", str(tmp_path)]


def test_mcp_server_applies_untrusted_isolation_unless_the_user_chose_trusted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from cdebt import sandbox

    old = sandbox.DEFAULT_ISOLATION
    try:
        sandbox.set_default_isolation("strong")
        assert "--trusted" not in adapters.mcp_argv(tmp_path)
        sandbox.set_default_isolation("host")
        assert "--trusted" in adapters.mcp_argv(tmp_path)
    finally:
        sandbox.set_default_isolation(old)


def test_a_question_from_both_the_agent_and_the_builtin_generator_is_asked_once(
    make_repo, tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    monkeypatch.setenv("FAKE_AGENT_REPORT", str(tmp_path / "report.json"))
    choice = AgentChoice(name="command", command=shlex.join([sys.executable, str(FAKE)]))
    probes, _u, _s = build_quiz(r.path, "pkg/shop.py", Config(), agent=choice, n=20, per_unit=6)
    qs = [p.question for p in probes]
    assert len(qs) == len(set(qs))
