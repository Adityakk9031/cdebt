from __future__ import annotations

import json

from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.config import Config
from cdebt.gate import run_gate, to_markdown
from cdebt.models import Record
from cdebt.plan import compute_plan
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import Repo

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
BASE = "def f(x):\n    return x + 1\n\n\ndef g(y):\n    return y * 2\n"


def _pr(make_repo, trailer: bool = True) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", BASE)
    r.commit("base")
    r.git("branch", "base")
    r.write("a.py", BASE.replace("x + 1", "x + 2"))
    r.commit("change f" + (f"\n\n{AI}" if trailer else ""))
    return r


def test_gate_fails_on_ai_change_in_unverified_unit(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _pr(make_repo)
    res = run_gate(r.path, Config(), "base")
    assert not res.passed and res.ai_lines_unverified == 1
    v = res.violations[0]
    assert (v.path, v.symbol, v.status, v.attribution_source.value) == (
        "a.py",
        "f",
        "none",
        "trailer",
    )
    md = to_markdown(res)
    assert "FAIL" in md and "`f`" in md and "cdebt quiz a.py" in md and "Dev" not in md


def test_gate_passes_after_understanding_committed_and_fails_again_on_edit(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _pr(make_repo)
    text = (r.path / "a.py").read_text()
    f = {u.symbol: u for u in extract_units("a.py", text)}["f"]
    st = Store(r.path)
    st.add(Record(unit_path="a.py", unit_symbol="f", content_hash=f.content_hash, passed=True))
    st.save()
    r.commit("record understanding")
    assert run_gate(r.path, Config(), "base").passed
    r.write("a.py", text.replace("x + 2", "x + 3"))
    r.commit(f"edit again\n\n{AI}")
    res = run_gate(r.path, Config(), "base")
    assert not res.passed and res.violations[0].status == "stale"  # expired by the edit


def test_gate_max_ai_lines(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _pr(make_repo)
    assert run_gate(r.path, Config(), "base", max_ai_lines=1).passed
    assert not run_gate(r.path, Config(), "base", max_ai_lines=0).passed


def test_unknown_attribution_warns_but_strict_fails(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _pr(make_repo, trailer=False)
    res = run_gate(r.path, Config(), "base")
    assert res.passed and res.unknown_changed_lines == 1 and res.unknown_files == ["a.py"]
    assert any("unknown" in n.lower() for n in res.notes)
    strict = run_gate(r.path, Config(), "base", strict_unknown=True)
    assert not strict.passed


def test_cli_gate_exit_codes_and_json(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _pr(make_repo)
    run = CliRunner()
    out = run.invoke(app, ["gate", "--repo", str(r.path), "--base", "base", "--json"])
    assert out.exit_code == 1 and json.loads(out.output)["passed"] is False
    md = r.path / "c.md"
    out = run.invoke(
        app,
        [
            "gate",
            "--repo",
            str(r.path),
            "--base",
            "base",
            "--max-ai-lines",
            "5",
            "--markdown",
            str(md),
        ],
    )
    assert out.exit_code == 0 and "PASS" in md.read_text()
    out = run.invoke(app, ["gate", "--repo", str(r.path), "--base", "nope"])
    assert out.exit_code == 2


def test_plan_ranks_by_priority(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    big = "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(30))
    r.write("big.py", big)
    r.write("small.py", "def s(x):\n    return x\n")
    r.commit(f"ai\n\n{AI}")
    items = compute_plan(r.path, Config(), scip_idx=None)
    assert [i.priority for i in items] == sorted((i.priority for i in items), reverse=True)
    assert {i.path for i in items} == {"small.py", "big.py"}
    assert all(
        i.action in {"quiz", "review", "rewrite-or-delete"} and i.effort_min >= 1 for i in items
    )
    out = CliRunner().invoke(app, ["plan", "--repo", str(r.path), "--no-scip", "--json"])
    assert out.exit_code == 0 and json.loads(out.output)[0]["rank"] == 1
    assert not (r.path / ".cdebt" / "understanding.json").exists()  # only a cache may appear
