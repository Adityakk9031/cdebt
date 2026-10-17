from __future__ import annotations

import json
from pathlib import Path

from cdebt.attribution import resolve_file
from cdebt.attribution.agent_trace import AgentTraceProvider
from cdebt.attribution.gitai import GitAiProvider, parse_note
from cdebt.attribution.resolve import default_providers
from cdebt.attribution.trailer import TrailerProvider, is_ai_trailer
from cdebt.gitutil import GitError, run
from cdebt.models import AttributionSource as S
from cdebt.models import AuthorKind as K
from conftest import FIX, Repo, git

G = FIX / "gitai"


def test_parse_real_notes() -> None:
    n = parse_note((G / "note_c2.txt").read_text())
    assert n.schema_version == "authorship/3.0.0"
    assert n.files["a.py"][0][1] == [(3, 7)]
    assert n.files["b.py"][0][1] == [(2, 3), (6, 6)]  # "2-3,6"
    assert n.files["a.py"][0][0].startswith("s_")
    h = parse_note((G / "note_c1.txt").read_text())
    assert h.files["b.py"][0][0].startswith("h_")


def test_real_gitai_resolution(gitai_repo: Path) -> None:
    fa = resolve_file(gitai_repo, "a.py", "HEAD", [GitAiProvider(gitai_repo)])
    # final_a.py: 1-2 human(c1), 3 AI, 4 human edit(c3), 5-7 AI, 8 human(c3)
    assert fa.kinds == [K.HUMAN, K.HUMAN, K.AI, K.HUMAN, K.AI, K.AI, K.AI, K.HUMAN]
    assert fa.source is S.GIT_AI and fa.ai_lines == 4
    fb = resolve_file(gitai_repo, "b.py", "HEAD", [GitAiProvider(gitai_repo)])
    assert [k.value for k in fb.kinds] == ["human", "ai", "ai", "human", "human", "ai"]


def test_real_gitai_matches_git_ai_blame(gitai_repo: Path) -> None:
    """Cross-check against git-ai's own blame output captured from the real tool."""
    fa = resolve_file(gitai_repo, "a.py", "HEAD", [GitAiProvider(gitai_repo)])
    for ln, kind in zip((G / "gitai_blame_a.txt").read_text().splitlines(), fa.kinds, strict=True):
        assert ("mock_ai" in ln) == (kind is K.AI)


def test_real_stats_agree(gitai_repo: Path) -> None:
    s = json.loads((G / "stats_c2.json").read_text())
    assert s["ai_additions"] == 5 + 3  # 5 lines in a.py + 3 in b.py
    fa = resolve_file(
        gitai_repo, "a.py", "ae683711269019703335e87e81d7f770d443376c", [GitAiProvider(gitai_repo)]
    )
    assert fa.ai_lines == 5


def test_trailer_source_is_coarse_and_labelled(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("human work")
    r.write("a.py", "x = 1\ny = 2\n")
    r.commit("feat\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    fa = resolve_file(r.path, "a.py", "HEAD", default_providers(r.path))
    assert fa.kinds == [K.UNKNOWN, K.AI]  # un-trailered commit is UNKNOWN, not human
    assert fa.source is S.TRAILER and fa.coarse
    assert is_ai_trailer("x\n\nco-authored-by: GitHub Copilot <c@x>")
    assert not is_ai_trailer("x\n\nCo-Authored-By: Alice <alice@example.com>")


def test_none_source_is_unknown(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "x = 1\n")
    r.commit("plain")
    fa = resolve_file(r.path, "a.py", "HEAD", default_providers(r.path))
    assert fa.kinds == [K.UNKNOWN] and fa.source is S.NONE and fa.human_lines == 0


def test_agent_trace_source(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", "a = 1\nb = 2\nc = 3\n")
    sha = r.commit("add")
    rec = {
        "version": "0.1.0",
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "timestamp": "2026-01-23T14:30:00Z",
        "vcs": {"type": "git", "revision": sha},
        "files": [
            {
                "path": "a.py",
                "conversations": [
                    {
                        "contributor": {"type": "ai", "model_id": "anthropic/claude"},
                        "ranges": [
                            {"start_line": 2, "end_line": 3},
                            {"start_line": 3, "end_line": 3, "contributor": {"type": "human"}},
                        ],
                    }
                ],
            }
        ],
    }
    (r.path / ".agent-trace").mkdir()
    (r.path / ".agent-trace" / "t.json").write_text(json.dumps(rec))
    fa = resolve_file(r.path, "a.py", "HEAD", [AgentTraceProvider(r.path)])
    assert fa.kinds == [K.UNKNOWN, K.AI, K.HUMAN] and fa.source is S.AGENT_TRACE


def test_priority_gitai_beats_trailer(gitai_repo: Path) -> None:
    git(gitai_repo, "commit", "-q", "--allow-empty", "-m", "x\n\nCo-Authored-By: Claude <a@b>")
    fa = resolve_file(gitai_repo, "a.py", "HEAD", default_providers(gitai_repo))
    assert fa.source_counts()["git-ai"] == 8  # every line claimed by git-ai, none by trailer
    assert TrailerProvider  # imported


def test_gitutil_refuses_writes(tmp_path: Path) -> None:
    import pytest

    for bad in (
        ["commit", "-m", "x"],
        ["notes", "add", "-m", "x"],
        ["config", "user.name", "x"],
        ["checkout", "main"],
        ["update-ref", "-d", "x"],
    ):
        with pytest.raises(GitError):
            run(bad, tmp_path)
