from __future__ import annotations

import json
import math

from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.config import Config
from cdebt.mapper import blast_factor, compute_map, scip_blast
from cdebt.models import Record
from cdebt.scip import load_index
from cdebt.stats import wilson
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import FIX, Repo

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
SRC = "def f(x):\n    return x + 1\n\n\ndef g(y):\n    return y * 2\n"


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", SRC)
    r.commit(f"add a\n\n{AI}")
    r.write("b.py", "from a import f\n\nprint(f(1))\n")
    r.commit("add b")
    return r


def test_wilson() -> None:
    lo, hi = wilson(5, 10)
    assert 0.23 < lo < 0.24 and 0.76 < hi < 0.77  # known Wilson 95% for 5/10
    assert wilson(0, 0) == (0.0, 1.0)


def test_scip_reader_on_real_index() -> None:
    idx = load_index(FIX / "scip" / "index.scip")
    assert (
        scip_blast(idx, "pkg/lib.py") == 3
    )  # app.py lines 1 (import), 5 and 9 (calls), deduped by (file, line)
    assert scip_blast(idx, "pkg/app.py") == 0


def test_blast_factor() -> None:
    assert blast_factor(0, "log") == 1.0
    assert math.isclose(blast_factor(2, "log"), 1 + math.log(3))
    assert blast_factor(0, "raw") == 1.0 and blast_factor(7, "raw") == 7.0


def test_map_hand_computed(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    cfg = Config(w_churn=1.0)
    res = compute_map(r.path, cfg, scip_idx=None)
    a = next(f for f in res.files if f.path == "a.py")
    # a.py: 6/6 AI lines (trailer), nothing understood, churn = 1/1 -> (1+1); blast approx:
    # names f,g(len<=2 dropped) -> "f" has len 1 so excluded -> 0 refs -> 1.0
    assert a.ai_lines == 6 and a.understood_share == 0 and a.attribution_source.value == "trailer"
    assert math.isclose(a.score, 1.0 * 1.0 * 2.0 * 1.0)
    b = next(f for f in res.files if f.path == "b.py")
    assert b.ai_lines == 0 and b.score == 0 and b.unknown_lines == 3  # unknown, not human
    assert res.summary.ai_share == 6 / 9 and res.summary.coarse_attribution


def test_understanding_reduces_score_and_expires(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    st = Store(r.path)
    u = {x.symbol: x for x in extract_units("a.py", SRC)}
    st.add(Record(unit_path="a.py", unit_symbol="f", content_hash=u["f"].content_hash, passed=True))
    st.save()
    a = next(f for f in compute_map(r.path, Config(), scip_idx=None).files if f.path == "a.py")
    assert a.understood_ai_lines == 2 and a.understood_share == 2 / 6
    r.write("a.py", SRC.replace("x + 1", "x + 9"))  # edit f -> understanding expires
    r.commit(f"edit\n\n{AI}")
    a2 = next(f for f in compute_map(r.path, Config(), scip_idx=None).files if f.path == "a.py")
    assert a2.understood_ai_lines == 0


def test_unknown_as_ai_flag(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    res = compute_map(r.path, Config(unknown_as_ai=True), scip_idx=None)
    assert next(f for f in res.files if f.path == "b.py").ai_lines == 3


def test_cli_map_json_has_no_people(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    out = CliRunner().invoke(app, ["map", "--repo", str(r.path), "--json", "--no-scip"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert set(data) == {"summary", "files"} and "Dev" not in out.output
    assert data["summary"]["attribution_sources"] == {"trailer": 6}


def test_cli_map_table(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    out = CliRunner().invoke(app, ["map", "--repo", str(r.path), "--no-scip"])
    assert out.exit_code == 0 and "a.py" in out.output and "trailer" in out.output
    assert not (r.path / ".cdebt" / "understanding.json").exists()  # map never records anything
