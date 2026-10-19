from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    # byte-exact content hashes: no CRLF conversion on Windows
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "core.autocrlf",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "core.safecrlf",
    "GIT_CONFIG_VALUE_1": "false",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_ENV, check=True, capture_output=True, text=True
    ).stdout


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        git(path, "init", "-q", "-b", "main")

    def write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg: str, *, all_: bool = True) -> str:
        if all_:
            git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", msg)
        return git(self.path, "rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        return git(self.path, *args)


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[str], Repo]:
    def _mk(name: str = "r") -> Repo:
        d = tmp_path / name
        d.mkdir()
        return Repo(d)

    return _mk


@pytest.fixture
def gitai_repo(tmp_path: Path) -> Path:
    """Clone of the REAL git-ai 1.7.5 scratch repo (bundle) with refs/notes/ai fetched."""
    dst = tmp_path / "gitai"
    subprocess.run(
        ["git", "clone", "-q", str(FIX / "gitai" / "repo.bundle"), str(dst)], check=True, env=_ENV
    )
    git(dst, "fetch", "-q", str(FIX / "gitai" / "repo.bundle"), "refs/notes/ai:refs/notes/ai")
    return dst


@pytest.fixture(autouse=True)
def _trusted_fixture_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test repos are throwaway fixtures we wrote: run them with the host sandbox so the suite
    does not depend on Docker. tests/test_trust_policy.py exercises the untrusted default."""
    monkeypatch.setenv("CDEBT_TRUSTED", "1")


# Windows: execution of probes is unsupported (docs/LIMITS.md); skip the tests that need the POSIX
# sandbox so CI proves everything else works there.
_NEEDS_POSIX = {
    "test_sandbox",
    "test_sandbox_docker",
    "test_sandbox_kill",
    "test_sandbox_node",
    "test_sandbox_fuzz",
    "test_sandbox_node_fuzz",
    "test_sandbox_redteam",
    "test_probes",
    "test_js_probes",
    "test_attest",
    "test_coverage",
    "test_study_run",
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    import sys

    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason="needs the POSIX sandbox; Windows is unsupported for execution")
    for it in items:
        if it.module.__name__.split(".")[-1] in _NEEDS_POSIX or it.get_closest_marker(
            "posix_sandbox"
        ):
            it.add_marker(skip)
