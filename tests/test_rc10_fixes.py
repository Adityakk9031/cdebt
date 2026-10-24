"""Fixes found in the first hand-answered test (rc9): typed-ahead keys, empty answers, the run
order on a retry, the gate's wording, agent detection and the missing agent extra, and
realistic generated inputs. No network; the pty test needs a POSIX terminal."""

from __future__ import annotations

import ast
import io
import os
import random
import sys
import threading
import time
from pathlib import Path

import pytest

from cdebt import adaptive
from cdebt.models import Probe


def _probe() -> Probe:
    return Probe(id="p", kind="runtime", path="m.py", symbol="f", question="q?", options=["1", "2"],
                 answer="1", verified_by="sandbox-exec", difficulty="easy")  # fmt: skip


def test_an_empty_answer_is_asked_again_never_graded(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n   \nB\n"))
    ans, _secs = adaptive.tty_ask(_probe(), None)
    assert ans == "B"
    assert capsys.readouterr().out.count("type a letter or the value") == 2


def test_end_of_input_returns_without_looping(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert adaptive.tty_ask(_probe(), None)[0] == ""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX terminal")
def test_keys_typed_before_the_question_are_dropped(monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    master, slave = os.openpty()
    os.write(master, b"\n")  # Enter pressed while the agent was writing questions
    time.sleep(0.2)
    tty = os.fdopen(slave, "r")
    monkeypatch.setattr(sys, "stdin", tty)

    def type_later() -> None:
        time.sleep(0.5)
        os.write(master, b"A\n")

    threading.Thread(target=type_later, daemon=True).start()
    try:
        ans, _secs = adaptive.tty_ask(_probe(), None)
    finally:
        tty.close()
        os.close(master)
    assert ans == "A"  # the early Enter did not become the answer to Q1


@pytest.mark.posix_sandbox
def test_a_retry_asks_never_quizzed_functions_first(make_repo) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    from cdebt.config import Config
    from cdebt.units import extract_units
    from test_adaptive import NATURAL, REL, Scripted, _pool, _repo

    r = _repo(make_repo)
    src = (r.path / REL).read_text()
    left: list[str] = []
    outs = adaptive.run_adaptive(
        r.path, REL, Config(), extract_units(REL, src), src, _pool(r), Scripted(**NATURAL),
        adaptive.Policy(max_questions_per_run=3), not_quizzed=left, first={"inc"},
    )  # fmt: skip
    assert [o.symbol for o in outs] == ["inc"] and left == ["classify"]  # smaller, but never asked


def test_a_claude_that_cannot_run_is_not_detected(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    from cdebt.agents import adapters

    bad = tmp_path / "claude"
    bad.write_bytes(b"\x7fELF not a mac program")
    bad.chmod(0o755)
    monkeypatch.setattr(adapters.shutil, "which", lambda name: str(bad))
    monkeypatch.setattr(adapters, "CLAUDE_MAC_GLOB", "no-such-dir-*/claude")
    assert adapters.find_claude() is None and "claude_code" not in adapters.detect()
    good = tmp_path / "ok" / "claude"
    good.parent.mkdir()
    good.write_text("#!/bin/sh\necho '9.9 (Claude Code)'\n")
    good.chmod(0o755)
    monkeypatch.setattr(adapters.shutil, "which", lambda name: str(good))
    assert adapters.find_claude() == str(good)


def test_a_missing_agent_extra_says_how_to_install_it(monkeypatch, make_repo) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    import importlib.util

    from cdebt import agents
    from cdebt.agents.userconfig import AgentChoice
    from cdebt.config import Config

    r = make_repo()
    r.write("m.py", "def f(x: int) -> int:\n    return x\n")
    r.commit("m")
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda n, *a: None if n == "mcp" else real(n, *a)
    )
    probes, notes = agents.propose_with_agent(
        r.path, "m.py", Config(), AgentChoice(name="claude_code")
    )
    assert probes == [] and any('pip install "cdebt[agent]"' in n for n in notes), notes


def test_generated_numbers_are_not_negative_by_default() -> None:
    from cdebt.probes.runtime import gen_value

    rng = random.Random(0)
    ints = [gen_value(ast.parse("int", mode="eval").body, rng) for _ in range(300)]
    floats = [gen_value(ast.parse("float", mode="eval").body, rng) for _ in range(300)]
    assert min(ints) >= 0 and min(floats) >= 0  # type: ignore[type-var]


def test_init_says_the_workflow_must_reach_the_default_branch(make_repo) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    """rc10 demo: `/cdebt recheck` did nothing because the workflow was only on `feature`;
    GitHub runs comment-triggered workflows from the default branch only."""
    from typer.testing import CliRunner

    from cdebt.cli import app
    from conftest import git

    r = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("a")
    git(r.path, "branch", "-M", "main")
    git(r.path, "checkout", "-q", "-b", "feature")
    res = CliRunner().invoke(app, ["init", "--repo", str(r.path), "--yes"])
    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    assert "you are on `feature`" in out and "to `main` (the default branch)" in out
    assert "git switch main" in out and "/cdebt recheck" in out
    assert "unpublished" not in out
    git(r.path, "checkout", "-q", "main")
    on_main = " ".join(
        CliRunner().invoke(app, ["init", "--repo", str(r.path), "--yes"]).output.split()
    )
    assert "commit .github/workflows/cdebt.yml on `main`" in on_main


@pytest.mark.skipif(sys.platform == "win32", reason="bash script")
def test_a_recheck_comment_gets_a_visible_reply(tmp_path: Path) -> None:
    """rc10 demo: `/cdebt recheck` edited the comment far above and looked like nothing happened."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    (bin_dir / "cdebt").write_text(
        "#!/bin/sh\nprintf '## cdebt: 3 AI-written lines not understood (advisory)\\n' > cdebt-gate.md\n"  # noqa: E501
        'echo \'{"passed": false, "ai_lines_unverified": 3}\'\n'
    )
    (bin_dir / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> {log}\n'
        'case "$*" in *--paginate*) echo 123;; *"-F body=@cdebt-recheck.md"*) cat cdebt-recheck.md >> '  # noqa: E501
        f"{log};; esac\n"
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    work = tmp_path / "w"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_TOKEN": "x",
           "GITHUB_REPOSITORY": "o/r", "ISSUE_NUMBER": "7", "PR_NUMBER": "", "GITHUB_BASE_REF": "main"}  # fmt: skip  # noqa: E501
    subprocess.run(
        ["bash", str(root / "scripts" / "action_run.sh")], cwd=work, env=env, check=False
    )
    text = log.read_text()
    assert "issues/comments/123" in text  # the sticky comment is still edited in place
    assert "re-checked: cdebt: 3 AI-written lines not understood (advisory)" in text  # and a reply
    # a normal PR event does not add the reply
    log.unlink()
    env["PR_NUMBER"] = "7"
    subprocess.run(
        ["bash", str(root / "scripts" / "action_run.sh")], cwd=work, env=env, check=False
    )
    assert "re-checked" not in log.read_text()
