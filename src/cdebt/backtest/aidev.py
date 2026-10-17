"""AIDev loader and (PR, file) unit builder. Definitions are FROZEN in docs/METHOD.md
(pre-registration). Table schemas VERIFIED 2026-10-17 via the HF datasets-server /info."""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ID = "hao-li/AIDev"
COLUMNS: dict[str, list[str]] = {
    "pull_request": [
        "id",
        "number",
        "user",
        "state",
        "created_at",
        "merged_at",
        "repo_id",
        "repo_url",
        "html_url",
        "title",
        "body",
        "agent",
    ],
    "pr_reviews": ["pr_id", "user", "user_type", "state", "submitted_at", "body"],
    "pr_review_comments": ["user", "user_type", "path", "pull_request_url", "created_at"],
    "pr_commits": ["sha", "pr_id"],
    "pr_commit_details": ["pr_id", "filename", "status", "additions"],
}
CODE_EXT = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".cs",
    ".rb",
    ".php",
    ".kt",
    ".swift",
)
FIX_RE = re.compile(r"\b(fix|fixes|fixed|bug|bugfix|hotfix|regression|crash|error)\b", re.I)
DAY = 86400.0


def fetch(dest: Path, revision: str = "main") -> dict[str, Any]:
    """Download ONLY the needed columns (HTTP range reads; the 1.3 GB `patch` column is never
    fetched). Needs network + the `backtest` extra. Writes dest/*.parquet and manifest.json."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, HfFileSystem

    dest.mkdir(parents=True, exist_ok=True)
    sha = HfApi().dataset_info(REPO_ID, revision=revision).sha
    fs = HfFileSystem()
    manifest: dict[str, Any] = {
        "dataset": REPO_ID,
        "revision": sha,
        "fetched_at": datetime.now(UTC).isoformat(),
        "tables": {},
    }
    for table, cols in COLUMNS.items():
        with fs.open(f"datasets/{REPO_ID}@{sha}/{table}.parquet", "rb") as f:
            t = pq.ParquetFile(f).read(columns=cols)
        pq.write_table(t, dest / f"{table}.parquet", compression="zstd")
        manifest["tables"][table] = {"rows": t.num_rows, "columns": cols}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _ts(s: Any) -> float:
    if s is None or s == "":
        return float("nan")
    return float(np.datetime64(str(s).replace("Z", "")).astype("datetime64[s]").astype("int64"))


def _is_bot(user: str | None, user_type: str | None) -> bool:
    return (user_type or "").lower() == "bot" or (user or "").lower().endswith("[bot]")


def _api_key(url: str) -> tuple[str, int] | None:
    m = re.search(r"github\.com/(?:repos/)?([^/]+/[^/]+)/pulls?/(\d+)", url or "")
    return (m.group(1).lower(), int(m.group(2))) if m else None


@dataclass
class Units:
    repo: np.ndarray  # int index of repo (cluster)
    pr_id: np.ndarray
    additions: np.ndarray
    agent: np.ndarray  # int index
    agent_names: list[str]
    U: np.ndarray
    C: np.ndarray
    S: np.ndarray
    eligible: np.ndarray
    fix90: np.ndarray
    fix30: np.ndarray
    revert90: np.ndarray
    revert30: np.ndarray
    ttf_days: np.ndarray  # NaN where no FIX within 90d
    merged_ts: np.ndarray  # unix seconds of the PR's merge
    n_prs_merged: int
    max_merged: float


def load_tables(d: Path) -> dict[str, list[dict[str, Any]]]:
    import pyarrow.parquet as pq

    return {t: pq.read_table(d / f"{t}.parquet").to_pylist() for t in COLUMNS}


def build_units(tables: dict[str, list[dict[str, Any]]]) -> Units:
    prs = {p["id"]: p for p in tables["pull_request"] if p.get("merged_at")}
    merged_ts = {i: _ts(p["merged_at"]) for i, p in prs.items()}
    max_m = max(merged_ts.values()) if merged_ts else float("nan")
    by_key = {_api_key(p["html_url"]): i for i, p in prs.items() if _api_key(p["html_url"])}
    # --- review evidence -> U ------------------------------------------------------------
    pr_review: dict[int, int] = {}  # 0 none, 1 any human review, 2 substantive body
    for r in tables["pr_reviews"]:
        i = r["pr_id"]
        p = prs.get(i)
        if p is None or _is_bot(r["user"], r["user_type"]) or r["user"] == p["user"]:
            continue
        if not _ts(r["submitted_at"]) <= merged_ts[i]:
            continue
        lvl = 2 if len((r["body"] or "").strip()) >= 20 else 1
        pr_review[i] = max(pr_review.get(i, 0), lvl)
    file_reviewed: set[tuple[int, str]] = set()
    for c in tables["pr_review_comments"]:
        i = by_key.get(_api_key(c["pull_request_url"]))
        p = prs.get(i) if i is not None else None
        if i is None or p is None or _is_bot(c["user"], c["user_type"]) or c["user"] == p["user"]:
            continue
        if _ts(c["created_at"]) <= merged_ts[i]:
            file_reviewed.add((i, str(c["path"])))
    # --- files per PR --------------------------------------------------------------------
    adds: dict[tuple[int, str], float] = {}
    for d in tables["pr_commit_details"]:
        i, fn = d["pr_id"], d["filename"] or ""
        if i in prs and fn.lower().endswith(CODE_EXT) and d["status"] != "removed":
            adds[(i, fn)] = adds.get((i, fn), 0.0) + float(d["additions"] or 0)
    # --- per (repo, file) timeline -------------------------------------------------------
    timeline: dict[tuple[int, str], list[tuple[float, int, bool]]] = {}
    for i, fn in adds:
        p = prs[i]
        timeline.setdefault((p["repo_id"], fn), []).append(
            (
                merged_ts[i],
                i,
                bool(FIX_RE.search(p["title"] or ""))
                and not (p["title"] or "").lower().lstrip().startswith("revert"),
            )
        )
    for v in timeline.values():
        v.sort()
    # --- reverts -------------------------------------------------------------------------
    sha_to_pr = {c["sha"]: c["pr_id"] for c in tables["pr_commits"] if c["pr_id"] in prs}
    revert_ts: dict[int, float] = {}
    for i, p in prs.items():
        title = (p["title"] or "").strip()
        if not title.lower().startswith("revert"):
            continue
        text = f"{title}\n{p['body'] or ''}"
        targets: set[int] = set()
        repo_name = (_api_key(p["html_url"]) or ("", 0))[0]
        for n in re.findall(r"#(\d+)", text):
            j = by_key.get((repo_name, int(n)))
            if j is not None and j != i and prs[j]["repo_id"] == p["repo_id"]:
                targets.add(j)
        for u in re.findall(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", text):
            j = by_key.get(_api_key(u))
            if j is not None and j != i:
                targets.add(j)
        for sh in re.findall(r"\b[0-9a-f]{40}\b", text):
            j = sha_to_pr.get(sh)
            if j is not None and j != i and prs[j]["repo_id"] == p["repo_id"]:
                targets.add(j)
        for j in targets:
            t = merged_ts[i]
            if t > merged_ts[j] and (j not in revert_ts or t < revert_ts[j]):
                revert_ts[j] = t
    # --- assemble ------------------------------------------------------------------------
    repos: dict[int, int] = {}
    agents: dict[str, int] = {}
    cols: dict[str, list[float]] = {
        k: []
        for k in (
            "repo",
            "pr",
            "add",
            "agent",
            "U",
            "C",
            "elig",
            "f90",
            "f30",
            "r90",
            "r30",
            "ttf",
            "mts",
        )
    }
    rep_max: dict[int, int] = {}
    for (i, fn), a in adds.items():
        p = prs[i]
        t0 = merged_ts[i]
        tl = timeline[(p["repo_id"], fn)]
        times = [x[0] for x in tl]
        lo = bisect.bisect_left(times, t0 - 90 * DAY)
        hi = bisect.bisect_left(times, t0)
        cnt = sum(1 for x in tl[lo:hi] if x[1] != i)
        fix_t = [
            x[0] - t0
            for x in tl[bisect.bisect_right(times, t0) :]
            if x[2] and x[1] != i and x[0] - t0 <= 90 * DAY
        ]
        u = 1.0 if (i, fn) in file_reviewed else {2: 0.5, 1: 0.25}.get(pr_review.get(i, 0), 0.0)
        rt = revert_ts.get(i)
        r_idx = repos.setdefault(p["repo_id"], len(repos))
        cols["repo"].append(r_idx)
        cols["pr"].append(i)
        cols["add"].append(a)
        cols["agent"].append(agents.setdefault(p["agent"] or "?", len(agents)))
        cols["U"].append(u)
        cols["C"].append(cnt)
        cols["elig"].append(float(t0 <= max_m - 90 * DAY))
        cols["f90"].append(float(bool(fix_t)))
        cols["f30"].append(float(any(x <= 30 * DAY for x in fix_t)))
        cols["r90"].append(float(rt is not None and rt - t0 <= 90 * DAY))
        cols["r30"].append(float(rt is not None and rt - t0 <= 30 * DAY))
        cols["ttf"].append(min(fix_t) / DAY if fix_t else float("nan"))
        cols["mts"].append(t0)
        rep_max[r_idx] = max(rep_max.get(r_idx, 0), cnt)
    repo_a = np.array(cols["repo"], dtype=int)
    cnt_a = np.array(cols["C"], dtype=float)
    mx = np.array([rep_max[r] for r in repo_a], dtype=float)
    C = np.divide(cnt_a, mx, out=np.zeros_like(cnt_a), where=mx > 0)
    U = np.array(cols["U"])
    return Units(
        repo=repo_a,
        pr_id=np.array(cols["pr"], dtype=int),
        additions=np.array(cols["add"]),
        agent=np.array(cols["agent"], dtype=int),
        agent_names=[k for k, _ in sorted(agents.items(), key=lambda kv: kv[1])],
        U=U,
        C=C,
        S=(1 - U) * (1 + C),
        eligible=np.array(cols["elig"]) > 0,
        fix90=np.array(cols["f90"]),
        fix30=np.array(cols["f30"]),
        revert90=np.array(cols["r90"]),
        revert30=np.array(cols["r30"]),
        ttf_days=np.array(cols["ttf"]),
        merged_ts=np.array(cols["mts"]),
        n_prs_merged=len(prs),
        max_merged=max_m,
    )
