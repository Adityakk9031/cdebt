"""Pre-registered analysis (docs/METHOD.md). Do not change after seeing outcomes."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from cdebt.backtest import metrics as M
from cdebt.backtest.aidev import Units


def _rate(y: np.ndarray) -> dict[str, Any]:
    k, n = int(y.sum()), len(y)
    lo, hi = M.wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else float("nan"), "ci95": [lo, hi]}


def _reindex(a: np.ndarray) -> np.ndarray:
    return np.unique(a, return_inverse=True)[1]


def _rr(
    exposed: np.ndarray, y: np.ndarray, repo: np.ndarray, reps: int, seed: int
) -> dict[str, Any]:
    cl = _reindex(repo)
    est, lo, hi = M.boot_cis(lambda w: M.mh_rr(exposed, y, cl, w), cl, reps=reps, seed=seed)
    return {
        "rr_mh": est,
        "ci95": [lo, hi],
        "n_repos": int(cl.max()) + 1 if len(cl) else 0,
        "exposed": _rate(y[exposed == 1]),
        "unexposed": _rate(y[exposed == 0]),
    }


def run(u: Units, *, reps: int = 2000, seed: int = 0) -> dict[str, Any]:
    e = u.eligible
    out: dict[str, Any] = {"seed": seed, "reps": reps}
    out["descriptive"] = {
        "merged_prs": u.n_prs_merged,
        "units": int(len(u.S)),
        "eligible_units": int(e.sum()),
        "repos": int(len(np.unique(u.repo))),
        "eligible_repos": int(len(np.unique(u.repo[e]))),
        "max_merged_at_unix": u.max_merged,
        "base_rates_eligible": {
            "fix90": _rate(u.fix90[e]),
            "fix30": _rate(u.fix30[e]),
            "revert90": _rate(u.revert90[e]),
            "revert30": _rate(u.revert30[e]),
        },
        "U_levels": {str(k): int((u.U[e] == k).sum()) for k in (0.0, 0.25, 0.5, 1.0)},
        "agents": {n: int(((u.agent == i) & e).sum()) for i, n in enumerate(u.agent_names)},
    }
    S = u.S[e]
    if len(S) < 10:
        out["error"] = "too few eligible units"
        return out
    q25, q75 = (float(x) for x in np.percentile(S, [25, 75]))
    out["cutoffs"] = {"q25": q25, "q75": q75, "degenerate": q25 == q75}
    if q25 == q75:
        return out
    idx = np.flatnonzero(e)
    hi_m, lo_m = q75 <= S, q25 >= S
    sel = idx[hi_m | lo_m]
    expo = (S[hi_m | lo_m] >= q75).astype(int)
    repo = u.repo[sel]
    out["groups"] = {"high_n": int(hi_m.sum()), "low_n": int(lo_m.sum())}
    out["H1_fix90"] = _rr(expo, u.fix90[sel], repo, reps, seed)
    out["H2_revert90"] = _rr(expo, u.revert90[sel], repo, reps, seed)
    out["fix30"] = _rr(expo, u.fix30[sel], repo, reps, seed)
    out["revert30"] = _rr(expo, u.revert30[sel], repo, reps, seed)
    # H3: time to first fix among FIX units
    has = u.fix90[sel] == 1
    xh = u.ttf_days[sel][has & (expo == 1)]
    xl = u.ttf_days[sel][has & (expo == 0)]
    ch = repo[has & (expo == 1)]
    cl_ = repo[has & (expo == 0)]
    est, lo, hi = M.cliffs_boot(xh, ch, xl, cl_, reps=reps, seed=seed)
    out["H3_ttf"] = {
        "cliffs_delta": est,
        "ci95": [lo, hi],
        "n_high": len(xh),
        "n_low": len(xl),
        "median_days_high": float(np.median(xh)) if len(xh) else float("nan"),
        "median_days_low": float(np.median(xl)) if len(xl) else float("nan"),
    }
    # H4: adjusted odds ratio
    ag = u.agent[sel]
    dummies = [(ag == k).astype(float) for k in range(1, len(u.agent_names))]
    X = np.column_stack([np.ones(len(sel)), expo, np.log1p(u.additions[sel]), u.C[sel], *dummies])
    y = u.fix90[sel]
    cl = _reindex(repo)
    est, lo, hi = M.boot_cis(
        lambda w: float(math.exp(M.logistic(X, y, w)[1])), cl, reps=reps, seed=seed
    )
    out["H4_adjusted_or"] = {"or_high": est, "ci95": [lo, hi]}
    # component check: no human review evidence vs some
    Ue = u.U[e]
    m = (Ue == 0) | (Ue >= 0.5)
    sel2 = idx[m]
    out["component_U"] = _rr((u.U[sel2] == 0).astype(int), u.fix90[sel2], u.repo[sel2], reps, seed)
    out["spearman_S_fix90"] = M.spearman(S, u.fix90[e])
    return out


def verdict_ratio(ci: list[float], est: float, higher_is_predicted: bool = True) -> str:
    lo, hi = ci
    if math.isnan(lo) or math.isnan(est):
        return "inconclusive (CI undefined)"
    if lo > 1:
        return "supported (CI excludes 1, predicted direction)"
    if hi < 1:
        return "opposite direction (CI excludes 1)"
    return "not supported (CI includes 1)"


def verdict_delta(ci: list[float], est: float) -> str:
    lo, hi = ci
    if math.isnan(lo) or math.isnan(est):
        return "inconclusive (CI undefined)"
    if lo > 0:
        return "supported (CI excludes 0, predicted direction: longer for HIGH)"
    if hi < 0:
        return "opposite direction (shorter for HIGH, CI excludes 0)"
    return "not supported (CI includes 0)"
