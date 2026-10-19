"""Untrusted repos get strong isolation (Docker or strict Seatbelt) or nothing; --trusted and
CDEBT_TRUSTED=1 are the ONLY opt-outs and are never read from the repository."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdebt import sandbox
from cdebt.cli import app
from cdebt.sandbox import SandboxUnavailable, run_code
from conftest import Repo


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CDEBT_TRUSTED", raising=False)
    monkeypatch.setattr(sandbox, "DEFAULT_ISOLATION", "host")
    yield


def _no_strong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "docker_available", lambda image=sandbox.DOCKER_IMAGE: False)
    monkeypatch.setattr(sandbox, "_sandbox_exec_available", lambda: False)


@pytest.mark.posix_sandbox
def test_strong_refuses_when_nothing_strong_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_strong(monkeypatch)
    with pytest.raises(SandboxUnavailable, match="not marked trusted"):
        run_code("print(1)", isolation="strong")
    with pytest.raises(SandboxUnavailable, match="Docker isolation requested"):
        run_code("print(1)", isolation="docker")
    assert run_code("print(1)", isolation="host").returncode == 0  # trusted path still works


def test_is_trusted_only_from_flag_or_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert not sandbox.is_trusted(False)
    assert sandbox.is_trusted(True)
    monkeypatch.setenv("CDEBT_TRUSTED", "1")
    assert sandbox.is_trusted(False)
    monkeypatch.setenv("CDEBT_TRUSTED", "yes")  # only the exact value counts
    assert not sandbox.is_trusted(False)


@pytest.mark.posix_sandbox
def test_repo_config_cannot_mark_itself_trusted(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A PR author controls files in the repo; a `trusted = true` there must have no effect."""
    r: Repo = make_repo()
    r.write(".cdebt/config.toml", "trusted = true\n[sandbox]\ntrusted = true\n")
    r.write("pyproject.toml", "[tool.cdebt]\ntrusted = true\n")
    r.write("m.py", "def f(a: int) -> int:\n    return a\n")
    r.commit("x")
    _no_strong(monkeypatch)
    res = CliRunner().invoke(app, ["quiz", "m.py", "--repo", str(r.path), "--kinds", "runtime"])
    assert res.exit_code == 2 and "not marked trusted" in res.output


@pytest.mark.posix_sandbox
def test_cli_trusted_flag_and_env(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", "def f(a: int) -> int:\n    return a + 1\n")
    r.commit("x")
    _no_strong(monkeypatch)
    run = CliRunner()
    base = [
        "quiz",
        "m.py",
        "--repo",
        str(r.path),
        "--kinds",
        "runtime",
        "--emit",
        str(r.path / "q.json"),
    ]
    assert run.invoke(app, base).exit_code == 2
    ok = run.invoke(app, [*base, "--trusted"])
    assert ok.exit_code == 0, ok.output
    monkeypatch.setenv("CDEBT_TRUSTED", "1")
    assert run.invoke(app, base).exit_code == 0
    monkeypatch.delenv("CDEBT_TRUSTED")
    cov = run.invoke(app, ["coverage", "--repo", str(r.path), "--no-scip"])
    assert cov.exit_code in (0, 2)  # no AI lines in this repo: nothing to run, or refusal


@pytest.mark.posix_sandbox
def test_mutation_probes_skipped_when_tests_cannot_be_isolated(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cdebt.probes import mutation
    from cdebt.units import extract_units

    r: Repo = make_repo()
    src = "def f(a: int) -> int:\n    return a + 1\n"
    r.write("m.py", src)
    r.write("tests/test_m.py", "from m import f\n\ndef test_f():\n    assert f(1) == 2\n")
    r.commit("x")
    _no_strong(monkeypatch)
    monkeypatch.setattr(sandbox, "DEFAULT_ISOLATION", "strong")
    probes, skips = mutation.generate(r.path, "m.py", src, extract_units("m.py", src), 0)
    assert probes == [] and "mutation probes skipped" in skips[0]


def test_strict_seatbelt_runs_clean_code_and_hides_home(monkeypatch: pytest.MonkeyPatch) -> None:
    if not sandbox._sandbox_exec_available():
        pytest.skip("sandbox-exec not available")
    monkeypatch.setattr(sandbox, "docker_available", lambda image=sandbox.DOCKER_IMAGE: False)
    r = run_code("print('hi')", isolation="strong")
    assert r.isolation.endswith("sandbox-exec(strict)") and r.payload
    assert r.payload["stdout"] == "hi\n"
