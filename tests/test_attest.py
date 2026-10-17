from __future__ import annotations

import json
import math

from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.config import Config
from cdebt.gate import run_gate, to_markdown
from cdebt.mapper import compute_map
from cdebt.models import Record
from cdebt.probes import constants
from cdebt.quiz import attest, build_quiz, record_results, unit_probe_kinds
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import Repo

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
SRC = """import os

LIMITS = [10, 20, 30]
TOTAL = sum(LIMITS) * 2
NAME = "svc"
GREETING = NAME.upper() + "!"
HOME = os.environ.get("HOME", "x")


def typed(a: int, b: int) -> int:
    return a + b


def untyped(x):
    return x * 2
"""


def _repo(make_repo, extra: str = "") -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", "X = 1\n")
    r.commit("base")
    r.git("branch", "base")
    r.write("m.py", SRC + extra)
    r.commit(f"ai\n\n{AI}")
    return r


def test_constant_probes_answers_from_execution(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    units = extract_units("m.py", SRC)
    probes, skips = constants.generate(r.path, "m.py", SRC, units, seed=0, per_unit=5)
    assert probes and all(p.kind == "constant" and p.symbol == "<module>" for p in probes)
    ns: dict[str, object] = {}
    exec(compile(SRC, "m", "exec"), ns)  # noqa: S102 (trusted fixture)
    names = {p.evidence["name"] for p in probes}
    assert {"TOTAL", "GREETING"} <= names  # computed constants first
    assert "HOME" not in names  # environment-dependent: never asked
    for p in probes:
        assert p.answer == repr(ns[str(p.evidence["name"])]) and p.answer in p.options
        assert p.evidence["evaluations"] == 2 and p.verified_by == "sandbox-exec"


def test_constant_probes_skip_unsafe_modules(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    src = "X = open('f.txt', 'w')\nY = 1 + 1\n"
    r.write("m.py", src)
    r.commit("x")
    probes, skips = constants.generate(r.path, "m.py", src, extract_units("m.py", src), 0)
    assert probes == [] and "module-level" in skips[0] and not (r.path / "f.txt").exists()


def test_module_unit_verified_by_constants(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    cfg = Config()
    probes, units, _ = build_quiz(r.path, "m.py", cfg, n=10, kinds=("constant",))
    recs = record_results(r.path, "m.py", cfg, units, probes, {p.id: p.answer for p in probes})
    assert [x.unit_symbol for x in recs] == ["<module>"] and recs[0].passed
    assert Store(r.path).status(units[-1] if units[-1].symbol == "<module>" else units[0]) in (
        "verified",
    )


def test_attest_scopes_and_refusal(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    cfg = Config()
    avail, _ = unit_probe_kinds(r.path, "m.py", cfg)
    assert "runtime" in avail["typed"] and "untyped" not in avail
    recs, notes = attest(r.path, "m.py", cfg, "read it, looks fine")
    by = {x.unit_symbol: x for x in recs}
    assert by["<module>"].scope == "module" and by["untyped"].scope == "function-unquizzable"
    assert "typed" not in by and any("REFUSED" in n and "typed" in n for n in notes)
    assert all(x.kind == "attested" and x.note == "read it, looks fine" for x in recs)
    forced, _ = attest(r.path, "m.py", cfg, "forced", symbols=["typed"], force=True)
    assert forced[0].scope == "function-quizzable"


def test_attest_requires_note_and_known_units(make_repo) -> None:  # type: ignore[no-untyped-def]
    import pytest

    r = _repo(make_repo)
    with pytest.raises(ValueError):
        attest(r.path, "m.py", Config(), "   ")
    with pytest.raises(ValueError):
        attest(r.path, "m.py", Config(), "ok", symbols=["nope"])


def test_attestation_is_hash_bound(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    attest(r.path, "m.py", Config(), "ok", symbols=["<module>"])
    st = Store(r.path)
    u = {x.symbol: x for x in extract_units("m.py", SRC)}["<module>"]
    assert st.status(u) == "attested"
    edited = {x.symbol: x for x in extract_units("m.py", SRC.replace("[10, 20, 30]", "[1]"))}
    assert Store(r.path).status(edited["<module>"]) == "stale"


def test_map_weights_attested_less_than_verified(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    cfg = Config()  # attested_weight 0.5
    f0 = compute_map(r.path, cfg, scip_idx=None).files[0]
    attest(r.path, "m.py", cfg, "ok", symbols=["<module>"])
    f1 = compute_map(r.path, cfg, scip_idx=None).files[0]
    mod_lines = len(next(u for u in extract_units("m.py", SRC) if u.symbol == "<module>").lines())
    assert f1.attested_ai_lines == mod_lines and f1.understood_ai_lines == 0
    assert math.isclose(f1.understood_share, 0.5 * mod_lines / f1.ai_lines)
    assert f1.score < f0.score
    # same unit VERIFIED counts fully, so the score drops further
    st = Store(r.path)
    u = next(x for x in extract_units("m.py", SRC) if x.symbol == "<module>")
    st.add(
        Record(unit_path="m.py", unit_symbol="<module>", content_hash=u.content_hash, passed=True)
    )
    st.save()
    f2 = compute_map(r.path, cfg, scip_idx=None).files[0]
    assert f2.understood_ai_lines == mod_lines and f2.score < f1.score
    cfg0 = Config(attested_weight=0.0)
    r2 = compute_map(r.path, cfg0, scip_idx=None).files[0]
    assert r2.understood_share == f2.understood_share  # verified record wins over attested


def test_gate_policies(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    cfg = Config()
    res = run_gate(r.path, cfg, "base")
    assert not res.passed and {v.symbol for v in res.violations} >= {"<module>", "typed", "untyped"}
    attest(r.path, "m.py", cfg, "signed off", label="x")
    # default: module-level + unquizzable function accepted, quizzable `typed` still failing
    res = run_gate(r.path, cfg, "base")
    assert [v.symbol for v in res.violations] == ["typed"] and not res.passed
    assert {v.symbol for v in res.attested_accepted} == {"<module>", "untyped"}
    assert res.attested_accepted_lines > 0
    md = to_markdown(res)
    assert "NON-VERIFIED" in md and "Accepted on attestation" in md
    # --require verified: nothing attested is accepted
    res_v = run_gate(r.path, cfg, "base", require="verified")
    assert {v.symbol for v in res_v.violations} >= {"<module>", "untyped", "typed"}
    assert res_v.attested_accepted == [] and any(v.status == "attested" for v in res_v.violations)
    # --require attested: forced attestation of the quizzable function is accepted too
    attest(r.path, "m.py", cfg, "forced", symbols=["typed"], force=True)
    assert not run_gate(r.path, cfg, "base").passed  # default still rejects function-quizzable
    res_a = run_gate(r.path, cfg, "base", require="attested")
    assert res_a.passed and res_a.ai_lines_unverified == 0


def test_gate_edit_expires_attestation(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    attest(r.path, "m.py", Config(), "ok", symbols=["<module>", "untyped"])
    r.commit("record")
    r.write("m.py", (r.path / "m.py").read_text().replace("[10, 20, 30]", "[10, 20, 31]"))
    r.commit(f"edit\n\n{AI}")
    res = run_gate(r.path, Config(), "base", require="attested")
    assert any(v.symbol == "<module>" and v.status == "stale" for v in res.violations)


def test_cli_attest_map_and_gate(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    monkeypatch.chdir(r.path)
    run = CliRunner()
    bad = run.invoke(app, ["attest", "m.py", "--repo", str(r.path), "--note", " "])
    assert bad.exit_code == 2
    out = run.invoke(app, ["attest", "m.py", "--repo", str(r.path), "--note", "read it"])
    assert out.exit_code == 0 and "NON-VERIFIED" in out.output and "REFUSED" in out.output
    m = run.invoke(app, ["map", "--repo", str(r.path), "--no-scip"])
    assert "NON-VERIFIED" in m.output
    j = json.loads(run.invoke(app, ["map", "--repo", str(r.path), "--no-scip", "--json"]).output)
    assert j["summary"]["attested_ai_lines"] > 0 and j["summary"]["units_attested"] >= 1
    g = run.invoke(app, ["gate", "--repo", str(r.path), "--base", "base", "--require", "bogus"])
    assert g.exit_code == 2
    g = run.invoke(app, ["gate", "--repo", str(r.path), "--base", "base", "--json"])
    d = json.loads(g.output)
    assert d["require"] == "default" and d["attested_accepted_lines"] > 0
