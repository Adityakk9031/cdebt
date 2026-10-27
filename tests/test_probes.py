from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.config import Config
from cdebt.models import Probe
from cdebt.probes import callgraph, mutation, runtime
from cdebt.probes.base import grade
from cdebt.quiz import build_quiz, record_results
from cdebt.scip import fingerprint, index_path, load_index, meta_path
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import FIX, Repo

MATHY = """def add(a: int, b: int) -> int:
    return a + b


def shout(s: str, times: int) -> str:
    return (s.upper() + "!") * (times if times > 0 else 1)


def first(xs: list[int]) -> int:
    return xs[0]


def untyped(x):
    return x
"""
TEST = """from pkg.mathy import add, shout

def test_add():
    assert add(2, 3) == 5

def test_shout_pos():
    assert shout("a", 2) == "A!A!"

def test_shout_one():
    assert shout("a", 0) == "A!"
"""


def _project(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/mathy.py", MATHY)
    r.write("tests/test_mathy.py", TEST)
    r.commit("init")
    return r


def test_runtime_answers_come_from_execution(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    units = extract_units("pkg/mathy.py", MATHY)
    probes, skips = runtime.generate(r.path, "pkg/mathy.py", MATHY, units, seed=1, per_unit=2)
    assert probes and all(p.verified_by == "sandbox-exec" for p in probes)
    ns: dict[str, object] = {}
    exec(MATHY, ns)  # noqa: S102 (trusted test fixture)
    for p in probes:
        call = p.question.split("`")[1]
        try:
            truth = repr(eval(call, ns))  # noqa: S307
        except Exception as e:  # noqa: BLE001
            truth = f"raises {type(e).__name__}"
        assert p.answer == truth and p.answer in p.options
        assert len(set(p.options)) == len(p.options) and p.evidence["deterministic_runs"] == 2
    assert any("untyped" in s for s in skips)


def test_runtime_is_seeded(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    u = extract_units("pkg/mathy.py", MATHY)
    a, _ = runtime.generate(r.path, "pkg/mathy.py", MATHY, u, seed=7)
    b, _ = runtime.generate(r.path, "pkg/mathy.py", MATHY, u, seed=7)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_runtime_skips_side_effecting_functions(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    src = (
        "import random\nimport os\n\n"
        "def roll(n: int) -> int:\n    return random.randint(0, n)\n\n"
        "def listing(n: int) -> int:\n    return len(os.listdir('.')) + n\n\n"
        "def writer(n: int) -> int:\n    open('x', 'w')\n    return n\n\n"
        "def pure(n: int) -> int:\n    return n * 2\n"
    )
    r.write("m.py", src)
    r.commit("x")
    probes, skips = runtime.generate(r.path, "m.py", src, extract_units("m.py", src), seed=0)
    # `roll` is quizzable now: random is seeded in the sandbox (the question says so)
    assert {p.symbol for p in probes} == {"pure", "roll"}
    assert all("random is seeded" in p.question for p in probes if p.symbol == "roll")
    text = " | ".join(skips)
    assert "listing" in text and "os" in text
    assert "writer" in text and "open" in text


def test_runtime_skips_module_level_side_effects(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    src = "x = open('f.txt', 'w')\n\ndef f(n: int) -> int:\n    return n\n"
    r.write("m.py", src)
    r.commit("x")
    probes, skips = runtime.generate(r.path, "m.py", src, extract_units("m.py", src), seed=0)
    assert probes == [] and "module-level" in skips[0]
    assert not (r.path / "f.txt").exists()


def test_grading_is_rule_based() -> None:
    p = Probe(
        id="1",
        kind="runtime",
        path="a",
        symbol="f",
        question="q",
        options=["1", "2", "3"],
        answer="2",
        verified_by="sandbox-exec",
    )
    assert grade(p, "B") and grade(p, "2") and grade(p, " 2 ") and not grade(p, "A")
    m = Probe(
        id="2",
        kind="callgraph",
        path="a",
        symbol="f",
        question="q",
        multi=True,
        options=["x.py", "y.py", "z.py", "(none)"],
        answer=json.dumps(["x.py", "z.py"]),
        verified_by="scip-index",
    )
    assert grade(m, "A,C") and grade(m, "c, a") and not grade(m, "A") and not grade(m, "A,B,C")
    n = m.model_copy(update={"answer": json.dumps(["(none)"])})
    assert grade(n, "none") and grade(n, "D")


def _scip_repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    for f in (FIX / "scip" / "pkg").glob("*.py"):
        r.write(f"pkg/{f.name}", f.read_text())
    r.commit("init")
    ip = index_path(r.path)
    ip.parent.mkdir(parents=True)
    shutil.copy(FIX / "scip" / "index.scip", ip)
    meta_path(r.path).write_text(
        json.dumps({"fingerprint": fingerprint(r.path, Config().extensions)})
    )
    return r


def test_callgraph_answers_from_real_scip_index(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _scip_repo(make_repo)
    src = (r.path / "pkg/lib.py").read_text()
    idx = load_index(index_path(r.path))
    probes, _ = callgraph.generate(r.path, "pkg/lib.py", extract_units("pkg/lib.py", src), idx, 3)
    assert probes and all(p.verified_by == "scip-index" and p.multi for p in probes)
    q1 = next(p for p in probes if "need updating" in p.question and p.symbol == "helper")
    assert json.loads(q1.answer) == ["pkg/app.py"]
    q2 = next(p for p in probes if "code units" in p.question and p.symbol == "helper")
    assert "pkg/app.py::main" in json.loads(q2.answer)
    assert set(json.loads(q2.answer)) <= {
        "pkg/app.py::main",
        "pkg/app.py::also",
        "pkg/app.py::<module>",
    }
    for p in probes:  # options always contain the truth plus at least one false candidate
        truth = set(json.loads(p.answer))
        assert truth <= set(p.options) and len(set(p.options) - truth - {"(none)"}) >= 1


def test_stale_index_not_trusted(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _scip_repo(make_repo)
    (r.path / "pkg/lib.py").write_text((r.path / "pkg/lib.py").read_text() + "\n# edit\n")
    probes, units, skips = build_quiz(r.path, "pkg/lib.py", Config(), kinds=("callgraph",))
    assert probes == [] and any("no fresh SCIP index" in s for s in skips)


def test_mutation_probe_computed_by_running_tests(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    units = extract_units("pkg/mathy.py", MATHY)
    probes, skips = mutation.generate(
        r.path, "pkg/mathy.py", MATHY, units, seed=2, per_unit=1, timeout=60
    )
    assert probes, skips
    for p in probes:
        assert p.verified_by == "mutation-run" and p.evidence["baseline_green"] is True
        failed = p.evidence["failed_tests"]
        truth = failed if failed else ["(none)"]
        assert json.loads(p.answer) == sorted(truth)  # type: ignore[type-var]
    # independent check of one mutation: applying `a + b` -> `a - b` must make test_add fail
    add = next(u for u in units if u.symbol == "add")
    site = next(s for s in mutation.sites(MATHY, add) if s.old == "+")
    mutated = mutation.apply(MATHY, site)
    assert mutated and "a - b" in mutated
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(r.path, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        (Path(d) / "pkg/mathy.py").write_text(mutated)
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
            cwd=d,
            capture_output=True,
            text=True,
        )
    assert "test_add" in out.stdout and out.returncode == 1


def test_mutation_sites_skip_signature_and_unary() -> None:
    src = "def f(a: int = 1) -> int:\n    x = -a + 2\n    return x\n"
    u = extract_units("m.py", src)[0]
    olds = [(s.old, s.row) for s in mutation.sites(src, u)]
    assert ("-", 2) not in olds and ("+", 2) in olds and ("2", 2) in olds
    assert all(row >= 2 for _, row in olds)  # the def line (and default `1`) never mutated


def test_mutation_skipped_when_baseline_red(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/mathy.py", MATHY)
    r.write("tests/test_bad.py", "def test_bad():\n    assert False\n")
    r.commit("init")
    probes, skips = mutation.generate(
        r.path, "pkg/mathy.py", MATHY, extract_units("pkg/mathy.py", MATHY), 0, timeout=60
    )
    assert probes == [] and "baseline" in skips[0]


def test_quiz_records_bound_to_hash_and_expire(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    cfg = Config()
    probes, units, _ = build_quiz(r.path, "pkg/mathy.py", cfg, n=4, seed=0, kinds=("runtime",))
    recs = record_results(
        r.path,
        "pkg/mathy.py",
        cfg,
        units,
        probes,
        {p.id: p.answer for p in probes},
        label="ci-audit",
    )
    assert recs and all(x.passed for x in recs)
    st = Store(r.path)
    assert all(st.is_understood(u) for u in units if u.symbol in {x.unit_symbol for x in recs})
    changed = {
        u.symbol: u for u in extract_units("pkg/mathy.py", MATHY.replace("a + b", "a + b + 0"))
    }
    if "add" in {x.unit_symbol for x in recs}:
        assert st.status(changed["add"]) == "stale"


def test_wrong_answers_do_not_pass(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    probes, units, _ = build_quiz(r.path, "pkg/mathy.py", Config(), n=3, kinds=("runtime",))
    wrong = {p.id: next(o for o in p.options if o != p.answer) for p in probes}
    recs = record_results(r.path, "pkg/mathy.py", Config(), units, probes, wrong)
    assert recs and not any(x.passed for x in recs)


def test_cli_quiz_emit_and_answers(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r = _project(make_repo)
    monkeypatch.chdir(r.path)
    qf = r.path / "q.json"
    res = CliRunner().invoke(
        app,
        [
            "quiz",
            "pkg/mathy.py",
            "--repo",
            str(r.path),
            "--n",
            "3",
            "--kinds",
            "runtime",
            "--emit",
            str(qf),
        ],
    )
    assert res.exit_code == 0, res.output
    qs = json.loads(qf.read_text())
    assert qs and all("answer" not in q for q in qs)  # answers never emitted
    probes, _, _ = build_quiz(r.path, "pkg/mathy.py", Config(), n=3, kinds=("runtime",))
    assert [p.id for p in probes] == [q["id"] for q in qs]  # deterministic regeneration
    af = r.path / "a.json"
    af.write_text(json.dumps({p.id: p.answer for p in probes}))
    res = CliRunner().invoke(
        app,
        [
            "quiz",
            "pkg/mathy.py",
            "--repo",
            str(r.path),
            "--n",
            "3",
            "--kinds",
            "runtime",
            "--answers",
            str(af),
        ],
    )
    assert res.exit_code == 0 and "UNDERSTOOD" in res.output, res.output
    assert (r.path / ".cdebt" / "understanding.json").exists()


def test_literal_none_answer_is_gradable() -> None:
    """Regression (found on a real repo): a function returning None has the literal answer
    'None'; the multi-select 'none' shortcut must not swallow it."""
    p = Probe(
        id="3",
        kind="runtime",
        path="a",
        symbol="f",
        question="q",
        options=["None", "0", "''", "[]"],
        answer="None",
        verified_by="sandbox-exec",
    )
    assert grade(p, "None") and grade(p, "A") and not grade(p, "B")
