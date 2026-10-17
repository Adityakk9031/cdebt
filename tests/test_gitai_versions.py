"""Real notes captured from git-ai 1.0.0 .. 1.7.5 (scripts/capture_gitai_versions.sh)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cdebt.attribution import resolve_file
from cdebt.attribution.gitai import GitAiProvider, parse_note
from cdebt.models import AttributionSource as S
from cdebt.models import AuthorKind as K
from conftest import FIX, git

V = FIX / "gitai_versions"
TAGS = sorted(
    (d.name for d in V.iterdir() if (d / "note_c2.txt").exists()),
    key=lambda t: tuple(int(x) for x in t[1:].split(".")),
)
# (layout, human lines recorded?)  -- from the captured data; see docs/GITAI_FORMATS.md
EXPECT = {
    "v1.0.0": "prompts",
    "v1.1.1": "prompts",
    "v1.2.1": "prompts",
    "v1.3.0": "prompts",
    "v1.4.0": "sessions",
    "v1.5.0": "sessions",
    "v1.6.0": "sessions",
    "v1.7.0": "sessions",
}


def test_all_expected_versions_were_captured() -> None:
    assert list(EXPECT) == TAGS


@pytest.mark.parametrize("tag", TAGS)
def test_real_note_layout_and_resolution(tag: str, tmp_path: Path) -> None:
    note = parse_note((V / tag / "note_c2.txt").read_text())
    assert note.supported and note.layout == EXPECT[tag]
    assert note.schema_version == "authorship/3.0.0"  # constant across ALL these versions
    assert note.files["a.py"][0][1] == [(3, 7)]
    dst = tmp_path / "r"
    subprocess.run(["git", "clone", "-q", str(V / tag / "repo.bundle"), str(dst)], check=True)
    git(dst, "fetch", "-q", str(V / tag / "repo.bundle"), "refs/notes/ai:refs/notes/ai")
    fa = resolve_file(dst, "a.py", "HEAD", [GitAiProvider(dst)])
    assert fa.kinds[2:7] == [K.AI] * 5 and fa.source is S.GIT_AI
    # Lines the human wrote before the AI edit: only >=1.7 records them as human; older
    # versions leave them unattributed, which cdebt must report as UNKNOWN (never "human").
    expected_head = K.HUMAN if tag == "v1.7.0" else K.UNKNOWN
    assert fa.kinds[:2] == [expected_head] * 2


@pytest.mark.parametrize("tag", TAGS)
def test_real_empty_attestation_notes_parse(tag: str) -> None:
    n = parse_note((V / tag / "note_c1.txt").read_text())
    assert n.supported and not n.malformed
    if tag != "v1.7.0":
        assert n.files == {} and n.layout == "empty"  # starts with a bare `---` line


def test_current_format_1_7_5_fixture_still_parses() -> None:
    n = parse_note((FIX / "gitai" / "note_c3.txt").read_text())
    assert n.layout == "sessions" and n.files["a.py"][0][0].startswith("h_")


def test_unsupported_schema_yields_no_claims() -> None:
    n = parse_note('a.py\n  s_x::t_y 1-3\n---\n{"schema_version": "authorship/9.0.0"}')
    assert not n.supported  # parsed, but cdebt will not interpret it


def test_malformed_note_degrades_to_no_claims() -> None:
    n = parse_note('a.py\n  s_x::t_y 1-three\n---\n{"schema_version": "authorship/3.0.0"}')
    assert n.malformed and not n.supported
    assert parse_note("garbage with no divider").files == {"garbage with no divider": []}
    assert not parse_note("garbage with no divider").supported


def test_unsupported_note_does_not_make_ai_claims(gitai_repo: Path) -> None:
    from cdebt.attribution import gitai

    prov = GitAiProvider(gitai_repo)
    sha = git(gitai_repo, "rev-parse", "HEAD~1").strip()  # the real AI commit (c2)
    note = parse_note((FIX / "gitai" / "note_c2.txt").read_text())
    note.meta["schema_version"] = "authorship/9.0.0"
    prov._cache[sha] = note
    fa = resolve_file(gitai_repo, "a.py", "HEAD", [prov])
    assert fa.ai_lines == 0 and gitai.SUPPORTED_SCHEMA_PREFIX == "authorship/3."
