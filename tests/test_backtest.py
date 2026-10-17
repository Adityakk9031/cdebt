from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from typer.testing import CliRunner

from cdebt.backtest import aidev, analysis, metrics, report
from cdebt.cli import app


def test_mh_rr_known_value() -> None:
    # two strata; hand computation: stratum A a1=2,n1=4,a0=1,n0=4 ; B a1=1,n1=2,a0=1,n0=6
    exposed = np.array([1] * 4 + [0] * 4 + [1] * 2 + [0] * 6)
    y = np.array([1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0])
    st = np.array([0] * 8 + [1] * 8)
    num = 2 * 4 / 8 + 1 * 6 / 8
    den = 1 * 4 / 8 + 1 * 2 / 8
    assert math.isclose(metrics.mh_rr(exposed, y, st), num / den)


def test_cliffs_delta_known() -> None:
    assert metrics.cliffs_delta(np.array([3.0, 4.0]), np.array([1.0, 2.0])) == 1.0
    assert metrics.cliffs_delta(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == -1.0
    assert metrics.cliffs_delta(np.array([1.0, 2.0]), np.array([1.0, 2.0])) == 0.0
    assert math.isnan(metrics.cliffs_delta(np.array([]), np.array([1.0])))


def test_logistic_recovers_coefficients() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=20000)
    p = 1 / (1 + np.exp(-(-0.5 + 1.2 * x)))
    y = (rng.random(20000) < p).astype(float)
    beta = metrics.logistic(np.column_stack([np.ones_like(x), x]), y)
    assert abs(beta[0] + 0.5) < 0.08 and abs(beta[1] - 1.2) < 0.08


def test_cluster_bootstrap_covers_true_rr_and_is_seeded() -> None:
    rng = np.random.default_rng(1)
    n_rep = 200
    repo = np.repeat(np.arange(n_rep), 20)
    expo = rng.integers(0, 2, len(repo))
    y = (rng.random(len(repo)) < np.where(expo == 1, 0.30, 0.15)).astype(float)  # true RR = 2
    f = lambda w: metrics.mh_rr(expo, y, repo, w)  # noqa: E731
    a = metrics.boot_cis(f, repo, reps=300, seed=3)
    b = metrics.boot_cis(f, repo, reps=300, seed=3)
    assert a == b and a[1] < 2.0 < a[2]


def test_null_effect_ci_includes_one() -> None:
    rng = np.random.default_rng(5)
    repo = np.repeat(np.arange(150), 20)
    expo = rng.integers(0, 2, len(repo))
    y = (rng.random(len(repo)) < 0.2).astype(float)  # no effect
    est, lo, hi = metrics.boot_cis(lambda w: metrics.mh_rr(expo, y, repo, w), repo, reps=300)
    assert lo < 1.0 < hi


def test_spearman() -> None:
    assert math.isclose(metrics.spearman(np.arange(10.0), np.arange(10.0) ** 2), 1.0)
    assert math.isnan(metrics.spearman(np.ones(5), np.arange(5.0)))


def _tables() -> dict[str, list[dict]]:  # type: ignore[type-arg]
    def pr(
        i: int,
        num: int,
        title: str,
        merged: str,
        repo: int = 1,
        body: str = "",
        user: str = "agent-user",
    ) -> dict:  # type: ignore[type-arg]
        return {
            "id": i,
            "number": num,
            "user": user,
            "state": "closed",
            "created_at": merged,
            "merged_at": merged,
            "repo_id": repo,
            "repo_url": f"https://api.github.com/repos/o/r{repo}",
            "html_url": f"https://github.com/o/r{repo}/pull/{num}",
            "title": title,
            "body": body,
            "agent": "Copilot",
        }

    prs = [
        pr(1, 1, "Add feature", "2025-01-01T00:00:00Z"),
        pr(2, 2, "Fix crash in feature", "2025-01-10T00:00:00Z"),
        pr(3, 3, 'Revert "Add feature"', "2025-01-20T00:00:00Z", body="reverts #1"),
        pr(4, 4, "Other", "2025-09-01T00:00:00Z"),
        pr(5, 1, "Add thing", "2025-01-01T00:00:00Z", repo=2),
    ]
    det = [
        {"pr_id": 1, "filename": "a.py", "status": "added", "additions": 10.0},
        {"pr_id": 1, "filename": "README.md", "status": "added", "additions": 3.0},
        {"pr_id": 2, "filename": "a.py", "status": "modified", "additions": 2.0},
        {"pr_id": 3, "filename": "a.py", "status": "modified", "additions": 1.0},
        {"pr_id": 4, "filename": "b.py", "status": "added", "additions": 5.0},
        {"pr_id": 5, "filename": "c.py", "status": "added", "additions": 5.0},
    ]
    reviews = [
        {
            "pr_id": 5,
            "user": "alice",
            "user_type": "User",
            "state": "APPROVED",
            "submitted_at": "2024-12-31T00:00:00Z",
            "body": "",
        },
        {
            "pr_id": 1,
            "user": "agent-user",
            "user_type": "User",
            "state": "APPROVED",
            "submitted_at": "2024-12-31T00:00:00Z",
            "body": "self review long enough text",
        },
        {
            "pr_id": 1,
            "user": "bot",
            "user_type": "Bot",
            "state": "COMMENTED",
            "submitted_at": "2024-12-31T00:00:00Z",
            "body": "bot bot bot bot bot bot bot",
        },
    ]
    rc = [
        {
            "user": "bob",
            "user_type": "User",
            "path": "a.py",
            "created_at": "2024-12-31T00:00:00Z",
            "pull_request_url": "https://api.github.com/repos/o/r2/pulls/1",
        }
    ]
    return {
        "pull_request": prs,
        "pr_commit_details": det,
        "pr_reviews": reviews,
        "pr_review_comments": rc,
        "pr_commits": [],
    }


def test_build_units_hand_checked() -> None:
    u = aidev.build_units(_tables())
    key = {(int(p),): i for i, p in enumerate(u.pr_id)}  # first unit per PR
    i1 = key[(1,)]
    assert (u.pr_id == 1).sum() == 1  # README.md excluded (not code)
    assert u.fix90[i1] == 1 and u.ttf_days[i1] == 9.0  # PR2 "Fix crash" 9 days later
    assert u.revert90[i1] == 1  # PR3 reverts #1 within 90d
    assert u.U[i1] == 0.0  # only self-review and a bot review: no human evidence
    i5 = key[(5,)]
    assert u.U[i5] == 0.25  # alice bare approval; bob's comment is on r2#1's a.py, not c.py
    i4 = key[(4,)]
    assert not u.eligible[i4]  # merged within 90d of dataset end
    assert u.eligible[i1]
    assert u.C[i1] == 0.0 and (u.S == (1 - u.U) * (1 + u.C)).all()


def test_review_comment_on_file_gives_full_credit() -> None:
    t = _tables()
    t["pr_commit_details"].append(
        {"pr_id": 5, "filename": "a.py", "status": "added", "additions": 1.0}
    )
    u = aidev.build_units(t)
    row = np.flatnonzero(u.pr_id == 5)
    assert sorted(u.U[row]) == [0.25, 1.0]  # a.py reviewed by bob (1.0); c.py bare approval


def test_analysis_runs_and_report_is_derived_from_results(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    prs, det, rev = [], [], []
    n_id = 0
    for repo in range(1, 40):
        for k in range(30):
            n_id += 1
            m = f"2025-0{1 + (k % 5)}-{1 + (k % 27):02d}T00:00:00Z"
            title = "Fix bug" if rng.random() < 0.2 else "Add stuff"
            prs.append(
                {
                    "id": n_id,
                    "number": n_id,
                    "user": "u",
                    "state": "closed",
                    "created_at": m,
                    "merged_at": m,
                    "repo_id": repo,
                    "repo_url": "x",
                    "html_url": f"https://github.com/o/r{repo}/pull/{n_id}",
                    "title": title,
                    "body": "",
                    "agent": "Devin",
                }
            )
            det.append(
                {"pr_id": n_id, "filename": f"f{k % 3}.py", "status": "modified", "additions": 5.0}
            )
            if rng.random() < 0.4:
                rev.append(
                    {
                        "pr_id": n_id,
                        "user": "rev",
                        "user_type": "User",
                        "state": "APPROVED",
                        "submitted_at": "2024-01-01T00:00:00Z",
                        "body": "x" * 30,
                    }
                )
    prs.append(
        {
            **prs[0],
            "id": 99999,
            "number": 99999,
            "merged_at": "2026-12-31T00:00:00Z",
            "html_url": "https://github.com/o/r1/pull/99999",
        }
    )
    tables = {
        "pull_request": prs,
        "pr_commit_details": det,
        "pr_reviews": rev,
        "pr_review_comments": [],
        "pr_commits": [],
    }
    res = analysis.run(aidev.build_units(tables), reps=60, seed=0)
    assert res["groups"]["high_n"] > 0 and "H1_fix90" in res and "H4_adjusted_or" in res
    md = report.render(res, None, "cdebt backtest ...")
    assert "Scope of this test" in md and "proxy" in md and "Pre-reg" not in md[:0]
    assert f"{res['H1_fix90']['exposed']['n']}" in md  # sample sizes are shown
    assert "supported" in md or "inconclusive" in md


def test_cli_backtest_on_fixture_dir(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    tabs = _tables()
    tabs["pr_commits"] = [{"sha": "a" * 40, "pr_id": 1}]
    tabs["pr_review_comments"] = tabs["pr_review_comments"] or []
    for name, rows in tabs.items():
        pq.write_table(pa.Table.from_pylist(rows), d / f"{name}.parquet")
    out = tmp_path / "R.md"
    r = CliRunner().invoke(app, ["backtest", "--data", str(d), "--out", str(out), "--reps", "20"])
    assert r.exit_code == 0, r.output
    assert out.exists() and out.with_suffix(".json").exists()
    r = CliRunner().invoke(app, ["backtest", "--data", str(tmp_path / "nope")])
    assert r.exit_code == 2
