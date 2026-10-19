"""Fallback heuristic: `Co-Authored-By:` trailers naming a known AI agent.

Commit-level and therefore coarse: every line last touched by such a commit is claimed AI,
including lines a human wrote in the same commit. Always labelled `trailer`. A commit
without the trailer yields NO claim (unknown), never "human".
"""

from __future__ import annotations

import re
from pathlib import Path

from cdebt import gitutil
from cdebt.gitutil import BlameLine
from cdebt.models import AttributionSource, AuthorKind

DEFAULT_AGENT_PATTERNS = (
    r"claude",
    r"anthropic",
    r"copilot",
    r"cursor",
    r"codex",
    r"openai",
    r"devin",
    r"gemini",
    r"aider",
    r"windsurf",
    r"cline",
    r"\[bot\]",
)
_TRAILER = re.compile(r"^co-authored-by:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def is_ai_trailer(message: str, patterns: tuple[str, ...] = DEFAULT_AGENT_PATTERNS) -> bool:
    for who in _TRAILER.findall(message):
        if any(re.search(p, who, re.IGNORECASE) for p in patterns):
            return True
    return False


class TrailerProvider:
    source = AttributionSource.TRAILER

    def __init__(self, repo: Path, patterns: tuple[str, ...] = DEFAULT_AGENT_PATTERNS) -> None:
        self.repo, self.patterns = repo, patterns
        self._cache: dict[str, bool] = {}

    def cache_key(self) -> str:
        return "patterns:" + "|".join(self.patterns)

    def _ai(self, sha: str) -> bool:
        if sha not in self._cache:
            self._cache[sha] = is_ai_trailer(gitutil.commit_message(self.repo, sha), self.patterns)
        return self._cache[sha]

    def claims(
        self, path: str, rev: str, blame: list[BlameLine]
    ) -> list[tuple[AuthorKind, bool] | None]:
        return [(AuthorKind.AI, True) if self._ai(bl.sha) else None for bl in blame]
