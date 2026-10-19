"""Docker sandbox path. Skipped unless a Docker daemon and python:3.11-slim are present.
CI runs this on a GitHub-hosted runner (see .github/workflows/ci.yml, job `docker-sandbox`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cdebt import sandbox
from cdebt.probes import runtime
from cdebt.sandbox import DOCKER_IMAGE, docker_available, run_code
from cdebt.units import extract_units

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker + image not available")


@pytest.fixture(autouse=True)
def _docker_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "DEFAULT_ISOLATION", "docker")  # the point of this module


def _leftover() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=cdebt-", "-q"], capture_output=True, text=True
    ).stdout
    return out.split()


def test_runs_in_docker_and_cleans_up() -> None:
    r = run_code("print('hi')", isolation="docker")
    assert r.isolation == "docker" and r.payload and r.payload["stdout"] == "hi\n"
    assert _leftover() == []


def test_escapes_contained_in_docker(tmp_path: Path) -> None:
    f = tmp_path / "w.txt"
    assert run_code(f"open({str(f)!r}, 'w')").violations and not f.exists()
    r = run_code("import socket\nsocket.socket().connect(('127.0.0.1', 9))")
    assert r.isolation == "docker" and r.violations
    assert run_code("import os\nos.fork()", isolation="docker").violations
    assert run_code("import ctypes\nctypes.CDLL(None)", isolation="docker").violations
    assert _leftover() == []


def test_infinite_loop_killed_and_container_removed() -> None:
    r = run_code("while True:\n    pass\n", timeout=3, isolation="docker")
    assert r.isolation == "docker" and r.timed_out
    assert _leftover() == []


def test_memory_bomb_contained_by_container_limit() -> None:
    r = run_code(
        "x = []\nwhile True:\n    x.append(bytearray(50_000_000))\n", timeout=30, mem_mb=128
    )
    assert r.isolation == "docker" and r.returncode != 0 and r.elapsed < 30
    assert _leftover() == []


def test_os_level_network_block_independent_of_audit_hook() -> None:
    """Same container flags as the sandbox, but WITHOUT the audit hook: --network none holds."""
    code = "import socket;s=socket.socket();s.settimeout(3);s.connect(('1.1.1.1',80))"
    p = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            DOCKER_IMAGE,
            "python",
            "-I",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and "unreachable" in p.stderr.lower() or "error" in p.stderr.lower()


def test_runtime_probe_answers_inside_docker(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    src = "def add(a: int, b: int) -> int:\n    return a + b\n"
    (repo / "pkg" / "m.py").write_text(src)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    probes, skips = runtime.generate(repo, "pkg/m.py", src, extract_units("pkg/m.py", src), 1)
    assert probes, skips
    assert all(p.evidence["isolation"] == "docker" for p in probes)
    a = probes[0]
    args = eval(a.evidence["args"])  # noqa: S307 (literal tuple we generated)
    assert a.answer == repr(sum(args))
