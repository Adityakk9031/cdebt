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


@pytest.mark.skipif(not docker_available(sandbox.NODE_IMAGE), reason="node image not pulled")
def test_node_probe_runs_in_docker(tmp_path: Path) -> None:
    """The Node runner inside the node image: result computed, network/fs writes contained."""
    root = tmp_path.resolve()
    (root / "m.mjs").write_text(
        "import fs from 'node:fs';\nimport net from 'node:net';\n"
        "let blocked = 0;\n"
        "try { fs.writeFileSync('x.txt', 'x'); } catch (e) { blocked++; }\n"
        "try { net.connect(9, '127.0.0.1'); } catch (e) { blocked++; }\n"
        "export const R = blocked;\nexport const S = 6 * 7;\n"
    )
    res = sandbox.run_in_sandbox(
        {"mode": "attrs", "module": str(root / "m.mjs"), "names": ["R", "S"]},
        root=root,
        timeout=30,
        runtime="node",
        isolation="docker",
    )
    assert res.isolation == "docker" and res.payload, res.stderr
    assert res.payload["values"]["S"][0] == "42" and res.payload["values"]["R"][0] == "2"
    assert not (root / "x.txt").exists()


def test_agent_trace_probes_and_ci_recheck_run_in_docker(tmp_path: Path) -> None:
    """The v1.1 traced mode (agent-proposed probes) and the CI re-execution, inside the container:
    the isolation CI uses for an untrusted pull request."""
    from cdebt.agents.probes import Rejected, execute, parse_proposal
    from cdebt.config import Config
    from cdebt.models import Record
    from cdebt.recheck import verify_record

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    src = (
        "def shipping(total: int, express: bool = False) -> int:\n"
        "    base = 0 if total >= 50 else 5\n"
        "    if express:\n        base = base + 10\n    return base\n"
    )
    (tmp_path / "pkg" / "shop.py").write_text(src)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    cfg = Config()

    def run(**kw):  # type: ignore[no-untyped-def]
        return execute(
            tmp_path, "pkg/shop.py", parse_proposal(kw), cfg, proposer="agent:t", source=src
        )

    p = run(
        target="shipping",
        kind="return_value",
        call_inputs={"args": [60], "kwargs": {"express": True}},
    )
    assert p.answer == "10" and p.evidence["isolation"] == "docker"
    b = run(target="shipping", kind="branch_taken", call_inputs={"args": [10]}, line=4)
    assert b.answer == "no"
    v = run(
        target="shipping",
        kind="variable_at_line",
        call_inputs={"args": [10, True]},
        line=5,
        variable="base",
    )
    assert v.answer == "15"
    with pytest.raises(Rejected, match="replaced the target"):
        run(target="shipping", kind="return_value", question="mock",
            harness_code="def probe():\n    target_module.shipping = lambda *a, **k: 3\n    return target_module.shipping(1)\n")  # fmt: skip
    # CI re-execution of a genuine record passes; a doctored answer fails
    for q in (p, b, v):
        q.user_answer, q.correct = q.answer, True
    rec = Record(
        unit_path="pkg/shop.py",
        unit_symbol="shipping",
        content_hash="h",
        probes=[p, b, v],
        passed=True,
    )
    assert verify_record(tmp_path, "pkg/shop.py", rec, cfg, src).ok
    wrong = next(o for o in p.options if o != p.answer)
    p.answer, p.user_answer = wrong, wrong
    bad = verify_record(tmp_path, "pkg/shop.py", rec, cfg, src)
    assert not bad.ok and "does not match re-execution" in (bad.reason or "")
    assert _leftover() == []
