"""Effect sizes with confidence intervals. numpy only. Repo-clustered bootstrap throughout:
resampling REPOS (not rows) because units within a repo are not independent."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from cdebt.stats import wilson

__all__ = ["wilson", "mh_rr", "boot_cis", "cliffs_delta", "logistic", "spearman", "Interval"]

Interval = tuple[float, float, float]  # (estimate, lo, hi)


def mh_rr(
    exposed: np.ndarray, y: np.ndarray, stratum: np.ndarray, w: np.ndarray | None = None
) -> float:
    """Mantel-Haenszel risk ratio of exposed(1) vs unexposed(0) across strata.
    `w` optional row weights (integer bootstrap counts). NaN if undefined."""
    w = np.ones(len(y)) if w is None else w
    k = int(stratum.max()) + 1 if len(stratum) else 0
    a1 = np.bincount(stratum, w * (exposed == 1) * y, k)
    n1 = np.bincount(stratum, w * (exposed == 1), k)
    a0 = np.bincount(stratum, w * (exposed == 0) * y, k)
    n0 = np.bincount(stratum, w * (exposed == 0), k)
    n = n1 + n0
    with np.errstate(divide="ignore", invalid="ignore"):
        num = np.nansum(np.where(n > 0, a1 * n0 / n, 0.0))
        den = np.nansum(np.where(n > 0, a0 * n1 / n, 0.0))
    return float(num / den) if den > 0 else float("nan")


def boot_cis(
    stat: Callable[[np.ndarray], float], cluster: np.ndarray, *, reps: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """`stat(weights)` takes per-ROW integer weights. Returns (estimate, lo95, hi95) where the
    CI is the percentile interval over repo-resampled weights. NaN replicates are dropped and
    counted via the returned estimate only; callers should report n of valid replicates."""
    rng = np.random.default_rng(seed)
    k = int(cluster.max()) + 1
    est = stat(np.ones(len(cluster)))
    vals = []
    for _ in range(reps):
        counts = np.bincount(rng.integers(0, k, k), minlength=k).astype(float)
        v = stat(counts[cluster])
        if not math.isnan(v):
            vals.append(v)
    if len(vals) < max(20, reps // 10):
        return est, float("nan"), float("nan")
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return est, float(lo), float(hi)


def cliffs_delta(x: np.ndarray, y: np.ndarray) -> float:
    """P(x > y) - P(x < y). In [-1, 1]; NaN if either group is empty."""
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    ys = np.sort(y)
    gt = np.searchsorted(ys, x, side="left").sum()  # y < x
    lt = (len(ys) - np.searchsorted(ys, x, side="right")).sum()  # y > x
    return float((gt - lt) / (len(x) * len(y)))


def cliffs_boot(
    x: np.ndarray, xc: np.ndarray, y: np.ndarray, yc: np.ndarray, *, reps: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    allc = np.unique(np.concatenate([xc, yc]))
    est = cliffs_delta(x, y)
    if len(allc) < 2:
        return est, float("nan"), float("nan")
    xi = {c: np.flatnonzero(xc == c) for c in allc}
    yi = {c: np.flatnonzero(yc == c) for c in allc}
    vals = []
    for _ in range(reps):
        pick = rng.choice(allc, len(allc))
        xs = np.concatenate([x[xi[c]] for c in pick])
        ys = np.concatenate([y[yi[c]] for c in pick])
        v = cliffs_delta(xs, ys)
        if not math.isnan(v):
            vals.append(v)
    if len(vals) < max(20, reps // 10):
        return est, float("nan"), float("nan")
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return est, float(lo), float(hi)


def logistic(
    X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None, iters: int = 25, ridge: float = 1e-6
) -> np.ndarray:
    """Weighted logistic regression by IRLS. X must include the intercept column."""
    w = np.ones(len(y)) if w is None else w
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30)
        p = 1 / (1 + np.exp(-eta))
        s = w * p * (1 - p) + 1e-12
        z = eta + (y - p) / np.maximum(p * (1 - p), 1e-12)
        A = (X * s[:, None]).T @ X + ridge * np.eye(X.shape[1])
        new = np.linalg.solve(A, (X * s[:, None]).T @ z)
        if np.max(np.abs(new - beta)) < 1e-8:
            beta = new
            break
        beta = new
    return beta


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    def rank(v: np.ndarray) -> np.ndarray:
        order = np.argsort(v, kind="mergesort")
        r = np.empty(len(v))
        r[order] = np.arange(len(v), dtype=float)
        # average ranks for ties
        _, inv, cnt = np.unique(v, return_inverse=True, return_counts=True)
        sums = np.bincount(inv, r)
        return (sums / cnt)[inv]

    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan")
    return float(np.corrcoef(rank(a), rank(b))[0, 1])
