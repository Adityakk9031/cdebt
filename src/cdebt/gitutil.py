"""Read-only git wrappers. Only allow-listed read subcommands are ever executed."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_ALLOWED = {
    "rev-parse",
    "ls-files",
    "blame",
    "notes",
    "show",
    "cat-file",
    "diff",
    "log",
    "rev-list",
    "for-each-ref",
    "merge-base",
    "config",
    "ls-tree",
}
_NOTES_READ = {"show", "list"}


class GitError(RuntimeError):
    pass


def run(args: list[str], cwd: Path, check: bool = True) -> str:
    sub = next((a for a in args if not a.startswith("-")), "")
    # `git notes --ref=ai show` etc: first non-flag is the subcommand
    if sub not in _ALLOWED:
        raise GitError(f"refusing non-read-only git command: {args!r}")
    if (
        sub == "notes"
        and not (set(args) & _NOTES_READ)
        or sub == "config"
        and "--get" not in args
        and "--get-all" not in args
        and "--get-regexp" not in args
    ):
        raise GitError(f"refusing non-read-only git command: {args!r}")
    p = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def repo_root(cwd: Path) -> Path:
    return Path(run(["rev-parse", "--show-toplevel"], cwd).strip())


def resolve(cwd: Path, rev: str) -> str:
    return run(["rev-parse", "--verify", f"{rev}^{{commit}}"], cwd).strip()


def ls_files(cwd: Path, rev: str | None = None) -> list[str]:
    if rev:
        return [p for p in run(["ls-tree", "-r", "--name-only", rev], cwd).splitlines() if p]
    return [p for p in run(["ls-files"], cwd).splitlines() if p]


def show_file(cwd: Path, rev: str, path: str) -> str | None:
    p = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=cwd, capture_output=True)
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class BlameLine:
    final: int
    sha: str
    orig: int
    path: str  # file path in the blamed commit


_HDR = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


def parse_blame_porcelain(text: str) -> list[BlameLine]:
    out: list[BlameLine] = []
    fname: dict[str, str] = {}
    cur: tuple[str, int, int] | None = None
    for line in text.splitlines():
        if line.startswith("\t"):
            if cur:
                sha, orig, final = cur
                out.append(BlameLine(final, sha, orig, fname.get(sha, "")))
            cur = None
            continue
        m = _HDR.match(line)
        if m:
            cur = (m.group(1), int(m.group(2)), int(m.group(3)))
        elif line.startswith("filename ") and cur:
            fname[cur[0]] = line[len("filename ") :]
    return out


def blame(cwd: Path, path: str, rev: str) -> list[BlameLine]:
    return parse_blame_porcelain(run(["blame", "--porcelain", rev, "--", path], cwd))


def note(cwd: Path, sha: str, ref: str = "ai") -> str | None:
    p = subprocess.run(
        ["git", "notes", f"--ref={ref}", "show", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return p.stdout if p.returncode == 0 else None


def commit_message(cwd: Path, sha: str) -> str:
    return run(["log", "-1", "--format=%B", sha], cwd)


_REV: dict[tuple[str, str], str] = {}
_BLOBS: dict[tuple[str, str], dict[str, str]] = {}


def rev_sha(cwd: Path, rev: str) -> str:
    """`resolve`, memoised for a branch/tag name only within one process and one HEAD move."""
    key = (str(cwd), rev)
    if key not in _REV or not re.fullmatch(r"[0-9a-f]{40}", rev):
        _REV[key] = resolve(cwd, rev)
    return _REV[key]


def blob_shas(cwd: Path, rev: str) -> dict[str, str]:
    """path -> blob sha for every file at `rev` (one `git ls-tree` per commit per process)."""
    sha = rev_sha(cwd, rev)
    key = (str(cwd), sha)
    if key not in _BLOBS:
        out: dict[str, str] = {}
        for line in run(["ls-tree", "-r", "-z", sha], cwd).split("\0"):
            if not line:
                continue
            meta, _, name = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3:
                out[name] = parts[2]
        _BLOBS[key] = out
    return _BLOBS[key]


def file_churn(cwd: Path, path: str, rev: str, since_days: int) -> int:
    out = run(
        ["log", f"--since={since_days}.days.ago", "--format=%H", rev, "--", path], cwd, check=False
    )
    return len([x for x in out.splitlines() if x])


@dataclass(frozen=True)
class Hunk:
    path: str
    lines: list[int]  # added/changed line numbers in the new file


def changed_lines(cwd: Path, base: str, head: str) -> list[Hunk]:
    """Added/modified lines in head relative to merge-base(base, head)."""
    mb = run(["merge-base", base, head], cwd).strip()
    text = run(["diff", "--unified=0", "--no-color", "--no-renames", mb, head], cwd)
    hunks: dict[str, list[int]] = {}
    path: str | None = None
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = None if line.endswith("/dev/null") else line[6:]
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                hunks.setdefault(path, []).extend(range(start, start + n))
    return [Hunk(p, ls) for p, ls in hunks.items()]
