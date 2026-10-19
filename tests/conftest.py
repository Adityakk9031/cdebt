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
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
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
