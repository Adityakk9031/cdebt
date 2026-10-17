from __future__ import annotations

from typing import Protocol

from cdebt.gitutil import BlameLine
from cdebt.models import AttributionSource, AuthorKind


class Provider(Protocol):
    source: AttributionSource

    def claims(
        self, path: str, rev: str, blame: list[BlameLine]
    ) -> list[tuple[AuthorKind, bool] | None]:
        """One entry per blamed line: (kind, coarse) or None for 'no evidence'."""
        ...
