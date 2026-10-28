"""The fake world: functions that call an API, run a command, read the environment, read or write
files or are async run for real in the sandbox, and the answer comes from the run. Nothing real
is contacted or spawned. No network and no real agent."""

from __future__ import annotations

import ast

import pytest

from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.config import Config
from conftest import Repo

pytestmark = pytest.mark.posix_sandbox

SVC = """import asyncio
import json
import os
import socket
import subprocess
import urllib.request
import uuid


def fetch_name(user_id: int) -> str:
    with urllib.request.urlopen(f"https://api.example.com/users/{user_id}") as r:
        return json.load(r)["name"]


def deploy_target() -> str:
    return os.environ.get("DEPLOY_ENV", "dev")


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def save_report(name: str) -> int:
    with open(f"report_{name}.txt", "w") as f:
        f.write("x")
    return 1


def load_port() -> int:
    with open("conf/app.json") as f:
        return json.load(f)["port"]


def new_id() -> str:
    return str(uuid.uuid4())


async def double_later(n: int) -> int:
    await asyncio.sleep(5)
    return n * 2


def say(n: int) -> int:
    print("hello", n)
    return n


def quiet(n: int) -> int:
    return n + 1


def dial() -> int:
    s = socket.create_connection(("example.com", 80))
    return s.fileno()


def spawn() -> int:
    return os.fork()


def escape() -> int:
    with open("/tmp/cdebt-world-escape.txt", "w") as f:
        f.write("x")
    return 1
"""


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/svc.py", SVC)
    r.commit("svc")
    return r


def _run(r: Repo, **kw):  # type: ignore[no-untyped-def]
    return execute(r.path, "pkg/svc.py", parse_proposal(kw), Config(), proposer="agent:test")


NAME_WORLD = {"http": [{"url": "/users/7", "body": {"name": "Ada"}}]}


def test_http_call_answers_from_the_scenario(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(
        r,
        target="fetch_name",
        kind="return_value",
        call_inputs={"args": [7]},
        world=NAME_WORLD,
    )
    assert p.answer == "'Ada'"
    assert "fake server" in p.question and "/users/7" in p.question
    assert p.evidence["effects"]["net"] == ["GET https://api.example.com/users/7"]


def test_unmatched_request_gets_the_default_answer(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="fetch_name", kind="return_value", call_inputs={"args": [1]})
    assert p.answer == "raises KeyError"  # the default fake server answers 200 and {}


def test_http_error_status_is_raised_like_the_real_library(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    w = {"http": [{"url": "/users/", "status": 503}]}
    p = _run(r, target="fetch_name", kind="return_value", call_inputs={"args": [1]}, world=w)
    assert p.answer == "raises HTTPError"


def test_requests_made_is_an_effect_question(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(
        r,
        target="fetch_name",
        kind="net_requests",
        call_inputs={"args": [7]},
        world=NAME_WORLD,
    )
    assert ast.literal_eval(p.answer) == ["GET https://api.example.com/users/7"]
    assert p.answer in p.options and len(p.options) >= 2


def test_environment_comes_from_the_scenario(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    plain = _run(r, target="deploy_target", kind="return_value")
    assert plain.answer == "'dev'" and "no environment variables" in plain.question
    p = _run(r, target="deploy_target", kind="return_value", world={"env": {"DEPLOY_ENV": "prod"}})
    assert p.answer == "'prod'" and "DEPLOY_ENV='prod'" in p.question


def test_shell_command_is_faked_and_logged(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    w = {"commands": [{"cmd": "rev-parse", "stdout": "abc123\n"}]}
    p = _run(r, target="git_head", kind="return_value", world=w)
    assert p.answer == "'abc123'"
    c = _run(r, target="git_head", kind="commands_run", world=w)
    assert ast.literal_eval(c.answer) == ["git rev-parse HEAD"]


def test_failing_command_raises_like_the_real_one(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    w = {"commands": [{"cmd": "git", "returncode": 128}]}
    p = _run(r, target="git_head", kind="return_value", world=w)
    assert p.answer == "raises CalledProcessError"


def test_file_writes_stay_in_scratch_and_are_a_question(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ret = _run(r, target="save_report", kind="return_value", call_inputs={"args": ["a"]})
    assert ret.answer == "1"  # two runs agree: the first run's file was removed before the second
    p = _run(r, target="save_report", kind="files_written", call_inputs={"args": ["a"]})
    assert ast.literal_eval(p.answer) == ["+report_a.txt"]
    assert not (r.path / "report_a.txt").exists()  # never touches the real repository


def test_scenario_files_exist_before_the_call(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    w = {"files": {"conf/app.json": '{"port": 8080}'}}
    p = _run(r, target="load_port", kind="return_value", world=w)
    assert p.answer == "8080" and "conf/app.json" in p.question
    assert _run(r, target="load_port", kind="return_value").answer == "raises FileNotFoundError"
    assert not (r.path / "conf").exists()


def test_uuid_is_seeded(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="new_id", kind="return_value")
    assert len(ast.literal_eval(p.answer)) == 36 and "seeded" in p.question


def test_async_function_runs_without_waiting(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="double_later", kind="return_value", call_inputs={"args": [4]})
    assert p.answer == "8"


def test_printed_output_is_a_question(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    p = _run(r, target="say", kind="stdout_text", call_inputs={"args": [3]})
    assert ast.literal_eval(p.answer) == "hello 3\n"


def test_nothing_to_ask_is_rejected(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    for kind in ("net_requests", "commands_run", "files_written", "stdout_text"):
        with pytest.raises(Rejected, match="nothing to ask"):
            _run(r, target="quiet", kind=kind, call_inputs={"args": [1]})


@pytest.mark.parametrize("target", ["dial", "spawn", "escape"])
def test_real_network_processes_and_outside_writes_stay_blocked(make_repo, target) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    with pytest.raises(Rejected, match="blocked by the sandbox|could not execute"):
        _run(r, target=target, kind="return_value")


def test_scenario_cannot_replace_code_or_escape_the_repo(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    with pytest.raises(Rejected, match="data"):
        _run(
            r,
            target="quiet",
            kind="return_value",
            call_inputs={"args": [1]},
            world={"files": {"pkg/svc.py": "def quiet(n):\n    return 99\n"}},
        )
    for bad in ("../outside.txt", "/etc/passwd"):
        with pytest.raises(Rejected, match="relative"):
            _run(
                r,
                target="quiet",
                kind="return_value",
                call_inputs={"args": [1]},
                world={"files": {bad: "x"}},
            )


def test_sibling_packages_in_a_monorepo_are_importable(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("packages/core/src/corelib/__init__.py", "")
    r.write(
        "packages/core/src/corelib/rates.py", "RATE = 3\n\n\ndef rate() -> int:\n    return RATE\n"
    )
    r.write("packages/app/src/applib/__init__.py", "")
    r.write(
        "packages/app/src/applib/pricing.py",
        "from corelib.rates import rate\n\n\ndef price(n: int) -> int:\n    return n * rate()\n",
    )
    r.commit("monorepo")
    p = execute(
        r.path,
        "packages/app/src/applib/pricing.py",
        parse_proposal({"target": "price", "kind": "return_value", "call_inputs": {"args": [4]}}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "12"
