"""Container/CPU probes at import time (cgroup files) and `Popen[...]` annotations must not make a
module un-runnable in the fake world."""

from __future__ import annotations

import pytest

from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.config import Config

pytestmark = pytest.mark.posix_sandbox

SRC = """import subprocess


def _cpus() -> int:
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            return int(fh.read().split()[0])
    except (OSError, ValueError):
        return 2


CPUS = _cpus()


def handle(p: "subprocess.Popen[str] | None") -> int:
    return CPUS


class Holder:
    proc: subprocess.Popen[str] | None = None
"""


def test_cgroup_probe_and_popen_subscript(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/m.py", SRC)
    r.commit("m")
    p = execute(
        r.path,
        "pkg/m.py",
        parse_proposal(
            {"target": "handle", "kind": "return_value", "call_inputs": {"args": [None]}}
        ),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "2"


def test_importing_requests_does_not_trip_the_socket_block(make_repo) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("urllib3")
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/h.py", "import urllib3\n\n\ndef two() -> int:\n    return 2\n")
    r.commit("h")
    p = execute(
        r.path,
        "pkg/h.py",
        parse_proposal({"target": "two", "kind": "return_value"}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "2"


def test_tempfile_goes_to_the_scratch_dir_not_system_tmp(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write(
        "pkg/t.py",
        "import tempfile\n\n\ndef where() -> bool:\n"
        "    with tempfile.NamedTemporaryFile() as f:\n"
        "        return f.name.startswith(tempfile.gettempdir())\n",
    )
    r.commit("t")
    p = execute(
        r.path,
        "pkg/t.py",
        parse_proposal({"target": "where", "kind": "return_value"}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "True"


def test_files_outside_the_repo_do_not_exist_in_the_fake_world(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write(
        "pkg/s.py",
        "def peek(path: str) -> str:\n"
        "    try:\n"
        "        with open(path) as f:\n"
        "            return f.read()\n"
        "    except FileNotFoundError:\n"
        "        return 'missing'\n",
    )
    r.commit("s")
    p = execute(
        r.path,
        "pkg/s.py",
        parse_proposal(
            {"target": "peek", "kind": "return_value", "call_inputs": {"args": [str(secret)]}}
        ),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "'missing'"  # nothing outside the repo is ever read
    assert "TOP-SECRET" not in p.question + "".join(p.options)
    assert "outside the repository do not exist" in p.question


def test_shared_memory_is_absent_but_optional_importers_still_load(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write(
        "pkg/mp.py",
        "import multiprocessing.managers\n\n\ndef shm() -> str:\n"
        "    try:\n"
        "        from multiprocessing import shared_memory  # noqa: F401\n"
        "    except ImportError:\n"
        "        return 'absent'\n"
        "    return 'present'\n",
    )
    r.commit("mp")
    p = execute(
        r.path,
        "pkg/mp.py",
        parse_proposal({"target": "shm", "kind": "return_value"}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "'absent'"


WRITER = """import os


def save(path: str) -> str:
    try:
        with open(path, "w") as f:
            f.write("x")
    except OSError as e:
        return f"failed {e.errno}"
    return "saved"


def make(path: str) -> str:
    try:
        os.mkdir(path)
    except OSError:
        return "no"
    return "yes"
"""


def test_writes_outside_the_repo_fail_like_a_read_only_disk(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/w.py", WRITER)
    r.commit("w")
    outside = tmp_path / "planted.txt"
    newdir = tmp_path / "newdir"

    def run(target: str, arg: str):  # type: ignore[no-untyped-def]
        return execute(
            r.path,
            "pkg/w.py",
            parse_proposal(
                {"target": target, "kind": "return_value", "call_inputs": {"args": [arg]}}
            ),
            Config(),
            proposer="agent:test",
        )

    p = run("save", str(outside))
    assert p.answer == "'failed 30'" and "read-only" in p.question
    assert not outside.exists()  # nothing was written outside the scratch copy
    assert run("make", str(newdir)).answer == "'no'" and not newdir.exists()
    assert run("make", "inside_dir").answer == "'yes'"  # inside the scratch copy still works
    assert not (r.path / "inside_dir").exists()  # ... and never in the real repository


def test_wrong_arguments_get_a_reason_the_agent_can_act_on(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/w.py", WRITER)
    r.commit("w")
    with pytest.raises(Rejected, match="failed before save ran.*def save"):
        execute(
            r.path,
            "pkg/w.py",
            parse_proposal({"target": "save", "kind": "return_value"}),
            Config(),
            proposer="agent:test",
        )


def test_objects_with_a_memory_address_get_a_clear_reason(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/o.py", "class Box:\n    pass\n\n\ndef box() -> Box:\n    return Box()\n")
    r.commit("o")
    with pytest.raises(Rejected, match="no stable printed form"):
        execute(
            r.path,
            "pkg/o.py",
            parse_proposal({"target": "box", "kind": "return_value"}),
            Config(),
            proposer="agent:test",
        )


def test_answers_show_the_repo_not_the_random_scratch_path(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write(
        "pkg/p.py",
        "import tempfile\n\n\ndef where() -> str:\n    return tempfile.gettempdir() + '/locks'\n",
    )
    r.commit("p")
    p = execute(
        r.path,
        "pkg/p.py",
        parse_proposal({"target": "where", "kind": "return_value"}),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "'<repo>/tmp/locks'"
    assert "cdebt-agent-" not in p.answer + "".join(p.options)


def test_deleting_a_real_file_outside_the_repo_never_happens(make_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    victim = tmp_path / "keep.txt"
    victim.write_text("still here")
    r = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/rm.py", "import os\n\n\ndef rm(p: str) -> int:\n    os.remove(p)\n    return 1\n")
    r.commit("rm")
    p = execute(
        r.path,
        "pkg/rm.py",
        parse_proposal(
            {"target": "rm", "kind": "return_value", "call_inputs": {"args": [str(victim)]}}
        ),
        Config(),
        proposer="agent:test",
    )
    assert p.answer == "raises OSError"
    assert victim.read_text() == "still here"
