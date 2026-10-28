"""`--project-python`: probes run with the project's own interpreter, so code that imports the
project's third-party packages can be run. The person chooses it; nothing in the repo can."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdebt import sandbox
from cdebt.agents import adapters
from cdebt.agents.probes import Rejected, execute, parse_proposal
from cdebt.cli import app
from cdebt.config import Config
from conftest import Repo

pytestmark = pytest.mark.posix_sandbox


@pytest.fixture
def venv_python(tmp_path: Path) -> Path:
    """A real venv (no pip, no network) with one fake third-party package in its site-packages."""
    d = tmp_path / "projvenv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(d)], check=True)
    site = next((d / "lib").glob("python*/site-packages"))
    (site / "fakedep.py").write_text("def bump(n):\n    return n + 1\n")
    return d / "bin" / "python"


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write(
        "pkg/uses.py", "import fakedep\n\n\ndef f(n: int) -> int:\n    return fakedep.bump(n)\n"
    )
    r.commit("uses a project dependency")
    return r


def _run(r: Repo):  # type: ignore[no-untyped-def]
    return execute(
        r.path,
        "pkg/uses.py",
        parse_proposal({"target": "f", "kind": "return_value", "call_inputs": {"args": [4]}}),
        Config(),
        proposer="agent:test",
    )


def test_without_a_project_python_the_dependency_is_missing(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(sandbox.PROJECT_PYTHON_ENV, raising=False)
    with pytest.raises(Rejected, match="ModuleNotFoundError"):
        _run(_repo(make_repo))


def test_with_a_project_python_the_function_runs(make_repo, monkeypatch, venv_python) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(sandbox.PROJECT_PYTHON_ENV, str(venv_python))
    assert _run(_repo(make_repo)).answer == "5"


def test_strict_isolation_with_a_project_python(make_repo, monkeypatch, venv_python) -> None:  # type: ignore[no-untyped-def]
    if not sandbox._sandbox_exec_available():
        pytest.skip("strict isolation needs macOS sandbox-exec")
    monkeypatch.setenv(sandbox.PROJECT_PYTHON_ENV, str(venv_python))
    monkeypatch.setattr(sandbox, "DEFAULT_ISOLATION", "strong")
    assert _run(_repo(make_repo)).answer == "5"


def test_strict_read_roots_cover_the_project_interpreter(monkeypatch, venv_python) -> None:  # type: ignore[no-untyped-def]
    roots = sandbox._strict_read_roots(str(venv_python), None)
    site = next((venv_python.parent.parent / "lib").glob("python*/site-packages"))
    assert any(str(site.resolve()).startswith(r) for r in roots)
    assert sys.prefix not in roots or sys.prefix == sys.base_prefix  # cdebt's own venv not exposed


def test_docker_is_refused_with_a_project_python(make_repo, monkeypatch, venv_python) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(sandbox.PROJECT_PYTHON_ENV, str(venv_python))
    with pytest.raises(sandbox.SandboxUnavailable, match="project-python"):
        sandbox.run_code("print(1)", isolation="docker")


@pytest.mark.parametrize("bad", ["/no/such/python", "/etc/hosts"])
def test_a_bad_project_python_fails_closed(monkeypatch, bad) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(sandbox.PROJECT_PYTHON_ENV, bad)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.project_python()
    res = CliRunner().invoke(app, ["--project-python", bad, "map"])
    assert res.exit_code == 2 and "project-python" in res.output


def test_the_agent_helper_process_gets_the_same_python(monkeypatch, venv_python) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(sandbox.PROJECT_PYTHON_ENV, str(venv_python))
    argv = adapters.mcp_argv(Path("/x"))
    assert argv[argv.index("--project-python") + 1] == str(venv_python)
    assert argv.index("--project-python") < argv.index("mcp")
    monkeypatch.delenv(sandbox.PROJECT_PYTHON_ENV)
    assert "--project-python" not in adapters.mcp_argv(Path("/x"))


def test_the_repo_cannot_choose_the_interpreter(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(sandbox.PROJECT_PYTHON_ENV, raising=False)
    r: Repo = make_repo()
    r.write(".cdebt/policy.toml", 'project_python = "/bin/sh"\n')
    r.write("pyproject.toml", '[tool.cdebt]\nproject_python = "/bin/sh"\n')
    r.commit("hostile config")
    assert sandbox.project_python() is None
