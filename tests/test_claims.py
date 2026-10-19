from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_claims", Path(__file__).parent.parent / "scripts" / "check_claims.py"
)
assert SPEC and SPEC.loader
cc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cc)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "res.md").write_text("risk ratio 0.997 over 167,163 units; 50% done\n")
    (tmp_path / "docs" / "res.json").write_text(json.dumps({"share": 0.332, "n": 25, "ok": True}))
    return tmp_path


def run(root: Path, md: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    f = root / "post.md"
    f.write_text(md)
    rc = cc.check([f])
    return rc, capsys.readouterr().out


def test_cited_numbers_pass_including_rounding_and_percent(root, capsys) -> None:  # type: ignore[no-untyped-def]
    rc, out = run(
        root,
        "The ratio was 0.997 [src: docs/res.md] over 167,163 units.\n\n"
        "About 33% of lines [src: docs/res.json] in 25 files.\n",
        capsys,
    )
    assert rc == 0 and "0 problem" in out
    rc, _ = run(root, "Ratio 1.0 [src: docs/res.md]\n", capsys)  # 0.997 rounds to 1.0
    assert rc == 0


def test_missing_citation_fails(root, capsys) -> None:  # type: ignore[no-untyped-def]
    rc, out = run(root, "We found 42 problems.\n", capsys)
    assert rc == 1 and "no [src: ...]" in out and "42" in out


def test_wrong_number_fails(root, capsys) -> None:  # type: ignore[no-untyped-def]
    rc, out = run(root, "The ratio was 0.5 [src: docs/res.md]\n", capsys)
    assert rc == 1 and "0.5 not found" in out
    rc, out = run(root, "Share was 34% [src: docs/res.json]\n", capsys)
    assert rc == 1


def test_missing_cited_file_fails(root, capsys) -> None:  # type: ignore[no-untyped-def]
    rc, out = run(root, "The ratio was 0.997 [src: docs/nope.md]\n", capsys)
    assert rc == 1 and "does not exist" in out


def test_citation_scope_is_the_block(root, capsys) -> None:  # type: ignore[no-untyped-def]
    md = "- first 0.997 [src: docs/res.md]\n- second 25 with no cite\n\n| a | 33% [src: docs/res.json] |\n| b | 7 |\n"
    rc, out = run(root, md, capsys)
    assert rc == 1 and out.count("FAIL") == 2 and "post.md:2" in out and "post.md:5" in out


def test_non_claims_are_not_checked(root, capsys) -> None:  # type: ignore[no-untyped-def]
    md = (
        "# 3. Heading number\n\nVersion 0.1.2 and Python 3.11, Node 24, git-ai 1.7.x, released 2026-10-19.\n\n"
        "Run `cdebt map --top 20` and see [link](http://x.com/20).\n\n"
        "```\nlots of 12345 numbers\n```\n\n<!-- 99 hidden -->\n\n1. numbered item without numbers\n"
    )
    rc, out = run(root, md, capsys)
    assert rc == 0, out


def test_ignore_marker_is_reported_not_silent(root, capsys) -> None:  # type: ignore[no-untyped-def]
    rc, out = run(root, "There are 12 flags <!-- claims:ignore -->\n", capsys)
    assert rc == 0 and "1 ignored" in out


def test_the_real_documents_pass() -> None:
    """The shipped README and launch post must satisfy their own audit."""
    base = Path(__file__).parent.parent
    files = [base / "README.md", base / "docs" / "LAUNCH_POST.md"]
    assert cc.check(files) == 0
