from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("numpy")

from cdebt.backtest import matched as M  # noqa: E402
from cdebt.backtest.aidev import DAY, Units  # noqa: E402


def _units(rows: list[dict]) -> Units:  # type: ignore[type-arg]
    n = len(rows)
    g = lambda k, d=0.0: np.array([r.get(k, d) for r in rows], dtype=float)  # noqa: E731
    return Units(
        repo=g("repo").astype(int),
        pr_id=g("pr").astype(int),
        additions=g("add", 10.0),
        agent=g("agent").astype(int),
        agent_names=["A", "B"],
        U=g("U"),
        C=np.zeros(n),
        S=np.ones(n),
        eligible=np.array([r.get("elig", True) for r in rows]),
        fix90=g("fix"),
        fix30=g("fix30"),
        revert90=np.zeros(n),
        revert30=np.zeros(n),
        ttf_days=np.full(n, np.nan),
        merged_ts=g("t") * DAY,
        n_prs_merged=n,
        max_merged=1e12,
    )


def test_pr_table_aggregates_files_to_prs() -> None:
    u = _units(
        [
            {"pr": 1, "U": 0.25, "add": 3, "fix": 0, "t": 1},
            {"pr": 1, "U": 1.0, "add": 4, "fix": 1, "t": 1},
            {"pr": 2, "U": 0.0, "add": 5, "t": 2},
        ]
    )
    t = M.pr_table(u)
    assert list(t["pr_id"]) == [1, 2]
    assert list(t["U"]) == [1.0, 0.0] and list(t["add"]) == [7.0, 5.0]
    assert list(t["fix90"]) == [1.0, 0.0] and list(t["merged"]) == [1 * DAY, 2 * DAY]


def _rows() -> list[dict]:  # type: ignore[type-arg]
    return [
        {"pr": 1, "repo": 0, "agent": 0, "U": 0.0, "t": 10, "add": 5},  # unreviewed
        {"pr": 2, "repo": 0, "agent": 0, "U": 0.5, "t": 25, "add": 6},  # ok partner (15d)
        {"pr": 3, "repo": 0, "agent": 0, "U": 0.5, "t": 12, "add": 6},  # closer partner (2d)
        {"pr": 4, "repo": 0, "agent": 1, "U": 0.5, "t": 10, "add": 5},  # wrong agent
        {"pr": 5, "repo": 0, "agent": 0, "U": 0.5, "t": 11, "add": 5000},  # wrong size bin
        {"pr": 6, "repo": 0, "agent": 0, "U": 0.5, "t": 200, "add": 5},  # too far in time
        {"pr": 7, "repo": 0, "agent": 0, "U": 0.25, "t": 10, "add": 5},  # bare approval: out
        {"pr": 8, "repo": 0, "agent": 0, "U": 0.0, "t": 30, "add": 5},  # unreviewed #2
    ]


def test_matching_rules_and_no_replacement() -> None:
    t = M.pr_table(_units(_rows()))
    edges = np.array([6.0, 100.0])  # bin0: add<6 ... bin1: 6..100, bin2: >100
    m = M.match(t, min_repo_prs=3, reviewed_min_u=0.5, edges=edges)
    got = {(int(t["pr_id"][a]), int(t["pr_id"][b])) for a, b in m["pairs"]}
    # PR1 (add 5, bin0): candidates same agent+bin0: PR6 too far -> none... PR2/PR3 are add 6 =
    # bin1, so PR1 has NO partner; PR8 likewise. Partners need the same size bin.
    assert got == set()
    edges = np.array([100.0, 1000.0])  # now add 5/6 share bin0; PR5 (5000) is bin2
    m = M.match(t, min_repo_prs=3, reviewed_min_u=0.5, edges=edges)
    got = {(int(t["pr_id"][a]), int(t["pr_id"][b])) for a, b in m["pairs"]}
    # PR1 takes nearest-in-time PR3 (2d); PR8 (t=30) then takes PR2 (5d), no replacement.
    assert got == {(1, 3), (8, 2)}
    assert m["n_pairs"] == 2 and m["n_repos"] == 1


def test_repo_threshold_excludes_small_repos() -> None:
    t = M.pr_table(_units(_rows()))
    m = M.match(t, min_repo_prs=50, reviewed_min_u=0.5, edges=np.array([100.0, 1000.0]))
    assert m["n_pairs"] == 0 and m["repos_meeting_threshold"] == 0


def test_matching_ignores_outcomes() -> None:
    rows = _rows()
    a = M.match(
        M.pr_table(_units(rows)),
        min_repo_prs=3,
        reviewed_min_u=0.5,
        edges=np.array([100.0, 1000.0]),
    )
    for r in rows:
        r["fix"] = 1.0
    b = M.match(
        M.pr_table(_units(rows)),
        min_repo_prs=3,
        reviewed_min_u=0.5,
        edges=np.array([100.0, 1000.0]),
    )
    assert (a["pairs"] == b["pairs"]).all()


def test_exact_mcnemar_known_values() -> None:
    assert math.isclose(M.exact_mcnemar_p(1, 5), 2 * (1 + 6) / 64)
    assert M.exact_mcnemar_p(3, 3) == 1.0
    assert math.isnan(M.exact_mcnemar_p(0, 0))


def _synthetic(effect: float, seed: int = 0, repos: int = 80, per: int = 40) -> Units:
    rng = np.random.default_rng(seed)
    rows, pid = [], 0
    for r in range(repos):
        base = rng.uniform(0.05, 0.3)
        for _ in range(per):
            pid += 1
            unrev = rng.random() < 0.5
            p = min(0.95, base * (effect if unrev else 1.0))
            f = float(rng.random() < p)
            rows.append(
                {
                    "pr": pid,
                    "repo": r,
                    "agent": 0,
                    "U": 0.0 if unrev else 1.0,
                    "t": rng.uniform(0, 20),
                    "add": 10,
                    "fix": f,
                    "fix30": f,
                }
            )
    return _units(rows)


def test_planted_effect_is_detected_and_null_is_not() -> None:
    res = M.run(_synthetic(effect=3.0), reps=300, seed=1)
    s = res["runs"]["primary_N20"]["fix90"]
    assert s["odds_ratio_b_over_c"] > 1.5 and s["or_ci95"][0] > 1.0
    assert M.verdict(s).startswith("supported")
    null = M.run(_synthetic(effect=1.0, seed=3), reps=300, seed=1)["runs"]["primary_N20"]["fix90"]
    assert null["or_ci95"][0] < 1.0 < null["or_ci95"][1]
    assert M.verdict(null).startswith("not supported")


def test_inconclusive_with_few_discordant_pairs() -> None:
    assert M.verdict({"n_pairs": 50, "discordant": 4, "or_ci95": [0.5, 9.0]}).startswith(
        "inconclusive"
    )


def test_run_is_deterministic() -> None:
    u = _synthetic(effect=2.0)
    assert M.run(u, reps=50, seed=2) == M.run(u, reps=50, seed=2)
