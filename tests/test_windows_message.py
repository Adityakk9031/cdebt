"""On Windows every command that executes code refuses with one clear message (runs on every OS
by pretending to be Windows)."""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from cdebt import sandbox
from cdebt.cli import app
from conftest import Repo


@pytest.mark.parametrize(
    "cmd",
    [
        ["quiz", "m.py"],
        ["attest", "m.py", "--note", "x"],
        ["coverage"],
    ],
)
def test_execution_commands_refuse_on_windows(make_repo, monkeypatch, cmd) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", "def f(a: int) -> int:\n    return a\n")
    r.commit("x")
    monkeypatch.setattr(sys, "platform", "win32")
    res = CliRunner().invoke(app, [*cmd, "--repo", str(r.path)])
    out = " ".join(res.output.split())
    assert res.exit_code == 2 and "Windows is not supported" in out and "WSL2" in out


def test_sandbox_refuses_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(sandbox.SandboxUnavailable, match="Windows is not supported"):
        sandbox.run_code("print(1)")


def test_read_only_commands_do_not_need_the_sandbox(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", "def f(a: int) -> int:\n    return a\n")
    r.commit("x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    monkeypatch.setattr(sys, "platform", "win32")
    for cmd in (["map", "--no-scip"], ["plan", "--no-scip"]):
        res = CliRunner().invoke(app, [*cmd, "--repo", str(r.path)])
        assert res.exit_code == 0, res.output


def test_version_flag_prints_the_installed_version() -> None:
    from typer.testing import CliRunner

    from cdebt.cli import app

    res = CliRunner().invoke(app, ["--version"])
    assert res.exit_code == 0 and res.output.startswith("cdebt ")
