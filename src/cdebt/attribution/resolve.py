"""Pick attribution per line by priority git-ai > agent-trace > trailer > none, and label it."""

from __future__ import annotations

from pathlib import Path

from cdebt import gitutil
from cdebt.attribution.agent_trace import AgentTraceProvider
from cdebt.attribution.base import Provider
from cdebt.attribution.gitai import GitAiProvider
from cdebt.attribution.trailer import TrailerProvider
from cdebt.models import AttributionSource, AuthorKind, FileAttribution


def default_providers(repo: Path, *, use_trailers: bool = True) -> list[Provider]:
    ps: list[Provider] = [GitAiProvider(repo), AgentTraceProvider(repo)]
    if use_trailers:
        ps.append(TrailerProvider(repo))
    return ps


def resolve_file(
    repo: Path, path: str, rev: str = "HEAD", providers: list[Provider] | None = None
) -> FileAttribution:
    providers = providers if providers is not None else default_providers(repo)
    try:
        bl = gitutil.blame(repo, path, rev)
    except gitutil.GitError:
        return FileAttribution(path=path, kinds=[], sources=[])
    n = len(bl)
    kinds = [AuthorKind.UNKNOWN] * n
    sources = [AttributionSource.NONE] * n
    coarse = False
    for prov in providers:  # priority order: first claim per line wins
        for i, c in enumerate(prov.claims(path, rev, bl)):
            if c is not None and kinds[i] is AuthorKind.UNKNOWN:
                kinds[i], sources[i] = c[0], prov.source
                coarse = coarse or c[1]
    return FileAttribution(path=path, kinds=kinds, sources=sources, coarse=coarse)


def resolve_files(
    repo: Path, paths: list[str], rev: str = "HEAD", providers: list[Provider] | None = None
) -> dict[str, FileAttribution]:
    providers = providers if providers is not None else default_providers(repo)
    return {p: resolve_file(repo, p, rev, providers) for p in paths}
