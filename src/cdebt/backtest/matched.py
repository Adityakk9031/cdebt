"""Analysis 2: within-repo matched pairs. Frozen in docs/PREREGISTRATION.md.
Matching never looks at outcomes."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from cdebt.backtest.aidev import DAY, Units

MIN_DISCORDANT = 10


def pr_table(u: Units) -> dict[str, np.ndarray]:
    """Aggregate (PR, file) units to PR level per the pre-registration."""
    ids, inv = np.unique(u.pr_id, return_inverse=True)
    k = len(ids)

    def agg(vals: np.ndarray, how: str) -> np.ndarray:
        if how == "sum":
            return np.bincount(inv, vals, k)
        out = np.full(k, -np.inf)
        np.maximum.at(out, inv, vals)
        return out

    first = np.zeros(k, dtype=int)
    first[inv[::-1]] = np.arange(len(inv))[::-1]  # first unit index of each PR
    return {
        "pr_id": ids,
        "repo": u.repo[first],
        "agent": u.agent[first],
        "merged": u.merged_ts[first],
        "add": agg(u.additions, "sum"),
        "U": agg(u.U, "max"),
        "fix90": agg(u.fix90, "max"),
        "fix30": agg(u.fix30, "max"),
        "eligible": u.eligible[first],
    }


def match(
    t: dict[str, np.ndarray], *, min_repo_prs: int, reviewed_min_u: float, edges: np.ndarray
) -> dict[str, Any]:
    """Return matched pairs (indices into the PR table) plus bookkeeping."""
    elig = t["eligible"]
    size_bin = np.digitize(t["add"], edges)
    unrev = elig & (t["U"] == 0)
    rev = elig & (t["U"] >= reviewed_min_u)
    repos, counts = np.unique(t["repo"][elig], return_counts=True)
    big = set(repos[counts >= min_repo_prs].tolist())
    pairs: list[tuple[int, int]] = []
    kept_repos: set[int] = set()
    by_repo: dict[int, list[int]] = {}
    for i0 in np.flatnonzero(unrev | rev):
        if int(t["repo"][i0]) in big:
            by_repo.setdefault(int(t["repo"][i0]), []).append(int(i0))
    for repo, idx in by_repo.items():
        us = sorted((i for i in idx if unrev[i]), key=lambda i: (t["merged"][i], t["pr_id"][i]))
        rs = [i for i in idx if rev[i]]
        if not us or not rs:
            continue
        used: set[int] = set()
        for i in us:
            best: int | None = None
            bd: tuple[float, int] | None = None
            for j in rs:
                if j in used or t["agent"][j] != t["agent"][i] or size_bin[j] != size_bin[i]:
                    continue
                d = abs(float(t["merged"][j] - t["merged"][i]))
                if d > 30 * DAY:
                    continue
                key = (d, int(t["pr_id"][j]))
                if bd is None or key < bd:
                    best, bd = j, key
            if best is not None:
                used.add(best)
                pairs.append((i, best))
                kept_repos.add(repo)
    p = np.array(pairs, dtype=int).reshape(-1, 2)
    return {
        "pairs": p,
        "n_pairs": len(p),
        "n_repos": len(kept_repos),
        "eligible_prs": int(elig.sum()),
        "unreviewed_in_big_repos": int(sum(1 for r in by_repo for i in by_repo[r] if unrev[i])),
        "reviewed_in_big_repos": int(sum(1 for r in by_repo for i in by_repo[r] if rev[i])),
        "repos_meeting_threshold": len(big),
    }


def exact_mcnemar_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return float("nan")
    k = min(b, c)
    tail: float = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return float(min(1.0, 2 * tail))


def _ratio(B: float, C: float) -> float:
    if C > 0:
        return B / C
    return float("inf") if B > 0 else float("nan")


def pair_stats(
    t: dict[str, np.ndarray], m: dict[str, Any], outcome: str, *, reps: int, seed: int
) -> dict[str, Any]:
    p = m["pairs"]
    if len(p) == 0:
        return {"n_pairs": 0, "note": "no pairs"}
    yu, yr = t[outcome][p[:, 0]] > 0, t[outcome][p[:, 1]] > 0
    b_i, c_i = (yu & ~yr).astype(float), (~yu & yr).astype(float)
    repo = t["repo"][p[:, 0]]
    cl = np.unique(repo, return_inverse=True)[1]
    k = int(cl.max()) + 1
    bk, ck, nk = np.bincount(cl, b_i, k), np.bincount(cl, c_i, k), np.bincount(cl, minlength=k)
    b, c, n = int(b_i.sum()), int(c_i.sum()), len(p)
    rng = np.random.default_rng(seed)
    ors, rds = [], []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, k, k), minlength=k).astype(float)
        B, C, N = float(w @ bk), float(w @ ck), float(w @ nk)
        o = _ratio(B, C)
        if not math.isnan(o):
            ors.append(o)
        if N > 0:
            rds.append((B - C) / N)

    def pct(v: list[float]) -> list[float]:
        if len(v) < max(20, reps // 10):
            return [float("nan"), float("nan")]
        lo, hi = np.percentile(v, [2.5, 97.5])
        return [float(lo), float(hi)]

    return {
        "n_pairs": n,
        "n_repos": k,
        "both_fix": int((yu & yr).sum()),
        "neither_fix": int((~yu & ~yr).sum()),
        "only_unreviewed_fix_b": b,
        "only_reviewed_fix_c": c,
        "discordant": b + c,
        "rate_unreviewed": float(yu.mean()),
        "rate_reviewed": float(yr.mean()),
        "odds_ratio_b_over_c": _ratio(float(b), float(c)),
        "or_ci95": pct(ors),
        "risk_difference": (b - c) / n,
        "rd_ci95": pct(rds),
        "mcnemar_exact_p_unclustered": exact_mcnemar_p(b, c),
    }


def balance(t: dict[str, np.ndarray], m: dict[str, Any]) -> dict[str, Any]:
    p = m["pairs"]
    if len(p) == 0:
        return {}
    a, r = p[:, 0], p[:, 1]
    return {
        "mean_additions_unreviewed": float(t["add"][a].mean()),
        "mean_additions_reviewed": float(t["add"][r].mean()),
        "median_abs_days_apart": float(np.median(np.abs(t["merged"][a] - t["merged"][r])) / DAY),
        "same_agent_share": float((t["agent"][a] == t["agent"][r]).mean()),
    }


def run(u: Units, *, reps: int = 2000, seed: int = 0) -> dict[str, Any]:
    t = pr_table(u)
    elig = t["eligible"]
    out: dict[str, Any] = {
        "seed": seed,
        "reps": reps,
        "eligible_prs": int(elig.sum()),
        "prs_total": int(len(t["pr_id"])),
    }
    if elig.sum() < 30:
        out["error"] = "too few eligible PRs"
        return out
    edges = np.percentile(t["add"][elig], [100 / 3, 200 / 3])
    out["size_bin_edges"] = [float(x) for x in edges]
    out["U_levels_eligible_prs"] = {
        str(k): int((t["U"][elig] == k).sum()) for k in (0.0, 0.25, 0.5, 1.0)
    }
    runs: dict[str, Any] = {}
    for name, n_min, rev_u in (
        ("primary_N20", 20, 0.5),
        ("sens_N10", 10, 0.5),
        ("sens_N50", 50, 0.5),
        ("dose_U1_N20", 20, 1.0),
    ):
        m = match(t, min_repo_prs=n_min, reviewed_min_u=rev_u, edges=edges)
        runs[name] = {
            "min_repo_prs": n_min,
            "reviewed_min_u": rev_u,
            "matching": {k: v for k, v in m.items() if k != "pairs"},
            "balance": balance(t, m),
            "fix90": pair_stats(t, m, "fix90", reps=reps, seed=seed),
            "fix30": pair_stats(t, m, "fix30", reps=reps, seed=seed),
        }
    out["runs"] = runs
    return out


def verdict(stats: dict[str, Any]) -> str:
    if stats.get("n_pairs", 0) == 0:
        return "no pairs"
    if stats["discordant"] < MIN_DISCORDANT:
        return f"inconclusive (<{MIN_DISCORDANT} discordant pairs)"
    lo = stats["or_ci95"][0]
    if math.isnan(lo):
        return "inconclusive (CI undefined)"
    if lo > 1:
        return "supported (OR CI lower bound > 1)"
    hi = stats["or_ci95"][1]
    if hi < 1:
        return "opposite direction (OR CI upper bound < 1)"
    return "not supported (CI includes 1)"
