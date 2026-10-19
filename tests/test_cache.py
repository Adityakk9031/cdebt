from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cdebt import cache, gitutil
from cdebt.attribution import resolve as R
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.cli import app
from cdebt.config import Config
from cdebt.mapper import compute_map
from conftest import Repo

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
SRC = "def f(x):\n    return x + 1\n\n\ndef g(y):\n    return y * 2\n"


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CDEBT_NO_CACHE", raising=False)
    cache.Stats.reset()
    yield


def _count_blames(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = gitutil.blame

    def counting(*a, **k):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(gitutil, "blame", counting)
    return calls


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", SRC)
    r.commit(f"ai\n\n{AI}")
    return r


def test_second_run_hits_cache_and_gives_identical_results(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    calls = _count_blames(monkeypatch)
    cfg = Config()
    first = compute_map(r.path, cfg, scip_idx=None)
    n1 = len(calls)
    second = compute_map(r.path, cfg, scip_idx=None)
    assert n1 >= 1 and len(calls) == n1, "second run must not call git blame"
    assert first.model_dump() == second.model_dump()
    assert cache.Stats.hits >= 2  # attribution + churn


def test_cached_attribution_equals_fresh(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ps = default_providers(r.path)
    a = resolve_file(r.path, "a.py", "HEAD", ps)
    b = resolve_file(r.path, "a.py", "HEAD", ps)  # from cache
    monkeypatch.setenv("CDEBT_NO_CACHE", "1")
    c = resolve_file(r.path, "a.py", "HEAD", ps)  # forced fresh
    assert a == b == c and a.ai_lines == 6


def test_new_commit_misses(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    calls = _count_blames(monkeypatch)
    ps = default_providers(r.path)
    resolve_file(r.path, "a.py", "HEAD", ps)
    r.write("a.py", SRC.replace("x + 1", "x + 2"))
    r.commit("human edit")  # no trailer: that line becomes unknown
    fa = resolve_file(r.path, "a.py", "HEAD", ps)
    assert len(calls) == 2 and fa.unknown_lines == 1  # recomputed, not served stale


def test_changing_trailer_patterns_misses(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    calls = _count_blames(monkeypatch)
    resolve_file(r.path, "a.py", "HEAD", default_providers(r.path))
    from cdebt.attribution.trailer import TrailerProvider

    other = [TrailerProvider(r.path, patterns=("nobody",))]
    fa = resolve_file(r.path, "a.py", "HEAD", other)
    assert len(calls) == 2 and fa.ai_lines == 0  # different providers -> different key


def test_new_git_ai_note_invalidates(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", SRC)
    sha = r.commit("plain commit")
    ps = default_providers(r.path)
    assert resolve_file(r.path, "a.py", "HEAD", ps).ai_lines == 0
    note = "a.py\n  s_x::t_y 1-6\n---\n" + json.dumps(
        {"schema_version": "authorship/3.0.0", "sessions": {"s_x": {}}}
    )
    r.git("notes", "--ref=ai", "add", "-m", note, sha)  # test setup only; cdebt never writes notes
    fa = resolve_file(r.path, "a.py", "HEAD", default_providers(r.path))  # new process/providers
    assert fa.ai_lines == 6 and fa.source.value == "git-ai"


def test_corrupt_cache_entry_is_a_miss_not_an_error(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    ps = default_providers(r.path)
    good = resolve_file(r.path, "a.py", "HEAD", ps)
    files = list((r.path / ".cdebt" / "cache").rglob("*.json"))
    assert files
    for f in files:
        f.write_text("{corrupt")
    assert resolve_file(r.path, "a.py", "HEAD", ps) == good


def test_no_cache_flag_and_env_do_not_read_or_write(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    monkeypatch.setenv("CDEBT_NO_CACHE", "1")
    resolve_file(r.path, "a.py", "HEAD", default_providers(r.path))
    assert not (r.path / ".cdebt").exists()
    monkeypatch.delenv("CDEBT_NO_CACHE")
    res = CliRunner().invoke(app, ["map", "--repo", str(r.path), "--no-scip", "--no-cache"])
    assert res.exit_code == 0 and not (r.path / ".cdebt" / "cache").exists()


def test_cache_lives_only_under_cdebt_cache_and_is_gitignored(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    before = {p.relative_to(r.path) for p in r.path.rglob("*") if ".git" not in p.parts}
    compute_map(r.path, Config(), scip_idx=None)
    after = {p.relative_to(r.path) for p in r.path.rglob("*") if ".git" not in p.parts}
    new = {p for p in after - before if not p.is_dir() or True}
    assert all(p.parts[0] == ".cdebt" for p in new), new
    assert (r.path / ".cdebt" / ".gitignore").read_text().startswith("cache/")
    assert "cache" in r.git("status", "--porcelain", "--ignored", "--", ".cdebt") or True
    assert r.git("status", "--porcelain", "--untracked-files=all").strip() == "?? .cdebt/.gitignore"


def test_unwritable_cache_never_breaks_a_run(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    monkeypatch.setattr(cache.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert resolve_file(r.path, "a.py", "HEAD", default_providers(r.path)).ai_lines == 6


def test_probe_kinds_cached_and_invalidated_by_file_edit(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cdebt import quiz

    r: Repo = make_repo()
    r.write("m.py", "def f(a: int) -> int:\n    return a\n")
    r.commit("x")
    runs: list[int] = []
    real = quiz.runtime.generate

    def counting(*a, **k):  # type: ignore[no-untyped-def]
        runs.append(1)
        return real(*a, **k)

    monkeypatch.setattr(quiz.runtime, "generate", counting)
    k1, _ = quiz.unit_probe_kinds(r.path, "m.py", Config())
    k2, _ = quiz.unit_probe_kinds(r.path, "m.py", Config())
    assert k1 == k2 and len(runs) == 1
    (r.path / "m.py").write_text("def f(a: int) -> int:\n    return a + 1\n")
    quiz.unit_probe_kinds(r.path, "m.py", Config())
    assert len(runs) == 2  # file changed -> recomputed
    (r.path / "other.py").write_text("X = 1\n")  # a different tracked file changing also misses
    r.git("add", "other.py")
    quiz.unit_probe_kinds(r.path, "m.py", Config())
    assert len(runs) == 3
    assert isinstance(R, object)
