from __future__ import annotations

import json
import shutil

from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.config import Config
from cdebt.coverage import compute_coverage
from cdebt.scip import fingerprint, index_path, meta_path
from conftest import FIX, Repo

AI = "Co-Authored-By: Claude <noreply@anthropic.com>"
PY = """LIMITS = [1, 2]
TOTAL = sum(LIMITS) * 3


def typed(a: int, b: int) -> int:
    return a + b


def untyped(x):
    return x
"""


def _repo(make_repo) -> Repo:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", PY)
    r.write("web/app.ts", "export function f(x: number): number {\n  return x + 1\n}\n")
    r.commit(f"ai\n\n{AI}")
    return r


def test_coverage_buckets(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    res = compute_coverage(r.path, Config(), top_n=10, scip_idx=None)
    by = {f.path: f for f in res.files}
    a, ts = by["a.py"], by["web/app.ts"]
    assert a.klass == "partly verifiable" and a.verifiable_lines > 0 and a.attest_only_lines > 0
    assert {"runtime", "constant"} <= set(a.probe_kinds)
    assert ts.klass == "no-quiz" and ts.no_quiz_lines == ts.ai_lines  # TypeScript, no index
    tot = a.ai_lines + ts.ai_lines
    assert res.verifiable_share == a.verifiable_lines / tot
    assert res.no_quiz_share == ts.ai_lines / tot and not res.scip_index
    assert (
        abs(
            (res.verifiable_share or 0)
            + (res.attest_only_share or 0)
            + (res.no_quiz_share or 0)
            - 1
        )
        < 1e-9
    )
    assert any("SCIP" in n for n in res.notes)


def test_coverage_cli_json_and_table_and_read_only(make_repo) -> None:  # type: ignore[no-untyped-def]
    r = _repo(make_repo)
    run = CliRunner()
    j = json.loads(
        run.invoke(app, ["coverage", "--repo", str(r.path), "--no-scip", "--json"]).output
    )
    assert j["top_n"] == 25 and {f["path"] for f in j["files"]} == {"a.py", "web/app.ts"}
    t = run.invoke(app, ["coverage", "--repo", str(r.path), "--no-scip"])
    text = " ".join(t.output.split())  # rich wraps lines
    assert t.exit_code == 0 and "can only be ATTESTED" in text and "no quiz support" in text
    assert not (r.path / ".cdebt").exists()


def test_scip_makes_typescript_partly_quizzable_only_with_index(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    for f in (FIX / "scip" / "pkg").glob("*.py"):
        r.write(f"pkg/{f.name}", f.read_text())
    r.commit(f"ai\n\n{AI}")
    ip = index_path(r.path)
    ip.parent.mkdir(parents=True)
    shutil.copy(FIX / "scip" / "index.scip", ip)
    meta_path(r.path).write_text(
        json.dumps({"fingerprint": fingerprint(r.path, Config().extensions)})
    )
    res = compute_coverage(r.path, Config(), top_n=10)
    lib = next(f for f in res.files if f.path == "pkg/lib.py")
    assert "callgraph" in lib.probe_kinds and res.scip_index
