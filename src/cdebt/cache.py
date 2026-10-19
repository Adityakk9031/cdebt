"""On-disk result cache under `.cdebt/cache/` (never anywhere else in the repo).

Keys are content addresses: commit sha + file blob sha + a signature of everything else the result
depends on (attribution providers and their inputs, cdebt version). A changed commit, file, note,
trailer pattern or cdebt version therefore misses by construction; there is no invalidation
logic to get wrong. Corrupt or unreadable entries are treated as misses. `CDEBT_NO_CACHE=1` or
`--no-cache` disables it. The cache directory is git-ignored via `.cdebt/.gitignore`."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

CACHE_VERSION = 1


class Stats:
    hits = 0
    misses = 0

    @classmethod
    def reset(cls) -> None:
        cls.hits = cls.misses = 0


def enabled() -> bool:
    return os.environ.get("CDEBT_NO_CACHE") != "1"


def make_key(*parts: object) -> str:
    h = hashlib.sha256()
    h.update(f"cdebt-cache-v{CACHE_VERSION}".encode())
    for p in parts:
        h.update(b"\0" + str(p).encode("utf-8", "replace"))
    return h.hexdigest()


class Cache:
    def __init__(self, repo: Path) -> None:
        self.root = repo / ".cdebt" / "cache" / f"v{CACHE_VERSION}"
        self._ready = False

    def _path(self, ns: str, key: str) -> Path:
        return self.root / ns / key[:2] / f"{key}.json"

    def get(self, ns: str, key: str) -> Any | None:
        if not enabled():
            return None
        try:
            val = json.loads(self._path(ns, key).read_text())
        except (OSError, ValueError):
            Stats.misses += 1
            return None
        Stats.hits += 1
        return val["v"] if isinstance(val, dict) and "v" in val else None

    def put(self, ns: str, key: str, value: Any) -> None:
        if not enabled():
            return
        p = self._path(ns, key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_gitignore()
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({"v": value}, f, separators=(",", ":"))
                os.replace(tmp, p)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            pass  # a cache must never break a run (read-only checkout, full disk, ...)

    def _ensure_gitignore(self) -> None:
        if self._ready:
            return
        gi = self.root.parent.parent / ".gitignore"  # .cdebt/.gitignore
        try:
            if not gi.exists():
                gi.write_text("cache/\n*.bak.json\n")
        except OSError:
            pass
        self._ready = True
