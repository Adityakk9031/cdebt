"""TypeScript/JavaScript probes. Answers come from executing the code in the Node sandbox and are
cross-checked here against a plain (trusted-fixture) `node` evaluation of the same call."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cdebt.config import Config
from cdebt.coverage import compute_coverage
from cdebt.probes import callgraph, js
from cdebt.quiz import build_quiz, record_results
from cdebt.scip import fingerprint, index_files, index_is_fresh, load_all, meta_path
from cdebt.store import Store
from cdebt.units import extract_units
from conftest import FIX, Repo

pytestmark = pytest.mark.skipif(not js.node_ok()[0], reason="node >= 24 not installed")
CALC = (FIX / "ts" / "src" / "calc.ts").read_text()


def _repo(make_repo, files: dict[str, str]) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    for rel, text in files.items():
        r.write(rel, text)
    r.commit("init")
    return r


def _oracle(repo: Path, rel: str, call: str) -> str:
    """Independent evaluation with plain node (fixtures are trusted): JSON of the result."""
    src = (repo / rel).read_text()
    names = ", ".join(sorted({call.split("(")[0]}))
    script = f"import * as m from {json.dumps('data:text/javascript,' + 'x')};" if False else ""
    del script
    tmp = repo / "oracle.mts"
    tmp.write_text(src + f"\nconsole.log(JSON.stringify({call}));\n")
    out = subprocess.run(["node", str(tmp)], capture_output=True, text=True, cwd=repo)
    tmp.unlink()
    assert out.returncode == 0, out.stderr
    del names
    return out.stdout.strip()


def test_runtime_answers_match_plain_node(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo, {"src/calc.ts": CALC})
    units = extract_units("src/calc.ts", CALC)
    probes, skips = js.generate_runtime(r.path, "src/calc.ts", CALC, units, seed=1, per_unit=2)
    assert probes and {p.symbol for p in probes} >= {"add", "shout", "first", "inner", "pure"}
    for p in probes:
        call = p.question.split("`")[1]
        assert json.loads(p.answer) == json.loads(_oracle(r.path, "src/calc.ts", call)), call
        assert p.answer in p.options and len(set(p.options)) == len(p.options)
        assert p.verified_by == "sandbox-exec" and p.evidence["runtime"] == "node"
        assert p.evidence["deterministic_runs"] == 2
    text = " | ".join(skips)
    assert "roll" in text and "Math.random" in text and "reads" in text and "process" in text


def test_runtime_is_seeded(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo, {"src/calc.ts": CALC})
    u = extract_units("src/calc.ts", CALC)
    a, _ = js.generate_runtime(r.path, "src/calc.ts", CALC, u, seed=4)
    b, _ = js.generate_runtime(r.path, "src/calc.ts", CALC, u, seed=4)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_constants_probes(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo, {"src/calc.ts": CALC})
    units = extract_units("src/calc.ts", CALC)
    probes, skips = js.generate_constants(r.path, "src/calc.ts", CALC, units, 0, per_unit=5)
    names = {p.evidence["name"]: p for p in probes}
    assert {"TOTAL", "GREETING", "LIMITS"} <= set(names)
    assert names["TOTAL"].answer == "120" and names["GREETING"].answer == '"SVC!"'
    assert "STAMP" not in names  # Date.now(): environment-dependent, never asked
    assert all(p.symbol == "<module>" and p.evidence["evaluations"] == 2 for p in probes)


def test_module_with_toplevel_side_effects_is_not_imported(make_repo) -> None:  # type: ignore[no-untyped-def]
    marker = "should_not_exist.txt"
    src = f"import fs from 'node:fs';\nfs.writeFileSync('{marker}', 'x');\nexport function f(a: number): number {{ return a; }}\n"
    r = _repo(make_repo, {"m.ts": src})
    probes, skips = js.generate_runtime(r.path, "m.ts", src, extract_units("m.ts", src), 0)
    assert probes == [] and "runs code on import" in skips[0]
    assert not (r.path / marker).exists()


def test_unsupported_inputs_are_skipped_with_reasons(make_repo) -> None:  # type: ignore[no-untyped-def]
    untyped = "export function f(a, b) { return a + b; }\n"
    r = _repo(make_repo, {"u.ts": untyped, "c.tsx": "export const A = <div/>;\n"})
    p, s = js.generate_runtime(r.path, "u.ts", untyped, extract_units("u.ts", untyped), 0)
    assert p == [] and "missing type" in " ".join(s)
    p2, s2 = js.generate_runtime(r.path, "c.tsx", "x", [], 0)
    assert p2 == [] and "JSX/TSX unsupported" in s2[0]


def test_jsdoc_typed_javascript(make_repo) -> None:  # type: ignore[no-untyped-def]
    src = "/**\n * @param {number} a\n * @param {string[]} words\n */\nexport function f(a, words) {\n  return words.length + a;\n}\n"
    r = _repo(make_repo, {"m.mjs": src})
    probes, skips = js.generate_runtime(r.path, "m.mjs", src, extract_units("m.mjs", src), 3)
    assert probes, skips
    call = probes[0].question.split("`")[1]
    assert json.loads(probes[0].answer) == json.loads(_oracle(r.path, "m.mjs", call))


def test_throwing_function_has_throws_answer(make_repo) -> None:  # type: ignore[no-untyped-def]
    src = "export function boom(n: number): number {\n  if (n > -100) throw new RangeError('x');\n  return n;\n}\n"
    r = _repo(make_repo, {"b.ts": src})
    probes, _ = js.generate_runtime(r.path, "b.ts", src, extract_units("b.ts", src), 0, 1)
    assert probes and probes[0].answer == "throws RangeError"


def test_quiz_records_for_typescript(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo, {"src/calc.ts": CALC})
    cfg = Config()
    probes, units, _ = build_quiz(
        r.path, "src/calc.ts", cfg, n=8, seed=0, kinds=("runtime", "constant")
    )
    recs = record_results(
        r.path, "src/calc.ts", cfg, units, probes, {p.id: p.answer for p in probes}
    )
    assert recs and all(x.passed for x in recs)
    st = Store(r.path)
    assert all(st.is_understood(u) for u in units if u.symbol in {x.unit_symbol for x in recs})


def test_coverage_counts_typescript_as_verifiable(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("src/calc.ts", CALC)
    r.commit("ai\n\nCo-Authored-By: Claude <noreply@anthropic.com>")  # every line AI (trailer)
    res = compute_coverage(r.path, Config(), top_n=5, scip_idx=None)
    f = next(x for x in res.files if x.path == "src/calc.ts")
    assert f.klass in ("verifiable", "partly verifiable") and f.verifiable_lines > 0
    assert {"runtime", "constant"} <= set(f.probe_kinds)


def _ts_repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    for f in (FIX / "scip_ts").rglob("*"):
        if f.is_file() and f.name != "index.scip":
            rel = f.relative_to(FIX / "scip_ts")
            r.write(str(rel), f.read_text())
    r.commit("init")
    cache = r.path / ".cdebt" / "cache"
    cache.mkdir(parents=True)
    shutil.copy(FIX / "scip_ts" / "index.scip", cache / "index.typescript.0.scip")
    meta_path(r.path).write_text(
        json.dumps(
            {
                "projects": [
                    {"file": "index.typescript.0.scip", "prefix": "", "language": "typescript"}
                ],
                "fingerprints": {"typescript": fingerprint(r.path, (".ts",))},
            }
        )
    )
    return r


def test_callgraph_from_real_scip_typescript_index(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _ts_repo(make_repo)
    assert index_is_fresh(r.path, Config().extensions)
    idx = load_all(r.path)
    src = (r.path / "src/lib.ts").read_text()
    probes, _ = callgraph.generate(r.path, "src/lib.ts", extract_units("src/lib.ts", src), idx, 3)
    assert probes and all(p.verified_by == "scip-index" for p in probes)
    q = next(p for p in probes if "need updating" in p.question and p.symbol == "helper")
    assert json.loads(q.answer) == ["src/app.ts"]
    q2 = next(p for p in probes if "code units" in p.question and p.symbol == "helper")
    assert "src/app.ts::main" in json.loads(q2.answer)


def test_scip_index_staleness_and_prefix(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _ts_repo(make_repo)
    meta = json.loads(meta_path(r.path).read_text())
    meta["projects"][0]["prefix"] = "pkg/"  # a monorepo sub-project
    meta_path(r.path).write_text(json.dumps(meta))
    assert "pkg/src/lib.ts" in load_all(r.path).docs and index_files(r.path)
    (r.path / "src/lib.ts").write_text((r.path / "src/lib.ts").read_text() + "\n// edit\n")
    assert not index_is_fresh(r.path, Config().extensions)  # edited TS source -> stale
